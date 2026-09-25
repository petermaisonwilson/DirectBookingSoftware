"""Build 302 v3: duration discounts and booking amendment ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0006_booking_amendments"
down_revision = "0005_multi_element_enquiries"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "setup_duration_discounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("min_nights", sa.Integer, nullable=False),
        sa.Column("discount_type", sa.String(40), nullable=False),
        sa.Column("discount_value", sa.Float, nullable=False),
        sa.Column("scope_type", sa.String(40), nullable=False, server_default="All elements"),
        sa.Column("element_type", sa.String(255), nullable=False, server_default=""),
        sa.Column("element_id", sa.Integer),
        sa.Column("active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("company_id","name",name="uq_duration_discount_company_name"),
    )
    op.create_index("idx_setup_duration_discounts_company","setup_duration_discounts",["company_id","active","min_nights"])
    op.add_column("booking_elements", sa.Column("original_arrival_date", sa.String(10), nullable=True))
    op.add_column("booking_elements", sa.Column("original_departure_date", sa.String(10), nullable=True))
    op.add_column("booking_elements", sa.Column("original_total_amount", sa.Float, nullable=True))
    op.execute("UPDATE booking_elements SET original_arrival_date=arrival_date, original_departure_date=departure_date, original_total_amount=total_amount WHERE original_arrival_date IS NULL")
    op.create_table(
        "booking_amendments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("booking_id", sa.Integer, nullable=False),
        sa.Column("booking_element_id", sa.Integer, nullable=False),
        sa.Column("old_arrival_date", sa.String(10), nullable=False),
        sa.Column("old_departure_date", sa.String(10), nullable=False),
        sa.Column("new_arrival_date", sa.String(10), nullable=False),
        sa.Column("new_departure_date", sa.String(10), nullable=False),
        sa.Column("old_booking_total", sa.Float, nullable=False),
        sa.Column("new_booking_total", sa.Float, nullable=False),
        sa.Column("manual_discount", sa.Float, nullable=False, server_default="0"),
        sa.Column("calculation_json", sa.Text, nullable=False),
        sa.Column("created_by_user_id", sa.Integer),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("idx_booking_amendments_booking","booking_amendments",["company_id","booking_id","id"])

def downgrade():
    op.drop_index("idx_booking_amendments_booking", table_name="booking_amendments")
    op.drop_table("booking_amendments")
    op.drop_column("booking_elements","original_total_amount")
    op.drop_column("booking_elements","original_departure_date")
    op.drop_column("booking_elements","original_arrival_date")
    op.drop_index("idx_setup_duration_discounts_company", table_name="setup_duration_discounts")
    op.drop_table("setup_duration_discounts")
