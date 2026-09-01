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

        assert operator.post('/setup/element-types', data={'csrf': csrf, 'name': 'Camping', 'id': ''}, follow_redirects=False).status_code == 303
        for name in ('Pitch 1', 'Pitch 2'):
            assert operator.post('/setup/elements', data={'csrf': csrf, 'id': '', 'name': name, 'element_type': 'Camping', 'pricing_method': 'Per night', 'base_price': '25'}, follow_redirects=False).status_code == 303
        assert operator.post('/setup/years/new', data={'csrf': csrf, 'year': '2035'}, follow_redirects=False).status_code == 303
        assert operator.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': '', 'name': 'Electric hook up', 'pricing_method': 'Per night'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            p1 = int(c.execute("SELECT id FROM setup_elements WHERE company_id=? AND name='Pitch 1'", (company,)).fetchone()['id'])
            p2 = int(c.execute("SELECT id FROM setup_elements WHERE company_id=? AND name='Pitch 2'", (company,)).fetchone()['id'])
            electric = int(c.execute("SELECT id FROM setup_addons WHERE company_id=? AND name='Electric hook up'", (company,)).fetchone()['id'])
            c.execute('INSERT OR REPLACE INTO setup_type_addons VALUES (?,?,?,?,?,?,?,?)', (company, 2035, 'Camping', electric, 1, 1, 1, 5.0))
            c.execute('INSERT OR REPLACE INTO setup_element_addons VALUES (?,?,?,?,?,?,?,?)', (company, 2035, p2, electric, 'N', None, None, None))

        # Closure appears as a distinct calendar bar and user-facing closure dates use dd/mm/yyyy on the calendar.
        assert operator.post('/setup/elements/availability/save', data={'csrf': csrf, 'element_id': str(p2), 'id': '', 'start_date': '2035-07-12', 'end_date': '2035-07-15', 'reason': 'Pitch damaged'}, follow_redirects=False).status_code == 303

        now = iso_now()
        with db.connect() as c:
            customer = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (company, 'Alice', 'Smith', 'alice@example.test', '', now, now)).lastrowid)
            booking = int(c.execute("INSERT INTO bookings(company_id,reference,customer_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (company, 'BK2035-101', customer, 'confirmed', '2035-07-10', '2035-07-13', 'EUR', 75, '{}', '', now, now)).lastrowid)
            c.execute("INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json) VALUES (?,?,?,?,?,?,?,?,?)", (company, booking, p1, '2035-07-10', '2035-07-13', 'Per night', 25, 75, '{}'))

        operations = operator.get('/operations')
        assert operations.status_code == 200 and 'Open Availability Calendar' in operations.text

        page = operator.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-08', 'arrival': '2035-07-10', 'departure': '2035-07-12'})
        assert page.status_code == 200
        assert all(text in page.text for text in ('Availability Calendar', 'Pitch 1', 'Pitch 2', 'Alice Smith', 'BK2035-101', 'Pitch damaged', 'Booked', 'Closed', 'Held', 'Available = clear'))
        assert f'/operations/bookings/{booking}' in page.text
        # Pitch 1 is booked for selected dates, so only Pitch 2 is offered; its Electric Add-on is unavailable.
        assert 'Available for your selected dates' in page.text and '✕ Electric hook up' in page.text

        detail = operator.get(f'/operations/bookings/{booking}')
        assert detail.status_code == 200 and all(text in detail.text for text in ('BK2035-101', 'Alice Smith', '10/07/2035', '13/07/2035', 'Pitch 1'))

        # Customer sees the same occupancy but never another customer's identity or booking reference.
        customer_view = TestClient(app)
        login(customer_view, 'customer@forestview.test', 'Customer013!')
        customer_page = customer_view.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-08'})
        assert customer_page.status_code == 200 and 'Booked' in customer_page.text
        assert 'Alice Smith' not in customer_page.text and 'BK2035-101' not in customer_page.text
        assert customer_view.get(f'/operations/bookings/{booking}').status_code == 403

        # Exact available result can be held and appears in the basket API.
        free = operator.get('/availability/calendar', params={'element_type': 'Camping', 'start': '2035-07-16', 'arrival': '2035-07-16', 'departure': '2035-07-18'})
        assert free.status_code == 200 and 'Select &amp; hold' in free.text
        held = operator.post('/availability/hold', data={'csrf': csrf, 'element_id': str(p1), 'arrival_date': '2035-07-16', 'departure_date': '2035-07-18'})
        assert held.status_code == 200 and held.json()['ok'] is True
        holds = operator.get('/availability/holds').json()['holds']
        assert any(h['element_name'] == 'Pitch 1' for h in holds)

    print('Direct Booking Web V1 coloured Availability Calendar test: passed')


if __name__ == '__main__':
    main()
