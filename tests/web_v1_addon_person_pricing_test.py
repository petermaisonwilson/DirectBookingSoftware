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
    return str(db.session_context(client.cookies.get(COOKIE_NAME))['csrf_token'])


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'addon-person.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)

        with db.connect() as c:
            forest = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name) VALUES (?,?)", (forest, 'Camping Pitch'))
            element_id = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (?,?,?,?,?)", (forest, 'Pitch P', 'Camping Pitch', 'Per night', 0)).lastrowid)
            adult_id = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,?,?)", (forest, 'Adult', 'A')).lastrowid)
            child_id = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,?,?)", (forest, 'Child', 'C')).lastrowid)
            breakfast_id = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (?,?,?)", (forest, 'Breakfast', 'Per quantity per day')).lastrowid)
            c.execute("INSERT INTO setup_years(company_id,year) VALUES (?,?)", (forest, 2026))
            season_id = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (forest, 2026, 'All Year 2026', '2026-01-01', '2026-12-31')).lastrowid)
            c.execute("INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)", (forest, 2026, element_id, season_id, 25.0))
            c.execute("INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)", (forest, 2026, element_id, 6))
            c.execute("INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)", (forest, 2026, element_id, adult_id, 4))
            c.execute("INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)", (forest, 2026, element_id, child_id, 4))
            c.execute("INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)", (forest, 2026, element_id, adult_id, 5.0))
            c.execute("INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)", (forest, 2026, element_id, child_id, 3.0))
            c.execute("INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)", (forest, 2026, 'Camping Pitch', breakfast_id, 1, 1, 6, 99.0))
            now = '2026-08-23T10:00:00+00:00'
            customer_id = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (forest, 'Pat', 'Breakfast', 'pat@example.test', '', now, now)).lastrowid)

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
            aggregate = c.execute('SELECT quantity FROM enquiry_addons WHERE enquiry_id=? AND addon_id=?', (enquiry_id, breakfast_id)).fetchone()
            assert int(aggregate['quantity']) == 5
            detail = c.execute('SELECT service_date,person_type_id,quantity FROM enquiry_addon_person_days WHERE enquiry_id=? AND addon_id=? ORDER BY service_date,person_type_id', (enquiry_id, breakfast_id)).fetchall()
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
