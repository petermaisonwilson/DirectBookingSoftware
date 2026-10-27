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

## Fresh acceptance and recovery policy

All current customer, enquiry, booking and finance records are disposable test
records. Start acceptance with fresh isolated databases; no historical conversion
project is required. Existing Setup and other tenant configurations are outside
any reset scope. The tests do not clear an existing installation.

For a **new empty** SQLite file or PostgreSQL database, configure its explicit
`DIRECTBOOKING_DATABASE_URL`, disable demo seeding, and run
`python -m alembic upgrade head`. SQLite's established local launcher can also
initialise a fresh file directly. Never apply these fresh-file instructions as
permission to stamp or overwrite an old local file. Normal incremental Alembic
upgrades remain supported and tested, including repeat runs and failed-constraint
rollback. Existing additive identity compatibility code is retained; no additional
historical migration tooling is planned.

`python tests/database_recovery_test.py` exercises fresh empty initialisation,
repeated upgrade, representative records created through the operational HTTP
workflow, native backup, rejection of damaged backups, restoration, exact table
contents, restart and subsequent sequence/ledger writes. SQLite uses its online
backup API with integrity/foreign-key checks. PostgreSQL uses real custom-format
`pg_dump` and transactional `pg_restore` in a dedicated `_test` database. CI uses
matching tools inside its PostgreSQL service container; outside CI install
matching PostgreSQL client tools and set `DBS_TEST_POSTGRES_URL`.

The PostgreSQL drill drops and restores ONLY its newly generated random test
schema. It verifies a failed restore leaves no partial schema before retrying a
valid archive. It also terminates a disposable pooled connection and verifies
pre-ping recovery, rejects missing-schema/unreachable configurations without
SQLite fallback, and runs migration/startup from staged package sources.

For real deployment backups, use the SQLite online backup API (not a raw copy of
an active WAL database), or whole-database PostgreSQL `pg_dump -Fc`, store the
archive outside the working database, verify it, and rehearse restoration into a
separate target. PostgreSQL roles/server configuration and external files require
separate backups. Before replacing any existing target, identify its scope,
retain its backup and obtain owner approval. A database backup restores Setup and
all tenant records at that snapshot; it is not a selective booking-data reset.
The automated test-schema drill does not establish site-specific disaster
recovery times or a production backup policy.

Permanent development requirements are in root `AGENTS.md` and the pull-request
template. The main build workflow covers pushes to every branch and pull requests
to every target (documentation-only pushes remain exempt). Both engines' central,
operational and recovery commands must pass before the dependent Windows build,
packaging or release can succeed. Repository branch protection is a separate
GitHub administrative setting; workflow failure alone does not prevent a user
with write permission from bypassing review or merging manually.
