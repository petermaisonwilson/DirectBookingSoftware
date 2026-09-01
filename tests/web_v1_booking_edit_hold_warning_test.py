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
    response = client.post('/login', data={'email': 'operator@forestview.test', 'password': 'Operator013!'}, follow_redirects=False)
    assert response.status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'booking-edit-hold-warning.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        login(client)

        context = db.session_context(client.cookies.get(COOKIE_NAME))
        assert context is not None
        company_id = int(context['company_id'])
        token = str(client.cookies.get(COOKIE_NAME))
        now = datetime.now(timezone.utc)
        renewal = now - timedelta(seconds=5)
        expires = now + timedelta(seconds=55)

        with db.connect() as c:
            c.execute("INSERT OR IGNORE INTO setup_element_types(company_id,name,active) VALUES (?,?,1)", (company_id, 'Edit Test Camping'))
            element_id = int(c.execute(
                "INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (?,?,?,?,?,1)",
                (company_id, 'Edit Test Pitch', 'Edit Test Camping', 'Per night', 0),
            ).lastrowid)
            adult_id = int(c.execute(
                "INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (?,?,?,?,?)",
                (company_id, 'Edit Test Adult', 'ETA', 1, 0),
            ).lastrowid)
            hold_id = int(c.execute(
                '''INSERT INTO element_holds(
                     company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,
                     renewal_required_at,expires_at,created_at,updated_at,lead_name
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    company_id, element_id, token, int(context['user_id']), '2035-08-10', '2035-08-13',
                    renewal.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds'),
                    now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), 'Smith',
                ),
            ).lastrowid)
            c.execute(
                'INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)',
                (hold_id, company_id, adult_id, 2, '[]'),
            )

        review = client.get('/availability/basket/review')
        assert review.status_code == 200
        assert f'href="/availability/start?edit_hold={hold_id}"' in review.text
        assert 'id="global-hold-modal"' in review.text
        assert 'Still want to hold these Elements?' in review.text

        edit = client.get('/availability/start', params={'edit_hold': hold_id})
        assert edit.status_code == 200
        assert 'Editing this basket item' in edit.text
        assert 'value="Smith"' in edit.text
        assert f'name="person_{adult_id}" value="2"' in edit.text
        assert f'name="edit_hold" value="{hold_id}"' in edit.text
        assert 'id="global-hold-modal"' in edit.text

        operations = client.get('/operations')
        assert operations.status_code == 200
        assert 'id="global-hold-modal"' in operations.text
        assert "fetch('/availability/basket'" in operations.text
        assert "setInterval(check,5000)" in operations.text

        status = client.get('/availability/basket')
        assert status.status_code == 200
        payload = status.json()
        assert payload['count'] == 1
        assert payload['items'][0]['lead_name'] == 'Smith'
        assert payload['items'][0]['element_name'] == 'Edit Test Pitch'
        assert payload['items'][0]['needs_confirmation'] is True

        calendar = client.get('/availability/calendar-v2', params={
            'element_type': 'Edit Test Camping', 'arrival': '2035-08-10', 'departure': '2035-08-13', 'edit_hold': hold_id,
        })
        assert calendar.status_code == 200
        assert 'RESERVE CHANGES' in calendar.text
        assert 'Editing Edit Test Pitch' in calendar.text

    print('Direct Booking Web V1 booking edit and global hold warning test: passed')


if __name__ == '__main__':
    main()
