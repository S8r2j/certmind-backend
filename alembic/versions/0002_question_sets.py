"""question sets: add set_number to questions

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-06

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add set_number to questions — all existing rows default to set 1
    op.add_column(
        "questions",
        sa.Column("set_number", sa.Integer(), server_default="1", nullable=False),
    )
    # Index for fast set-based lookups
    op.create_index("ix_questions_exam_set", "questions", ["exam_slug", "set_number"])


def downgrade() -> None:
    op.drop_index("ix_questions_exam_set", table_name="questions")
    op.drop_column("questions", "set_number")
