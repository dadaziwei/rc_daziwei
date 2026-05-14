import os
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from notification_service.config import get_settings
from notification_service.constants import NotificationStatus
from notification_service.models import Notification
from notification_service.worker import claim_due_notifications, utc_now


def postgres_test_url() -> str:
    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not set")

    database_name = (urlparse(url).path or "").lstrip("/")
    if "test" not in database_name and os.getenv("ALLOW_DESTRUCTIVE_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL integration tests require a test database URL")
    return url


@pytest.fixture()
def postgres_engine(monkeypatch: pytest.MonkeyPatch):
    url = postgres_test_url()
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()

    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        get_settings.cache_clear()


def run_migrations() -> None:
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_alembic_migration_runs_on_postgresql(postgres_engine) -> None:
    run_migrations()

    inspector = inspect(postgres_engine)

    assert "notifications" in inspector.get_table_names()
    assert "delivery_attempts" in inspector.get_table_names()


def test_postgresql_skip_locked_claims_distinct_rows(postgres_engine) -> None:
    run_migrations()
    session_factory = sessionmaker(bind=postgres_engine, autoflush=False, autocommit=False)

    with postgres_engine.begin() as connection:
        connection.execute(text("DELETE FROM delivery_attempts"))
        connection.execute(text("DELETE FROM notifications"))

    now = utc_now()
    with session_factory() as db:
        db.add_all(
            [
                Notification(
                    idempotency_key="first",
                    source_system="order-service",
                    event_type="order.paid",
                    target_url="https://vendor.example.com/api/notify",
                    method="POST",
                    headers={},
                    body={"order_id": "1"},
                    status=NotificationStatus.PENDING.value,
                    next_retry_at=now,
                ),
                Notification(
                    idempotency_key="second",
                    source_system="order-service",
                    event_type="order.paid",
                    target_url="https://vendor.example.com/api/notify",
                    method="POST",
                    headers={},
                    body={"order_id": "2"},
                    status=NotificationStatus.PENDING.value,
                    next_retry_at=now,
                ),
            ]
        )
        db.commit()

    session_one = session_factory()
    session_two = session_factory()
    try:
        transaction_one = session_one.begin()
        transaction_two = session_two.begin()

        first_claim = claim_due_notifications(session_one, limit=1, current_time=now)
        second_claim = claim_due_notifications(session_two, limit=1, current_time=now)

        transaction_one.commit()
        transaction_two.commit()
    finally:
        session_one.close()
        session_two.close()

    assert len(first_claim) == 1
    assert len(second_claim) == 1
    assert set(first_claim).isdisjoint(second_claim)
