from __future__ import annotations

import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
from online.webv1 import register_web_v1


def login(client: TestClient, email: str, password: str) -> None:
    response = client.post('/login', data={'email': email, 'password': password}, follow_redirects=False)
    assert response.status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'webv1.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database

        with db.connect() as c:
            forest = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            riverside = int(c.execute("SELECT id FROM companies WHERE name='Riverside Cabins'").fetchone()['id'])
            now = '2026-08-21T18:00:00+00:00'
            forest_customer = c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,created_at,updated_at) VALUES (?,?,?,?,?,?)", (forest,'Demo','Forest','forest@example.test',now,now)).lastrowid
            riverside_customer = c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,created_at,updated_at) VALUES (?,?,?,?,?,?)", (riverside,'Demo','River','river@example.test',now,now)).lastrowid
            enquiry = c.execute("INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (forest,forest_customer,'new','website','2026-09-01','2026-09-03',2,now,now)).lastrowid
            offer = c.execute("INSERT INTO offers(company_id,enquiry_id,customer_id,status,total_amount,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (forest,enquiry,forest_customer,'draft',100.0,now,now)).lastrowid
            booking = c.execute("INSERT INTO bookings(company_id,reference,customer_id,enquiry_id,offer_id,status,arrival_date,departure_date,total_amount,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (forest,'FV-DEMO-001',forest_customer,enquiry,offer,'confirmed','2026-09-01','2026-09-03',100.0,now,now)).lastrowid
            c.execute("INSERT INTO arrivals(company_id,booking_id,customer_id,arrival_type,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (forest,booking,forest_customer,'booked','expected',now,now))
            c.execute("INSERT INTO arrivals(company_id,customer_id,arrival_type,status,created_at,updated_at) VALUES (?,?,?,?,?,?)", (forest,None,'walk_in','arrived',now,now))
            c.execute("INSERT INTO enquiries(company_id,customer_id,status,source,created_at,updated_at) VALUES (?,?,?,?,?,?)", (riverside,riverside_customer,'new','phone',now,now))

        client = TestClient(app)
        health = client.get('/health')
        assert health.status_code == 200
        assert health.json()['build'] == '282'

        login(client, 'operator@forestview.test', 'Operator013!')
        page = client.get('/operations')
        assert page.status_code == 200
        assert 'Forest View Campsite' in page.text
        assert 'Riverside Cabins' not in page.text
        assert '<strong>1</strong> customer record(s)' in page.text
        assert '<strong>1</strong> enquiry record(s)' in page.text
        assert '<strong>1</strong> offer record(s)' in page.text
        assert '<strong>1</strong> booking record(s)' in page.text
        assert '<strong>2</strong> arrival record(s)' in page.text
        assert 'walk-in/unbooked arrivals' in page.text
        client.post('/logout', follow_redirects=False)

        login(client, 'operator@riverside.test', 'Operator013!')
        page = client.get('/operations')
        assert page.status_code == 200
        assert 'Riverside Cabins' in page.text
        assert 'Forest View Campsite' not in page.text
        assert '<strong>1</strong> customer record(s)' in page.text
        assert '<strong>1</strong> enquiry record(s)' in page.text
        assert '<strong>0</strong> booking record(s)' in page.text
        client.post('/logout', follow_redirects=False)

        login(client, 'customer@forestview.test', 'Customer013!')
        assert client.get('/operations').status_code == 403
        client.post('/logout', follow_redirects=False)

        login(client, 'supervisor@directbooking.test', 'Supervisor013!')
        assert client.get('/operations').status_code == 403
        dashboard = client.get('/dashboard')
        context = db.session_context(client.cookies.get(COOKIE_NAME))
        csrf = str(context['csrf_token'])
        response = client.post(f'/support/start/{forest}', data={'csrf': csrf}, follow_redirects=False)
        assert response.status_code == 303
        page = client.get('/operations')
        assert page.status_code == 200
        assert 'Forest View Campsite' in page.text
        assert 'Riverside Cabins' not in page.text
        assert 'Online Build 282' in page.text

        with db.connect() as c:
            meta = c.execute("SELECT value FROM web_schema_meta WHERE key='schema_version'").fetchone()
            assert meta and meta['value'] == 'web-v1-foundation'
            for table in ('customer_records','enquiries','offers','bookings','booking_elements','booking_people','booking_addons','arrivals','self_checkin_tokens'):
                exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                assert exists, f'Missing Web V1 table: {table}'

    print('Direct Booking Web V1 foundation test: passed')


if __name__ == '__main__':
    main()
