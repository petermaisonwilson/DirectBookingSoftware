# Direct Booking Software development contract

- Maintain both native SQLite and genuine PostgreSQL support for every software
  change. An engine-specific success is insufficient. Do not emulate PostgreSQL
  or translate SQLite SQL at runtime.
- Retain Build 302 v3 and branch `build-302-payment-rules-fix` for the current
  assignment. Do not create Build 303 or merge PR #21 without explicit instruction.
- Preserve tenant isolation, pricing, payment authorisation, booking lifecycle,
  expiry/recovery behaviour, history and existing client-configured business rules.
  Consult the owner before architecture or business-rule decisions.
- All booking inventory writes must use the shared locked transaction, recheck
  availability inside it, and commit finance and immutable history atomically.
  Extend canonical operational commands rather than creating parallel writers.
- Use named bound SQL and tenant-scoped ownership checks. Never accept client
  input as proof of reservation, allocation, payment-method or tenant ownership.
- Keep normal schema upgrades incremental and repeatable on both engines. Test
  upgrade failure rollback when changing constraints/schema. Never stamp an
  unverified legacy database or replace invalid monetary values automatically.
- Existing customer/enquiry/booking/financial records are disposable test data;
  do not develop additional historical-data migration/conversion tooling unless
  a genuine technical necessity has been explained to the owner first. Fresh
  isolated test databases are the default acceptance route.
- Discardable test records do not authorise deletion/overwrite of Setup, source,
  business rules or other tenant data. Identify exact scope and obtain approval
  before modifying those existing records. Tests create and clean up only their
  own temporary SQLite files and isolated schemas in a PostgreSQL *_test database.
- Required checks: reservation_integration_test.py and
  run_operational_acceptance.py on BOTH engines, plus database_recovery_test.py
  on BOTH engines. Add meaningful coverage to these gates for changed behaviour.
  PostgreSQL recovery uses real pg_dump/pg_restore, never a SQL mock or skip.
- Do not weaken assertions, skip an engine, continue after a failing required
  command, or publish/package a successful build with a failed portability gate.
- Report verified engine/version, executable commit, checks and remaining limits.
  Passing CI does not establish production deployment readiness by itself.
