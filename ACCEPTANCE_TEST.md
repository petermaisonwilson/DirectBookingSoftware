# Direct Booking Software - Build 011 Acceptance Test

Build 011 passes when:

1. GitHub Actions runs on `windows-latest`, the source smoke test passes, PyInstaller creates `DirectBookingSoftware.exe`, and the packaged EXE self-test passes.
2. GitHub uploads `DirectBookingSoftware-Build011` containing the EXE and this acceptance test.
3. On Windows the application title shows `Direct Booking Software - Build 011`.
4. Existing Build 009 annual-grid safety and Build 010 Add-on catalogue/pricing methods remain intact.
5. `Add-on rules` now contains two sections: `Element Type defaults` and `Element overrides`.
6. Element Type is based on the existing Element Group/Type field (for example Camping, Gites, Fishing).
7. Normal Add-on availability, minimum quantity, maximum quantity and price are configured once per Element Type/year rather than once for every individual Element.
8. Every active Element Type/Add-on default pair must be explicitly reviewed as Yes or No. A blank default is highlighted and blocks saving.
9. A Yes default requires Min, Max and Price; a zero price is valid and gently highlighted; a No default requires no Min/Max/Price.
10. Every Element automatically inherits the Add-on rule for its Element Type when no individual override exists.
11. `Element overrides` defaults to `Inherit`; Inherit stores no individual rule and therefore uses the Element Type default.
12. An individual Element override of Yes or No takes priority over the Element Type default.
13. A Yes override can replace the inherited Min, Max and Price for that specific Element.
14. Returning an override to Inherit deletes the individual exception and immediately restores the Element Type rule.
15. Existing Build 010 individual Element/Add-on rules are retained and appear as individual overrides; Build 011 performs no destructive database reset.
16. Example: Camping → Dog Yes, 0–2, €3/night means all Camping Elements inherit that rule; Pitch 7 → Dog No overrides it only for Pitch 7.
17. Example: Gites → Dog No means Gite Elements do not offer Dogs unless a specific Gite is deliberately overridden.
18. A new Element added to an already-configured Element Type automatically inherits that Type's existing Add-on defaults; it does not create a full new matrix of Add-on setup work.
19. A new Add-on creates one unreviewed default for each active Element Type, rather than one unreviewed row for every individual Element.
20. If no Element override and no Element Type default exists, the Add-on is treated as unavailable/unconfigured rather than guessed.
21. `Copy previous year` copies and verifies both Element Type Add-on defaults and individual Element overrides alongside the existing annual setup.
22. If Add-on copy verification fails, the newly-created year is rolled back rather than left partially configured.
23. `Delete year` removes both Type defaults and Element overrides for that year while preserving other years and catalogue records.
24. Add-ons still inherit the parent Element's dates. Anything requiring independent start/end dates remains an Element.
25. Add-on pricing still supports Fixed once, Per quantity, Per night, Per quantity per night, Per day and Per quantity per day.
26. Existing Elements, Person Types, seasons, discounts, annual pricing, occupancy and dormant Client/booking snapshot foundations remain intact.

Build 011 is deliberately a Setup refinement only. The later Availability / Offer Builder will resolve Add-ons using this priority: **individual Element override → Element Type default → unavailable if no rule exists**.
