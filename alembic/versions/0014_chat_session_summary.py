"""Add summary column to chat_sessions for conversation compression

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_sessions", sa.Column("summary", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("chat_sessions", "summary")
