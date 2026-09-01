from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME, create_app
from online.database import iso_now
from online.webv1 import register_web_v1


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'cleanup.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        assert client.post('/login', data={'email': 'operator@forestview.test', 'password': 'Operator013!'}, follow_redirects=False).status_code == 303
        context = db.session_context(client.cookies.get(COOKIE_NAME)); assert context
        csrf = str(context['csrf_token']); company = int(context['company_id'])

        # Element setup no longer exposes or requires Base Price.
        page = client.get('/setup/elements')
        assert page.status_code == 200
        assert 'Base price' not in page.text
        element_type = 'Cleanup Test Type'
        assert client.post('/setup/element-types', data={'csrf': csrf, 'id': '', 'name': element_type}, follow_redirects=False).status_code == 303
        assert client.post('/setup/elements', data={
            'csrf': csrf,
            'id': '',
            'name': 'No Base Price Test',
            'element_type': element_type,
            'pricing_method': 'Per night',
        }, follow_redirects=False).status_code == 303
        with db.connect() as c:
            element = c.execute("SELECT * FROM setup_elements WHERE company_id=? AND name='No Base Price Test'", (company,)).fetchone()
            assert element is not None
            assert float(element['base_price']) == 0.0
        assert 'Base price' not in client.get('/setup/elements').text

        # Delete Year is visible on the canonical Years page and deletes an unused year.
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2044'}, follow_redirects=False).status_code == 303
        years_page = client.get('/setup/years')
        assert years_page.status_code == 200
        assert 'Delete year' in years_page.text and '2044' in years_page.text
        deleted = client.post('/setup/maintenance/years/delete', data={'csrf': csrf, 'year': '2044'}, follow_redirects=False)
        assert deleted.status_code == 303
        with db.connect() as c:
            assert c.execute('SELECT 1 FROM setup_years WHERE company_id=? AND year=2044', (company,)).fetchone() is None

        # A year with saved Enquiry/Booking history remains protected.
        assert client.post('/setup/years/new', data={'csrf': csrf, 'year': '2045'}, follow_redirects=False).status_code == 303
        now = iso_now()
        with db.connect() as c:
            customer = int(c.execute(
                'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                (company, 'Year', 'Protection', 'year-protection@example.test', '', now, now),
            ).lastrowid)
            c.execute(
                'INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (company, customer, 'new', 'Test', '2045-06-01', '2045-06-03', 1, '', now, now),
            )
        blocked = client.post('/setup/maintenance/years/delete', data={'csrf': csrf, 'year': '2045'}, follow_redirects=False)
        assert blocked.status_code == 303 and 'message=' in blocked.headers['location']
        with db.connect() as c:
            assert c.execute('SELECT 1 FROM setup_years WHERE company_id=? AND year=2045', (company,)).fetchone() is not None

    print('Direct Booking Element Base Price removal / Delete Year regression: passed')


if __name__ == '__main__':
    main()
