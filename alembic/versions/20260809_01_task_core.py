"""add task core tables

Revision ID: 20260809_01
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_01"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    task_status = sa.Enum("open", "in_progress", "done", "cancelled", name="task_status")
    task_priority = sa.Enum("low", "normal", "high", name="task_priority")
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", task_status, nullable=False, server_default="open"),
        sa.Column("priority", task_priority, nullable=False, server_default="normal"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False, server_default="api"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
    )
    op.create_index("ix_tasks_status_due_at", "tasks", ["status", "due_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_audit_events_idempotency_key"),
    )
    op.create_index("ix_audit_events_task_created", "audit_events", ["task_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_task_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_tasks_status_due_at", table_name="tasks")
    op.drop_table("tasks")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="task_priority").drop(bind, checkfirst=True)
        sa.Enum(name="task_status").drop(bind, checkfirst=True)
