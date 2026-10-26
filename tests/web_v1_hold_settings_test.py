from __future__ import annotations
from operational_backend import create_app
from online.sql import execute_sql, insert_sql

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME
from online.webv1 import register_web_v1


def login(client: TestClient, email: str, password: str) -> None:
    assert client.post('/login', data={'email': email, 'password': password}, follow_redirects=False).status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'hold-settings.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database

        operator = TestClient(app)
        login(operator, 'operator@forestview.test', 'Operator013!')
        ctx = db.session_context(operator.cookies.get(COOKIE_NAME))
        csrf = str(ctx['csrf_token'])
        with db.connect() as c:
            company = int(execute_sql(c, "SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            # Complete operating inventory for the hold test. Availability now deliberately
            # requires Seasonal Pricing, occupancy and explicit Person setup before sale.
            execute_sql(c, 'INSERT INTO setup_element_types(company_id,name,active) VALUES (:q19_0,:q19_1,1) ON CONFLICT DO NOTHING', (company, 'Camping'))
            execute_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q20_0,:q20_1,:q20_2,:q20_3,:q20_4,1)', (company, 'Hold Pitch', 'Camping', 'Per night', 0))
            pitch = int(execute_sql(c, "SELECT id FROM setup_elements WHERE company_id=:q31_0 AND name='Hold Pitch'", (company,)).fetchone()['id'])
            execute_sql(c, 'INSERT INTO setup_years(company_id,year) VALUES (:q21_0,:q21_1) ON CONFLICT DO NOTHING', (company, 2035))
            season_id = int(insert_sql(c, 'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q26_0,:q26_1,:q26_2,:q26_3,:q26_4)', (company, 2035, 'Season', '2035-01-01', '2035-12-31')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q22_0,:q22_1,:q22_2,:q22_3,:q22_4)', (company, 2035, pitch, season_id, 20.0))
            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q23_0,:q23_1,:q23_2,:q23_3)', (company, 2035, pitch, 6))
            people = execute_sql(c, 'SELECT id FROM setup_person_types WHERE company_id=:q27_0 AND active=1', (company,)).fetchall()
            for person in people:
                pid = int(person['id'])
                execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (:q24_0,:q24_1,:q24_2,:q24_3,:q24_4) ON CONFLICT(company_id,year,element_id,person_type_id) DO UPDATE SET max_count=excluded.max_count,min_count=excluded.min_count', (company, 2035, pitch, pid, 6))
                execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q25_0,:q25_1,:q25_2,:q25_3,:q25_4) ON CONFLICT(company_id,year,element_id,person_type_id) DO UPDATE SET rate=excluded.rate', (company, 2035, pitch, pid, 0.0))

        # Defaults are the agreed production values.
        page = operator.get('/company/hold-settings')
        assert page.status_code == 200
        assert 'value="600"' in page.text and 'value="60"' in page.text

        # Client/Operator may change the timing to a short test value.
        save = operator.post('/company/hold-settings', data={'csrf': csrf, 'hold_seconds': '30', 'grace_seconds': '12'}, follow_redirects=False)
        assert save.status_code == 303
        with db.connect() as c:
            row = execute_sql(c, 'SELECT hold_seconds,grace_seconds FROM company_hold_settings WHERE company_id=:q28_0', (company,)).fetchone()
            assert int(row['hold_seconds']) == 30 and int(row['grace_seconds']) == 12

        # New holds use the client-defined timing, not the old hard-coded 10 + 1 minutes.
        before = datetime.now(timezone.utc)
        hold = operator.post('/availability/hold', data={
            'csrf': csrf,
            'element_id': str(pitch),
            'arrival_date': '2035-06-01',
            'departure_date': '2035-06-03',
        })
        assert hold.status_code == 200 and hold.json()['ok'] is True
        h = hold.json()['hold']
        assert h['hold_seconds'] == 30 and h['grace_seconds'] == 12
        prompt = datetime.fromisoformat(h['renewal_required_at'])
        expires = datetime.fromisoformat(h['expires_at'])
        assert 27 <= (prompt - before).total_seconds() <= 33
        assert 11 <= (expires - prompt).total_seconds() <= 13

        renewed = operator.post('/availability/holds/renew', data={'csrf': csrf})
        assert renewed.status_code == 200
        assert renewed.json()['hold_seconds'] == 30 and renewed.json()['grace_seconds'] == 12

        # Customer can use holds but cannot view or change the client's timing settings.
        customer = TestClient(app)
        login(customer, 'customer@forestview.test', 'Customer013!')
        customer_ctx = db.session_context(customer.cookies.get(COOKIE_NAME))
        customer_csrf = str(customer_ctx['csrf_token'])
        assert customer.get('/company/hold-settings').status_code == 403
        assert customer.post('/company/hold-settings', data={'csrf': customer_csrf, 'hold_seconds': '99', 'grace_seconds': '99'}, follow_redirects=False).status_code == 403

        # Supervisor may change the selected client's timing while in Support Mode.
        supervisor = TestClient(app)
        login(supervisor, 'supervisor@directbooking.test', 'Supervisor013!')
        sup_ctx = db.session_context(supervisor.cookies.get(COOKIE_NAME)); sup_csrf = str(sup_ctx['csrf_token'])
        assert supervisor.post(f'/support/start/{company}', data={'csrf': sup_csrf}, follow_redirects=False).status_code == 303
        sup_ctx = db.session_context(supervisor.cookies.get(COOKIE_NAME)); sup_csrf = str(sup_ctx['csrf_token'])
        sup_save = supervisor.post('/company/hold-settings', data={'csrf': sup_csrf, 'hold_seconds': '45', 'grace_seconds': '15'}, follow_redirects=False)
        assert sup_save.status_code == 303
        with db.connect() as c:
            row = execute_sql(c, 'SELECT hold_seconds,grace_seconds FROM company_hold_settings WHERE company_id=:q29_0', (company,)).fetchone()
            assert int(row['hold_seconds']) == 45 and int(row['grace_seconds']) == 15
            actions = {str(r['action']) for r in execute_sql(c, 'SELECT action FROM audit_log WHERE company_id=:q32_0', (company,)).fetchall()}
            assert 'HOLD_TIMING_UPDATED' in actions

    print('Direct Booking Web V1 client-defined hold timing test: passed')


if __name__ == '__main__':
    main()
