from __future__ import annotations
from operational_backend import create_app
from online.sql import execute_sql, insert_sql

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME
from online.webv1 import register_web_v1


def login(client: TestClient, email: str, password: str) -> None:
    response = client.post('/login', data={'email': email, 'password': password}, follow_redirects=False)
    assert response.status_code == 303


def csrf_for(client: TestClient, db) -> str:
    context = db.session_context(client.cookies.get(COOKIE_NAME))
    assert context is not None
    return str(context['csrf_token'])


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'client-register.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)

        with db.connect() as c:
            forest = int(execute_sql(c, "SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            riverside = int(execute_sql(c, "SELECT id FROM companies WHERE name='Riverside Cabins'").fetchone()['id'])
            now = '2026-08-22T08:00:00+00:00'
            river_customer = int(insert_sql(c,
                'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (:q270_0,:q270_1,:q270_2,:q270_3,:q270_4,:q270_5,:q270_6)',
                (riverside, 'River', 'Guest', 'river@example.test', '555-RIVER', now, now),
            ).lastrowid)

        login(client, 'operator@forestview.test', 'Operator013!')
        register = client.get('/operations/customers')
        assert register.status_code == 200
        assert 'Client Register' in register.text
        assert 'Mobile' in register.text and 'Fixed telephone' in register.text
        assert 'River Guest' not in register.text

        csrf = csrf_for(client, db)
        bad_customer = client.post('/operations/customers/new', data={
            'csrf': csrf, 'first_name': '', 'last_name': '', 'email': 'blank@example.test', 'mobile_phone': '', 'fixed_phone': '', 'notes': ''
        })
        assert bad_customer.status_code == 400
        assert 'Enter both Family name and First name.' in bad_customer.text
        with db.connect() as c:
            assert execute_sql(c, 'SELECT COUNT(*) AS n FROM customer_records WHERE company_id=:q278_0', (forest,)).fetchone()['n'] == 0

        create = client.post('/operations/customers/new', data={
            'csrf': csrf, 'first_name': 'Alice', 'last_name': 'Walker', 'email': 'alice@example.test',
            'mobile_phone': '0612345678', 'fixed_phone': '0299001122', 'address1': '1 Lake Road', 'address2': '',
            'town': 'Testville', 'postcode': '53000', 'country': 'France', 'notes': 'Returning guest test'
        }, follow_redirects=False)
        assert create.status_code == 303
        assert create.headers['location'].startswith('/operations/customers/')
        customer_id = int(create.headers['location'].split('/')[3].split('?')[0])

        with db.connect() as c:
            saved_customer = execute_sql(c, 'SELECT * FROM customer_records WHERE id=:q271_0', (customer_id,)).fetchone()
            assert saved_customer['mobile_phone'] == '0612345678'
            assert saved_customer['fixed_phone'] == '0299001122'
            assert saved_customer['address1'] == '1 Lake Road'
            assert saved_customer['country'] == 'France'

        search = client.get('/operations/customers?q=0612345678')
        assert search.status_code == 200
        assert 'Alice Walker' in search.text
        assert 'river@example.test' not in search.text

        detail = client.get(f'/operations/customers/{customer_id}')
        assert detail.status_code == 200
        assert 'Alice Walker' in detail.text
        assert 'Customer master details' in detail.text
        assert '1 Lake Road' in detail.text
        assert 'Booking history' in detail.text
        assert 'Returning guest test' in detail.text

        # Exact email/mobile/fixed matching suggests the existing master record rather than silently creating a duplicate.
        duplicate = client.post('/operations/customers/new', data={
            'csrf': csrf, 'first_name': 'Alicia', 'last_name': 'Walker', 'email': 'alice@example.test',
            'mobile_phone': '06 12 34 56 78', 'fixed_phone': '02 99 00 11 22', 'address1': '', 'address2': '',
            'town': '', 'postcode': '', 'country': '', 'notes': ''
        })
        assert duplicate.status_code == 409
        assert 'Possible existing Customer found' in duplicate.text
        assert 'Email + Mobile + Fixed telephone' in duplicate.text
        assert 'Use this Customer' in duplicate.text
        with db.connect() as c:
            assert execute_sql(c, 'SELECT COUNT(*) AS n FROM customer_records WHERE company_id=:q279_0', (forest,)).fetchone()['n'] == 1

        csrf = csrf_for(client, db)
        bad_enquiry = client.post(f'/operations/customers/{customer_id}/enquiries/new', data={
            'csrf': csrf, 'arrival_date': '2026-09-10', 'departure_date': '', 'party_size': '2',
            'source': 'Phone', 'notes': 'Should not save'
        })
        assert bad_enquiry.status_code == 400
        assert 'Enter both arrival and departure dates' in bad_enquiry.text
        assert '2026-09-10' in bad_enquiry.text
        with db.connect() as c:
            assert execute_sql(c, 'SELECT COUNT(*) AS n FROM enquiries WHERE company_id=:q280_0', (forest,)).fetchone()['n'] == 0

        backwards = client.post(f'/operations/customers/{customer_id}/enquiries/new', data={
            'csrf': csrf, 'arrival_date': '2026-09-10', 'departure_date': '2026-09-09', 'party_size': '2',
            'source': 'Phone', 'notes': ''
        })
        assert backwards.status_code == 400
        assert 'Departure date must be after arrival date.' in backwards.text

        good = client.post(f'/operations/customers/{customer_id}/enquiries/new', data={
            'csrf': csrf, 'arrival_date': '2026-09-10', 'departure_date': '2026-09-12', 'party_size': '2',
            'source': 'Phone', 'notes': 'Needs electric pitch'
        }, follow_redirects=False)
        assert good.status_code == 303
        assert good.headers['location'].startswith('/operations/reservations/')
        assert good.headers['location'].endswith('?saved=1')

        vague = client.post(f'/operations/customers/{customer_id}/enquiries/new', data={
            'csrf': csrf, 'arrival_date': '', 'departure_date': '', 'party_size': '',
            'source': 'Email', 'notes': 'Dates not known yet'
        }, follow_redirects=False)
        assert vague.status_code == 303

        with db.connect() as c:
            enquiry_rows = execute_sql(c, 'SELECT * FROM enquiries WHERE company_id=:q272_0 AND customer_id=:q272_1 ORDER BY id', (forest, customer_id)).fetchall()
            assert len(enquiry_rows) == 2
            assert enquiry_rows[0]['arrival_date'] == '2026-09-10'
            assert enquiry_rows[0]['departure_date'] == '2026-09-12'
            assert enquiry_rows[0]['party_size'] == 2
            assert enquiry_rows[1]['arrival_date'] is None
            assert enquiry_rows[1]['departure_date'] is None
            actions = [r['action'] for r in execute_sql(c, "SELECT action FROM audit_log WHERE company_id=:q281_0 AND action IN ('CUSTOMER_CREATED','ENQUIRY_CREATED') ORDER BY id", (forest,)).fetchall()]
            assert actions == ['CUSTOMER_CREATED', 'ENQUIRY_CREATED', 'ENQUIRY_CREATED']

            # A newly arrived direct Booking can be explicitly re-linked to the existing master Customer; never automatically.
            second_customer = int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,mobile_phone,fixed_phone,created_at,updated_at)\n                VALUES (:q273_0,:q273_1,:q273_2,:q273_3,:q273_4,:q273_5,:q273_6,:q273_7,:q273_8)', (forest, 'Alice', 'Online', 'alice@example.test', '0612345678', '0612345678', '', now, now)).lastrowid)
            booking_id = int(insert_sql(c, 'INSERT INTO bookings(company_id,reference,customer_id,status,arrival_date,departure_date,total_amount,pricing_snapshot_json,created_at,updated_at)\n                VALUES (:q274_0,:q274_1,:q274_2,:q274_3,:q274_4,:q274_5,:q274_6,:q274_7,:q274_8,:q274_9)', (forest, 'DB26-99999', second_customer, 'confirmed', '2026-10-01', '2026-10-03', 100.0, '{}', now, now)).lastrowid)

        match_page = client.get(f'/operations/bookings/{booking_id}/customer-matches')
        assert match_page.status_code == 200
        assert 'Possible existing Customer' in match_page.text
        assert 'Alice Walker' in match_page.text
        assert 'Add Booking to this Customer master file' in match_page.text
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT customer_id FROM bookings WHERE id=:q282_0', (booking_id,)).fetchone()['customer_id']) == second_customer

        linked = client.post(f'/operations/bookings/{booking_id}/link-customer', data={'csrf': csrf, 'customer_id': str(customer_id)}, follow_redirects=False)
        assert linked.status_code == 303
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT customer_id FROM bookings WHERE id=:q283_0', (booking_id,)).fetchone()['customer_id']) == customer_id
            linked_audit = execute_sql(c, "SELECT action FROM audit_log WHERE company_id=:q275_0 AND entity_type='booking' AND entity_id=:q275_1 ORDER BY id DESC LIMIT 1", (forest, str(booking_id))).fetchone()
            assert linked_audit['action'] == 'BOOKING_LINKED_TO_CUSTOMER'

        detail_after_link = client.get(f'/operations/customers/{customer_id}')
        assert 'DB26-99999' in detail_after_link.text

        # Cross-client record IDs must not leak through direct URLs.
        assert client.get(f'/operations/customers/{river_customer}').status_code == 404
        client.post('/logout', follow_redirects=False)

        login(client, 'operator@riverside.test', 'Operator013!')
        river_register = client.get('/operations/customers')
        assert river_register.status_code == 200
        assert 'River Guest' in river_register.text
        assert 'Alice Walker' not in river_register.text
        client.post('/logout', follow_redirects=False)

        login(client, 'customer@forestview.test', 'Customer013!')
        assert client.get('/operations/customers').status_code == 403
        client.post('/logout', follow_redirects=False)

        login(client, 'supervisor@directbooking.test', 'Supervisor013!')
        assert client.get('/operations/customers').status_code == 403
        supervisor_csrf = csrf_for(client, db)
        enter = client.post(f'/support/start/{forest}', data={'csrf': supervisor_csrf}, follow_redirects=False)
        assert enter.status_code == 303
        support_register = client.get('/operations/customers')
        assert support_register.status_code == 200
        assert 'Alice Walker' in support_register.text
        assert 'River Guest' not in support_register.text
        assert 'SUPPORT MODE' in support_register.text

    print('Direct Booking Web V1 Client Register + Customer matching + New Enquiry test: passed')


if __name__ == '__main__':
    main()
