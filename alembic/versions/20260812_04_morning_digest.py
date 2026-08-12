"""add durable morning digest delivery tracking"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_04"
down_revision: str | None = "20260810_03"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "digest_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("digest_type", sa.String(length=32), nullable=False, server_default="MORNING"),
        sa.Column("digest_date", sa.Date(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "digest_type", "digest_date", name="uq_digest_delivery_user_type_date"
        ),
    )
    op.alter_column("user_settings", "morning_digest_time", server_default="07:00:00")
    op.execute(
        sa.text(
            "UPDATE user_settings SET morning_digest_time = '07:00:00' "
            "WHERE morning_digest_time = '08:00:00'"
        )
    )


def downgrade() -> None:
    op.alter_column("user_settings", "morning_digest_time", server_default="08:00:00")
    op.drop_table("digest_deliveries")
