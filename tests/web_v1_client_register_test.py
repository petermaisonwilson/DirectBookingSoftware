from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
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
            forest = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            riverside = int(c.execute("SELECT id FROM companies WHERE name='Riverside Cabins'").fetchone()['id'])
            now = '2026-08-22T08:00:00+00:00'
            river_customer = int(c.execute(
                "INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (riverside, 'River', 'Guest', 'river@example.test', '555-RIVER', now, now),
            ).lastrowid)

        login(client, 'operator@forestview.test', 'Operator013!')
        register = client.get('/operations/customers')
        assert register.status_code == 200
        assert 'Client Register' in register.text
        assert 'River Guest' not in register.text

        csrf = csrf_for(client, db)
        bad_customer = client.post('/operations/customers/new', data={
            'csrf': csrf, 'first_name': '', 'last_name': '', 'email': 'blank@example.test', 'phone': '', 'notes': ''
        })
        assert bad_customer.status_code == 400
        assert 'Enter at least a first name or last name.' in bad_customer.text
        with db.connect() as c:
            assert c.execute('SELECT COUNT(*) AS n FROM customer_records WHERE company_id=?', (forest,)).fetchone()['n'] == 0

        create = client.post('/operations/customers/new', data={
            'csrf': csrf, 'first_name': 'Alice', 'last_name': 'Walker', 'email': 'alice@example.test',
            'phone': '0612345678', 'notes': 'Returning guest test'
        }, follow_redirects=False)
        assert create.status_code == 303
        assert create.headers['location'].startswith('/operations/customers/')
        customer_id = int(create.headers['location'].split('/')[3].split('?')[0])

        search = client.get('/operations/customers?q=0612345678')
        assert search.status_code == 200
        assert 'Alice Walker' in search.text
        assert 'river@example.test' not in search.text

        detail = client.get(f'/operations/customers/{customer_id}')
        assert detail.status_code == 200
        assert 'Alice Walker' in detail.text
        assert 'Returning guest test' in detail.text

        csrf = csrf_for(client, db)
        bad_enquiry = client.post(f'/operations/customers/{customer_id}/enquiries/new', data={
            'csrf': csrf, 'arrival_date': '2026-09-10', 'departure_date': '', 'party_size': '2',
            'source': 'Phone', 'notes': 'Should not save'
        })
        assert bad_enquiry.status_code == 400
        assert 'Enter both arrival and departure dates' in bad_enquiry.text
        assert '2026-09-10' in bad_enquiry.text
        with db.connect() as c:
            assert c.execute('SELECT COUNT(*) AS n FROM enquiries WHERE company_id=?', (forest,)).fetchone()['n'] == 0

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
        assert good.headers['location'] == f'/operations/customers/{customer_id}?enquiry_created=1'

        vague = client.post(f'/operations/customers/{customer_id}/enquiries/new', data={
            'csrf': csrf, 'arrival_date': '', 'departure_date': '', 'party_size': '',
            'source': 'Email', 'notes': 'Dates not known yet'
        }, follow_redirects=False)
        assert vague.status_code == 303

        with db.connect() as c:
            enquiry_rows = c.execute('SELECT * FROM enquiries WHERE company_id=? AND customer_id=? ORDER BY id', (forest, customer_id)).fetchall()
            assert len(enquiry_rows) == 2
            assert enquiry_rows[0]['arrival_date'] == '2026-09-10'
            assert enquiry_rows[0]['departure_date'] == '2026-09-12'
            assert enquiry_rows[0]['party_size'] == 2
            assert enquiry_rows[1]['arrival_date'] is None
            assert enquiry_rows[1]['departure_date'] is None
            actions = [r['action'] for r in c.execute("SELECT action FROM audit_log WHERE company_id=? AND action IN ('CUSTOMER_CREATED','ENQUIRY_CREATED') ORDER BY id", (forest,)).fetchall()]
            assert actions == ['CUSTOMER_CREATED', 'ENQUIRY_CREATED', 'ENQUIRY_CREATED']

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

    print('Direct Booking Web V1 Client Register + New Enquiry test: passed')


if __name__ == '__main__':
    main()
