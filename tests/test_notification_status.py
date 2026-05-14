from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from notification_service.models import DeliveryAttempt, Notification


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
        "attempt_count": 0,
        "max_attempts": 5,
    }
    values.update(overrides)
    notification = Notification(**values)
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def test_get_existing_notification(
    client: TestClient,
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        notification = create_notification(db)
        notification_id = notification.id

    response = client.get(f"/notifications/{notification_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == notification_id
    assert body["idempotency_key"] == "order-123-inventory-notify"
    assert body["source_system"] == "order-service"
    assert body["event_type"] == "order.paid"
    assert body["target_url"] == "https://vendor.example.com/api/notify"
    assert body["method"] == "POST"
    assert body["status"] == "pending"
    assert body["attempt_count"] == 0
    assert body["max_attempts"] == 5
    assert body["next_retry_at"] is None
    assert body["last_error"] is None
    assert body["created_at"]
    assert body["updated_at"]
    assert body["attempts"] == []


def test_get_missing_notification_returns_404(client: TestClient) -> None:
    response = client.get("/notifications/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Notification not found"


def test_get_notification_includes_attempts_ordered_by_created_at(
    client: TestClient,
    db_session_factory: sessionmaker[Session],
) -> None:
    first_created_at = datetime(2026, 5, 14, 12, 10, tzinfo=UTC)
    second_created_at = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)

    with db_session_factory() as db:
        notification = create_notification(db, attempt_count=2, last_error="HTTP 500")
        db.add_all(
            [
                DeliveryAttempt(
                    notification_id=notification.id,
                    attempt_number=1,
                    status_code=500,
                    success=False,
                    error_message="HTTP 500",
                    response_body_preview="first",
                    duration_ms=120,
                    created_at=first_created_at,
                ),
                DeliveryAttempt(
                    notification_id=notification.id,
                    attempt_number=2,
                    status_code=200,
                    success=True,
                    error_message=None,
                    response_body_preview="second",
                    duration_ms=80,
                    created_at=second_created_at,
                ),
            ]
        )
        db.commit()
        notification_id = notification.id

    response = client.get(f"/notifications/{notification_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["attempt_count"] == 2
    assert body["last_error"] == "HTTP 500"
    assert [attempt["attempt_number"] for attempt in body["attempts"]] == [2, 1]
    assert body["attempts"][0] == {
        "attempt_number": 2,
        "status_code": 200,
        "success": True,
        "error_message": None,
        "response_body_preview": "second",
        "duration_ms": 80,
        "created_at": body["attempts"][0]["created_at"],
    }
    assert body["attempts"][1]["response_body_preview"] == "first"
