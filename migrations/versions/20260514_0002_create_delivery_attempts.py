"""create delivery attempts table

Revision ID: 20260514_0002
Revises: 20260514_0001
Create Date: 2026-05-14 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0002"
down_revision: str | None = "20260514_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("response_body_preview", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notification_id",
            "attempt_number",
            name="uq_delivery_attempts_notification_attempt_number",
        ),
    )
    op.create_index(
        "ix_delivery_attempts_notification_id",
        "delivery_attempts",
        ["notification_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_notification_id", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
