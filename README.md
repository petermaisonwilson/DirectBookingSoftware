# Direct Booking Software

Windows desktop booking-handling software for small accommodation and activity operators.

## Build 001

Build 001 establishes the desktop application foundation:

- Python 3.11
- PySide6 / Qt desktop UI
- SQLite development database
- Main navigation for Dashboard, Enquiries, Availability, Bookings, Finance and Setup
- Database schema for companies, users, elements, seasons, enquiries, offers, bookings, transactions and audit log
- GitHub Actions Windows build
- PyInstaller packaging to `DirectBookingSoftware.exe`

This build is intentionally a foundation build. Booking rules and the full workflows will be migrated into the desktop application in later builds.

## Development architecture

The local SQLite database is a development-stage storage layer. Database access is isolated so that the long-term product can move to a central secure API and PostgreSQL without replacing the UI.
