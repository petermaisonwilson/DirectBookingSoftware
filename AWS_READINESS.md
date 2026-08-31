# Direct Booking Software — AWS Readiness / Database Portability

## Purpose

This milestone prepares the existing DBS Web V1 application for a future Debian/AWS deployment without changing the accepted booking behaviour.

The production target is:

- Route 53 for DNS
- HTTPS at Nginx / AWS edge
- Debian-based EC2 application host
- Python 3.11+ / FastAPI / Uvicorn
- PostgreSQL for the production database (Amazon RDS when commercially justified)
- SQLite retained for simple local Windows development and demonstrations

## Non-negotiable architecture rule

From this milestone onward, new DBS features must not create or alter database tables inside route/page modules.

Schema ownership belongs to SQLAlchemy metadata and numbered Alembic migrations. Application code may query and update data, but schema changes are explicit migrations.

No SQL compatibility translator, SQL interception layer, monkey modification, or hidden database-specific workaround is to be introduced. Existing SQLite-specific source is converted at its proper owner.

## Runtime environments

`DIRECTBOOKING_ENV` is one of:

- `development`
- `test`
- `production`

`DIRECTBOOKING_DATABASE_URL` is the canonical database setting.

Examples:

- Local SQLite: `sqlite:///online_data/direct_booking_online_dev.db`
- PostgreSQL: `postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE`

The legacy `DIRECTBOOKING_DB` path remains accepted while local tests are being migrated.

Production must never seed or display demo users. Production cookies are HTTPS-only.

## Migration order

1. Runtime environment and database URL configuration.
2. SQLAlchemy engine and portable core schema.
3. Alembic migration framework and core migration.
4. Linux + PostgreSQL automated verification.
5. Move existing Web V1 lifecycle schema out of `webv1_core.py`.
6. Move Setup schemas out of `setup014_core.py` / `setup015_core.py` and related setup modules.
7. Move Availability / holds schema out of `webv1_availability.py`.
8. Move Booking Requirements and Booking Status schema changes/triggers into migrations.
9. Convert raw SQLite-only query constructs (`PRAGMA`, `INSERT OR IGNORE`, `INSERT OR REPLACE`, `COLLATE NOCASE`, SQLite date functions and positional `?` parameters) at their source owners.
10. Run the complete accepted #276 behaviour suite on Windows/SQLite and Linux/PostgreSQL before the milestone is accepted.

## Existing SQLite-specific areas found during the initial audit

At minimum the current source contains SQLite-specific schema/query behaviour in:

- `online/database.py`
- `online/setup014_core.py`
- `online/setup015_core.py`
- `online/webv1_core.py`
- `online/webv1_availability.py`
- `online/webv1_booking_requirements.py`
- `online/webv1_booking_status.py`

Other Web V1 modules are to be checked as they are brought into the migration set.

## Build rule

The AWS readiness workflow is manual-only while this milestone is being prepared. No numbered Direct Booking build is to be submitted until the user explicitly approves the build.
