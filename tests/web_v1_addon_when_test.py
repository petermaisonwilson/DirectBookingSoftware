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
    r = client.post('/login', data={'email': email, 'password': password}, follow_redirects=False)
    assert r.status_code == 303


def csrf_for(client: TestClient, db) -> str:
    return str(db.session_context(client.cookies.get(COOKIE_NAME))['csrf_token'])


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'addon-when.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        with db.connect() as c:
            forest = int(execute_sql(c, "SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            execute_sql(c, 'INSERT INTO setup_element_types(company_id,name) VALUES (:q363_0,:q363_1) ON CONFLICT DO NOTHING', (forest, 'Camping Pitch'))
            element_id = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (:q370_0,:q370_1,:q370_2,:q370_3,:q370_4)', (forest, 'Pitch A', 'Camping Pitch', 'Per night', 0)).lastrowid)
            adult_id = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name) VALUES (:q371_0,:q371_1,:q371_2)', (forest, 'Adult', 'A')).lastrowid)
            breakfast_id = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (:q372_0,:q372_1,:q372_2)', (forest, 'Breakfast', 'Per quantity per day')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_years(company_id,year) VALUES (:q364_0,:q364_1)', (forest, 2026))
            season_id = int(insert_sql(c, 'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q373_0,:q373_1,:q373_2,:q373_3,:q373_4)', (forest, 2026, 'All Year 2026', '2026-01-01', '2026-12-31')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q365_0,:q365_1,:q365_2,:q365_3,:q365_4)', (forest, 2026, element_id, season_id, 25.0))
            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q366_0,:q366_1,:q366_2,:q366_3)', (forest, 2026, element_id, 6))
            execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (:q367_0,:q367_1,:q367_2,:q367_3,:q367_4)', (forest, 2026, element_id, adult_id, 6))
            execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q368_0,:q368_1,:q368_2,:q368_3,:q368_4)', (forest, 2026, element_id, adult_id, 5.0))
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q369_0,:q369_1,:q369_2,:q369_3,:q369_4,:q369_5,:q369_6,:q369_7)', (forest, 2026, 'Camping Pitch', breakfast_id, 1, 1, 4, 10.0))
            now = '2026-08-22T10:00:00+00:00'
            customer_id = int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (:q374_0,:q374_1,:q374_2,:q374_3,:q374_4,:q374_5,:q374_6)', (forest, 'Alice', 'Walker', 'alice@example.test', '', now, now)).lastrowid)

        from online.webv1_addon_when import initialise_addon_when
        initialise_addon_when(db)

        login(client, 'operator@forestview.test', 'Operator013!')
        csrf = csrf_for(client, db)

        addons_page = client.get('/setup/addons')
        assert addons_page.status_code == 200
        assert 'Feature / Extra Timings' in addons_page.text
        assert '/setup/addons/when' in addons_page.text

        r = client.post('/setup/addons/when', data={'csrf': csrf, 'addon_id': str(breakfast_id), 'every_active': '1', 'every_label': 'Every day', 'selected_active': '1', 'selected_label': 'Selected days'}, follow_redirects=False)
        assert r.status_code == 303

        page = client.get(f'/operations/customers/{customer_id}/enquiries/new')
        assert page.status_code == 200
        assert 'When?' in page.text
        assert page.text.index('Every day') < page.text.index('Selected days')
        assert 'addon-day-check' not in page.text
        assert 'Leave a date at 0 if none are required.' in page.text
        assert 'addon-picker' in page.text
        assert '✕ N/A:' in page.text
        assert "row.style.display='none'" not in page.text

        payload = {
            'csrf': csrf,
            'arrival_date': '2026-09-10',
            'departure_date': '2026-09-14',
            'party_size': '',
            'source': 'Phone',
            'notes': '',
            'element_type': 'Camping Pitch',
            'element_id': str(element_id),
            f'person_{adult_id}': '4',
            f'addon_{breakfast_id}': '0',
            f'addon_when_{breakfast_id}': 'selected_days',
            f'addon_day_{breakfast_id}_2026-09-10': '2',
            f'addon_day_{breakfast_id}_2026-09-11': '0',
            f'addon_day_{breakfast_id}_2026-09-12': '4',
            f'addon_day_{breakfast_id}_2026-09-13': '0',
        }
        calc = client.post(f'/operations/customers/{customer_id}/enquiries/new', data=payload | {'action': 'calculate'})
        assert calc.status_code == 200
        assert 'Calculated provisional total: €240.00' in calc.text
        assert '2026-09-10: 2' in calc.text
        assert '2026-09-12: 4' in calc.text
        assert '2026-09-11:' not in calc.text
        assert '2026-09-13:' not in calc.text

        saved = client.post(f'/operations/customers/{customer_id}/enquiries/new', data=payload | {'action': 'save'}, follow_redirects=False)
        assert saved.status_code == 303
        enquiry_id = int(saved.headers['location'].split('/')[3].split('?')[0])
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT quantity FROM enquiry_addons WHERE enquiry_id=:q377_0 AND addon_id=:q377_1', (enquiry_id, breakfast_id)).fetchone()['quantity']) == 6
            daily = execute_sql(c, 'SELECT service_date,quantity FROM enquiry_addon_days WHERE enquiry_id=:q375_0 AND addon_id=:q375_1 ORDER BY service_date', (enquiry_id, breakfast_id)).fetchall()
            assert [(str(x['service_date']), int(x['quantity'])) for x in daily] == [('2026-09-10', 2), ('2026-09-12', 4)]

        edit = client.get(f'/operations/enquiries/{enquiry_id}/edit')
        assert edit.status_code == 200
        assert 'Selected days' in edit.text
        assert 'addon-day-check' not in edit.text

        client.post('/logout', follow_redirects=False)
        login(client, 'customer@forestview.test', 'Customer013!')
        preview = client.get(f'/customer/direct-booking-preview?arrival=2026-09-10&departure=2026-09-14&element={element_id}')
        assert preview.status_code == 200
        assert 'Direct booking preview' in preview.text
        assert 'Breakfast' in preview.text
        assert 'When?' in preview.text
        assert preview.text.index('Every day') < preview.text.index('Selected days')
        assert 'preview-day-check' not in preview.text
        assert "box.innerHTML=''" in preview.text

    print('Direct Booking Web V1 Feature / Extra Timing UX test: passed')


if __name__ == '__main__':
    main()
