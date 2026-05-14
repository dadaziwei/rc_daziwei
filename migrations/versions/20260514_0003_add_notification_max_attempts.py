"""add notification max attempts

Revision ID: 20260514_0003
Revises: 20260514_0002
Create Date: 2026-05-14 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260514_0003"
down_revision: str | None = "20260514_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("notifications", "max_attempts")
