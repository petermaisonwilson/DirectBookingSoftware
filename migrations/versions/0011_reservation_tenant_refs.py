"""Enforce tenant ownership of reservation customer and element references."""
from alembic import op
import sqlalchemy as sa

revision = '0011_reservation_tenant_refs'
down_revision = '0010_history_immutable'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Refuse incompatible existing data; never delete or silently reassign it.
    for sql in (
        'SELECT r.id FROM reservations r LEFT JOIN customer_records c '
        'ON c.id=r.customer_id AND c.company_id=r.company_id '
        'WHERE r.customer_id IS NOT NULL AND c.id IS NULL LIMIT 1',
        'SELECT r.id FROM reservation_elements r LEFT JOIN setup_elements e '
        'ON e.id=r.element_id AND e.company_id=r.company_id WHERE e.id IS NULL LIMIT 1',
    ):
        if connection.execute(sa.text(sql)).first() is not None:
            raise RuntimeError('Reservation tenant references must be corrected before migration')
    op.create_index('uq_customer_records_tenant_id', 'customer_records', ['company_id', 'id'], unique=True)
    op.create_index('uq_setup_elements_tenant_id', 'setup_elements', ['company_id', 'id'], unique=True)
    with op.batch_alter_table('reservations') as batch:
        batch.create_foreign_key('fk_reservation_customer_tenant', 'customer_records',
                                 ['company_id', 'customer_id'], ['company_id', 'id'])
    with op.batch_alter_table('reservation_elements') as batch:
        batch.create_foreign_key('fk_reservation_element_owner', 'setup_elements',
                                 ['company_id', 'element_id'], ['company_id', 'id'])


def downgrade():
    with op.batch_alter_table('reservation_elements') as batch:
        batch.drop_constraint('fk_reservation_element_owner', type_='foreignkey')
    with op.batch_alter_table('reservations') as batch:
        batch.drop_constraint('fk_reservation_customer_tenant', type_='foreignkey')
    op.drop_index('uq_setup_elements_tenant_id', table_name='setup_elements')
    op.drop_index('uq_customer_records_tenant_id', table_name='customer_records')
