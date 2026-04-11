"""Add question_type column and multi/fill session counters

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "questions",
        sa.Column("question_type", sa.Text(), nullable=False, server_default="single"),
    )
    op.add_column(
        "practice_sessions",
        sa.Column("multi_served", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "practice_sessions",
        sa.Column("fill_served", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("questions", "question_type")
    op.drop_column("practice_sessions", "multi_served")
    op.drop_column("practice_sessions", "fill_served")
