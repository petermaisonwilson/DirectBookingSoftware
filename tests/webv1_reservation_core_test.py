"""Build 302 reservation lifecycle regression tests."""
import sqlite3
import unittest
from online.webv1_reservation_core import allowed_transition, inventory_conflict, transition, validate_allocations, ReservationError


class ReservationLifecycleTests(unittest.TestCase):
    def test_master_reservation_read_paths_are_tenant_scoped(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from online.database import OnlineDatabase
        from online.webv1_reservation_service import reservation_summary, list_reservations

        with TemporaryDirectory() as directory:
            database = OnlineDatabase(Path(directory) / 'reservation-read.sqlite')
            with database.connect() as connection:
                connection.executescript("""
                    CREATE TABLE reservations (
                        id INTEGER PRIMARY KEY, company_id INTEGER, state TEXT, created_at TEXT);
                    CREATE TABLE setup_elements (id INTEGER, company_id INTEGER, name TEXT);
                    CREATE TABLE reservation_elements (
                        id INTEGER, company_id INTEGER, reservation_id INTEGER, element_id INTEGER);
                    CREATE TABLE reservation_history (
                        id INTEGER, company_id INTEGER, reservation_id INTEGER, event_type TEXT);
                    INSERT INTO reservations VALUES
                        (1, 1, 'ENQUIRY_HELD', '2026-10-26'),
                        (2, 2, 'CONFIRMED', '2026-10-26');
                    INSERT INTO setup_elements VALUES (10,1,'Pitch A'),(20,2,'Pitch B');
                    INSERT INTO reservation_elements VALUES (1,1,1,10),(2,2,2,20);
                    INSERT INTO reservation_history VALUES (1,1,1,'CREATED'),(2,2,2,'CREATED');
                """)
            summary = reservation_summary(database, 1, 1)
            self.assertEqual(summary['reservation']['id'], 1)
            self.assertEqual(summary['elements'][0]['element_name'], 'Pitch A')
            self.assertEqual(len(summary['history']), 1)
            self.assertIsNone(reservation_summary(database, 1, 2))
            self.assertEqual([row['id'] for row in list_reservations(database, 1)], [1])
            self.assertEqual(list_reservations(database, 1, state='CONFIRMED'), [])

    def test_reservation_reads_through_sqlalchemy_connection(self):
        """Exercise the PostgreSQL-style read API without requiring a server."""
        from sqlalchemy import create_engine, text
        from online.webv1_reservation_service import reservation_summary, list_reservations

        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(text(
                'CREATE TABLE reservations (id INTEGER, company_id INTEGER, state TEXT, created_at TEXT)'))
            connection.execute(text(
                'CREATE TABLE setup_elements (id INTEGER, company_id INTEGER, name TEXT)'))
            connection.execute(text(
                'CREATE TABLE reservation_elements (id INTEGER, company_id INTEGER, reservation_id INTEGER, element_id INTEGER)'))
            connection.execute(text(
                'CREATE TABLE reservation_history (id INTEGER, company_id INTEGER, reservation_id INTEGER, event_type TEXT)'))
            connection.execute(text(
                "INSERT INTO reservations VALUES (1,1,'ENQUIRY_HELD','2026-10-26'),"
                "(2,2,'CONFIRMED','2026-10-26')"))
            connection.execute(text("INSERT INTO setup_elements VALUES (10,1,'Pitch A')"))
            connection.execute(text("INSERT INTO reservation_elements VALUES (1,1,1,10)"))
            connection.execute(text("INSERT INTO reservation_history VALUES (1,1,1,'CREATED')"))

        class SqlAlchemyReadDatabase:
            is_sqlite = False

            def sql_connection(self):
                return engine.connect()

        database = SqlAlchemyReadDatabase()
        self.assertEqual(reservation_summary(database, 1, 1)['elements'][0]['element_name'], 'Pitch A')
        self.assertIsNone(reservation_summary(database, 1, 2))
        self.assertEqual([row['id'] for row in list_reservations(database, 1)], [1])
        self.assertEqual(list_reservations(database, 1, state='CONFIRMED'), [])
        engine.dispose()

    def test_inventory_conflict_via_sqlalchemy_connection(self):
        """Named-parameter inventory checks work with SQLAlchemy connections."""
        from sqlalchemy import create_engine, text

        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(text(
                'CREATE TABLE reservations (id INTEGER, company_id INTEGER, state TEXT)'))
            connection.execute(text(
                'CREATE TABLE reservation_elements (reservation_id INTEGER, company_id INTEGER, '
                'element_id INTEGER, arrival_date TEXT, departure_date TEXT)'))
            connection.execute(text(
                "INSERT INTO reservations VALUES (1,1,'CONFIRMED'),"
                "(2,2,'CONFIRMED'),(3,1,'ENQUIRY_RELEASED')"))
            connection.execute(text(
                "INSERT INTO reservation_elements VALUES "
                "(1,1,10,'2026-11-10','2026-11-15'),"
                "(2,2,10,'2026-11-10','2026-11-15'),"
                "(3,1,11,'2026-11-10','2026-11-15')"))
            self.assertIsNotNone(inventory_conflict(
                connection, 1, 10, '2026-11-12', '2026-11-16'))
            self.assertIsNone(inventory_conflict(
                connection, 1, 10, '2026-11-15', '2026-11-20'))
            self.assertIsNone(inventory_conflict(
                connection, 1, 10, '2026-11-12', '2026-11-16', exclude_reservation_id=1))
            self.assertIsNone(inventory_conflict(
                connection, 1, 11, '2026-11-12', '2026-11-16'))
            self.assertIsNone(inventory_conflict(
                connection, 3, 10, '2026-11-12', '2026-11-16'))
        engine.dispose()

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
            transition(db, 1, 2, 'ENQUIRY_HELD', '2026-10-22', check_legacy=False)
        self.assertEqual(db.execute('SELECT state FROM reservations WHERE id=2').fetchone()[0],
                         'ENQUIRY_RELEASED')
        db.execute('UPDATE reservation_elements SET arrival_date=?,departure_date=? WHERE reservation_id=2',
                   ('2026-11-05','2026-11-08'))
        transition(db, 1, 2, 'ENQUIRY_HELD', '2026-10-22', check_legacy=False)
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


    def test_cancelled_reservation_cannot_be_amended(self):
        from online.webv1_reservation_core import replace_reservation_elements
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE reservations(id INTEGER PRIMARY KEY,company_id INTEGER,state TEXT,
                total_amount REAL,updated_at TEXT);
            INSERT INTO reservations VALUES (1,1,'CANCELLED',50,'');
        """)
        with self.assertRaisesRegex(ReservationError, 'Cancelled'):
            replace_reservation_elements(db,1,1,[
                {'element_id':10,'arrival_date':'2026-11-01','departure_date':'2026-11-05'}
            ],'2026-10-24',check_legacy=False)
        self.assertEqual(db.execute('SELECT total_amount FROM reservations WHERE id=1').fetchone()[0],50)
        db.close()


    def test_create_single_master_enquiry_with_two_elements(self):
        from online.webv1_reservation_core import create_reservation
        from unittest.mock import patch
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE setup_elements(id INTEGER,company_id INTEGER,active INTEGER);
            CREATE TABLE reservations(id INTEGER PRIMARY KEY,company_id INTEGER,reference TEXT,
                customer_id INTEGER,state TEXT,currency TEXT,total_amount REAL,notes TEXT,
                created_at TEXT,updated_at TEXT);
            CREATE TABLE reservation_elements(id INTEGER PRIMARY KEY,company_id INTEGER,
                reservation_id INTEGER,element_id INTEGER,arrival_date TEXT,departure_date TEXT,
                total_amount REAL,pricing_snapshot_json TEXT,lead_name TEXT);
            INSERT INTO setup_elements VALUES (10,1,1),(11,1,1),(10,2,1);
        """)
        items = [
            {'element_id':10,'arrival_date':'2026-11-01','departure_date':'2026-11-05','total_amount':80},
            {'element_id':11,'arrival_date':'2026-11-01','departure_date':'2026-11-05','total_amount':120},
        ]
        with patch('online.webv1_status_availability._booking_conflict', return_value=None), \
             patch('online.webv1_status_availability._enquiry_conflict', return_value=None):
            rid = create_reservation(db,1,'E-100',None,items,'2026-10-24')
        record = db.execute('SELECT * FROM reservations WHERE id=?',(rid,)).fetchone()
        self.assertEqual(record['state'],'ENQUIRY_HELD')
        self.assertEqual(record['total_amount'],200)
        self.assertEqual(record['company_id'],1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM reservation_elements WHERE reservation_id=?',(rid,)).fetchone()[0],2)
        db.close()


if __name__ == '__main__':
    unittest.main()
