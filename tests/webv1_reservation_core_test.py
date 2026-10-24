"""Build 302 reservation lifecycle regression tests."""
import sqlite3
import unittest
from online.webv1_reservation_core import allowed_transition, inventory_conflict, transition, validate_allocations, ReservationError


class ReservationLifecycleTests(unittest.TestCase):
    def test_cancellation_is_terminal(self):
        self.assertFalse(allowed_transition('CANCELLED', 'CONFIRMED'))
        self.assertFalse(allowed_transition('CANCELLED', 'ENQUIRY_HELD'))

    def test_valid_confirm_and_release(self):
        self.assertTrue(allowed_transition('ENQUIRY_HELD', 'CONFIRMED'))
        self.assertTrue(allowed_transition('ENQUIRY_HELD', 'ENQUIRY_RELEASED'))

    def test_tenant_scoped_conflicts_and_released_space(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE reservations(id INTEGER, company_id INTEGER, state TEXT, updated_at TEXT);
            CREATE TABLE reservation_elements(id INTEGER, company_id INTEGER, reservation_id INTEGER,
                element_id INTEGER, arrival_date TEXT, departure_date TEXT);
            INSERT INTO reservations VALUES (1,1,'ENQUIRY_HELD',''),(2,1,'ENQUIRY_RELEASED',''),
                (3,2,'CONFIRMED',''),(4,1,'CANCELLED','');
            INSERT INTO reservation_elements VALUES (1,1,1,10,'2026-11-01','2026-11-05'),
                (2,1,2,11,'2026-11-01','2026-11-05'),
                (3,2,3,10,'2026-11-01','2026-11-05'),
                (4,1,4,12,'2026-11-01','2026-11-05');
        """)
        self.assertTrue(inventory_conflict(db, 1, 10, '2026-11-04', '2026-11-06'))
        self.assertFalse(inventory_conflict(db, 1, 10, '2026-11-05', '2026-11-06'))
        self.assertFalse(inventory_conflict(db, 1, 11, '2026-11-02', '2026-11-04'))
        self.assertFalse(inventory_conflict(db, 1, 12, '2026-11-02', '2026-11-04'))
        self.assertFalse(inventory_conflict(db, 2, 11, '2026-11-02', '2026-11-04'))
        self.assertTrue(inventory_conflict(db, 2, 10, '2026-11-02', '2026-11-04'))
        self.assertFalse(inventory_conflict(db, 1, 10, '2026-11-02', '2026-11-04', 1))
        with self.assertRaises(ReservationError):
            transition(db, 2, 1, 'CANCELLED', '2026-10-21')
        with self.assertRaises(ReservationError):
            transition(db, 1, 4, 'ENQUIRY_HELD', '2026-10-21')
        db.close()

    def test_validate_allocations_rejects_invalid_and_cross_tenant_elements(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE setup_elements(id INTEGER, company_id INTEGER, active INTEGER);
            CREATE TABLE reservations(id INTEGER, company_id INTEGER, state TEXT);
            CREATE TABLE reservation_elements(id INTEGER, company_id INTEGER, reservation_id INTEGER,
                element_id INTEGER, arrival_date TEXT, departure_date TEXT);
            INSERT INTO setup_elements VALUES (10,1,1),(11,1,0),(20,2,1);
            INSERT INTO reservations VALUES (1,1,'CONFIRMED');
            INSERT INTO reservation_elements VALUES (1,1,1,10,'2026-11-01','2026-11-05');
        """)
        def entry(element, arrival='2026-11-10', departure='2026-11-15'):
            return {'element_id':element,'arrival_date':arrival,'departure_date':departure}
        validate_allocations(db, 1, [entry(10)])
        for allocation in (entry(20), entry(11), entry(10,'2026-11-04','2026-11-06'),
                           entry(10,'2026-11-10','2026-11-10'), entry(10,'bad','2026-11-15')):
            with self.subTest(allocation=allocation), self.assertRaises(ReservationError):
                validate_allocations(db, 1, [allocation])
        with self.assertRaises(ReservationError):
            validate_allocations(db, 1, [entry(10), entry(10,'2026-11-14','2026-11-18')])
        db.close()


    def test_reinstatement_rechecks_space_and_does_not_change_state_on_conflict(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE setup_elements(id INTEGER, company_id INTEGER, active INTEGER);
            CREATE TABLE reservations(id INTEGER, company_id INTEGER, state TEXT, updated_at TEXT);
            CREATE TABLE reservation_elements(id INTEGER, company_id INTEGER, reservation_id INTEGER,
                element_id INTEGER, arrival_date TEXT, departure_date TEXT);
            INSERT INTO setup_elements VALUES (10,1,1);
            INSERT INTO reservations VALUES (1,1,'CONFIRMED',''),(2,1,'ENQUIRY_RELEASED','');
            INSERT INTO reservation_elements VALUES (1,1,1,10,'2026-11-01','2026-11-05'),
                (2,1,2,10,'2026-11-03','2026-11-06');
        """)
        with self.assertRaises(ReservationError):
            transition(db, 1, 2, 'ENQUIRY_HELD', '2026-10-22')
        self.assertEqual(db.execute('SELECT state FROM reservations WHERE id=2').fetchone()[0],
                         'ENQUIRY_RELEASED')
        db.execute('UPDATE reservation_elements SET arrival_date=?,departure_date=? WHERE reservation_id=2',
                   ('2026-11-05','2026-11-08'))
        transition(db, 1, 2, 'ENQUIRY_HELD', '2026-10-22')
        self.assertEqual(db.execute('SELECT state FROM reservations WHERE id=2').fetchone()[0],
                         'ENQUIRY_HELD')
        db.close()


    def test_booking_conversion_rejects_canonical_allocation(self):
        from online.webv1_bookings import _lock_and_verify_conversion
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE reservations(id INTEGER, company_id INTEGER, state TEXT);
            CREATE TABLE reservation_elements(id INTEGER, company_id INTEGER, reservation_id INTEGER,
                element_id INTEGER, arrival_date TEXT, departure_date TEXT);
            CREATE TABLE bookings(id INTEGER, company_id INTEGER, enquiry_id INTEGER);
            INSERT INTO reservations VALUES (1,1,'CONFIRMED');
            INSERT INTO reservation_elements VALUES (1,1,1,10,'2026-11-01','2026-11-05');
        """)
        with self.assertRaisesRegex(ValueError, 'allocated to another reservation'):
            _lock_and_verify_conversion(db, 1, 22, [
                {'element_id':10,'arrival_date':'2026-11-02','departure_date':'2026-11-04'}])
        db.close()


    def test_create_rejects_existing_booking_before_writing(self):
        from online.webv1_reservation_core import create_reservation
        from unittest.mock import patch
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE setup_elements(id INTEGER,company_id INTEGER,active INTEGER);
            CREATE TABLE reservations(id INTEGER PRIMARY KEY,company_id INTEGER,state TEXT);
            CREATE TABLE reservation_elements(id INTEGER,company_id INTEGER,reservation_id INTEGER,
                element_id INTEGER,arrival_date TEXT,departure_date TEXT);
            INSERT INTO setup_elements VALUES (10,1,1);
        """)
        item = {'element_id':10,'arrival_date':'2026-11-01','departure_date':'2026-11-05'}
        with patch('online.webv1_status_availability._booking_conflict', return_value={'id':9}), \
             patch('online.webv1_status_availability._enquiry_conflict', return_value=None):
            with self.assertRaisesRegex(ReservationError, 'already booked'):
                create_reservation(db,1,'E-001',None,[item],'2026-10-24')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],0)
        db.close()


    def test_create_rejects_held_enquiry_before_writing(self):
        from online.webv1_reservation_core import create_reservation
        from unittest.mock import patch
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE setup_elements(id INTEGER,company_id INTEGER,active INTEGER);
            CREATE TABLE reservations(id INTEGER PRIMARY KEY,company_id INTEGER,state TEXT);
            CREATE TABLE reservation_elements(id INTEGER,company_id INTEGER,reservation_id INTEGER,
                element_id INTEGER,arrival_date TEXT,departure_date TEXT);
            INSERT INTO setup_elements VALUES (10,1,1);
        """)
        item = {'element_id':10,'arrival_date':'2026-11-01','departure_date':'2026-11-05'}
        with patch('online.webv1_status_availability._booking_conflict', return_value=None), \
             patch('online.webv1_status_availability._enquiry_conflict', return_value={'id':12}):
            with self.assertRaisesRegex(ReservationError, 'already held'):
                create_reservation(db,1,'E-002',None,[item],'2026-10-24')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],0)
        db.close()


    def test_multi_element_amendment_replaces_elements_and_total(self):
        from online.webv1_reservation_core import replace_reservation_elements
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE setup_elements(id INTEGER,company_id INTEGER,active INTEGER);
            CREATE TABLE reservations(id INTEGER PRIMARY KEY,company_id INTEGER,state TEXT,
                total_amount REAL,updated_at TEXT);
            CREATE TABLE reservation_elements(id INTEGER PRIMARY KEY,company_id INTEGER,reservation_id INTEGER,
                element_id INTEGER,arrival_date TEXT,departure_date TEXT,total_amount REAL,
                pricing_snapshot_json TEXT,lead_name TEXT);
            INSERT INTO setup_elements VALUES (10,1,1),(11,1,1);
            INSERT INTO reservations VALUES (1,1,'ENQUIRY_HELD',50,'');
            INSERT INTO reservation_elements VALUES (1,1,1,10,'2026-11-01','2026-11-05',50,'{}','');
        """)
        items = [
            {'element_id':10,'arrival_date':'2026-11-02','departure_date':'2026-11-06','total_amount':80},
            {'element_id':11,'arrival_date':'2026-11-02','departure_date':'2026-11-06','total_amount':120},
        ]
        replace_reservation_elements(db,1,1,items,'2026-10-24',check_legacy=False)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM reservation_elements').fetchone()[0],2)
        self.assertEqual(db.execute('SELECT total_amount FROM reservations WHERE id=1').fetchone()[0],200)
        db.close()


if __name__ == '__main__':
    unittest.main()
