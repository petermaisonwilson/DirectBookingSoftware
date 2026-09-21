"""Add client booking currency.

Revision ID: 0004_client_currency
Revises: 0003_person_type_minimums
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_client_currency"
down_revision = "0003_person_type_minimums"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("companies", sa.Column("currency", sa.String(length=3), nullable=False, server_default="EUR"))


def downgrade():
    op.drop_column("companies", "currency")
