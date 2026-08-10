"""expand Task Core to the reviewed Phase 1 schema"""

# Alembic's declarative column definitions are intentionally kept readable.
# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_02"
down_revision: str | None = "20260809_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="ru"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id", name="uq_users_telegram_user_id"),
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("telegram_username", sa.String(length=255), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("relation_type", sa.String(length=32), nullable=False, server_default="OTHER"),
        sa.Column("trust_level", sa.String(length=32), nullable=False, server_default="KNOWN"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE VARCHAR(32) USING status::text")
        op.execute("ALTER TABLE tasks ALTER COLUMN priority TYPE VARCHAR(8) USING priority::text")

    op.add_column("tasks", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("task_type", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("assignee_contact_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("created_by_contact_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("start_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("tasks", sa.Column("due_precision", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("source_type", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("source_id", sa.String(length=255), nullable=True))
    op.add_column("tasks", sa.Column("source_url", sa.String(length=2048), nullable=True))
    op.add_column("tasks", sa.Column("calendar_event_id", sa.String(length=255), nullable=True))
    op.add_column("tasks", sa.Column("calendar_sync_status", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("tasks", sa.Column("parent_task_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("is_recurring", sa.Boolean(), nullable=True))
    op.add_column("tasks", sa.Column("recurrence_rule", sa.String(length=255), nullable=True))
    op.add_column("tasks", sa.Column("version", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("metadata", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        sa.text(
            "UPDATE tasks SET status = CASE status "
            "WHEN 'open' THEN 'NEW' WHEN 'in_progress' THEN 'IN_PROGRESS' "
            "WHEN 'done' THEN 'DONE' WHEN 'cancelled' THEN 'CANCELLED' ELSE status END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE tasks SET priority = CASE priority "
            "WHEN 'low' THEN 'P4' WHEN 'normal' THEN 'P3' WHEN 'high' THEN 'P1' ELSE priority END"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO users (id, name, timezone, language, is_active) "
            f"VALUES ('{SYSTEM_USER_ID}', 'API Owner', 'UTC', 'ru', TRUE)"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE tasks SET user_id = '{SYSTEM_USER_ID}', task_type = 'MY_TASK', "
            "due_precision = 'UNKNOWN', is_recurring = FALSE, version = 1, metadata = '{}' "
            "WHERE user_id IS NULL"
        )
    )
    op.alter_column("tasks", "user_id", nullable=False)
    op.alter_column("tasks", "task_type", nullable=False, server_default="MY_TASK")
    op.alter_column("tasks", "status", nullable=False, server_default="NEW")
    op.alter_column("tasks", "priority", nullable=False, server_default="P3")
    op.alter_column("tasks", "due_precision", nullable=False, server_default="UNKNOWN")
    op.alter_column("tasks", "is_recurring", nullable=False, server_default=sa.false())
    op.alter_column("tasks", "version", nullable=False, server_default="1")
    op.alter_column("tasks", "metadata", nullable=False, server_default="{}")
    op.create_foreign_key("fk_tasks_user", "tasks", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        "fk_tasks_assignee_contact", "tasks", "contacts", ["assignee_contact_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_tasks_created_by_contact", "tasks", "contacts", ["created_by_contact_id"], ["id"]
    )
    op.create_foreign_key("fk_tasks_parent", "tasks", "tasks", ["parent_task_id"], ["id"])
    op.create_index("idx_tasks_user_status", "tasks", ["user_id", "status"])
    op.create_index("idx_tasks_user_due", "tasks", ["user_id", "due_at"])
    op.create_index("idx_tasks_assignee", "tasks", ["assignee_contact_id"])
    op.create_index("idx_tasks_next_check", "tasks", ["next_check_at"])

    op.create_table(
        "source_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("sender_external_id", sa.String(length=255), nullable=True),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("sender_email", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False, server_default="NEW"),
        sa.Column("classification", sa.String(length=64), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "source_type", "external_id", name="uq_source_external"),
    )
    op.create_table(
        "task_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_message_id"], ["source_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "source_message_id", "relation", name="uq_task_source"),
    )
    op.create_table(
        "task_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="USER"),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_message_id"], ["source_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_task_events_idempotency"),
    )
    op.create_index("ix_task_events_task_created", "task_events", ["task_id", "created_at"])
    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reminder_type", sa.String(length=32), nullable=False),
        sa.Column("recipient_type", sa.String(length=32), nullable=False, server_default="OWNER"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_reminders_dedupe"),
    )
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("morning_digest_time", sa.Time(), nullable=False, server_default="08:00:00"),
        sa.Column("evening_digest_time", sa.Time(), nullable=False, server_default="19:00:00"),
        sa.Column("weekly_review_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("quiet_hours_start", sa.Time(), nullable=False, server_default="22:00:00"),
        sa.Column("quiet_hours_end", sa.Time(), nullable=False, server_default="07:00:00"),
        sa.Column("auto_create_owner_tasks", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_create_external_tasks", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_calendar", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gmail_poll_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("default_priority", sa.String(length=8), nullable=False, server_default="P3"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO user_settings (user_id) "
            f"SELECT '{SYSTEM_USER_ID}' WHERE NOT EXISTS "
            "(SELECT 1 FROM user_settings WHERE user_id = "
            f"'{SYSTEM_USER_ID}')"
        )
    )


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_table("reminders")
    op.drop_index("ix_task_events_task_created", table_name="task_events")
    op.drop_table("task_events")
    op.drop_table("task_sources")
    op.drop_table("source_messages")
    op.drop_index("idx_tasks_next_check", table_name="tasks")
    op.drop_index("idx_tasks_assignee", table_name="tasks")
    op.drop_index("idx_tasks_user_due", table_name="tasks")
    op.drop_index("idx_tasks_user_status", table_name="tasks")
    op.drop_constraint("fk_tasks_parent", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_created_by_contact", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_assignee_contact", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_user", "tasks", type_="foreignkey")
    for name in (
        "deleted_at",
        "metadata",
        "version",
        "recurrence_rule",
        "is_recurring",
        "parent_task_id",
        "confidence",
        "calendar_sync_status",
        "calendar_event_id",
        "source_url",
        "source_id",
        "source_type",
        "last_reminded_at",
        "next_check_at",
        "due_precision",
        "due_date",
        "start_at",
        "created_by_contact_id",
        "assignee_contact_id",
        "task_type",
        "user_id",
    ):
        op.drop_column("tasks", name)
    op.drop_table("contacts")
    op.drop_table("users")
