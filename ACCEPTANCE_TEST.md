# Direct Booking Software - Build 009 Acceptance Test

Build 009 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The Build 009 source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads `DirectBookingSoftware-Build009` containing the EXE and this acceptance test.
7. On a Windows PC, opening the EXE shows `Direct Booking Software - Build 009`.
8. Setup retains `Person types` and `Annual grids`.
9. Annual grids retains the three grids: `Seasonal element rates`, `Person rates / supplements`, and `Occupancy`.
10. Every annual grid displays a simple cell legend explaining blank, zero and configured values.
11. A required blank cell is visually highlighted as missing.
12. A required blank cell remains genuinely blank and is counted by the missing-data warning.
13. `Save all annual grids` refuses to save while any required cell remains blank.
14. When Save is refused, the first incomplete grid is opened and the grid/overall status text identifies the remaining blank counts.
15. The save-block message explains that every required blank must contain a figure, `0`, or `No limit` where appropriate.
16. An explicit `0` / `0.00` is valid and is not counted as missing.
17. An explicit zero is given a gentle review highlight so an accidental zero is noticeable without generating warning counts or pop-ups.
18. Positive/non-zero configured values remain normal and unflagged.
19. In Person rates / supplements, `0.00` explicitly means no charge/supplement where appropriate.
20. In Occupancy, `0` explicitly means that person type is not allowed; `No limit` remains valid where appropriate.
21. Newly added Elements in an existing/copied year appear with highlighted required blanks until reviewed.
22. Newly added Person Types create highlighted required cells until reviewed.
23. `Copy previous year` continues to copy and verify season structure, seasonal element rates, person rates/supplements and occupancy limits.
24. `Delete year` remains available with confirmation and preserves unrelated setup data.
25. The only remaining pricing year cannot be deleted.
26. A pricing year referenced by a frozen historic booking-pricing snapshot cannot be deleted.
27. Pricing Test continues to use annual data for the stay dates and rejects genuinely incomplete annual setup.
28. Seasonal boundary pricing, person charges/supplements and duration discounts continue to calculate as in Build 008.
29. Existing operator settings, Elements, Person Types, Seasons, discount rules and annual years remain intact; no local database deletion is required.
30. Dormant Client / booking-party / frozen-pricing snapshot foundations remain intact for later Enquiry, Offer and Booking builds.

Build 009 deliberately finishes annual pricing/configuration safety before the Enquiry / Offer Builder is introduced. Blank means not configured and must be resolved; zero means deliberately configured as zero and is therefore valid but visually highlighted for review.

Historical booking principle for later builds: once a booking is created, its actual commercial calculation will be stored as a frozen booking snapshot and will not silently recalculate when annual pricing grids are changed. Deliberate booking amendments will be recorded as amendments.

Manual arbitrary +/- adjustments with separate customer-facing and internal notes remain deferred to the real offer/booking pricing workflow.
