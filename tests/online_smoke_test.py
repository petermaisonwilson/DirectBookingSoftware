from __future__ import annotations

import re
import sqlite3
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from online.app import COOKIE_NAME, can_view_booking_log, create_app


def csrf_from(text: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', text)
    assert match, "CSRF field not found"
    return match.group(1)


def login(client: TestClient, email: str, password: str):
    response = client.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert response.status_code == 303
    assert COOKIE_NAME in response.cookies or COOKIE_NAME in client.cookies
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    return dashboard


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "online014.db"
        app = create_app(db_path)
        database = app.state.database

        with TestClient(app) as anonymous:
            health = anonymous.get("/health")
            assert health.status_code == 200
            assert health.json()["build"] == "014"

        with TestClient(app) as supervisor:
            dashboard = login(supervisor, "supervisor@directbooking.test", "Supervisor013!")
            assert "Supervisor Dashboard" in dashboard.text
            assert "Forest View Campsite" in dashboard.text
            assert "Riverside Cabins" in dashboard.text
            context = database.session_context(supervisor.cookies.get(COOKIE_NAME))
            assert can_view_booking_log(context) is True
            forest = next(row for row in database.companies() if row["name"] == "Forest View Campsite")
            csrf = csrf_from(dashboard.text)
            start = supervisor.post(f"/support/start/{forest['id']}", data={"csrf": csrf}, follow_redirects=True)
            assert start.status_code == 200
            assert "SUPPORT MODE" in start.text
            assert "Viewing Forest View Campsite" in start.text

            csrf = csrf_from(start.text)
            save = supervisor.post(
                "/company/settings",
                data={"csrf": csrf, "contact_email": "new@forestview.test", "phone": "+33 2 99 88 77 66"},
                follow_redirects=True,
            )
            assert save.status_code == 200 and "permanent audit trail" in save.text
            rows = database.audit_rows(company_id=int(forest["id"]))
            update = next(row for row in rows if row["action"] == "COMPANY_CONTACT_UPDATED")
            assert update["actor_role"] == "supervisor"
            assert update["acting_company_id"] == forest["id"]
            assert "new@forestview.test" in update["after_json"]

            audit = supervisor.get(f"/audit?company_id={forest['id']}")
            assert audit.status_code == 200
            assert "Global Audit" in audit.text
            assert "COMPANY_CONTACT_UPDATED" in audit.text

        with TestClient(app) as operator:
            dashboard = login(operator, "operator@forestview.test", "Operator013!")
            assert "Forest View Campsite" in dashboard.text
            assert "Riverside Cabins" not in dashboard.text
            context = database.session_context(operator.cookies.get(COOKIE_NAME))
            assert can_view_booking_log(context) is True
            assert operator.get("/audit").status_code == 403
            settings = operator.get("/company/settings")
            csrf = csrf_from(settings.text)
            changed = operator.post(
                "/company/settings",
                data={"csrf": csrf, "contact_email": "operator-change@forestview.test", "phone": "12345"},
                follow_redirects=True,
            )
            assert changed.status_code == 200
            rows = database.audit_rows(company_id=context["company_id"])
            update = next(row for row in rows if row["action"] == "COMPANY_CONTACT_UPDATED")
            assert update["actor_role"] == "operator"

        with TestClient(app) as customer:
            dashboard = login(customer, "customer@forestview.test", "Customer013!")
            assert "Customer Area" in dashboard.text
            context = database.session_context(customer.cookies.get(COOKIE_NAME))
            assert can_view_booking_log(context) is False
            assert customer.get("/audit").status_code == 403
            assert customer.get("/company/settings").status_code == 403

        with database.connect() as connection:
            audit_id = connection.execute("SELECT id FROM audit_log ORDER BY id LIMIT 1").fetchone()["id"]
            try:
                connection.execute("DELETE FROM audit_log WHERE id=?", (audit_id,))
                raise AssertionError("Audit row was deletable")
            except sqlite3.DatabaseError as exc:
                assert "append-only" in str(exc)
            try:
                connection.execute("UPDATE audit_log SET action='CHANGED' WHERE id=?", (audit_id,))
                raise AssertionError("Audit row was editable")
            except sqlite3.DatabaseError as exc:
                assert "append-only" in str(exc)

    print("Online Build 014 foundation smoke test: passed")


if __name__ == "__main__":
    main()
