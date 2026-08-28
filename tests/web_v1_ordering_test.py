from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
from online.webv1 import register_web_v1


def login(client: TestClient) -> None:
    response = client.post('/login', data={'email': 'operator@forestview.test', 'password': 'Operator013!'}, follow_redirects=False)
    assert response.status_code == 303


def assert_in_order(text: str, labels: list[str]) -> None:
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions), (labels, positions)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'ordering.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        login(client)

        context = db.session_context(client.cookies.get(COOKIE_NAME))
        assert context is not None
        csrf = str(context['csrf_token'])
        company = int(context['company_id'])

        # Menu-card ordering is personal to the logged-in user.
        setup = client.get('/setup')
        assert setup.status_code == 200
        assert 'Drag the boxes into your preferred order' in setup.text
        wanted = ['price_test', 'pricing', 'years', 'feature_extra_rules', 'features_extras', 'occupancy', 'person_types', 'elements', 'element_types']
        saved = client.post('/ui/menu-order', data={'csrf': csrf, 'page_key': 'setup', 'order': ','.join(wanted)})
        assert saved.status_code == 200
        setup = client.get('/setup')
        assert_in_order(setup.text, ['Price / Rules test', 'Seasonal pricing', 'Annual setup', 'Feature / Extra Rules', 'Features & Extras', 'Occupancy', 'Person Types', 'Elements', 'Element Types'])

        # Setup-list ordering is Client-wide and drives Booking Requirements too.
        with db.connect() as c:
            ids = {}
            for name, short in (
                ('Ordering Adult', 'OA'),
                ('Ordering Child U12', 'OC12'),
                ('Ordering Child U16', 'OC16'),
                ('Ordering Infant', 'OI'),
                ('Ordering Child U8', 'OC8'),
            ):
                ids[name] = int(c.execute(
                    'INSERT INTO setup_person_types(company_id,name,short_name,active,ask_age) VALUES (?,?,?,?,?)',
                    (company, name, short, 1, 0),
                ).lastrowid)

        desired_names = ['Ordering Adult', 'Ordering Child U8', 'Ordering Child U12', 'Ordering Child U16', 'Ordering Infant']
        order_ids = [str(ids[name]) for name in desired_names]
        saved = client.post('/setup/item-order', data={'csrf': csrf, 'list_key': 'person_types', 'order': ','.join(order_ids)})
        assert saved.status_code == 200

        people_setup = client.get('/setup/person-types')
        assert people_setup.status_code == 200
        assert '☰ Drag' in people_setup.text
        assert_in_order(people_setup.text, desired_names)

        requirements = client.get('/availability/start')
        assert requirements.status_code == 200
        assert_in_order(requirements.text, desired_names)

        with db.connect() as c:
            stored = c.execute(
                'SELECT item_id FROM setup_item_order WHERE company_id=? AND list_key=? ORDER BY position',
                (company, 'person_types'),
            ).fetchall()
            assert [int(r['item_id']) for r in stored] == [int(x) for x in order_ids]

    print('Direct Booking drag-and-drop menu and Setup ordering test: passed')


if __name__ == '__main__':
    main()
