# Direct Booking Software - Build 004 Acceptance Test

Build 004 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The Build 004 source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads `DirectBookingSoftware-Build004` containing the EXE and this acceptance test.
7. On a Windows PC, opening the EXE shows `Direct Booking Software - Build 004`.
8. The Dashboard has an `Open Pricing Test` button.
9. The Pricing Test allows selection of an active element, arrival date, departure date and guest count without creating an enquiry, offer or booking.
10. Nights are calculated as departure minus arrival.
11. Per-day charging uses nights + 1, so both arrival and departure dates are chargeable days.
12. `Per night` calculates base price × nights.
13. `Per day` calculates base price × chargeable days.
14. `Per stay` charges the configured base price once.
15. `Per person` calculates base price × guests.
16. `Per person per night` calculates base price × guests × nights.
17. `Per package` charges the configured base price once.
18. A qualifying duration discount is applied automatically after the base calculation.
19. If several duration discounts qualify, only the single rule giving the largest customer discount is applied.
20. The Pricing Test clearly displays nights/days/guests, pricing type, base calculation, base amount, discount rule, discount amount and final price.
21. Departure before arrival is rejected rather than producing a price.
22. Existing Build 003 operator settings, seasons, elements and discount rules remain intact; Build 004 does not require deleting the local database.

Seasonal rate overrides are not introduced in Build 004 because the current data model has seasons but does not yet have element-by-season rate records. Manual arbitrary +/- price adjustments with separate customer-facing and internal notes remain intentionally deferred until the offer/pricing workflow stage.
