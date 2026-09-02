from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
from online.setup015 import register_setup015


def login(client: TestClient, email: str, password: str) -> None:
    response = client.post('/login', data={'email': email, 'password': password}, follow_redirects=False)
    assert response.status_code == 303


def csrf(app, client: TestClient) -> str:
    token = client.cookies.get(COOKIE_NAME); assert token
    context = app.state.database.session_context(token); assert context
    return str(context['csrf_token'])


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'online015.db', seed_demo=True)
        register_setup015(app)
        client = TestClient(app)
        login(client, 'operator@forestview.test', 'Operator013!')
        token = csrf(app, client); db = app.state.database
        with db.connect() as c:
            forest = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])

        r = client.post('/setup/element-types', data={'csrf': token, 'id': '', 'name': ''}, follow_redirects=False)
        assert r.status_code == 400 and 'Please correct the highlighted field' in r.text and 'border:2px solid #b42318' in r.text and '"detail"' not in r.text
        r = client.post('/setup/element-types', data={'csrf': token, 'id': '', 'name': 'Camping'}, follow_redirects=False); assert r.status_code == 303
        r = client.post('/setup/element-types', data={'csrf': token, 'id': '', 'name': 'camping'}, follow_redirects=False); assert r.status_code == 400 and 'already exists' in r.text

        page = client.get('/setup/elements'); assert '<select' in page.text and 'name="element_type"' in page.text and '>Camping</option>' in page.text
        r = client.post('/setup/elements', data={'csrf': token, 'id': '', 'name': 'Pitch 7', 'element_type': 'Camping', 'pricing_method': 'Per night', 'base_price': '0.00'}, follow_redirects=False); assert r.status_code == 303
        client.post('/setup/person-types', data={'csrf': token, 'name': 'Adult', 'short_name': 'Ad'}, follow_redirects=False)
        client.post('/setup/person-types', data={'csrf': token, 'name': 'Child', 'short_name': 'Ch'}, follow_redirects=False)
        client.post('/setup/addons', data={'csrf': token, 'name': 'Dog', 'pricing_method': 'Per quantity per night'}, follow_redirects=False)

        with db.connect() as c:
            element = c.execute("SELECT * FROM setup_elements WHERE company_id=? AND name='Pitch 7'", (forest,)).fetchone(); adult = c.execute("SELECT * FROM setup_person_types WHERE company_id=? AND name='Adult'", (forest,)).fetchone(); child = c.execute("SELECT * FROM setup_person_types WHERE company_id=? AND name='Child'", (forest,)).fetchone(); dog = c.execute("SELECT * FROM setup_addons WHERE company_id=? AND name='Dog'", (forest,)).fetchone()
        assert element and adult and child and dog

        client.post('/setup/years/new', data={'csrf': token, 'year': '2026'}, follow_redirects=False)
        with db.connect() as c: season = c.execute('SELECT * FROM setup_seasons WHERE company_id=? AND year=2026', (forest,)).fetchone()
        price_key = f"r_{element['id']}_{season['id']}"
        r = client.post('/setup/pricing', data={'csrf': token, 'year': '2026', price_key: '20.00'}, follow_redirects=False); assert r.status_code == 303

        incomplete_occupancy = {
            'csrf': token, 'year': '2026', f"t_{element['id']}": '6',
            f"pmin_{element['id']}_{adult['id']}": '1', f"p_{element['id']}_{adult['id']}": '6', f"pr_{element['id']}_{adult['id']}": '',
            f"pmin_{element['id']}_{child['id']}": '0', f"p_{element['id']}_{child['id']}": '0', f"pr_{element['id']}_{child['id']}": '0',
        }
        r = client.post('/setup/occupancy', data=incomplete_occupancy, follow_redirects=False)
        assert r.status_code == 400 and 'Person € box' in r.text and 'border:2px solid #b42318' in r.text
        with db.connect() as c:
            assert c.execute('SELECT COUNT(*) n FROM setup_occupancy WHERE company_id=? AND year=2026', (forest,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) n FROM setup_person_prices WHERE company_id=? AND year=2026', (forest,)).fetchone()['n'] == 0

        occupancy = {
            'csrf': token, 'year': '2026', f"t_{element['id']}": '6',
            f"pmin_{element['id']}_{adult['id']}": '1', f"p_{element['id']}_{adult['id']}": '6', f"pr_{element['id']}_{adult['id']}": '8.00',
            f"pmin_{element['id']}_{child['id']}": '0', f"p_{element['id']}_{child['id']}": '0', f"pr_{element['id']}_{child['id']}": '0',
        }
        r = client.post('/setup/occupancy', data=occupancy, follow_redirects=False); assert r.status_code == 303
        page = client.get('/setup/occupancy?year=2026')
        assert 'Occupancy &amp; Person pricing' in page.text or 'Occupancy & Person pricing' in page.text
        assert '<th style="text-align:center">Min</th><th style="text-align:center">Max</th><th style="text-align:center">€</th>' in page.text
        with db.connect() as c:
            limit = c.execute('SELECT min_count,max_count FROM setup_person_limits WHERE company_id=? AND year=2026 AND element_id=? AND person_type_id=?', (forest, element['id'], adult['id'])).fetchone()
            assert int(limit['min_count']) == 1 and int(limit['max_count']) == 6
            assert float(c.execute('SELECT rate FROM setup_person_prices WHERE company_id=? AND year=2026 AND element_id=? AND person_type_id=?', (forest, element['id'], adult['id'])).fetchone()['rate']) == 8.0
            assert float(c.execute('SELECT rate FROM setup_person_prices WHERE company_id=? AND year=2026 AND element_id=? AND person_type_id=?', (forest, element['id'], child['id'])).fetchone()['rate']) == 0.0

        payload = {'csrf': token, 'year': '2026', f"ty_{dog['id']}_Camping": 'on', f"tymin_{dog['id']}_Camping": '1', f"tymax_{dog['id']}_Camping": '2', f"tyrate_{dog['id']}_Camping": '3.00', f"ov_{element['id']}_{dog['id']}": 'N'}
        r = client.post('/setup/addon-rules', data=payload, follow_redirects=False); assert r.status_code == 303

        calc = {'csrf': token, 'element_id': str(element['id']), 'start_date': '2026-06-01', 'end_date': '2026-06-03', f"person_{adult['id']}": '2', f"person_{child['id']}": '0', f"addon_{dog['id']}": '1'}
        r = client.post('/setup/price-test', data=calc, follow_redirects=False); assert r.status_code == 400 and 'unavailable' in r.text.lower()
        payload[f"ov_{element['id']}_{dog['id']}"] = 'I'
        r = client.post('/setup/addon-rules', data=payload, follow_redirects=False); assert r.status_code == 303
        r = client.post('/setup/price-test', data=calc, follow_redirects=False)
        assert r.status_code == 200 and 'Total: €78.00' in r.text and 'Adult' in r.text and '€8.00' in r.text and 'Element Type default Y' in r.text and 'max="2"' in r.text and '2 night(s), 2 person(s)' in r.text

        calc[f"addon_{dog['id']}"] = '3'
        r = client.post('/setup/price-test', data=calc, follow_redirects=False)
        assert r.status_code == 400 and 'minimum/maximum quantity' in r.text and 'max="2"' in r.text
        calc[f"person_{child['id']}"] = '1'; calc[f"addon_{dog['id']}"] = '0'
        r = client.post('/setup/price-test', data=calc, follow_redirects=False); assert r.status_code == 400 and 'occupancy rules' in r.text

        with db.connect() as c: type_id = int(c.execute("SELECT id FROM setup_element_types WHERE company_id=? AND name='Camping'", (forest,)).fetchone()['id'])
        r = client.post('/setup/element-types', data={'csrf': token, 'id': str(type_id), 'name': 'Touring'}, follow_redirects=False); assert r.status_code == 303
        with db.connect() as c:
            assert c.execute('SELECT element_type FROM setup_elements WHERE id=?', (element['id'],)).fetchone()['element_type'] == 'Touring'
            assert c.execute('SELECT COUNT(*) n FROM setup_type_addons WHERE company_id=? AND year=2026 AND element_type=?', (forest, 'Touring')).fetchone()['n'] == 1

        r = client.post('/setup/years/copy', data={'csrf': token, 'year': '2027'}, follow_redirects=False); assert r.status_code == 303
        with db.connect() as c:
            assert c.execute('SELECT copied_from_year FROM setup_years WHERE company_id=? AND year=2027', (forest,)).fetchone()['copied_from_year'] == 2026
            assert c.execute('SELECT COUNT(*) n FROM setup_element_rates WHERE company_id=? AND year=2027', (forest,)).fetchone()['n'] == 1
            assert c.execute('SELECT COUNT(*) n FROM setup_person_prices WHERE company_id=? AND year=2027', (forest,)).fetchone()['n'] == 2
            copied_limit = c.execute('SELECT min_count,max_count FROM setup_person_limits WHERE company_id=? AND year=2027 AND element_id=? AND person_type_id=?', (forest, element['id'], adult['id'])).fetchone()
            assert int(copied_limit['min_count']) == 1 and int(copied_limit['max_count']) == 6

        client.post('/logout', follow_redirects=False); login(client, 'operator@riverside.test', 'Operator013!'); assert 'Touring' not in client.get('/setup/element-types').text
        client.post('/logout', follow_redirects=False); login(client, 'customer@forestview.test', 'Customer013!'); assert client.get('/setup').status_code == 403

    print('Online Build 015 Element Types / Occupancy Person Min-Max pricing / Add-on cap / price-rules test: passed')


if __name__ == '__main__':
    main()
