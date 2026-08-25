from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME, create_app
from online.database import iso_now
from online.webv1 import register_web_v1


def login(client: TestClient, email: str, password: str) -> None:
    assert client.post('/login', data={'email': email, 'password': password}, follow_redirects=False).status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'calendar.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        operator = TestClient(app)
        login(operator, 'operator@forestview.test', 'Operator013!')
        ctx = db.session_context(operator.cookies.get(COOKIE_NAME)); csrf = str(ctx['csrf_token'])
        with db.connect() as c:
            company = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            defaults = c.execute('SELECT * FROM booking_status_definitions WHERE company_id=? ORDER BY display_order', (company,)).fetchall()
            assert [r['name'] for r in defaults[:4]] == ['Enquiry / Held', 'Deposit Paid', 'Balance Paid', 'On Site']
            held_status = next(r for r in defaults if r['internal_state'] == 'HELD')
            balance_status = next(r for r in defaults if r['name'] == 'Balance Paid')
            released_status = next(r for r in defaults if r['internal_state'] == 'RELEASED')
            assert int(held_status['blocks_availability']) == 1 and int(held_status['expiry_minutes']) == 10

        assert operator.post('/setup/element-types', data={'csrf': csrf, 'name': 'Camping', 'id': ''}, follow_redirects=False).status_code == 303
        for name in ('Pitch 1', 'Pitch 2'):
            assert operator.post('/setup/elements', data={'csrf': csrf, 'id': '', 'name': name, 'element_type': 'Camping', 'pricing_method': 'Per night', 'base_price': '25'}, follow_redirects=False).status_code == 303
        assert operator.post('/setup/years/new', data={'csrf': csrf, 'year': '2035'}, follow_redirects=False).status_code == 303
        assert operator.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': '', 'name': 'Electric hook up', 'pricing_method': 'Per night'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            season = c.execute('SELECT id FROM setup_seasons WHERE company_id=? AND year=? LIMIT 1', (company, 2035)).fetchone()
            if season is None:
                c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (company, 2035, 'Season', '2035-01-01', '2035-12-31'))
            p1 = int(c.execute("SELECT id FROM setup_elements WHERE company_id=? AND name='Pitch 1'", (company,)).fetchone()['id'])
            p2 = int(c.execute("SELECT id FROM setup_elements WHERE company_id=? AND name='Pitch 2'", (company,)).fetchone()['id'])
            electric = int(c.execute("SELECT id FROM setup_addons WHERE company_id=? AND name='Electric hook up'", (company,)).fetchone()['id'])
            c.execute('INSERT OR REPLACE INTO setup_type_addons VALUES (?,?,?,?,?,?,?,?)', (company, 2035, 'Camping', electric, 1, 1, 1, 5.0))
            c.execute('INSERT OR REPLACE INTO setup_element_addons VALUES (?,?,?,?,?,?,?,?)', (company, 2035, p2, electric, 'N', None, None, None))

            # Availability now requires complete setup. Give both test pitches
            # valid Seasonal Pricing, occupancy, person limits and explicit prices.
            seasons = c.execute('SELECT id FROM setup_seasons WHERE company_id=? AND year=?', (company, 2035)).fetchall()
            people = c.execute('SELECT id FROM setup_person_types WHERE company_id=? AND active=1', (company,)).fetchall()
            for element_id in (p1, p2):
                for season_row in seasons:
                    c.execute(
                        'INSERT OR REPLACE INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)',
                        (company, 2035, element_id, int(season_row['id']), 25.0),
                    )
                c.execute(
                    'INSERT OR REPLACE INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)',
                    (company, 2035, element_id, 6),
                )
                for person in people:
                    pid = int(person['id'])
                    c.execute(
                        'INSERT OR REPLACE INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)',
                        (company, 2035, element_id, pid, 6),
                    )
                    c.execute(
                        'INSERT OR REPLACE INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)',
                        (company, 2035, element_id, pid, 0.0),
                    )

        assert operator.post('/setup/elements/availability/save', data={'csrf': csrf, 'element_id': str(p2), 'id': '', 'start_date': '2035-07-12', 'end_date': '2035-07-15', 'reason': 'Pitch damaged'}, follow_redirects=False).status_code == 303

        now = iso_now()
        with db.connect() as c:
            customer = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (company, 'Alice', 'Smith', 'alice@example.test', '', now, now)).lastrowid)
            booking = int(c.execute("INSERT INTO bookings(company_id,reference,customer_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (company, 'BK2035-101', customer, 'confirmed', '2035-07-10', '2035-07-13', 'EUR', 75, '{}', '', now, now)).lastrowid)
            c.execute("INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json) VALUES (?,?,?,?,?,?,?,?,?)", (company, booking, p1, '2035-07-10', '2035-07-13', 'Per night', 25, 75, '{}'))
            booking_status = c.execute('SELECT s.* FROM bookings b JOIN booking_status_definitions s ON s.id=b.workflow_status_id WHERE b.id=?', (booking,)).fetchone()
            assert booking_status['name'] == 'Deposit Paid'

            enquiry = int(c.execute("INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (company, customer, 'new', 'Test', '2035-07-20', '2035-07-23', 2, '', now, now)).lastrowid)
            c.execute("INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at) VALUES (?,?,?,?,?,?,?)", (enquiry, company, 'Camping', p2, 75, '{}', now))
            e = c.execute('SELECT * FROM enquiries WHERE id=?', (enquiry,)).fetchone()
            assert int(e['workflow_status_id']) == int(held_status['id']) and e['availability_expires_at']

        operations = operator.get('/operations')
        assert operations.status_code == 200 and 'Open Availability Calendar' in operations.text

        page = operator.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-08', 'arrival': '2035-07-10', 'departure': '2035-07-12'})
        assert page.status_code == 200
        assert all(text in page.text for text in ('Availability Calendar', 'Pitch 1', 'Pitch 2', 'Alice Smith', 'BK2035-101', 'Deposit Paid', 'Pitch damaged', 'Enquiry / Held', 'Available = clear'))
        assert f'/operations/bookings/{booking}' in page.text

        cells = re.findall(r'class="cal-cell [^"]+"([^>]*)', page.text)
        assert cells and all('grid-column:' in attrs and 'grid-row:1' in attrs for attrs in cells)

        assert 'Check availability' not in page.text
        assert 'overflow:auto' in page.text and 'max-height:520px' in page.text
        assert 'position:sticky;top:0' in page.text and 'position:sticky;left:0' in page.text
        assert 'class="cal-cell available date-pick' in page.text and 'data-date="2035-07-16"' in page.text
        assert "departureInput.addEventListener('change',goToSelection)" in page.text
        assert "departureInput.value=chosen" in page.text
        assert 'selected-date' in page.text and 'selected-start' in page.text
        assert 'Available for your selected dates' in page.text and '✕ Electric hook up' in page.text

        # Real browser POSTs must use the URL-encoded format consumed by app.form_data.
        assert "new URLSearchParams()" in page.text
        assert "application/x-www-form-urlencoded;charset=UTF-8" in page.text
        assert "new FormData()" not in page.text
        assert "data.error||data.detail||'Unable to hold that Element'" in page.text

        # With no explicit manual start, the selected stay is centred with dates on both sides.
        centred = operator.get('/availability/calendar', params={'element_type': 'Camping', 'arrival': '2035-10-10', 'departure': '2035-10-13'})
        assert centred.status_code == 200
        assert 'id="calendar-start" type="date" name="start" value="2035-09-28"' in centred.text
        assert 'data-date="2035-10-01"' in centred.text and 'data-date="2035-10-20"' in centred.text
        assert 'scrollBox.clientWidth/2' in centred.text

        # Anything displayed as available must be accepted by the hold endpoint in the same session.
        assert 'Pitch 1' in centred.text and 'Select &amp; hold' in centred.text
        hold = operator.post('/availability/hold', data={'csrf': csrf, 'element_id': str(p1), 'arrival_date': '2035-10-10', 'departure_date': '2035-10-13'})
        assert hold.status_code == 200 and hold.json()['ok'] is True
        held = operator.get('/availability/holds').json()['holds']
        assert any(item['element_id'] == p1 for item in held)
        assert operator.post('/availability/holds/release', data={'csrf': csrf}).status_code == 200

        # Long stays retain useful context before and after the selected period.
        long_page = operator.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-08-01', 'arrival': '2035-08-01', 'departure': '2035-09-15'})
        assert long_page.status_code == 200 and '--days:59' in long_page.text and 'data-date="2035-09-15"' in long_page.text

        blocked_by_enquiry = operator.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-07-20', 'departure': '2035-07-22'}).json()['elements']
        assert 'Pitch 2' not in {e['name'] for e in blocked_by_enquiry}
        enquiry_page = operator.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-18'})
        assert f'Enquiry #{enquiry}' in enquiry_page.text and 'Enquiry / Held' in enquiry_page.text
        with db.connect() as c:
            c.execute("UPDATE enquiries SET availability_expires_at=datetime('now','-1 minute') WHERE id=?", (enquiry,))
        after_expiry = operator.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-07-20', 'departure': '2035-07-22'}).json()['elements']
        assert 'Pitch 2' in {e['name'] for e in after_expiry}

        detail = operator.get(f'/operations/bookings/{booking}')
        assert detail.status_code == 200 and all(text in detail.text for text in ('BK2035-101', 'Alice Smith', '10/07/2035', '13/07/2035', 'Pitch 1', 'Deposit Paid'))

        change_definition = operator.post('/setup/booking-statuses/save', data={
            'csrf': csrf, 'id': str(balance_status['id']), 'name': 'Balance Paid', 'short_name': 'Paid', 'colour': '#123456',
            'display_order': '30', 'internal_state': 'CONFIRMED', 'expiry_minutes': '', 'blocks_availability': '1'
        }, follow_redirects=False)
        assert change_definition.status_code == 303
        change_booking = operator.post('/operations/bookings/status', data={'csrf': csrf, 'booking_id': str(booking), 'status_id': str(balance_status['id'])}, follow_redirects=False)
        assert change_booking.status_code == 303
        changed_page = operator.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-08'})
        assert 'Balance Paid' in changed_page.text and 'background:#123456' in changed_page.text

        assert operator.post('/operations/bookings/status', data={'csrf': csrf, 'booking_id': str(booking), 'status_id': str(released_status['id'])}, follow_redirects=False).status_code == 303
        released_search = operator.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-07-10', 'departure': '2035-07-12'}).json()['elements']
        assert 'Pitch 1' in {e['name'] for e in released_search}

        customer_view = TestClient(app)
        login(customer_view, 'customer@forestview.test', 'Customer013!')
        customer_page = customer_view.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-08'})
        assert customer_page.status_code == 200
        assert 'Alice Smith' not in customer_page.text and 'BK2035-101' not in customer_page.text and 'Balance Paid' not in customer_page.text
        assert 'Unavailable' in customer_page.text
        assert customer_view.get(f'/operations/bookings/{booking}').status_code == 403

        statuses_page = operator.get('/setup/booking-statuses')
        assert statuses_page.status_code == 200 and all(x in statuses_page.text for x in ('Booking Statuses', 'Calendar colour', 'Blocks availability', 'Future email automation'))
        with db.connect() as c:
            actions = {str(r['action']) for r in c.execute('SELECT action FROM audit_log WHERE company_id=?', (company,)).fetchall()}
        assert {'BOOKING_STATUS_SAVED', 'BOOKING_STATUS_CHANGED'}.issubset(actions)

    print('Direct Booking Web V1 calendar usability + status workflow test: passed')


if __name__ == '__main__':
    main()
