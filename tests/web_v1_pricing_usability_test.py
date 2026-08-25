from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
from online.setup015_readiness import element_available_setup_ready
from online.webv1 import register_web_v1

POUND = '\u00a3'


def login(client: TestClient, email: str, password: str) -> None:
    r = client.post('/login', data={'email': email, 'password': password}, follow_redirects=False)
    assert r.status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        app = create_app(Path(td) / 'pricing-usability.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)

        # Supervisor chooses the Client base currency; Client can see but cannot edit it.
        login(client, 'supervisor@directbooking.test', 'Supervisor013!')
        ctx = db.session_context(client.cookies.get(COOKIE_NAME)); assert ctx
        with db.connect() as c:
            forest = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
        r = client.post(f'/support/start/{forest}', data={'csrf': ctx['csrf_token']}, follow_redirects=False)
        assert r.status_code == 303
        ctx = db.session_context(client.cookies.get(COOKIE_NAME)); assert ctx
        settings = client.get('/company/settings')
        assert settings.status_code == 200
        assert 'Base currency' in settings.text and 'Only the Supervisor can change it' in settings.text
        changed = client.post('/company/base-currency', data={'csrf': ctx['csrf_token'], 'base_currency': 'GBP'}, follow_redirects=False)
        assert changed.status_code == 303
        with db.connect() as c:
            assert c.execute('SELECT base_currency FROM companies WHERE id=?', (forest,)).fetchone()['base_currency'] == 'GBP'
        assert POUND in client.get('/company/settings').text

        client.post('/logout')
        login(client, 'operator@forestview.test', 'Operator013!')
        ctx = db.session_context(client.cookies.get(COOKIE_NAME)); assert ctx
        assert 'Base currency is set by the Supervisor' in client.get('/company/settings').text
        assert client.post('/company/base-currency', data={'csrf': ctx['csrf_token'], 'base_currency': 'USD'}).status_code == 403
        company = int(ctx['company_id'])

        # Build a self-contained Element/Season setup.
        with db.connect() as c:
            c.execute("INSERT INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (company, 'Test Type'))
            element_id = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,0,1)", (company, 'Test Cabin', 'Test Type', 'Per night')).lastrowid)
            person_id = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active) VALUES (?,?,?,1)", (company, 'Adult', 'Adult')).lastrowid)
            c.execute('INSERT INTO setup_years(company_id,year) VALUES (?,?)', (company, 2041))
            season_id = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (company, 2041, 'Autumn', '2041-05-01', '2041-09-30')).lastrowid)
            c.execute('INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)', (company, 2041, element_id, season_id, 25.0))
            c.execute('INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)', (company, 2041, element_id, 4))
            c.execute('INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)', (company, 2041, element_id, person_id, 4))
            c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (company, 2041, element_id, person_id, 0.0))

        # Existing Season extension inherits the stored Season price automatically.
        ready, _ = element_available_setup_ready(db, company, element_id, date(2041, 9, 20), date(2041, 9, 22))
        assert ready
        from online.webv1_status_availability import availability_state
        assert availability_state(db, company, element_id, '2041-10-05', '2041-10-06')['state'] == 'OUT_OF_SEASON'
        extend = client.post('/setup/maintenance/seasons/save', data={
            'csrf': ctx['csrf_token'], 'id': str(season_id), 'name': 'Autumn',
            'start_date': '2041-05-01', 'end_date': '2041-10-31',
        }, follow_redirects=False)
        assert extend.status_code == 303
        state = availability_state(db, company, element_id, '2041-10-05', '2041-10-06')
        assert state['available'] is True

        # A brand-new Season extends the operating window but is off sale until its price exists.
        with db.connect() as c:
            extra_id = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (company, 2041, 'Extra Month', '2041-11-01', '2041-11-30')).lastrowid)
        state = availability_state(db, company, element_id, '2041-11-05', '2041-11-06')
        assert state['available'] is False and state['state'] == 'SETUP_INCOMPLETE'
        guide = client.get(f'/setup/guidance?year=2041&focus_element_id={element_id}').json()
        assert guide['focus']['name'] == 'Test Cabin' and guide['focus']['count'] >= 1
        audit_page = client.get(f'/setup/audit/element?year=2041&element_id={element_id}')
        assert 'Setup Audit — Test Cabin' in audit_page.text and 'Extra Month price missing' in audit_page.text
        with db.connect() as c:
            c.execute('INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)', (company, 2041, element_id, extra_id, 25.0))
        state = availability_state(db, company, element_id, '2041-11-05', '2041-11-06')
        assert state['available'] is True
        guide = client.get(f'/setup/guidance?year=2041&focus_element_id={element_id}').json()
        assert guide['focus']['count'] == 0

        # Season Maintenance is now immediately below Add Season, not at the bottom.
        pricing = client.get('/setup/pricing?year=2041')
        assert pricing.status_code == 200
        pricing_post = '<form method="post" action="/setup/pricing">'
        assert pricing.text.index('<h2>Add season</h2>') < pricing.text.index('<h2>Season maintenance</h2>') < pricing.text.index(pricing_post)
        assert 'setup-guidance-script' in pricing.text
        assert POUND in pricing.text  # currency presentation follows the Client base currency

        # Enquiry pricing shows duration/basis, and min=max=1 Add-ons get the auto-one UI helper.
        with db.connect() as c:
            addon_id = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (company, 'Linen', 'Per night')).lastrowid)
            c.execute('INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,1,1,1,?)', (company, 2041, 'Test Type', addon_id, 5.0))
            customer_id = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,datetime('now'),datetime('now'))", (company, 'Test', 'Guest', 'test@example.test', '')).lastrowid)
        new_page = client.get(f'/operations/customers/{customer_id}/enquiries/new')
        assert new_page.status_code == 200 and 'addon-auto-one' in new_page.text
        calc = client.post(f'/operations/customers/{customer_id}/enquiries/new', data={
            'csrf': ctx['csrf_token'], 'arrival_date': '2041-09-10', 'departure_date': '2041-09-13',
            'party_size': '1', 'source': 'Test', 'notes': '', 'element_type': 'Test Type', 'element_id': str(element_id),
            f'person_{person_id}': '1', f'addon_selected_{addon_id}': '1', f'addon_{addon_id}': '1', f'addon_when_{addon_id}': 'every_day',
            'action': 'calculate',
        })
        assert calc.status_code == 200
        assert 'Duration: 3 night(s)' in calc.text
        assert POUND in calc.text

    print('Direct Booking pricing usability / Season extension regression: passed')


if __name__ == '__main__':
    main()
