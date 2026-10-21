"""Unified reservation lifecycle contract for Build 302 v3."""

STATES = ('ENQUIRY_HELD', 'ENQUIRY_RELEASED', 'CONFIRMED', 'CANCELLED')
TRANSITIONS = {
    'ENQUIRY_HELD': ('ENQUIRY_RELEASED', 'CONFIRMED', 'CANCELLED'),
    'ENQUIRY_RELEASED': ('ENQUIRY_HELD', 'CONFIRMED', 'CANCELLED'),
    'CONFIRMED': ('CANCELLED',),
    'CANCELLED': (),
}


def allowed_transition(source, target):
    return target in TRANSITIONS.get(source, ())
