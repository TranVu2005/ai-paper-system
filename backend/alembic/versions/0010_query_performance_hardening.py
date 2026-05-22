"""query performance hardening for core flows

Revision ID: 0010_query_performance_hardening
Revises: 0009_relational_hardening
Create Date: 2026-05-10 16:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_query_performance_hardening"
down_revision = "0009_relational_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE documents SET status = 'uploaded' WHERE status IS NULL")
    op.execute("UPDATE document_jobs SET status = 'pending' WHERE status IS NULL")

    op.alter_column("documents", "status", existing_type=sa.String(), nullable=False)
    op.alter_column("document_jobs", "status", existing_type=sa.String(), nullable=False)

    op.create_check_constraint(
        "ck_documents_status_valid",
        "documents",
        "status IN ('uploaded', 'processing', 'processed', 'failed')",
    )
    op.create_check_constraint(
        "ck_document_jobs_status_valid",
        "document_jobs",
        "status IN ('pending', 'running', 'done', 'failed')",
    )
    op.create_check_constraint(
        "ck_document_jobs_retry_count_non_negative",
        "document_jobs",
        "retry_count >= 0",
    )

    op.create_index(
        "ix_documents_user_workspace_created_at",
        "documents",
        ["user_id", "workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_documents_status_created_at",
        "documents",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_document_jobs_status_created_at",
        "document_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_workspaces_user_is_deleted",
        "workspaces",
        ["user_id", "is_deleted"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_workspaces_user_is_deleted", table_name="workspaces")
    op.drop_index("ix_document_jobs_status_created_at", table_name="document_jobs")
    op.drop_index("ix_documents_status_created_at", table_name="documents")
    op.drop_index("ix_documents_user_workspace_created_at", table_name="documents")

    op.drop_constraint("ck_document_jobs_retry_count_non_negative", "document_jobs", type_="check")
    op.drop_constraint("ck_document_jobs_status_valid", "document_jobs", type_="check")
    op.drop_constraint("ck_documents_status_valid", "documents", type_="check")

    op.alter_column("document_jobs", "status", existing_type=sa.String(), nullable=True)
    op.alter_column("documents", "status", existing_type=sa.String(), nullable=True)
