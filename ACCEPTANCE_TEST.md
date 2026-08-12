# Direct Booking Software - Build 003 Acceptance Test

Build 003 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The Build 003 source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads `DirectBookingSoftware-Build003` containing the EXE and this acceptance test.
7. On a Windows PC, opening the EXE shows `Direct Booking Software - Build 003`.
8. Setup contains four tabs: `Operator & reminders`, `Seasons`, `Elements`, and `Discount rules`.
9. In Elements, create a new unused test element and permanently delete it. It must disappear from the list and remain deleted after restarting the app.
10. Elements that have historical offer or booking use must be protected from permanent deletion; the app must instruct the operator to make them inactive instead.
11. Add a duration discount rule with a minimum stay and one of these types: percentage, fixed amount, or free nights.
12. A discount rule can apply to all elements, one group, or one selected element.
13. Discount rules can be edited and activated/inactivated and remain present after restarting the app.
14. Where more than one duration rule qualifies, the pricing foundation selects the single rule that gives the largest discount rather than stacking rules.
15. Free-night rules are only applied to night-based pricing types.
16. Existing Build 002 operator settings, seasons and elements are retained; Build 003 must not require deleting the local database.

Manual arbitrary +/- price adjustments with separate customer-facing and internal notes are intentionally deferred to the later offer/pricing build.
