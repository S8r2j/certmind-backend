"""add code column to exams table

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("exams", sa.Column("code", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("exams", "code")
