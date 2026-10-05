"""Build 302 v3: per-element enquiry recovery state.

Released enquiries can be reopened safely even when only some original Elements
remain available.  Available Elements resume blocking immediately; unavailable
ones are marked for replacement until the operator chooses a valid alternative.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_enquiry_element_recovery"
down_revision = "0006_booking_amendments"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("enquiry_elements", sa.Column("recovery_state", sa.String(32), nullable=False, server_default="held"))
    op.create_index("idx_enquiry_elements_recovery", "enquiry_elements", ["company_id","enquiry_id","recovery_state"])

def downgrade():
    op.drop_index("idx_enquiry_elements_recovery", table_name="enquiry_elements")
    op.drop_column("enquiry_elements", "recovery_state")
