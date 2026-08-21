# Direct Booking Web V1 Foundation

This branch starts the permanent fully web-based Direct Booking product.

## Foundation scope

- Browser-only application.
- Existing proven Setup retained: Element Types, Elements, Person Types, seasons, pricing, occupancy, Person pricing and Add-on rules.
- Client-isolated operational lifecycle: Customer -> Enquiry -> Offer -> Booking -> Arrival / Self Check-in.
- Confirmed booking structures store pricing snapshots so later Setup changes do not rewrite historical booking prices.
- Arrival records support both booked and walk-in/unbooked arrivals.
- Supervisor Support Mode and immutable audit foundation remain in place.

## Deliberately not in this milestone

- Visual availability calendar.
- Working enquiry/offer/booking entry forms.
- Payments.
- Customer-facing booking widget.
- Production hosting configuration.

The foundation branch must pass its own Windows CI before it is considered for main.
