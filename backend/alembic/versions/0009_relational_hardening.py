"""relational hardening for core tables

Revision ID: 0009_relational_hardening
Revises: 0008_login_events
Create Date: 2026-05-10 00:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_relational_hardening"
down_revision = "0008_login_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_foreign_key(
        "fk_refresh_tokens_user_id_users",
        "refresh_tokens",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column("login_events", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_login_events_user_id", "login_events", ["user_id"], unique=False)
    op.create_foreign_key(
        "fk_login_events_user_id_users",
        "login_events",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "fk_document_jobs_requested_by_user_id_users",
        "document_jobs",
        "users",
        ["requested_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_document_jobs_requested_by_user_id", "document_jobs", ["requested_by_user_id"], unique=False)

    op.create_foreign_key(
        "fk_document_recommendations_recommended_document_id_documents",
        "document_recommendations",
        "documents",
        ["recommended_document_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Backfill new user_id fields from user_email when possible.
    op.execute(
        """
        UPDATE refresh_tokens rt
        SET user_id = u.id
        FROM users u
        WHERE rt.user_email = u.email
        """
    )
    op.execute(
        """
        UPDATE login_events le
        SET user_id = u.id
        FROM users u
        WHERE le.user_email = u.email
        """
    )

    # Clean invalid child rows before enforcing NOT NULL constraints.
    op.execute("DELETE FROM document_chunks WHERE document_id IS NULL")
    op.execute("DELETE FROM document_summaries WHERE document_id IS NULL")
    op.execute("DELETE FROM qa_history WHERE user_id IS NULL OR document_id IS NULL")

    op.alter_column("document_chunks", "document_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("document_summaries", "document_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("qa_history", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("qa_history", "document_id", existing_type=sa.Integer(), nullable=False)

    op.create_unique_constraint(
        "uq_document_chunks_document_id_chunk_index",
        "document_chunks",
        ["document_id", "chunk_index"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_document_chunks_document_id_chunk_index", "document_chunks", type_="unique")

    op.alter_column("qa_history", "document_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("qa_history", "user_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("document_summaries", "document_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("document_chunks", "document_id", existing_type=sa.Integer(), nullable=True)

    op.drop_constraint(
        "fk_document_recommendations_recommended_document_id_documents",
        "document_recommendations",
        type_="foreignkey",
    )

    op.drop_index("ix_document_jobs_requested_by_user_id", table_name="document_jobs")
    op.drop_constraint(
        "fk_document_jobs_requested_by_user_id_users",
        "document_jobs",
        type_="foreignkey",
    )

    op.drop_constraint("fk_login_events_user_id_users", "login_events", type_="foreignkey")
    op.drop_index("ix_login_events_user_id", table_name="login_events")
    op.drop_column("login_events", "user_id")

    op.drop_constraint("fk_refresh_tokens_user_id_users", "refresh_tokens", type_="foreignkey")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "user_id")
