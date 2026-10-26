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


def login(client, email, password):
    assert client.post('/login', data={'email': email, 'password': password}, follow_redirects=False).status_code == 303


def csrf_for(client, db):
    return str(db.session_context(client.cookies.get(COOKIE_NAME))['csrf_token'])


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'picker.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        with db.connect() as c:
            company = int(execute_sql(c, "SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            execute_sql(c, 'INSERT INTO setup_element_types(company_id,name) VALUES (:q119_0,:q119_1)', (company, 'Pitch'))
            element = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (:q131_0,:q131_1,:q131_2,:q131_3,:q131_4)', (company, 'Pitch P', 'Pitch', 'Per night', 0)).lastrowid)
            adult = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name) VALUES (:q132_0,:q132_1,:q132_2)', (company, 'Adult', 'Adult')).lastrowid)
            child = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name) VALUES (:q133_0,:q133_1,:q133_2)', (company, 'Child under 16', 'ChildU16')).lastrowid)
            breakfast = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (:q134_0,:q134_1,:q134_2)', (company, 'Breakfast', 'Per quantity per day')).lastrowid)
            dogs = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (:q135_0,:q135_1,:q135_2)', (company, 'Dogs', 'Per quantity')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_years(company_id,year) VALUES (:q120_0,2026)', (company,))
            season = int(insert_sql(c, 'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q136_0,:q136_1,:q136_2,:q136_3,:q136_4)', (company, 2026, 'All Year', '2026-01-01', '2026-12-31')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q121_0,:q121_1,:q121_2,:q121_3,:q121_4)', (company, 2026, element, season, 25))
            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q122_0,:q122_1,:q122_2,:q122_3)', (company, 2026, element, 4))
            for pid, rate in ((adult, 5), (child, 3)):
                execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (:q129_0,:q129_1,:q129_2,:q129_3,:q129_4)', (company, 2026, element, pid, 4))
                execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q130_0,:q130_1,:q130_2,:q130_3,:q130_4)', (company, 2026, element, pid, rate))
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q123_0,:q123_1,:q123_2,:q123_3,:q123_4,:q123_5,:q123_6,:q123_7)', (company, 2026, 'Pitch', breakfast, 1, 1, 4, 99))
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q124_0,:q124_1,:q124_2,:q124_3,:q124_4,:q124_5,:q124_6,:q124_7)', (company, 2026, 'Pitch', dogs, 0, 0, 0, 0))
            now = '2026-08-23T12:00:00+00:00'
            customer = int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (:q137_0,:q137_1,:q137_2,:q137_3,:q137_4,:q137_5,:q137_6)', (company, 'Picker', 'Test', 'picker@test.invalid', '', now, now)).lastrowid)
        from online.webv1_addon_when import initialise_addon_when
        from online.webv1_addon_person import initialise_addon_person
        initialise_addon_when(db); initialise_addon_person(db)
        with db.connect() as c:
            execute_sql(c, 'UPDATE setup_addon_when_options SET active=1 WHERE company_id=:q125_0 AND addon_id=:q125_1', (company, breakfast))
            execute_sql(c, "UPDATE setup_addon_person_pricing SET pricing_mode='person_type' WHERE company_id=:q126_0 AND addon_id=:q126_1", (company, breakfast))
            execute_sql(c, 'INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (:q127_0,:q127_1,:q127_2,:q127_3,:q127_4)', (company, breakfast, 2026, adult, 10))
            execute_sql(c, 'INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (:q128_0,:q128_1,:q128_2,:q128_3,:q128_4)', (company, breakfast, 2026, child, 6))

        login(client, 'operator@forestview.test', 'Operator013!')
        csrf = csrf_for(client, db)
        page = client.get(f'/operations/customers/{customer}/enquiries/new')
        assert page.status_code == 200
        assert 'Available Add-ons' in page.text
        assert '✕ N/A = Not available for selected Element.' in page.text
        assert '✕ N/A:' in page.text
        assert 'ChildU16' in page.text
        assert 'addon-picker-check' in page.text
        assert 'activePeople()' in page.text

        common = {
            'csrf': csrf, 'arrival_date': '2026-09-10', 'departure_date': '2026-09-12',
            'party_size': '', 'source': 'Web', 'notes': '', 'element_type': 'Pitch', 'element_id': str(element),
            f'person_{adult}': '2', f'person_{child}': '0', f'addon_selected_{breakfast}': '1',
            f'addon_when_{breakfast}': 'every_day', f'addon_person_{breakfast}_{adult}': '2', 'action': 'calculate'
        }
        good = client.post(f'/operations/customers/{customer}/enquiries/new', data=common)
        assert good.status_code == 200
        assert 'Calculated provisional total: €110.00' in good.text

        forged = client.post(f'/operations/customers/{customer}/enquiries/new', data=common | {f'addon_person_{breakfast}_{child}': '1'})
        assert forged.status_code in (200, 400)
        assert 'exceed the people on the Enquiry' in forged.text

        saved = client.post(f'/operations/customers/{customer}/enquiries/new', data=common | {f'addon_person_{breakfast}_{adult}': '0', 'action': 'save'}, follow_redirects=False)
        assert saved.status_code == 303
        enquiry_id = int(saved.headers['location'].split('/')[3].split('?')[0])
        with db.connect() as c:
            selected = execute_sql(c, 'SELECT addon_id FROM enquiry_selected_addons WHERE enquiry_id=:q138_0 AND addon_id=:q138_1', (enquiry_id, breakfast)).fetchone()
            assert selected is not None

        client.post('/logout', follow_redirects=False)
        login(client, 'customer@forestview.test', 'Customer013!')
        preview = client.get(f'/customer/direct-booking-preview?arrival=2026-09-10&departure=2026-09-12&element={element}&person_{adult}=2&person_{child}=0')
        assert preview.status_code == 200
        assert 'Available Add-ons' in preview.text
        assert 'Dogs' in preview.text
        assert '✕ N/A:' in preview.text
        assert 'ChildU16' not in preview.text.split('preview-detail')[1] or 'activePeople' in preview.text

    print('Direct Booking Web V1 Add-on picker test: passed')


if __name__ == '__main__':
    main()
