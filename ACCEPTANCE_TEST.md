# Build 015 Acceptance Test

1. Start with `START_BUILD015.bat` and log in as the Forest View operator.
2. Open **Setup > Element Types**. Add `Camping`. Try adding `camping` again and confirm the page stays visible with the red validation message.
3. Open **Elements**. Confirm Element Type is a dropdown, not a free-text box. Add or edit an Element using `Camping`.
4. Leave a required Setup value blank (for example Occupancy). Confirm the page stays visible and uses the same red warning/highlight style rather than a browser bubble or raw JSON.
5. Create/check a pricing year, seasonal Element price, occupancy limits and an Add-on rule.
6. Open **Price / Rules test**. Choose the Element, enter arrival/departure and people, then calculate.
7. Confirm occupancy limits are enforced.
8. Confirm an Add-on set to **N** on the individual Element is unavailable even if its Element Type default is **Y**.
9. Change the Element override back to **I** and confirm the Add-on follows the Element Type default and appears in the price breakdown.
10. Confirm the displayed total matches the configured seasonal Element rate and Add-on rule.
11. Rename the Element Type and confirm existing Elements continue to show under the renamed Type.
12. Copy the pricing year and confirm the copied rates/occupancy/Add-on rules remain present.
