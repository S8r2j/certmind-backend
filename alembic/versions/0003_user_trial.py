"""add trial_used flag to users

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-07

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Track whether the user has already consumed their free 1-week trial.
    # Set to TRUE for any user who already has a subscription so existing users
    # are not accidentally granted a second trial.
    op.add_column(
        "users",
        sa.Column("trial_used", sa.Boolean(), server_default="FALSE", nullable=False),
    )
    # Mark existing users who already have any subscription as trial_used so
    # they are not granted a new free trial after this migration runs.
    op.execute(
        "UPDATE users SET trial_used = TRUE "
        "WHERE id IN (SELECT DISTINCT user_id FROM user_subscriptions)"
    )


def downgrade() -> None:
    op.drop_column("users", "trial_used")
