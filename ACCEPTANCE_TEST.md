# Direct Booking Software - Build 008 Acceptance Test

Build 008 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The Build 008 source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads `DirectBookingSoftware-Build008` containing the EXE and this acceptance test.
7. On a Windows PC, opening the EXE shows `Direct Booking Software - Build 008`.
8. Setup contains `Person types` and a new `Annual grids` tab; the old one-element-at-a-time Occupancy and Person pricing tabs are no longer the year-specific editing route.
9. Annual grids contains three grids: `Seasonal element rates`, `Person rates / supplements`, and `Occupancy`.
10. Each grid displays all active Elements at once so missing or inconsistent setup can be seen without selecting Elements one at a time.
11. Person rate / supplement cells use normal directly editable numeric text. `0.00` explicitly means no supplement on a base-priced Element.
12. Seasonal element pricing shows one column for each active season covering the selected year.
13. A blank required cell remains genuinely missing; Build 008 must not silently replace missing annual data with a guessed value.
14. Each annual grid displays a missing-data/completeness warning, plus an overall annual setup warning.
15. The first Build 008 run migrates the current Build 007 pricing/occupancy data into the current year without deleting the legacy data.
16. `New blank year` creates an independent year with blank required grid cells (and an initial all-year season where no season exists).
17. `Copy previous year` copies the previous year's season structure, seasonal element rates, person rates/supplements and occupancy limits into the new year.
18. The copy is transactional and verified: the copied seasonal price values, person rates/supplements, occupancy totals and person limits must match the source records before the new year is committed. A failed verification rolls the new year back rather than leaving a misleading partial copy.
19. Cross-year season dates are shifted by their actual year difference rather than forcing both season endpoints into the same calendar year.
20. Annual grids includes a `Delete year` button with confirmation.
21. Deleting a year removes that year's annual rates, person rates/supplements and occupancy records without deleting Elements, Person Types or unrelated setup data.
22. The only remaining pricing year cannot be deleted, preventing an accidental empty annual configuration.
23. A pricing year referenced by a frozen historic booking-pricing snapshot cannot be deleted.
24. If a new Element was added after the source year, it appears in the copied target year's grids with missing cells and the completeness warning identifies the omission.
25. If a new Person Type was added after the source year, its new person-pricing and occupancy cells appear missing until reviewed.
26. Previous pricing years remain selectable and their stored annual data is retained rather than overwritten by later years.
27. Pricing Test uses annual data for the stay dates, including the correct seasonal Element rate for each chargeable night/day.
28. A stay that crosses seasonal boundaries is calculated from the appropriate seasonal rates rather than one rate for the entire stay.
29. A stay that reaches a year with no configured pricing year is rejected with a clear error.
30. Pricing is rejected if a required annual seasonal rate, person rate/supplement, or occupancy record is missing.
31. Occupancy limits remain enforced before pricing is accepted.
32. Duration discounts continue to apply after seasonal Element charges and person charges/supplements have been combined.
33. Existing operator settings, Elements, Person Types, Seasons, discount rules and Build 007 data remain intact; no local database deletion is required.
34. Dormant database foundations exist for permanent Clients, linking bookings to Clients, booking-party snapshots and frozen booking-pricing snapshots. Build 008 does not yet expose Client Register or booking-copy UI.

Historical booking principle for later builds: once a booking is created, its actual commercial calculation will be stored as a frozen booking snapshot and will not silently recalculate when annual pricing grids are changed. Deliberate booking amendments will be recorded as amendments.

Future requirement retained but intentionally not implemented in Build 008: the Client Register will show each client's booking history, and an old booking can later be used as a template for a new booking by copying reusable customer/party/resource information but not dates or historic prices.

Manual arbitrary +/- adjustments with separate customer-facing and internal notes remain deferred to the real offer/booking pricing workflow.
