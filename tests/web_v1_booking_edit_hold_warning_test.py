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
            for type_name in ('Edit Test Camping', 'Edit Test Fishing', 'Edit Test Cabins'):
                c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (cid, type_name))
            pitch1 = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Edit Test Pitch', 'Edit Test Camping', 'Per night', 0)).lastrowid)
            pitch2 = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Edit Test Pitch 2', 'Edit Test Camping', 'Per night', 0)).lastrowid)
            fishing = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Edit Test Peg A', 'Edit Test Fishing', 'Per day', 0)).lastrowid)
            cabin = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Edit Test Cabin 1', 'Edit Test Cabins', 'Per night', 0)).lastrowid)
            c.execute("INSERT OR IGNORE INTO setup_years(company_id,year) VALUES (?,?)", (cid, 2035))
            season = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (cid, 2035, 'Edit Season', '2035-01-01', '2035-12-31')).lastrowid)
            adult = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (?,?,?,?,?)", (cid, 'Edit Test Adult', 'ETA', 1, 0)).lastrowid)
            child = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (?,?,?,?,?)", (cid, 'Edit Test Child', 'ETC', 1, 0)).lastrowid)
            for eid in (pitch1, pitch2, fishing, cabin):
                c.execute('INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)', (cid, 2035, eid, season, 25.0))
            for eid, adult_max in ((pitch1, 1), (pitch2, 6), (cabin, 6)):
                c.execute('INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)', (cid, 2035, eid, 8))
                c.execute('INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)', (cid, 2035, eid, adult, adult_max))
                c.execute('INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (?,?,?,?,?)', (cid, 2035, eid, child, 6))
                c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (cid, 2035, eid, adult, 0.0))
                c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (cid, 2035, eid, child, 0.0))

            motorhome = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (cid, 'Edit Test Motorhome', 'Fixed once')).lastrowid)
            caravan = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (cid, 'Edit Test Caravan', 'Fixed once')).lastrowid)
            for aid in (motorhome, caravan):
                c.execute("UPDATE setup_addons SET item_kind='Feature',feature_group='Vehicle Type',ask_before_availability=1 WHERE id=? AND company_id=?", (aid, cid))
                for type_name in ('Edit Test Camping', 'Edit Test Fishing', 'Edit Test Cabins'):
                    c.execute('''INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate)
                                 VALUES (?,?,?,?,?,?,?,?)''', (cid, 2035, type_name, aid, 1, 0, 1, 0.0))

            def make_hold(element_id: int, lead: str) -> int:
                return int(c.execute(
                    '''INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,
                       renewal_required_at,expires_at,created_at,updated_at,lead_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (cid, element_id, token, int(context['user_id']), '2035-08-10', '2035-08-13',
                     (now + timedelta(minutes=9)).isoformat(timespec='seconds'), (now + timedelta(minutes=10)).isoformat(timespec='seconds'),
                     now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), lead),
                ).lastrowid)

            camping_hold = make_hold(pitch1, 'Smith')
            fishing_hold = make_hold(fishing, 'Smith')
            jones_hold = make_hold(cabin, 'Jones')

            for hold_id in (camping_hold, fishing_hold, jones_hold):
                c.execute('INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)', (hold_id, cid, adult, 1, '[]'))
                c.execute('INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)', (hold_id, cid, child, 2, '[]'))
                c.execute('INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity) VALUES (?,?,?,?)', (hold_id, cid, motorhome, 1))
                c.execute('INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity) VALUES (?,?,?,?)', (hold_id, cid, caravan, 0))

        review = client.get('/availability/basket/review')
        assert review.status_code == 200
        assert 'Smith' in review.text and 'Edit Test Pitch' in review.text and 'Edit Test Peg A' in review.text
        assert 'Jones' in review.text and 'Edit Test Cabin 1' in review.text
        assert f'href="/availability/start?edit_hold={camping_hold}"' in review.text and 'EDIT BOOKING' in review.text

        legacy = client.get('/availability/basket/edit', params={'hold_id': camping_hold}, follow_redirects=False)
        assert legacy.status_code == 303 and legacy.headers['location'] == f'/availability/start?edit_hold={camping_hold}'

        edit = client.get('/availability/start', params={'edit_hold': camping_hold})
        assert edit.status_code == 200
        assert 'Editing this basket item' in edit.text
        assert 'name="element_type"' in edit.text and 'value="Edit Test Camping" selected' in edit.text
        assert 'value="Smith"' in edit.text
        assert f'name="person_{adult}" value="1"' in edit.text and f'name="person_{child}" value="2"' in edit.text
        assert 'PEOPLE &amp; REQUIREMENTS' not in edit.text and 'ELEMENT &amp; DATES' not in edit.text

        # Smith changes the shared party requirements while editing Camping:
        # 2 adults, no children, Caravan instead of Motorhome.
        changed = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(camping_hold), 'lead_name': 'Smith', 'element_type': 'Edit Test Camping',
            'arrival': '2035-08-10', 'departure': '2035-08-13',
            f'person_{adult}': '2', f'person_{child}': '0',
            f'addon_{motorhome}': '0', f'addon_{caravan}': '1',
        }, follow_redirects=False)
        assert changed.status_code == 303
        assert 'element_type=Edit+Test+Camping' in changed.headers['location'] and f'edit_hold={camping_hold}' in changed.headers['location']

        # Element choices stay protected, but shared party requirements immediately
        # propagate to every Smith hold. Jones must remain untouched.
        with db.connect() as c:
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (camping_hold,)).fetchone()['element_id']) == pitch1
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (fishing_hold,)).fetchone()['element_id']) == fishing
            for hold_id in (camping_hold, fishing_hold):
                assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (hold_id, adult)).fetchone()['quantity']) == 2
                assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (hold_id, child)).fetchone()['quantity']) == 0
                assert int(c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (hold_id, motorhome)).fetchone()['quantity']) == 0
                assert int(c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (hold_id, caravan)).fetchone()['quantity']) == 1
            assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (jones_hold, child)).fetchone()['quantity']) == 2
            assert int(c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (jones_hold, motorhome)).fetchone()['quantity']) == 1
            assert int(c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (jones_hold, caravan)).fetchone()['quantity']) == 0

        shared_review = client.get('/availability/basket/review')
        assert shared_review.status_code == 200
        smith_section = shared_review.text
        assert smith_section.count('Edit Test Caravan 1') >= 2
        assert 'Edit Test Peg A' in smith_section
        # Jones still contributes the one remaining old Motorhome/children snapshot.
        assert 'Edit Test Motorhome 1' in smith_section and '2 Edit Test Child' in smith_section

        calendar = client.get(changed.headers['location'])
        assert calendar.status_code == 200
        assert 'Editing Smith' in calendar.text and 'Every Element of the selected type is shown' in calendar.text
        assert 'Edit Test Pitch' in calendar.text and 'Edit Test Pitch 2' in calendar.text
        assert 'CURRENTLY HELD — NOW UNSUITABLE' in calendar.text
        assert 'Edit Test Adult max 1' in calendar.text
        assert 'cal-cell own-held now-unsuitable' in calendar.text
        assert 'Not suitable for your requirements' in calendar.text
        assert 'USE THIS ELEMENT' in calendar.text
        assert 'id="edit-action-box-script"' not in calendar.text

        # Server refuses the now-unsuitable held Element even if browser controls are bypassed.
        blocked = client.post('/availability/basket/update', data={
            'csrf': csrf, 'hold_id': str(camping_hold), 'element_id': str(pitch1),
            'arrival_date': '2035-08-10', 'departure_date': '2035-08-13',
        })
        assert blocked.status_code == 409 and 'not suitable' in blocked.json()['error'].lower()
        with db.connect() as c:
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (camping_hold,)).fetchone()['element_id']) == pitch1

        # Choosing a suitable replacement changes Camping only. Fishing stays held and
        # keeps the new shared Smith requirements.
        updated = client.post('/availability/basket/update', data={
            'csrf': csrf, 'hold_id': str(camping_hold), 'element_id': str(pitch2),
            'arrival_date': '2035-08-10', 'departure_date': '2035-08-13',
        })
        assert updated.status_code == 200
        with db.connect() as c:
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (camping_hold,)).fetchone()['element_id']) == pitch2
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (fishing_hold,)).fetchone()['element_id']) == fishing
            assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (fishing_hold, child)).fetchone()['quantity']) == 0
            assert int(c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (fishing_hold, caravan)).fetchone()['quantity']) == 1

        # Element Type can still change during an edit while the selected Element remains protected.
        cabin_search = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(camping_hold), 'lead_name': 'Smith', 'element_type': 'Edit Test Cabins',
            'arrival': '2035-08-10', 'departure': '2035-08-13',
            f'person_{adult}': '2', f'person_{child}': '0',
            f'addon_{motorhome}': '0', f'addon_{caravan}': '1',
        }, follow_redirects=False)
        assert cabin_search.status_code == 303 and 'element_type=Edit+Test+Cabins' in cabin_search.headers['location']
        cabin_calendar = client.get(cabin_search.headers['location'])
        assert 'Edit Test Cabin 1' in cabin_calendar.text
        assert 'You are now viewing <strong>Edit Test Cabins</strong>' in cabin_calendar.text
        with db.connect() as c:
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (camping_hold,)).fetchone()['element_id']) == pitch2
            assert int(c.execute('SELECT element_id FROM element_holds WHERE id=?', (fishing_hold,)).fetchone()['element_id']) == fishing

        final_review = client.get('/availability/basket/review')
        assert 'Edit Test Pitch 2' in final_review.text and 'Edit Test Peg A' in final_review.text and 'Edit Test Cabin 1' in final_review.text
        operations = client.get('/operations')
        assert 'id="global-hold-modal"' in operations.text and "fetch('/availability/basket'" in operations.text

    print('Direct Booking Web V1 shared requirements and held-unsuitable edit test: passed')


if __name__ == '__main__':
    main()
