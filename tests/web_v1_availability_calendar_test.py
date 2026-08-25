from __future__ import annotations

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


def complete_element_setup(db, company: int, year: int, element_ids: list[int]) -> None:
    with db.connect() as c:
        seasons = c.execute('SELECT id FROM setup_seasons WHERE company_id=? AND year=?', (company, year)).fetchall()
        people = c.execute('SELECT id FROM setup_person_types WHERE company_id=? AND active=1', (company,)).fetchall()
        for element_id in element_ids:
            for season in seasons:
                c.execute(
                    'INSERT OR REPLACE INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)',
                    (company, year, element_id, int(season['id']), 25.0),
                )
            c.execute('INSERT OR REPLACE INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)', (company, year, element_id, 6))
            for person in people:
                pid = int(person['id'])
                c.execute('INSERT OR REPLACE INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)', (company, year, element_id, pid, 6))
                c.execute('INSERT OR REPLACE INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (company, year, element_id, pid, 0.0))


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

        for type_name in ('Camping', 'B&B'):
            assert operator.post('/setup/element-types', data={'csrf': csrf, 'name': type_name, 'id': ''}, follow_redirects=False).status_code == 303
        for name, type_name in (('Pitch 1', 'Camping'), ('Pitch 2', 'Camping'), ('Room 1', 'B&B'), ('Room 2', 'B&B')):
            assert operator.post('/setup/elements', data={'csrf': csrf, 'id': '', 'name': name, 'element_type': type_name, 'pricing_method': 'Per night'}, follow_redirects=False).status_code == 303
        assert operator.post('/setup/years/new', data={'csrf': csrf, 'year': '2035'}, follow_redirects=False).status_code == 303

        with db.connect() as c:
            season = c.execute('SELECT id FROM setup_seasons WHERE company_id=? AND year=? LIMIT 1', (company, 2035)).fetchone()
            if season is None:
                c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (company, 2035, 'Season', '2035-01-01', '2035-12-31'))
            ids = {str(r['name']): int(r['id']) for r in c.execute('SELECT id,name FROM setup_elements WHERE company_id=?', (company,)).fetchall()}
        p1, p2, room1, room2 = ids['Pitch 1'], ids['Pitch 2'], ids['Room 1'], ids['Room 2']
        complete_element_setup(db, company, 2035, [p1, p2, room1, room2])

        # Existing booking and closure remain visibly unavailable in the lower calendar.
        now = iso_now()
        with db.connect() as c:
            customer = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (company, 'Alice', 'Smith', 'alice@example.test', '', now, now)).lastrowid)
            booking = int(c.execute("INSERT INTO bookings(company_id,reference,customer_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (company, 'BK2035-101', customer, 'confirmed', '2035-07-10', '2035-07-13', 'EUR', 75, '{}', '', now, now)).lastrowid)
            c.execute("INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json) VALUES (?,?,?,?,?,?,?,?,?)", (company, booking, p2, '2035-07-10', '2035-07-13', 'Per night', 25, 75, '{}'))
        assert operator.post('/setup/elements/availability/save', data={'csrf': csrf, 'element_id': str(p2), 'id': '', 'start_date': '2035-07-20', 'end_date': '2035-07-23', 'reason': 'Pitch damaged'}, follow_redirects=False).status_code == 303

        page = operator.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-08', 'arrival': '2035-07-10', 'departure': '2035-07-12'})
        assert page.status_code == 200
        assert all(x in page.text for x in ('Availability Calendar', 'Availability / alternatives', 'Pitch 1', 'Pitch 2', 'Alice Smith', 'BK2035-101', 'Pitch damaged', 'Available = clear'))
        assert f'/operations/bookings/{booking}' in page.text
        assert 'Check availability' not in page.text
        assert 'overflow:auto' in page.text and 'position:sticky;left:0' in page.text
        assert 'class="cal-cell available date-pick' in page.text

        # First hold establishes the booking anchor window: 10-20 October.
        first = operator.post('/availability/hold', data={'csrf': csrf, 'element_id': str(p1), 'arrival_date': '2035-10-10', 'departure_date': '2035-10-20'})
        assert first.status_code == 200 and first.json()['ok'] is True
        basket = operator.get('/availability/basket').json()
        assert basket['count'] == 1 and basket['anchor'] == {'arrival_date': '2035-10-10', 'departure_date': '2035-10-20'}

        # Browsing another Element Type keeps the original anchor dates in the lower workspace.
        browse_rooms = operator.get('/availability/calendar', params={'element_type': 'B&B', 'arrival': '2035-10-10', 'departure': '2035-10-20'})
        assert browse_rooms.status_code == 200
        assert all(x in browse_rooms.text for x in ('Booking in progress', 'Pitch 1', 'progress-scroll', 'Availability / alternatives — B&amp;B', 'Room 1', 'Room 2'))
        assert 'anchorArr="2035-10-10"' in browse_rooms.text and 'anchorDep="2035-10-20"' in browse_rooms.text
        assert 'progressScroll.scrollLeft=scrollBox.scrollLeft' in browse_rooms.text

        # Add a shorter B&B stay inside the original Camping stay.
        room_hold = operator.post('/availability/hold', data={'csrf': csrf, 'element_id': str(room1), 'arrival_date': '2035-10-12', 'departure_date': '2035-10-17'})
        assert room_hold.status_code == 200 and room_hold.json()['ok'] is True
        basket = operator.get('/availability/basket').json()
        assert basket['count'] == 2
        items = {item['element_name']: item for item in basket['items']}
        assert items['Pitch 1']['arrival_date'] == '2035-10-10'
        assert items['Room 1']['arrival_date'] == '2035-10-12' and items['Room 1']['departure_date'] == '2035-10-17'

        # Both selected Elements are now rows in the upper calendar; there is no floating basket.
        combined = operator.get('/availability/calendar', params={'element_type': 'B&B', 'arrival': '2035-10-10', 'departure': '2035-10-20'})
        assert combined.status_code == 200
        assert combined.text.count('progress-name') >= 2
        assert 'booking-basket' not in combined.text and 'position:fixed;top:86px' not in combined.text
        assert 'Edit</a>' in combined.text and 'progress-remove' in combined.text

        # Edit Room 1: lower calendar shows all B&B alternatives and an UPDATE action.
        edit = operator.get('/availability/calendar', params={'element_type': 'B&B', 'arrival': '2035-10-12', 'departure': '2035-10-17', 'edit_hold': items['Room 1']['id']})
        assert edit.status_code == 200
        assert all(x in edit.text for x in ('Editing Room 1', 'Room 1', 'Room 2', 'UPDATE', 'edit_hold'))

        # Replace Room 1 with Room 2 and shorten its dates. Pitch 1 must remain untouched.
        updated = operator.post('/availability/basket/update', data={'csrf': csrf, 'hold_id': str(items['Room 1']['id']), 'element_id': str(room2), 'arrival_date': '2035-10-13', 'departure_date': '2035-10-16'})
        assert updated.status_code == 200 and updated.json()['ok'] is True
        basket = operator.get('/availability/basket').json()
        updated_items = {item['element_name']: item for item in basket['items']}
        assert set(updated_items) == {'Pitch 1', 'Room 2'}
        assert updated_items['Pitch 1']['arrival_date'] == '2035-10-10' and updated_items['Pitch 1']['departure_date'] == '2035-10-20'
        assert updated_items['Room 2']['arrival_date'] == '2035-10-13' and updated_items['Room 2']['departure_date'] == '2035-10-16'
        assert basket['anchor'] == {'arrival_date': '2035-10-10', 'departure_date': '2035-10-20'}

        # Removing one upper-calendar row releases only that Element.
        removed = operator.post('/availability/basket/remove', data={'csrf': csrf, 'hold_id': str(updated_items['Room 2']['id'])})
        assert removed.status_code == 200 and removed.json()['ok'] is True
        remaining = operator.get('/availability/basket').json()['items']
        assert [item['element_name'] for item in remaining] == ['Pitch 1']

        with db.connect() as c:
            actions = {str(r['action']) for r in c.execute('SELECT action FROM audit_log WHERE company_id=?', (company,)).fetchall()}
        assert {'ELEMENT_HOLD_UPDATED', 'ELEMENT_HOLD_REMOVED'}.issubset(actions)
        assert operator.post('/availability/holds/release', data={'csrf': csrf}).status_code == 200

        # Customer view still exposes no booking/customer identity.
        customer_view = TestClient(app)
        login(customer_view, 'customer@forestview.test', 'Customer013!')
        customer_page = customer_view.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-08'})
        assert customer_page.status_code == 200
        assert 'Alice Smith' not in customer_page.text and 'BK2035-101' not in customer_page.text
        assert 'Unavailable' in customer_page.text
        assert customer_view.get(f'/operations/bookings/{booking}').status_code == 403

    print('Direct Booking Web V1 two-level availability calendar + booking timeline test: passed')


if __name__ == '__main__':
    main()
