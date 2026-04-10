"""add option_explanations, streak, time tracking, attempts, admin, coupons

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-10
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── questions ────────────────────────────────────────────────────────────
    op.add_column("questions", sa.Column("option_explanations", JSONB, nullable=True))

    # ── user_progress ─────────────────────────────────────────────────────────
    op.add_column("user_progress", sa.Column("streak_days", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("user_progress", sa.Column("last_streak_date", sa.Date(), nullable=True))
    op.add_column("user_progress", sa.Column("time_committed_seconds", sa.Integer(), nullable=False, server_default="0"))

    # ── practice_sessions ─────────────────────────────────────────────────────
    op.add_column("practice_sessions", sa.Column("time_spent_seconds", sa.Integer(), nullable=False, server_default="0"))

    # ── users ─────────────────────────────────────────────────────────────────
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="FALSE"))

    # ── user_subscriptions ────────────────────────────────────────────────────
    op.add_column("user_subscriptions", sa.Column("notified_expiry", sa.Boolean(), nullable=False, server_default="FALSE"))

    # ── user_question_attempts ────────────────────────────────────────────────
    op.create_table(
        "user_question_attempts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("exam_slug", sa.Text(), nullable=False),
        sa.Column("question_id", sa.Text(), nullable=False),
        sa.Column("user_answer", sa.Text(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_attempts_user_exam", "user_question_attempts", ["user_id", "exam_slug"])

    # ── discount_coupons ──────────────────────────────────────────────────────
    op.create_table(
        "discount_coupons",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("discount_pct", sa.Integer(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),   # NULL = unlimited
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("stripe_coupon_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("discount_coupons")
    op.drop_table("user_question_attempts")
    op.drop_column("user_subscriptions", "notified_expiry")
    op.drop_column("users", "is_admin")
    op.drop_column("practice_sessions", "time_spent_seconds")
    op.drop_column("user_progress", "time_committed_seconds")
    op.drop_column("user_progress", "last_streak_date")
    op.drop_column("user_progress", "streak_days")
    op.drop_column("questions", "option_explanations")
