"""Reuse the portable reservation migrations for existing local SQLite files.

PostgreSQL uses the explicit Alembic deployment path. Existing unversioned local
files keep their established feature initialisers; only the additive canonical
reservation migrations are applied here, without stamping an invented baseline.
"""
from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def initialise_reservation_storage(database):
    if not database.is_sqlite:
        required = {'reservations', 'reservation_elements', 'reservation_history'}
        missing = required - set(inspect(database.engine).get_table_names())
        if missing:
            raise RuntimeError('Run the DBS Alembic migrations before starting PostgreSQL: ' + ', '.join(sorted(missing)))
        return
    engine = create_engine(database.config.database_url)
    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                if 'reservations' not in tables:
                    import_module('migrations.versions.0008_unified_reservations').upgrade()
                elif 'reservation_elements' not in tables:
                    raise RuntimeError('Incomplete local reservation schema; restore or repair the interrupted migration before starting')
                if 'reservation_history' not in tables:
                    import_module('migrations.versions.0009_reservation_history').upgrade()
                triggers = set(connection.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='reservation_history'"
                )).scalars())
                if not {'reservation_history_no_update', 'reservation_history_no_delete'} <= triggers:
                    if triggers & {'reservation_history_no_update', 'reservation_history_no_delete'}:
                        raise RuntimeError('Incomplete local reservation-history protection')
                    import_module('migrations.versions.0010_reservation_history_immutable').upgrade()
                constraints = {fk['name'] for fk in inspect(connection).get_foreign_keys('reservations')}
                if 'fk_reservation_customer_tenant' not in constraints:
                    import_module('migrations.versions.0011_reservation_tenant_refs').upgrade()
    finally:
        engine.dispose()
