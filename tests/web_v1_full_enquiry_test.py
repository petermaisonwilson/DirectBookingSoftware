from __future__ import annotations

import json
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
        app = create_app(Path(temp_dir) / 'integrated-enquiry.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)

        with db.connect() as c:
            forest = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            riverside = int(c.execute("SELECT id FROM companies WHERE name='Riverside Cabins'").fetchone()['id'])
            c.execute("INSERT INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (forest, 'Camping Pitch'))
            element_id = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (forest, 'Pitch A', 'Camping Pitch', 'Per night', 0)).lastrowid)
            adult_id = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active) VALUES (?,?,?,1)", (forest, 'Adult', 'A')).lastrowid)
            child_id = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active) VALUES (?,?,?,1)", (forest, 'Child', 'C')).lastrowid)
            addon_id = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (forest, 'Electric Hook-up', 'Fixed once')).lastrowid)
            c.execute("INSERT INTO setup_years(company_id,year) VALUES (?,2026)", (forest,))
            season_id = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (forest, 2026, 'All Year', '2026-01-01', '2026-12-31')).lastrowid)
            c.execute("INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)", (forest, 2026, element_id, season_id, 25.0))
            c.execute("INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)", (forest, 2026, element_id, 4))
            c.execute("INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)", (forest, 2026, element_id, adult_id, 4))
            c.execute("INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)", (forest, 2026, element_id, child_id, 3))
            c.execute("INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)", (forest, 2026, element_id, adult_id, 5.0))
            c.execute("INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)", (forest, 2026, element_id, child_id, 2.0))
            c.execute("INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)", (forest, 2026, 'Camping Pitch', addon_id, 1, 1, 1, 3.0))
            now = '2026-08-22T10:00:00+00:00'
            customer_id = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (forest, 'Alice', 'Walker', 'alice@example.test', '0612345678', now, now)).lastrowid)
            river_customer = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (riverside, 'River', 'Guest', 'river@example.test', '', now, now)).lastrowid)
            river_enquiry = int(c.execute("INSERT INTO enquiries(company_id,customer_id,status,source,created_at,updated_at) VALUES (?,?,?,?,?,?)", (riverside, river_customer, 'new', 'Web', now, now)).lastrowid)

        login(client, 'operator@forestview.test', 'Operator013!')
        page = client.get(f'/operations/customers/{customer_id}/enquiries/new')
        assert page.status_code == 200
        for text in ('New Enquiry', 'Element Type', 'Specific Element (optional)', 'Adult', 'Child', 'Electric Hook-up', 'Calculate provisional price', 'Save Enquiry'):
            assert text in page.text
        assert 'Load Setup rules' not in page.text

        csrf = csrf_for(client, db)
        payload = {
            'csrf': csrf,
            'arrival_date': '2026-09-10',
            'departure_date': '2026-09-12',
            'party_size': '',
            'source': 'Phone',
            'notes': 'Needs electric pitch',
            'element_type': 'Camping Pitch',
            'element_id': str(element_id),
            f'person_{adult_id}': '2',
            f'person_{child_id}': '1',
            f'addon_{addon_id}': '1',
        }
        calculated = client.post(f'/operations/customers/{customer_id}/enquiries/new', data=payload | {'action': 'calculate'})
        assert calculated.status_code == 200
        assert 'Calculated provisional total: €77.00' in calculated.text
        with db.connect() as c:
            assert c.execute('SELECT COUNT(*) AS n FROM enquiries WHERE company_id=?', (forest,)).fetchone()['n'] == 0

        saved = client.post(f'/operations/customers/{customer_id}/enquiries/new', data=payload | {'action': 'save'}, follow_redirects=False)
        assert saved.status_code == 303
        assert saved.headers['location'].startswith('/operations/enquiries/')
        enquiry_id = int(saved.headers['location'].split('/')[3].split('?')[0])
        with db.connect() as c:
            enquiry = c.execute('SELECT * FROM enquiries WHERE id=? AND company_id=?', (enquiry_id, forest)).fetchone()
            assert enquiry['arrival_date'] == '2026-09-10' and enquiry['departure_date'] == '2026-09-12'
            assert int(enquiry['party_size']) == 3
            request_row = c.execute('SELECT * FROM enquiry_requests WHERE enquiry_id=? AND company_id=?', (enquiry_id, forest)).fetchone()
            assert request_row['element_type'] == 'Camping Pitch'
            assert int(request_row['element_id']) == element_id
            assert float(request_row['provisional_total']) == 77.0
            snapshot = json.loads(request_row['pricing_snapshot_json'])
            assert snapshot['total'] == 77.0 and snapshot['people_total'] == 3
            addon = c.execute('SELECT quantity FROM enquiry_addons WHERE enquiry_id=? AND addon_id=?', (enquiry_id, addon_id)).fetchone()
            assert int(addon['quantity']) == 1

        detail = client.get(f'/operations/enquiries/{enquiry_id}')
        assert detail.status_code == 200
        assert 'Pitch A' in detail.text and '€77.00' in detail.text and 'Electric Hook-up' in detail.text
        assert f'/operations/enquiries/{enquiry_id}/edit' in detail.text

        edit = client.get(f'/operations/enquiries/{enquiry_id}/edit')
        assert edit.status_code == 200
        assert 'Edit Enquiry' in edit.text and 'Pitch A' in edit.text and 'Needs electric pitch' in edit.text

        # The old staged URL now safely redirects to the integrated editor. A blank element never causes FastAPI integer parsing errors.
        old = client.get(f'/operations/enquiries/{enquiry_id}/build?element_type=Camping%20Pitch&element=', follow_redirects=False)
        assert old.status_code == 303
        assert old.headers['location'] == f'/operations/enquiries/{enquiry_id}/edit'

        # Type-only enquiries can still be saved without a specific Element or price.
        type_only = client.post(f'/operations/customers/{customer_id}/enquiries/new', data={
            'csrf': csrf, 'arrival_date': '', 'departure_date': '', 'party_size': '2', 'source': 'Email', 'notes': 'Dates not known',
            'element_type': 'Camping Pitch', 'element_id': '', 'action': 'save'
        }, follow_redirects=False)
        assert type_only.status_code == 303
        type_only_id = int(type_only.headers['location'].split('/')[3].split('?')[0])
        with db.connect() as c:
            req = c.execute('SELECT * FROM enquiry_requests WHERE enquiry_id=? AND company_id=?', (type_only_id, forest)).fetchone()
            assert req is not None and req['element_type'] == 'Camping Pitch' and req['element_id'] is None and req['provisional_total'] is None

        register = client.get('/operations/enquiries')
        assert register.status_code == 200 and 'Pitch A' in register.text and '€77.00' in register.text and 'River Guest' not in register.text
        assert client.get(f'/operations/enquiries/{river_enquiry}/edit').status_code == 404
        client.post('/logout', follow_redirects=False)

        login(client, 'customer@forestview.test', 'Customer013!')
        assert client.get(f'/operations/customers/{customer_id}/enquiries/new').status_code == 403
        client.post('/logout', follow_redirects=False)

        login(client, 'supervisor@directbooking.test', 'Supervisor013!')
        assert client.get(f'/operations/customers/{customer_id}/enquiries/new').status_code == 403
        support_csrf = csrf_for(client, db)
        enter = client.post(f'/support/start/{forest}', data={'csrf': support_csrf}, follow_redirects=False)
        assert enter.status_code == 303
        support_page = client.get(f'/operations/customers/{customer_id}/enquiries/new')
        assert support_page.status_code == 200 and 'SUPPORT MODE' in support_page.text and 'Camping Pitch' in support_page.text

    print('Direct Booking Web V1 integrated New/Edit Enquiry test: passed')


if __name__ == '__main__':
    main()
