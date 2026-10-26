from __future__ import annotations
from operational_backend import create_app
from online.sql import execute_sql, insert_sql

import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online import BUILD
from online.app import COOKIE_NAME
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
            forest = int(execute_sql(c, "SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            riverside = int(execute_sql(c, "SELECT id FROM companies WHERE name='Riverside Cabins'").fetchone()['id'])
            now = '2026-08-21T18:00:00+00:00'
            forest_customer = insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,created_at,updated_at) VALUES (:q261_0,:q261_1,:q261_2,:q261_3,:q261_4,:q261_5)', (forest,'Demo','Forest','forest@example.test',now,now)).lastrowid
            riverside_customer = insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,created_at,updated_at) VALUES (:q262_0,:q262_1,:q262_2,:q262_3,:q262_4,:q262_5)', (riverside,'Demo','River','river@example.test',now,now)).lastrowid
            enquiry = insert_sql(c, 'INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,created_at,updated_at) VALUES (:q263_0,:q263_1,:q263_2,:q263_3,:q263_4,:q263_5,:q263_6,:q263_7,:q263_8)', (forest,forest_customer,'new','website','2026-09-01','2026-09-03',2,now,now)).lastrowid
            offer = insert_sql(c, 'INSERT INTO offers(company_id,enquiry_id,customer_id,status,total_amount,created_at,updated_at) VALUES (:q264_0,:q264_1,:q264_2,:q264_3,:q264_4,:q264_5,:q264_6)', (forest,enquiry,forest_customer,'draft',100.0,now,now)).lastrowid
            booking = insert_sql(c, 'INSERT INTO bookings(company_id,reference,customer_id,enquiry_id,offer_id,status,arrival_date,departure_date,total_amount,created_at,updated_at) VALUES (:q265_0,:q265_1,:q265_2,:q265_3,:q265_4,:q265_5,:q265_6,:q265_7,:q265_8,:q265_9,:q265_10)', (forest,'FV-DEMO-001',forest_customer,enquiry,offer,'confirmed','2026-09-01','2026-09-03',100.0,now,now)).lastrowid
            execute_sql(c, 'INSERT INTO arrivals(company_id,booking_id,customer_id,arrival_type,status,created_at,updated_at) VALUES (:q258_0,:q258_1,:q258_2,:q258_3,:q258_4,:q258_5,:q258_6)', (forest,booking,forest_customer,'booked','expected',now,now))
            execute_sql(c, 'INSERT INTO arrivals(company_id,customer_id,arrival_type,status,created_at,updated_at) VALUES (:q259_0,:q259_1,:q259_2,:q259_3,:q259_4,:q259_5)', (forest,None,'walk_in','arrived',now,now))
            execute_sql(c, 'INSERT INTO enquiries(company_id,customer_id,status,source,created_at,updated_at) VALUES (:q260_0,:q260_1,:q260_2,:q260_3,:q260_4,:q260_5)', (riverside,riverside_customer,'new','phone',now,now))

        client = TestClient(app)
        health = client.get('/health')
        assert health.status_code == 200
        assert health.json()['build'] == BUILD

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
        assert f'Online Build {BUILD}' in page.text

        with db.connect() as c:
            meta = execute_sql(c, "SELECT value FROM web_schema_meta WHERE key='schema_version'").fetchone()
            assert meta and meta['value'] == 'web-v1-foundation'
            for table in ('customer_records','enquiries','offers','bookings','booking_elements','booking_people','booking_addons','arrivals','self_checkin_tokens'):
                exists = execute_sql(c, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:q267_0", (table,)).fetchone()
                assert exists, f'Missing Web V1 table: {table}'

    print('Direct Booking Web V1 foundation test: passed')


if __name__ == '__main__':
    main()
