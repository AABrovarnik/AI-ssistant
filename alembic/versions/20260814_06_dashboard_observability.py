"""add durable dashboard observability and candidate lifecycle"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_06"
down_revision: str | None = "20260812_05"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_poll_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stored_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ignored_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notified_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["integration_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_poll_runs_account_started", "integration_poll_runs", ["account_id", "started_at"]
    )
    op.create_index(
        "ix_poll_runs_user_started", "integration_poll_runs", ["user_id", "started_at"]
    )

    op.create_table(
        "task_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=False),
        sa.Column("classification", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.String(length=255), nullable=True),
        sa.Column("notification_error", sa.String(length=2000), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["source_message_id"], ["source_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_message_id", name="uq_task_candidates_source_message"),
    )
    op.create_index(
        "ix_task_candidates_user_status_detected",
        "task_candidates",
        ["user_id", "status", "detected_at"],
    )
    op.create_index(
        "ix_task_candidates_user_detected", "task_candidates", ["user_id", "detected_at"]
    )

    op.create_index(
        "ix_source_messages_user_source_received",
        "source_messages",
        ["user_id", "source_type", "received_at"],
    )
    op.create_index(
        "ix_source_messages_user_status_created",
        "source_messages",
        ["user_id", "processing_status", "created_at"],
    )
    op.create_index("ix_task_sources_source_message", "task_sources", ["source_message_id"])
    op.create_index("ix_task_events_user_created", "task_events", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_task_events_user_created", table_name="task_events")
    op.drop_index("ix_task_sources_source_message", table_name="task_sources")
    op.drop_index("ix_source_messages_user_status_created", table_name="source_messages")
    op.drop_index("ix_source_messages_user_source_received", table_name="source_messages")
    op.drop_index("ix_task_candidates_user_detected", table_name="task_candidates")
    op.drop_index("ix_task_candidates_user_status_detected", table_name="task_candidates")
    op.drop_table("task_candidates")
    op.drop_index("ix_poll_runs_user_started", table_name="integration_poll_runs")
    op.drop_index("ix_poll_runs_account_started", table_name="integration_poll_runs")
    op.drop_table("integration_poll_runs")
