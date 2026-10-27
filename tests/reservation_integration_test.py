"""Real migrated-schema integration contract; PostgreSQL is never emulated.

SQLite: python -m unittest discover -s tests -p reservation_integration_test.py
PostgreSQL: set DBS_TEST_POSTGRES_URL to a disposable migrated test database.
The PostgreSQL URL must name a dedicated database ending in _test.
"""
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from online.config import RuntimeConfig
from online.sql import execute_sql
from online.database import OnlineDatabase
from online.webv1_reservation_core import ReservationError, inventory_conflict, _execute
from online.webv1_reservation_service import (
    create_enquiry, amend_reservation, change_reservation_state,
    reservation_summary, list_reservations,
)

NOW = '2026-10-26T14:00:00+00:00'


class ReservationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.url = os.environ.get('DBS_TEST_POSTGRES_URL')
        if cls.url:
            parsed = make_url(cls.url)
            if parsed.get_backend_name() != 'postgresql' or not (parsed.database or '').endswith('_test'):
                raise RuntimeError('Use a dedicated PostgreSQL database ending in _test')
        else:
            cls.url = 'sqlite:///' + str(Path(cls.temp.name) / 'reservations.sqlite')
        config = Config('alembic.ini')
        config.set_main_option('sqlalchemy.url', cls.url.replace('%', '%%'))
        command.upgrade(config, '0009_reservation_history')
        cls.engine = create_engine(cls.url)
        cls.metadata = MetaData()
        cls.metadata.reflect(cls.engine)
        # Verify additive integrity migrations preserve existing tenant data.
        with cls.engine.begin() as c:
            def seed(table, **values):
                return c.execute(insert(cls.metadata.tables[table]).values(**values)).inserted_primary_key[0]
            cid = seed('companies', name='migration-test-' + uuid4().hex, created_at=NOW)
            eid = seed('setup_elements', company_id=cid, name='Migration pitch',
                       element_type='Camping', pricing_method='per_night')
            customer = seed('customer_records', company_id=cid, first_name='Existing',
                            last_name='Customer', created_at=NOW, updated_at=NOW)
            rid = seed('reservations', company_id=cid, customer_id=customer, reference='preserved',
                       state='ENQUIRY_HELD', created_at=NOW, updated_at=NOW)
            seed('reservation_elements', company_id=cid, reservation_id=rid, element_id=eid,
                 arrival_date='2026-11-01', departure_date='2026-11-05')
            seed('reservation_history', company_id=cid, reservation_id=rid, event_type='CREATED',
                 actor='Existing staff', created_at=NOW)
        command.upgrade(config, 'head')
        with cls.engine.connect() as c:
            assert c.execute(text('SELECT customer_id FROM reservations WHERE id=:rid'), {'rid': rid}).scalar_one() == customer
            assert c.execute(text('SELECT element_id FROM reservation_elements WHERE reservation_id=:rid'), {'rid': rid}).scalar_one() == eid
            assert c.execute(text('SELECT actor FROM reservation_history WHERE reservation_id=:rid'), {'rid': rid}).scalar_one() == 'Existing staff'
        cls.metadata = MetaData()
        cls.metadata.reflect(cls.engine)
        cls.db = OnlineDatabase(RuntimeConfig('test', cls.url, False, False, '127.0.0.1', 8000))
        print('Reservation integration backend:', cls.engine.dialect.name)

    @classmethod
    def tearDownClass(cls):
        if cls.db.engine is not None:
            cls.db.engine.dispose()
        cls.engine.dispose()
        cls.temp.cleanup()

    def add(self, table, **values):
        with self.engine.begin() as c:
            return c.execute(insert(self.metadata.tables[table]).values(**values)).inserted_primary_key[0]

    def setUp(self):
        self.cid = self.add('companies', name='reservation-test-' + uuid4().hex, created_at=NOW)
        self.other = self.add('companies', name='other-test-' + uuid4().hex, created_at=NOW)
        self.elements = [self.add('setup_elements', company_id=self.cid, name='Pitch ' + str(n),
                                  element_type='Camping', pricing_method='per_night') for n in range(3)]
        self.foreign = self.add('setup_elements', company_id=self.other, name='Foreign',
                                element_type='Camping', pricing_method='per_night')

    def allocation(self, n=0, start='2026-11-01', end='2026-11-05', amount=80):
        return dict(element_id=self.elements[n], arrival_date=start, departure_date=end,
                    total_amount=amount, lead_name='Wilson', pricing_snapshot_json='{"total":80}')

    def create(self, items=None, company=None):
        return create_enquiry(self.db, company or self.cid, uuid4().hex, None,
                              items or [self.allocation()], NOW, actor='Peter')

    def summary(self, rid):
        return reservation_summary(self.db, self.cid, rid)

    def test_create_amend_lifecycle_history_and_availability(self):
        rid = self.create([self.allocation(), self.allocation(1, amount=120)])
        self.assertEqual(self.summary(rid)['reservation']['total_amount'], 200)
        amend_reservation(self.db, self.cid, rid, [self.allocation(2, amount=150)], NOW,
                          actor='Sarah', reason='Change pitch')
        record = self.summary(rid)
        self.assertEqual(len(record['elements']), 1)
        self.assertEqual(record['reservation']['total_amount'], 150)
        self.assertEqual(record['elements'][0]['lead_name'], 'Wilson')
        for state in ('ENQUIRY_RELEASED', 'ENQUIRY_HELD', 'CONFIRMED', 'CANCELLED'):
            change_reservation_state(self.db, self.cid, rid, state, NOW, actor='Peter', reason='Test')
            with self.db.reservation_write_transaction(self.cid) as c:
                conflict = inventory_conflict(c, self.cid, self.elements[2], '2026-11-02', '2026-11-04')
            self.assertEqual(conflict is not None, state in ('ENQUIRY_HELD', 'CONFIRMED'))
        history = self.summary(rid)['history']
        self.assertEqual(len(history), 6)
        self.assertEqual(history[-1]['event_type'], 'CREATED')
        self.assertEqual(history[-2]['actor'], 'Sarah')
        self.assertEqual(history[-2]['reason'], 'Change pitch')
        self.assertEqual(history[0]['old_state'], 'CONFIRMED')
        with self.assertRaises(ReservationError):
            change_reservation_state(self.db, self.cid, rid, 'ENQUIRY_HELD', NOW)
        with self.assertRaises(ReservationError):
            amend_reservation(self.db, self.cid, rid, [self.allocation()], NOW)
        self.assertEqual(len(self.summary(rid)['history']), 6)

    def test_tenant_isolation_and_invalid_allocations(self):
        rid = self.create()
        self.assertIsNone(reservation_summary(self.db, self.other, rid))
        self.assertEqual(list_reservations(self.db, self.other), [])
        for action in (lambda: amend_reservation(self.db, self.other, rid, [self.allocation()], NOW),
                       lambda: change_reservation_state(self.db, self.other, rid, 'CANCELLED', NOW),
                       lambda: self.create([dict(self.allocation(), element_id=self.foreign)]),
                       lambda: self.create([self.allocation(1), self.allocation(1)]),
                       lambda: self.create([self.allocation(1, end='2026-11-01')]),
                       lambda: self.create([self.allocation(1, start='bad')]),
                       lambda: self.create([self.allocation(1, start='20261101')])):
            with self.assertRaises(ReservationError):
                action()
        self.assertEqual(len(self.summary(rid)['history']), 1)
        self.assertEqual(len(list_reservations(self.db, self.cid)), 1)

    def test_customer_ownership_is_enforced_at_service_boundary(self):
        customer = self.add('customer_records', company_id=self.other, first_name='Other',
                            last_name='Tenant', created_at=NOW, updated_at=NOW)
        with self.assertRaisesRegex(ReservationError, 'Customer'):
            create_enquiry(self.db, self.cid, uuid4().hex, customer,
                           [self.allocation()], NOW)
        self.assertEqual(list_reservations(self.db, self.cid), [])
        rid = create_enquiry(self.db, self.other, uuid4().hex, customer,
                             [dict(self.allocation(), element_id=self.foreign)], NOW)
        self.assertEqual(reservation_summary(self.db, self.other, rid)['reservation']['customer_id'], customer)

    def test_concurrent_creation_against_reinstatement_has_one_winner(self):
        rid = self.create()
        change_reservation_state(self.db, self.cid, rid, 'ENQUIRY_RELEASED', NOW)
        self.race([lambda: self.create(), lambda: change_reservation_state(
            self.db, self.cid, rid, 'ENQUIRY_HELD', NOW)])
        blocking = [r for r in list_reservations(self.db, self.cid) if r['state'] == 'ENQUIRY_HELD']
        self.assertEqual(len(blocking), 1)

    def test_closures_and_live_holds_block_all_central_writes(self):
        rid = self.create()
        change_reservation_state(self.db, self.cid, rid, 'ENQUIRY_RELEASED', NOW)
        self.add('element_closures', company_id=self.cid, element_id=self.elements[0],
                 start_date='2026-11-01', end_date='2026-11-05', created_at=NOW, updated_at=NOW)
        self.add('element_holds', company_id=self.cid, element_id=self.elements[1],
                 session_token='live', arrival_date='2026-11-01', departure_date='2026-11-05',
                 renewal_required_at=NOW, expires_at='2999-01-01T00:00:00+00:00', created_at=NOW, updated_at=NOW)
        self.add('element_holds', company_id=self.cid, element_id=self.elements[2],
                 session_token='expired', arrival_date='2026-11-01', departure_date='2026-11-05',
                 renewal_required_at=NOW, expires_at='2000-01-01T00:00:00+00:00', created_at=NOW, updated_at=NOW)
        before = self.summary(rid)
        for action in (lambda: self.create(), lambda: self.create([self.allocation(1)]),
                       lambda: amend_reservation(self.db, self.cid, rid, [self.allocation(1)], NOW),
                       lambda: change_reservation_state(self.db, self.cid, rid, 'ENQUIRY_HELD', NOW)):
            with self.assertRaises(ReservationError):
                action()
        self.assertEqual(self.summary(rid), before)
        self.create([self.allocation(2)])  # Expired holds do not block or require destructive cleanup.

    def test_database_rejects_cross_tenant_references(self):
        rid = self.create()
        customer = self.add('customer_records', company_id=self.other, first_name='Other',
                            last_name='Tenant', created_at=NOW, updated_at=NOW)
        cases = [
            ('UPDATE reservations SET customer_id=:foreign WHERE company_id=:company_id AND id=:rid', customer),
            ('UPDATE reservation_elements SET element_id=:foreign WHERE company_id=:company_id AND reservation_id=:rid', self.foreign),
            ('UPDATE reservation_elements SET reservation_id=:foreign WHERE company_id=:company_id AND reservation_id=:rid',
             self.create([dict(self.allocation(), element_id=self.foreign)], company=self.other)),
            ('UPDATE reservation_history SET reservation_id=:foreign WHERE company_id=:company_id AND reservation_id=:rid', rid),
        ]
        for sql, foreign in cases:
            with self.assertRaises((DBAPIError, sqlite3.DatabaseError)):
                with self.db.reservation_write_transaction(self.cid) as c:
                    _execute(c, sql, {'foreign': foreign, 'company_id': self.cid, 'rid': rid})
        self.assertEqual(self.summary(rid)['elements'][0]['element_id'], self.elements[0])
        self.assertIsNone(self.summary(rid)['reservation']['customer_id'])

    def test_history_is_append_only_in_database(self):
        rid = self.create()
        for sql in ("UPDATE reservation_history SET actor='changed' WHERE company_id=:company_id AND reservation_id=:rid",
                    "DELETE FROM reservation_history WHERE company_id=:company_id AND reservation_id=:rid"):
            with self.assertRaisesRegex((DBAPIError, sqlite3.DatabaseError), 'append-only'):
                with self.db.reservation_write_transaction(self.cid) as c:
                    _execute(c, sql, {'company_id': self.cid, 'rid': rid})
        self.assertEqual(self.summary(rid)['history'][0]['actor'], 'Peter')

    def test_rollback_includes_elements_total_and_history(self):
        rid = self.create()
        before = self.summary(rid)
        with patch('online.webv1_reservation_service._history', side_effect=RuntimeError('history failure')):
            for action in (lambda: self.create([self.allocation(1)]),
                           lambda: amend_reservation(self.db, self.cid, rid, [self.allocation(1)], NOW),
                           lambda: change_reservation_state(self.db, self.cid, rid, 'CANCELLED', NOW)):
                with self.assertRaisesRegex(RuntimeError, 'history failure'):
                    action()
        self.assertEqual(self.summary(rid), before)
        self.assertEqual(len(list_reservations(self.db, self.cid)), 1)

    def test_overlap_boundaries_reinstatement_and_amendment_rollback(self):
        first = self.create()
        self.create([self.allocation(start='2026-11-05', end='2026-11-08')])
        with self.assertRaises(ReservationError):
            self.create([self.allocation(start='2026-11-04', end='2026-11-06')])
        change_reservation_state(self.db, self.cid, first, 'ENQUIRY_RELEASED', NOW)
        replacement = self.create()
        with self.assertRaises(ReservationError):
            change_reservation_state(self.db, self.cid, first, 'ENQUIRY_HELD', NOW)
        before = self.summary(replacement)
        with self.assertRaises(ReservationError):
            amend_reservation(self.db, self.cid, replacement,
                              [self.allocation(start='2026-11-04', end='2026-11-07')], NOW)
        self.assertEqual(self.summary(replacement), before)
        self.assertEqual(self.summary(first)['reservation']['state'], 'ENQUIRY_RELEASED')

    def race(self, actions):
        barrier = Barrier(len(actions))
        def run(action):
            barrier.wait(timeout=10)
            try:
                action()
                return 'won'
            except ReservationError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            results = list(pool.map(run, actions))
        self.assertEqual(sorted(results), ['conflict', 'won'])

    def test_write_transaction_owns_its_isolation(self):
        if self.db.is_sqlite:
            with self.db.reservation_write_transaction(self.cid) as c:
                self.assertTrue(c.in_transaction)
            return
        original = self.db.engine
        try:
            self.db.engine = original.execution_options(isolation_level='REPEATABLE READ')
            with self.db.reservation_write_transaction(self.cid) as c:
                self.assertEqual(c.execute(text('SHOW transaction_isolation')).scalar_one(), 'read committed')
            self.race([lambda: self.create(), lambda: self.create()])
        finally:
            self.db.engine = original

    def test_concurrent_creation_has_exactly_one_winner(self):
        self.race([lambda: self.create(), lambda: self.create()])
        self.assertEqual(len(list_reservations(self.db, self.cid)), 1)

    def test_concurrent_amendment_has_exactly_one_winner(self):
        ids = [self.create([self.allocation(n)]) for n in (0, 1)]
        self.race([lambda rid=rid: amend_reservation(self.db, self.cid, rid,
                    [self.allocation(2)], NOW) for rid in ids])
        self.assertEqual(sum(len(self.summary(rid)['history']) for rid in ids), 3)

    def test_concurrent_reinstatement_has_exactly_one_winner(self):
        ids = []
        for _ in range(2):
            rid = self.create()
            change_reservation_state(self.db, self.cid, rid, 'ENQUIRY_RELEASED', NOW)
            ids.append(rid)
        self.race([lambda rid=rid: change_reservation_state(self.db, self.cid, rid,
                    'ENQUIRY_HELD', NOW) for rid in ids])
        self.assertEqual(sorted(self.summary(rid)['reservation']['state'] for rid in ids),
                         ['ENQUIRY_HELD', 'ENQUIRY_RELEASED'])

    def test_operational_writers_recheck_canonical_inventory(self):
        from online.webv1_availability import create_or_replace_hold
        from online.webv1_booking_amendments import apply_amendment
        rid = self.create()
        # Represent a competing commit after the preliminary availability read.
        with patch('online.webv1_availability.availability_state', return_value={'available': True}):
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                create_or_replace_hold(self.db, {'user_id': None}, self.cid, 'basket',
                                       self.elements[0], '2026-11-01', '2026-11-05')
        bid = self.add('bookings', company_id=self.cid, reference=uuid4().hex,
                       arrival_date='2026-12-01', departure_date='2026-12-05', created_at=NOW, updated_at=NOW)
        beid = self.add('booking_elements', company_id=self.cid, booking_id=bid,
                        element_id=self.elements[0], arrival_date='2026-12-01', departure_date='2026-12-05',
                        pricing_method_snapshot='per_night')
        quote = dict(booking_id=bid, booking_element_id=beid, old_arrival='2026-12-01',
                     old_departure='2026-12-05', old_booking_total=0, new_arrival='2026-11-01', new_departure='2026-11-05')
        with self.assertRaisesRegex(ValueError, 'central reservation'):
            apply_amendment(self.db, {'company_id': self.cid, 'role': 'operator', 'acting_company_id': None}, quote)
        with self.db.connect() as c:
            self.assertEqual(execute_sql(c, 'SELECT COUNT(*) AS n FROM element_holds WHERE company_id=:tenant', (self.cid,)).fetchone()[0], 0)
            self.assertEqual(execute_sql(c, 'SELECT arrival_date FROM booking_elements WHERE id=:element', (beid,)).fetchone()[0], '2026-12-01')
        self.assertEqual(len(self.summary(rid)['history']), 1)
        from online.webv1_enquiry_builder import _save
        with self.assertRaisesRegex(ValueError, 'central reservation'):
            _save(self.db, {}, self.cid, None,
                  dict(element_type='Camping', element_id=str(self.elements[0]),
                       arrival_date='2026-11-01', departure_date='2026-11-05'), None)
        from online.webv1_booking_progress import _save_basket_enquiry
        hid = self.add('element_holds', company_id=self.cid, element_id=self.elements[0],
                       session_token='stale-basket', arrival_date='2026-11-01', departure_date='2026-11-05',
                       renewal_required_at=NOW, expires_at='2999-01-01T00:00:00+00:00', created_at=NOW, updated_at=NOW)
        item = dict(id=hid, element_id=self.elements[0], arrival_date='2026-11-01', departure_date='2026-11-05')
        with patch('online.webv1_booking_progress._held_items', return_value=[item]), \
             patch('online.webv1_booking_progress._hold_enquiry_values', return_value=(item, {})), \
             patch('online.webv1_booking_progress._calculate', return_value=({'people_total': 1, 'total': 80}, set(), '')):
            with self.assertRaisesRegex(ValueError, 'central reservation'):
                _save_basket_enquiry(self.db, {}, self.cid, None, 'stale-basket', '')
        with self.db.connect() as c:
            self.assertEqual(execute_sql(c, 'SELECT COUNT(*) AS n FROM enquiries WHERE company_id=:tenant', (self.cid,)).fetchone()[0], 0)

    def test_legacy_booking_and_multi_and_single_enquiry_conflicts(self):
        for kind in ('booking', 'multi', 'single'):
            with self.subTest(kind=kind):
                item = self.allocation(('booking', 'multi', 'single').index(kind))
                dates = dict(arrival_date=item['arrival_date'], departure_date=item['departure_date'])
                if kind == 'booking':
                    parent = self.add('bookings', company_id=self.cid, reference=uuid4().hex,
                                      created_at=NOW, updated_at=NOW, **dates)
                    self.add('booking_elements', company_id=self.cid, booking_id=parent,
                             element_id=item['element_id'], pricing_method_snapshot='per_night', **dates)
                else:
                    parent = self.add('enquiries', company_id=self.cid, created_at=NOW, updated_at=NOW, **dates)
                    if kind == 'multi':
                        self.add('enquiry_elements', company_id=self.cid, enquiry_id=parent,
                                 element_id=item['element_id'], created_at=NOW, updated_at=NOW, **dates)
                    else:
                        self.add('enquiry_requests', company_id=self.cid, enquiry_id=parent,
                                 element_id=item['element_id'], element_type='Camping', updated_at=NOW)
                with self.assertRaises(ReservationError):
                    self.create([item])
        self.assertEqual(list_reservations(self.db, self.cid), [])


if __name__ == '__main__':
    unittest.main()
