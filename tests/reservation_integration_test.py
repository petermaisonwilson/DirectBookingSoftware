"""Real migrated-schema integration contract; PostgreSQL is never emulated.

SQLite: python -m unittest discover -s tests -p reservation_integration_test.py
PostgreSQL: set DBS_TEST_POSTGRES_URL to a disposable migrated test database.
The PostgreSQL URL must name a dedicated database ending in _test.
"""
import os
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

from online.config import RuntimeConfig
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
        command.upgrade(config, 'head')
        cls.engine = create_engine(cls.url)
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
                       lambda: self.create([self.allocation(1, end='2026-11-01')])):
            with self.assertRaises(ReservationError):
                action()
        self.assertEqual(len(self.summary(rid)['history']), 1)
        self.assertEqual(len(list_reservations(self.db, self.cid)), 1)

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
