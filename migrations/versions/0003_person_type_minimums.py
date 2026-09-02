from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_person_type_minimums"
down_revision = "0002_setup_booking_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "setup_person_limits",
        sa.Column("min_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("setup_person_limits", "min_count")