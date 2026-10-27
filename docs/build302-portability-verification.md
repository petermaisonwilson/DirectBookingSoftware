# Build 302 v3 — operational reservation transition verification

27 October 2026. Repository: `petermaisonwilson/DirectBookingSoftware`.
Branch: `build-302-payment-rules-fix`. Build/version remain **302 v3**.
No Build 303 was created. PR #21 remains open and unmerged.

## Result

Verified executable source: `aa8899d95fec4708585455899164c72547f0e4f3`.
Final workflow: [Build #696](https://github.com/petermaisonwilson/DirectBookingSoftware/actions/runs/40486194351).
Both final workflow jobs concluded **success**.
The report-only commit following it changes documentation, not executable source.

The operational workflow now carries **one canonical reservation ID** from the
initial enquiry through confirmation, amendment, cancellation and financial
settlement. A booking confirmation attaches its retained detail record to that
same master; it does not create another canonical reservation. Element allocation
IDs also survive confirmation and date amendments. Operational forms and calendar
links use `/operations/reservations/{id}` throughout those phases.

Payment, refund, cancellation and amendment records carry `reservation_id`, with
tenant foreign keys. Financial summaries read the canonical master and its linked
ledger. The readable booking history includes events from the enquiry phase,
through the shared append-only reservation history and its unique source-audit link.
Business changes, compatibility/detail writes, ledger and history commit together.

The separate development-only creation form is retired. Its entry forwards to the
existing Booking Requirements workflow, so new UI records cannot bypass the
complete requirements/confirmation/finance path. Stale form submissions create
nothing; retained standalone summaries remain readable.

**Physical storage boundary:** existing enquiry/booking detail tables, their
internal IDs and compatibility URLs are retained. Operational writes update those
details and the canonical aggregate in the same transaction. Party requirements,
original frozen prices and historical links are preserved there. This is an
operational identity/finance integration, not a claim that every legacy table or
column has been removed. No destructive schema retirement, tenant reset, live
customer conversion or production deployment was performed.

**Feature development can resume on this verified operational baseline**, using
the canonical reservation commands and retaining the SQLite/PostgreSQL CI gate.
The identity/finance/history integration previously outstanding at Build #690 is
now implemented and exercised end-to-end. This is not approval for a production
data cutover or removal of compatibility storage. Site-specific invalid or
incomplete historic records still require review rather than inferred corrections.

## Where the interruption stopped, and commits

GitHub inspection established `9cb44e7` / green Build #692 as the latest verified
commit. The workspace still held the uncommitted final consistency and monetary
work. That work was recovered, reviewed and tested; the earlier completed
portability implementation was not repeated.

| Commit | Result |
| --- | --- |
| `b8f0c1be411f669713ed6253917b135fc0ed70df` | Add operational identity links and tenant constraints; retain master/allocation IDs through confirmation; link finance and history; provide additive, idempotent adoption of existing records. Verified by Build #691. |
| `9cb44e7cd832478d2f86f440ec45f32669a886a9` | Use stable reservation URLs in forms, calendar, reports and customer links; preserve existing controller validation, currency and duration presentation; show shared history; use canonical element IDs for replacement. Verified by Build #692. |
| `81ae097bc39ce800073901920f81fdf0cf85ff0d` | Share canonical header/allocation insertion and operational confirmation; require staff reason in the no-payment domain command; prevent raw lifecycle/price edits bypassing operational finance; fix support-mode amendment tenant selection; enforce finite monetary values in services and migration 0014. Verified by Build #693. |
| `99cda7126f73a4fd93a6f364789856e2bd5ea24b` | Make SQLite Alembic DDL and versioning transactional; prove failed monetary migration preserves schema/data/history and can be retried. Verified by Build #694. |
| `33be6279ea85393e033b95cceb04e085b0fc3e1d` | Preserve configured non-blocking status behaviour in canonical inventory and closure checks; keep lifecycle separate from availability configuration; reconcile the earlier incorrect status mapping idempotently; reject converted enquiries with missing booking owners. Verified by Build #695. |
| `aa8899d95fec4708585455899164c72547f0e4f3` | Retire the incomplete standalone development creation form; forward its GET entry to the existing complete booking flow, reject stale submissions without creating partial records, retain standalone summaries and canonical compatibility redirects. Verified by Build #696. |

These commits build on the already verified operational SQL port in `b25cd65`,
UTC expiry/tenant locking correction `a2ed97`, atomic history/launcher work
`f7f1100`, calendar/currency/ownership work `9cac188`, row-mapping fix `bb43b03`
and closure concurrency fix `445cfe0` (Build #690).

No runtime SQL rewriting or SQLite emulation is used. SQL is owned by its source
module, with named binds and normalised result/generated-ID handling. Engine
specific migration code implements equivalent database constraints.

## Final demonstrated tests

| Suite | SQLite | Genuine PostgreSQL 16.15 |
| --- | --- | --- |
| Migrated-schema central integration | 15 passed, no skips | 15 passed, no skips |
| Complete operational acceptance scripts | All 20 passed | All 20 passed |
| HTTP/concurrency/migration/fault tests within the operational runner | 20 passed | 20 passed |

The final service log explicitly identifies PostgreSQL **16.15**. Migrations and
application statements execute through psycopg against that server. These are
actual database results, not an inference from a green SQLite job.

The final Windows job also passed its existing regressions, 15 reservation unit
tests, persistent-data and launcher/occupied-port checks, local server health,
package construction and Build 302 v3 Test #696 pre-release publication.
The database acceptance job runs SQLite and PostgreSQL on Linux CI; the Windows
job exercises local SQLite and packaging. Windows-to-remote-PostgreSQL networking
has not been separately rehearsed.

[Build #696 test package](https://github.com/petermaisonwilson/DirectBookingSoftware/releases/tag/dbs-build-302-test-696).
Both final jobs passed. No test was disabled to obtain the result.

The operational runner executes **20 scripts per engine**. One script contains
**20 explicit HTTP, concurrency, migration and fault-injection tests**; those tests
are included within the 20-script result, not an additional independent suite.
The other scripts retain the existing assertions for Setup, customer matching,
pricing, person/selected-day add-ons, occupancy, enquiries, basket holds, calendar
and booking behaviour. Navigation assertions were updated for canonical URLs;
business-rule assertions and rollback fault injection were retained.

The PostgreSQL fixtures assert the genuine backend and migrate isolated schemas
in a dedicated database ending `_test`. The launcher test starts an actual
`run_online.py` process and checks its reported backend, Build 302 and v3. A
PostgreSQL failure cannot silently select SQLite.

## What the end-to-end evidence establishes

- Requirements, independently named guests, temporary holds and customer matching
  lead to a multi-element enquiry. Confirmation retains the reservation identity
  and creates one account balance, with configured currency and frozen pricing.
- Both payment confirmation and authorised confirmation without payment work.
  Blank authorisation reasons are rejected at HTTP and domain boundaries. The
  staff record and reason commit with confirmation; payment labels remain distinct
  from lifecycle state. Existing deposit/threshold/balance-due rules are retained.
- A test deliberately makes canonical IDs differ from enquiry/booking IDs, then
  exercises the same URL through edit, release, reopen and confirmation. This
  prevents coincidentally equal IDs from hiding a resolver error. Form actions,
  HTML responses, CSRF and cross-tenant rejection are checked.
- Release/reopen recheck inventory, including partial conflict and replacement.
  Canonical element IDs resolve only within their own tenant and reservation.
  Calendar links and inventory agree with operational availability for canonical
  and retained detail records, closures, timed holds and incomplete Setup.
- Configured non-blocking enquiry and booking statuses retain their original
  effect, including closure checks and central creation. Changing a status label
  or availability configuration does not fabricate a release lifecycle. Repeated
  correction of the earlier mapping produces no duplicate reconciliation history.
- Amendments retain allocation identity, other elements and payment records;
  recalculate through the existing pricing rules; update the master balance; and
  reject a stale preview. Supervisor amendments use the selected support tenant,
  record that staff identity and reject access after switching tenants.
- Cancellation remains terminal for accommodation changes. Approved refunds and
  other financial settlement remain accessible through the same reservation URL.
  Refund races cannot exceed the approved amount.
- Independent writers race for holds, basket conversion, confirmation, reopening,
  amendments, refunds and closure/booking combinations. Central integration also
  races creation, amendment, reinstatement and mixed operations. Conflicting
  writers recheck after locking; successful writes do not double-allocate inventory
  in those tested paths.
- Injected failures in both the original audit write and the canonical history
  append roll back source details, canonical state/allocations, payments and
  other finance together. Database triggers preserve append-only history.
- Existing records are adopted without changing stored prices, receipts, guest
  names or source IDs. Repeated startup/import retains the same identities and
  does not duplicate history. Ambiguous source relationships, missing booking
  owners and cross-tenant financial ownership are rejected without reassignment
  or partial import.
- Negative amounts and non-finite values (`NaN`, positive/negative infinity) are
  rejected before operational writes; direct database checks also reject invalid
  ledger values on both engines. An invalid historical amount causes migration
  failure without overwriting it. The regression checks unchanged schema version,
  original records, immutable history, absence of leftover rebuild tables and a
  successful retry after explicit correction of a disposable fixture.

## Failures investigated

The recovered SQLite regression initially targeted `_write_booking`, which had
moved into the shared confirmation service. Its fault-injection target was updated;
the forced rollback assertion remains in place.

The new migration-failure test exposed an actual SQLite driver/Alembic issue:
without an explicit transaction before DDL, an empty rebuild table survived a
failed migration. The migration environment now begins an immediate SQLite
transaction before schema work and treats DDL transactionally. PostgreSQL retains
its transactional DDL path. Build #694 verifies the correction on both engines.

The final source audit found that the canonical check ignored existing configured
non-blocking statuses, and that the first identity mapper misclassified those
enquiries as released. The common availability predicate now honours the retained
configuration, while lifecycle follows the operational record. No new status or
pricing policy was introduced.

## Limits and remaining deployment work

- Earlier standalone prototype records without operational requirements/finance
  links remain read-only. No missing business facts are invented. Converting or
  clearing such site-specific records requires reviewed data and the agreed
  tenant/backup scope; the normal creation UI no longer produces them.
- Existing detail storage and old URL compatibility remain as described above.
  A canonical-only physical schema and destructive removal of historical tables
  have not been performed or claimed. Removing compatibility storage requires a
  separate reviewed migration and verified customer backup/rollback point.
- Evidence uses disposable databases and synthetic tenants, including populated
  upgrades and injected failures. It is not a production backup/restore rehearsal,
  exhaustive client-data conversion or deployment sign-off. Invalid/ambiguous
  historic data requires review; migration never guesses an owner or repairs a
  monetary value silently.
- Inventory locks are cooperative application transactions: SQLite
  `BEGIN IMMEDIATE`, PostgreSQL tenant advisory locks in namespace 302 with
  READ COMMITTED isolation. Arbitrary privileged SQL or writers bypassing that
  boundary are outside the tested guarantee; no exclusion constraint is claimed.
- Manual payment/refund recording is verified. Actual external card settlement,
  automatic messages and external provider integration are not established by
  these tests. Unconfigured card processing retains its existing rejection.
- PostgreSQL 16 is demonstrated. All PostgreSQL releases, deployment topologies,
  production load and a manual browser usability review are not claimed tested.
  No pending calendar or other product feature was added as part of this work.

## Reproduction

From the repository root, install `requirements-online.txt`, then run:

```sh
python -m unittest discover -s tests -p webv1_reservation_core_test.py
python -m unittest discover -s tests -p reservation_integration_test.py -v
python tests/run_operational_acceptance.py
```

Set `DBS_TEST_POSTGRES_URL` to a dedicated disposable PostgreSQL database whose
name ends in `_test`, and repeat the integration and operational commands. The
fixtures create and remove only their controlled test schemas; never target a
customer database. See `docs/build302-database-runtime.md` for startup, explicit
PostgreSQL migrations and the unversioned local SQLite boundary.
