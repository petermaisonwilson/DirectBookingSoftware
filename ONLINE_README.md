# Direct Booking Software — Online Build 015

Build 015 extends the accepted Build 014 online foundation with:

- Client-defined Element Types with case-insensitive duplicate protection.
- Element Type dropdowns when adding/editing Elements.
- Safe type rename and active/inactive status; renames follow existing Elements and type-level Add-on rules.
- Consistent red in-page validation across Setup forms and grids.
- A Price / Rules test screen using dates, people, occupancy, seasonal rates, Add-on defaults and Element I/Y/N overrides.
- Existing Build 014 year copy, permissions, Support Mode and audit behaviour retained.

## Run locally

Double-click `START_BUILD015.bat` and keep the black window open while testing.
The local browser address is `http://127.0.0.1:8000`.

The local development database is `online_data/direct_booking_online_dev.db` relative to the extracted folder. If you want to test migration with your previous local data, copy that database into the same relative location before starting Build 015.

## Test calculator scope

Build 015 is a Setup/rules calculator, not yet a Booking. A stay must remain within one pricing year. If seasons overlap, the narrowest matching season wins, so a specific Summer season overrides an All Year season.
