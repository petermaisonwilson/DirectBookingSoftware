from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
from online.webv1 import register_web_v1
from online.webv1_booking_requirements import _relevant_addon_ids_for_type, _relevant_person_ids_for_type


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
        cid = int(context['company_id']); token = str(client.cookies.get(COOKIE_NAME)); csrf = str(context['csrf_token']); now = datetime.now(timezone.utc)

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
            rules = {
                pitch1: (8, 1, 1, 0, 6),
                pitch2: (8, 1, 6, 0, 6),
                fishing: (1, 1, 1, 0, 0),
                cabin: (8, 1, 6, 0, 6),
            }
            for eid, (total_max, adult_min, adult_max, child_min, child_max) in rules.items():
                c.execute('INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)', (cid, 2035, eid, total_max))
                c.execute('INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (?,?,?,?,?,?)', (cid, 2035, eid, adult, adult_max, adult_min))
                c.execute('INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (?,?,?,?,?,?)', (cid, 2035, eid, child, child_max, child_min))
                c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (cid, 2035, eid, adult, 0.0))
                c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (cid, 2035, eid, child, 0.0))

            motorhome = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (cid, 'Edit Test Motorhome', 'Fixed once')).lastrowid)
            caravan = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (cid, 'Edit Test Caravan', 'Fixed once')).lastrowid)
            landing = int(c.execute("INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (?,?,?,1)", (cid, 'Edit Test Landing Net', 'Fixed once')).lastrowid)
            for aid in (motorhome, caravan):
                c.execute("UPDATE setup_addons SET item_kind='Feature',feature_group='Vehicle Type',ask_before_availability=1 WHERE id=? AND company_id=?", (aid, cid))
                for type_name in ('Edit Test Camping', 'Edit Test Cabins'):
                    c.execute('INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)', (cid, 2035, type_name, aid, 1, 0, 1, 0.0))
            c.execute("UPDATE setup_addons SET item_kind='Feature',feature_group='Fishing Equipment',ask_before_availability=0 WHERE id=? AND company_id=?", (landing, cid))
            c.execute('INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?)', (cid, 2035, 'Edit Test Fishing', landing, 1, 0, 1, 0.0))

            def make_hold(element_id: int, lead: str) -> int:
                return int(c.execute('''INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at,lead_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (cid, element_id, token, int(context['user_id']), '2035-08-10', '2035-08-13', (now + timedelta(minutes=9)).isoformat(timespec='seconds'), (now + timedelta(minutes=10)).isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), lead)).lastrowid)

            camping_hold = make_hold(pitch1, 'Smith'); fishing_hold = make_hold(fishing, 'Smith'); jones_hold = make_hold(cabin, 'Jones')
            # Old stored snapshots deliberately contain irrelevant Fishing values. #286
            # must stop displaying/evaluating those values against the Fishing Peg.
            for hold_id in (camping_hold, fishing_hold, jones_hold):
                c.execute('INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)', (hold_id, cid, adult, 1, '[]'))
                c.execute('INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)', (hold_id, cid, child, 2, '[]'))
                c.execute('INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity) VALUES (?,?,?,?)', (hold_id, cid, motorhome, 1))
                c.execute('INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity) VALUES (?,?,?,?)', (hold_id, cid, caravan, 0))

        assert _relevant_person_ids_for_type(db, cid, 'Edit Test Fishing', 2035) == {adult}
        assert _relevant_addon_ids_for_type(db, cid, 'Edit Test Fishing', 2035) == set()
        assert {adult, child}.issubset(_relevant_person_ids_for_type(db, cid, 'Edit Test Camping', 2035))
        assert {motorhome, caravan}.issubset(_relevant_addon_ids_for_type(db, cid, 'Edit Test Camping', 2035))

        occupancy = client.get('/setup/occupancy?year=2035')
        assert occupancy.status_code == 200
        assert '>Min<' in occupancy.text and '>Max<' in occupancy.text
        assert f'name="pmin_{fishing}_{adult}" value="1"' in occupancy.text
        assert f'name="pmin_{fishing}_{child}" value="0"' in occupancy.text

        review = client.get('/availability/basket/review')
        assert review.status_code == 200 and 'Edit Test Pitch' in review.text and 'Edit Test Peg A' in review.text and 'Edit Test Cabin 1' in review.text
        # Motorhome and Child are relevant to Camping and Cabin, but not Fishing: only
        # two copies should be visible despite three deliberately stale snapshots.
        assert review.text.count('Edit Test Motorhome 1') == 2
        assert review.text.count('2 Edit Test Child') == 2

        edit = client.get('/availability/start', params={'edit_hold': camping_hold})
        assert edit.status_code == 200 and 'Editing this basket item' in edit.text
        assert 'value="Edit Test Camping" selected' in edit.text
        assert 'Only the Person Types, Features and Extras' in edit.text or 'only the Person Types, Features and Extras' in edit.text

        changed = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(camping_hold), 'lead_name': 'Smith', 'element_type': 'Edit Test Camping',
            'arrival': '2035-08-10', 'departure': '2035-08-13', f'person_{adult}': '2', f'person_{child}': '0',
            f'addon_{motorhome}': '0', f'addon_{caravan}': '1',
        }, follow_redirects=False)
        assert changed.status_code == 303
        with db.connect() as c:
            assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (camping_hold, adult)).fetchone()['quantity']) == 2
            assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (camping_hold, child)).fetchone()['quantity']) == 0
            # Fishing intentionally remains its own one-adult booking; no cross-Element copying.
            assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (fishing_hold, adult)).fetchone()['quantity']) == 1
            assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (fishing_hold, child)).fetchone()['quantity']) == 2
            assert int(c.execute('SELECT quantity FROM hold_requirement_addons WHERE hold_id=? AND addon_id=?', (fishing_hold, motorhome)).fetchone()['quantity']) == 1
            assert int(c.execute('SELECT quantity FROM hold_requirement_people WHERE hold_id=? AND person_type_id=?', (jones_hold, child)).fetchone()['quantity']) == 2

        changed_review = client.get('/availability/basket/review')
        assert changed_review.text.count('Edit Test Caravan 1') == 1
        assert changed_review.text.count('Edit Test Motorhome 1') == 1
        assert changed_review.text.count('2 Edit Test Child') == 1

        calendar = client.get(changed.headers['location'])
        assert calendar.status_code == 200
        assert 'CURRENTLY HELD — NOW UNSUITABLE' in calendar.text
        assert 'Edit Test Adult max 1' in calendar.text
        assert 'cal-cell own-held now-unsuitable' in calendar.text

        blocked = client.post('/availability/basket/update', data={'csrf': csrf, 'hold_id': str(camping_hold), 'element_id': str(pitch1), 'arrival_date': '2035-08-10', 'departure_date': '2035-08-13'})
        assert blocked.status_code == 409 and 'not suitable' in blocked.json()['error'].lower()
        updated = client.post('/availability/basket/update', data={'csrf': csrf, 'hold_id': str(camping_hold), 'element_id': str(pitch2), 'arrival_date': '2035-08-10', 'departure_date': '2035-08-13'})
        assert updated.status_code == 200

        # No hard-coded Adult rule: the configured minimum is what rejects a
        # children-only Camping request.
        children_only = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(camping_hold), 'lead_name': 'Smith', 'element_type': 'Edit Test Camping',
            'arrival': '2035-08-10', 'departure': '2035-08-13', f'person_{adult}': '0', f'person_{child}': '2',
            f'addon_{motorhome}': '0', f'addon_{caravan}': '1',
        }, follow_redirects=False)
        assert children_only.status_code == 303
        children_calendar = client.get(children_only.headers['location'])
        assert 'Edit Test Adult minimum 1' in children_calendar.text

        # Editing Fishing cleans its old irrelevant snapshot and keeps only the one
        # Person Type Fishing actually uses. Landing Net is not an Ask question.
        fishing_edit = client.get('/availability/start', params={'edit_hold': fishing_hold})
        assert fishing_edit.status_code == 200 and 'value="Edit Test Fishing" selected' in fishing_edit.text
        fishing_saved = client.post('/availability/requirements-v3', data={
            'csrf': csrf, 'edit_hold': str(fishing_hold), 'lead_name': 'Smith', 'element_type': 'Edit Test Fishing',
            'arrival': '2035-08-10', 'departure': '2035-08-13', f'person_{adult}': '1',
        }, follow_redirects=False)
        assert fishing_saved.status_code == 303
        with db.connect() as c:
            fishing_people = c.execute('SELECT person_type_id,quantity FROM hold_requirement_people WHERE hold_id=? ORDER BY person_type_id', (fishing_hold,)).fetchall()
            assert [(int(r['person_type_id']), int(r['quantity'])) for r in fishing_people] == [(adult, 1)]
            assert c.execute('SELECT 1 FROM hold_requirement_addons WHERE hold_id=?', (fishing_hold,)).fetchone() is None

        final_review = client.get('/availability/basket/review')
        assert 'Edit Test Pitch 2' in final_review.text and 'Edit Test Peg A' in final_review.text and 'Edit Test Cabin 1' in final_review.text
        operations = client.get('/operations')
        assert 'id="global-hold-modal"' in operations.text and "fetch('/availability/basket'" in operations.text

    print('Direct Booking Web V1 Element-relevant requirements and Person minimum test: passed')


if __name__ == '__main__':
    main()