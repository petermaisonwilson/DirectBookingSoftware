# Direct Booking Software — Online Build 013

Build 013 is the first browser-based foundation. It is **not live on the internet**. It runs on the Windows PC in front of you and stores its test database locally in `online_data`.

## Start it on Windows

1. Extract the complete Build 013 artifact to its own folder.
2. Double-click `START_BUILD013.bat`.
3. On the first run, allow it a minute to create its private Python environment and install the web components.
4. Your normal browser opens to `http://127.0.0.1:8000`.
5. Keep the black starter window open while testing. Closing it stops the local server.

## Test accounts

- Supervisor: `supervisor@directbooking.test` / `Supervisor013!`
- Forest View operator: `operator@forestview.test` / `Operator013!`
- Forest View customer: `customer@forestview.test` / `Customer013!`

These credentials are deliberately local test data only. They are not intended for a live VPS.

## What Build 013 proves

- one browser application with Supervisor, Client/Operator and Customer roles;
- client/company separation;
- Supervisor `View as Client` Support Mode;
- Support Mode changes remain attributed to the Supervisor in the audit trail;
- a permanent append-only audit foundation;
- Global Audit is Supervisor-only and searchable by client and date;
- the future Booking Log permission is Supervisor + Client/Operator only, never Customer.

The Windows Builds 001–012 remain in the repository unchanged as the reference prototype for pricing, Elements, Add-ons and annual setup.

## Storage note

Build 013 deliberately uses SQLite only for safe local development. The online code keeps storage behind its own data layer so we can replace that with the central PostgreSQL database when the WHUK VPS is ready.
