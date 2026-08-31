from __future__ import annotations

from online.config import load_runtime_config
from online.database import OnlineDatabase


def main() -> None:
    db = OnlineDatabase(load_runtime_config())
    assert db.dialect_name == "postgresql"
    db.initialise(seed_demo=False)
    db.seed_demo_data()

    supervisor = db.user_by_email("supervisor@directbooking.test")
    assert supervisor is not None
    assert supervisor["role"] == "supervisor"

    session = db.create_session(int(supervisor["id"]))
    context = db.session_context(session["token"])
    assert context is not None
    assert context["user_id"] == supervisor["id"]

    companies = db.companies()
    forest = next(row for row in companies if row["name"] == "Forest View Campsite")
    db.set_acting_company(session["token"], int(forest["id"]))
    context = db.session_context(session["token"])
    assert context is not None
    assert context["acting_company_id"] == forest["id"]

    before, after = db.update_company_contact(
        int(forest["id"]),
        contact_email="postgres-core@forestview.test",
        phone="+33 2 11 22 33 44",
    )
    assert before["contact_email"] != after["contact_email"]
    assert db.company(int(forest["id"]))["contact_email"] == after["contact_email"]

    db.write_audit(
        action="POSTGRES_CORE_TEST",
        entity_type="company",
        entity_id=forest["id"],
        actor_user_id=supervisor["id"],
        actor_role=supervisor["role"],
        company_id=forest["id"],
        acting_company_id=forest["id"],
        before=before,
        after=after,
    )
    audit = db.audit_rows(company_id=int(forest["id"]))
    assert any(row["action"] == "POSTGRES_CORE_TEST" for row in audit)

    db.delete_session(session["token"])
    assert db.session_context(session["token"]) is None
    print("PostgreSQL core runtime passed")


if __name__ == "__main__":
    main()
