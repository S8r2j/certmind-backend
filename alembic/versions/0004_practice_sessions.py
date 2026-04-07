"""add practice_sessions table

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-07

One row per user per set per exam. Tracks how many questions answered in that
set and whether the set is complete (50 questions done).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "practice_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_slug", sa.Text(), nullable=False),
        sa.Column("set_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("questions_answered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_active_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    # One active session per user per exam per set
    op.create_unique_constraint(
        "uq_practice_session_user_exam_set",
        "practice_sessions",
        ["user_id", "exam_slug", "set_number"],
    )
    op.create_index("ix_practice_sessions_user_exam", "practice_sessions", ["user_id", "exam_slug"])


def downgrade() -> None:
    op.drop_index("ix_practice_sessions_user_exam", table_name="practice_sessions")
    op.drop_constraint("uq_practice_session_user_exam_set", "practice_sessions", type_="unique")
    op.drop_table("practice_sessions")
