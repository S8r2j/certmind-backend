"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-06

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, TEXT

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "exams",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("domains", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("total_questions", sa.Integer, server_default="0"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "questions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("exam_slug", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("topic", sa.Text),
        sa.Column("stem", sa.Text, nullable=False),
        sa.Column("options", JSONB, nullable=False),
        sa.Column("correct_answer", sa.Text, nullable=False),
        sa.Column("explanation", sa.Text, nullable=False, server_default="''"),
        sa.Column("difficulty", sa.Text, server_default="'medium'"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_questions_exam_domain", "questions", ["exam_slug", "domain"],
                    postgresql_where=sa.text("is_active = true"))

    op.create_table(
        "user_subscriptions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exam_slug", sa.Text, nullable=False),
        sa.Column("stripe_session_id", sa.Text, unique=True),
        sa.Column("stripe_payment_intent_id", sa.Text),
        sa.Column("status", sa.Text, server_default="'pending'"),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_subscriptions_user", "user_subscriptions", ["user_id", "status"])

    op.create_table(
        "user_progress",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exam_slug", sa.Text, nullable=False),
        sa.Column("domain_scores", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("questions_seen", ARRAY(TEXT), server_default=sa.text("'{}'::text[]")),
        sa.Column("total_answered", sa.Integer, server_default="0"),
        sa.Column("total_correct", sa.Integer, server_default="0"),
        sa.Column("last_active_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "exam_slug", name="uq_progress_user_exam"),
    )
    op.create_index("idx_progress_user_exam", "user_progress", ["user_id", "exam_slug"])

    op.create_table(
        "chat_sessions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exam_slug", sa.Text, nullable=False),
        sa.Column("messages", JSONB, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "token_usage",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscription_id", UUID, sa.ForeignKey("user_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("input_tokens", sa.Integer, server_default="0"),
        sa.Column("output_tokens", sa.Integer, server_default="0"),
        sa.Column("recorded_at", sa.Date, server_default=sa.text("CURRENT_DATE")),
        sa.UniqueConstraint("user_id", "subscription_id", "recorded_at", name="uq_token_usage_daily"),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_token", sa.Text, nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_sessions_token", "user_sessions", ["session_token"],
                    postgresql_where=sa.text("is_active = true"))


def downgrade() -> None:
    op.drop_table("user_sessions")
    op.drop_table("token_usage")
    op.drop_table("chat_sessions")
    op.drop_table("user_progress")
    op.drop_table("user_subscriptions")
    op.drop_table("questions")
    op.drop_table("exams")
    op.drop_table("users")
