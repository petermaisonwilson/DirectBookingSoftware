# Direct Booking Software - Build 010 Acceptance Test

Build 010 passes when:

1. GitHub Actions runs on `windows-latest`, source smoke tests pass, PyInstaller creates `DirectBookingSoftware.exe`, and the packaged EXE self-test passes.
2. GitHub uploads `DirectBookingSoftware-Build010` containing the EXE and this acceptance test.
3. On Windows the application title shows `Direct Booking Software - Build 010`.
4. Existing Build 009 annual-grid safety remains intact: required blanks are highlighted and blocked from Save; explicit zeroes remain valid and gently highlighted.
5. Setup contains separate `Elements` and `Add-ons` concepts. Existing fundamental bookable resources remain Elements; extras are created in the new `Add-ons` tab.
6. Add-ons do not become standalone Elements in the existing Element pricing calculator.
7. Add-on pricing methods include `Fixed once`, `Per quantity`, `Per night`, `Per quantity per night`, `Per day`, and `Per quantity per day`.
8. An Add-on inherits the dates/duration of the Element it is attached to; Add-ons do not have independent booking dates in Build 010.
9. Setup contains an annual `Add-on rules` tab showing every active Element/Add-on pair.
10. Every Element/Add-on pair must be explicitly reviewed as `Yes` or `No`. A blank `Available?` cell means unreviewed and is visibly highlighted.
11. An allowed (`Yes`) Add-on requires a minimum quantity, maximum quantity and price for that Element/year.
12. Maximum quantity cannot be lower than minimum quantity; quantities cannot be negative; Add-on prices cannot be negative.
13. A disallowed (`No`) Add-on requires no quantity or price and will not later be offered with that Element.
14. Example behaviour is supported: Dogs may be allowed on a Pitch but not a Gite; Landing Net hire may be allowed on a Fishing Peg but not accommodation.
15. A zero Add-on price is valid, does not create a missing-data warning, and receives a gentle review highlight.
16. `Fixed once` charges once irrespective of Element duration. `Per quantity` multiplies only by quantity. Night/day pricing methods use the parent Element duration, with quantity multiplication where specified.
17. `Copy previous year` copies and verifies Add-on rules alongside seasons, Element rates, Person rates/supplements and occupancy.
18. If Add-on-rule copy verification fails, the newly-created pricing year is removed rather than being left partially configured.
19. `Delete year` removes that year's Add-on rules as well as the established annual setup while retaining other years.
20. A new Element added after a year was copied creates unreviewed Add-on relationships for that year.
21. A new Add-on added after a year was copied creates unreviewed relationships against the active Elements for that year.
22. Existing Elements, Person Types, seasons, discounts, annual pricing and occupancy data remain intact; no database reset is required.
23. Existing dormant Client, booking-party and frozen-pricing snapshot foundations remain intact for later Enquiry/Offer/Booking work.

Build 010 establishes the Setup model only. The later Availability / Offer Builder will use it so an Element search presents only the Add-ons allowed for that Element and inherits the Element's selected dates.

Terminology is deliberate: **Elements** are fundamental bookable resources with their own dates. **Add-ons** are extras attached to an Element and inherit that Element's dates.
