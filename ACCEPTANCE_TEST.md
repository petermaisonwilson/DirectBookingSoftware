# Direct Booking Software - Build 012 Acceptance Test

Build 012 passes when:

1. GitHub Actions runs on `windows-latest`, the source smoke test passes, PyInstaller creates `DirectBookingSoftware.exe`, and the packaged EXE self-test passes.
2. GitHub uploads `DirectBookingSoftware-Build012` containing the EXE and this acceptance test.
3. On Windows the application title shows `Direct Booking Software - Build 012`.
4. Existing annual pricing, occupancy, Element Type inheritance, Add-on pricing methods, copy-year behaviour and deletion safety remain unchanged from Build 011.
5. In `Add-on rules → Element Type defaults`, the old editable Yes/No text cell is replaced by a simple tick control.
6. Ticked means **Y / Yes / available** for that Element Type; unticked means **N / No / not available**.
7. There is no blank/unreviewed availability state in the Element Type default control: a newly shown unticked row explicitly means No once saved.
8. When the Type-default control is ticked, Min, Max and Price must be completed before saving.
9. When the Type-default control is unticked, Min, Max and Price are not required and are visually inactive.
10. A Type-default price of `0.00` remains valid and retains the gentle zero-price highlight.
11. In `Element overrides`, the old word-based state entry is replaced by three compact radio choices: **I | Y | N**.
12. A brief explanation above the override grid states: **I = Inherit Element Type rule, Y = Yes allow this Add-on, N = No do not allow this Add-on**.
13. Exactly one of I, Y or N is selected for each override row.
14. **I** is the default when no individual Element override is stored.
15. **Y** enables Min, Max and Price and stores an individual Yes override when saved.
16. **N** stores an individual No override and requires no Min, Max or Price.
17. Returning a row to **I** removes the individual exception and restores inheritance from the Element Type default.
18. Existing Build 011 stored Yes/No overrides open with the correct Y or N radio selected; no database reset or conversion is required.
19. Existing Type defaults open with the correct ticked/unticked state.
20. Example behaviour remains: Camping → Dog Yes can be inherited by Pitch 1, while Pitch 7 → Dog N overrides it only for Pitch 7.
21. `Copy previous year` still copies and verifies Element Type defaults and individual overrides exactly as in Build 011.
22. `Delete year` still removes those annual rules safely while preserving catalogue records and other years.
23. Add-ons still inherit their parent Element dates; anything needing independent dates remains an Element.
24. Existing Elements, Person Types, seasons, discounts, annual pricing, occupancy and dormant Client/booking snapshot foundations remain intact.

Build 012 is deliberately a **UI-entry refinement only**. It does not alter Add-on pricing, inheritance priority or booking logic. The inheritance priority remains: **individual Element override → Element Type default → unavailable if no rule exists**.
