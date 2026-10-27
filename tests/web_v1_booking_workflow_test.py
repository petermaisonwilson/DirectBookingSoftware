from __future__ import annotations
from operational_backend import create_app, reservation_id_for, source_id_from_location
from online.sql import execute_sql, insert_sql

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from online.app import COOKIE_NAME
from online.database import iso_now
from online.webv1 import register_web_v1
from online.webv1_status_availability import availability_state
import online.webv1_bookings as booking_module
from online.webv1_bookings import convert_enquiry_with_payment, payment_due
from online.webv1_booking_amendments import amendment_quote, apply_amendment


def login(client: TestClient, email: str, password: str) -> None:
    assert client.post('/login', data={'email': email, 'password': password}, follow_redirects=False).status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(Path(temp_dir) / 'booking-workflow.db', seed_demo=True)
        register_web_v1(app)
        db = app.state.database
        client = TestClient(app)
        login(client, 'operator@forestview.test', 'Operator013!')
        ctx = db.session_context(client.cookies.get(COOKIE_NAME)); csrf = str(ctx['csrf_token']); cid = int(ctx['company_id'])
        now = iso_now()

        with db.connect() as c:
            execute_sql(c, 'INSERT INTO setup_element_types(company_id,name,active) VALUES (:q42_0,:q42_1,1) ON CONFLICT DO NOTHING', (cid, 'Lodge'))
            element_id = int(insert_sql(c, 'INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price,active) VALUES (:q64_0,:q64_1,:q64_2,:q64_3,:q64_4,1)', (cid, 'Lake Lodge 1', 'Lodge', 'Per night', 0)).lastrowid)
            execute_sql(c, 'INSERT INTO setup_years(company_id,year) VALUES (:q43_0,:q43_1) ON CONFLICT DO NOTHING', (cid, 2035))
            season_id = int(insert_sql(c, 'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q65_0,:q65_1,:q65_2,:q65_3,:q65_4)', (cid, 2035, 'Summer', '2035-01-01', '2035-12-31')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q44_0,:q44_1,:q44_2,:q44_3,:q44_4)', (cid, 2035, element_id, season_id, 100))
            person_id = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name,active) VALUES (:q66_0,:q66_1,:q66_2,1)', (cid, 'Adult', 'Adult')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q45_0,:q45_1,:q45_2,:q45_3,:q45_4)', (cid, 2035, element_id, person_id, 10))
            addon_id = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (:q67_0,:q67_1,:q67_2,1)', (cid, 'Breakfast', 'Fixed once')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q46_0,:q46_1,:q46_2,:q46_3,:q46_4,:q46_5,:q46_6,:q46_7)', (cid, 2035, 'Lodge', addon_id, 1, 1, 4, 20))
            free_addon_id = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method,active) VALUES (:q68_0,:q68_1,:q68_2,1)', (cid, 'Motorhome U8 Tons', 'Fixed once')).lastrowid)
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q47_0,:q47_1,:q47_2,:q47_3,:q47_4,:q47_5,:q47_6,:q47_7)', (cid, 2035, 'Lodge', free_addon_id, 1, 1, 1, 0))

            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) VALUES (:q48_0,:q48_1,:q48_2,:q48_3) ON CONFLICT(company_id,year,element_id) DO UPDATE SET max_total=excluded.max_total', (cid, 2035, element_id, 6))
            people = execute_sql(c, 'SELECT id FROM setup_person_types WHERE company_id=:q69_0 AND active=1', (cid,)).fetchall()
            for person in people:
                pid = int(person['id'])
                execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count) VALUES (:q63_0,:q63_1,:q63_2,:q63_3,:q63_4) ON CONFLICT(company_id,year,element_id,person_type_id) DO UPDATE SET max_count=excluded.max_count,min_count=excluded.min_count', (cid, 2035, element_id, pid, 6))
                existing_price = execute_sql(c, 'SELECT rate FROM setup_person_prices WHERE company_id=:q94_0 AND year=:q94_1 AND element_id=:q94_2 AND person_type_id=:q94_3', (cid, 2035, element_id, pid)).fetchone()
                if existing_price is None:
                    execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q70_0,:q70_1,:q70_2,:q70_3,:q70_4)', (cid, 2035, element_id, pid, 0.0))

            customer_id = int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (:q71_0,:q71_1,:q71_2,:q71_3,:q71_4,:q71_5,:q71_6)', (cid, 'Workflow', 'Tester', 'workflow@example.test', '123', now, now)).lastrowid)
            enquiry_id = int(insert_sql(c, 'INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at) VALUES (:q72_0,:q72_1,:q72_2,:q72_3,:q72_4,:q72_5,:q72_6,:q72_7,:q72_8,:q72_9)', (cid, customer_id, 'new', 'Test', '2035-06-10', '2035-06-13', 2, 'Booking workflow test', now, now)).lastrowid)
            snapshot = {
                'element_type': 'Lodge', 'element_id': element_id, 'element_name': 'Lake Lodge 1', 'year': 2035,
                'nights': 3, 'people_total': 2, 'addon_when': {str(addon_id): 'every_day'},
                'addon_days': {}, 'addon_people': {}, 'addon_person_days': {}, 'selected_addons': [addon_id, free_addon_id],
                'lines': [
                    {'item': 'Lake Lodge 1', 'rule': 'Per night', 'amount': 300.0},
                    {'item': 'Adult', 'rule': '2 adults', 'amount': 60.0},
                    {'item': 'Breakfast', 'rule': 'Fixed once', 'amount': 20.0},
                    {'item': 'Motorhome U8 Tons', 'rule': 'Fixed once', 'amount': 0.0},
                ], 'total': 380.0,
            }
            execute_sql(c, 'INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at) VALUES (:q49_0,:q49_1,:q49_2,:q49_3,:q49_4,:q49_5,:q49_6)', (enquiry_id, cid, 'Lodge', element_id, 380.0, json.dumps(snapshot), now))
            execute_sql(c, 'INSERT INTO enquiry_people(enquiry_id,company_id,person_type_id,quantity) VALUES (:q50_0,:q50_1,:q50_2,:q50_3)', (enquiry_id, cid, person_id, 2))
            execute_sql(c, 'INSERT INTO enquiry_addons(enquiry_id,company_id,addon_id,quantity) VALUES (:q51_0,:q51_1,:q51_2,:q51_3)', (enquiry_id, cid, addon_id, 1))
            execute_sql(c, 'INSERT INTO enquiry_addons(enquiry_id,company_id,addon_id,quantity) VALUES (:q52_0,:q52_1,:q52_2,:q52_3)', (enquiry_id, cid, free_addon_id, 1))
            confirmed = execute_sql(c, "SELECT id,colour FROM booking_status_definitions WHERE company_id=:q73_0 AND active=1 AND internal_state='CONFIRMED' ORDER BY display_order,id LIMIT 1", (cid,)).fetchone()
            released = execute_sql(c, "SELECT id FROM booking_status_definitions WHERE company_id=:q74_0 AND active=1 AND internal_state='RELEASED' ORDER BY display_order,id LIMIT 1", (cid,)).fetchone()
            held = execute_sql(c, "SELECT id,name FROM booking_status_definitions WHERE company_id=:q75_0 AND active=1 AND internal_state='HELD' ORDER BY display_order,id LIMIT 1", (cid,)).fetchone()
            confirmed_id = int(confirmed['id']); confirmed_colour = str(confirmed['colour']); released_id = int(released['id']); held_id = int(held['id'])
            assert str(held['name']) == 'Enquiry / Held'

            future = datetime.now(timezone.utc) + timedelta(minutes=30)
            own_hold_id = int(insert_sql(c, 'INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at,lead_name)\n                VALUES (:q76_0,:q76_1,:q76_2,:q76_3,:q76_4,:q76_5,:q76_6,:q76_7,:q76_8,:q76_9,:q76_10)', (cid, element_id, str(client.cookies.get(COOKIE_NAME)), int(ctx['user_id']), '2035-06-10', '2035-06-13', future.isoformat(timespec='seconds'), future.isoformat(timespec='seconds'), now, now, 'Tester')).lastrowid)
            foreign_hold_id = int(insert_sql(c, 'INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at,lead_name)\n                VALUES (:q77_0,:q77_1,:q77_2,:q77_3,:q77_4,:q77_5,:q77_6,:q77_7,:q77_8,:q77_9,:q77_10)', (cid, element_id, 'different-session-token', int(ctx['user_id']), '2035-06-10', '2035-06-13', future.isoformat(timespec='seconds'), future.isoformat(timespec='seconds'), now, now, 'Other customer')).lastrowid)

        setup_page = client.get('/setup')
        assert setup_page.status_code == 200 and 'Payment Methods' in setup_page.text and '/setup/payment-methods' in setup_page.text
        duration_page=client.get('/setup/duration-discounts')
        assert duration_page.status_code==200 and 'Duration Discounts' in duration_page.text and 'Add Duration Discount' in duration_page.text
        rules_page=client.get('/setup/payment-methods')
        assert rules_page.status_code==200 and 'Payment / Deposit Rules' in rules_page.text and 'Always Require Full Payment' in rules_page.text
        saved_rules=client.post('/setup/payment-rules',data={'csrf':csrf,'deposit_type':'percent','deposit_value':'25','full_payment_threshold':'150','balance_due_days':'30'},follow_redirects=False)
        assert saved_rules.status_code==303
        due,reason=payment_due(db,cid,380.0,'2035-06-10',today=datetime(2035,1,1).date())
        assert due==95.0 and '25%' in reason
        with db.connect() as c:
            execute_sql(c, "UPDATE payment_rules SET deposit_type='fixed',deposit_value=100,full_payment_threshold=150,balance_due_days=30,always_require_full_payment=0 WHERE company_id=:q53_0",(cid,))
        assert payment_due(db,cid,120.0,'2035-06-10',today=datetime(2035,1,1).date())[0]==120.0
        assert payment_due(db,cid,380.0,'2035-01-20',today=datetime(2035,1,1).date())[0]==380.0

        # TEST DATA ONLY: visible status labels are client-editable, but finance
        # automation must continue to use the stable machine meaning.
        with db.connect() as c:
            execute_sql(c, "UPDATE booking_status_definitions SET name='Part Paid' WHERE company_id=:q54_0 AND system_code='DEPOSIT_PAID'", (cid,))
            execute_sql(c, "UPDATE booking_status_definitions SET name='Settled' WHERE company_id=:q55_0 AND system_code='BALANCE_PAID'", (cid,))

        # Adopt the deliberately seeded legacy fixture as an existing installation would.
        from online.reservation_identity_upgrade import adopt_company
        with db.reservation_write_transaction(cid) as c:
            adopt_company(c, cid)
        detail = client.get(f'/operations/enquiries/{enquiry_id}')
        assert detail.status_code == 200
        assert 'Keep as Quote' not in detail.text and 'KEEP AS QUOTE' not in detail.text
        assert 'Confirm Booking' in detail.text and 'CONFIRM BOOKING' in detail.text

        before = availability_state(db, cid, element_id, '2035-06-10', '2035-06-13')
        assert before['available'] is False and before['state'] == 'ENQUIRY'
        holding_report=client.get('/operations/enquiries?status=holding')
        assert holding_report.status_code==200 and f'#{enquiry_id}' in holding_report.text and 'Holding' in holding_report.text
        reservation_id = reservation_id_for(db, cid, enquiry_id=enquiry_id)
        first = client.post(f'/operations/enquiries/{enquiry_id}/convert', data={'csrf': csrf, 'workflow_status_id': str(confirmed_id)}, follow_redirects=False)
        assert first.status_code == 303 and f'/operations/reservations/{reservation_id}/confirm' in first.headers['location']
        with db.connect() as c:
            assert execute_sql(c, 'SELECT id FROM bookings WHERE company_id=:q95_0 AND enquiry_id=:q95_1', (cid, enquiry_id)).fetchone() is None
            execute_sql(c, 'DELETE FROM element_holds WHERE id=:q56_0 AND company_id=:q56_1', (foreign_hold_id, cid))
            assert execute_sql(c, 'SELECT id FROM element_holds WHERE id=:q96_0 AND company_id=:q96_1', (own_hold_id, cid)).fetchone() is not None
            cash = execute_sql(c, "SELECT id FROM payment_method_definitions WHERE company_id=:q78_0 AND name='Cash' AND active=1", (cid,)).fetchone()
            assert cash is not None
            cash_id = int(cash['id'])

        confirm_page = client.get(first.headers['location'])
        assert confirm_page.status_code == 200 and 'Take Payment' in confirm_page.text and 'CONFIRM WITHOUT PAYMENT' in confirm_page.text
        assert 'Payment Required Now:' in confirm_page.text and '€100.00' in confirm_page.text and 'Deposit Pending' in confirm_page.text and 'Party:' in confirm_page.text
        assert 'Booking Elements' in confirm_page.text and 'Lead Passenger:' in confirm_page.text and 'Element total:' in confirm_page.text
        assert 'Breakfast (€20.00)' in confirm_page.text
        assert 'Motorhome U8 Tons' in confirm_page.text and 'Motorhome U8 Tons (€0.00)' not in confirm_page.text
        assert 'readonly' not in confirm_page.text and 'min="100.00"' in confirm_page.text and 'max="380.00"' in confirm_page.text
        # Merely visiting/abandoning Confirm Booking is non-mutating: the Enquiry
        # remains the availability blocker until an explicit conversion/release.
        abandoned=client.get(f'/operations/enquiries/{enquiry_id}')
        assert abandoned.status_code==200
        with db.connect() as c:
            assert execute_sql(c, 'SELECT id FROM bookings WHERE company_id=:q97_0 AND enquiry_id=:q97_1',(cid,enquiry_id)).fetchone() is None
            assert execute_sql(c, 'SELECT status FROM enquiries WHERE id=:q107_0 AND company_id=:q107_1',(enquiry_id,cid)).fetchone()['status']=='new'
        abandoned_state=availability_state(db,cid,element_id,'2035-06-10','2035-06-13')
        assert abandoned_state['state']=='ENQUIRY' and abandoned_state['available'] is False
        too_small=client.post(f'/operations/enquiries/{enquiry_id}/confirm-payment',data={'csrf':csrf,'workflow_status_id':str(confirmed_id),'payment_method_id':str(cash_id),'amount':'99.00','payment_date':'2035-05-01'},follow_redirects=False)
        assert too_small.status_code==303
        with db.connect() as c:
            assert execute_sql(c, 'SELECT id FROM bookings WHERE company_id=:q98_0 AND enquiry_id=:q98_1',(cid,enquiry_id)).fetchone() is None
            assert execute_sql(c, 'SELECT id FROM booking_payments WHERE company_id=:q99_0',(cid,)).fetchone() is None
            cash_row=execute_sql(c, 'SELECT * FROM payment_method_definitions WHERE id=:q79_0',(cash_id,)).fetchone()
        original_write=booking_module.write_confirmation_details
        def fail_after_write(*args,**kwargs):
            original_write(*args,**kwargs)
            raise RuntimeError('Atomicity test failure')
        booking_module.write_confirmation_details=fail_after_write
        try:
            try:
                convert_enquiry_with_payment(db,ctx,cid,enquiry_id,confirmed_id,amount=100.0,payment_date='2035-05-01',method=cash_row)
                raise AssertionError('forced payment failure did not raise')
            except RuntimeError:
                pass
        finally:
            booking_module.write_confirmation_details=original_write
        with db.connect() as c:
            assert execute_sql(c, 'SELECT id FROM bookings WHERE company_id=:q100_0 AND enquiry_id=:q100_1',(cid,enquiry_id)).fetchone() is None
            assert execute_sql(c, 'SELECT id FROM booking_payments WHERE company_id=:q101_0',(cid,)).fetchone() is None
            assert execute_sql(c, 'SELECT status FROM enquiries WHERE id=:q108_0',(enquiry_id,)).fetchone()['status']=='new'
        with db.connect() as c:
            assert execute_sql(c, 'SELECT id FROM bookings WHERE company_id=:q102_0 AND enquiry_id=:q102_1', (cid, enquiry_id)).fetchone() is None

        too_large=client.post(f'/operations/enquiries/{enquiry_id}/confirm-payment',data={'csrf':csrf,'workflow_status_id':str(confirmed_id),'payment_method_id':str(cash_id),'amount':'381.00','payment_date':'2035-05-01'},follow_redirects=False)
        assert too_large.status_code==303
        with db.connect() as c: assert execute_sql(c, 'SELECT id FROM bookings WHERE company_id=:q103_0 AND enquiry_id=:q103_1',(cid,enquiry_id)).fetchone() is None

        converted = client.post(f'/operations/enquiries/{enquiry_id}/confirm-payment', data={'csrf': csrf, 'workflow_status_id': str(confirmed_id), 'payment_method_id': str(cash_id), 'amount': '150.00', 'payment_date': '2035-05-01', 'reference': 'DEP-1', 'notes': 'Deposit above minimum'}, follow_redirects=False)
        assert converted.status_code == 303 and f'/operations/reservations/{reservation_id}' in converted.headers['location']
        booking_id = source_id_from_location(db, cid, converted.headers['location'], kind='booking')

        with db.connect() as c:
            b = execute_sql(c, 'SELECT * FROM bookings WHERE id=:q80_0 AND company_id=:q80_1', (booking_id, cid)).fetchone()
            assert b is not None and b['enquiry_id'] == enquiry_id and float(b['total_amount']) == 380.0
            assert str(b['reference']).startswith('DB') and int(b['workflow_status_id']) == confirmed_id
            assert execute_sql(c, 'SELECT status FROM enquiries WHERE id=:q109_0', (enquiry_id,)).fetchone()['status'] == 'converted'
            be = execute_sql(c, 'SELECT * FROM booking_elements WHERE booking_id=:q81_0', (booking_id,)).fetchone()
            assert be is not None and int(be['element_id']) == element_id and float(be['total_amount']) == 300.0
            bp = execute_sql(c, 'SELECT * FROM booking_people WHERE booking_element_id=:q82_0', (be['id'],)).fetchone()
            assert bp is not None and int(bp['quantity']) == 2 and float(bp['total_amount']) == 60.0
            ba = execute_sql(c, 'SELECT * FROM booking_addons WHERE booking_element_id=:q83_0 AND addon_id=:q83_1', (be['id'], addon_id)).fetchone()
            free_ba = execute_sql(c, 'SELECT * FROM booking_addons WHERE booking_element_id=:q84_0 AND addon_id=:q84_1', (be['id'], free_addon_id)).fetchone()
            assert ba is not None and float(ba['total_amount']) == 20.0
            assert free_ba is not None and float(free_ba['total_amount']) == 0.0
            frozen = json.loads(ba['rule_snapshot_json']); assert float(frozen['frozen_amount']) == 20.0
            reference = str(b['reference'])
            assert execute_sql(c, 'SELECT id FROM element_holds WHERE id=:q104_0 AND company_id=:q104_1', (own_hold_id, cid)).fetchone() is None
            assert execute_sql(c, 'SELECT 1 FROM hold_requirement_people WHERE hold_id=:q105_0', (own_hold_id,)).fetchone() is None
            assert execute_sql(c, 'SELECT 1 FROM hold_requirement_addons WHERE hold_id=:q106_0', (own_hold_id,)).fetchone() is None

        after = availability_state(db, cid, element_id, '2035-06-10', '2035-06-13')
        assert after['available'] is False and after['state'] == 'BOOKED' and after['booking_id'] == booking_id
        assert after['colour'] == confirmed_colour
        cal = client.get('/availability/calendar-v2?element_type=Lodge&arrival=2035-06-10&departure=2035-06-13')
        assert cal.status_code == 200 and reference in cal.text and confirmed_colour in cal.text
        assert 'hold-expiry-calendar-refresh' not in cal.text
        assert 'id="global-hold-modal"' in cal.text
        assert 'id="hold-modal"' not in cal.text
        assert 'checkExpiry' not in cal.text and 'releasedTransition' not in cal.text

        with db.connect() as c:
            execute_sql(c, 'UPDATE setup_element_rates SET rate=999 WHERE company_id=:q57_0 AND year=:q57_1 AND element_id=:q57_2', (cid, 2035, element_id))
            execute_sql(c, 'UPDATE setup_person_prices SET rate=999 WHERE company_id=:q58_0 AND year=:q58_1 AND element_id=:q58_2 AND person_type_id=:q58_3', (cid, 2035, element_id, person_id))
        booking_page = client.get(f'/operations/bookings/{booking_id}')
        assert booking_page.status_code == 200 and '€380.00' in booking_page.text and 'Frozen Booking' in booking_page.text
        assert 'Frozen total:' in booking_page.text and 'People:' in booking_page.text and 'Add-ons:' in booking_page.text and 'Lead Passenger:' in booking_page.text
        assert 'Breakfast × 1 (€20.00)' in booking_page.text
        assert 'Motorhome U8 Tons × 1' in booking_page.text and 'Motorhome U8 Tons × 1 (€0.00)' not in booking_page.text

        # Once converted, the Enquiry is historical source data: neither GET nor POST
        # may reopen its pricing editor or mutate the frozen Booking.
        edit_converted = client.get(f'/operations/enquiries/{enquiry_id}/edit', follow_redirects=False)
        assert edit_converted.status_code == 303 and edit_converted.headers['location'] == f'/operations/reservations/{reservation_id}'
        post_converted = client.post(f'/operations/enquiries/{enquiry_id}/edit', data={'csrf': csrf, 'arrival_date':'2035-06-11','departure_date':'2035-06-14'}, follow_redirects=False)
        assert post_converted.status_code == 303 and post_converted.headers['location'] == f'/operations/reservations/{reservation_id}'
        with db.connect() as c:
            still_frozen = execute_sql(c, 'SELECT arrival_date,departure_date,total_amount FROM bookings WHERE id=:q85_0',(booking_id,)).fetchone()
            assert still_frozen['arrival_date']=='2035-06-10' and still_frozen['departure_date']=='2035-06-13' and float(still_frozen['total_amount'])==380.0

        page = client.get(f'/operations/bookings/{booking_id}')
        assert '€150.00' in page.text and '€230.00' in page.text and 'Recorded payment €150.00' in page.text
        assert 'value="230.00"' in page.text
        assert 'Change Status' not in page.text and 'controlled automatically by DBS' in page.text
        assert 'Amend Booking' in page.text and 'Preview Amendment' in page.text

        # TEST DATA ONLY: pay the remaining balance using the normal route. Renaming
        # the visible statuses above must not stop DBS selecting the correct state.
        paid_off = client.post(f'/operations/bookings/{booking_id}/payments', data={
            'csrf': csrf, 'amount': '230.00', 'payment_date': '2035-05-02',
            'payment_method_id': str(cash_id), 'reference': 'BAL-1', 'notes': 'Balance'
        }, follow_redirects=False)
        assert paid_off.status_code == 303
        with db.connect() as c:
            paid_status = execute_sql(c, 'SELECT s.name,s.system_code FROM bookings b\n                JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id\n                WHERE b.id=:q86_0 AND b.company_id=:q86_1', (booking_id, cid)).fetchone()
            assert paid_status['name'] == 'Settled' and paid_status['system_code'] == 'BALANCE_PAID'
        paid_page = client.get(f'/operations/bookings/{booking_id}')
        assert 'Outstanding:</strong> €0.00' in paid_page.text

        # Confirmed-booking amendment: historical nights remain frozen even after Setup rate changes.
        # Extending from 3 to 7 nights prices only four new nights at the current €999 rate and
        # applies the configured duration discount across the whole amended package.
        duration_saved = client.post('/setup/duration-discounts', data={
            'csrf': csrf, 'name': '7 nights', 'min_nights': '7',
            'discount_type': 'Percentage', 'discount_value': '10',
            'scope_type': 'Element', 'element_id': str(element_id), 'element_type': ''
        }, follow_redirects=False)
        assert duration_saved.status_code == 303
        with db.connect() as c:
            duration_row = execute_sql(c, "SELECT id,active FROM setup_duration_discounts WHERE company_id=:q87_0 AND name='7 nights'", (cid,)).fetchone()
            assert duration_row is not None and int(duration_row['active']) == 1
            duration_id = int(duration_row['id'])
        toggled = client.post('/setup/duration-discounts/toggle', data={'csrf': csrf, 'id': str(duration_id)}, follow_redirects=False)
        assert toggled.status_code == 303
        with db.connect() as c:
            assert int(execute_sql(c, 'SELECT active FROM setup_duration_discounts WHERE id=:q117_0', (duration_id,)).fetchone()['active']) == 0
        assert client.post('/setup/duration-discounts/toggle', data={'csrf': csrf, 'id': str(duration_id)}, follow_redirects=False).status_code == 303
        quote=amendment_quote(db,cid,booking_id,int(execute_sql(c, 'SELECT id FROM booking_elements WHERE booking_id=:q118_0',(booking_id,)).fetchone()['id']) if False else 0,'2035-06-10','2035-06-17') if False else None
        with db.connect() as c:
            beid=int(execute_sql(c, 'SELECT id FROM booking_elements WHERE booking_id=:q110_0 AND company_id=:q110_1',(booking_id,cid)).fetchone()['id'])
        quote=amendment_quote(db,cid,booking_id,beid,'2035-06-10','2035-06-17',20.0)
        assert quote['retained_historic_base']==380.0 and quote['added_nights']==4
        assert quote['added_current_element']==3996.0 and quote['duration_discount']>0
        assert quote['duration_rule']['name']=='7 nights' and quote['manual_discount']==20.0
        amendment_id=apply_amendment(db,ctx,quote)
        assert amendment_id>0
        with db.connect() as c:
            amended=execute_sql(c, 'SELECT b.arrival_date,b.departure_date,b.total_amount,s.name AS status_name,s.system_code FROM bookings b LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id WHERE b.id=:q88_0',(booking_id,)).fetchone()
            original=execute_sql(c, 'SELECT original_arrival_date,original_departure_date,original_total_amount FROM booking_elements WHERE id=:q89_0',(beid,)).fetchone()
        assert amended['departure_date']=='2035-06-17'
        assert amended['status_name']=='Part Paid' and amended['system_code']=='DEPOSIT_PAID'
        assert original['original_arrival_date']=='2035-06-10' and original['original_departure_date']=='2035-06-13' and float(original['original_total_amount'])==300.0
        amended_page=client.get(f'/operations/bookings/{booking_id}')
        assert 'Staff discount:</strong> −€20.00' in amended_page.text
        assert 'name="manual_discount" min="0" step="0.01" value="20.00"' in amended_page.text
        shorter=amendment_quote(db,cid,booking_id,beid,'2035-06-10','2035-06-15')
        assert shorter['new_nights']==5 and shorter['duration_discount']==0 and shorter['manual_discount']==20.0


        # Release/reopen is a separate lifecycle from basket holds and conversion.
        # Prove both successful reclaim and refusal when another Booking owns the dates.
        with db.connect() as c:
            life_customer=int(insert_sql(c, 'INSERT INTO customer_records(company_id,first_name,last_name,email,phone,created_at,updated_at) VALUES (:q90_0,:q90_1,:q90_2,:q90_3,:q90_4,:q90_5,:q90_6)',(cid,'Lifecycle','Test','life@example.test','',now,now)).lastrowid)
            life_id=int(insert_sql(c, 'INSERT INTO enquiries(company_id,customer_id,status,workflow_status_id,source,arrival_date,departure_date,party_size,notes,created_at,updated_at) VALUES (:q91_0,:q91_1,:q91_2,:q91_3,:q91_4,:q91_5,:q91_6,:q91_7,:q91_8,:q91_9,:q91_10)',(cid,life_customer,'new',held_id,'Test','2035-07-01','2035-07-04',2,'Lifecycle regression',now,now)).lastrowid)
            execute_sql(c, 'INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at) VALUES (:q59_0,:q59_1,:q59_2,:q59_3,:q59_4,:q59_5,:q59_6)',(life_id,cid,'Lodge',element_id,300.0,json.dumps(snapshot),now))
        life_state=availability_state(db,cid,element_id,'2035-07-01','2035-07-04')
        assert life_state['state']=='ENQUIRY', life_state
        assert client.post(f'/operations/enquiries/{life_id}/release',data={'csrf':csrf},follow_redirects=False).status_code==303
        assert availability_state(db,cid,element_id,'2035-07-01','2035-07-04')['available'] is True
        released_report = client.get('/operations/enquiries?status=released')
        assert released_report.status_code == 200 and f'#{life_id}' in released_report.text
        assert f'<a href="/operations/reservations/{reservation_id}">#{enquiry_id}</a>' not in released_report.text
        life_reservation_id = reservation_id_for(db, cid, enquiry_id=life_id)
        released_anchor = f'<a href="/operations/reservations/{life_reservation_id}">#{life_id}</a>'
        assert released_anchor in released_report.text
        released_row = released_report.text.split(released_anchor, 1)[1].split('</tr>', 1)[0]
        assert 'RELEASE ENQUIRY' not in released_row and 'REOPEN ENQUIRY' in released_row
        assert client.post(f'/operations/enquiries/{life_id}/reopen',data={'csrf':csrf},follow_redirects=False).status_code==303
        assert availability_state(db,cid,element_id,'2035-07-01','2035-07-04')['state']=='ENQUIRY'
        assert client.post(f'/operations/enquiries/{life_id}/release',data={'csrf':csrf},follow_redirects=False).status_code==303
        with db.connect() as c:
            blocker=int(insert_sql(c, 'INSERT INTO bookings(company_id,reference,customer_id,status,workflow_status_id,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,created_at,updated_at) VALUES (:q92_0,:q92_1,:q92_2,:q92_3,:q92_4,:q92_5,:q92_6,:q92_7,:q92_8,:q92_9,:q92_10,:q92_11)',(cid,'DB-LIFECYCLE-BLOCK',life_customer,'confirmed',confirmed_id,'2035-07-01','2035-07-04','EUR',300.0,'{}',now,now)).lastrowid)
            execute_sql(c, 'INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json) VALUES (:q60_0,:q60_1,:q60_2,:q60_3,:q60_4,:q60_5,:q60_6,:q60_7,:q60_8)',(cid,blocker,element_id,'2035-07-01','2035-07-04','Per night',100.0,300.0,'{}'))
        refused=client.post(f'/operations/enquiries/{life_id}/reopen',data={'csrf':csrf},follow_redirects=False)
        assert refused.status_code==303 and 'Cannot+reopen' in refused.headers['location']
        with db.connect() as c:
            assert execute_sql(c, 'SELECT status FROM enquiries WHERE id=:q111_0',(life_id,)).fetchone()['status']=='closed'
            execute_sql(c, 'DELETE FROM booking_elements WHERE booking_id=:q61_0',(blocker,))
            execute_sql(c, 'DELETE FROM bookings WHERE id=:q62_0',(blocker,))
            life_actions=[str(r['action']) for r in execute_sql(c, "SELECT action FROM audit_log WHERE company_id=:q112_0 AND entity_type='enquiry' AND entity_id=:q112_1 ORDER BY id",(cid,str(life_id))).fetchall()]
        assert 'ENQUIRY_RELEASED' in life_actions and 'ENQUIRY_REOPENED' in life_actions
        page = client.get(f'/operations/bookings/{booking_id}')
        assert 'Booking History' in page.text and 'What happened' in page.text

        ops = client.get('/operations'); assert '/operations/bookings' in ops.text
        register = client.get('/operations/bookings'); assert reference in register.text

        with db.connect() as c:
            actions = [str(r['action']) for r in execute_sql(c, "SELECT action FROM audit_log WHERE company_id=:q113_0 AND entity_type='booking' AND entity_id=:q113_1", (cid, str(booking_id))).fetchall()]
            assert 'BOOKING_CREATED' in actions and 'BOOKING_PAYMENT_RECORDED' in actions and 'BOOKING_STATUS_AUTOMATIC' in actions

        # Terminal cancellation: explicit staff authority, inventory release and immutable history.
        review=client.get(f'/operations/bookings/{booking_id}/cancel')
        assert review.status_code==200 and 'Confirm Permanent Cancellation' in review.text
        refused=client.post(f'/operations/bookings/{booking_id}/cancel',data={
            'csrf':csrf,'reason':'Customer requested cancellation',
            'refund_decision':'no_refund','refund_amount':'0',
            'financial_note':'Non-refundable payment retained'
        },follow_redirects=False)
        assert refused.status_code==400
        cancelled=client.post(f'/operations/bookings/{booking_id}/cancel',data={
            'csrf':csrf,'reason':'Customer requested cancellation',
            'refund_decision':'no_refund','refund_amount':'0',
            'financial_note':'Non-refundable payment retained','acknowledge':'yes'
        },follow_redirects=False)
        assert cancelled.status_code==303
        with db.connect() as c:
            b=execute_sql(c, 'SELECT status FROM bookings WHERE company_id=:q93_0 AND id=:q93_1',(cid,booking_id)).fetchone()
            assert b['status']=='cancelled'
            assert execute_sql(c, 'SELECT COUNT(*) AS n FROM booking_cancellations WHERE company_id=:q114_0 AND booking_id=:q114_1',(cid,booking_id)).fetchone()['n']==1
            assert execute_sql(c, "SELECT COUNT(*) AS n FROM audit_log WHERE company_id=:q115_0 AND entity_type='booking' AND entity_id=:q115_1 AND action='BOOKING_CANCELLED'",(cid,str(booking_id))).fetchone()['n']==1
        cancelled_page=client.get(f'/operations/bookings/{booking_id}')
        assert cancelled_page.status_code==200 and 'Cancelled — permanent' in cancelled_page.text
        assert 'Review Cancellation' not in cancelled_page.text
        assert reference in client.get('/operations/bookings').text
        duplicate=client.post(f'/operations/bookings/{booking_id}/cancel',data={
            'csrf':csrf,'reason':'Second cancellation','refund_decision':'no_refund',
            'refund_amount':'0','financial_note':'Duplicate','acknowledge':'yes'
        },follow_redirects=False)
        assert duplicate.status_code==400
        unapproved_refund=client.post(f'/operations/bookings/{booking_id}/refunds',data={
            'csrf':csrf,'amount':'1','refund_date':'2035-06-01','method':'Bank transfer',
            'reference':'TEST-1','acknowledge':'yes'
        },follow_redirects=False)
        assert unapproved_refund.status_code==400
        assert client.get(f'/operations/bookings/{booking_id}/cancel',follow_redirects=False).status_code==303

        # Cancelled bookings release inventory and cannot accept accommodation amendments.
        released=availability_state(db,cid,element_id,'2035-06-10','2035-06-13')
        assert released['available'] is True, released
        try:
            amendment_quote(db,cid,booking_id,beid,'2035-06-10','2035-06-18')
            raise AssertionError('Cancelled booking accepted an amendment quote')
        except ValueError as exc:
            assert 'cannot be amended' in str(exc)
        rejected_amendment=client.post(f'/operations/bookings/{booking_id}/amend-preview',data={
            'csrf':csrf,'booking_element_id':str(beid),
            'arrival_date':'2035-06-10','departure_date':'2035-06-18','manual_discount':'0'
        },follow_redirects=False)
        assert rejected_amendment.status_code==303
        with db.connect() as c:
            assert execute_sql(c, 'SELECT status FROM bookings WHERE id=:q116_0',(booking_id,)).fetchone()['status']=='cancelled'


    print('Direct Booking Web V1 Booking workflow test: passed')


if __name__ == '__main__':
    main()
