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
        if not {'enquiry_id', 'booking_id'} <= {col['name'] for col in inspect(database.engine).get_columns('reservations')}:
            raise RuntimeError('Run the DBS Alembic migrations before starting PostgreSQL: operational reservation identities are missing')
        if 'ck_booking_payments_amount_finite' not in {check['name'] for check in inspect(database.engine).get_check_constraints('booking_payments')}:
            raise RuntimeError('Run the DBS Alembic migrations before starting PostgreSQL: monetary integrity migration is missing')
        _adopt_operational_identities(database)
        return
    engine = create_engine(database.config.database_url)
    try:
        with engine.begin() as connection:
            # Make additive DDL atomic too; pysqlite otherwise defers BEGIN.
            connection.exec_driver_sql('BEGIN IMMEDIATE')
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
                if 'enquiry_id' not in {col['name'] for col in inspect(connection).get_columns('reservations')}:
                    import_module('migrations.versions.0013_operational_identity').upgrade()
                if 'ck_booking_payments_amount_finite' not in {check['name'] for check in inspect(connection).get_check_constraints('booking_payments')}:
                    import_module('migrations.versions.0014_finite_reservation_money').upgrade()
    finally:
        engine.dispose()
    _adopt_operational_identities(database)


def _adopt_operational_identities(database):
    from .sql import execute_sql
    from .reservation_identity_upgrade import adopt_company
    with database.connect() as connection:
        companies = [int(row[0]) for row in execute_sql(connection, 'SELECT id FROM companies ORDER BY id').fetchall()]
    for company_id in companies:
        with database.reservation_write_transaction(company_id) as connection:
            adopt_company(connection, company_id)
