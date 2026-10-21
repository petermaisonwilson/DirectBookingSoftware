"""Build 302 reservation lifecycle regression tests."""
import unittest
from online.webv1_reservation_core import allowed_transition


class ReservationLifecycleTests(unittest.TestCase):
    def test_cancellation_is_terminal(self):
        self.assertFalse(allowed_transition('CANCELLED', 'CONFIRMED'))
        self.assertFalse(allowed_transition('CANCELLED', 'ENQUIRY_HELD'))

    def test_valid_confirm_and_release(self):
        self.assertTrue(allowed_transition('ENQUIRY_HELD', 'CONFIRMED'))
        self.assertTrue(allowed_transition('ENQUIRY_HELD', 'ENQUIRY_RELEASED'))


if __name__ == '__main__':
    unittest.main()
