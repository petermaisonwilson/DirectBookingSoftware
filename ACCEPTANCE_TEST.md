# Direct Booking Software - Build 005 Acceptance Test

Build 005 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The Build 005 source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads `DirectBookingSoftware-Build005` containing the EXE and this acceptance test.
7. On a Windows PC, opening the EXE shows `Direct Booking Software - Build 005`.
8. Setup contains the existing four tabs plus `Person types` and `Occupancy`.
9. Person types are operator-defined: create at least `Adult` with short label `Ad` and `Child` with short label `Ch`.
10. More person types can be added without a fixed hard-coded limit.
11. A person type can be edited and activated/inactivated, and the changes remain after restarting the application.
12. In Occupancy, select an element and set a maximum total-person capacity.
13. For each defined person type, set either a numeric maximum or `No limit`.
14. A person-type maximum of 0 means that type is not allowed on that element.
15. Example test: set a Pitch to maximum 4 total, Adult max 4 and Child max 4; this configuration saves and remains after restart.
16. Example test: set a Fishing Peg to maximum 1 total, Adult max 1 and Child max 0; this configuration saves and remains after restart.
17. The data layer rejects occupancy above an element's overall maximum and above any configured person-type maximum.
18. Existing Build 004 operator settings, seasons, elements, discount rules and pricing-test data remain intact; Build 005 must not require deleting the local database.
19. The Build 004 Pricing Test remains available unchanged for now.

Build 005 intentionally establishes the person/occupancy settings foundation only. Build 006 will replace the Pricing Test's single guest count with quantities for each active person type, apply person-type pricing where relevant, and enforce these occupancy limits during pricing.

Seasonal rate overrides and manual arbitrary +/- adjustments with separate customer-facing and internal notes remain deferred to later pricing/offer builds.
