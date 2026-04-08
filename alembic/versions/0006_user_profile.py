"""add profile fields to users

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-08

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("middle_name", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("gender", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("date_of_birth", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("employment_details", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("goals", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "goals")
    op.drop_column("users", "employment_details")
    op.drop_column("users", "date_of_birth")
    op.drop_column("users", "gender")
    op.drop_column("users", "last_name")
    op.drop_column("users", "middle_name")
    op.drop_column("users", "first_name")
