# Direct Booking Software - Build 002 Acceptance Test

Build 002 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The Build 002 source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads `DirectBookingSoftware-Build002` containing the EXE and this acceptance test.
7. On a Windows PC, opening the EXE shows `Direct Booking Software - Build 002`.
8. Setup contains three tabs: `Operator & reminders`, `Seasons`, and `Elements`.
9. Operator settings can be changed and saved, including business name, address, email, phone, reminder settings and global deposit settings.
10. An element can be added with a name, group, pricing type and base price, then edited and inactivated/reactivated.
11. A season can be added with start/end dates displayed as `dd/mm/yyyy`, priority and active status, then edited and inactivated/reactivated.
12. The Dashboard `Active elements` count changes when an element is activated or inactivated.
13. Close the application and reopen it. The saved operator settings, elements and seasons must still be present.
14. Existing Build 001 local data is retained; Build 002 must not require deleting the local database.
