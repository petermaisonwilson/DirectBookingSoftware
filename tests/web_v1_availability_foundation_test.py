from __future__ import annotations
from operational_backend import create_app
from online.sql import execute_sql, insert_sql

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME
from online.database import iso_now
from online.webv1 import register_web_v1


def login(client: TestClient) -> None:
    assert client.post('/login', data={'email': 'operator@forestview.test', 'password': 'Operator013!'}, follow_redirects=False).status_code == 303


def context(client: TestClient, db):
    return db.session_context(client.cookies.get(COOKIE_NAME))


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'availability.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app); login(client)
        ctx = context(client, db); csrf = str(ctx['csrf_token']); token = client.cookies.get(COOKIE_NAME)
        with db.connect() as c:
            company = int(execute_sql(c, "SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])

        # Create a self-contained Element Type, two Elements, one year and two Add-ons.
        assert client.post('/setup/element-types', data={'csrf': csrf, 'name': 'Camping', 'id': ''}, follow_redirects=False).status_code == 303
        for name in ('Pitch 1', 'Pitch 2'):
            assert client.post('/setup/elements', data={'csrf': csrf, 'id': '', 'name': name, 'element_type': 'Camping', 'pricing_method': 'Per night', 'base_price': '25'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2035'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': '', 'name': 'Electric hook up', 'pricing_method': 'Per night'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': '', 'name': 'Breakfast', 'pricing_method': 'Per quantity'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            p1 = int(execute_sql(c, "SELECT id FROM setup_elements WHERE company_id=:q236_0 AND name='Pitch 1'", (company,)).fetchone()['id'])
            p2 = int(execute_sql(c, "SELECT id FROM setup_elements WHERE company_id=:q237_0 AND name='Pitch 2'", (company,)).fetchone()['id'])
            electric = int(execute_sql(c, "SELECT id FROM setup_addons WHERE company_id=:q238_0 AND name='Electric hook up'", (company,)).fetchone()['id'])
            breakfast = int(execute_sql(c, "SELECT id FROM setup_addons WHERE company_id=:q239_0 AND name='Breakfast'", (company,)).fetchone()['id'])
            # Pitch 1 inherits Electric=Y and Breakfast=Y; Pitch 2 explicitly disables Electric.
            execute_sql(c, 'INSERT INTO setup_type_addons VALUES (:q222_0,:q222_1,:q222_2,:q222_3,:q222_4,:q222_5,:q222_6,:q222_7) ON CONFLICT(company_id,year,element_type,addon_id) DO UPDATE SET allowed=excluded.allowed,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate', (company, 2035, 'Camping', electric, 1, 1, 1, 5.0))
            execute_sql(c, 'INSERT INTO setup_type_addons VALUES (:q223_0,:q223_1,:q223_2,:q223_3,:q223_4,:q223_5,:q223_6,:q223_7) ON CONFLICT(company_id,year,element_type,addon_id) DO UPDATE SET allowed=excluded.allowed,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate', (company, 2035, 'Camping', breakfast, 1, 0, 10, 8.0))
            execute_sql(c, 'INSERT INTO setup_element_addons VALUES (:q224_0,:q224_1,:q224_2,:q224_3,:q224_4,:q224_5,:q224_6,:q224_7) ON CONFLICT(company_id,year,element_id,addon_id) DO UPDATE SET state=excluded.state,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate', (company, 2035, p2, electric, 'N', None, None, None))

            # Availability now deliberately requires complete setup. Give both test pitches
            # valid Seasonal Pricing, occupancy, person limits and explicit person prices.
            seasons = execute_sql(c, 'SELECT id FROM setup_seasons WHERE company_id=:q228_0 AND year=:q228_1', (company, 2035)).fetchall()
            people = execute_sql(c, 'SELECT id FROM setup_person_types WHERE company_id=:q229_0 AND active=1', (company,)).fetchall()
            for element_id in (p1, p2):
                for season in seasons:
                    execute_sql(c,
                        'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q230_0,:q230_1,:q230_2,:q230_3,:q230_4) ON CONFLICT(company_id,year,element_id,season_id) DO UPDATE SET rate=excluded.rate',
                        (company, 2035, element_id, int(season['id']), 25.0),
                    )
                execute_sql(c,
                    'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q227_0,:q227_1,:q227_2,:q227_3) ON CONFLICT(company_id,year,element_id) DO UPDATE SET max_total=excluded.max_total',
                    (company, 2035, element_id, 6),
                )
                for person in people:
                    pid = int(person['id'])
                    execute_sql(c,
                        'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (:q231_0,:q231_1,:q231_2,:q231_3,:q231_4) ON CONFLICT(company_id,year,element_id,person_type_id) DO UPDATE SET max_count=excluded.max_count,min_count=excluded.min_count',
                        (company, 2035, element_id, pid, 6),
                    )
                    execute_sql(c,
                        'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q232_0,:q232_1,:q232_2,:q232_3,:q232_4) ON CONFLICT(company_id,year,element_id,person_type_id) DO UPDATE SET rate=excluded.rate',
                        (company, 2035, element_id, pid, 0.0),
                    )

        elements_page = client.get('/setup/elements')
        assert elements_page.status_code == 200 and 'Availability' in elements_page.text
        availability_page = client.get(f'/setup/elements/availability?element_id={p1}')
        assert availability_page.status_code == 200 and 'Available throughout the operating season' in availability_page.text

        # Both pitches initially free; Add-on indicators differ by Element.
        search = client.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-07-10', 'departure': '2035-07-13'})
        assert search.status_code == 200
        found = {e['name']: e for e in search.json()['elements']}
        assert set(found) == {'Pitch 1', 'Pitch 2'}
        p1_addons = {a['name']: a['available'] for a in found['Pitch 1']['addons']}
        p2_addons = {a['name']: a['available'] for a in found['Pitch 2']['addons']}
        assert p1_addons['Electric hook up'] is True and p2_addons['Electric hook up'] is False
        assert p1_addons['Breakfast'] is True and p2_addons['Breakfast'] is True

        # Closing Pitch 2 removes it from the available-only result for overlapping dates.
        close = client.post('/setup/elements/availability/save', data={'csrf': csrf, 'element_id': str(p2), 'id': '', 'start_date': '2035-07-11', 'end_date': '2035-07-14', 'reason': 'Pitch damaged'}, follow_redirects=False)
        assert close.status_code == 303
        overlapping = client.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-07-10', 'departure': '2035-07-13'}).json()['elements']
        assert [e['name'] for e in overlapping] == ['Pitch 1']
        # Boundary logic: Pitch 2 reopens on the closure end date.
        reopened = client.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-07-14', 'departure': '2035-07-16'}).json()['elements']
        assert {e['name'] for e in reopened} == {'Pitch 1', 'Pitch 2'}

        # Existing booking makes Pitch 1 unavailable and prevents a conflicting closure.
        now = iso_now()
        with db.connect() as c:
            customer = int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (:q233_0,:q233_1,:q233_2,:q233_3,:q233_4,:q233_5,:q233_6)', (company, 'Booked', 'Guest', 'booked@example.test', '', now, now)).lastrowid)
            booking = int(insert_sql(c, 'INSERT INTO bookings(company_id,reference,customer_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at) VALUES (:q234_0,:q234_1,:q234_2,:q234_3,:q234_4,:q234_5,:q234_6,:q234_7,:q234_8,:q234_9,:q234_10,:q234_11)', (company, 'BK2035-001', customer, 'confirmed', '2035-08-01', '2035-08-04', 'EUR', 75, '{}', '', now, now)).lastrowid)
            execute_sql(c, 'INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json) VALUES (:q225_0,:q225_1,:q225_2,:q225_3,:q225_4,:q225_5,:q225_6,:q225_7,:q225_8)', (company, booking, p1, '2035-08-01', '2035-08-04', 'Per night', 25, 75, '{}'))
        booked_search = client.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-08-02', 'departure': '2035-08-03'}).json()['elements']
        assert [e['name'] for e in booked_search] == ['Pitch 2']
        blocked_closure = client.post('/setup/elements/availability/save', data={'csrf': csrf, 'element_id': str(p1), 'id': '', 'start_date': '2035-08-02', 'end_date': '2035-08-05', 'reason': 'Maintenance'}, follow_redirects=False)
        assert blocked_closure.status_code == 303 and 'booking' in blocked_closure.headers['location'].lower()

        # Temporary hold: owner still sees held Element; a second session does not.
        hold = client.post('/availability/hold', data={'csrf': csrf, 'element_id': str(p1), 'arrival_date': '2035-09-01', 'departure_date': '2035-09-04'})
        assert hold.status_code == 200 and hold.json()['ok'] is True
        owner_search = client.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-09-01', 'departure': '2035-09-04'}).json()['elements']
        owner_p1 = next(e for e in owner_search if e['name'] == 'Pitch 1')
        assert owner_p1['state'] == 'HELD_BY_YOU'

        other = TestClient(app); login(other); other_ctx = context(other, db); other_csrf = str(other_ctx['csrf_token'])
        other_search = other.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2035-09-01', 'departure': '2035-09-04'}).json()['elements']
        assert 'Pitch 1' not in {e['name'] for e in other_search}
        competing = other.post('/availability/hold', data={'csrf': other_csrf, 'element_id': str(p1), 'arrival_date': '2035-09-01', 'departure_date': '2035-09-04'})
        assert competing.status_code == 409

        # Force the hold into its one-minute confirmation window; status must request confirmation.
        past_prompt = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec='seconds')
        future_expiry = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat(timespec='seconds')
        with db.connect() as c:
            execute_sql(c, 'UPDATE element_holds SET renewal_required_at=:q226_0,expires_at=:q226_1 WHERE company_id=:q226_2 AND session_token=:q226_3', (past_prompt, future_expiry, company, token))
        status = client.get('/availability/holds').json()['holds']
        assert len(status) == 1 and status[0]['element_name'] == 'Pitch 1' and status[0]['needs_confirmation'] is True
        renewed = client.post('/availability/holds/renew', data={'csrf': csrf})
        assert renewed.status_code == 200 and renewed.json()['count'] == 1
        assert client.get('/availability/holds').json()['holds'][0]['needs_confirmation'] is False

        # Release immediately and prove another session can now acquire it.
        released = client.post('/availability/holds/release', data={'csrf': csrf})
        assert released.status_code == 200 and released.json()['released'] == 1
        other_hold = other.post('/availability/hold', data={'csrf': other_csrf, 'element_id': str(p1), 'arrival_date': '2035-09-01', 'departure_date': '2035-09-04'})
        assert other_hold.status_code == 200 and other_hold.json()['ok'] is True

        # Out-of-season dates return no Elements.
        assert client.get('/availability/search', params={'element_type': 'Camping', 'arrival': '2036-01-01', 'departure': '2036-01-03'}).json()['elements'] == []

        with db.connect() as c:
            actions = {str(r['action']) for r in execute_sql(c, 'SELECT action FROM audit_log WHERE company_id=:q240_0', (company,)).fetchall()}
        assert {'ELEMENT_CLOSURE_SAVED', 'ELEMENT_HOLD_SAVED', 'ELEMENT_HOLDS_RENEWED', 'ELEMENT_HOLDS_RELEASED'}.issubset(actions)

    print('Direct Booking Web V1 availability foundation test: passed')


if __name__ == '__main__':
    main()
