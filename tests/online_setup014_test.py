from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, create_app
from online.setup014 import register_setup014


def login(client: TestClient, email: str, password: str) -> None:
    response = client.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert response.status_code == 303


def csrf(app, client: TestClient) -> str:
    token = client.cookies.get(COOKIE_NAME)
    assert token
    context = app.state.database.session_context(token)
    assert context
    return str(context["csrf_token"])


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "online014.db"
        app = create_app(db_path, seed_demo=True)
        register_setup014(app)
        client = TestClient(app)

        login(client, "operator@forestview.test", "Operator013!")
        assert client.get("/setup").status_code == 200
        token = csrf(app, client)

        # Core catalogues.
        r = client.post("/setup/elements", data={
            "csrf": token, "id": "", "name": "Pitch 7", "element_type": "Camping",
            "pricing_method": "Per night", "base_price": "20.00",
        }, follow_redirects=False)
        assert r.status_code == 303
        client.post("/setup/person-types", data={"csrf": token, "name": "Adult", "short_name": "Ad"}, follow_redirects=False)
        client.post("/setup/person-types", data={"csrf": token, "name": "Child", "short_name": "Ch"}, follow_redirects=False)
        client.post("/setup/addons", data={"csrf": token, "name": "Dog", "pricing_method": "Per quantity per night"}, follow_redirects=False)

        db = app.state.database
        with db.connect() as c:
            forest = c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()["id"]
            element = c.execute("SELECT * FROM setup_elements WHERE company_id=? AND name='Pitch 7'", (forest,)).fetchone()
            adults = c.execute("SELECT * FROM setup_person_types WHERE company_id=? AND name='Adult'", (forest,)).fetchone()
            child = c.execute("SELECT * FROM setup_person_types WHERE company_id=? AND name='Child'", (forest,)).fetchone()
            dog = c.execute("SELECT * FROM setup_addons WHERE company_id=? AND name='Dog'", (forest,)).fetchone()
        assert element and adults and child and dog

        # Blank annual year automatically has an All Year season.
        r = client.post("/setup/years/new", data={"csrf": token, "year": "2026"}, follow_redirects=False)
        assert r.status_code == 303
        with db.connect() as c:
            season = c.execute("SELECT * FROM setup_seasons WHERE company_id=? AND year=2026", (forest,)).fetchone()
        assert season and season["start_date"] == "2026-01-01" and season["end_date"] == "2026-12-31"

        # Seasonal pricing: zero would also be accepted, but use an obvious value here.
        r = client.post("/setup/pricing", data={
            "csrf": token, "year": "2026", f"r_{element['id']}_{season['id']}": "25.00",
        }, follow_redirects=False)
        assert r.status_code == 303

        # Occupancy: explicit zero means Child is not allowed on this Element.
        r = client.post("/setup/occupancy", data={
            "csrf": token, "year": "2026", f"t_{element['id']}": "6",
            f"p_{element['id']}_{adults['id']}": "6", f"p_{element['id']}_{child['id']}": "0",
        }, follow_redirects=False)
        assert r.status_code == 303
        with db.connect() as c:
            child_limit = c.execute(
                "SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=2026 AND element_id=? AND person_type_id=?",
                (forest, element["id"], child["id"]),
            ).fetchone()["max_count"]
        assert child_limit == 0

        # Type default + individual N override preserves the Windows 012 I/Y/N model.
        addon_payload = {
            "csrf": token, "year": "2026",
            f"ty_{dog['id']}_Camping": "on",
            f"tymin_{dog['id']}_Camping": "0",
            f"tymax_{dog['id']}_Camping": "2",
            f"tyrate_{dog['id']}_Camping": "3.00",
            f"ov_{element['id']}_{dog['id']}": "N",
            f"ovmin_{element['id']}_{dog['id']}": "",
            f"ovmax_{element['id']}_{dog['id']}": "",
            f"ovrate_{element['id']}_{dog['id']}": "",
        }
        r = client.post("/setup/addon-rules", data=addon_payload, follow_redirects=False)
        assert r.status_code == 303
        with db.connect() as c:
            type_rule = c.execute("SELECT * FROM setup_type_addons WHERE company_id=? AND year=2026 AND element_type='Camping' AND addon_id=?", (forest,dog["id"])).fetchone()
            override = c.execute("SELECT * FROM setup_element_addons WHERE company_id=? AND year=2026 AND element_id=? AND addon_id=?", (forest,element["id"],dog["id"])).fetchone()
        assert type_rule["allowed"] == 1 and type_rule["max_qty"] == 2 and float(type_rule["rate"]) == 3.0
        assert override["state"] == "N"

        # Copy previous year must copy rates, occupancy and both Add-on rule levels.
        r = client.post("/setup/years/copy", data={"csrf": token, "year": "2027"}, follow_redirects=False)
        assert r.status_code == 303
        with db.connect() as c:
            assert c.execute("SELECT copied_from_year FROM setup_years WHERE company_id=? AND year=2027", (forest,)).fetchone()["copied_from_year"] == 2026
            assert c.execute("SELECT COUNT(*) n FROM setup_element_rates WHERE company_id=? AND year=2027", (forest,)).fetchone()["n"] == 1
            assert c.execute("SELECT COUNT(*) n FROM setup_occupancy WHERE company_id=? AND year=2027", (forest,)).fetchone()["n"] == 1
            assert c.execute("SELECT COUNT(*) n FROM setup_type_addons WHERE company_id=? AND year=2027", (forest,)).fetchone()["n"] == 1
            assert c.execute("SELECT state FROM setup_element_addons WHERE company_id=? AND year=2027", (forest,)).fetchone()["state"] == "N"

        # Setup changes are audited.
        audit = db.audit_rows(company_id=int(forest))
        actions = {row["action"] for row in audit}
        assert "ELEMENT_SAVED" in actions
        assert "OCCUPANCY_SAVED" in actions
        assert "ADDON_RULES_SAVED" in actions
        assert "PRICING_YEAR_COPIED" in actions

        # Another client cannot see Forest View setup data.
        client.post("/logout", follow_redirects=False)
        login(client, "operator@riverside.test", "Operator013!")
        page = client.get("/setup/elements")
        assert page.status_code == 200
        assert "Pitch 7" not in page.text

        # Customer role is denied Setup completely.
        client.post("/logout", follow_redirects=False)
        login(client, "customer@forestview.test", "Customer013!")
        assert client.get("/setup").status_code == 403

    print("Online Build 014 setup test: passed")


if __name__ == "__main__":
    main()
