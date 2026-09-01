from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
from online.webv1 import register_web_v1


def login(client: TestClient) -> None:
    assert client.post('/login', data={'email': 'operator@forestview.test', 'password': 'Operator013!'}, follow_redirects=False).status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'booking-edit-hold-warning.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        login(client)
        context = db.session_context(client.cookies.get(COOKIE_NAME))
        assert context is not None
        cid = int(context['company_id'])
        token = str(client.cookies.get(COOKIE_NAME))
        csrf = str(context['csrf_token'])
        now = datetime.now(timezone.utc)

        with db.connect() as c:
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (cid, 'Edit Test Camping'))
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (cid, 'Edit Test Cabins'))
            pitch1 = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Edit Test Pitch', 'Edit Test Camping', 'Per night', 0)).lastrowid)
            pitch2 = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Edit Test Pitch 2', 'Edit Test Camping', 'Per night', 0)).lastrowid)
            cabin = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Edit Test Cabin 1', 'Edit Test Cabins', 'Per night', 0)).lastrowid)
            c.execute("INSERT OR IGNORE INTO setup_years(company_id,year) VALUES (?,?)", (cid, 2035))
            season = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (cid, 2035, 'Edit Season', '2035-01-01', '2035-12-31')).lastrowid)
            adult = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (?,?,?,?,?)", (cid, 'Edit Test Adult', 'ETA', 1, 0)).lastrowid)
            for eid, maximum in ((pitch1, 6), (pitch2, 1), (cabin, 6)):
                c.execute('INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)', (cid, 2035, eid, season, 25.0))
                c.execute('INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)', (cid, 2035, eid, 6))
                c.execute('INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)', (cid, 2035, eid, adult, maximum))
                c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (cid, 2035, eid, adult, 0.0))
            hold_id = int(c.execute(
                '''INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,
                   renewal_required_at,expires_at,created_at,updated_at,lead_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (cid, pitch1, token, int(context['user_id']), '2035-08-10', '2035-08-13',
                 (now + timedelta(minutes=9)).isoformat(timespec='seconds'), (now + timedelta(minutes=10)).isoformat(timespec='seconds'),
                 now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), 'Smith'),
            ).lastrowid)
            c.execute('INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)', (hold_id, cid, adult, 1, '[]'))

        review = client.get('/availability/basket/review')
        assert review.status_code == 200
        assert f'href="/availability/start?edit_hold={hold_id}"' in review.text
        assert 'EDIT BOOKING' in review.text

        legacy = client.get('/availability/basket/edit', params={'hold_id': hold_id}, follow_redirects=False)
        assert legacy.status_code == 303 and legacy.headers['location'] == f'/availability/start?edit_hold={hold_id}'

        edit = client.get('/availability/start', params={'edit_hold': hold_id})
        assert edit.status_code == 200
        assert 'Editing this basket item' in edit.text
        assert 'name="element_type"' in edit.text and 'value="Edit Test Camping" selected' in edit.text
        assert 'value="Smith"' in edit.text and f'name="person_{adult}" value="1"' in edit.text
        assert 'PEOPLE &amp; REQUIREMENTS' not in edit.text and 'ELEMENT &amp; DATES' not in edit.text

        changed = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(hold_id), 'lead_name': 'Smith', 'element_type': 'Edit Test Camping',
            'arrival': '2035-08-10', 'departure': '2035-08-13', f'person_{adult}': '2',
        }, follow_redirects=False)
        assert changed.status_code == 303
        assert 'element_type=Edit+Test+Camping' in changed.headers['location'] and f'edit_hold={hold_id}' in changed.headers['location']

        # Editing requirements alone must not alter the protected held snapshot.
        with db.connect() as c:
            held = c.execute('SELECT element_id FROM element_holds WHERE id=?', (hold_id,)).fetchone()
            snap = c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (hold_id, adult)).fetchone()
            assert int(held['element_id']) == pitch1 and int(snap['quantity']) == 1

        calendar = client.get(changed.headers['location'])
        assert calendar.status_code == 200
        assert 'Editing Smith' in calendar.text and 'Every Element of the selected type is shown' in calendar.text
        assert 'Edit Test Pitch' in calendar.text and 'Edit Test Pitch 2' in calendar.text
        assert 'Not suitable: Edit Test Adult max 1' in calendar.text
        assert 'Not suitable for your requirements' in calendar.text
        assert 'USE THIS ELEMENT' in calendar.text
        assert 'id="edit-action-box-script"' not in calendar.text

        # Server refuses an unsuitable replacement even if browser controls are bypassed.
        blocked = client.post('/availability/basket/update', data={
            'csrf': csrf, 'hold_id': str(hold_id), 'element_id': str(pitch2),
            'arrival_date': '2035-08-10', 'departure_date': '2035-08-13',
        })
        assert blocked.status_code == 409 and 'not suitable' in blocked.json()['error'].lower()
        with db.connect() as c:
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (hold_id,)).fetchone()['element_id']) == pitch1
            assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (hold_id, adult)).fetchone()['quantity']) == 1

        # Element Type can change while the old Element remains safely held.
        cabin_search = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(hold_id), 'lead_name': 'Smith', 'element_type': 'Edit Test Cabins',
            'arrival': '2035-08-10', 'departure': '2035-08-13', f'person_{adult}': '1',
        }, follow_redirects=False)
        assert cabin_search.status_code == 303 and 'element_type=Edit+Test+Cabins' in cabin_search.headers['location']
        cabin_calendar = client.get(cabin_search.headers['location'])
        assert 'Edit Test Cabin 1' in cabin_calendar.text
        assert 'You are now viewing <strong>Edit Test Cabins</strong>' in cabin_calendar.text
        with db.connect() as c:
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (hold_id,)).fetchone()['element_id']) == pitch1

        # Make Pitch 2 suitable, then explicitly choose it. Only now does the held item change.
        suitable = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(hold_id), 'lead_name': 'Smith', 'element_type': 'Edit Test Camping',
            'arrival': '2035-08-11', 'departure': '2035-08-14', f'person_{adult}': '1',
        }, follow_redirects=False)
        assert suitable.status_code == 303
        suitable_calendar = client.get(suitable.headers['location'])
        assert 'Edit Test Pitch 2' in suitable_calendar.text and 'USE THIS ELEMENT' in suitable_calendar.text

        updated = client.post('/availability/basket/update', data={
            'csrf': csrf, 'hold_id': str(hold_id), 'element_id': str(pitch2),
            'arrival_date': '2035-08-11', 'departure_date': '2035-08-14',
        })
        assert updated.status_code == 200
        with db.connect() as c:
            held = c.execute('SELECT element_id,arrival_date,departure_date FROM element_holds WHERE id=?', (hold_id,)).fetchone()
            snap = c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (hold_id, adult)).fetchone()
            assert int(held['element_id']) == pitch2
            assert held['arrival_date'] == '2035-08-11' and held['departure_date'] == '2035-08-14'
            assert int(snap['quantity']) == 1

        final_review = client.get('/availability/basket/review')
        assert 'Edit Test Pitch 2' in final_review.text and '11/08/2035' in final_review.text and '14/08/2035' in final_review.text
        operations = client.get('/operations')
        assert 'id="global-hold-modal"' in operations.text and "fetch('/availability/basket'" in operations.text

    print('Direct Booking Web V1 simplified requirements/calendar edit test: passed')


if __name__ == '__main__':
    main()
