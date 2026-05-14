from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from notification_service.models import DeliveryAttempt, Notification
from notification_service.repositories.delivery_attempts import (
    MAX_RESPONSE_BODY_PREVIEW_LENGTH,
    create_delivery_attempt,
)


def create_notification(db: Session, **overrides: Any) -> Notification:
    values: dict[str, Any] = {
        "idempotency_key": "order-123-inventory-notify",
        "source_system": "order-service",
        "event_type": "order.paid",
        "target_url": "https://vendor.example.com/api/notify",
        "method": "POST",
        "headers": {"X-Source": "order-service"},
        "body": {"order_id": "123"},
        "status": "pending",
    }
    values.update(overrides)
    notification = Notification(**values)
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def test_create_delivery_attempt_success(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        notification = create_notification(db)

        attempt = create_delivery_attempt(
            db,
            notification,
            status_code=200,
            success=True,
            error_message=None,
            response_body='{"ok": true}',
            duration_ms=123,
        )
        db.commit()
        db.refresh(notification)
        db.refresh(attempt)

        assert attempt.id is not None
        assert attempt.notification_id == notification.id
        assert attempt.attempt_number == 1
        assert attempt.status_code == 200
        assert attempt.success is True
        assert attempt.error_message is None
        assert attempt.response_body_preview == '{"ok": true}'
        assert attempt.duration_ms == 123
        assert notification.attempt_count == 1


def test_response_body_preview_is_truncated(
    db_session_factory: sessionmaker[Session],
) -> None:
    long_response_body = "x" * (MAX_RESPONSE_BODY_PREVIEW_LENGTH + 50)

    with db_session_factory() as db:
        notification = create_notification(db)

        attempt = create_delivery_attempt(
            db,
            notification,
            status_code=500,
            success=False,
            error_message="vendor error",
            response_body=long_response_body,
            duration_ms=456,
        )
        db.commit()
        db.refresh(attempt)

        assert len(attempt.response_body_preview or "") == MAX_RESPONSE_BODY_PREVIEW_LENGTH
        assert attempt.response_body_preview == "x" * MAX_RESPONSE_BODY_PREVIEW_LENGTH


def test_notification_can_have_multiple_delivery_attempts(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        notification = create_notification(db)

        first_attempt = create_delivery_attempt(
            db,
            notification,
            status_code=None,
            success=False,
            error_message="timeout",
            response_body=None,
            duration_ms=1000,
        )
        second_attempt = create_delivery_attempt(
            db,
            notification,
            status_code=200,
            success=True,
            error_message=None,
            response_body="ok",
            duration_ms=80,
        )
        db.commit()
        db.refresh(notification)

        attempts = list(
            db.scalars(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.notification_id == notification.id)
                .order_by(DeliveryAttempt.attempt_number)
            )
        )

        assert [attempt.attempt_number for attempt in attempts] == [1, 2]
        assert first_attempt.attempt_number == 1
        assert second_attempt.attempt_number == 2
        assert notification.attempt_count == 2
