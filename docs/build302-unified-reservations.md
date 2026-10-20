# Build 302 v3 — Unified Reservation Architecture (implementation contract)

Status: design baseline; no destructive migration authorised until target tenant and rollback are independently verified.

## Agreed invariants
- One DBS installation serves independent company tenants. All reservation-related queries, writes, joins, exports and background jobs must enforce company_id on the server and database. No cross-company references.
- Each new company starts with no customers, accommodation elements, prices, reservations or transactions, but receives necessary software-level defaults and working Setup facilities.
- Optional future multi-company booking/search is NOT implemented now. Stable company/reservation/element identifiers and service boundaries must permit an explicitly opted-in, permission-controlled API later.
- One durable reservation identity from initial enquiry through confirmation, amendments and cancellation. Reservation states: ENQUIRY_HELD, ENQUIRY_RELEASED, CONFIRMED, CANCELLED. CANCELLED is terminal for accommodation and price changes; financial settlement, notes and audit remain possible.
- Availability has one authoritative, transactional conflict check for all blocking states and all physical elements; same reservation cannot double-allocate an element on overlapping dates. Released/cancelled never block inventory. Reinstatement requires fresh availability check.
- Reservation elements, party, pricing snapshots, discounts, staff-only manual discounts, payments, refunds, cancellations and audit history all belong to the same reservation. No destructive overwrite of financial transactions.
- Confirmation without payment requires staff authorisation, explicit warning, reason and audit. Payment state is distinct from lifecycle state.
- Outgoing customer messages require individual staff approval; audit recipient, subject, body, staff, timestamp and result.

## Existing dependency audit (Build #593)
- migrations/versions/0002_setup_booking_foundation.py creates legacy enquiries, bookings and booking elements.
- migrations/versions/0005_multi_element_enquiries.py adds enquiry_elements as a compatibility layer.
- online/webv1_enquiries.py and online/webv1_enquiry_builder.py read/write legacy enquiries.
- online/webv1_bookings.py converts an enquiry by inserting a *second* booking record; payment/refund/cancellation tables reference bookings.
- online/webv1_status_availability.py checks enquiries and bookings separately.
- online/webv1_booking_amendments.py and online/webv1_booking_finance.py operate on bookings.
- Existing company_id scoping is present but must be tested for tenant isolation at every boundary.

## Implementation order
1. Inventory all legacy SQL references and cross-tenant routes; write isolated two-company regression fixtures and lifecycle/availability contract tests. No data deletion.
2. Introduce canonical reservations and reservation_elements with explicit tenant-scoped foreign keys/uniqueness and status constraints. Use a migration suitable for both supported DB engines; avoid SQLite-only production assumptions.
3. Introduce shared reservation repository/service with atomic availability and lifecycle transitions, reusable financial calculations, and append-only history. Route enquiry, booking and calendar operations through it, removing duplicated conversion logic.
4. Move payment/refund/cancellation/amendment routes and UI to the canonical reservation ID; preserve existing user-facing behaviour and audit semantics.
5. Remove obsolete tables/paths only after end-to-end regression tests pass and rollback has been verified.
6. Only then request confirmation of exact test company ID, current database backup/restore point and tenant-scoped data reset. Never issue global DELETE/TRUNCATE.

## Efficiency checkpoints
- Before each structural change, verify whether it eliminates duplicated logic rather than adding permanent adapters.
- Keep each commit cohesive and CI-testable. Stop and consult owner before material changes in agreed business rules or deployment strategy.
- Never treat a passing build as proof of live-data safety; require tenant-isolation, lifecycle and rollback tests.
