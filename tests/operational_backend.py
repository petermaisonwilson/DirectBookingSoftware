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
