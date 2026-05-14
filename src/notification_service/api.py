from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from notification_service.database import get_db
from notification_service.config import get_settings
from notification_service.constants import NotificationStatus
from notification_service.models import DeliveryAttempt, Notification
from notification_service.schemas import (
    NotificationCreate,
    NotificationDetailResponse,
    NotificationResponse,
)

router = APIRouter()


def utc_now() -> datetime:
    return datetime.now(UTC)


def allowed_target_hosts() -> set[str]:
    return {
        host.strip().lower()
        for host in get_settings().target_host_allowlist.split(",")
        if host.strip()
    }


def ensure_target_url_allowed(target_url: str) -> None:
    allowed_hosts = allowed_target_hosts()
    if not allowed_hosts:
        return

    host = (urlparse(target_url).hostname or "").lower()
    if host in allowed_hosts:
        return
    if any(host.endswith(f".{allowed_host}") for allowed_host in allowed_hosts):
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="target_url host is not allowed",
    )


def create_or_get_notification(
    db: Session,
    payload: NotificationCreate,
) -> tuple[Notification, bool]:
    existing = db.scalar(
        select(Notification).where(Notification.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        return existing, False

    target_url = str(payload.target_url)
    ensure_target_url_allowed(target_url)

    notification = Notification(
        idempotency_key=payload.idempotency_key,
        source_system=payload.source_system,
        event_type=payload.event_type,
        target_url=target_url,
        method=payload.method,
        headers=payload.headers,
        body=payload.body,
        status=NotificationStatus.PENDING.value,
        max_attempts=get_settings().max_attempts,
        next_retry_at=utc_now(),
    )
    db.add(notification)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Notification).where(Notification.idempotency_key == payload.idempotency_key)
        )
        if existing is None:
            raise
        return existing, False

    db.refresh(notification)
    return notification, True


@router.post(
    "/notifications",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_notification(
    payload: NotificationCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> Notification:
    notification, created = create_or_get_notification(db, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
    return notification


@router.post("/notifications/{notification_id}/retry", response_model=NotificationResponse)
def retry_notification(
    notification_id: int,
    db: Session = Depends(get_db),
) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if notification.status != NotificationStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed notifications can be retried",
        )

    notification.status = NotificationStatus.PENDING.value
    notification.next_retry_at = utc_now()
    notification.last_error = None
    db.commit()
    db.refresh(notification)
    return notification


@router.get("/notifications/{notification_id}", response_model=NotificationDetailResponse)
def get_notification(
    notification_id: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    attempts = list(
        db.scalars(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.notification_id == notification.id)
            .order_by(DeliveryAttempt.created_at.asc(), DeliveryAttempt.attempt_number.asc())
        )
    )

    return {
        "id": notification.id,
        "idempotency_key": notification.idempotency_key,
        "source_system": notification.source_system,
        "event_type": notification.event_type,
        "target_url": notification.target_url,
        "method": notification.method,
        "status": notification.status,
        "attempt_count": notification.attempt_count,
        "max_attempts": notification.max_attempts,
        "next_retry_at": notification.next_retry_at,
        "last_error": notification.last_error,
        "created_at": notification.created_at,
        "updated_at": notification.updated_at,
        "attempts": attempts,
    }
