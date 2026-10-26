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


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'availability-enquiry-bridge.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        assert client.post('/login', data={'email': 'operator@forestview.test', 'password': 'Operator013!'}, follow_redirects=False).status_code == 303
        context = db.session_context(client.cookies.get(COOKIE_NAME))
        assert context is not None
        cid = int(context['company_id'])
        token = str(client.cookies.get(COOKIE_NAME))
        csrf = str(context['csrf_token'])
        now = datetime.now(timezone.utc)

        with db.connect() as c:
            execute_sql(c, 'INSERT INTO setup_element_types(company_id,name,active) VALUES (:q423_0,:q423_1,1) ON CONFLICT DO NOTHING', (cid, 'Bridge Camping'))
            element_id = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q440_0,:q440_1,:q440_2,:q440_3,:q440_4,1)', (cid, 'Bridge Pitch A', 'Bridge Camping', 'Per night', 0)).lastrowid)
            execute_sql(c, 'INSERT INTO setup_years(company_id,year) VALUES (:q424_0,:q424_1) ON CONFLICT DO NOTHING', (cid, 2036))
            season_id = int(insert_sql(c, 'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q441_0,:q441_1,:q441_2,:q441_3,:q441_4)', (cid, 2036, 'Bridge Season', '2036-01-01', '2036-12-31')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q425_0,:q425_1,:q425_2,:q425_3,:q425_4)', (cid, 2036, element_id, season_id, 20.0))
            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q426_0,:q426_1,:q426_2,:q426_3)', (cid, 2036, element_id, 6))
            chosen_person = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (:q442_0,:q442_1,:q442_2,:q442_3,:q442_4)', (cid, 'Bridge Adult', 'BA', 1, 0)).lastrowid)
            execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (:q427_0,:q427_1,:q427_2,:q427_3,:q427_4,:q427_5)', (cid, 2036, element_id, chosen_person, 6, 0))
            execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q428_0,:q428_1,:q428_2,:q428_3,:q428_4)', (cid, 2036, element_id, chosen_person, 0.0))
            customer_id = int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,mobile_phone,fixed_phone,address1,address2,town,postcode,country,notes,created_at,updated_at)\n                VALUES (:q443_0,:q443_1,:q443_2,:q443_3,:q443_4,:q443_5,:q443_6,:q443_7,:q443_8,:q443_9,:q443_10,:q443_11,:q443_12,:q443_13,:q443_14)', (cid, 'Alice', 'Walker', 'alice.bridge@example.test', '0611223344', '0611223344', '0299001122', '1 Test Road', '', 'Testville', '12345', 'France', '', now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'))).lastrowid)
            hold_id = int(insert_sql(c, 'INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at,lead_name)\n                VALUES (:q444_0,:q444_1,:q444_2,:q444_3,:q444_4,:q444_5,:q444_6,:q444_7,:q444_8,:q444_9,:q444_10)', (cid, element_id, token, int(context['user_id']), '2036-08-10', '2036-08-12', (now + timedelta(minutes=9)).isoformat(timespec='seconds'), (now + timedelta(minutes=10)).isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), 'Walker')).lastrowid)
            execute_sql(c, 'INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (:q429_0,:q429_1,:q429_2,:q429_3,:q429_4)', (hold_id, cid, chosen_person, 2, '[]'))
            element2_id = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q445_0,:q445_1,:q445_2,:q445_3,:q445_4,1)', (cid, 'Bridge Pitch B', 'Bridge Camping', 'Per night', 0)).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q430_0,:q430_1,:q430_2,:q430_3,:q430_4)', (cid, 2036, element2_id, season_id, 30.0))
            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q431_0,:q431_1,:q431_2,:q431_3)', (cid, 2036, element2_id, 6))
            execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (:q432_0,:q432_1,:q432_2,:q432_3,:q432_4,:q432_5)', (cid, 2036, element2_id, chosen_person, 6, 0))
            execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q433_0,:q433_1,:q433_2,:q433_3,:q433_4)', (cid, 2036, element2_id, chosen_person, 0.0))
            enquiry_count_before = int(execute_sql(c, 'SELECT COUNT(*) AS n FROM enquiries WHERE company_id=:q463_0', (cid,)).fetchone()['n'])
            customer_count_before = int(execute_sql(c, 'SELECT COUNT(*) AS n FROM customer_records WHERE company_id=:q464_0', (cid,)).fetchone()['n'])

        # TEST DATA ONLY: exercise the real Add / Change Element path rather than
        # seeding a second hold directly. The first held Element must remain intact.
        add_start = client.get('/availability/start', params={'element_type': 'Bridge Camping'})
        assert add_start.status_code == 200
        assert 'Next Guest Surname (if different)' in add_start.text
        assert 'name="lead_name" placeholder="SURNAME" required value="Walker"' in add_start.text
        assert 'name="edit_hold"' not in add_start.text

        add_requirements = client.post('/availability/requirements', data={
            'csrf': csrf,
            'lead_name': 'Jones',
            'element_type': 'Bridge Camping',
            'arrival': '2036-08-15',
            'departure': '2036-08-18',
            f'person_{chosen_person}': '3',
        }, follow_redirects=False)
        assert add_requirements.status_code == 303
        assert '/availability/calendar-v2?' in add_requirements.headers['location']
        second_hold = client.post('/availability/hold', data={
            'csrf': csrf,
            'element_id': str(element2_id),
            'arrival_date': '2036-08-15',
            'departure_date': '2036-08-18',
        })
        assert second_hold.status_code == 200 and second_hold.json()['ok'] is True
        hold2_id = int(second_hold.json()['hold']['id'])
        with db.connect() as c:
            first_hold = execute_sql(c, 'SELECT lead_name FROM element_holds WHERE id=:q446_0', (hold_id,)).fetchone()
            second_hold_row = execute_sql(c, 'SELECT lead_name FROM element_holds WHERE id=:q447_0', (hold2_id,)).fetchone()
            assert first_hold is not None and str(first_hold['lead_name']) == 'Walker'
            assert second_hold_row is not None and str(second_hold_row['lead_name']) == 'Jones'
            first_people = execute_sql(c, 'SELECT quantity FROM hold_requirement_people WHERE hold_id=:q448_0 AND person_type_id=:q448_1', (hold_id, chosen_person)).fetchone()
            second_people = execute_sql(c, 'SELECT quantity FROM hold_requirement_people WHERE hold_id=:q449_0 AND person_type_id=:q449_1', (hold2_id, chosen_person)).fetchone()
            assert first_people is not None and int(first_people['quantity']) == 2
            assert second_people is not None and int(second_people['quantity']) == 3

        review = client.get('/availability/basket/review')
        assert review.status_code == 200
        assert 'Bridge Pitch A' in review.text
        assert 'CONTINUE TO CUSTOMER DETAILS' in review.text and 'ADD ANOTHER ELEMENT' in review.text
        # TEST DATA ONLY: the persistent Booking in progress summary is the one
        # basket summary; do not repeat the same Elements in a verification table.
        assert review.text.count('Booking in progress') == 1
        assert 'Verify booking contents' not in review.text
        assert '<th>Name</th><th>Element</th><th>Arrival</th>' not in review.text

        details = client.get('/availability/basket/customer', params={'hold_id': hold_id})
        assert details.status_code == 200
        for text in ('Customer Details', 'Lead Customer', '* Required field', 'Email address *', 'Mobile telephone *', 'Fixed telephone', 'Bridge Pitch A', 'Bridge Pitch B', 'SAVE ENQUIRY'):
            assert text in details.text
        assert 'name="last_name" required value="Walker"' in details.text
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT COUNT(*) AS n FROM enquiries WHERE company_id=:q465_0', (cid,)).fetchone()['n']) == enquiry_count_before

        customer_payload = {
            'csrf': csrf,
            'hold_id': str(hold_id),
            'first_name': 'Alicia',
            'last_name': 'Walker-Smith',
            'email': 'alice.bridge@example.test',
            'mobile_phone': '06 11 22 33 44',
            'fixed_phone': '02 99 00 11 22',
            'address1': 'Different address',
            'address2': '',
            'town': 'Elsewhere',
            'postcode': '99999',
            'country': 'France',
            'notes': 'Availability enquiry bridge test',
        }
        duplicate = client.post('/availability/basket/customer', data=customer_payload)
        assert duplicate.status_code == 409
        assert 'Possible existing Customer' in duplicate.text
        assert 'Email + Mobile + Fixed telephone' in duplicate.text
        assert 'USE THIS CUSTOMER' in duplicate.text
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT COUNT(*) AS n FROM enquiries WHERE company_id=:q466_0', (cid,)).fetchone()['n']) == enquiry_count_before
            assert int(execute_sql(c, 'SELECT COUNT(*) AS n FROM customer_records WHERE company_id=:q467_0', (cid,)).fetchone()['n']) == customer_count_before

        saved = client.post('/availability/basket/customer', data=customer_payload | {'existing_customer_id': str(customer_id)}, follow_redirects=False)
        assert saved.status_code == 303
        assert saved.headers['location'].startswith('/operations/enquiries/')
        enquiry_id = int(saved.headers['location'].split('/')[3].split('?')[0])
        # TEST DATA ONLY: a newly-created Enquiry is holding space for the first
        # time. "HELD AGAIN" is reserved for an Enquiry that has actually been reopened.
        new_enquiry_page = client.get(f'/operations/enquiries/{enquiry_id}')
        assert new_enquiry_page.status_code == 200
        assert 'HOLDING SPACE' in new_enquiry_page.text
        assert 'HELD AGAIN' not in new_enquiry_page.text
        # TEST DATA ONLY: editing a held Enquiry must load the authoritative
        # element-specific requirements, not zeroed legacy enquiry-wide values.
        edit_page = client.get(f'/operations/enquiries/{enquiry_id}/edit')
        assert edit_page.status_code == 200
        # TEST DATA ONLY: this enquiry has two Elements, so the one-Element editor
        # must refuse to flatten or overwrite either Element.
        assert 'This Enquiry contains 2 Elements.' in edit_page.text
        assert 'could accidentally overwrite part of a multi-Element Enquiry' in edit_page.text
        with db.connect() as c:
            protected = execute_sql(c, 'SELECT element_id,lead_name,party_size,provisional_total FROM enquiry_elements WHERE enquiry_id=:q450_0 AND company_id=:q450_1 ORDER BY sort_order,id',(enquiry_id,cid)).fetchall()
            assert [int(r['element_id']) for r in protected] == [element_id,element2_id]
            assert [str(r['lead_name']) for r in protected] == ['Walker','Jones']
        assert 'Current space remains held while you edit.' not in edit_page.text
        assert 'SAVE CHANGES' not in edit_page.text
        assert 'CHECK CHANGES &amp; PRICE' not in edit_page.text
        # TEST DATA ONLY: the protection is enforced server-side too; a direct
        # POST cannot bypass the guarded multi-Element editor.
        blocked_post = client.post(f'/operations/enquiries/{enquiry_id}/edit', data={
            'csrf': csrf, 'action': 'save', 'arrival_date': '2036-09-01',
            'departure_date': '2036-09-03', 'party_size': '1', 'source': 'Availability',
            'notes': '', 'element_type': 'Bridge Camping', 'element_id': str(element_id),
            f'person_{chosen_person}': '1'
        }, follow_redirects=False)
        assert blocked_post.status_code == 409
        assert 'DBS has not changed anything.' in blocked_post.text
        with db.connect() as c:
            still_protected = execute_sql(c, 'SELECT element_id,arrival_date,departure_date,party_size FROM enquiry_elements WHERE enquiry_id=:q451_0 AND company_id=:q451_1 ORDER BY sort_order,id',(enquiry_id,cid)).fetchall()
            assert [(int(x['element_id']),str(x['arrival_date']),str(x['departure_date']),int(x['party_size'])) for x in still_protected] == [(element_id,'2036-08-10','2036-08-12',2),(element2_id,'2036-08-15','2036-08-18',3)]
        with db.connect() as c:
            enquiry = execute_sql(c, 'SELECT * FROM enquiries WHERE id=:q452_0 AND company_id=:q452_1', (enquiry_id, cid)).fetchone()
            assert enquiry is not None
            assert int(enquiry['customer_id']) == customer_id
            assert enquiry['arrival_date'] == '2036-08-10'
            assert enquiry['departure_date'] == '2036-08-18'
            assert enquiry['source'] == 'Availability'
            assert int(enquiry['party_size']) == 5
            assert enquiry['availability_expires_at'] is None
            request_row = execute_sql(c, 'SELECT * FROM enquiry_requests WHERE enquiry_id=:q453_0 AND company_id=:q453_1', (enquiry_id, cid)).fetchone()
            assert request_row is not None
            assert int(request_row['element_id']) == element_id
            assert request_row['element_type'] == 'Bridge Camping'
            assert float(request_row['provisional_total']) == 130.0
            element_rows=execute_sql(c, 'SELECT * FROM enquiry_elements WHERE enquiry_id=:q454_0 AND company_id=:q454_1 ORDER BY sort_order,id',(enquiry_id,cid)).fetchall()
            assert len(element_rows)==2
            assert [int(r['element_id']) for r in element_rows]==[element_id,element2_id]
            assert [str(r['lead_name']) for r in element_rows]==['Walker','Jones']
            assert [float(r['provisional_total']) for r in element_rows]==[40.0,90.0]
            assert [int(r['party_size']) for r in element_rows]==[2,3]
            people=execute_sql(c, 'SELECT ep.quantity FROM enquiry_element_people ep JOIN enquiry_elements ee ON ee.id=ep.enquiry_element_id WHERE ee.enquiry_id=:q455_0 AND ep.company_id=:q455_1 AND ep.person_type_id=:q455_2 ORDER BY ee.sort_order',(enquiry_id,cid,chosen_person)).fetchall()
            assert [int(p['quantity']) for p in people]==[2,3]
            assert execute_sql(c, 'SELECT id FROM element_holds WHERE id=:q459_0 AND company_id=:q459_1 AND session_token=:q459_2', (hold_id, cid, token)).fetchone() is None
            assert execute_sql(c, 'SELECT id FROM element_holds WHERE id=:q460_0 AND company_id=:q460_1 AND session_token=:q460_2', (hold2_id, cid, token)).fetchone() is None
            assert execute_sql(c, 'SELECT 1 FROM hold_requirement_people WHERE hold_id=:q461_0', (hold_id,)).fetchone() is None
            assert execute_sql(c, 'SELECT 1 FROM hold_requirement_addons WHERE hold_id=:q462_0', (hold_id,)).fetchone() is None
            assert int(execute_sql(c, 'SELECT COUNT(*) AS n FROM customer_records WHERE company_id=:q468_0', (cid,)).fetchone()['n']) == customer_count_before

        from online.webv1_status_availability import availability_state
        # TEST DATA ONLY: even a legacy/stale expiry timestamp must not silently release a saved Enquiry.
        # Once the temporary basket is converted, only explicit Release Enquiry frees inventory.
        with db.connect() as c:
            execute_sql(c, "UPDATE enquiries SET availability_expires_at='2000-01-01 00:00:00' WHERE id=:q434_0 AND company_id=:q434_1", (enquiry_id,cid))
        first_state=availability_state(db,cid,element_id,'2036-08-10','2036-08-12')
        second_state=availability_state(db,cid,element2_id,'2036-08-15','2036-08-18')
        assert first_state['available'] is False and first_state['state']=='ENQUIRY' and int(first_state['enquiry_id'])==enquiry_id
        holding_report=client.get('/operations/enquiries?holding=1')
        assert holding_report.status_code==200 and f'<a href="/operations/enquiries/{enquiry_id}">#{enquiry_id}</a>' in holding_report.text
        enquiry_anchor=f'<a href="/operations/enquiries/{enquiry_id}">#{enquiry_id}</a>'
        enquiry_row=holding_report.text.split(enquiry_anchor,1)[1].split('</tr>',1)[0]
        assert 'Holding Space' in enquiry_row and 'RELEASE ENQUIRY' in enquiry_row
        assert second_state['available'] is False and second_state['state']=='ENQUIRY' and int(second_state['enquiry_id'])==enquiry_id

        basket = client.get('/availability/basket')
        assert basket.status_code == 200
        assert basket.json()['count'] == 0
        enquiry_page = client.get(f'/operations/enquiries/{enquiry_id}')
        assert enquiry_page.status_code == 200
        assert 'Alice Walker' in enquiry_page.text
        assert 'Bridge Pitch A' in enquiry_page.text
        assert 'Bridge Pitch B' in enquiry_page.text
        # TEST DATA ONLY: the edited first Element remains present and the untouched second Element keeps its value.
        assert '€90.00' in enquiry_page.text
        assert 'Provisional total:' in enquiry_page.text

        # TEST DATA ONLY: partial reopen recovery. One original Element remains
        # available while the other becomes unavailable after explicit release.
        released=client.post(f'/operations/enquiries/{enquiry_id}/release',data={'csrf':csrf},follow_redirects=False)
        assert released.status_code==303
        with db.connect() as c:
            execute_sql(c, 'INSERT INTO element_closures(company_id,element_id,start_date,end_date,reason,created_at,updated_at) VALUES (:q435_0,:q435_1,:q435_2,:q435_3,:q435_4,:q435_5,:q435_6)',(cid,element2_id,'2036-08-15','2036-08-18','TEST DATA ONLY recovery conflict',now.isoformat(timespec='seconds'),now.isoformat(timespec='seconds')))
            element3_id=int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q456_0,:q456_1,:q456_2,:q456_3,:q456_4,1)',(cid,'Bridge Pitch C','Bridge Camping','Per night',0)).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q436_0,:q436_1,:q436_2,:q436_3,:q436_4)',(cid,2036,element3_id,season_id,35.0))
            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q437_0,:q437_1,:q437_2,:q437_3)',(cid,2036,element3_id,6))
            execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (:q438_0,:q438_1,:q438_2,:q438_3,:q438_4,:q438_5)',(cid,2036,element3_id,chosen_person,6,0))
            execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q439_0,:q439_1,:q439_2,:q439_3,:q439_4)',(cid,2036,element3_id,chosen_person,0.0))
        reopened=client.post(f'/operations/enquiries/{enquiry_id}/reopen',data={'csrf':csrf},follow_redirects=False)
        assert reopened.status_code==303
        with db.connect() as c:
            states=execute_sql(c, 'SELECT id,element_id,recovery_state FROM enquiry_elements WHERE enquiry_id=:q457_0 AND company_id=:q457_1 ORDER BY sort_order,id',(enquiry_id,cid)).fetchall()
            assert [str(x['recovery_state']) for x in states]==['held','needs_replacement']
            missing_eeid=int(states[1]['id'])
        assert availability_state(db,cid,element_id,'2036-08-10','2036-08-12')['state']=='ENQUIRY'
        partial=client.get(f'/operations/enquiries/{enquiry_id}')
        assert partial.status_code==200
        assert 'AVAILABLE — HELD AGAIN' in partial.text
        assert 'NOT AVAILABLE — REPLACEMENT REQUIRED' in partial.text
        assert 'FIND REPLACEMENT' in partial.text
        assert 'require replacement' in partial.text and 'disabled' in partial.text
        calendar=client.get('/availability/calendar-v2',params={'recovery_enquiry':enquiry_id,'recovery_element':missing_eeid,'element_type':'Bridge Camping','arrival':'2036-08-15','departure':'2036-08-18'})
        assert calendar.status_code==200
        assert 'REPLACE UNAVAILABLE ELEMENT' in calendar.text and 'Bridge Pitch B' in calendar.text
        assert 'USE REPLACEMENT' in calendar.text
        replacement=client.post(f'/operations/enquiries/{enquiry_id}/elements/{missing_eeid}/replace',data={'csrf':csrf,'element_id':element3_id,'arrival_date':'2036-08-16','departure_date':'2036-08-19'})
        assert replacement.status_code==200 and replacement.json()['ok'] is True and replacement.json()['remaining_replacements']==0
        with db.connect() as c:
            changed=execute_sql(c, 'SELECT element_id,arrival_date,departure_date,recovery_state FROM enquiry_elements WHERE id=:q458_0',(missing_eeid,)).fetchone()
            assert int(changed['element_id'])==element3_id and changed['arrival_date']=='2036-08-16' and changed['departure_date']=='2036-08-19' and changed['recovery_state']=='held'
        assert availability_state(db,cid,element3_id,'2036-08-16','2036-08-19')['state']=='ENQUIRY'
        recovered=client.get(f'/operations/enquiries/{enquiry_id}')
        assert 'NOT AVAILABLE — REPLACEMENT REQUIRED' not in recovered.text
        assert 'CONFIRM BOOKING' in recovered.text and '<button disabled>CONFIRM BOOKING</button>' not in recovered.text

    print('Direct Booking Web V1 Availability to Customer matching to Save Enquiry bridge test: passed')


if __name__ == '__main__':
    main()
