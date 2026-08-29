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


def main() -> None:
    """Authoritative Booking Requirements, Availability and basket-lock regression."""
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'current-availability.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        login(client, 'operator@forestview.test', 'Operator013!')

        context = db.session_context(client.cookies.get(COOKIE_NAME))
        assert context is not None
        csrf = str(context['csrf_token'])
        company = int(context['company_id'])
        token = str(client.cookies.get(COOKIE_NAME))

        entry = client.get('/availability/calendar', follow_redirects=False)
        assert entry.status_code == 303 and entry.headers['location'] == '/availability/start'
        start = client.get('/availability/start')
        assert start.status_code == 200
        assert 'Booking requirements' in start.text and 'coming and when?' in start.text
        assert 'name="arrival"' in start.text and 'name="departure"' in start.text
        assert 'Date of birth is not collected' in start.text

        with db.connect() as c:
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (company, 'Current Camping'))
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (company, 'Current Fishing'))
            pitch_one = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (company, 'Current Pitch 1', 'Current Camping', 'Per night', 0)).lastrowid)
            pitch_two = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (company, 'Current Pitch 2', 'Current Camping', 'Per night', 0)).lastrowid)
            peg = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (company, 'Current Peg A', 'Current Fishing', 'Per day', 0)).lastrowid)
            c.execute("INSERT OR IGNORE INTO setup_years(company_id,year) VALUES (?,?)", (company, 2035))
            season = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (company, 2035, 'Current Season', '2035-01-01', '2035-12-31')).lastrowid)
            for element_id in (pitch_one, pitch_two, peg):
                c.execute('INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)', (company, 2035, element_id, season, 25.0))
            for element_id in (pitch_one, pitch_two):
                c.execute('INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)', (company, 2035, element_id, 6))
            adult = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (?,?,?,?,?)", (company, 'Current Adult', 'CA', 1, 0)).lastrowid)
            child = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (?,?,?,?,?)", (company, 'Current Child U12', 'CC', 1, 1)).lastrowid)
            people = c.execute('SELECT id FROM setup_person_types WHERE company_id=? AND active=1', (company,)).fetchall()
            for element_id in (pitch_one, pitch_two):
                for person in people:
                    pid = int(person['id'])
                    max_count = 0 if pid == child and element_id == pitch_one else 6
                    c.execute('INSERT OR REPLACE INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)', (company, 2035, element_id, pid, max_count))
                    c.execute('INSERT OR REPLACE INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (company, 2035, element_id, pid, 0.0))

        for name in ('Current Motorhome', 'Current Caravan'):
            response = client.post('/setup/addons', data={'csrf': csrf, 'name': name, 'item_kind': 'Feature', 'feature_group': 'Vehicle Type', 'pricing_method': 'Fixed once', 'ask_before_availability': 'on'}, follow_redirects=False)
            assert response.status_code == 303

        with db.connect() as c:
            motorhome = int(c.execute('SELECT id FROM setup_addons WHERE company_id=? AND name=?', (company, 'Current Motorhome')).fetchone()['id'])
            caravan = int(c.execute('SELECT id FROM setup_addons WHERE company_id=? AND name=?', (company, 'Current Caravan')).fetchone()['id'])
            for aid in (motorhome, caravan):
                c.execute('''INSERT OR REPLACE INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)''', (company, 2035, 'Current Camping', aid, 1, 0, 1, 0.0))

        saved = client.post('/availability/requirements-v2', data={'csrf': csrf, f'person_{adult}': '2', f'person_{child}': '1', f'age_{child}_1': '6', 'feature_group_Vehicle_Type': str(motorhome), f'addon_{motorhome}': '1', f'addon_{caravan}': '1'}, follow_redirects=False)
        assert saved.status_code == 303 and saved.headers['location'] == '/availability/calendar-v2'

        with db.connect() as c:
            age_row = c.execute('SELECT quantity,ages_json FROM booking_requirement_people WHERE session_token=? AND company_id=? AND person_type_id=?', (token, company, child)).fetchone()
            assert age_row is not None and int(age_row['quantity']) == 1 and str(age_row['ages_json']) == '[6]'
            chosen = {int(r['addon_id']): int(r['quantity']) for r in c.execute('SELECT addon_id,quantity FROM booking_requirement_addons WHERE session_token=? AND company_id=?', (token, company)).fetchall()}
            assert chosen.get(motorhome) == 1 and chosen.get(caravan) == 0

        camping = client.get('/availability/calendar-v2', params={'element_type': 'Current Camping', 'start': '2035-07-01', 'arrival': '2035-07-01', 'departure': '2035-07-04'})
        assert camping.status_code == 200
        assert 'Current Pitch 1' in camping.text and 'Current Pitch 2' in camping.text
        assert 'Current Motorhome 1' in camping.text and 'Current Caravan 1' not in camping.text
        assert 'Current Child U12 not allowed' in camping.text

        fishing = client.get('/availability/calendar-v2', params={'element_type': 'Current Fishing', 'start': '2035-07-01', 'arrival': '2035-07-01', 'departure': '2035-07-02'})
        assert fishing.status_code == 200 and 'Current Peg A' in fishing.text
        assert 'no Current Motorhome' not in fishing.text and 'maximum occupancy' not in fishing.text

        # Hold Pitch 2 for only three nights. The rest of its visible row must stay
        # genuinely green (but duplicate-protected), not become yellow.
        hold_response = client.post('/availability/hold', data={'csrf': csrf, 'element_id': str(pitch_two), 'arrival_date': '2035-07-10', 'departure_date': '2035-07-13'})
        assert hold_response.status_code == 200
        hold_id = int(hold_response.json()['hold']['id'])
        held_calendar = client.get('/availability/calendar-v2', params={'element_type': 'Current Camping', 'start': '2035-07-06', 'arrival': '2035-07-10', 'departure': '2035-07-13'})
        assert held_calendar.status_code == 200
        assert 'cal-cell own-held' in held_calendar.text
        assert 'cal-cell available basket-locked' in held_calendar.text
        assert 'already in your basket. Use EDIT in Booking in progress to change it.' in held_calendar.text

        duplicate = client.post('/availability/hold', data={'csrf': csrf, 'element_id': str(pitch_two), 'arrival_date': '2035-07-20', 'departure_date': '2035-07-22'})
        assert duplicate.status_code == 409
        assert 'already held in your basket' in duplicate.json()['error']

        # Explicit EDIT -> Change must load this hold's snapshot into the working form.
        edit_requirements = client.get('/availability/start', params={'edit_hold': hold_id})
        assert edit_requirements.status_code == 200
        assert 'Editing this basket item' in edit_requirements.text
        with db.connect() as c:
            working_adult = c.execute('SELECT quantity FROM booking_requirement_people WHERE company_id=? AND session_token=? AND person_type_id=?', (company, token, adult)).fetchone()
            assert working_adult is not None and int(working_adult['quantity']) == 2

        edited = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(hold_id), 'arrival': '2035-07-10', 'departure': '2035-07-13',
            f'person_{adult}': '1', f'person_{child}': '0', f'addon_{motorhome}': '0', f'addon_{caravan}': '1',
        }, follow_redirects=False)
        assert edited.status_code == 303 and f'edit_hold={hold_id}' in edited.headers['location']
        with db.connect() as c:
            held_adult = c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (hold_id, adult)).fetchone()
            held_motorhome = c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (hold_id, motorhome)).fetchone()
            held_caravan = c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (hold_id, caravan)).fetchone()
            assert held_adult is not None and int(held_adult['quantity']) == 1
            assert held_motorhome is not None and int(held_motorhome['quantity']) == 0
            assert held_caravan is not None and int(held_caravan['quantity']) == 1

        # REMOVE must clear the hold and its snapshot records together.
        removed = client.post('/availability/basket/remove-view', data={'csrf': csrf, 'hold_id': str(hold_id), 'return_to': '/availability/basket/review'}, follow_redirects=False)
        assert removed.status_code == 303
        with db.connect() as c:
            assert c.execute('SELECT COUNT(*) AS n FROM element_holds WHERE id=?', (hold_id,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) AS n FROM hold_requirement_people WHERE hold_id=?', (hold_id,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) AS n FROM hold_requirement_addons WHERE hold_id=?', (hold_id,)).fetchone()['n'] == 0

        restart = client.get('/availability/calendar', follow_redirects=False)
        assert restart.status_code == 303 and restart.headers['location'] == '/availability/start'
        with db.connect() as c:
            ready = c.execute('SELECT ready FROM booking_requirement_sessions WHERE session_token=? AND company_id=?', (token, company)).fetchone()
            people_left = c.execute('SELECT COUNT(*) AS n FROM booking_requirement_people WHERE session_token=? AND company_id=?', (token, company)).fetchone()
            assert ready is not None and int(ready['ready']) == 0
            assert int(people_left['n']) == 0

    print('Direct Booking Web V1 authoritative Booking Requirements -> Availability and basket-lock test: passed')


if __name__ == '__main__':
    main()
