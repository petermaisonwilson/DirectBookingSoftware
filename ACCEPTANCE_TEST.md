# Direct Booking Software - Build 001 Acceptance Test

Build 001 passes when:

1. GitHub Actions runs on `windows-latest`.
2. Python 3.11 and pinned dependencies install successfully.
3. The source smoke test passes.
4. PyInstaller creates `DirectBookingSoftware.exe`.
5. The packaged EXE runs with `--self-test` and exits successfully.
6. GitHub uploads the EXE in a `DirectBookingSoftware-Build001` artifact.
7. On a Windows PC, opening the EXE shows the Direct Booking Software desktop shell with:
   - Dashboard
   - Enquiries
   - Availability
   - Bookings
   - Finance
   - Setup
8. The SQLite development database is created under the current user's local application-data folder.
