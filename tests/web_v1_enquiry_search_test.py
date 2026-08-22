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
        app = create_app(Path(temp_dir) / 'enquiry-search.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)

        with db.connect() as c:
            forest = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            riverside = int(c.execute("SELECT id FROM companies WHERE name='Riverside Cabins'").fetchone()['id'])
            now = '2026-08-22T10:00:00+00:00'
            alice = int(c.execute(
                "INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (forest, 'Alice', 'Walker', 'alice@example.test', '0611111111', now, now),
            ).lastrowid)
            bob = int(c.execute(
                "INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (forest, 'Bob', 'Smith', 'bob@example.test', '0622222222', now, now),
            ).lastrowid)
            river = int(c.execute(
                "INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (riverside, 'River', 'Guest', 'river@example.test', '0633333333', now, now),
            ).lastrowid)

            enquiry_alice = int(c.execute(
                '''INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (forest, alice, 'new', 'Phone', '2026-09-10', '2026-09-12', 2, 'Electric pitch', now, now),
            ).lastrowid)
            enquiry_bob = int(c.execute(
                '''INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (forest, bob, 'qualified', 'Website', '2026-10-01', '2026-10-05', 4, 'Family stay', now, now),
            ).lastrowid)
            enquiry_river = int(c.execute(
                '''INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (riverside, river, 'new', 'Phone', '2026-09-11', '2026-09-13', 2, 'Other client', now, now),
            ).lastrowid)

        login(client, 'operator@forestview.test', 'Operator013!')

        operations = client.get('/operations')
        assert operations.status_code == 200
        assert 'Open Enquiry Search' in operations.text

        all_enquiries = client.get('/operations/enquiries')
        assert all_enquiries.status_code == 200
        assert f'#{enquiry_alice}' in all_enquiries.text
        assert f'#{enquiry_bob}' in all_enquiries.text
        assert f'#{enquiry_river}' not in all_enquiries.text
        assert 'River Guest' not in all_enquiries.text

        by_name = client.get('/operations/enquiries?q=Alice')
        assert 'Alice Walker' in by_name.text
        assert 'Bob Smith' not in by_name.text

        by_email = client.get('/operations/enquiries?q=bob%40example.test')
        assert 'Bob Smith' in by_email.text
        assert 'Alice Walker' not in by_email.text

        by_phone = client.get('/operations/enquiries?q=0611111111')
        assert 'Alice Walker' in by_phone.text
        assert 'Bob Smith' not in by_phone.text

        by_status = client.get('/operations/enquiries?status=qualified')
        assert 'Bob Smith' in by_status.text
        assert 'Alice Walker' not in by_status.text

        by_source = client.get('/operations/enquiries?source=web')
        assert 'Bob Smith' in by_source.text
        assert 'Alice Walker' not in by_source.text

        by_arrival = client.get('/operations/enquiries?arrival_from=2026-09-01&arrival_to=2026-09-30')
        assert 'Alice Walker' in by_arrival.text
        assert 'Bob Smith' not in by_arrival.text

        by_departure = client.get('/operations/enquiries?departure_from=2026-10-01&departure_to=2026-10-31')
        assert 'Bob Smith' in by_departure.text
        assert 'Alice Walker' not in by_departure.text

        detail = client.get(f'/operations/enquiries/{enquiry_alice}')
        assert detail.status_code == 200
        assert f'Enquiry #{enquiry_alice}' in detail.text
        assert 'Alice Walker' in detail.text
        assert 'Electric pitch' in detail.text
        assert f'/operations/customers/{alice}' in detail.text

        # Direct URL must not expose another Client's enquiry.
        assert client.get(f'/operations/enquiries/{enquiry_river}').status_code == 404
        client.post('/logout', follow_redirects=False)

        login(client, 'operator@riverside.test', 'Operator013!')
        river_list = client.get('/operations/enquiries')
        assert river_list.status_code == 200
        assert 'River Guest' in river_list.text
        assert 'Alice Walker' not in river_list.text
        assert 'Bob Smith' not in river_list.text
        client.post('/logout', follow_redirects=False)

        login(client, 'customer@forestview.test', 'Customer013!')
        assert client.get('/operations/enquiries').status_code == 403
        client.post('/logout', follow_redirects=False)

        login(client, 'supervisor@directbooking.test', 'Supervisor013!')
        assert client.get('/operations/enquiries').status_code == 403
        csrf = csrf_for(client, db)
        enter = client.post(f'/support/start/{forest}', data={'csrf': csrf}, follow_redirects=False)
        assert enter.status_code == 303
        support_list = client.get('/operations/enquiries')
        assert support_list.status_code == 200
        assert 'Alice Walker' in support_list.text
        assert 'River Guest' not in support_list.text
        assert 'SUPPORT MODE' in support_list.text

    print('Direct Booking Web V1 Enquiry Search test: passed')


if __name__ == '__main__':
    main()
