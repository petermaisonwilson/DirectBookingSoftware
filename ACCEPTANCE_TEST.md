# Direct Booking Software - Build 006 Acceptance Test

Build 006 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The Build 006 source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads `DirectBookingSoftware-Build006` containing the EXE and this acceptance test.
7. On a Windows PC, opening the EXE shows `Direct Booking Software - Build 006`.
8. Setup retains the Build 005 tabs and adds `Person pricing`.
9. Person pricing allows an element-specific rate for every configured person type.
10. Leaving a person rate as `Use base price` makes that person type use the element's existing Base Price.
11. The Pricing Test no longer uses one generic guest count; it shows a quantity for every active person type.
12. At least one person must be entered before a price can be calculated.
13. Pricing rejects a mix of people that exceeds the selected element's overall maximum capacity.
14. Pricing rejects a mix that exceeds a configured maximum for an individual person type, including a zero-not-allowed limit.
15. Per night, Per day, Per stay and Per package continue to calculate from the element price while still validating the actual people occupying the element.
16. Per person calculates the sum of each person type quantity multiplied by that type's element rate (or Base Price fallback).
17. Per person per night calculates each person-type subtotal using quantity × nights × that type's element rate, then sums the subtotals.
18. The Pricing Test displays the selected people mix as well as the full base calculation.
19. Duration discounts are applied after the complete person-aware base calculation.
20. Person-type rates remain after closing and reopening the application.
21. Existing Build 005 person types, occupancy limits, operator settings, seasons, elements and discount rules remain intact; Build 006 must not require deleting the local database.

Example: if a Per person per night element has Adult €20, Child €10, 2 Adults + 2 Children for 3 nights gives a base price of €180 before any qualifying duration discount.

Seasonal rate overrides and manual arbitrary +/- adjustments with separate customer-facing and internal notes remain deferred to later pricing/offer builds.
