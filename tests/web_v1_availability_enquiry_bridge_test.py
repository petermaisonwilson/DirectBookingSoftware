from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
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
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (cid, 'Bridge Camping'))
            element_id = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)", (cid, 'Bridge Pitch A', 'Bridge Camping', 'Per night', 0)).lastrowid)
            c.execute("INSERT OR IGNORE INTO setup_years(company_id,year) VALUES (?,?)", (cid, 2036))
            season_id = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)", (cid, 2036, 'Bridge Season', '2036-01-01', '2036-12-31')).lastrowid)
            c.execute('INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (?,?,?,?,?)', (cid, 2036, element_id, season_id, 20.0))
            c.execute('INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (?,?,?,?)', (cid, 2036, element_id, 6))
            people = c.execute('SELECT id FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY id', (cid,)).fetchall()
            assert people
            chosen_person = int(people[0]['id'])
            for person in people:
                pid = int(person['id'])
                c.execute('INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (?,?,?,?,?,?)', (cid, 2036, element_id, pid, 6, 0))
                c.execute('INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (cid, 2036, element_id, pid, 0.0))
            customer_id = int(c.execute('''INSERT INTO customer_records(company_id,first_name,last_name,email,phone,mobile_phone,fixed_phone,address1,address2,town,postcode,country,notes,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (cid, 'Alice', 'Walker', 'alice.bridge@example.test', '0611223344', '0611223344', '0299001122', '1 Test Road', '', 'Testville', '12345', 'France', '', now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'))).lastrowid)
            hold_id = int(c.execute('''INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at,lead_name)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (cid, element_id, token, int(context['user_id']), '2036-08-10', '2036-08-12', (now + timedelta(minutes=9)).isoformat(timespec='seconds'), (now + timedelta(minutes=10)).isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), 'Walker')).lastrowid)
            c.execute('INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)', (hold_id, cid, chosen_person, 2, '[]'))
            enquiry_count_before = int(c.execute('SELECT COUNT(*) AS n FROM enquiries WHERE company_id=?', (cid,)).fetchone()['n'])
            customer_count_before = int(c.execute('SELECT COUNT(*) AS n FROM customer_records WHERE company_id=?', (cid,)).fetchone()['n'])

        review = client.get('/availability/basket/review')
        assert review.status_code == 200
        assert 'Bridge Pitch A' in review.text
        assert 'CUSTOMER DETAILS / SAVE ENQUIRY' in review.text

        details = client.get('/availability/basket/customer', params={'hold_id': hold_id})
        assert details.status_code == 200
        for text in ('Customer Details', 'Lead Customer', 'Email address *', 'Mobile telephone', 'Fixed telephone', 'SAVE ENQUIRY'):
            assert text in details.text
        with db.connect() as c:
            assert int(c.execute('SELECT COUNT(*) AS n FROM enquiries WHERE company_id=?', (cid,)).fetchone()['n']) == enquiry_count_before

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
            assert int(c.execute('SELECT COUNT(*) AS n FROM enquiries WHERE company_id=?', (cid,)).fetchone()['n']) == enquiry_count_before
            assert int(c.execute('SELECT COUNT(*) AS n FROM customer_records WHERE company_id=?', (cid,)).fetchone()['n']) == customer_count_before

        saved = client.post('/availability/basket/customer', data=customer_payload | {'existing_customer_id': str(customer_id)}, follow_redirects=False)
        assert saved.status_code == 303
        assert saved.headers['location'].startswith('/operations/enquiries/')
        enquiry_id = int(saved.headers['location'].split('/')[3].split('?')[0])
        with db.connect() as c:
            enquiry = c.execute('SELECT * FROM enquiries WHERE id=? AND company_id=?', (enquiry_id, cid)).fetchone()
            assert enquiry is not None
            assert int(enquiry['customer_id']) == customer_id
            assert enquiry['arrival_date'] == '2036-08-10'
            assert enquiry['departure_date'] == '2036-08-12'
            assert enquiry['source'] == 'Availability'
            assert int(enquiry['party_size']) == 2
            request_row = c.execute('SELECT * FROM enquiry_requests WHERE enquiry_id=? AND company_id=?', (enquiry_id, cid)).fetchone()
            assert request_row is not None
            assert int(request_row['element_id']) == element_id
            assert request_row['element_type'] == 'Bridge Camping'
            assert float(request_row['provisional_total']) == 40.0
            person = c.execute('SELECT quantity FROM enquiry_people WHERE enquiry_id=? AND company_id=? AND person_type_id=?', (enquiry_id, cid, chosen_person)).fetchone()
            assert person is not None and int(person['quantity']) == 2
            assert c.execute('SELECT id FROM element_holds WHERE id=? AND company_id=? AND session_token=?', (hold_id, cid, token)).fetchone() is not None
            assert int(c.execute('SELECT COUNT(*) AS n FROM customer_records WHERE company_id=?', (cid,)).fetchone()['n']) == customer_count_before

        enquiry_page = client.get(f'/operations/enquiries/{enquiry_id}')
        assert enquiry_page.status_code == 200
        assert 'Alice Walker' in enquiry_page.text
        assert 'Bridge Pitch A' in enquiry_page.text
        assert '€40.00' in enquiry_page.text

    print('Direct Booking Web V1 Availability to Customer matching to Save Enquiry bridge test: passed')


if __name__ == '__main__':
    main()
