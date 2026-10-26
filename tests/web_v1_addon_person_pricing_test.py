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
    return str(db.session_context(client.cookies.get(COOKIE_NAME))['csrf_token'])


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'addon-person.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)

        with db.connect() as c:
            forest = int(execute_sql(c, "SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            execute_sql(c, 'INSERT INTO setup_element_types(company_id,name) VALUES (:q1_0,:q1_1) ON CONFLICT DO NOTHING', (forest, 'Camping Pitch'))
            element_id = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (:q10_0,:q10_1,:q10_2,:q10_3,:q10_4)', (forest, 'Pitch P', 'Camping Pitch', 'Per night', 0)).lastrowid)
            adult_id = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name) VALUES (:q11_0,:q11_1,:q11_2)', (forest, 'Adult', 'A')).lastrowid)
            child_id = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name) VALUES (:q12_0,:q12_1,:q12_2)', (forest, 'Child', 'C')).lastrowid)
            breakfast_id = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (:q13_0,:q13_1,:q13_2)', (forest, 'Breakfast', 'Per quantity per day')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_years(company_id,year) VALUES (:q2_0,:q2_1)', (forest, 2026))
            season_id = int(insert_sql(c, 'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q14_0,:q14_1,:q14_2,:q14_3,:q14_4)', (forest, 2026, 'All Year 2026', '2026-01-01', '2026-12-31')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q3_0,:q3_1,:q3_2,:q3_3,:q3_4)', (forest, 2026, element_id, season_id, 25.0))
            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q4_0,:q4_1,:q4_2,:q4_3)', (forest, 2026, element_id, 6))
            execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (:q5_0,:q5_1,:q5_2,:q5_3,:q5_4)', (forest, 2026, element_id, adult_id, 4))
            execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (:q6_0,:q6_1,:q6_2,:q6_3,:q6_4)', (forest, 2026, element_id, child_id, 4))
            execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q7_0,:q7_1,:q7_2,:q7_3,:q7_4)', (forest, 2026, element_id, adult_id, 5.0))
            execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q8_0,:q8_1,:q8_2,:q8_3,:q8_4)', (forest, 2026, element_id, child_id, 3.0))
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q9_0,:q9_1,:q9_2,:q9_3,:q9_4,:q9_5,:q9_6,:q9_7)', (forest, 2026, 'Camping Pitch', breakfast_id, 1, 1, 6, 99.0))
            now = '2026-08-23T10:00:00+00:00'
            customer_id = int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (:q15_0,:q15_1,:q15_2,:q15_3,:q15_4,:q15_5,:q15_6)', (forest, 'Pat', 'Breakfast', 'pat@example.test', '', now, now)).lastrowid)

        from online.webv1_addon_person import initialise_addon_person
        from online.webv1_addon_when import initialise_addon_when
        initialise_addon_when(db)
        initialise_addon_person(db)

        login(client, 'operator@forestview.test', 'Operator013!')
        csrf = csrf_for(client, db)

        setup = client.post(
            '/setup/addons/when',
            data={
                'csrf': csrf,
                'addon_id': str(breakfast_id),
                'year': '2026',
                'every_active': '1',
                'every_label': 'Every day',
                'selected_active': '1',
                'selected_label': 'Selected days',
                'pricing_mode': 'person_type',
                f'person_rate_{adult_id}': '10.00',
                f'person_rate_{child_id}': '6.00',
            },
            follow_redirects=False,
        )
        assert setup.status_code == 303

        setup_page = client.get('/setup/addons/when?year=2026')
        assert setup_page.status_code == 200
        assert 'Feature / Extra Timings &amp; Person Pricing' in setup_page.text
        assert 'Price by Person Type' in setup_page.text
        assert '10.00' in setup_page.text and '6.00' in setup_page.text

        page = client.get(f'/operations/customers/{customer_id}/enquiries/new')
        assert page.status_code == 200
        assert 'Priced by Person Type' in page.text
        assert f'name="addon_person_{breakfast_id}_{adult_id}"' in page.text
        assert f'name="addon_person_{breakfast_id}_{child_id}"' in page.text
        assert 'Only Person Types on this Enquiry are shown.' in page.text

        common = {
            'csrf': csrf,
            'arrival_date': '2026-09-10',
            'departure_date': '2026-09-14',
            'party_size': '',
            'source': 'Phone',
            'notes': '',
            'element_type': 'Camping Pitch',
            'element_id': str(element_id),
            f'person_{adult_id}': '2',
            f'person_{child_id}': '1',
        }

        every = common | {
            f'addon_when_{breakfast_id}': 'every_day',
            f'addon_person_{breakfast_id}_{adult_id}': '2',
            f'addon_person_{breakfast_id}_{child_id}': '1',
            'action': 'calculate',
        }
        calc_every = client.post(f'/operations/customers/{customer_id}/enquiries/new', data=every)
        assert calc_every.status_code == 200
        assert 'Calculated provisional total: €256.00' in calc_every.text
        assert 'Adult 2 × €10.00' in calc_every.text
        assert 'Child 1 × €6.00' in calc_every.text

        too_many = every | {f'addon_person_{breakfast_id}_{adult_id}': '3'}
        bad = client.post(f'/operations/customers/{customer_id}/enquiries/new', data=too_many)
        assert bad.status_code == 200 or bad.status_code == 400
        assert 'exceed the people on the Enquiry' in bad.text

        selected = common | {
            f'addon_when_{breakfast_id}': 'selected_days',
            f'addon_day_person_{breakfast_id}_{adult_id}_2026-09-10': '2',
            f'addon_day_person_{breakfast_id}_{child_id}_2026-09-10': '1',
            f'addon_day_person_{breakfast_id}_{adult_id}_2026-09-12': '1',
            f'addon_day_person_{breakfast_id}_{child_id}_2026-09-12': '1',
            'action': 'calculate',
        }
        calc_selected = client.post(f'/operations/customers/{customer_id}/enquiries/new', data=selected)
        assert calc_selected.status_code == 200
        assert 'Calculated provisional total: €194.00' in calc_selected.text
        assert '2026-09-10: Adult 2, Child 1' in calc_selected.text
        assert '2026-09-12: Adult 1, Child 1' in calc_selected.text

        saved = client.post(f'/operations/customers/{customer_id}/enquiries/new', data=selected | {'action': 'save'}, follow_redirects=False)
        assert saved.status_code == 303
        enquiry_id = int(saved.headers['location'].split('/')[3].split('?')[0])
        with db.connect() as c:
            aggregate = execute_sql(c, 'SELECT quantity FROM enquiry_addons WHERE enquiry_id=:q16_0 AND addon_id=:q16_1', (enquiry_id, breakfast_id)).fetchone()
            assert int(aggregate['quantity']) == 5
            detail = execute_sql(c, 'SELECT service_date,person_type_id,quantity FROM enquiry_addon_person_days WHERE enquiry_id=:q17_0 AND addon_id=:q17_1 ORDER BY service_date,person_type_id', (enquiry_id, breakfast_id)).fetchall()
            assert len(detail) == 4

        edit = client.get(f'/operations/enquiries/{enquiry_id}/edit')
        assert edit.status_code == 200
        assert f'name="addon_day_person_{breakfast_id}_{adult_id}_'+"'" not in edit.text
        assert 'personDayValues' in edit.text
        assert '2026-09-10' in edit.text

        client.post('/logout', follow_redirects=False)
        login(client, 'customer@forestview.test', 'Customer013!')
        preview = client.get(f'/customer/direct-booking-preview?arrival=2026-09-10&departure=2026-09-14&element={element_id}&person_{adult_id}=2&person_{child_id}=1')
        assert preview.status_code == 200
        assert 'Breakfast' in preview.text
        assert '€10.00' in preview.text
        assert '€6.00' in preview.text
        assert 'activePeople()' in preview.text

    print('Direct Booking Web V1 Feature / Extra Person Type pricing test: passed')


if __name__ == '__main__':
    main()
