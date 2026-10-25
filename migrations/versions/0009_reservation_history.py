"""Append-only reservation history for the single-record lifecycle."""
from alembic import op
import sqlalchemy as sa

revision = '0009_reservation_history'
down_revision = '0008_unified_reservations'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'reservation_history',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('company_id', sa.Integer, nullable=False),
        sa.Column('reservation_id', sa.Integer, nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('old_state', sa.String(32)),
        sa.Column('new_state', sa.String(32)),
        sa.Column('actor', sa.String(255), nullable=False),
        sa.Column('reason', sa.Text, nullable=False, server_default=''),
        sa.Column('details_json', sa.Text, nullable=False, server_default='{}'),
        sa.Column('created_at', sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(
            ['company_id', 'reservation_id'],
            ['reservations.company_id', 'reservations.id'],
            name='fk_reservation_history_tenant'),
    )
    op.create_index('idx_reservation_history_parent', 'reservation_history',
                    ['company_id', 'reservation_id', 'id'])


def downgrade():
    op.drop_index('idx_reservation_history_parent', table_name='reservation_history')
    op.drop_table('reservation_history')
