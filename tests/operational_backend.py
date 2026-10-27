"""Run the same operational acceptance scenarios against either real backend.

Every PostgreSQL application gets a fresh schema in a dedicated *_test database.
Test source uses the same named SQL executor as production; no SQL is translated.
"""
import atexit
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os
from uuid import uuid4
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from online.app import create_app as application_factory
from unittest.mock import patch

_schemas = {}
_apps = []


def create_app(db_path=None, *, seed_demo=None):
    url = os.environ.get('DBS_TEST_POSTGRES_URL')
    if not url:
        return application_factory(db_path, seed_demo=seed_demo)
    parsed = make_url(url)
    if parsed.get_backend_name() != 'postgresql' or not (parsed.database or '').endswith('_test'):
        raise RuntimeError('Operational tests require a dedicated PostgreSQL database ending in _test')
    key = str(db_path) if db_path is not None else uuid4().hex
    if key not in _schemas:
        schema = 'dbs_workflow_' + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as c:
            c.execute(text('CREATE SCHEMA ' + schema))
        scoped = parsed.update_query_dict({'options': '-csearch_path=' + schema}).render_as_string(hide_password=False)
        config = Config('alembic.ini')
        config.set_main_option('sqlalchemy.url', scoped.replace('%', '%%'))
        command.upgrade(config, 'head')
        _schemas[key] = (admin, schema, scoped)
    scoped = _schemas[key][2]
    with patch.dict(os.environ, {'DIRECTBOOKING_DATABASE_URL': scoped, 'DIRECTBOOKING_ENV': 'test',
                                 'DIRECTBOOKING_SEED_DEMO': '1'}):
        app = application_factory(seed_demo=seed_demo)
    assert app.state.database.dialect_name == 'postgresql'
    _apps.append(app)
    print('Operational application backend: PostgreSQL; isolated schema:', _schemas[key][1])
    return app


@atexit.register
def cleanup():
    for app in _apps:
        app.state.database.engine.dispose()
    for admin, schema, _ in _schemas.values():
        with admin.begin() as c:
            c.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
        admin.dispose()


def reservation_id_for(database, company_id, *, enquiry_id=None, booking_id=None):
    """Read the persisted identity for source-based compatibility assertions."""
    from online.sql import execute_sql
    assert (enquiry_id is None) != (booking_id is None)
    column, value = ('enquiry_id', enquiry_id) if enquiry_id is not None else ('booking_id', booking_id)
    with database.connect() as connection:
        row = execute_sql(connection, f'SELECT id FROM reservations WHERE company_id=:tenant AND {column}=:source',
                          (company_id, value)).fetchone()
    assert row is not None, 'The operational record must retain its canonical reservation identity'
    return int(row[0])


def source_id_from_location(database, company_id, location, *, kind):
    """Validate a canonical redirect before resolving a fixture's legacy ID."""
    from online.sql import execute_sql
    from urllib.parse import urlparse
    import re
    match = re.fullmatch(r'/operations/reservations/(\d+)', urlparse(location).path)
    assert match, location
    assert kind in ('enquiry', 'booking')
    with database.connect() as connection:
        row = execute_sql(connection, f'SELECT {kind}_id FROM reservations WHERE company_id=:tenant AND id=:reservation',
                          (company_id, int(match.group(1)))).fetchone()
    assert row is not None and row[0] is not None, 'Redirect must resolve to the expected operational record'
    return int(row[0])
