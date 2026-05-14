from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from notification_service.database import SessionLocal
from notification_service.models import Notification
from notification_service.repositories.delivery_attempts import create_delivery_attempt

HTTP_TIMEOUT_SECONDS = 5.0
RETRY_BACKOFFS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=3),
)


@dataclass(frozen=True)
class DeliveryResult:
    status_code: int | None
    success: bool
    retryable: bool
    error_message: str | None
    response_body: str | None
    duration_ms: int


def utc_now() -> datetime:
    return datetime.now(UTC)


def retry_delay_for_attempt(attempt_number: int) -> timedelta:
    if attempt_number <= 0:
        return RETRY_BACKOFFS[0]
    index = min(attempt_number, len(RETRY_BACKOFFS)) - 1
    return RETRY_BACKOFFS[index]


def is_retryable_status_code(status_code: int) -> bool:
    return status_code in {408, 429} or status_code >= 500


def is_due(notification: Notification, current_time: datetime) -> bool:
    if notification.status != "pending" or notification.next_retry_at is None:
        return False
    next_retry_at = notification.next_retry_at
    if next_retry_at.tzinfo is None:
        current_time = current_time.replace(tzinfo=None)
    return next_retry_at <= current_time


def select_due_notification_ids(
    db: Session,
    *,
    limit: int,
    current_time: datetime,
) -> list[int]:
    return list(
        db.scalars(
            select(Notification.id)
            .where(
                Notification.status == "pending",
                Notification.next_retry_at <= current_time,
            )
            .order_by(Notification.next_retry_at.asc(), Notification.id.asc())
            .limit(limit)
        )
    )


def deliver_notification(
    notification: Notification,
    *,
    request_func: Callable[..., httpx.Response] | None = None,
) -> DeliveryResult:
    started_at = monotonic()
    request = request_func or httpx.request

    if notification.method != "POST":
        return DeliveryResult(
            status_code=None,
            success=False,
            retryable=False,
            error_message=f"Unsupported HTTP method: {notification.method}",
            response_body=None,
            duration_ms=0,
        )

    try:
        response = request(
            notification.method,
            notification.target_url,
            headers=notification.headers,
            json=notification.body,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        return DeliveryResult(
            status_code=None,
            success=False,
            retryable=True,
            error_message=f"HTTP request timed out: {exc}",
            response_body=None,
            duration_ms=_elapsed_ms(started_at),
        )
    except httpx.RequestError as exc:
        return DeliveryResult(
            status_code=None,
            success=False,
            retryable=True,
            error_message=f"HTTP request failed: {exc}",
            response_body=None,
            duration_ms=_elapsed_ms(started_at),
        )

    status_code = response.status_code
    success = 200 <= status_code < 300
    retryable = is_retryable_status_code(status_code)
    error_message = None if success else f"HTTP {status_code}"

    return DeliveryResult(
        status_code=status_code,
        success=success,
        retryable=retryable,
        error_message=error_message,
        response_body=response.text,
        duration_ms=_elapsed_ms(started_at),
    )


def process_due_notifications(
    limit: int = 10,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
    request_func: Callable[..., httpx.Response] | None = None,
    now_func: Callable[[], datetime] = utc_now,
) -> int:
    current_time = now_func()
    with session_factory() as db:
        notification_ids = select_due_notification_ids(db, limit=limit, current_time=current_time)

    processed_count = 0
    for notification_id in notification_ids:
        if process_one_notification(
            notification_id,
            session_factory=session_factory,
            request_func=request_func,
            now_func=now_func,
        ):
            processed_count += 1
    return processed_count


def process_one_notification(
    notification_id: int,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
    request_func: Callable[..., httpx.Response] | None = None,
    now_func: Callable[[], datetime] = utc_now,
) -> bool:
    current_time = now_func()
    with session_factory() as db:
        notification = db.get(Notification, notification_id)
        if notification is None or not is_due(notification, current_time):
            return False

        result = deliver_notification(notification, request_func=request_func)
        create_delivery_attempt(
            db,
            notification,
            status_code=result.status_code,
            success=result.success,
            error_message=result.error_message,
            response_body=result.response_body,
            duration_ms=result.duration_ms,
        )
        apply_delivery_result(notification, result, now_func())
        db.commit()
        return True


def apply_delivery_result(
    notification: Notification,
    result: DeliveryResult,
    current_time: datetime,
) -> None:
    if result.success:
        notification.status = "success"
        notification.next_retry_at = None
        notification.last_error = None
        return

    notification.last_error = result.error_message
    if result.retryable and notification.attempt_count < notification.max_attempts:
        notification.status = "pending"
        notification.next_retry_at = current_time + retry_delay_for_attempt(notification.attempt_count)
        return

    notification.status = "failed"
    notification.next_retry_at = None


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((monotonic() - started_at) * 1000))
