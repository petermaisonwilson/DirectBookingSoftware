from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME, create_app
from online.database import iso_now
from online.webv1 import register_web_v1
from online.webv1_status_availability import availability_state


def login(client: TestClient, email: str, password: str) -> None:
    assert client.post('/login', data={'email': email, 'password': password}, follow_redirects=False).status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'booking-workflow.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        login(client, 'operator@forestview.test', 'Operator013!')
        ctx = db.session_context(client.cookies.get(COOKIE_NAME)); csrf = str(ctx['csrf_token']); cid = int(ctx['company_id'])
        now = iso_now()

        with db.connect() as c:
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (cid, 'Lodge'))
            element_id = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Lake Lodge 1', 'Lodge', 'Per night', 0)).lastrowid)
            c.execute("INSERT OR IGNORE INTO setup_years(company_id,year) VALUES (?,?)", (cid, 2035))
            season_id = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (cid, 2035, 'Summer', '2035-01-01', '2035-12-31')).lastrowid)
            c.execute("INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)", (cid, 2035, element_id, season_id, 100))
            person_id = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active) VALUES (?,?,?,1)", (cid, 'Adult', 'Adult')).lastrowid)
            c.execute("INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)", (cid, 2035, element_id, person_id, 10))
            addon_id = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (cid, 'Breakfast', 'Fixed once')).lastrowid)
            c.execute("INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)", (cid, 2035, 'Lodge', addon_id, 1, 1, 4, 20))

            c.execute('INSERT OR REPLACE INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)', (cid, 2035, element_id, 6))
            people = c.execute('SELECT id FROM setup_person_types WHERE company_id=? AND active=1', (cid,)).fetchall()
            for person in people:
                pid = int(person['id'])
                c.execute('INSERT OR REPLACE INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)', (cid, 2035, element_id, pid, 6))
                existing_price = c.execute('SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, 2035, element_id, pid)).fetchone()
                if existing_price is None:
                    c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (cid, 2035, element_id, pid, 0.0))

            customer_id = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (cid, 'Workflow', 'Tester', 'workflow@example.test', '123', now, now)).lastrowid)
            enquiry_id = int(c.execute("INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (cid, customer_id, 'new', 'Test', '2035-06-10', '2035-06-13', 2, 'Booking workflow test', now, now)).lastrowid)
            snapshot = {
                'element_type': 'Lodge', 'element_id': element_id, 'element_name': 'Lake Lodge 1', 'year': 2035,
                'nights': 3, 'people_total': 2, 'addon_when': {str(addon_id): 'every_day'},
                'addon_days': {}, 'addon_people': {}, 'addon_person_days': {}, 'selected_addons': [addon_id],
                'lines': [
                    {'item': 'Lake Lodge 1', 'rule': 'Per night', 'amount': 300.0},
                    {'item': 'Adult', 'rule': '2 adults', 'amount': 60.0},
                    {'item': 'Breakfast', 'rule': 'Fixed once', 'amount': 20.0},
                ], 'total': 380.0,
            }
            c.execute("INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at) VALUES (?,?,?,?,?,?,?)", (enquiry_id, cid, 'Lodge', element_id, 380.0, json.dumps(snapshot), now))
            c.execute("INSERT INTO enquiry_people(enquiry_id,company_id,person_type_id,quantity) VALUES (?,?,?,?)", (enquiry_id, cid, person_id, 2))
            c.execute("INSERT INTO enquiry_addons(enquiry_id,company_id,addon_id,quantity) VALUES (?,?,?,?)", (enquiry_id, cid, addon_id, 1))
            confirmed = c.execute("SELECT id,colour FROM booking_status_definitions WHERE company_id=? AND active=1 AND internal_state='CONFIRMED' ORDER BY display_order,id LIMIT 1", (cid,)).fetchone()
            released = c.execute("SELECT id FROM booking_status_definitions WHERE company_id=? AND active=1 AND internal_state='RELEASED' ORDER BY display_order,id LIMIT 1", (cid,)).fetchone()
            confirmed_id = int(confirmed['id']); confirmed_colour = str(confirmed['colour']); released_id = int(released['id'])

        detail = client.get(f'/operations/enquiries/{enquiry_id}')
        assert detail.status_code == 200
        assert 'Convert to Booking' in detail.text and 'Confirm / Convert to Booking' in detail.text

        before = availability_state(db, cid, element_id, '2035-06-10', '2035-06-13')
        assert before['available'] is False and before['state'] == 'ENQUIRY'

        converted = client.post(f'/operations/enquiries/{enquiry_id}/convert', data={'csrf': csrf, 'workflow_status_id': str(confirmed_id)}, follow_redirects=False)
        assert converted.status_code == 303 and '/operations/bookings/' in converted.headers['location']
        booking_id = int(converted.headers['location'].split('/operations/bookings/')[1].split('?')[0])

        with db.connect() as c:
            b = c.execute('SELECT * FROM bookings WHERE id=? AND company_id=?', (booking_id, cid)).fetchone()
            assert b is not None and b['enquiry_id'] == enquiry_id and float(b['total_amount']) == 380.0
            assert str(b['reference']).startswith('DB') and int(b['workflow_status_id']) == confirmed_id
            assert c.execute('SELECT status FROM enquiries WHERE id=?', (enquiry_id,)).fetchone()['status'] == 'converted'
            be = c.execute('SELECT * FROM booking_elements WHERE booking_id=?', (booking_id,)).fetchone()
            assert be is not None and int(be['element_id']) == element_id and float(be['total_amount']) == 300.0
            bp = c.execute('SELECT * FROM booking_people WHERE booking_element_id=?', (be['id'],)).fetchone()
            assert bp is not None and int(bp['quantity']) == 2 and float(bp['total_amount']) == 60.0
            ba = c.execute('SELECT * FROM booking_addons WHERE booking_element_id=?', (be['id'],)).fetchone()
            assert ba is not None and int(ba['addon_id']) == addon_id and float(ba['total_amount']) == 20.0
            frozen = json.loads(ba['rule_snapshot_json']); assert float(frozen['frozen_amount']) == 20.0
            reference = str(b['reference'])

        after = availability_state(db, cid, element_id, '2035-06-10', '2035-06-13')
        assert after['available'] is False and after['state'] == 'BOOKED' and after['booking_id'] == booking_id
        cal = client.get('/availability/calendar-v2?element_type=Lodge&arrival=2035-06-10&departure=2035-06-13')
        assert cal.status_code == 200 and reference in cal.text and confirmed_colour in cal.text
        assert 'hold-expiry-calendar-refresh' not in cal.text
        assert 'id="global-hold-modal"' in cal.text
        assert 'id="hold-modal"' not in cal.text
        assert 'checkExpiry' not in cal.text and 'releasedTransition' not in cal.text

        with db.connect() as c:
            c.execute('UPDATE setup_element_rates SET rate=999 WHERE company_id=? AND year=? AND element_id=?', (cid, 2035, element_id))
            c.execute('UPDATE setup_person_prices SET rate=999 WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, 2035, element_id, person_id))
        booking_page = client.get(f'/operations/bookings/{booking_id}')
        assert booking_page.status_code == 200 and '€380.00' in booking_page.text and 'Frozen Booking' in booking_page.text

        pay = client.post(f'/operations/bookings/{booking_id}/payments', data={'csrf': csrf, 'amount': '100.00', 'payment_date': '2035-05-01', 'method': 'Card', 'reference': 'PAY-1', 'notes': 'Deposit'}, follow_redirects=False)
        assert pay.status_code == 303
        page = client.get(f'/operations/bookings/{booking_id}')
        assert '€100.00' in page.text and '€280.00' in page.text and 'BOOKING_PAYMENT_RECORDED' in page.text

        change = client.post(f'/operations/bookings/{booking_id}/status', data={'csrf': csrf, 'workflow_status_id': str(released_id)}, follow_redirects=False)
        assert change.status_code == 303
        free = availability_state(db, cid, element_id, '2035-06-10', '2035-06-13')
        assert free['available'] is True
        page = client.get(f'/operations/bookings/{booking_id}')
        assert 'BOOKING_STATUS_CHANGED' in page.text

        ops = client.get('/operations'); assert '/operations/bookings' in ops.text
        register = client.get('/operations/bookings'); assert reference in register.text

        with db.connect() as c:
            actions = [str(r['action']) for r in c.execute("SELECT action FROM audit_log WHERE company_id=? AND entity_type='booking' AND entity_id=?", (cid, str(booking_id))).fetchall()]
            assert 'BOOKING_CREATED' in actions and 'BOOKING_PAYMENT_RECORDED' in actions and 'BOOKING_STATUS_CHANGED' in actions

    print('Direct Booking Web V1 Booking workflow test: passed')


if __name__ == '__main__':
    main()
