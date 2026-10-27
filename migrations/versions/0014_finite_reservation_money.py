"""Reject non-finite monetary values consistently on SQLite and PostgreSQL.

PostgreSQL supports NaN and infinities that SQLite handles differently. These
are not valid prices, payments or refunds; no stored amount is changed here.
"""
from alembic import op
import sqlalchemy as sa

revision = '0014_finite_reservation_money'
down_revision = '0013_operational_identity'
branch_labels = None
depends_on = None

AMOUNTS = {
    'reservations': ('total_amount',),
    'reservation_elements': ('total_amount',),
    'booking_payments': ('amount',),
    'booking_refunds': ('amount',),
    'booking_cancellations': ('refund_amount', 'retained_amount'),
    'booking_amendments': ('old_booking_total', 'new_booking_total', 'manual_discount'),
}


def upgrade():
    for table, columns in AMOUNTS.items():
        existing = sa.inspect(op.get_bind()).get_check_constraints(table)
        # Preserve unnamed native SQLite constraints during table recreation.
        checks = tuple(sa.CheckConstraint(check['sqltext'], name=f'ck_{table}_retained_{index}')
                       for index, check in enumerate(existing) if not check.get('name'))
        with op.batch_alter_table(table, table_args=checks) as batch:
            for column in columns:
                batch.create_check_constraint(f'ck_{table}_{column}_finite',
                    f'({column} - {column}) IS NOT NULL AND ({column} - {column}) = 0')


def downgrade():
    for table, columns in reversed(tuple(AMOUNTS.items())):
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                batch.drop_constraint(f'ck_{table}_{column}_finite', type_='check')
