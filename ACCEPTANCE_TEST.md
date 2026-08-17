# Direct Booking Software — Online Build 014 Acceptance Test

Build 014 transfers the proven Setup model from the Windows prototype into the browser while retaining the accepted Build 013 online foundation.

Build 014 passes when:

1. GitHub Actions passes the original online foundation smoke test and the new `tests/online_setup014_test.py` Setup workflow test.
2. CI starts the local server and `/health` reports Build 014.
3. GitHub uploads `DirectBookingSoftware-Online-Build014` with `START_BUILD014.bat`.
4. Supervisor, Client/Operator and Customer roles continue to behave as in accepted Build 013.
5. Supervisor Support Mode remains visibly obvious and Setup changes made there are attributed to the Supervisor, not the client.
6. Client/Operator users can access Setup only for their own company.
7. Customers cannot access Setup.
8. Setup contains Elements, Person Types, Add-ons, Years, Seasonal pricing, Occupancy and Add-on rules.
9. Elements have a name, Element Type, pricing method and Base Price.
10. Element pricing methods include Per night, Per day, Per stay, Per person, Per person per night and Per package.
11. Person Types remain people/occupants rather than Elements or Add-ons.
12. Add-ons remain extras attached to Elements and support Fixed once, Per quantity, Per night, Per quantity per night, Per day and Per quantity per day.
13. Pricing years are independent and can be created blank or created using Copy previous year.
14. A blank year starts with an All Year season covering 1 January to 31 December.
15. Seasonal Element prices are entered for every active Element × Season cell; blank required cells are rejected while `0.00` is valid.
16. Occupancy is annual and contains Total maximum plus one maximum per Person Type for each Element.
17. Occupancy zero is valid; a Person Type maximum of zero means that Person Type is not allowed on that Element.
18. Element Type Add-on defaults use a simple availability tick: ticked = Yes/available, unticked = No/unavailable.
19. A Yes Type default requires Min, Max and Price; a zero Add-on price is valid.
20. Individual Element Add-on overrides use **I | Y | N** where I = inherit, Y = individual Yes and N = individual No.
21. I stores no individual exception and therefore returns to the Element Type default.
22. Individual Element override takes priority over the Element Type default.
23. Add-on inheritance priority remains **individual Element override → Element Type default → unavailable if no rule exists**.
24. Copy previous year copies seasons, seasonal Element prices, occupancy, Person Type limits, Element Type Add-on defaults and individual Element overrides.
25. A copied season keeps the same month/day pattern in the new year.
26. A newly added Element in an already-configured Element Type can inherit the Type Add-on default without requiring an individual Add-on rule.
27. Setup records are company-scoped; data created for Forest View cannot be viewed by another client such as Riverside.
28. Meaningful Setup changes are written to the permanent audit trail with the correct actor/client context.
29. Global Audit remains Supervisor-only and the append-only audit protection remains intact.
30. Windows Build 012 remains untouched as the reference prototype.

Build 014 deliberately stops before the Client Register, visual availability calendar, enquiry/offer workflow, customer-direct booking and payments.
