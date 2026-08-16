# Direct Booking Software - Build 007 Acceptance Test

Build 007 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The Build 007 source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads `DirectBookingSoftware-Build007` containing the EXE and this acceptance test.
7. On a Windows PC, opening the EXE shows `Direct Booking Software - Build 007`.
8. Setup retains Person types, Occupancy and Person pricing from Build 006.
9. For `Per person` and `Per person per night` elements, configured Person pricing values remain the actual person-type rates; an unset type falls back to the element Base Price.
10. For `Per night`, `Per day`, `Per stay` and `Per package` elements, configured Person pricing values are supplements added on top of the element base charge.
11. An unset person value on those fixed/base-priced element types adds no supplement.
12. Per-night supplements use person quantity × nights × supplement rate.
13. Per-day supplements use person quantity × chargeable days × supplement rate.
14. Per-stay and Per-package supplements use person quantity × supplement rate once.
15. Occupancy validation is performed before the price is accepted.
16. Example: Pitch 1 at €20 per night for 7 nights = €140 element base. With 2 Adults at €5 per night and 2 Children at €3 per night, person charges = €112 and combined price before discount = €252.
17. The Pricing Test separately displays Element base charge, Person charges, Combined before discount, Discount and Final price.
18. Duration discounts apply to the combined element base + person charges amount.
19. Existing Build 006 person rates/supplements remain stored and are not deleted during upgrade.
20. Existing person types, occupancy limits, operator settings, seasons, elements and discount rules remain intact; Build 007 must not require deleting the local database.

Adults, Children or other person types remain occupants of an Element and must not be created as separate bookable Elements merely to price them.

Seasonal rate overrides, enquiry/offer/booking workflow and manual arbitrary +/- adjustments with separate customer-facing and internal notes remain deferred to later builds.
