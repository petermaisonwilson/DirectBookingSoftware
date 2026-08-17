# Direct Booking Software — Online Build 013 Acceptance Test

Build 013 is the first online/browser architecture build. Windows Build 012 remains frozen as the reference desktop prototype.

Build 013 passes when:

1. GitHub Actions installs the online dependencies, imports the application and passes `tests/online_smoke_test.py`.
2. CI starts the local web server and receives an OK response from `/health` reporting Build 013.
3. GitHub uploads `DirectBookingSoftware-Online-Build013` containing the online application, starter script, requirements and instructions.
4. On a Windows PC, extracting the artifact and double-clicking `START_BUILD013.bat` starts the local server and opens the application in a normal browser.
5. The application clearly identifies itself as Online Build 013 and runs at `127.0.0.1`, so it is local-only rather than exposed to the internet.
6. Three permission roles exist from the beginning: Supervisor, Client/Operator and Customer.
7. A Supervisor dashboard lists client companies and can enter `View as Client` / Support Mode for a selected client.
8. Support Mode is visibly obvious on screen and identifies the client being viewed.
9. A Supervisor action made in Support Mode remains attributed to the Supervisor in the audit trail and records the client in which the Supervisor was acting.
10. A Client/Operator is tied to one company and cannot see another company's dashboard/data.
11. A Customer receives a separate restricted area and cannot access client settings or Supervisor audit screens.
12. The audit foundation records meaningful actions including successful login, failed login, Support Mode entry/exit and client-setting changes.
13. Audit change records retain before and after values.
14. Audit rows are append-only: normal database UPDATE or DELETE attempts are rejected.
15. Global Audit is available only to Supervisor users and is searchable by Client and date/date range.
16. Client/Operator and Customer users receive a forbidden response if they attempt to open Global Audit.
17. Future Booking Log permissions are encoded now: Supervisor and Client/Operator may view it; Customer may not.
18. Build 013 uses a separate local development database and does not alter or reset the Windows Build 012 database.
19. Windows Builds 001–012 remain in the repository as the reference prototype; Build 013 does not rewrite their pricing/Add-on logic.
20. The online storage layer is isolated so local SQLite can later be replaced with the central PostgreSQL database on the managed VPS.

Build 013 deliberately stops before transferring the full Elements/Add-ons/annual-pricing Setup and before building the Client Register, availability calendar, enquiries, bookings or payments.
