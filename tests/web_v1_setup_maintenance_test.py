from __future__ import annotations
from operational_backend import create_app
from online.sql import execute_sql, insert_sql

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME
from online.database import iso_now
from online.webv1 import register_web_v1
from online.webv1_features_extras_v2 import initialise_features_extras


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
            company = int(execute_sql(c, "SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])

        # Features and Extras both independently support Ask before Availability.
        extra_save = client.post('/setup/addons', data={
            'csrf': csrf,
            'id': '',
            'name': 'Test Electric Hook-up',
            'item_kind': 'Extra',
            'pricing_method': 'Fixed once',
            'ask_before_availability': 'on',
        }, follow_redirects=False)
        assert extra_save.status_code == 303
        with db.connect() as c:
            extra = execute_sql(c, "SELECT id,item_kind,ask_before_availability FROM setup_addons WHERE company_id=:q163_0 AND name='Test Electric Hook-up'", (company,)).fetchone()
            assert extra is not None and str(extra['item_kind']) == 'Extra' and int(extra['ask_before_availability']) == 1
            extra_id = int(extra['id'])
        edit_page = client.get(f'/setup/addons?edit={extra_id}')
        assert edit_page.status_code == 200
        assert 'name="ask_before_availability" checked' in edit_page.text
        initialise_features_extras(db)
        with db.connect() as c:
            extra = execute_sql(c, 'SELECT item_kind,ask_before_availability FROM setup_addons WHERE id=:q164_0', (extra_id,)).fetchone()
            assert extra is not None and str(extra['item_kind']) == 'Extra' and int(extra['ask_before_availability']) == 1
        extra_to_feature = client.post('/setup/addons', data={
            'csrf': csrf,
            'id': str(extra_id),
            'name': 'Test Electric Hook-up',
            'item_kind': 'Feature',
            'feature_group': '',
            'pricing_method': 'Fixed once',
            'ask_before_availability': 'on',
        }, follow_redirects=False)
        assert extra_to_feature.status_code == 303
        feature_to_extra = client.post('/setup/addons', data={
            'csrf': csrf,
            'id': str(extra_id),
            'name': 'Test Electric Hook-up',
            'item_kind': 'Extra',
            'pricing_method': 'Fixed once',
            'ask_before_availability': 'on',
        }, follow_redirects=False)
        assert feature_to_extra.status_code == 303
        with db.connect() as c:
            extra = execute_sql(c, 'SELECT item_kind,ask_before_availability FROM setup_addons WHERE id=:q165_0', (extra_id,)).fetchone()
            assert extra is not None and str(extra['item_kind']) == 'Extra' and int(extra['ask_before_availability']) == 1

        # Person Type maintenance.
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'person', 'id': '', 'name': 'Senior Guest', 'short_name': 'SeniorG'}, follow_redirects=False).status_code == 303
        with db.connect() as c: person_id = int(execute_sql(c, "SELECT id FROM setup_person_types WHERE company_id=:q176_0 AND name='Senior Guest'", (company,)).fetchone()['id'])
        page = client.get('/setup/person-types'); assert page.status_code == 200 and all(x in page.text for x in ('Edit', 'Deactivate', 'Delete'))
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'person', 'id': str(person_id), 'name': 'Senior 65+', 'short_name': '65PLUS'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/catalog/toggle', data={'csrf': csrf, 'kind': 'person', 'id': str(person_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            row = execute_sql(c, 'SELECT * FROM setup_person_types WHERE id=:q166_0', (person_id,)).fetchone(); assert row['name'] == 'Senior 65+' and row['short_name'] == '65PLUS' and int(row['active']) == 0
        assert client.post('/setup/maintenance/catalog/toggle', data={'csrf': csrf, 'kind': 'person', 'id': str(person_id)}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/catalog/delete', data={'csrf': csrf, 'kind': 'person', 'id': str(person_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert execute_sql(c, 'SELECT id FROM setup_person_types WHERE id=:q169_0', (person_id,)).fetchone() is None

        # Add-on maintenance, including edit and physical delete while unused.
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': '', 'name': 'Late Checkout', 'pricing_method': 'Fixed once'}, follow_redirects=False).status_code == 303
        with db.connect() as c: addon_id = int(execute_sql(c, "SELECT id FROM setup_addons WHERE company_id=:q177_0 AND name='Late Checkout'", (company,)).fetchone()['id'])
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': str(addon_id), 'name': 'Late Departure', 'pricing_method': 'Per quantity'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/catalog/delete', data={'csrf': csrf, 'kind': 'addon', 'id': str(addon_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert execute_sql(c, 'SELECT id FROM setup_addons WHERE id=:q170_0', (addon_id,)).fetchone() is None

        # Unused Element and Element Type can be deleted.
        assert client.post('/setup/element-types', data={'csrf': csrf, 'name': 'Maintenance Type', 'id': ''}, follow_redirects=False).status_code == 303
        with db.connect() as c: type_id = int(execute_sql(c, "SELECT id FROM setup_element_types WHERE company_id=:q178_0 AND name='Maintenance Type'", (company,)).fetchone()['id'])
        assert client.post('/setup/elements', data={'csrf': csrf, 'id': '', 'name': 'Maintenance Element', 'element_type': 'Maintenance Type', 'pricing_method': 'Per night', 'base_price': '20.00'}, follow_redirects=False).status_code == 303
        with db.connect() as c: element_id = int(execute_sql(c, "SELECT id FROM setup_elements WHERE company_id=:q179_0 AND name='Maintenance Element'", (company,)).fetchone()['id'])
        assert client.post('/setup/maintenance/catalog/delete', data={'csrf': csrf, 'kind': 'element', 'id': str(element_id)}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/element-types/delete', data={'csrf': csrf, 'id': str(type_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert execute_sql(c, 'SELECT id FROM setup_elements WHERE id=:q171_0', (element_id,)).fetchone() is None
            assert execute_sql(c, 'SELECT id FROM setup_element_types WHERE id=:q172_0', (type_id,)).fetchone() is None

        # Historical Element is protected from deletion but may be deactivated.
        assert client.post('/setup/element-types', data={'csrf': csrf, 'name': 'History Type', 'id': ''}, follow_redirects=False).status_code == 303
        assert client.post('/setup/elements', data={'csrf': csrf, 'id': '', 'name': 'History Element', 'element_type': 'History Type', 'pricing_method': 'Per night', 'base_price': '30.00'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            used_element = int(execute_sql(c, "SELECT id FROM setup_elements WHERE company_id=:q180_0 AND name='History Element'", (company,)).fetchone()['id'])
            now = iso_now(); customer = int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (:q167_0,:q167_1,:q167_2,:q167_3,:q167_4,:q167_5,:q167_6)', (company, 'History', 'Guest', 'history@example.test', '', now, now)).lastrowid)
            enquiry = int(insert_sql(c, 'INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at) VALUES (:q168_0,:q168_1,:q168_2,:q168_3,:q168_4,:q168_5,:q168_6,:q168_7,:q168_8,:q168_9)', (company, customer, 'new', 'Phone', '2026-10-01', '2026-10-03', 2, '', now, now)).lastrowid)
            execute_sql(c, 'INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at) VALUES (:q160_0,:q160_1,:q160_2,:q160_3,:q160_4,:q160_5,:q160_6)', (enquiry, company, 'History Type', used_element, 0, '{}', now))
        blocked = client.post('/setup/maintenance/catalog/delete', data={'csrf': csrf, 'kind': 'element', 'id': str(used_element)}, follow_redirects=False)
        assert blocked.status_code == 303 and 'message=' in blocked.headers['location']
        assert client.post('/setup/maintenance/catalog/toggle', data={'csrf': csrf, 'kind': 'element', 'id': str(used_element)}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert int(execute_sql(c, 'SELECT active FROM setup_elements WHERE id=:q184_0', (used_element,)).fetchone()['active']) == 0

        # Year deletion and protection.
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2031'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/years/delete', data={'csrf': csrf, 'year': '2031'}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert execute_sql(c, 'SELECT year FROM setup_years WHERE company_id=:q173_0 AND year=2031', (company,)).fetchone() is None
        used_year = client.post('/setup/maintenance/years/delete', data={'csrf': csrf, 'year': '2026'}, follow_redirects=False)
        assert used_year.status_code == 303 and 'message=' in used_year.headers['location']

        # Season edit/delete in an unused year.
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2032'}, follow_redirects=False).status_code == 303
        with db.connect() as c: season_id = int(execute_sql(c, 'SELECT id FROM setup_seasons WHERE company_id=:q181_0 AND year=2032', (company,)).fetchone()['id'])
        assert client.get(f'/setup/maintenance/seasons/edit?id={season_id}').status_code == 200
        assert client.post('/setup/maintenance/seasons/save', data={'csrf': csrf, 'id': str(season_id), 'name': 'Summer 2032', 'start_date': '2032-01-01', 'end_date': '2032-12-31'}, follow_redirects=False).status_code == 303
        assert client.post('/setup/maintenance/seasons/delete', data={'csrf': csrf, 'id': str(season_id)}, follow_redirects=False).status_code == 303
        with db.connect() as c: assert execute_sql(c, 'SELECT id FROM setup_seasons WHERE id=:q174_0', (season_id,)).fetchone() is None
        assert client.post('/setup/maintenance/years/delete', data={'csrf': csrf, 'year': '2032'}, follow_redirects=False).status_code == 303

        # Dedicated Add-on for rule reset; do not depend on demo catalogue data.
        assert client.post('/setup/maintenance/catalog/save', data={'csrf': csrf, 'kind': 'addon', 'id': '', 'name': 'Rule Test Addon', 'pricing_method': 'Per quantity'}, follow_redirects=False).status_code == 303
        with db.connect() as c: rule_addon = int(execute_sql(c, "SELECT id FROM setup_addons WHERE company_id=:q182_0 AND name='Rule Test Addon'", (company,)).fetchone()['id'])
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2033'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q161_0,:q161_1,:q161_2,:q161_3,:q161_4,:q161_5,:q161_6,:q161_7) ON CONFLICT(company_id,year,element_type,addon_id) DO UPDATE SET allowed=excluded.allowed,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate', (company, 2033, 'History Type', rule_addon, 1, 1, 2, 5))
            execute_sql(c, 'INSERT INTO setup_element_addons(company_id,year,element_id,addon_id,state,min_qty,max_qty,rate) VALUES (:q162_0,:q162_1,:q162_2,:q162_3,:q162_4,:q162_5,:q162_6,:q162_7) ON CONFLICT(company_id,year,element_id,addon_id) DO UPDATE SET state=excluded.state,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate', (company, 2033, used_element, rule_addon, 'Y', 1, 2, 6))
        assert client.post('/setup/maintenance/addon-rules/reset', data={'csrf': csrf, 'year': '2033'}, follow_redirects=False).status_code == 303
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT COUNT(*) AS n FROM setup_type_addons WHERE company_id=:q185_0 AND year=2033', (company,)).fetchone()['n']) == 0
            assert int(execute_sql(c, 'SELECT COUNT(*) AS n FROM setup_element_addons WHERE company_id=:q186_0 AND year=2033', (company,)).fetchone()['n']) == 0
            actions = {str(r['action']) for r in execute_sql(c, 'SELECT action FROM audit_log WHERE company_id=:q183_0', (company,)).fetchall()}
            required = {'PERSON_TYPE_SAVED','PERSON_DELETED','ADDON_SAVED','ADDON_DELETED','ELEMENT_DELETED','ELEMENT_TYPE_DELETED','PRICING_YEAR_DELETED','SEASON_SAVED','SEASON_DELETED','ADDON_RULES_RESET','FEATURE_EXTRA_SAVED'}
            assert required.issubset(actions), (required - actions)

    print('Direct Booking Web V1 Setup maintenance test: passed')


if __name__ == '__main__':
    main()
