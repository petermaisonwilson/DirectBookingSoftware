# Build 302 v3 reservation portability verification

26 October 2026. Repository: `petermaisonwilson/DirectBookingSoftware`.
Branch: `build-302-payment-rules-fix`. Build number and version remain 302 v3.
PR #21 has not been merged or changed.

## Result and scope

The central reservation service now executes creation, multi-element amendment,
lifecycle changes, history writes, summaries, listings and transactional inventory
checks on native SQLite and genuine PostgreSQL 16. The existing SQLite workflow
regressions also pass. This establishes portability of this service, not of every
existing application route.

## Commits

- `89b7f62483869db3bc4a633138ee90539922c6e7`: remove remaining positional SQLite
  statements from central element inserts, amendments and history; use explicit
  named SQL and correct SQLAlchemy row mappings; make legacy booking/enquiry
  conflict queries portable; introduce migrated-schema SQLite/PostgreSQL CI.
- `3a5e18cb3eb020991f658a6d6285ffc97d77d851`: validate customer ownership at the
  service boundary; enforce tenant customer/element references with composite
  foreign keys; make history append-only with engine-specific database triggers;
  check closures and unexpired basket holds within the reservation transaction;
  expand integration coverage to thirteen tests and preserve populated records
  while upgrading the integrity migrations.
- The commit containing this report additionally rejects malformed/noncanonical
  date input before engine-specific date queries and requires the portability job
  to pass before the Windows test/package/release job can run.

All changes retain the existing lifecycle states, transition rules, totals and
pricing snapshots. No payment-rule changes, tenant reset, live-data migration,
Build 303 or PR #21 merge was performed.

## Demonstrated test results

[Build #680](https://github.com/petermaisonwilson/DirectBookingSoftware/actions/runs/40337024829)
passed its existing Windows regression job and eight integration tests on each
engine. This was the first genuine PostgreSQL execution of the newly completed
central write path in this work.

[Build #681](https://github.com/petermaisonwilson/DirectBookingSoftware/actions/runs/40337582822)
passed its Windows regression job and thirteen integration tests on each engine.
The PostgreSQL service log identifies PostgreSQL 16.15; migrations and queries
executed against that server using psycopg, without mocks or SQLite emulation.
The thirteen-test suite covers:

- Multi-element creation and amendment, totals and retained lead/pricing data.
- Release, reinstatement, confirmation and terminal cancellation; inventory
  blocking follows lifecycle state.
- Tenant-scoped summaries/lists, customer/element ownership, and database rejection
  of cross-tenant references.
- Atomic rollback of record, elements, total, state and history when history fails.
- Append-only history: direct UPDATE and DELETE are rejected by the database.
- Overlap rejection, adjacent departure/arrival boundaries, and unsuccessful
  reinstatement/amendment leaving existing data intact.
- Existing legacy bookings, multi-element enquiries and single-element enquiries
  remain visible to the canonical conflict checks.
- Closures and live basket holds block central writes; expired holds do not block.
- Separate-connection concurrent creation, amendment, reinstatement, and mixed
  creation/reinstatement: exactly one conflicting writer succeeds.
- Populated reservations, allocations, customer links and history survive the
  additive integrity migrations from revision 0009 to the latest schema.

The fifteen existing reservation unit regressions passed locally and in the
Windows job. Local booking workflow, calendar, availability/enquiry bridge and
booking edit/hold-warning regressions also passed. The full Windows workflow
additionally checks Setup, customers, enquiry editing/search, add-ons, hold timing,
persistent data, launcher behaviour, imports and server health.

The report commit reruns these automated checks, including the final malformed-date
cases. A follow-up isolation regression brings the integration suite to fourteen
tests per engine and verifies the READ COMMITTED override described below. Its workflow result must be green before treating that commit as accepted.

## Concurrency mechanism and its boundary

The existing transaction design is retained: SQLite uses `BEGIN IMMEDIATE`;
PostgreSQL uses a tenant-scoped transaction advisory lock in namespace 302.
Validation, writes and history all happen before that transaction commits. PostgreSQL reservation transactions explicitly
use READ COMMITTED, including when the engine default is REPEATABLE READ, so the
lock acquisition cannot retain a stale pre-wait inventory snapshot. The dedicated
isolation regression also races writers under that non-default engine setting. A
waiting writer rechecks committed inventory and is rejected on conflict.

This guarantee applies to writers using `reservation_write_transaction` and the
central service. PostgreSQL advisory locks are cooperative: arbitrary external SQL
or a future writer bypassing the boundary is not protected by that lock. The
legacy PostgreSQL operational write paths are not claimed to be migrated. No
reservation occupancy exclusion constraint has been added or claimed.

## Remaining limitations and feature-development decision

The unified service and the existing operational application are still separate:

- `online/webv1_reservation_preview.py` remains a development preview.
- `online/webv1_master_enquiry.py` still uses SQLite-only Setup/customer/pricing
  dependencies. Its HTTP route is not an end-to-end PostgreSQL proof.
- `availability_state()` and legacy calendar/Setup readiness access still use
  native SQLite connections. Portable central inventory guards are demonstrated;
  the complete PostgreSQL availability UI is not.
- Legacy booking amendment/payment/refund/finance routes have not been moved onto
  the canonical reservation identity. Payment-aware confirmation authorisation
  and basket-hold handoff are not established by these service integration tests.
- New migrations were tested on disposable databases, including populated upgrade
  fixtures. No production backup/restore rehearsal or existing-client conversion
  has been performed. The tenant-reference migration refuses incompatible data
  rather than deleting or reassigning it.
- The established download package is not a demonstrated PostgreSQL deployment
  package. Green SQLite UI tests do not change that limitation.

Feature work that uses the verified central service can build on this tested
baseline. If the requirement is to finish the entire operational SQLite/PostgreSQL
transition before adding features, feature development should remain paused until
the remaining route/finance integration and end-to-end PostgreSQL tests pass.
Full PostgreSQL application compatibility and production deployment readiness are
not claimed.

## Reproduction

From the repository root, install `requirements-online.txt` and run:

```sh
python -m unittest discover -s tests -p webv1_reservation_core_test.py
python -m unittest discover -s tests -p reservation_integration_test.py -v
```

For the same integration suite on PostgreSQL, set `DBS_TEST_POSTGRES_URL` to a
**dedicated disposable** PostgreSQL database whose name ends in `_test`, then run
the second command. The suite applies migrations and inserts test tenants; it must
never target a customer database. The branch workflow provisions PostgreSQL 16
and runs both variants automatically.
