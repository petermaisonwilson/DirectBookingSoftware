"""Attach operational records to the permanent reservation identity.

Additive foundation for the service cutover. No existing operational record or
financial transaction is removed, renumbered or assigned to another tenant.
"""
from alembic import op
import sqlalchemy as sa

revision = '0013_operational_identity'
down_revision = '0012_operational_schema'
branch_labels = None
depends_on = None

FINANCE = ('booking_payments', 'booking_refunds', 'booking_cancellations', 'booking_amendments')


def upgrade():
    for table in ('enquiries', 'bookings', 'enquiry_elements', 'booking_elements'):
        op.create_index('uq_' + table + '_identity_owner', table, ['company_id', 'id'], unique=True)
    with op.batch_alter_table('reservations') as batch:
        batch.add_column(sa.Column('enquiry_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('booking_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_reservation_enquiry_owner', 'enquiries',
                                 ['company_id', 'enquiry_id'], ['company_id', 'id'])
        batch.create_foreign_key('fk_reservation_booking_owner', 'bookings',
                                 ['company_id', 'booking_id'], ['company_id', 'id'])
        batch.create_unique_constraint('uq_reservation_enquiry', ['company_id', 'enquiry_id'])
        batch.create_unique_constraint('uq_reservation_booking', ['company_id', 'booking_id'])
    with op.batch_alter_table('reservation_elements') as batch:
        batch.add_column(sa.Column('enquiry_element_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('booking_element_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('recovery_state', sa.String(32), nullable=False, server_default='held'))
        batch.create_check_constraint('ck_reservation_recovery', "recovery_state IN ('held','needs_replacement')")
        batch.create_foreign_key('fk_allocation_enquiry_owner', 'enquiry_elements',
                                 ['company_id', 'enquiry_element_id'], ['company_id', 'id'])
        batch.create_foreign_key('fk_allocation_booking_owner', 'booking_elements',
                                 ['company_id', 'booking_element_id'], ['company_id', 'id'])
        batch.create_unique_constraint('uq_allocation_enquiry_source', ['company_id', 'enquiry_element_id'])
        batch.create_unique_constraint('uq_allocation_booking_source', ['company_id', 'booking_element_id'])
    # An additive column/index preserves the append-only history triggers.
    op.add_column('reservation_history', sa.Column('audit_log_id', sa.Integer(), nullable=True))
    op.create_index('uq_reservation_history_audit', 'reservation_history', ['company_id', 'audit_log_id'], unique=True)
    for table in FINANCE:
        # SQLite reflection omits unnamed checks during table recreation. Preserve
        # the original payment/refund amount rules, giving them durable names.
        existing_checks = sa.inspect(op.get_bind()).get_check_constraints(table)
        checks = tuple(sa.CheckConstraint(item['sqltext'], name=f'ck_{table}_preserved_{index}')
                       for index, item in enumerate(existing_checks)
                       if not item.get('name'))
        with op.batch_alter_table(table, table_args=checks) as batch:
            batch.add_column(sa.Column('reservation_id', sa.Integer(), nullable=True))
            if table in ('booking_payments', 'booking_refunds') and not any(
                    'amount>0' in item['sqltext'].replace(' ', '').replace('(', '').replace(')', '')
                    for item in existing_checks):
                batch.create_check_constraint('ck_' + table + '_amount_positive', 'amount > 0')
            batch.create_foreign_key('fk_' + table + '_reservation', 'reservations',
                                     ['company_id', 'reservation_id'], ['company_id', 'id'])
        op.create_index('idx_' + table + '_reservation', table, ['company_id', 'reservation_id', 'id'])


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text('SELECT id FROM reservations WHERE enquiry_id IS NOT NULL OR booking_id IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Operational identities exist; restore the verified backup rather than sever their history')
    op.drop_index('uq_reservation_history_audit', table_name='reservation_history')
    op.drop_column('reservation_history', 'audit_log_id')
    for table in reversed(FINANCE):
        op.drop_index('idx_' + table + '_reservation', table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint('fk_' + table + '_reservation', type_='foreignkey')
            batch.drop_column('reservation_id')
    with op.batch_alter_table('reservation_elements') as batch:
        for name in ('uq_allocation_enquiry_source', 'uq_allocation_booking_source'):
            batch.drop_constraint(name, type_='unique')
        for name in ('fk_allocation_enquiry_owner', 'fk_allocation_booking_owner'):
            batch.drop_constraint(name, type_='foreignkey')
        batch.drop_constraint('ck_reservation_recovery', type_='check')
        for name in ('enquiry_element_id', 'booking_element_id', 'recovery_state'):
            batch.drop_column(name)
    with op.batch_alter_table('reservations') as batch:
        for name in ('uq_reservation_enquiry', 'uq_reservation_booking'):
            batch.drop_constraint(name, type_='unique')
        for name in ('fk_reservation_enquiry_owner', 'fk_reservation_booking_owner'):
            batch.drop_constraint(name, type_='foreignkey')
        batch.drop_column('enquiry_id')
        batch.drop_column('booking_id')
    for table in ('enquiries', 'bookings', 'enquiry_elements', 'booking_elements'):
        op.drop_index('uq_' + table + '_identity_owner', table_name=table)
