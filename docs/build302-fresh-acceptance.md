# Build 302 v3: fresh-database acceptance and permanent portability gate

27 October 2026. Branch `build-302-payment-rules-fix`.

## Decision

**Normal feature development can resume on this verified dual-engine baseline.**
Build #697 completed successfully, including its dependent Windows regressions,
15 reservation unit tests, launcher/occupied-port checks, local server health,
packaging and pre-release publication. The new acceptance work required one CI
build, with no application-code or business-rule correction needed.

[Build 302 v3 Test #697 package](https://github.com/petermaisonwilson/DirectBookingSoftware/releases/tag/dbs-build-302-test-697).
No Build 303 was created and PR #21 remains open and unmerged. The subsequent
report commit changes documentation only; executable source remains `77ea58c`.

## Scope and data handling

The owner's clarified scope treats all existing customer, enquiry, booking and
financial records as disposable test data. No historical customer-data migration
project is needed. This work added no historical conversion utility and did not
change application architecture, pricing or business rules.

Every drill used its own newly created temporary SQLite file or random schema in
`reservations_test`. No existing installation was cleared. Existing Setup,
application source/business configuration and other client/tenant data were not
deleted or overwritten. Previous compatibility code remains in place; it was not
expanded into a migration project.

## Changes

Commit `77ea58c9bd305cddd1b338f49caf6432177f6ff7` adds:

- Four fresh-start/recovery acceptance scenarios per engine, using the existing
  full HTTP booking journey to generate representative records.
- SQLite online backups and integrity/foreign-key checks; damaged-backup
  rejection; exact restored table contents; restart and subsequent writes.
- Genuine PostgreSQL custom-format pg_dump/pg_restore, intentionally damaged
  archive failure with transactional rollback, valid restore, exact restored
  table contents and successful subsequent sequence/financial writes.
- PostgreSQL pooled-connection termination/reconnection, explicit missing-schema
  and unreachable-server failure without SQLite fallback.
- Migration and actual launcher health checks from staged release source files.
- Repository-wide `AGENTS.md` rules, a dual-engine PR checklist, and main workflow
  coverage for every push branch and PR target. The normal documentation-only
  push exemption remains. Required recovery tests join the existing central and
  operational tests; Windows build, packaging and release depend on their success.

Only tests, workflow and development/runtime documentation changed. The small
existing launcher test change permits exercising a staged package directory and
explicit database URL. Normal schema migrations remain unchanged.

## Test evidence

Executable/test/workflow commit: `77ea58c9bd305cddd1b338f49caf6432177f6ff7`.
[Build #697](https://github.com/petermaisonwilson/DirectBookingSoftware/actions/runs/40489684538),
backend job `122140182291`, passed all required steps. Its service log identifies
**PostgreSQL 16.15**. No engine was emulated and no required test was skipped.

| Required check | SQLite | Genuine PostgreSQL 16.15 |
| --- | --- | --- |
| Migrated-schema central integration and races | 15 passed | 15 passed |
| Full operational acceptance | All 20 scripts passed | All 20 scripts passed |
| HTTP/concurrency/monetary-migration tests inside those scripts | 20 passed | 20 passed |
| Fresh start, native backup/recovery, connection checks and staged startup | 4 passed | 4 passed |

The 20 HTTP/concurrency tests are included in the 20-script operational result,
not an additional independent suite. The recovery drill reuses the existing HTTP
journey to generate its own fixture, rather than introducing a data importer.
Connection termination/pre-ping and missing/unreachable-server assertions are
PostgreSQL-specific; SQLite uses normal file connections and native backup
integrity/failure checks.

## Verified functionality

The operational suites cover Requirements, Setup/pricing, occupancy and add-ons,
customer matching, multi-element enquiries, timed holds, release/reopen/replacement,
confirmation with payment and authorised confirmation without payment, amendments,
availability/calendar, cancellation/refund and shared immutable history. Concurrent
writers and injected audit/history failures exercise double-booking prevention and
atomic rollback. Tenant and support-mode boundaries remain covered. Negative and
non-finite monetary constraints, failed upgrade rollback and retry remain covered
on both engines.

Fresh empty schema acceptance explicitly checks zero companies, users, customers,
enquiries, bookings, canonical reservations, payments, refunds and Setup elements
before fixture generation. The recovery drill compares all table contents,
including Setup, both tenants, financial records and audit/history, with its
pre-backup snapshot. PostgreSQL restore uses the matching client tools in the
PostgreSQL service container, not a SQL approximation. Restarts and subsequent
writes establish that the restored database is usable beyond a readable archive.

## Limits

- GitHub reports this branch is unprotected. Failed tests prevent a successful
  workflow and dependent packaging/release; they do not technically prevent a
  privileged user from manually merging or changing the workflow. Required-check
  branch protection needs GitHub administrator configuration, which the connected
  GitHub tools cannot write. No claim of an immutable merge policy is made.
- Recovery is demonstrated on disposable synthetic databases. Production backup
  scheduling, offsite retention, roles/server configuration, external files,
  full-instance disaster recovery times and a real deployment remain separate.
  PostgreSQL restoration here covers the complete isolated application schema,
  not cluster-wide roles or another installation's database.
- PostgreSQL acceptance and staged-source startup run on Linux CI. The Windows
  package and SQLite regressions are separate gates. Windows-to-remote-PostgreSQL
  networking is not separately rehearsed.
- Existing compatibility/detail tables remain. No physical schema retirement or
  historical conversion is required to resume feature development.
- External card settlement, automatic messaging, every PostgreSQL version and
  arbitrary direct SQL bypassing cooperative inventory locks are not established
  by these tests.

## Reproduction

Install `requirements-online.txt` and run:

```sh
python -m unittest discover -s tests -p reservation_integration_test.py -v
python tests/run_operational_acceptance.py
python tests/database_recovery_test.py
```

Repeat with `DBS_TEST_POSTGRES_URL` pointing to a dedicated PostgreSQL database
ending `_test`; install matching pg_dump/pg_restore clients. CI instead uses its
PostgreSQL service container via `DBS_TEST_POSTGRES_CONTAINER`. Never point these
drills at an existing customer/tenant database. See
[the runtime guide](build302-database-runtime.md) and root `AGENTS.md` for future
upgrade, backup and dual-engine development requirements.
