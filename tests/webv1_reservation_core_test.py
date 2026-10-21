"""Build 302 reservation lifecycle regression tests."""
import sqlite3
import unittest
from online.webv1_reservation_core import allowed_transition, inventory_conflict, transition, ReservationError


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


if __name__ == '__main__':
    unittest.main()
