# Build 302 v3 operational database verification

26 October 2026. Repository: `petermaisonwilson/DirectBookingSoftware`.
Branch: `build-302-payment-rules-fix`. Build/version remain **302 v3**.
No Build 303 was created; PR #21 was not merged.

## Scope and conclusion

This report supersedes the earlier central-service-only report. The existing
operational application now uses source-owned portable SQL for Setup, customers,
enquiries, basket holds, booking conversion, amendments, calendar/availability,
payments, cancellation/refunds and audit history. Both the original operational
acceptance assertions and additional HTTP race/failure assertions execute against
SQLite and a genuine PostgreSQL server.

Verified source commit: `445cfe0d9e21444341ff2c0c9252e85b62205a65`.
Final source verification: [Build #690](https://github.com/petermaisonwilson/DirectBookingSoftware/actions/runs/40351867281).
The report-only commit following it does not change executable source.

Database-engine portability does **not** mean that the architecture contract's
single-reservation-identity cutover has been completed. Existing enquiry and booking
records and their finance links remain in use; the canonical creation/detail UI is
still labelled development/preview. Legacy-to-canonical data conversion, routing all
finance to canonical IDs, and retirement of duplicate conversion paths remain
outstanding. No destructive schema retirement or live-data cutover was attempted.
The agreed architecture contract remains unchanged.

Feature development remains paused under the original central-architecture acceptance
gate. Passing operational PostgreSQL tests alone cannot close the remaining canonical
identity and finance integration items. This is a verified portability milestone,
not a claim that the entire agreed architecture assignment is complete.

## Implementation commits

| Commit | Change |
| --- | --- |
| `b25cd6554fb38d66e3fa5a9a3c27e86081f0a498` | Port operational SQL at source; fix configured PostgreSQL app startup; add operational schema migration 0012, isolated real-backend fixtures and both-engine acceptance runner. |
| `a2ed975c8ded9f352baea34c9cc2b7b8dcadf0bd` | Correct UTC expiry queries, tenant-scoped hold cleanup and locked legacy/canonical inventory rechecks; enable the legacy coexistence integration test on PostgreSQL. |
| `f7f11008a51caba436935e40fed7b6824f814256` | Commit business changes and audit history together; lock conversion before its snapshot; reject stale amendments; add HTTP races/fault injection and genuine launcher-backend checks; package migrations/runtime instructions. |
| `9cac1888cc2525a7c6e4977d675cad23988575d5` | Integrate canonical calendar inventory, remove redundant per-cell inventory reads, verify twelve HTTP scenarios, fix hold-removal ownership, stale lifecycle reads, atomic staff reasons and configured booking currency. |
| `bb43b038ec0cb8bdf444933d7555e6ee8c6e8196` | Correct the canonical inventory result boundary exposed by PostgreSQL calendar tests: return a named mapping rather than a raw SQLAlchemy tuple row. |
| `445cfe0d9e21444341ff2c0c9252e85b62205a65` | Serialize closure creation/edit/delete with booking writers, validate tenant element ownership, protect canonical confirmed inventory and commit closure history atomically; add a thirteenth HTTP race/rollback test. |

The earlier central-service commits and their integrity migrations remain included.
The shared SQL executor binds named parameters and normalises rows/generated IDs;
it does not translate or rewrite SQL. SQLite-only schema operations are restricted
to explicit local-file initialisation branches. PostgreSQL uses Alembic migrations.

## Tests and failure investigation

On the final source commit, the SQLite/PostgreSQL job passed:

| Suite | SQLite | Genuine PostgreSQL 16.15 |
| --- | --- | --- |
| Migrated-schema central reservation integration | 15 passed, no skips | 15 passed, no skips |
| Operational acceptance scripts | All 20 passed | All 20 passed |
| HTTP/concurrency/fault tests within the operational suite | 13 passed | 13 passed |

The final logs identify PostgreSQL **16.15**, not an emulation. The operational app
uses its configured psycopg/SQLAlchemy PostgreSQL engine and isolated migrated
schemas. PostgreSQL and SQLite exercise the same assertions and production routes.

The final Windows regression job also passed, including reservation unit tests,
existing operational regressions, persistent data/launcher checks, server health,
package construction and the Build 302 v3 Test #690 pre-release. Package creation
and automated health are demonstrated; a live production deployment is not.
Both final workflow jobs concluded **success**. The preceding #689 workflow also
completed successfully. No test was disabled to obtain these results.

Local checks additionally passed the 15 reservation unit tests, 13 HTTP tests,
closure/availability foundation regression and `git diff --check`.

The **20 operational acceptance scripts** include existing business-rule assertions
for Setup, customers, enquiry/search/editing, prices, add-ons and person pricing,
hold timing, availability/calendar, the enquiry bridge and complete booking workflow.
The twentieth script contains **13 explicit HTTP/concurrency/failure tests**:

- Closure creation races booking confirmation and other closures safely; foreign-tenant elements are rejected; injected history failures roll back creation, edits and deletion.
- Actual `run_online.py` process honours the configured database; `/health` reports
  the expected backend. PostgreSQL errors do not silently fall back to SQLite.
- Canonical multi-element creation, tenant-scoped detail/history and calendar links.
- Concurrent amendments reject superseded previews rather than overwriting them.
- Concurrent reopening allows only one conflicting enquiry to regain inventory.
- Confirmation without payment rejects an empty reason and saves authorised staff
  reason/history atomically.
- Expired hold cleanup affects only the current tenant.
- Competing HTTP hold, conversion and refund writers preserve occupancy and finance
  limits throughout an enquiry-to-booking-to-amendment-to-cancellation/refund journey.
- Injected history failures roll back booking, payment, amendment, cancellation and
  refund changes, leaving no partially committed financial or inventory updates.
- Hold/enquiry audit failures leave no partial allocation; competing basket saves
  cannot convert the same temporary inventory twice.
- Hold-removal cannot delete another session's or another tenant's party details.
- Multi-element bookings retain separate guest surnames, one payment/balance,
  configured GBP currency and unaffected elements during an amendment.
- Staff calendar inventory agrees with authoritative availability for canonical
  allocations, legacy enquiries with missing workflow definitions, bookings, holds,
  closures and unpriced Setup dates.

The **15 migrated-schema central integration tests per engine** additionally cover
creation, amendment, lifecycle transitions, rollback, tenant foreign keys, immutable
history, populated additive upgrades, adjacency boundaries, legacy coexistence,
closures/holds and independent-connection races. The former SQLite-only coexistence
test now runs on PostgreSQL rather than being skipped. The isolation regression
also verifies the explicit READ COMMITTED override when an engine is configured
with REPEATABLE READ.

Intermediate failures were investigated rather than retried unchanged:

- #685: two remaining production `datetime('now')` predicates and SQLite-specific
  fixture/schema assumptions were exposed by real PostgreSQL.
- #686: two fixture errors (wrong tenant variable and integer comparison against
  the audit text identity) were corrected; 17/19 operational scripts already passed.
- [#687](https://github.com/petermaisonwilson/DirectBookingSoftware/actions/runs/40345370377):
  the expanded 20-script suite and existing Windows checks passed on that commit.
- [#688](https://github.com/petermaisonwilson/DirectBookingSoftware/actions/runs/40350451701):
  the newly added canonical calendar assertions caught a genuine raw SQLAlchemy-row
  mapping defect on PostgreSQL. Corrected at the shared inventory boundary in
  `bb43b03`; the tests and production checks were retained.
- [#689](https://github.com/petermaisonwilson/DirectBookingSoftware/actions/runs/40351257998):
  both database suites and Windows regressions/package checks passed after that fix.
- #690 additionally verifies the final closure concurrency/ownership/history fix;
  all 20 operational scripts and the 13 HTTP cases pass on each database.

## Concurrency and audit guarantees

The existing shared transaction boundary is preserved: SQLite `BEGIN IMMEDIATE`;
PostgreSQL transaction-scoped tenant advisory lock in namespace 302 and READ
COMMITTED isolation. Inventory is rechecked after acquiring the lock. Business
writes, related finance records and their audit events commit or roll back together.
Stale enquiry/amendment state is re-read under that lock. Tenant/session ownership
is checked before child records are deleted.

These are cooperative application-writer guarantees. Arbitrary privileged SQL
that bypasses the boundary is not protected by the advisory lock. No occupancy
exclusion constraint or protection against external writers is claimed.

## Calendar performance evidence

A disposable SQLite benchmark compared the previous baseline `5f3cb82` with
`9cac188`, using the same 30 elements, 60 days and three HTTP calendar renders.
Every render retained 10 booking bars and 1,700 available cells.

| Measurement | Baseline | Updated |
| --- | ---: | ---: |
| Median render seconds | 3.798 | 1.771 |
| SELECT statements per render | 13,904 | 3,706 |

The grouped calendar snapshot now covers canonical and legacy inventory, and
empty cells run the unchanged Setup gates without repeating all inventory queries.
Write/selection paths retain the complete locked availability validator. This is
synthetic local SQLite evidence, not a production or PostgreSQL load-test claim.
The reproducible harness is `tests/calendar_portability_benchmark.py`.

## Remaining limitations

- Canonical identity/finance/UI consolidation described above is unfinished. Both
  schemas coexist; this report does not claim completion of steps 3–5 of the
  unified-reservation implementation contract.
- Tests use disposable databases and synthetic tenants. No live tenant records
  were reset, migrated or deleted, and no production backup/restore or existing
  customer conversion rehearsal has been completed.
- Manual payment/refund recording is tested. Actual external card processing,
  settlement and outgoing message delivery are not established by these tests;
  unconfigured card processing retains its existing rejection behaviour.
- HTTP tests exercise the actual ASGI application and database. Launcher startup
  also uses a real server process. They are not a manual browser usability review,
  exhaustive configuration matrix, production soak test or deployment sign-off.
- PostgreSQL 16 is demonstrated; all PostgreSQL releases, extensions, infrastructure
  configurations and arbitrary external integrations are not claimed compatible.

## Reproduction

Install `requirements-online.txt` from the repository root, then run:

```sh
python -m unittest discover -s tests -p webv1_reservation_core_test.py
python -m unittest discover -s tests -p reservation_integration_test.py -v
python tests/run_operational_acceptance.py
```

For genuine PostgreSQL, set `DBS_TEST_POSTGRES_URL` to a **dedicated disposable**
PostgreSQL database ending in `_test`, then run the integration and operational
commands again. The runner migrates isolated schemas and asserts the backend;
cleanup removes only those test schemas. Never target a customer database.

See `docs/build302-database-runtime.md` for explicit PostgreSQL migration/startup
instructions and the existing unversioned local SQLite-file upgrade boundary.
