"""Bring the migrated schema up to the existing Build 302 operational schema."""
from alembic import op
import sqlalchemy as sa

revision = '0012_operational_schema'
down_revision = '0011_reservation_tenant_refs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('booking_cancellations',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('refund_decision', sa.Text(), nullable=False),
        sa.Column('refund_amount', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('retained_amount', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('financial_note', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.UniqueConstraint('company_id','booking_id'),
    )
    op.create_table('booking_refunds',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('refund_date', sa.Text(), nullable=False),
        sa.Column('method', sa.Text(), nullable=False),
        sa.Column('reference', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('notes', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
    )
    op.create_index('idx_booking_refunds_booking', 'booking_refunds', ['company_id', 'booking_id', 'id'], unique=0)
    op.create_table('payment_method_definitions',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('method_type', sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default=sa.text('10')),
        sa.Column('active', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
    )
    op.create_index('uq_payment_methods_company_name_ci', 'payment_method_definitions',
                    [sa.text('company_id'), sa.text('lower(name)')], unique=True)
    op.create_index('idx_payment_methods_company', 'payment_method_definitions', ['company_id', 'active', 'display_order', 'name'], unique=0)
    op.create_table('payment_rules',
        sa.Column('company_id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('deposit_type', sa.Text(), nullable=False, server_default=sa.text("'fixed'")),
        sa.Column('deposit_value', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('full_payment_threshold', sa.Float(), nullable=True),
        sa.Column('balance_due_days', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('always_require_full_payment', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.Text(), nullable=False),
    )
    op.add_column('booking_status_definitions', sa.Column('system_code', sa.Text(), nullable=True))
    op.add_column('customer_records', sa.Column('mobile_phone', sa.Text(), nullable=False, server_default=sa.text("''")))
    op.add_column('customer_records', sa.Column('fixed_phone', sa.Text(), nullable=False, server_default=sa.text("''")))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("""CREATE FUNCTION dbs_default_workflow() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.workflow_status_id IS NULL THEN
            IF TG_TABLE_NAME='enquiries' THEN
              SELECT id INTO NEW.workflow_status_id FROM booking_status_definitions
                WHERE company_id=NEW.company_id AND active=1 AND internal_state='HELD'
                ORDER BY display_order,id LIMIT 1;
              NEW.availability_expires_at := NULL;
            ELSE
              SELECT id INTO NEW.workflow_status_id FROM booking_status_definitions
                WHERE company_id=NEW.company_id AND active=1 AND internal_state=CASE
                  WHEN NEW.status='cancelled' THEN 'RELEASED'
                  WHEN NEW.status='provisional' THEN 'RESERVED'
                  WHEN NEW.status IN ('completed','no_show') THEN 'RELEASED'
                  ELSE 'CONFIRMED' END
                ORDER BY display_order,id LIMIT 1;
            END IF;
          END IF;
          RETURN NEW;
        END; $$""")
        for table in ('enquiries','bookings'):
            op.execute(f'CREATE TRIGGER dbs_default_workflow BEFORE INSERT ON {table} '
                       'FOR EACH ROW EXECUTE FUNCTION dbs_default_workflow()')
        op.execute("""CREATE FUNCTION dbs_immutable_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'Audit log is append-only'; END; $$""")
        op.execute('CREATE TRIGGER dbs_immutable_audit BEFORE UPDATE OR DELETE ON audit_log '
                   'FOR EACH ROW EXECUTE FUNCTION dbs_immutable_audit()')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER dbs_immutable_audit ON audit_log')
        op.execute('DROP FUNCTION dbs_immutable_audit()')
        for table in ('enquiries','bookings'):
            op.execute(f'DROP TRIGGER dbs_default_workflow ON {table}')
        op.execute('DROP FUNCTION dbs_default_workflow()')
    op.drop_column('customer_records', 'fixed_phone')
    op.drop_column('customer_records', 'mobile_phone')
    op.drop_column('booking_status_definitions', 'system_code')
    op.drop_table('payment_rules')
    op.drop_table('payment_method_definitions')
    op.drop_table('booking_refunds')
    op.drop_table('booking_cancellations')
