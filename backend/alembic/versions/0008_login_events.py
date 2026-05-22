"""add login events table

Revision ID: 0008_login_events
Revises: 0007_password_reset_codes
Create Date: 2026-05-10 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_login_events"
down_revision = "0007_password_reset_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_email", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="password"),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.create_index("ix_login_events_id", "login_events", ["id"], unique=False)
    op.create_index("ix_login_events_user_email", "login_events", ["user_email"], unique=False)
    op.create_index("ix_login_events_provider", "login_events", ["provider"], unique=False)
    op.create_index("ix_login_events_device_id", "login_events", ["device_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_login_events_device_id", table_name="login_events")
    op.drop_index("ix_login_events_provider", table_name="login_events")
    op.drop_index("ix_login_events_user_email", table_name="login_events")
    op.drop_index("ix_login_events_id", table_name="login_events")
    op.drop_table("login_events")
