"""scope task idempotency to the owner"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_03"
down_revision: str | None = "20260809_02"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_tasks_idempotency_key", "tasks", type_="unique")
    op.create_unique_constraint(
        "uq_tasks_user_idempotency_key", "tasks", ["user_id", "idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_tasks_user_idempotency_key", "tasks", type_="unique")
    op.create_unique_constraint("uq_tasks_idempotency_key", "tasks", ["idempotency_key"])
