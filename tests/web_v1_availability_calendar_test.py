from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
from online.database import iso_now
from online.webv1 import register_web_v1


def login(client: TestClient, email: str, password: str) -> None:
    response = client.post('/login', data={'email': email, 'password': password}, follow_redirects=False)
    assert response.status_code == 303


def main() -> None:
    """Authoritative named multi-party Booking Requirements, Availability and basket lifecycle regression."""
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
        assert 'Please enter the lead name' in start.text and 'name="lead_name"' in start.text and 'placeholder="NAME"' in start.text
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

        for name, kind, group in (
            ('Current Motorhome', 'Feature', 'Vehicle Type'),
            ('Current Caravan', 'Feature', 'Vehicle Type'),
            ('Current Pets', 'Extra', ''),
        ):
            response = client.post('/setup/addons', data={'csrf': csrf, 'name': name, 'item_kind': kind, 'feature_group': group, 'pricing_method': 'Fixed once', 'ask_before_availability': 'on'}, follow_redirects=False)
            assert response.status_code == 303

        with db.connect() as c:
            motorhome = int(c.execute('SELECT id FROM setup_addons WHERE company_id=? AND name=?', (company, 'Current Motorhome')).fetchone()['id'])
            caravan = int(c.execute('SELECT id FROM setup_addons WHERE company_id=? AND name=?', (company, 'Current Caravan')).fetchone()['id'])
            pets = int(c.execute('SELECT id FROM setup_addons WHERE company_id=? AND name=?', (company, 'Current Pets')).fetchone()['id'])
            assert int(c.execute('SELECT ask_before_availability FROM setup_addons WHERE id=?', (pets,)).fetchone()['ask_before_availability']) == 1
            for aid in (motorhome, caravan, pets):
                c.execute('''INSERT OR REPLACE INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)''', (company, 2035, 'Current Camping', aid, 1, 0, 1, 0.0))

        # Smith family requirements and first held Element.
        saved = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'lead_name': 'Smith', 'arrival': '2035-07-10', 'departure': '2035-07-13',
            f'person_{adult}': '2', f'person_{child}': '1', f'age_{child}_1': '6',
            f'addon_{motorhome}': '0', f'addon_{caravan}': '1', f'addon_{pets}': '0',
        }, follow_redirects=False)
        assert saved.status_code == 303 and saved.headers['location'].startswith('/availability/calendar-v2?arrival=2035-07-10')

        with db.connect() as c:
            session = c.execute('SELECT lead_name FROM booking_requirement_sessions WHERE session_token=? AND company_id=?', (token, company)).fetchone()
            assert session is not None and session['lead_name'] == 'Smith'
            age_row = c.execute('SELECT quantity,ages_json FROM booking_requirement_people WHERE session_token=? AND company_id=? AND person_type_id=?', (token, company, child)).fetchone()
            assert age_row is not None and int(age_row['quantity']) == 1 and str(age_row['ages_json']) == '[6]'

        camping = client.get('/availability/calendar-v2', params={'element_type': 'Current Camping', 'start': '2035-07-06', 'arrival': '2035-07-10', 'departure': '2035-07-13'})
        assert camping.status_code == 200
        assert 'Current Pitch 1' in camping.text and 'Current Pitch 2' in camping.text
        assert 'Name:</strong> Smith' in camping.text
        assert 'Current Child U12 not allowed' in camping.text

        hold_response = client.post('/availability/hold', data={'csrf': csrf, 'element_id': str(pitch_two), 'arrival_date': '2035-07-10', 'departure_date': '2035-07-13'})
        assert hold_response.status_code == 200
        smith_hold = int(hold_response.json()['hold']['id'])
        with db.connect() as c:
            assert c.execute('SELECT lead_name FROM element_holds WHERE id=?', (smith_hold,)).fetchone()['lead_name'] == 'Smith'

        held_calendar = client.get('/availability/calendar-v2', params={'element_type': 'Current Camping', 'start': '2035-07-06', 'arrival': '2035-07-10', 'departure': '2035-07-13'})
        assert held_calendar.status_code == 200
        assert 'cal-cell own-held' in held_calendar.text
        assert 'cal-cell available basket-locked' in held_calendar.text
        assert '.cal-cell.basket-locked{cursor:help}' in held_calendar.text
        assert 'Already in your basket — use EDIT in Booking in progress to change it.' in held_calendar.text
        assert 'hold-expiry-calendar-refresh' not in held_calendar.text

        # ADD Element remains blank, but the existing held Element is still visible.
        blank_add = client.get('/availability/calendar-v2', params={'start': '2035-07-06', 'arrival': '2035-07-10', 'departure': '2035-07-13'})
        assert blank_add.status_code == 200
        assert '<option value="" selected>Select Element Type</option>' in blank_add.text
        assert 'Current Pitch 2' in blank_add.text and 'cal-cell own-held' in blank_add.text
        assert 'Held Elements are shown below.' in blank_add.text

        duplicate = client.post('/availability/hold', data={'csrf': csrf, 'element_id': str(pitch_two), 'arrival_date': '2035-07-20', 'departure_date': '2035-07-22'})
        assert duplicate.status_code == 409
        assert 'already held in your basket' in duplicate.json()['error']

        # NEW BOOKING clears the working group only; Smith remains held with Smith's snapshot.
        fresh_group = client.post('/availability/new-booking', data={'csrf': csrf}, follow_redirects=False)
        assert fresh_group.status_code == 303 and fresh_group.headers['location'] == '/availability/start'
        fresh_page = client.get('/availability/start')
        assert 'name="lead_name" placeholder="NAME" required value=""' in fresh_page.text
        with db.connect() as c:
            assert c.execute('SELECT lead_name FROM element_holds WHERE id=?', (smith_hold,)).fetchone()['lead_name'] == 'Smith'

        # Jones family is independent and holds Pitch 1.
        jones_saved = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'lead_name': 'Jones', 'arrival': '2035-07-11', 'departure': '2035-07-14',
            f'person_{adult}': '2', f'person_{child}': '0',
            f'addon_{motorhome}': '1', f'addon_{caravan}': '0', f'addon_{pets}': '1',
        }, follow_redirects=False)
        assert jones_saved.status_code == 303
        jones_hold_response = client.post('/availability/hold', data={'csrf': csrf, 'element_id': str(pitch_one), 'arrival_date': '2035-07-11', 'departure_date': '2035-07-14'})
        assert jones_hold_response.status_code == 200
        jones_hold = int(jones_hold_response.json()['hold']['id'])

        review = client.get('/availability/basket/review')
        assert review.status_code == 200
        assert 'Smith' in review.text and 'Jones' in review.text
        assert 'Current Pitch 2' in review.text and 'Current Pitch 1' in review.text
        assert 'Current Caravan 1' in review.text and 'Current Motorhome 1' in review.text and 'Current Pets 1' in review.text
        progress_calendar = client.get('/availability/calendar-v2', params={'element_type': 'Current Camping', 'start': '2035-07-06'})
        assert 'Smith — Current Pitch 2' in progress_calendar.text and 'Jones — Current Pitch 1' in progress_calendar.text
        assert 'Current Caravan 1' in progress_calendar.text and 'Current Pets 1' in progress_calendar.text

        # Explicit EDIT -> Change loads Smith, then saving updates Smith's hold immediately.
        edit_requirements = client.get('/availability/start', params={'edit_hold': smith_hold})
        assert edit_requirements.status_code == 200
        assert 'Editing this basket item' in edit_requirements.text and 'value="Smith"' in edit_requirements.text
        edited = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(smith_hold), 'lead_name': 'Smith', 'arrival': '2035-07-10', 'departure': '2035-07-13',
            f'person_{adult}': '2', f'person_{child}': '2', f'age_{child}_1': '7', f'age_{child}_2': '10',
            f'addon_{motorhome}': '0', f'addon_{caravan}': '1', f'addon_{pets}': '1',
        }, follow_redirects=False)
        assert edited.status_code == 303 and f'edit_hold={smith_hold}' in edited.headers['location']

        # Complete the edit through the same basket update route used by RESERVE CHANGES.
        completed_edit = client.post('/availability/basket/update', data={
            'csrf': csrf, 'hold_id': str(smith_hold), 'element_id': str(pitch_two),
            'arrival_date': '2035-07-10', 'departure_date': '2035-07-13',
        })
        assert completed_edit.status_code == 200
        with db.connect() as c:
            smith_child = c.execute('SELECT quantity,ages_json FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (smith_hold, child)).fetchone()
            smith_pets = c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (smith_hold, pets)).fetchone()
            jones_child = c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (jones_hold, child)).fetchone()
            jones_motorhome = c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (jones_hold, motorhome)).fetchone()
            assert smith_child is not None and int(smith_child['quantity']) == 2 and str(smith_child['ages_json']) == '[7, 10]'
            assert smith_pets is not None and int(smith_pets['quantity']) == 1
            assert jones_child is not None and int(jones_child['quantity']) == 0
            assert jones_motorhome is not None and int(jones_motorhome['quantity']) == 1

        edited_review = client.get('/availability/basket/review')
        assert 'Smith' in edited_review.text and '2 Current Child U12 (ages 7, 10)' in edited_review.text and 'Current Pets 1' in edited_review.text
        assert 'Jones' in edited_review.text and 'Current Motorhome 1' in edited_review.text

        # REMOVE only Smith; Jones remains intact.
        removed = client.post('/availability/basket/remove-view', data={'csrf': csrf, 'hold_id': str(smith_hold), 'return_to': '/availability/basket/review'}, follow_redirects=False)
        assert removed.status_code == 303
        with db.connect() as c:
            assert c.execute('SELECT COUNT(*) AS n FROM element_holds WHERE id=?', (smith_hold,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) AS n FROM hold_requirement_people WHERE hold_id=?', (smith_hold,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) AS n FROM hold_requirement_addons WHERE hold_id=?', (smith_hold,)).fetchone()['n'] == 0
            assert c.execute('SELECT lead_name FROM element_holds WHERE id=?', (jones_hold,)).fetchone()['lead_name'] == 'Jones'

        # RELEASE clears the remaining hold and its snapshots in one operation.
        released = client.post('/availability/holds/release', data={'csrf': csrf})
        assert released.status_code == 200 and released.json()['released'] == 1
        with db.connect() as c:
            assert c.execute('SELECT COUNT(*) AS n FROM element_holds WHERE id=?', (jones_hold,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) AS n FROM hold_requirement_people WHERE hold_id=?', (jones_hold,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) AS n FROM hold_requirement_addons WHERE hold_id=?', (jones_hold,)).fetchone()['n'] == 0

        # Automatic expiry uses the basket status endpoint and cleans snapshots too.
        expiry_hold_response = client.post('/availability/hold', data={'csrf': csrf, 'element_id': str(pitch_one), 'arrival_date': '2035-08-01', 'departure_date': '2035-08-03'})
        assert expiry_hold_response.status_code == 200
        expiry_hold = int(expiry_hold_response.json()['hold']['id'])
        with db.connect() as c:
            c.execute('UPDATE element_holds SET renewal_required_at=?,expires_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', '2000-01-01T00:00:01+00:00', expiry_hold))
        expired_status = client.get('/availability/basket')
        assert expired_status.status_code == 200 and expired_status.json()['items'] == []
        with db.connect() as c:
            assert c.execute('SELECT COUNT(*) AS n FROM element_holds WHERE id=?', (expiry_hold,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) AS n FROM hold_requirement_people WHERE hold_id=?', (expiry_hold,)).fetchone()['n'] == 0
            assert c.execute('SELECT COUNT(*) AS n FROM hold_requirement_addons WHERE hold_id=?', (expiry_hold,)).fetchone()['n'] == 0

        restart = client.get('/availability/calendar', follow_redirects=False)
        assert restart.status_code == 303 and restart.headers['location'] == '/availability/start'
        with db.connect() as c:
            ready = c.execute('SELECT ready,lead_name FROM booking_requirement_sessions WHERE session_token=? AND company_id=?', (token, company)).fetchone()
            people_left = c.execute('SELECT COUNT(*) AS n FROM booking_requirement_people WHERE session_token=? AND company_id=?', (token, company)).fetchone()
            assert ready is not None and int(ready['ready']) == 0 and str(ready['lead_name']) == ''
            assert int(people_left['n']) == 0

    print('Direct Booking Web V1 named multi-party Booking Requirements -> Availability and basket lifecycle test: passed')


if __name__ == '__main__':
    main()
