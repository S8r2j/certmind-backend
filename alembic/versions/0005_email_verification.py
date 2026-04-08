"""add email verification and password reset fields to users

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-08

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), server_default="FALSE", nullable=False))
    op.add_column("users", sa.Column("email_verify_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("email_verify_token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("reset_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("reset_token_expires_at", sa.DateTime(timezone=True), nullable=True))

    # All existing users are grandfathered in as verified so they are not locked out
    op.execute("UPDATE users SET email_verified = TRUE")


def downgrade() -> None:
    op.drop_column("users", "reset_token_expires_at")
    op.drop_column("users", "reset_token")
    op.drop_column("users", "email_verify_token_expires_at")
    op.drop_column("users", "email_verify_token")
    op.drop_column("users", "email_verified")
