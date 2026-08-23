from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME, create_app
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
            company = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            c.execute("INSERT INTO setup_element_types(company_id,name) VALUES (?,?)", (company, 'Pitch'))
            element = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (?,?,?,?,?)", (company, 'Pitch P', 'Pitch', 'Per night', 0)).lastrowid)
            adult = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,?,?)", (company, 'Adult', 'Adult')).lastrowid)
            child = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,?,?)", (company, 'Child under 16', 'ChildU16')).lastrowid)
            breakfast = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (?,?,?)", (company, 'Breakfast', 'Per quantity per day')).lastrowid)
            dogs = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (?,?,?)", (company, 'Dogs', 'Per quantity')).lastrowid)
            c.execute("INSERT INTO setup_years(company_id,year) VALUES (?,2026)", (company,))
            season = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (company, 2026, 'All Year', '2026-01-01', '2026-12-31')).lastrowid)
            c.execute("INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)", (company, 2026, element, season, 25))
            c.execute("INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)", (company, 2026, element, 4))
            for pid, rate in ((adult, 5), (child, 3)):
                c.execute("INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)", (company, 2026, element, pid, 4))
                c.execute("INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)", (company, 2026, element, pid, rate))
            c.execute("INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)", (company, 2026, 'Pitch', breakfast, 1, 1, 4, 99))
            c.execute("INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)", (company, 2026, 'Pitch', dogs, 0, 0, 0, 0))
            now = '2026-08-23T12:00:00+00:00'
            customer = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (company, 'Picker', 'Test', 'picker@test.invalid', '', now, now)).lastrowid)
        from online.webv1_addon_when import initialise_addon_when
        from online.webv1_addon_person import initialise_addon_person
        initialise_addon_when(db); initialise_addon_person(db)
        with db.connect() as c:
            c.execute("UPDATE setup_addon_when_options SET active=1 WHERE company_id=? AND addon_id=?", (company, breakfast))
            c.execute("UPDATE setup_addon_person_pricing SET pricing_mode='person_type' WHERE company_id=? AND addon_id=?", (company, breakfast))
            c.execute("INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (?,?,?,?,?)", (company, breakfast, 2026, adult, 10))
            c.execute("INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (?,?,?,?,?)", (company, breakfast, 2026, child, 6))

        login(client, 'operator@forestview.test', 'Operator013!')
        csrf = csrf_for(client, db)
        page = client.get(f'/operations/customers/{customer}/enquiries/new')
        assert page.status_code == 200
        assert 'Available Add-ons' in page.text
        assert '✕ N/A = Not available for selected Element.' in page.text
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
        assert 'Calculated provisional total: €160.00' in good.text

        forged = client.post(f'/operations/customers/{customer}/enquiries/new', data=common | {f'addon_person_{breakfast}_{child}': '1'})
        assert forged.status_code in (200, 400)
        assert 'exceed the people on the Enquiry' in forged.text

        saved = client.post(f'/operations/customers/{customer}/enquiries/new', data=common | {f'addon_person_{breakfast}_{adult}': '0', 'action': 'save'}, follow_redirects=False)
        assert saved.status_code == 303
        enquiry_id = int(saved.headers['location'].split('/')[3].split('?')[0])
        with db.connect() as c:
            selected = c.execute('SELECT addon_id FROM enquiry_selected_addons WHERE enquiry_id=? AND addon_id=?', (enquiry_id, breakfast)).fetchone()
            assert selected is not None

        client.post('/logout', follow_redirects=False)
        login(client, 'customer@forestview.test', 'Customer013!')
        preview = client.get(f'/customer/direct-booking-preview?arrival=2026-09-10&departure=2026-09-12&element={element}&person_{adult}=2&person_{child}=0')
        assert preview.status_code == 200
        assert 'Available Add-ons' in preview.text
        assert 'Dogs' in preview.text
        assert '✕ — N/A:' in preview.text
        assert 'ChildU16' not in preview.text.split('preview-detail')[1] or 'activePeople' in preview.text

    print('Direct Booking Web V1 Add-on picker test: passed')


if __name__ == '__main__':
    main()
