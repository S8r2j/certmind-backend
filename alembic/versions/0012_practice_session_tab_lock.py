"""add active_tab_id to practice_sessions for tab-level session locking

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-10
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "practice_sessions",
        sa.Column("active_tab_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("practice_sessions", "active_tab_id")
