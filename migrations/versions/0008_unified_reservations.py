"""Build 302 v3: canonical, company-scoped reservation foundation.

The old enquiry/booking tables remain untouched while callers are migrated.
No destructive data operation is performed by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_unified_reservations"
down_revision = "0007_enquiry_element_recovery"
branch_labels = None
depends_on = None

STATES = ("ENQUIRY_HELD", "ENQUIRY_RELEASED", "CONFIRMED", "CANCELLED")

def upgrade():
    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("reference", sa.String(120), nullable=False),
        sa.Column("customer_id", sa.Integer),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("total_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint("state IN ('ENQUIRY_HELD','ENQUIRY_RELEASED','CONFIRMED','CANCELLED')", name="ck_reservation_state"),
        sa.UniqueConstraint("company_id", "reference", name="uq_reservation_company_reference"),
        sa.UniqueConstraint("company_id", "id", name="uq_reservation_company_id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_reservation_company"),
    )
    op.create_index("idx_reservations_company_state", "reservations", ["company_id", "state"])
    op.create_table(
        "reservation_elements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("reservation_id", sa.Integer, nullable=False),
        sa.Column("element_id", sa.Integer, nullable=False),
        sa.Column("arrival_date", sa.String(10), nullable=False),
        sa.Column("departure_date", sa.String(10), nullable=False),
        sa.Column("total_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("pricing_snapshot_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("lead_name", sa.String(255), nullable=False, server_default=""),
        sa.CheckConstraint("arrival_date < departure_date", name="ck_reservation_element_dates"),
        sa.ForeignKeyConstraint(["company_id", "reservation_id"], ["reservations.company_id", "reservations.id"], name="fk_reservation_element_tenant"),
    )
    op.create_index("idx_reservation_elements_inventory", "reservation_elements", ["company_id", "element_id", "arrival_date", "departure_date"])
    op.create_index("idx_reservation_elements_parent", "reservation_elements", ["company_id", "reservation_id"])

def downgrade():
    op.drop_index("idx_reservation_elements_parent", table_name="reservation_elements")
    op.drop_index("idx_reservation_elements_inventory", table_name="reservation_elements")
    op.drop_table("reservation_elements")
    op.drop_index("idx_reservations_company_state", table_name="reservations")
    op.drop_table("reservations")
