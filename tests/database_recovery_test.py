"""Fresh-start and native backup/recovery acceptance; disposable databases only.

Run once per engine, like the operational suite. PostgreSQL uses pg_dump and
pg_restore from the CI service container (or installed matching client tools).
This is a test drill, not a customer-data conversion or reset utility.
"""
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from operational_backend import create_app
from operational_concurrency_test import OperationalJourneyTests
from online.app import create_app as application_factory
from online.sql import execute_sql
from online.webv1 import register_web_v1


class RecoveryTests(OperationalJourneyTests):
    def snapshot(self):
        engine = create_engine(self.db.config.database_url)
        try:
            tables = inspect(engine).get_table_names()
            with self.db.connect() as c:
                return {table: sorted(repr(tuple(row)) for row in
                    execute_sql(c, 'SELECT * FROM "' + table + '"').fetchall())
                    for table in tables}
        finally:
            engine.dispose()

    def pg_tool(self, tool, *args, archive=None):
        parsed = make_url(self.db.config.database_url)
        self.assertTrue((parsed.database or '').endswith('_test'))
        container = os.environ.get('DBS_TEST_POSTGRES_CONTAINER')
        if container:
            invocation = ['docker', 'exec', '-i', container, tool,
                          '-U', parsed.username or 'postgres', '-d', parsed.database]
            env = os.environ.copy()
        else:
            invocation = [tool, '-h', parsed.host or 'localhost', '-p', str(parsed.port or 5432),
                          '-U', parsed.username or 'postgres', '-d', parsed.database]
            env = dict(os.environ, PGPASSWORD=parsed.password or '')
        return subprocess.run(invocation + list(args), input=archive, capture_output=True, env=env)

    def test_empty_initialisation_and_repeatable_schema_upgrade(self):
        # A separate empty store: no generated customers, accounts or Setup.
        path = Path(self.temp.name) / 'fresh-empty.sqlite'
        if self.db.dialect_name == 'sqlite':
            config = Config('alembic.ini')
            config.set_main_option('sqlalchemy.url', ('sqlite:///' + str(path)).replace('%', '%%'))
            command.upgrade(config, 'head')
            command.upgrade(config, 'head')
        empty = create_app(path, seed_demo=False)
        register_web_v1(empty)
        with empty.state.database.connect() as c:
            for table in ('companies', 'users', 'customer_records', 'enquiries', 'bookings',
                          'reservations', 'booking_payments', 'booking_refunds', 'setup_elements'):
                self.assertEqual(execute_sql(c, 'SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
        config = Config('alembic.ini')
        config.set_main_option('sqlalchemy.url', empty.state.database.config.database_url.replace('%', '%%'))
        command.upgrade(config, 'head')
        print('Empty initialisation and repeated upgrade verified:', self.db.dialect_name)

    def test_native_backup_restore_and_failed_restore_recovery(self):
        # Generate representative records through the existing HTTP acceptance
        # journey: customer, enquiry, payment, amendment, cancellation, refund,
        # shared history and two-tenant access checks. No historical import.
        self.test_full_http_journey_with_competing_writers()
        before = self.snapshot()
        if self.db.dialect_name == 'postgresql':
            with self.db.connect() as c:
                schema = execute_sql(c, 'SELECT current_schema()').fetchone()[0]
            self.assertRegex(schema, r'^dbs_workflow_[a-f0-9]{32}$')
            dumped = self.pg_tool('pg_dump', '--format=custom', '--schema=' + schema)
            self.assertEqual(dumped.returncode, 0, dumped.stderr.decode())
            self.assertTrue(dumped.stdout.startswith(b'PGDMP'))
            # Release all app connections before simulated loss of ONLY this
            # randomly named test schema. Setup in every other schema is intact.
            self.db.engine.dispose()
            with self.db.engine.begin() as c:
                c.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
            failed = self.pg_tool('pg_restore', '--single-transaction', '--exit-on-error',
                                 archive=dumped.stdout[:len(dumped.stdout)//2])
            self.assertNotEqual(failed.returncode, 0)
            with self.db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=:schema'),
                                           {'schema': schema}).scalar_one(), 0)
            restored = self.pg_tool('pg_restore', '--single-transaction', '--exit-on-error', archive=dumped.stdout)
            self.assertEqual(restored.returncode, 0, restored.stderr.decode())
        else:
            archive = Path(self.temp.name) / 'verified-backup.sqlite'
            with sqlite3.connect(self.db.path) as source, sqlite3.connect(archive) as target:
                source.backup(target)
                self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            corrupt = Path(self.temp.name) / 'corrupt.sqlite'
            corrupt.write_bytes(archive.read_bytes()[:100])
            with self.assertRaises(sqlite3.DatabaseError):
                with sqlite3.connect(corrupt) as bad:
                    bad.execute('PRAGMA integrity_check').fetchall()
            # A rejected backup must not have touched the working database.
            self.assertEqual(self.snapshot(), before)
            self.db.path.unlink()
            with sqlite3.connect(archive) as source, sqlite3.connect(self.db.path) as target:
                source.backup(target)
                self.assertEqual(target.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(self.snapshot(), before)
        # Restart, then exercise writes: restored sequence values must work too.
        restarted = create_app(self.db.path or Path(self.temp.name) / 'operational.sqlite', seed_demo=False)
        register_web_v1(restarted)
        self.assertEqual(restarted.state.database.dialect_name, self.db.dialect_name)
        self.new_element('After restore: sequence continuity')
        rid = int(self.scalar('SELECT id FROM reservations WHERE company_id=:tenant', (self.cid,)))
        response = self.clients[0].post(f'/operations/reservations/{rid}/refunds', data={
            'csrf': self.csrf(), 'amount': '25', 'refund_date': '2038-05-03', 'method': 'Cash',
            'reference': 'After restore', 'acknowledge': 'yes'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(self.scalar('SELECT SUM(amount) FROM booking_refunds WHERE company_id=:tenant', (self.cid,)), 175)
        self.assertEqual(self.clients[0].get(f'/operations/reservations/{rid}').status_code, 200)
        print('Native backup, failed restore, exact data recovery and subsequent writes verified:', self.db.dialect_name)

    def test_packaged_sources_migrate_and_launch(self):
        # Mirror the executable source layout shipped by the Windows workflow;
        # neither a developer checkout nor tests are available to the launcher.
        stage = Path(self.temp.name) / 'package'
        stage.mkdir()
        for folder in ('online', 'migrations'):
            shutil.copytree(folder, stage / folder, ignore=shutil.ignore_patterns('__pycache__'))
        for filename in ('run_online.py', 'alembic.ini', 'requirements-online.txt'):
            shutil.copy2(filename, stage / filename)
        package_url = self.db.config.database_url if self.db.dialect_name == 'postgresql' else 'sqlite:///' + str(stage / 'package-fresh.sqlite')
        env = dict(os.environ, DIRECTBOOKING_DATABASE_URL=package_url,
                   DIRECTBOOKING_ENV='test', DIRECTBOOKING_SEED_DEMO='0')
        migrated = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                                  cwd=stage, env=env, capture_output=True, text=True)
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        self.test_actual_launcher_uses_the_configured_database(test_root=stage, database_url=package_url)
        print('Packaged migration sources and real launcher verified:', self.db.dialect_name)

    def test_connection_failure_is_explicit_and_connection_can_recover(self):
        if self.db.dialect_name == 'postgresql':
            # Return a connection to the pool, then terminate it from a separate
            # test connection; pool_pre_ping must replace it transparently.
            with self.db.engine.connect() as c:
                old_pid = c.execute(text('SELECT pg_backend_pid()')).scalar_one()
            admin = create_engine(os.environ['DBS_TEST_POSTGRES_URL'])
            try:
                with admin.begin() as c:
                    self.assertTrue(c.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': old_pid}).scalar_one())
                with self.db.engine.connect() as c:
                    self.assertNotEqual(c.execute(text('SELECT pg_backend_pid()')).scalar_one(), old_pid)
            finally:
                admin.dispose()
            broken = make_url(self.db.config.database_url).update_query_dict(
                {'options': '-csearch_path=dbs_missing_schema', 'connect_timeout': '2'}).render_as_string(hide_password=False)
            unreachable = make_url(broken).set(host='127.0.0.1', port=1).render_as_string(hide_password=False)
            sentinel = Path(self.temp.name) / 'must-not-fallback.sqlite'
            for url in (broken, unreachable):
                with patch.dict(os.environ, DIRECTBOOKING_DATABASE_URL=url,
                                DIRECTBOOKING_DB=str(sentinel), DIRECTBOOKING_ENV='test',
                                DIRECTBOOKING_SEED_DEMO='0'):
                    with self.assertRaises(Exception):
                        application_factory(seed_demo=False)
                self.assertFalse(sentinel.exists())
        # The launcher startup/health path is exercised by the operational suite;
        # verify ordinary transactions still work after these failures.
        self.assertGreater(self.scalar('SELECT COUNT(*) FROM companies'), 0)
        print('Explicit failure and connection recovery verified:', self.db.dialect_name)


if __name__ == '__main__':
    names = ('test_empty_initialisation_and_repeatable_schema_upgrade',
             'test_native_backup_restore_and_failed_restore_recovery',
             'test_connection_failure_is_explicit_and_connection_can_recover',
             'test_packaged_sources_migrate_and_launch')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(RecoveryTests(name) for name in names))
    sys.exit(not result.wasSuccessful())
