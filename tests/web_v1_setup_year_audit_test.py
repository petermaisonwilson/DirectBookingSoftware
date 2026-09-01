from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME, create_app
from online.webv1 import register_web_v1


def login(client: TestClient) -> None:
    assert client.post('/login', data={'email': 'operator@forestview.test', 'password': 'Operator013!'}, follow_redirects=False).status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'setup-audit.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        login(client)
        context = db.session_context(client.cookies.get(COOKIE_NAME)); csrf = str(context['csrf_token'])

        with db.connect() as c:
            company = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (company, 'Audit Type'))
            element = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,0,1)", (company, 'Audit Element', 'Audit Type', 'Per night')).lastrowid)
            person = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active) VALUES (?,?,?,1)", (company, 'Audit Person', 'AuditP')).lastrowid)
            addon = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (company, 'Audit Addon', 'Per quantity')).lastrowid)
            c.execute("INSERT OR REPLACE INTO setup_addon_person_pricing(company_id,addon_id,pricing_mode) VALUES (?,?,?)", (company, addon, 'person_type'))
            c.execute("INSERT INTO setup_years(company_id,year,copied_from_year) VALUES (?,?,NULL)", (company, 2040))
            season = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (company, 2040, 'Audit Season', '2040-01-01', '2040-12-31')).lastrowid)
            c.execute("INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)", (company, 2040, element, season, 100.00))
            c.execute("INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)", (company, 2040, element, 4))
            c.execute("INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)", (company, 2040, element, person, 4))
            c.execute("INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)", (company, 2040, element, person, 20.00))
            c.execute("INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)", (company, 2040, 'Audit Type', addon, 1, 1, 1, 10.00))
            c.execute("INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (?,?,?,?,?)", (company, addon, 2040, person, 5.00))

        years_page = client.get('/setup/years')
        assert years_page.status_code == 200
        assert 'Price adjustment %' in years_page.text
        assert 'Round adjusted prices up to the nearest whole number' in years_page.text
        assert 'Create new blank-pricing year' in years_page.text
        assert 'Run Setup Audit' in years_page.text

        copied = client.post('/setup/years/copy', data={
            'csrf': csrf, 'source_year': '2040', 'year': '2041', 'percent': '4.3', 'round_up': '1'
        }, follow_redirects=False)
        assert copied.status_code == 303 and copied.headers['location'] == '/setup/audit?year=2041'
        with db.connect() as c:
            season_2041 = c.execute("SELECT * FROM setup_seasons WHERE company_id=? AND year=2041 AND name='Audit Season'", (company,)).fetchone()
            assert season_2041 is not None and season_2041['start_date'] == '2041-01-01' and season_2041['end_date'] == '2041-12-31'
            rate = c.execute('SELECT rate FROM setup_element_rates WHERE company_id=? AND year=2041 AND element_id=? AND season_id=?', (company, element, int(season_2041['id']))).fetchone()
            assert float(rate['rate']) == 105.0
            assert float(c.execute('SELECT rate FROM setup_person_prices WHERE company_id=? AND year=2041 AND element_id=? AND person_type_id=?', (company, element, person)).fetchone()['rate']) == 21.0
            addon_rule = c.execute("SELECT * FROM setup_type_addons WHERE company_id=? AND year=2041 AND element_type='Audit Type' AND addon_id=?", (company, addon)).fetchone()
            assert int(addon_rule['allowed']) == 1 and int(addon_rule['min_qty']) == 1 and int(addon_rule['max_qty']) == 1 and float(addon_rule['rate']) == 11.0
            assert float(c.execute('SELECT rate FROM setup_addon_person_rates WHERE company_id=? AND addon_id=? AND year=2041 AND person_type_id=?', (company, addon, person)).fetchone()['rate']) == 6.0
            assert int(c.execute('SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=2041 AND element_id=?', (company, element)).fetchone()['max_total']) == 4

        blank = client.post('/setup/years/new', data={'csrf': csrf, 'source_year': '2041', 'year': '2042'}, follow_redirects=False)
        assert blank.status_code == 303 and blank.headers['location'] == '/setup/audit?year=2042'
        with db.connect() as c:
            season_2042 = c.execute("SELECT * FROM setup_seasons WHERE company_id=? AND year=2042 AND name='Audit Season'", (company,)).fetchone()
            assert season_2042 is not None
            assert c.execute('SELECT rate FROM setup_element_rates WHERE company_id=? AND year=2042 AND element_id=?', (company, element)).fetchone() is None
            assert c.execute('SELECT rate FROM setup_person_prices WHERE company_id=? AND year=2042 AND element_id=? AND person_type_id=?', (company, element, person)).fetchone() is None
            assert c.execute('SELECT rate FROM setup_addon_person_rates WHERE company_id=? AND addon_id=? AND year=2042 AND person_type_id=?', (company, addon, person)).fetchone() is None
            blank_rule = c.execute("SELECT * FROM setup_type_addons WHERE company_id=? AND year=2042 AND element_type='Audit Type' AND addon_id=?", (company, addon)).fetchone()
            assert int(blank_rule['allowed']) == 1 and int(blank_rule['min_qty']) == 1 and int(blank_rule['max_qty']) == 1 and blank_rule['rate'] is None
            assert int(c.execute('SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=2042 AND element_id=?', (company, element)).fetchone()['max_total']) == 4
            assert int(c.execute('SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=2042 AND element_id=? AND person_type_id=?', (company, element, person)).fetchone()['max_count']) == 4

        audit_page = client.get('/setup/audit?year=2042')
        assert audit_page.status_code == 200
        assert 'Audit Element — Audit Season price missing' in audit_page.text
        assert 'Audit Element — Audit Person price missing' in audit_page.text
        assert 'Audit Type — Audit Addon price missing' in audit_page.text
        assert 'Audit Addon — Audit Person price missing' in audit_page.text
        assert '/setup/pricing?year=2042' in audit_page.text
        assert '/setup/occupancy?year=2042' in audit_page.text
        assert '/setup/addon-rules?year=2042' in audit_page.text
        assert '/setup/addons/when?year=2042' in audit_page.text

        # Explicit zero is a completed price, not a missing price.
        with db.connect() as c:
            season_2042 = int(c.execute("SELECT id FROM setup_seasons WHERE company_id=? AND year=2042 AND name='Audit Season'", (company,)).fetchone()['id'])
            c.execute('INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,0)', (company, 2042, element, season_2042))
            c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,0)', (company, 2042, element, person))
            c.execute("UPDATE setup_type_addons SET rate=0 WHERE company_id=? AND year=2042 AND element_type='Audit Type' AND addon_id=?", (company, addon))
            c.execute('INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (?,?,?,?,0)', (company, addon, 2042, person))
            actions = {str(r['action']) for r in c.execute('SELECT action FROM audit_log WHERE company_id=?', (company,)).fetchall()}
            assert 'PRICING_YEAR_COPIED_ADJUSTED' in actions and 'PRICING_YEAR_CREATED_BLANK' in actions

        audit_after_zero = client.get('/setup/audit?year=2042')
        assert 'Audit Element — Audit Season price missing' not in audit_after_zero.text
        assert 'Audit Element — Audit Person price missing' not in audit_after_zero.text
        assert 'Audit Type — Audit Addon price missing' not in audit_after_zero.text
        assert 'Audit Addon — Audit Person price missing' not in audit_after_zero.text

    print('Direct Booking Web V1 Setup Audit / Year Copy test: passed')


if __name__ == '__main__':
    main()
