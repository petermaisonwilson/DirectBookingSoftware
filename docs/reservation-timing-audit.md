# Build 302 v3 performance audit — 11 November 2026

Measurements use fresh disposable SQLite databases and representative operational journeys.
These are full server response timings (including middleware and response body), not internet
latency, browser rendering, or a promise of identical performance on customer hardware.
The same audit runs on genuine PostgreSQL 16 in the required GitHub Actions gate.

## Requirements-screen root cause and correction

Repeated Element/year/person/add-on lookups, including repeated availability calculations
inside the Feature UI middleware, caused excessive database traffic. Tenant-scoped grouped
reads and request-local rule maps now reuse the existing override interpretation. No global
cache, schema change, availability write changes, or pricing rule changes were introduced.

Identical 30-Element, two-year, five-add-on fixture, same local runtime:

| Action | Build 732 seconds | Revised seconds | Build 732 SELECTs | Revised SELECTs |
|---|---:|---:|---:|---:|
| Who’s coming and when? | 1.5839 | 0.0335 | 2879 | 35 |
| New Booking including redirect to requirements | 1.5600 | 0.0390 | 2882 | 38 |

Query-count budgets are regression assertions; runtime is reported rather than subjected
to a hardware-dependent threshold. Explicit regression compares grouped add-on lookups with
the existing live resolver for overrides and years. Browser regression covers empty-calendar
click → required lead/party → filtered availability → first named hold → second Element.

## Site request inventory

742 requests; 129 method/path templates had a successful response out of 152 registered templates. A route template can cover several buttons and states; this is not a claim that every browser control and input permutation has been tested.

| Screen or action | Samples | Median seconds | 95th percentile | Maximum seconds |
|---|---:|---:|---:|---:|
| `GET /availability/calendar-v2` | 31 | 0.0259 | 0.1533 | 0.2033 |
| `POST /login` | 106 | 0.0341 | 0.0457 | 0.0786 |
| `POST /operations/enquiries/{enquiry_id}/edit` | 4 | 0.0250 | 0.0715 | 0.0715 |
| `GET /availability/start` | 17 | 0.0140 | 0.0618 | 0.0618 |
| `GET /availability/basket/review` | 8 | 0.0411 | 0.0544 | 0.0544 |
| `POST /availability/basket/customer` | 22 | 0.0185 | 0.0326 | 0.0413 |
| `POST /availability/hold` | 33 | 0.0136 | 0.0244 | 0.0396 |
| `POST /operations/reservations/{reservation_id:int}/edit` | 22 | 0.0202 | 0.0329 | 0.0378 |
| `POST /operations/customers/{customer_id}/enquiries/new` | 13 | 0.0213 | 0.0368 | 0.0368 |
| `POST /operations/bookings/{booking_id}/amend-apply` | 3 | 0.0329 | 0.0353 | 0.0353 |
| `GET /operations/reservations/{reservation_id:int}/edit` | 43 | 0.0135 | 0.0285 | 0.0348 |
| `GET /operations/reservations/{reservation_id:int}` | 14 | 0.0146 | 0.0286 | 0.0286 |
| `POST /operations/reservations/{reservation_id:int}/confirm-payment` | 7 | 0.0153 | 0.0231 | 0.0231 |
| `GET /availability/search` | 7 | 0.0105 | 0.0229 | 0.0229 |
| `POST /availability/requirements` | 31 | 0.0079 | 0.0154 | 0.0223 |
| `GET /operations/enquiries/{enquiry_id}` | 10 | 0.0168 | 0.0212 | 0.0212 |
| `POST /operations/reservations/{reservation_id:int}/elements/{allocation_id:int}/replace` | 1 | 0.0206 | 0.0206 | 0.0206 |
| `POST /operations/enquiries/{enquiry_id}/reopen` | 5 | 0.0077 | 0.0178 | 0.0178 |
| `POST /operations/enquiries/{enquiry_id}/confirm-payment` | 11 | 0.0129 | 0.0173 | 0.0173 |
| `GET /dashboard` | 5 | 0.0034 | 0.0158 | 0.0158 |
| `GET /setup/occupancy` | 2 | 0.0088 | 0.0153 | 0.0153 |
| `POST /availability/requirements-v3` | 3 | 0.0103 | 0.0149 | 0.0149 |
| `GET /operations/bookings/{booking_id}` | 6 | 0.0115 | 0.0138 | 0.0138 |
| `GET /availability/basket/customer` | 2 | 0.0121 | 0.0136 | 0.0136 |
| `GET /operations/enquiries/{enquiry_id}/confirm` | 1 | 0.0134 | 0.0134 | 0.0134 |
| `POST /setup/maintenance/catalog/toggle` | 3 | 0.0058 | 0.0132 | 0.0132 |
| `POST /operations/reservations/{reservation_id:int}/amend-apply` | 2 | 0.0130 | 0.0132 | 0.0132 |
| `POST /availability/basket/update` | 2 | 0.0102 | 0.0130 | 0.0130 |
| `GET /customer/direct-booking-preview` | 3 | 0.0085 | 0.0126 | 0.0126 |
| `POST /operations/reservations/{reservation_id:int}/amend-preview` | 1 | 0.0123 | 0.0123 | 0.0123 |
| `GET /operations/reservations/{reservation_id:int}/confirm` | 4 | 0.0093 | 0.0109 | 0.0109 |
| `GET /operations/reservations/{reservation_id:int}/customer-matches` | 1 | 0.0107 | 0.0107 | 0.0107 |
| `POST /operations/reservations/{reservation_id:int}/confirm-without-payment` | 1 | 0.0104 | 0.0104 | 0.0104 |
| `GET /operations/customers/{customer_id}/enquiries/new` | 6 | 0.0094 | 0.0103 | 0.0103 |
| `POST /setup/years/new` | 6 | 0.0043 | 0.0101 | 0.0101 |
| `POST /operations/enquiries/{enquiry_id}/confirm-without-payment` | 2 | 0.0057 | 0.0094 | 0.0094 |
| `GET /setup/addon-rules` | 1 | 0.0092 | 0.0092 | 0.0092 |
| `POST /setup/elements/availability/save` | 7 | 0.0040 | 0.0090 | 0.0090 |
| `GET /operations/enquiries` | 15 | 0.0055 | 0.0084 | 0.0084 |
| `GET /operations/reservations/{reservation_id:int}/cancel` | 1 | 0.0083 | 0.0083 | 0.0083 |
| `POST /operations/reservations/{reservation_id:int}/refunds` | 1 | 0.0082 | 0.0082 | 0.0082 |
| `POST /operations/reservations/{reservation_id:int}/reopen` | 1 | 0.0077 | 0.0077 | 0.0077 |
| `POST /setup/addons/when` | 2 | 0.0071 | 0.0076 | 0.0076 |
| `GET /setup/payment-methods` | 1 | 0.0072 | 0.0072 | 0.0072 |
| `GET /setup/pricing` | 1 | 0.0069 | 0.0069 | 0.0069 |
| `GET /setup/addons/when` | 1 | 0.0066 | 0.0066 | 0.0066 |
| `GET /operations/customers/new` | 1 | 0.0063 | 0.0063 | 0.0063 |
| `GET /setup/audit` | 2 | 0.0060 | 0.0062 | 0.0062 |
| `GET /audit` | 1 | 0.0061 | 0.0061 | 0.0061 |
| `GET /operations` | 7 | 0.0046 | 0.0059 | 0.0059 |
| `GET /setup/years` | 1 | 0.0059 | 0.0059 | 0.0059 |
| `POST /operations/reservations/{reservation_id:int}/payments` | 3 | 0.0049 | 0.0059 | 0.0059 |
| `POST /operations/bookings/{booking_id}/payments` | 1 | 0.0058 | 0.0058 | 0.0058 |
| `GET /operations/reservations/{reservation_id:int}/build` | 1 | 0.0057 | 0.0057 | 0.0057 |
| `POST /operations/reservations/{reservation_id:int}/cancel` | 1 | 0.0056 | 0.0056 | 0.0056 |
| `GET /setup/price-test` | 1 | 0.0056 | 0.0056 | 0.0056 |
| `GET /setup/elements` | 2 | 0.0035 | 0.0054 | 0.0054 |
| `GET /operations/customers/{customer_id}` | 2 | 0.0048 | 0.0054 | 0.0054 |
| `POST /setup/maintenance/seasons/save` | 2 | 0.0043 | 0.0054 | 0.0054 |
| `GET /setup` | 3 | 0.0047 | 0.0053 | 0.0053 |
| `GET /setup/maintenance/seasons/edit` | 1 | 0.0053 | 0.0053 | 0.0053 |
| `POST /support/start/{company_id}` | 9 | 0.0041 | 0.0052 | 0.0052 |
| `GET /setup/elements/availability` | 1 | 0.0052 | 0.0052 | 0.0052 |
| `POST /setup/years/copy` | 2 | 0.0033 | 0.0051 | 0.0051 |
| `GET /operations/bookings/{booking_id}/customer-matches` | 1 | 0.0051 | 0.0051 | 0.0051 |
| `GET /company/settings` | 4 | 0.0040 | 0.0051 | 0.0051 |
| `GET /operations/bookings/{booking_id}/cancel` | 2 | 0.0036 | 0.0051 | 0.0051 |
| `POST /setup/maintenance/element-types/delete` | 1 | 0.0050 | 0.0050 | 0.0050 |
| `GET /setup/guidance` | 2 | 0.0046 | 0.0050 | 0.0050 |
| `POST /logout` | 19 | 0.0026 | 0.0049 | 0.0049 |
| `GET /operations/customers` | 4 | 0.0034 | 0.0047 | 0.0047 |
| `POST /setup/addons` | 7 | 0.0033 | 0.0046 | 0.0046 |
| `POST /setup/maintenance/years/delete` | 3 | 0.0030 | 0.0046 | 0.0046 |
| `POST /operations/reservations/{reservation_id:int}/convert` | 1 | 0.0046 | 0.0046 | 0.0046 |
| `GET /setup/duration-discounts` | 2 | 0.0042 | 0.0045 | 0.0045 |
| `POST /operations/bookings/{booking_id}/link-customer` | 1 | 0.0044 | 0.0044 | 0.0044 |
| `POST /operations/reservations/{reservation_id:int}/release` | 1 | 0.0044 | 0.0044 | 0.0044 |
| `GET /operations/master-reservations/{reservation_id:int}` | 1 | 0.0044 | 0.0044 | 0.0044 |
| `GET /operations/bookings` | 2 | 0.0038 | 0.0043 | 0.0043 |
| `GET /setup/person-types` | 3 | 0.0040 | 0.0042 | 0.0042 |
| `GET /setup/audit/element` | 1 | 0.0042 | 0.0042 | 0.0042 |
| `POST /operations/bookings/{booking_id}/cancel` | 2 | 0.0038 | 0.0041 | 0.0041 |
| `POST /setup/maintenance/catalog/save` | 7 | 0.0030 | 0.0040 | 0.0040 |
| `POST /operations/bookings/{booking_id}/amend-preview` | 1 | 0.0040 | 0.0040 | 0.0040 |
| `GET /setup/booking-statuses` | 1 | 0.0040 | 0.0040 | 0.0040 |
| `POST /setup/elements` | 5 | 0.0034 | 0.0039 | 0.0039 |
| `GET /setup/addons` | 2 | 0.0039 | 0.0039 | 0.0039 |
| `GET /company/hold-settings` | 1 | 0.0039 | 0.0039 | 0.0039 |
| `GET /availability/holds` | 2 | 0.0031 | 0.0039 | 0.0039 |
| `POST /setup/maintenance/addon-rules/reset` | 1 | 0.0038 | 0.0038 | 0.0038 |
| `POST /setup/duration-discounts` | 1 | 0.0038 | 0.0038 | 0.0038 |
| `POST /availability/holds/renew` | 3 | 0.0036 | 0.0037 | 0.0037 |
| `POST /setup/duration-discounts/toggle` | 2 | 0.0034 | 0.0037 | 0.0037 |
| `GET /operations/customers/{customer_id}/edit` | 1 | 0.0037 | 0.0037 | 0.0037 |
| `POST /operations/enquiries/{enquiry_id}/release` | 5 | 0.0034 | 0.0036 | 0.0036 |
| `POST /ui/menu-order` | 1 | 0.0035 | 0.0035 | 0.0035 |
| `POST /company/base-currency` | 1 | 0.0035 | 0.0035 | 0.0035 |
| `GET /operations/master-reservations` | 1 | 0.0035 | 0.0035 | 0.0035 |
| `POST /operations/customers/new` | 1 | 0.0034 | 0.0034 | 0.0034 |
| `GET /operations/enquiries/{enquiry_id}/edit` | 6 | 0.0029 | 0.0034 | 0.0034 |
| `POST /setup/price-test` | 1 | 0.0033 | 0.0033 | 0.0033 |
| `POST /setup/item-order` | 1 | 0.0033 | 0.0033 | 0.0033 |
| `POST /setup/payment-rules` | 1 | 0.0033 | 0.0033 | 0.0033 |
| `POST /setup/maintenance/catalog/delete` | 4 | 0.0028 | 0.0032 | 0.0032 |
| `POST /availability/holds/release` | 2 | 0.0031 | 0.0032 | 0.0032 |
| `POST /setup/booking-statuses/save` | 5 | 0.0028 | 0.0032 | 0.0032 |
| `GET /operations/foundation` | 1 | 0.0032 | 0.0032 | 0.0032 |
| `POST /setup/element-types` | 5 | 0.0024 | 0.0031 | 0.0031 |
| `GET /availability/basket/edit` | 1 | 0.0031 | 0.0031 | 0.0031 |
| `GET /availability/calendar` | 2 | 0.0024 | 0.0030 | 0.0030 |
| `POST /setup/maintenance/seasons/delete` | 1 | 0.0029 | 0.0029 | 0.0029 |
| `POST /setup/person-types/age-toggle` | 2 | 0.0029 | 0.0029 | 0.0029 |
| `POST /company/hold-settings` | 2 | 0.0026 | 0.0028 | 0.0028 |
| `POST /company/settings` | 1 | 0.0028 | 0.0028 | 0.0028 |
| `POST /availability/new-booking` | 3 | 0.0023 | 0.0026 | 0.0026 |
| `POST /availability/basket/remove-view` | 4 | 0.0024 | 0.0025 | 0.0025 |
| `GET /operations/enquiries/{enquiry_id}/build` | 1 | 0.0024 | 0.0024 | 0.0024 |
| `POST /setup/elements/availability/delete` | 1 | 0.0023 | 0.0023 | 0.0023 |
| `GET /availability/basket` | 3 | 0.0020 | 0.0022 | 0.0022 |
| `POST /operations/enquiries/{enquiry_id}/convert` | 1 | 0.0022 | 0.0022 | 0.0022 |
| `GET /` | 1 | 0.0020 | 0.0020 | 0.0020 |
| `POST /setup/addon-rules` | 2 | 0.0015 | 0.0017 | 0.0017 |
| `GET /operations/master-reservations/new` | 1 | 0.0015 | 0.0015 | 0.0015 |
| `GET /health` | 1 | 0.0013 | 0.0013 | 0.0013 |
| `POST /setup/pricing` | 1 | 0.0013 | 0.0013 | 0.0013 |
| `GET /setup/element-types` | 1 | 0.0013 | 0.0013 | 0.0013 |
| `POST /setup/occupancy` | 1 | 0.0012 | 0.0012 | 0.0012 |
| `POST /setup/person-types` | 2 | 0.0009 | 0.0009 | 0.0009 |
| `GET /login` | 1 | 0.0006 | 0.0006 | 0.0006 |

## Coverage still requiring separate exercise

The following templates had no successful response. Some are compatibility APIs rather
than separate user controls. They are explicitly unverified by this timing run.

```text
GET /docs
GET /docs/oauth2-redirect
GET /openapi.json
GET /redoc
POST /availability/basket/remove
POST /availability/master-calendar-entry
POST /availability/requirements-v2
POST /operations/bookings/status
POST /operations/bookings/{booking_id}/refunds
POST /operations/customers/{customer_id}/edit
POST /operations/enquiries/{enquiry_id}/elements/{enquiry_element_id}/replace
POST /operations/master-reservations/new
POST /operations/reservations/{reservation_id:int}/link-customer
POST /setup/addons/requirement-toggle
POST /setup/booking-statuses/delete
POST /setup/booking-statuses/toggle
POST /setup/element-types/toggle
POST /setup/elements/popup
POST /setup/payment-methods
POST /setup/payment-methods/toggle
POST /setup/seasons
POST /support/stop
POST /ui/menu-order/reset
```

Reproduce: `python tests/site_timing_audit.py`. Set `DBS_TEST_POSTGRES_URL` to a dedicated
PostgreSQL database ending `_test` for the identical isolated-schema run. No emails are
sent; no external customer database is used. The audit records template paths, status,
scenario and duration only, with no payloads, query strings, cookies or customer identifiers.
