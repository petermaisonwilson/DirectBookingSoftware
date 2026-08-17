# Direct Booking Software — Online Build 014

Build 014 moves the proven Windows Setup model into the browser. It is still **local-only** on your Windows PC and is not exposed to the internet.

## Start it on Windows

1. Extract the complete Build 014 artifact into its own folder.
2. Double-click `START_BUILD014.bat`.
3. Your normal browser opens to `http://127.0.0.1:8000`.
4. Keep the black starter window open while testing.

## Test accounts

- Supervisor: `supervisor@directbooking.test` / `Supervisor013!`
- Forest View operator: `operator@forestview.test` / `Operator013!`
- Forest View customer: `customer@forestview.test` / `Customer013!`

The passwords retain the 013 suffix because these are the same local test accounts created by the accepted online foundation.

## What Build 014 adds

Client/Operator and Supervisor-in-Support-Mode users now have a `Setup` area containing:

- Elements and Element Types;
- Person Types;
- Add-ons and their pricing methods;
- independent pricing years;
- create blank year or Copy previous year;
- seasons and seasonal Element prices;
- annual occupancy with explicit zero accepted;
- Element Type Add-on defaults;
- individual Element Add-on overrides using I / Y / N.

The inherited Add-on priority remains:

**individual Element override → Element Type default → unavailable if no rule exists**.

A copied year carries seasonal rates, occupancy, Type Add-on defaults and individual overrides into the new year.

## Permission rules

- Supervisor can use Setup only while viewing a client in Support Mode.
- Client/Operator can use Setup only for their own client account.
- Customers cannot access Setup.
- Setup changes are written to the permanent audit trail.
- Global Audit remains Supervisor-only.

## Still not in Build 014

There is no booking calendar, enquiry workflow, Client Register, live customer booking or payments yet.

## Storage note

Build 014 still uses SQLite only for safe local development. The online storage remains isolated behind the application data layer so it can later move to PostgreSQL on the managed VPS.
