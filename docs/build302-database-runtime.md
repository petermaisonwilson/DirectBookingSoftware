# Build 302 v3 database runtime

DBS keeps the existing local SQLite-file startup and also supports the operational
PostgreSQL runtime. No SQL is translated or rewritten at runtime: the owning
modules declare portable named statements; the shared executor binds values and
normalises returned rows/generated keys.

## Local SQLite

The established launcher and permanent data-file location remain unchanged.
Existing unversioned SQLite files continue through the established additive local
initialisers. Canonical reservation storage uses the same reservation migrations
as PostgreSQL, without pretending the old database has a verified Alembic baseline.
No tenant's operational records are reset or deleted during startup.

Do not run `alembic upgrade head` or stamp an arbitrary revision against an existing
unversioned local file. Back up and verify its actual schema before any deliberate
conversion to versioned migrations.

## PostgreSQL

Use a backed-up, explicitly selected database. Install `requirements-online.txt`,
configure `DIRECTBOOKING_DATABASE_URL` with a `postgresql+psycopg://` URL, and run
`python -m alembic upgrade head` from the package directory before starting DBS.
The release contains `alembic.ini` and the migration sources needed for this.

Set `DIRECTBOOKING_ENV=production` and `DIRECTBOOKING_SEED_DEMO=0` for production.
Do not seed demo accounts into a production database. Preserve the existing
approved account-provisioning and deployment procedure.

`run_online.py` and the app factory honour the configured URL. A PostgreSQL error
is surfaced; it never silently selects a SQLite file instead. `/health` reports
`database_backend` so deployment verification can check the actual engine.

Reservation transactions use SQLite `BEGIN IMMEDIATE`, or PostgreSQL tenant
advisory locks with READ COMMITTED isolation. Inventory writers must retain that
shared boundary; separate preliminary availability reads are not authorisation
to write later without a locked recheck.

## Identity adoption and monetary integrity

On startup, the operational identity importer associates existing enquiries,
bookings, element details, finance and history with the permanent reservation ID.
It is additive and idempotent: source prices, financial amounts, source IDs and
party details are preserved. Missing/ambiguous relationships and cross-tenant
financial owners stop adoption rather than being guessed or reassigned.

Migration `0014_finite_reservation_money` rejects non-finite monetary values on
both engines. It never replaces an invalid amount. SQLite versioned migrations
and native canonical-storage upgrades begin an immediate transaction before DDL;
PostgreSQL retains transactional DDL. A failed upgrade must leave its prior schema,
version and records available for review and retry. If an older interrupted build
has already left migration debris, inspect the database and backup before repair;
do not stamp a revision or delete rebuild tables blindly.

The former standalone development creation entry forwards to the complete
Booking Requirements flow. Existing standalone summaries are retained read-only;
missing operational/financial details are never fabricated.

Operational forms use `/operations/reservations/{id}`. Old enquiry/booking URLs
remain compatible; detail storage remains present for original requirements,
pricing and historical links. New features must use the operational reservation
commands and shared write transaction, rather than raw state/allocation updates
that bypass pricing, confirmation authorisation or finance checks.

## Verification

`python tests/run_operational_acceptance.py` runs the operational acceptance
scenarios on SQLite. Setting `DBS_TEST_POSTGRES_URL` to a disposable PostgreSQL
database ending in `_test` runs the same assertions on PostgreSQL, in isolated
schemas. Test schema creation and removal must never target a customer database.
The test runner asserts the real backend and includes HTTP concurrency and
injected history-failure cases.

These instructions do not authorise a live database conversion, tenant reset,
new hosting deployment or any change to business rules.
