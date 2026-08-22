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


def csrf_for(client: TestClient, db) -> str:
    context = db.session_context(client.cookies.get(COOKIE_NAME))
    assert context is not None
    return str(context['csrf_token'])


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'operations-navigation.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)

        login(client, 'operator@forestview.test', 'Operator013!')
        dashboard = client.get('/dashboard')
        assert dashboard.status_code == 200
        assert '<a href="/operations">Operations</a>' in dashboard.text
        assert 'Open Operations' in dashboard.text
        assert 'Client Register and Enquiry Search are available in Operations.' in dashboard.text
        operations = client.get('/operations')
        assert operations.status_code == 200
        client.post('/logout', follow_redirects=False)

        login(client, 'customer@forestview.test', 'Customer013!')
        customer_dashboard = client.get('/dashboard')
        assert customer_dashboard.status_code == 200
        assert '<a href="/operations">Operations</a>' not in customer_dashboard.text
        client.post('/logout', follow_redirects=False)

        login(client, 'supervisor@directbooking.test', 'Supervisor013!')
        supervisor_dashboard = client.get('/dashboard')
        assert supervisor_dashboard.status_code == 200
        assert '<a href="/operations">Operations</a>' not in supervisor_dashboard.text
        with db.connect() as connection:
            forest = int(connection.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
        csrf = csrf_for(client, db)
        enter = client.post(f'/support/start/{forest}', data={'csrf': csrf}, follow_redirects=False)
        assert enter.status_code == 303
        support_page = client.get('/company/settings')
        assert support_page.status_code == 200
        assert '<a href="/operations">Operations</a>' in support_page.text
        assert 'SUPPORT MODE' in support_page.text

    print('Direct Booking Web V1 Operations navigation test: passed')


if __name__ == '__main__':
    main()
