from sqlalchemy import func, select
from sqlalchemy.orm import Session

from notification_service.models import DeliveryAttempt, Notification

MAX_RESPONSE_BODY_PREVIEW_LENGTH = 1000


def truncate_response_body_preview(response_body: str | None) -> str | None:
    if response_body is None:
        return None
    return response_body[:MAX_RESPONSE_BODY_PREVIEW_LENGTH]


def create_delivery_attempt(
    db: Session,
    notification: Notification,
    *,
    status_code: int | None,
    success: bool,
    error_message: str | None = None,
    response_body: str | None = None,
    duration_ms: int,
) -> DeliveryAttempt:
    max_attempt_number = (
        db.scalar(
            select(func.max(DeliveryAttempt.attempt_number)).where(
                DeliveryAttempt.notification_id == notification.id
            )
        )
        or 0
    )
    attempt_number = max(notification.attempt_count, max_attempt_number) + 1

    attempt = DeliveryAttempt(
        notification_id=notification.id,
        attempt_number=attempt_number,
        status_code=status_code,
        success=success,
        error_message=error_message,
        response_body_preview=truncate_response_body_preview(response_body),
        duration_ms=duration_ms,
    )
    notification.attempt_count = attempt_number
    db.add(attempt)
    db.flush()
    return attempt
