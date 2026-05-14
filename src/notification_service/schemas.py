from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class NotificationCreate(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    source_system: str = Field(min_length=1, max_length=100)
    event_type: str = Field(min_length=1, max_length=100)
    target_url: AnyHttpUrl
    method: str = Field(min_length=1, max_length=10)
    headers: dict[str, Any] = Field(default_factory=dict)
    body: Any

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        method = value.upper()
        if method != "POST":
            raise ValueError("Only POST is supported in the first version")
        return method


class NotificationResponse(BaseModel):
    id: int
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
