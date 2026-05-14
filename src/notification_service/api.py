from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from notification_service.database import get_db
from notification_service.models import Notification
from notification_service.schemas import NotificationCreate, NotificationResponse

router = APIRouter()


def create_or_get_notification(
    db: Session,
    payload: NotificationCreate,
) -> tuple[Notification, bool]:
    existing = db.scalar(
        select(Notification).where(Notification.idempotency_key == payload.idempotency_key)
    )
    if existing is not None:
        return existing, False

    notification = Notification(
        idempotency_key=payload.idempotency_key,
        source_system=payload.source_system,
        event_type=payload.event_type,
        target_url=str(payload.target_url),
        method=payload.method,
        headers=payload.headers,
        body=payload.body,
        status="pending",
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
