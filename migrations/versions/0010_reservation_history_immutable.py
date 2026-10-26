"""Enforce the agreed append-only reservation history on both engines."""
from alembic import op

revision = '0010_history_immutable'
down_revision = '0009_reservation_history'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("""CREATE FUNCTION reservation_history_reject_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'Reservation history is append-only'; END; $$""")
        op.execute('''CREATE TRIGGER reservation_history_immutable
            BEFORE UPDATE OR DELETE ON reservation_history
            FOR EACH ROW EXECUTE FUNCTION reservation_history_reject_mutation()''')
    else:
        for action in ('UPDATE', 'DELETE'):
            op.execute(f'''CREATE TRIGGER reservation_history_no_{action.lower()}
                BEFORE {action} ON reservation_history BEGIN
                SELECT RAISE(ABORT, 'Reservation history is append-only'); END''')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER reservation_history_immutable ON reservation_history')
        op.execute('DROP FUNCTION reservation_history_reject_mutation()')
    else:
        for action in ('update', 'delete'):
            op.execute(f'DROP TRIGGER reservation_history_no_{action}')
