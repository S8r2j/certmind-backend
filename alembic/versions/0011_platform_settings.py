"""add platform_settings table for admin-configurable values

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-10
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default settings seeded on first run
DEFAULTS = [
    ("trial_days",            "3",     "Duration of the free trial in days"),
    ("trial_question_limit",  "25",    "Maximum questions allowed during trial period"),
    ("subscription_days",     "7",     "Duration of a paid subscription in days"),
    ("session_set_size",      "50",    "Number of questions per practice session set"),
    ("chat_max_tokens_per_day", "50000", "Max AI tokens a user can consume per day in chat mode"),
]


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("key",        sa.Text(), primary_key=True),
        sa.Column("value",      sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    # Seed defaults
    op.bulk_insert(
        sa.table(
            "platform_settings",
            sa.column("key",         sa.Text()),
            sa.column("value",       sa.Text()),
            sa.column("description", sa.Text()),
        ),
        [{"key": k, "value": v, "description": d} for k, v, d in DEFAULTS],
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
