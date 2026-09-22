"""Build 302 v3: multi-element enquiries.

Adds a per-element parent/detail model without rewriting the legacy single-element
tables. Existing enquiries are copied into one enquiry_element on upgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_multi_element_enquiries"
down_revision = "0004_client_currency"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "enquiry_elements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("enquiry_id", sa.Integer, nullable=False),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("element_type", sa.String(255), nullable=False, server_default=""),
        sa.Column("element_id", sa.Integer, nullable=False),
        sa.Column("arrival_date", sa.String(10), nullable=False),
        sa.Column("departure_date", sa.String(10), nullable=False),
        sa.Column("lead_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("party_size", sa.Integer),
        sa.Column("provisional_total", sa.Float),
        sa.Column("pricing_snapshot_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_index("idx_enquiry_elements_enquiry", "enquiry_elements", ["company_id","enquiry_id","sort_order"])
    for table in ("enquiry_people","enquiry_addons","enquiry_addon_days","enquiry_addon_people","enquiry_addon_person_days","enquiry_selected_addons"):
        op.add_column(table, sa.Column("enquiry_element_id", sa.Integer, nullable=True))
        op.create_index("idx_"+table+"_element", table, ["enquiry_element_id"])
    op.add_column("booking_elements", sa.Column("lead_name", sa.String(255), nullable=False, server_default=""))
    # Preserve every existing enquiry as a one-element enquiry.
    op.execute("""
        INSERT INTO enquiry_elements
        (enquiry_id,company_id,element_type,element_id,arrival_date,departure_date,lead_name,
         party_size,provisional_total,pricing_snapshot_json,sort_order,created_at,updated_at)
        SELECT e.id,e.company_id,er.element_type,er.element_id,e.arrival_date,e.departure_date,'',
               e.party_size,er.provisional_total,er.pricing_snapshot_json,1,e.created_at,e.updated_at
        FROM enquiries e JOIN enquiry_requests er
          ON er.enquiry_id=e.id AND er.company_id=e.company_id
        WHERE er.element_id IS NOT NULL AND e.arrival_date IS NOT NULL AND e.departure_date IS NOT NULL
    """)
    for table in ("enquiry_people","enquiry_addons","enquiry_addon_days","enquiry_addon_people","enquiry_addon_person_days","enquiry_selected_addons"):
        op.execute(f"""UPDATE {table} SET enquiry_element_id=(
            SELECT ee.id FROM enquiry_elements ee
            WHERE ee.enquiry_id={table}.enquiry_id AND ee.company_id={table}.company_id
            ORDER BY ee.sort_order,ee.id LIMIT 1
        ) WHERE enquiry_element_id IS NULL""")

def downgrade():
    op.drop_column("booking_elements","lead_name")
    for table in ("enquiry_selected_addons","enquiry_addon_person_days","enquiry_addon_people","enquiry_addon_days","enquiry_addons","enquiry_people"):
        op.drop_index("idx_"+table+"_element", table_name=table)
        op.drop_column(table,"enquiry_element_id")
    op.drop_index("idx_enquiry_elements_enquiry", table_name="enquiry_elements")
    op.drop_table("enquiry_elements")
