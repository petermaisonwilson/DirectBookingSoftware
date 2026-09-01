from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME, create_app
from online.database import iso_now
from online.webv1 import register_web_v1


def login(client: TestClient) -> None:
    assert client.post('/login', data={'email': 'operator@forestview.test', 'password': 'Operator013!'}, follow_redirects=False).status_code == 303


def csrf_for(client: TestClient, db) -> str:
    return str(db.session_context(client.cookies.get(COOKIE_NAME))['csrf_token'])


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'maintenance.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        login(client)
        csrf = csrf_for(client, db)
        with db.connect() as c:
            company = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])

        # Person Type maintenance.
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'person', 'id': '', 'name': 'Senior Guest', 'short_name': 'SeniorG'}, follow_redirects=False).status_code == 303
        with db.connect() as c: person_id = int(c.execute("SELECT id FROM setup_person_types WHERE company_id=? AND name='Senior Guest'", (company,)).fetchone()['id'])
        page = client.get('/setup/person-types'); assert page.status_code == 200 and all(x in page.text for x in ('Edit', 'Deactivate', 'Delete'))
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'person', 'id': str(person_id), 'name': 'Senior 65+', 'short_name': '65PLUS'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/catalog/toggle', data={'csrf': csrf, 'kind': 'person', 'id': str(person_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            row = c.execute('SELECT * FROM setup_person_types WHERE id=?', (person_id,)).fetchone(); assert row['name'] == 'Senior 65+' and row['short_name'] == '65PLUS' and int(row['active']) == 0
        assert client.post('/setup/maintenance/catalog/toggle', data={'csrf': csrf, 'kind': 'person', 'id': str(person_id)}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/catalog/delete', data={'csrf': csrf, 'kind': 'person', 'id': str(person_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert c.execute('SELECT id FROM setup_person_types WHERE id=?', (person_id,)).fetchone() is None

        # Add-on maintenance, including edit and physical delete while unused.
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': '', 'name': 'Late Checkout', 'pricing_method': 'Fixed once'}, follow_redirects=False).status_code == 303
        with db.connect() as c: addon_id = int(c.execute("SELECT id FROM setup_addons WHERE company_id=? AND name='Late Checkout'", (company,)).fetchone()['id'])
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': str(addon_id), 'name': 'Late Departure', 'pricing_method': 'Per quantity'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/catalog/delete', data={'csrf': csrf, 'kind': 'addon', 'id': str(addon_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert c.execute('SELECT id FROM setup_addons WHERE id=?', (addon_id,)).fetchone() is None

        # Unused Element and Element Type can be deleted.
        assert client.post('/setup/element-types', data={'csrf': csrf, 'name': 'Maintenance Type', 'id': ''}, follow_redirects=False).status_code == 303
        with db.connect() as c: type_id = int(c.execute("SELECT id FROM setup_element_types WHERE company_id=? AND name='Maintenance Type'", (company,)).fetchone()['id'])
        assert client.post('/setup/elements', data={'csrf': csrf, 'id': '', 'name': 'Maintenance Element', 'element_type': 'Maintenance Type', 'pricing_method': 'Per night', 'base_price': '20.00'}, follow_redirects=False).status_code == 303
        with db.connect() as c: element_id = int(c.execute("SELECT id FROM setup_elements WHERE company_id=? AND name='Maintenance Element'", (company,)).fetchone()['id'])
        assert client.post('/setup/maintenance/catalog/delete', data={'csrf': csrf, 'kind': 'element', 'id': str(element_id)}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/element-types/delete', data={'csrf': csrf, 'id': str(type_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert c.execute('SELECT id FROM setup_elements WHERE id=?', (element_id,)).fetchone() is None
            assert c.execute('SELECT id FROM setup_element_types WHERE id=?', (type_id,)).fetchone() is None

        # Historical Element is protected from deletion but may be deactivated.
        assert client.post('/setup/element-types', data={'csrf': csrf, 'name': 'History Type', 'id': ''}, follow_redirects=False).status_code == 303
        assert client.post('/setup/elements', data={'csrf': csrf, 'id': '', 'name': 'History Element', 'element_type': 'History Type', 'pricing_method': 'Per night', 'base_price': '30.00'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            used_element = int(c.execute("SELECT id FROM setup_elements WHERE company_id=? AND name='History Element'", (company,)).fetchone()['id'])
            now = iso_now(); customer = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (company, 'History', 'Guest', 'history@example.test', '', now, now)).lastrowid)
            enquiry = int(c.execute("INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (company, customer, 'new', 'Phone', '2026-10-01', '2026-10-03', 2, '', now, now)).lastrowid)
            c.execute("INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at) VALUES (?,?,?,?,?,?,?)", (enquiry, company, 'History Type', used_element, 0, '{}', now))
        blocked = client.post('/setup/maintenance/catalog/delete', data={'csrf': csrf, 'kind': 'element', 'id': str(used_element)}, follow_redirects=False)
        assert blocked.status_code == 303 and 'message=' in blocked.headers['location']
        assert client.post('/setup/maintenance/catalog/toggle', data={'csrf': csrf, 'kind': 'element', 'id': str(used_element)}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert int(c.execute('SELECT active FROM setup_elements WHERE id=?', (used_element,)).fetchone()['active']) == 0

        # Year deletion and protection.
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2031'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/years/delete', data={'csrf': csrf, 'year': '2031'}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert c.execute('SELECT year FROM setup_years WHERE company_id=? AND year=2031', (company,)).fetchone() is None
        used_year = client.post('/setup/maintenance/years/delete', data={'csrf': csrf, 'year': '2026'}, follow_redirects=False)
        assert used_year.status_code == 303 and 'message=' in used_year.headers['location']

        # Season edit/delete in an unused year.
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2032'}, follow_redirects=False).status_code == 303
        with db.connect() as c: season_id = int(c.execute('SELECT id FROM setup_seasons WHERE company_id=? AND year=2032', (company,)).fetchone()['id'])
        assert client.get(f'/setup/maintenance/seasons/edit?id={season_id}').status_code == 200
        assert client.post('/setup/maintenance/seasons/save', data={'csrf': csrf, 'id': str(season_id), 'name': 'Summer 2032', 'start_date': '2032-01-01', 'end_date': '2032-12-31'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/seasons/delete', data={'csrf': csrf, 'id': str(season_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert c.execute('SELECT id FROM setup_seasons WHERE id=?', (season_id,)).fetchone() is None
        assert client.post('/setup/maintenance/years/delete', data={'csrf': csrf, 'year': '2032'}, follow_redirects=False).status_code == 303

        # Dedicated Add-on for rule reset; do not depend on demo catalogue data.
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': '', 'name': 'Rule Test Addon', 'pricing_method': 'Per quantity'}, follow_redirects=False).status_code == 303
        with db.connect() as c: rule_addon = int(c.execute("SELECT id FROM setup_addons WHERE company_id=? AND name='Rule Test Addon'", (company,)).fetchone()['id'])
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2033'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)", (company, 2033, 'History Type', rule_addon, 1, 1, 2, 5))
            c.execute("INSERT OR REPLACE INTO setup_element_addons(company_id,year,element_id,addon_id,state,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)", (company, 2033, used_element, rule_addon, 'Y', 1, 2, 6))
        assert client.post('/setup/maintenance/addon-rules/reset', data={'csrf': csrf, 'year': '2033'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert int(c.execute('SELECT COUNT(*) AS n FROM setup_type_addons WHERE company_id=? AND year=2033', (company,)).fetchone()['n']) == 0
            assert int(c.execute('SELECT COUNT(*) AS n FROM setup_element_addons WHERE company_id=? AND year=2033', (company,)).fetchone()['n']) == 0
            actions = {str(r['action']) for r in c.execute('SELECT action FROM audit_log WHERE company_id=?', (company,)).fetchall()}
            required = {'PERSON_TYPE_SAVED','PERSON_DELETED','ADDON_SAVED','ADDON_DELETED','ELEMENT_DELETED','ELEMENT_TYPE_DELETED','PRICING_YEAR_DELETED','SEASON_SAVED','SEASON_DELETED','ADDON_RULES_RESET'}
            assert required.issubset(actions), (required - actions)

    print('Direct Booking Web V1 Setup maintenance test: passed')


if __name__ == '__main__':
    main()
