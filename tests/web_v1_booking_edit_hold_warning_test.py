from __future__ import annotations
from operational_backend import create_app
from online.sql import execute_sql, insert_sql

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME
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
                execute_sql(c, 'INSERT INTO setup_element_types(company_id,name,active) VALUES (:q291_0,:q291_1,1) ON CONFLICT DO NOTHING', (cid, type_name))
            pitch1 = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q305_0,:q305_1,:q305_2,:q305_3,:q305_4,1)', (cid, 'Edit Test Pitch', 'Edit Test Camping', 'Per night', 0)).lastrowid)
            pitch2 = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q306_0,:q306_1,:q306_2,:q306_3,:q306_4,1)', (cid, 'Edit Test Pitch 2', 'Edit Test Camping', 'Per night', 0)).lastrowid)
            fishing = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q307_0,:q307_1,:q307_2,:q307_3,:q307_4,1)', (cid, 'Edit Test Peg A', 'Edit Test Fishing', 'Per day', 0)).lastrowid)
            cabin = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q308_0,:q308_1,:q308_2,:q308_3,:q308_4,1)', (cid, 'Edit Test Cabin 1', 'Edit Test Cabins', 'Per night', 0)).lastrowid)
            execute_sql(c, 'INSERT INTO setup_years(company_id,year) VALUES (:q284_0,:q284_1) ON CONFLICT DO NOTHING', (cid, 2035))
            season = int(insert_sql(c, 'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q309_0,:q309_1,:q309_2,:q309_3,:q309_4)', (cid, 2035, 'Edit Season', '2035-01-01', '2035-12-31')).lastrowid)
            adult = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (:q310_0,:q310_1,:q310_2,:q310_3,:q310_4)', (cid, 'Edit Test Adult', 'ETA', 1, 0)).lastrowid)
            child = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (:q311_0,:q311_1,:q311_2,:q311_3,:q311_4)', (cid, 'Edit Test Child', 'ETC', 1, 0)).lastrowid)
            for eid in (pitch1, pitch2, fishing, cabin):
                execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q292_0,:q292_1,:q292_2,:q292_3,:q292_4)', (cid, 2035, eid, season, 25.0))
            rules = {
                pitch1: (8, 1, 1, 0, 6),
                pitch2: (8, 1, 6, 0, 6),
                fishing: (1, 1, 1, 0, 0),
                cabin: (8, 1, 6, 0, 6),
            }
            for eid, (total_max, adult_min, adult_max, child_min, child_max) in rules.items():
                execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q293_0,:q293_1,:q293_2,:q293_3)', (cid, 2035, eid, total_max))
                execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (:q294_0,:q294_1,:q294_2,:q294_3,:q294_4,:q294_5)', (cid, 2035, eid, adult, adult_max, adult_min))
                execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (:q295_0,:q295_1,:q295_2,:q295_3,:q295_4,:q295_5)', (cid, 2035, eid, child, child_max, child_min))
                execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q296_0,:q296_1,:q296_2,:q296_3,:q296_4)', (cid, 2035, eid, adult, 0.0))
                execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q297_0,:q297_1,:q297_2,:q297_3,:q297_4)', (cid, 2035, eid, child, 0.0))

            motorhome = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (:q312_0,:q312_1,:q312_2,1)', (cid, 'Edit Test Motorhome', 'Fixed once')).lastrowid)
            caravan = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (:q313_0,:q313_1,:q313_2,1)', (cid, 'Edit Test Caravan', 'Fixed once')).lastrowid)
            landing = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (:q314_0,:q314_1,:q314_2,1)', (cid, 'Edit Test Landing Net', 'Fixed once')).lastrowid)
            for aid in (motorhome, caravan):
                execute_sql(c, "UPDATE setup_addons SET item_kind='Feature',feature_group='Vehicle Type',ask_before_availability=1 WHERE id=:q298_0 AND company_id=:q298_1", (aid, cid))
                for type_name in ('Edit Test Camping', 'Edit Test Cabins'):
                    execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q315_0,:q315_1,:q315_2,:q315_3,:q315_4,:q315_5,:q315_6,:q315_7)', (cid, 2035, type_name, aid, 1, 0, 1, 0.0))
            execute_sql(c, "UPDATE setup_addons SET item_kind='Feature',feature_group='Fishing Equipment',ask_before_availability=0 WHERE id=:q285_0 AND company_id=:q285_1", (landing, cid))
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q286_0,:q286_1,:q286_2,:q286_3,:q286_4,:q286_5,:q286_6,:q286_7)', (cid, 2035, 'Edit Test Fishing', landing, 1, 0, 1, 0.0))

            def make_hold(element_id: int, lead: str) -> int:
                return int(insert_sql(c, 'INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at,lead_name) VALUES (:q319_0,:q319_1,:q319_2,:q319_3,:q319_4,:q319_5,:q319_6,:q319_7,:q319_8,:q319_9,:q319_10)', (cid, element_id, token, int(context['user_id']), '2035-08-10', '2035-08-13', (now + timedelta(minutes=9)).isoformat(timespec='seconds'), (now + timedelta(minutes=10)).isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), lead)).lastrowid)

            camping_hold = make_hold(pitch1, 'Smith'); fishing_hold = make_hold(fishing, 'Smith'); jones_hold = make_hold(cabin, 'Jones')
            for hold_id in (camping_hold, fishing_hold, jones_hold):
                execute_sql(c, 'INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (:q299_0,:q299_1,:q299_2,:q299_3,:q299_4)', (hold_id, cid, adult, 1, '[]'))
                execute_sql(c, 'INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (:q300_0,:q300_1,:q300_2,:q300_3,:q300_4)', (hold_id, cid, child, 2, '[]'))
                execute_sql(c, 'INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity) VALUES (:q301_0,:q301_1,:q301_2,:q301_3)', (hold_id, cid, motorhome, 1))
                execute_sql(c, 'INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity) VALUES (:q302_0,:q302_1,:q302_2,:q302_3)', (hold_id, cid, caravan, 0))

        assert _relevant_person_ids_for_type(db, cid, 'Edit Test Fishing', 2035) == {adult}
        assert _relevant_addon_ids_for_type(db, cid, 'Edit Test Fishing', 2035) == set()
        assert {adult, child}.issubset(_relevant_person_ids_for_type(db, cid, 'Edit Test Camping', 2035))
        assert {motorhome, caravan}.issubset(_relevant_addon_ids_for_type(db, cid, 'Edit Test Camping', 2035))

        occupancy = client.get('/setup/occupancy?year=2035')
        assert occupancy.status_code == 200
        assert '>Min<' in occupancy.text and '>Max<' in occupancy.text
        assert f'name="pmin_{fishing}_{adult}" value="1"' in occupancy.text
        assert f'name="pmin_{fishing}_{child}" value="0"' in occupancy.text

        # Ask Age is owned by the Person Types page and drives Booking Requirements directly.
        person_types = client.get('/setup/person-types')
        assert person_types.status_code == 200
        assert 'Ask Age' in person_types.text
        assert '/setup/person-types/age-toggle' in person_types.text
        age_on = client.post('/setup/person-types/age-toggle', data={'csrf': csrf, 'person_type_id': str(child)}, follow_redirects=False)
        assert age_on.status_code == 303
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT ask_age FROM setup_person_types WHERE id=:q321_0 AND company_id=:q321_1', (child, cid)).fetchone()['ask_age']) == 1
        age_requirements = client.get('/availability/start', params={'element_type': 'Edit Test Camping'})
        assert age_requirements.status_code == 200
        assert '<label>Choose Element Type</label>' in age_requirements.text
        assert f'data-person="{child}" data-ask-age="1"' in age_requirements.text
        assert f'id="ages-{child}"' in age_requirements.text
        assert f'data-person="{adult}" data-ask-age="0"' in age_requirements.text
        age_off = client.post('/setup/person-types/age-toggle', data={'csrf': csrf, 'person_type_id': str(child)}, follow_redirects=False)
        assert age_off.status_code == 303
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT ask_age FROM setup_person_types WHERE id=:q322_0 AND company_id=:q322_1', (child, cid)).fetchone()['ask_age']) == 0

        review = client.get('/availability/basket/review')
        assert review.status_code == 200 and 'Edit Test Pitch' in review.text and 'Edit Test Peg A' in review.text and 'Edit Test Cabin 1' in review.text
        # TEST DATA ONLY: Basket has one authoritative summary. Each relevant
        # value therefore appears only in the persistent booking summary.
        # Fishing's deliberately stale Child/Motorhome values remain hidden.
        assert review.text.count('Edit Test Motorhome 1') == 2
        assert review.text.count('2 Edit Test Child') == 2
        assert 'Verify booking contents' not in review.text

        edit = client.get('/availability/start', params={'edit_hold': camping_hold})
        assert edit.status_code == 200 and 'Editing this basket item' in edit.text
        assert 'value="Edit Test Camping" selected' in edit.text
        assert 'only the Person Types, Features and Extras' in edit.text
        assert 'action="/availability/requirements"' in edit.text
        assert '<label>Choose Element Type</label>' in edit.text

        changed = client.post('/availability/requirements', data={
            'csrf': csrf, 'edit_hold': str(camping_hold), 'lead_name': 'Smith', 'element_type': 'Edit Test Camping',
            'arrival': '2035-08-10', 'departure': '2035-08-13', f'person_{adult}': '2', f'person_{child}': '0',
            f'addon_{motorhome}': '0', f'addon_{caravan}': '1',
        }, follow_redirects=False)
        assert changed.status_code == 303
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT quantity FROM hold_requirement_people WHERE hold_id=:q323_0 AND person_type_id=:q323_1', (camping_hold, adult)).fetchone()['quantity']) == 2
            assert int(execute_sql(c, 'SELECT quantity FROM hold_requirement_people WHERE hold_id=:q324_0 AND person_type_id=:q324_1', (camping_hold, child)).fetchone()['quantity']) == 0
            assert int(execute_sql(c, 'SELECT quantity FROM hold_requirement_people WHERE hold_id=:q325_0 AND person_type_id=:q325_1', (fishing_hold, adult)).fetchone()['quantity']) == 1
            assert int(execute_sql(c, 'SELECT quantity FROM hold_requirement_people WHERE hold_id=:q326_0 AND person_type_id=:q326_1', (fishing_hold, child)).fetchone()['quantity']) == 2
            assert int(execute_sql(c, 'SELECT quantity FROM hold_requirement_addons WHERE hold_id=:q327_0 AND addon_id=:q327_1', (fishing_hold, motorhome)).fetchone()['quantity']) == 1
            assert int(execute_sql(c, 'SELECT quantity FROM hold_requirement_people WHERE hold_id=:q328_0 AND person_type_id=:q328_1', (jones_hold, child)).fetchone()['quantity']) == 2

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

        children_only = client.post('/availability/requirements', data={
            'csrf': csrf, 'edit_hold': str(camping_hold), 'lead_name': 'Smith', 'element_type': 'Edit Test Camping',
            'arrival': '2035-08-10', 'departure': '2035-08-13', f'person_{adult}': '0', f'person_{child}': '2',
            f'addon_{motorhome}': '0', f'addon_{caravan}': '1',
        }, follow_redirects=False)
        assert children_only.status_code == 303
        children_calendar = client.get(children_only.headers['location'])
        assert 'Edit Test Adult minimum 1' in children_calendar.text

        fishing_edit = client.get('/availability/start', params={'edit_hold': fishing_hold})
        assert fishing_edit.status_code == 200 and 'value="Edit Test Fishing" selected' in fishing_edit.text
        fishing_saved = client.post('/availability/requirements', data={
            'csrf': csrf, 'edit_hold': str(fishing_hold), 'lead_name': 'Smith', 'element_type': 'Edit Test Fishing',
            'arrival': '2035-08-10', 'departure': '2035-08-13', f'person_{adult}': '1',
        }, follow_redirects=False)
        assert fishing_saved.status_code == 303
        with db.connect() as c:
            fishing_people = execute_sql(c, 'SELECT person_type_id,quantity FROM hold_requirement_people WHERE hold_id=:q316_0 ORDER BY person_type_id', (fishing_hold,)).fetchall()
            assert [(int(r['person_type_id']), int(r['quantity'])) for r in fishing_people] == [(adult, 1)]
            assert execute_sql(c, 'SELECT 1 FROM hold_requirement_addons WHERE hold_id=:q320_0', (fishing_hold,)).fetchone() is None

        final_review = client.get('/availability/basket/review')
        assert 'Edit Test Pitch 2' in final_review.text and 'Edit Test Peg A' in final_review.text and 'Edit Test Cabin 1' in final_review.text
        # Hold polling belongs only to the active booking journey. Ordinary
        # Operations pages must not poll the basket; the basket review must.
        # A fresh Requirements page has no live hold and must not start the
        # basket monitor. Once a hold exists, journey pages may monitor it.
        with db.connect() as c:
            saved_holds = execute_sql(c, 'SELECT id FROM element_holds WHERE company_id=:q317_0 AND session_token=:q317_1', (cid, token)).fetchall()
            saved_ids = [int(r['id']) for r in saved_holds]
            for hid in saved_ids:
                execute_sql(c, 'DELETE FROM hold_requirement_people WHERE hold_id=:q303_0', (hid,))
                execute_sql(c, 'DELETE FROM hold_requirement_addons WHERE hold_id=:q304_0', (hid,))
            execute_sql(c, 'DELETE FROM element_holds WHERE company_id=:q287_0 AND session_token=:q287_1', (cid, token))
        fresh_requirements = client.get('/availability/start')
        assert "fetch('/availability/basket'" not in fresh_requirements.text
        # Restore one valid live hold and prove Requirements opts into monitoring.
        with db.connect() as c:
            restored_hold = int(insert_sql(c, 'INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at,lead_name) VALUES (:q318_0,:q318_1,:q318_2,:q318_3,:q318_4,:q318_5,:q318_6,:q318_7,:q318_8,:q318_9,:q318_10)', (cid, pitch2, token, int(context['user_id']), '2035-08-10', '2035-08-13', (now + timedelta(minutes=9)).isoformat(timespec='seconds'), (now + timedelta(minutes=10)).isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), 'Smith')).lastrowid)
        live_requirements = client.get('/availability/start')
        assert "fetch('/availability/basket'" in live_requirements.text
        with db.connect() as c:
            execute_sql(c, 'DELETE FROM hold_requirement_people WHERE hold_id=:q288_0', (restored_hold,))
            execute_sql(c, 'DELETE FROM hold_requirement_addons WHERE hold_id=:q289_0', (restored_hold,))
            execute_sql(c, 'DELETE FROM element_holds WHERE id=:q290_0', (restored_hold,))

        operations = client.get('/operations')
        assert 'id="global-hold-modal"' not in operations.text and "fetch('/availability/basket'" not in operations.text
        assert 'id="global-hold-modal"' in final_review.text and "fetch('/availability/basket'" in final_review.text

    print('Direct Booking Web V1 Element-relevant requirements and Person minimum test: passed')


if __name__ == '__main__':
    main()
