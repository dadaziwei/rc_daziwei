from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from notification_service.models import Notification


def notification_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "idempotency_key": "order-123-inventory-notify",
        "source_system": "order-service",
        "event_type": "order.paid",
        "target_url": "https://vendor.example.com/api/notify",
        "method": "POST",
        "headers": {
            "X-Source": "order-service",
        },
        "body": {
            "order_id": "123",
            "sku": "A001",
            "quantity": 1,
        },
    }
    payload.update(overrides)
    return payload


def count_notifications(db_session_factory: sessionmaker[Session]) -> int:
    with db_session_factory() as db:
        return db.scalar(select(func.count()).select_from(Notification)) or 0


def test_create_notification_success(
    client: TestClient,
    db_session_factory: sessionmaker[Session],
) -> None:
    response = client.post("/notifications", json=notification_payload())

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "status", "created_at"}
    assert body["id"] == 1
    assert body["status"] == "pending"
    assert body["created_at"]

    with db_session_factory() as db:
        notification = db.scalar(select(Notification))

    assert notification is not None
    assert notification.idempotency_key == "order-123-inventory-notify"
    assert notification.source_system == "order-service"
    assert notification.event_type == "order.paid"
    assert notification.target_url == "https://vendor.example.com/api/notify"
    assert notification.method == "POST"
    assert notification.headers == {"X-Source": "order-service"}
    assert notification.body == {"order_id": "123", "sku": "A001", "quantity": 1}
    assert notification.status == "pending"
    assert notification.attempt_count == 0


def test_duplicate_idempotency_key_returns_existing_notification(
    client: TestClient,
    db_session_factory: sessionmaker[Session],
) -> None:
    first_response = client.post("/notifications", json=notification_payload())
    second_response = client.post(
        "/notifications",
        json=notification_payload(
            source_system="another-service",
            body={"order_id": "different"},
        ),
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert second_response.json() == first_response.json()
    assert count_notifications(db_session_factory) == 1


def test_duplicate_idempotency_key_preserves_original_notification(
    client: TestClient,
    db_session_factory: sessionmaker[Session],
) -> None:
    first_response = client.post("/notifications", json=notification_payload())

    for _ in range(3):
        duplicate_response = client.post(
            "/notifications",
            json=notification_payload(
                source_system="changed-service",
                event_type="order.cancelled",
                headers={"X-Source": "changed-service"},
                body={"order_id": "changed"},
            ),
        )
        assert duplicate_response.status_code == 200
        assert duplicate_response.json() == first_response.json()

    with db_session_factory() as db:
        notification = db.scalar(select(Notification))

    assert notification is not None
    assert count_notifications(db_session_factory) == 1
    assert notification.source_system == "order-service"
    assert notification.event_type == "order.paid"
    assert notification.headers == {"X-Source": "order-service"}
    assert notification.body == {"order_id": "123", "sku": "A001", "quantity": 1}


def test_idempotency_key_unique_constraint_protects_create_race(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as db:
        db.add(
            Notification(
                idempotency_key="same-key",
                source_system="order-service",
                event_type="order.paid",
                target_url="https://vendor.example.com/api/notify",
                method="POST",
                headers={},
                body={"order_id": "123"},
                status="pending",
            )
        )
        db.commit()

        db.add(
            Notification(
                idempotency_key="same-key",
                source_system="another-service",
                event_type="order.paid",
                target_url="https://vendor.example.com/api/notify",
                method="POST",
                headers={},
                body={"order_id": "456"},
                status="pending",
            )
        )

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()

    assert count_notifications(db_session_factory) == 1


def test_create_notification_does_not_call_external_http(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("POST /notifications must not call external HTTP")

    monkeypatch.setattr("httpx.request", fail_if_called)

    response = client.post("/notifications", json=notification_payload())

    assert response.status_code == 201
    assert response.json()["status"] == "pending"


def test_invalid_target_url_returns_validation_error(client: TestClient) -> None:
    response = client.post(
        "/notifications",
        json=notification_payload(target_url="ftp://vendor.example.com/api/notify"),
    )

    assert response.status_code == 422


def test_non_post_method_returns_validation_error(client: TestClient) -> None:
    response = client.post("/notifications", json=notification_payload(method="GET"))

    assert response.status_code == 422


def test_created_notification_status_is_pending(client: TestClient) -> None:
    response = client.post("/notifications", json=notification_payload())

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
