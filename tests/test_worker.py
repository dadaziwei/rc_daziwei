from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from notification_service.models import DeliveryAttempt, Notification
from notification_service.worker import HTTP_TIMEOUT_SECONDS, process_due_notifications, process_one_notification


FIXED_NOW = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)


def now() -> datetime:
    return FIXED_NOW


def create_due_notification(db: Session, **overrides: Any) -> Notification:
    values: dict[str, Any] = {
        "idempotency_key": "order-123-inventory-notify",
        "source_system": "order-service",
        "event_type": "order.paid",
        "target_url": "https://vendor.example.com/api/notify",
        "method": "POST",
        "headers": {"X-Source": "order-service"},
        "body": {"order_id": "123"},
        "status": "pending",
        "attempt_count": 0,
        "max_attempts": 5,
        "next_retry_at": FIXED_NOW - timedelta(minutes=1),
    }
    values.update(overrides)
    notification = Notification(**values)
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def get_notification(db_session_factory: sessionmaker[Session], notification_id: int) -> Notification:
    with db_session_factory() as db:
        notification = db.get(Notification, notification_id)
        assert notification is not None
        return notification


def get_attempts(
    db_session_factory: sessionmaker[Session],
    notification_id: int,
) -> list[DeliveryAttempt]:
    with db_session_factory() as db:
        return list(
            db.scalars(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.notification_id == notification_id)
                .order_by(DeliveryAttempt.attempt_number)
            )
        )


def mock_response(status_code: int, text: str = "") -> Callable[..., httpx.Response]:
    def request(*args: Any, **kwargs: Any) -> httpx.Response:
        assert args[0] == "POST"
        assert args[1] == "https://vendor.example.com/api/notify"
        assert kwargs["headers"] == {"X-Source": "order-service"}
        assert kwargs["json"] == {"order_id": "123"}
        assert kwargs["timeout"] == HTTP_TIMEOUT_SECONDS
        return httpx.Response(status_code=status_code, text=text)

    return request


def test_2xx_marks_notification_success(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        notification = create_due_notification(db)

    processed = process_one_notification(
        notification.id,
        session_factory=db_session_factory,
        request_func=mock_response(200, "ok"),
        now_func=now,
    )

    updated = get_notification(db_session_factory, notification.id)
    attempts = get_attempts(db_session_factory, notification.id)

    assert processed is True
    assert updated.status == "success"
    assert updated.next_retry_at is None
    assert updated.last_error is None
    assert updated.attempt_count == 1
    assert len(attempts) == 1
    assert attempts[0].success is True
    assert attempts[0].status_code == 200


def test_5xx_reschedules_pending_notification(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        notification = create_due_notification(db)

    process_one_notification(
        notification.id,
        session_factory=db_session_factory,
        request_func=mock_response(500, "vendor failed"),
        now_func=now,
    )

    updated = get_notification(db_session_factory, notification.id)
    attempts = get_attempts(db_session_factory, notification.id)

    assert updated.status == "pending"
    assert updated.next_retry_at is not None
    assert updated.last_error == "HTTP 500"
    assert updated.attempt_count == 1
    assert len(attempts) == 1
    assert attempts[0].success is False
    assert attempts[0].status_code == 500
    assert attempts[0].response_body_preview == "vendor failed"


def test_timeout_reschedules_pending_notification(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        notification = create_due_notification(db)

    def timeout_request(*args: Any, **kwargs: Any) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    process_one_notification(
        notification.id,
        session_factory=db_session_factory,
        request_func=timeout_request,
        now_func=now,
    )

    updated = get_notification(db_session_factory, notification.id)
    attempts = get_attempts(db_session_factory, notification.id)

    assert updated.status == "pending"
    assert updated.next_retry_at is not None
    assert updated.attempt_count == 1
    assert "timed out" in (updated.last_error or "")
    assert len(attempts) == 1
    assert attempts[0].status_code is None
    assert attempts[0].success is False
    assert "timed out" in (attempts[0].error_message or "")


def test_400_marks_notification_failed(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        notification = create_due_notification(db)

    process_one_notification(
        notification.id,
        session_factory=db_session_factory,
        request_func=mock_response(400, "bad request"),
        now_func=now,
    )

    updated = get_notification(db_session_factory, notification.id)
    attempts = get_attempts(db_session_factory, notification.id)

    assert updated.status == "failed"
    assert updated.next_retry_at is None
    assert updated.last_error == "HTTP 400"
    assert updated.attempt_count == 1
    assert len(attempts) == 1
    assert attempts[0].status_code == 400
    assert attempts[0].success is False


def test_retryable_failure_reaching_max_attempts_marks_failed(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        notification = create_due_notification(db, attempt_count=4, max_attempts=5)

    process_one_notification(
        notification.id,
        session_factory=db_session_factory,
        request_func=mock_response(503, "unavailable"),
        now_func=now,
    )

    updated = get_notification(db_session_factory, notification.id)
    attempts = get_attempts(db_session_factory, notification.id)

    assert updated.status == "failed"
    assert updated.next_retry_at is None
    assert updated.last_error == "HTTP 503"
    assert updated.attempt_count == 5
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 5
    assert attempts[0].status_code == 503


def test_process_due_notifications_writes_attempt_for_each_processed_notification(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        first = create_due_notification(db, idempotency_key="first")
        second = create_due_notification(db, idempotency_key="second")
        first_id = first.id
        second_id = second.id
        create_due_notification(
            db,
            idempotency_key="future",
            next_retry_at=FIXED_NOW + timedelta(minutes=10),
        )

    processed = process_due_notifications(
        limit=10,
        session_factory=db_session_factory,
        request_func=mock_response(200, "ok"),
        now_func=now,
    )

    assert processed == 2
    assert len(get_attempts(db_session_factory, first_id)) == 1
    assert len(get_attempts(db_session_factory, second_id)) == 1


def test_worker_uses_mocked_httpx_request(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_session_factory() as db:
        notification = create_due_notification(db)

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def mocked_request(*args: Any, **kwargs: Any) -> httpx.Response:
        calls.append((args, kwargs))
        return httpx.Response(status_code=200, text="ok")

    monkeypatch.setattr("notification_service.worker.httpx.request", mocked_request)

    process_one_notification(notification.id, session_factory=db_session_factory, now_func=now)

    assert len(calls) == 1
    assert calls[0][1]["timeout"] == HTTP_TIMEOUT_SECONDS
