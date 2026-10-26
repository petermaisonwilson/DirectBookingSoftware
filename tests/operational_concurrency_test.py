"""Real HTTP journey, concurrent writers and audit-failure rollback on either backend."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from html.parser import HTMLParser
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from operational_backend import create_app
from fastapi.testclient import TestClient
from online.app import COOKIE_NAME
from online.database import iso_now
from online.sql import execute_sql, insert_sql
from online.webv1 import register_web_v1
from online.webv1_booking_amendments import amendment_quote
from online.webv1_status_availability import availability_state


class OperationalJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.app = create_app(Path(self.temp.name) / 'operational.sqlite', seed_demo=True)
        register_web_v1(self.app)
        self.db = self.app.state.database
        self.clients = [TestClient(self.app, raise_server_exceptions=False) for _ in range(2)]
        self.contexts = []
        for client in self.clients:
            self.assertEqual(client.post('/login', data={'email': 'operator@forestview.test',
                'password': 'Operator013!'}, follow_redirects=False).status_code, 303)
            self.contexts.append(self.db.session_context(client.cookies.get(COOKIE_NAME)))
        self.cid = int(self.contexts[0]['company_id'])
        with self.db.connect() as c:
            execute_sql(c, "INSERT INTO setup_element_types(company_id,name) VALUES (:tenant,'Camping')", (self.cid,))
            execute_sql(c, 'INSERT INTO setup_years(company_id,year) VALUES (:tenant,2038)', (self.cid,))
            self.season = int(insert_sql(c, "INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) "
                "VALUES (:tenant,2038,'Season','2038-01-01','2038-12-31')", (self.cid,)).lastrowid)
            self.person = int(insert_sql(c, "INSERT INTO setup_person_types(company_id,name,short_name) "
                "VALUES (:tenant,'Adult','A')", (self.cid,)).lastrowid)
            self.customer = int(insert_sql(c,
                'INSERT INTO customer_records(company_id,first_name,last_name,email,mobile_phone,created_at,updated_at) '
                "VALUES (:tenant,'Race','Customer','race@example.test','0612345678',:created,:updated)",
                (self.cid, iso_now(), iso_now())).lastrowid)
            self.cash = int(execute_sql(c, "SELECT id FROM payment_method_definitions WHERE company_id=:tenant AND name='Cash'",
                                       (self.cid,)).fetchone()['id'])
        self.element = self.new_element('Pitch A')
        print('Operational journey backend:', self.db.dialect_name)

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.temp.cleanup()

    def new_element(self, name):
        with self.db.connect() as c:
            eid = int(insert_sql(c, "INSERT INTO setup_elements(company_id,name,element_type,pricing_method) "
                "VALUES (:tenant,:name,'Camping','Per night')", (self.cid, name)).lastrowid)
            execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) '
                'VALUES (:tenant,2038,:element,:season,100)', (self.cid, eid, self.season))
            execute_sql(c, 'INSERT INTO setup_occupancy(company_id,year,element_id,max_total) '
                'VALUES (:tenant,2038,:element,2)', (self.cid, eid))
            execute_sql(c, 'INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) '
                'VALUES (:tenant,2038,:element,:person,2,0)', (self.cid, eid, self.person))
            execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) '
                'VALUES (:tenant,2038,:element,:person,0)', (self.cid, eid, self.person))
        return eid

    def csrf(self, index=0):
        return self.contexts[index]['csrf_token']

    def scalar(self, sql, params=()):
        with self.db.connect() as c:
            return execute_sql(c, sql, params).fetchone()[0]

    def prepare(self, index=0, lead='Customer'):
        response = self.clients[index].post('/availability/requirements', data={
            'csrf': self.csrf(index), 'lead_name': lead, 'element_type': 'Camping',
            'arrival': '2038-06-01', 'departure': '2038-06-03', f'person_{self.person}': '1'},
            follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)

    def hold(self, index=0, element=None):
        return self.clients[index].post('/availability/hold', data={'csrf': self.csrf(index),
            'element_id': element or self.element, 'arrival_date': '2038-06-01',
            'departure_date': '2038-06-03'})

    def basket_payload(self, hold_id, index=0):
        return {'csrf': self.csrf(index), 'hold_id': hold_id, 'existing_customer_id': self.customer,
                'first_name': 'Race', 'last_name': 'Customer', 'email': 'race@example.test',
                'mobile_phone': '0612345678', 'notes': 'HTTP acceptance journey'}

    def enquiry(self):
        self.prepare()
        held = self.hold()
        self.assertEqual(held.status_code, 200, held.text)
        response = self.clients[0].post('/availability/basket/customer',
            data=self.basket_payload(held.json()['hold']['id']), follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.assertRegex(response.headers['location'], r'/operations/enquiries/\d+')
        return int(re.search(r'/enquiries/(\d+)', response.headers['location']).group(1))

    def payment_payload(self):
        return {'csrf': self.csrf(), 'amount': '200', 'payment_date': '2038-05-01',
                'payment_method_id': self.cash, 'reference': 'Cash received'}

    def book(self, enquiry_id):
        response = self.clients[0].post(f'/operations/enquiries/{enquiry_id}/confirm-payment',
            data=self.payment_payload(), follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.assertRegex(response.headers['location'], r'/operations/bookings/\d+')
        return int(re.search(r'/bookings/(\d+)', response.headers['location']).group(1))

    def race(self, functions):
        barrier = Barrier(len(functions))
        def run(fn):
            barrier.wait(timeout=15)
            return fn()
        with ThreadPoolExecutor(max_workers=len(functions)) as executor:
            return list(executor.map(run, functions))

    def test_actual_launcher_uses_the_configured_database(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        environment = dict(os.environ, DIRECTBOOKING_DATABASE_URL=self.db.config.database_url,
            DIRECTBOOKING_ENV='test', DIRECTBOOKING_SEED_DEMO='0', DIRECTBOOKING_NO_BROWSER='1',
            DIRECTBOOKING_HOST='127.0.0.1', DIRECTBOOKING_PORT=str(port))
        log_path = Path(self.temp.name) / 'server.log'
        with log_path.open('w') as log:
            process = subprocess.Popen([sys.executable, 'run_online.py'], env=environment,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 25
                health = None
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                            health = json.loads(response.read())
                        break
                    except OSError:
                        time.sleep(0.1)
                self.assertIsNotNone(health, log_path.read_text())
                self.assertEqual(health['build'], '302')
                self.assertEqual(health['version'], 'v3')
                self.assertEqual(health['database_backend'], self.db.dialect_name)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_canonical_creation_calendar_and_tenant_isolation(self):
        second = self.new_element('Pitch B')
        self.assertEqual(self.clients[0].get('/operations/master-reservations').status_code, 200)
        page = self.clients[0].get('/operations/master-reservations/new')
        self.assertEqual(page.status_code, 200, page.text)
        response = self.clients[0].post('/operations/master-reservations/new', data={
            'csrf': self.csrf(), 'customer_id': self.customer, 'element_ids': [self.element, second],
            'arrival_date': '2038-07-01', 'departure_date': '2038-07-03', f'person_{self.person}': '1'},
            follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        url = response.headers['location']
        rid = int(url.rsplit('/', 1)[1])
        detail = self.clients[0].get(url)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn('Pitch A', detail.text)
        self.assertIn('Pitch B', detail.text)
        self.assertEqual(self.scalar('SELECT total_amount FROM reservations WHERE company_id=:tenant AND id=:reservation',
                                     (self.cid, rid)), 400)
        self.assertEqual(self.scalar('SELECT actor FROM reservation_history WHERE company_id=:tenant AND reservation_id=:reservation',
                                     (self.cid, rid)), 'Forest Operator')
        calendar = self.clients[0].get('/availability/calendar-v2', params={
            'start': '2038-07-01', 'date_to': '2038-07-05', 'element_type': 'Camping'})
        self.assertEqual(calendar.status_code, 200, calendar.text)
        self.assertIn('href="' + url + '"', calendar.text)
        self.assertFalse(availability_state(self.db, self.cid, self.element, '2038-07-01', '2038-07-03')['available'])
        with TestClient(self.app) as foreign:
            self.assertEqual(foreign.post('/login', data={'email': 'operator@riverside.test',
                'password': 'Operator013!'}, follow_redirects=False).status_code, 303)
            self.assertEqual(foreign.get(url).status_code, 404)
            foreign_context = self.db.session_context(foreign.cookies.get(COOKIE_NAME))
            response = foreign.post('/operations/master-reservations/new', data={
                'csrf': foreign_context['csrf_token'], 'customer_id': self.customer, 'element_ids': self.element,
                'arrival_date': '2038-07-01', 'departure_date': '2038-07-03'}, follow_redirects=False)
            self.assertEqual(response.status_code, 400)
        from online.webv1_reservation_service import change_reservation_state
        change_reservation_state(self.db, self.cid, rid, 'ENQUIRY_RELEASED', iso_now(), actor='Forest Operator')
        calendar = self.clients[0].get('/availability/calendar-v2', params={
            'start': '2038-07-01', 'date_to': '2038-07-05', 'element_type': 'Camping'})
        self.assertNotIn('href="' + url + '"', calendar.text)
        self.assertTrue(availability_state(self.db, self.cid, self.element, '2038-07-01', '2038-07-03')['available'])

    def test_confirmation_without_payment_retains_staff_reason_atomically(self):
        enquiry_id = self.enquiry()
        original = self.db.write_audit
        def fail_authorisation(**kwargs):
            if kwargs['action'] == 'BOOKING_CONFIRMED_WITHOUT_PAYMENT':
                raise RuntimeError('Injected authorisation history failure')
            return original(**kwargs)
        route = f'/operations/enquiries/{enquiry_id}/confirm-without-payment'
        with patch.object(self.db, 'write_audit', side_effect=fail_authorisation):
            response = self.clients[0].post(route, data={'csrf': self.csrf(), 'reason': 'Approved account'}, follow_redirects=False)
            self.assertEqual(response.status_code, 500, response.headers.get('location', response.text))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM bookings WHERE company_id=:tenant', (self.cid,)), 0)
        response = self.clients[0].post(route, data={'csrf': self.csrf(), 'reason': ''}, follow_redirects=False)
        self.assertIn('reason', response.headers['location'])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM bookings WHERE company_id=:tenant', (self.cid,)), 0)
        response = self.clients[0].post(route, data={'csrf': self.csrf(), 'reason': 'Approved account'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        payload = self.scalar("SELECT after_json FROM audit_log WHERE company_id=:tenant "
            "AND action='BOOKING_CONFIRMED_WITHOUT_PAYMENT'", (self.cid,))
        self.assertEqual(json.loads(payload)['reason'], 'Approved account')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM booking_payments WHERE company_id=:tenant', (self.cid,)), 0)

    def test_staff_calendar_snapshot_matches_authoritative_availability(self):
        canonical = self.new_element('Canonical pitch')
        legacy = self.new_element('Legacy enquiry pitch')
        closed = self.new_element('Closed pitch')
        unpriced = self.new_element('Unpriced pitch')
        booked = self.new_element('Booked pitch')
        now = iso_now()
        from online.webv1_reservation_service import create_enquiry
        create_enquiry(self.db, self.cid, 'CANONICAL-PARITY', self.customer,
            [{'element_id': canonical, 'arrival_date': '2038-06-03', 'departure_date': '2038-06-05', 'total_amount': 200}], now)
        with self.db.connect() as c:
            execute_sql(c, 'INSERT INTO element_holds(company_id,element_id,session_token,arrival_date,departure_date,'
                'renewal_required_at,expires_at,created_at,updated_at) '
                "VALUES (:tenant,:element,'foreign-session','2038-06-02','2038-06-04',:renewal,:expiry,:created,:updated)",
                (self.cid, self.element, '2999-01-01T00:00:00+00:00', '2999-01-01T00:00:00+00:00', now, now))
            eid = int(insert_sql(c, 'INSERT INTO enquiries(company_id,customer_id,status,arrival_date,departure_date,created_at,updated_at) '
                "VALUES (:tenant,:customer,'new','2038-06-01','2038-06-03',:created,:updated)", (self.cid, self.customer, now, now)).lastrowid)
            execute_sql(c, 'INSERT INTO enquiry_elements(company_id,enquiry_id,element_id,element_type,arrival_date,departure_date,created_at,updated_at) '
                "VALUES (:tenant,:enquiry,:element,'Camping','2038-06-01','2038-06-03',:created,:updated)", (self.cid, eid, legacy, now, now))
            execute_sql(c, 'UPDATE enquiries SET workflow_status_id=NULL WHERE company_id=:tenant AND id=:enquiry', (self.cid, eid))
            execute_sql(c, 'INSERT INTO element_closures(company_id,element_id,start_date,end_date,reason,created_at,updated_at) '
                "VALUES (:tenant,:element,'2038-06-04','2038-06-07','Closed',:created,:updated)", (self.cid, closed, now, now))
            execute_sql(c, 'DELETE FROM setup_element_rates WHERE company_id=:tenant AND element_id=:element', (self.cid, unpriced))
            bid = int(insert_sql(c, 'INSERT INTO bookings(company_id,customer_id,reference,status,arrival_date,departure_date,created_at,updated_at) '
                "VALUES (:tenant,:customer,'PARITY-BOOKING','confirmed','2038-06-04','2038-06-06',:created,:updated)",
                (self.cid, self.customer, now, now)).lastrowid)
            execute_sql(c, 'INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot) '
                "VALUES (:tenant,:booking,:element,'2038-06-04','2038-06-06','Per night')", (self.cid, bid, booked))
        response = self.clients[0].get('/availability/calendar-v2', params={
            'date_from': '2038-06-01', 'date_to': '2038-06-10'})
        self.assertEqual(response.status_code, 200, response.text)
        class Cells(HTMLParser):
            def __init__(self):
                super().__init__()
                self.current = None
                self.depth = 0
                self.values = {}
            def handle_starttag(self, tag, attributes):
                attributes = dict(attributes)
                classes = attributes.get('class', '').split()
                if tag == 'div':
                    if self.current is None and 'element-row' in classes:
                        self.current = int(attributes['data-element'])
                        self.depth = 1
                    elif self.current is not None:
                        self.depth += 1
                if self.current is not None and 'cal-cell' in classes and 'data-date' in attributes:
                    self.values[(self.current, attributes['data-date'])] = 'available' in classes
            def handle_endtag(self, tag):
                if tag == 'div' and self.current is not None:
                    self.depth -= 1
                    if not self.depth:
                        self.current = None
        cells = Cells()
        cells.feed(response.text)
        for element in (self.element, canonical, legacy, closed, unpriced, booked):
            for offset in range(10):
                day = date(2038, 6, 1) + timedelta(days=offset)
                state = availability_state(self.db, self.cid, element, day.isoformat(), (day + timedelta(days=1)).isoformat())
                self.assertEqual(cells.values.get((element, day.isoformat())), state['available'], (element, day, state))

    def test_full_http_journey_with_competing_writers(self):
        for index in range(2):
            self.prepare(index)
        responses = self.race([lambda index=index: self.hold(index) for index in range(2)])
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        winner = next(i for i, response in enumerate(responses) if response.status_code == 200)
        if winner:
            self.clients.reverse()
            self.contexts.reverse()
        hold_id = responses[winner].json()['hold']['id']
        response = self.clients[0].post('/availability/basket/customer',
            data=self.basket_payload(hold_id), follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        enquiry_id = int(re.search(r'/enquiries/(\d+)', response.headers['location']).group(1))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant', (self.cid,)), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM enquiries WHERE company_id=:tenant', (self.cid,)), 1)
        # Both submissions originate from the same authorised payment screen.
        self.clients[1].cookies.update(self.clients[0].cookies)
        responses = self.race([lambda client=client: client.post(
            f'/operations/enquiries/{enquiry_id}/confirm-payment', data=self.payment_payload(),
            follow_redirects=False) for client in self.clients])
        self.assertTrue(all(r.status_code == 303 for r in responses), [r.text for r in responses])
        self.assertEqual(sum('/operations/bookings/' in r.headers['location'] for r in responses), 1)
        booking_id = int(self.scalar('SELECT id FROM bookings WHERE company_id=:tenant AND enquiry_id=:enquiry',
                                     (self.cid, enquiry_id)))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM booking_payments WHERE company_id=:tenant AND booking_id=:booking',
                                     (self.cid, booking_id)), 1)
        beid = int(self.scalar('SELECT id FROM booking_elements WHERE company_id=:tenant AND booking_id=:booking',
                              (self.cid, booking_id)))
        quote = amendment_quote(self.db, self.cid, booking_id, beid, '2038-06-01', '2038-06-04')
        preview = self.clients[0].post(f'/operations/bookings/{booking_id}/amend-preview', data={
            'csrf': self.csrf(), 'booking_element_id': beid, 'arrival_date': '2038-06-01',
            'departure_date': '2038-06-04', 'manual_discount': '0'})
        self.assertEqual(preview.status_code, 200, preview.text)
        response = self.clients[0].post(f'/operations/bookings/{booking_id}/amend-apply',
            data={'csrf': self.csrf(), 'quote': json.dumps(quote)}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(self.scalar('SELECT total_amount FROM bookings WHERE company_id=:tenant AND id=:booking',
                                     (self.cid, booking_id)), 300)
        calendar = self.clients[0].get('/availability/calendar-v2', params={
            'start': '2038-06-01', 'end': '2038-06-07', 'element_type': 'Camping'})
        self.assertEqual(calendar.status_code, 200)
        self.assertIn('Customer', calendar.text)
        response = self.clients[0].post(f'/operations/bookings/{booking_id}/cancel', data={
            'csrf': self.csrf(), 'reason': 'Customer cancelled', 'refund_decision': 'full_refund',
            'refund_amount': '200', 'financial_note': 'Approved cash refund', 'acknowledge': 'yes'},
            follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        responses = self.race([lambda client=client: client.post(f'/operations/bookings/{booking_id}/refunds', data={
            'csrf': self.csrf(), 'amount': '150', 'refund_date': '2038-05-02', 'method': 'Cash',
            'reference': 'Refund receipt', 'acknowledge': 'yes'}, follow_redirects=False) for client in self.clients])
        self.assertEqual(sorted(r.status_code for r in responses), [303, 400])
        self.assertEqual(self.scalar('SELECT SUM(amount) FROM booking_refunds WHERE company_id=:tenant AND booking_id=:booking',
                                     (self.cid, booking_id)), 150)
        self.assertTrue(availability_state(self.db, self.cid, self.element, '2038-06-01', '2038-06-04')['available'])
        for action in ('BOOKING_CREATED','BOOKING_PAYMENT_RECORDED','BOOKING_AMENDED','BOOKING_CANCELLED','BOOKING_REFUND_RECORDED'):
            self.assertEqual(self.scalar("SELECT COUNT(*) FROM audit_log WHERE company_id=:tenant AND entity_type='booking' "
                'AND entity_id=:booking AND action=:action', (self.cid, str(booking_id), action)), 1, action)
        history = self.clients[0].get(f'/operations/bookings/{booking_id}')
        self.assertEqual(history.status_code, 200)
        self.assertIn('Cancelled', history.text)
        self.assertIn('Refund', history.text)

    def test_multi_element_booking_has_one_payment_balance_and_keeps_guest_names(self):
        response = self.clients[0].post('/company/settings', data={'csrf': self.csrf(),
            'contact_email': 'race@example.test', 'phone': '0612345678', 'currency': 'GBP'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        second = self.new_element('Pitch B')
        self.prepare(lead='Customer')
        first_hold = self.hold()
        self.assertEqual(first_hold.status_code, 200, first_hold.text)
        self.prepare(lead='Jones')
        second_hold = self.hold(element=second)
        self.assertEqual(second_hold.status_code, 200, second_hold.text)
        response = self.clients[0].post('/availability/basket/customer',
            data=self.basket_payload(first_hold.json()['hold']['id']), follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        enquiry_id = int(re.search(r'/enquiries/(\d+)', response.headers['location']).group(1))
        response = self.clients[0].post(f'/operations/enquiries/{enquiry_id}/confirm-payment',
            data=dict(self.payment_payload(), amount='400'), follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.assertIn('/operations/bookings/', response.headers['location'])
        bid = int(re.search(r'/bookings/(\d+)', response.headers['location']).group(1))
        with self.db.connect() as c:
            elements = execute_sql(c, 'SELECT id,element_id,lead_name,arrival_date,departure_date FROM booking_elements '
                'WHERE company_id=:tenant AND booking_id=:booking ORDER BY id', (self.cid, bid)).fetchall()
        self.assertEqual([element['lead_name'] for element in elements], ['Customer', 'Jones'])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM bookings WHERE company_id=:tenant', (self.cid,)), 1)
        self.assertEqual(self.scalar('SELECT currency FROM bookings WHERE company_id=:tenant AND id=:booking', (self.cid, bid)), 'GBP')
        self.assertEqual(self.scalar('SELECT SUM(amount) FROM booking_payments WHERE company_id=:tenant AND booking_id=:booking', (self.cid, bid)), 400)
        quote = amendment_quote(self.db, self.cid, bid, int(elements[0]['id']), '2038-06-01', '2038-06-04')
        response = self.clients[0].post(f'/operations/bookings/{bid}/amend-apply',
            data={'csrf': self.csrf(), 'quote': json.dumps(quote)}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.scalar('SELECT total_amount FROM bookings WHERE company_id=:tenant AND id=:booking', (self.cid, bid)), 500)
        self.assertEqual(self.scalar('SELECT departure_date FROM booking_elements WHERE company_id=:tenant AND id=:element',
            (self.cid, int(elements[1]['id']))), '2038-06-03')
        from online.webv1_booking_finance import booking_financials
        self.assertEqual(booking_financials(self.db, self.cid, bid)['outstanding'], 100)

    def test_competing_amendments_reject_a_superseded_preview(self):
        bid = self.book(self.enquiry())
        beid = int(self.scalar('SELECT id FROM booking_elements WHERE company_id=:tenant AND booking_id=:booking', (self.cid, bid)))
        quotes = [amendment_quote(self.db, self.cid, bid, beid, '2038-06-01', end)
                  for end in ('2038-06-04', '2038-06-05')]
        responses = self.race([lambda index=index: self.clients[index].post(f'/operations/bookings/{bid}/amend-apply',
            data={'csrf': self.csrf(index), 'quote': json.dumps(quotes[index])}, follow_redirects=False) for index in range(2)])
        self.assertTrue(all(response.status_code == 303 for response in responses))
        self.assertEqual(sum('Booking+amended' in response.headers['location'] for response in responses), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM booking_amendments WHERE company_id=:tenant AND booking_id=:booking', (self.cid, bid)), 1)
        self.assertIn(self.scalar('SELECT total_amount FROM bookings WHERE company_id=:tenant AND id=:booking', (self.cid, bid)), (300, 400))

    def test_concurrent_reopening_reholds_only_one_enquiry(self):
        ids = []
        for _ in range(2):
            eid = self.enquiry()
            response = self.clients[0].post(f'/operations/enquiries/{eid}/release',
                data={'csrf': self.csrf()}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            ids.append(eid)
        responses = self.race([lambda index=index: self.clients[index].post(f'/operations/enquiries/{ids[index]}/reopen',
            data={'csrf': self.csrf(index)}, follow_redirects=False) for index in range(2)])
        self.assertTrue(all(response.status_code == 303 for response in responses))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM enquiry_elements WHERE company_id=:tenant AND recovery_state='held'", (self.cid,)), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM enquiry_elements WHERE company_id=:tenant AND recovery_state='needs_replacement'", (self.cid,)), 1)

    def test_hold_and_enquiry_history_failures_leave_no_partial_allocations(self):
        self.prepare()
        original = self.db.write_audit
        def fail_hold(**kwargs):
            if kwargs['action'] == 'ELEMENT_HOLD_SAVED':
                raise RuntimeError('Injected hold history failure')
            return original(**kwargs)
        with patch.object(self.db, 'write_audit', side_effect=fail_hold):
            response = self.hold()
            self.assertEqual(response.status_code, 500, response.headers.get('location', response.text))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant', (self.cid,)), 0)
        response = self.hold()
        self.assertEqual(response.status_code, 200, response.text)
        hold_id = response.json()['hold']['id']
        def fail_enquiry(**kwargs):
            if kwargs['action'] == 'ENQUIRY_CREATED':
                raise RuntimeError('Injected enquiry history failure')
            return original(**kwargs)
        with patch.object(self.db, 'write_audit', side_effect=fail_enquiry):
            response = self.clients[0].post('/availability/basket/customer',
                data=self.basket_payload(hold_id), follow_redirects=False)
            self.assertEqual(response.status_code, 500, response.headers.get('location', response.text))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM enquiries WHERE company_id=:tenant', (self.cid,)), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant', (self.cid,)), 1)
        responses = self.race([lambda: self.clients[0].post('/availability/basket/customer',
            data=self.basket_payload(hold_id), follow_redirects=False) for _ in range(2)])
        self.assertEqual(sum('/operations/enquiries/' in response.headers.get('location', '') for response in responses), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM enquiries WHERE company_id=:tenant', (self.cid,)), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant', (self.cid,)), 0)

    def test_hold_removal_cannot_delete_another_session_or_tenants_party(self):
        self.prepare()
        response = self.hold()
        self.assertEqual(response.status_code, 200, response.text)
        hid = response.json()['hold']['id']
        before = self.scalar('SELECT SUM(quantity) FROM hold_requirement_people WHERE company_id=:tenant AND hold_id=:hold', (self.cid, hid))
        self.assertEqual(before, 1)
        response = self.clients[1].post('/availability/basket/remove-view',
            data={'csrf': self.csrf(1), 'hold_id': hid}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.scalar('SELECT SUM(quantity) FROM hold_requirement_people WHERE company_id=:tenant AND hold_id=:hold', (self.cid, hid)), before)
        with TestClient(self.app) as foreign:
            self.assertEqual(foreign.post('/login', data={'email': 'operator@riverside.test',
                'password': 'Operator013!'}, follow_redirects=False).status_code, 303)
            context = self.db.session_context(foreign.cookies.get(COOKIE_NAME))
            response = foreign.post('/availability/basket/remove-view',
                data={'csrf': context['csrf_token'], 'hold_id': hid}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
        self.assertEqual(self.scalar('SELECT SUM(quantity) FROM hold_requirement_people WHERE company_id=:tenant AND hold_id=:hold', (self.cid, hid)), before)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant AND id=:hold', (self.cid, hid)), 1)
        response = self.clients[0].post('/availability/basket/remove-view',
            data={'csrf': self.csrf(), 'hold_id': hid}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant AND id=:hold', (self.cid, hid)), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM hold_requirement_people WHERE company_id=:tenant AND hold_id=:hold', (self.cid, hid)), 0)

    def test_expired_hold_cleanup_is_tenant_scoped(self):
        with self.db.connect() as c:
            other = int(execute_sql(c, "SELECT id FROM companies WHERE name='Riverside Cabins'").fetchone()['id'])
            eid = int(insert_sql(c, "INSERT INTO setup_elements(company_id,name,element_type,pricing_method) "
                "VALUES (:tenant,'Foreign pitch','Camping','Per night')", (other,)).lastrowid)
            hid = int(insert_sql(c, 'INSERT INTO element_holds(company_id,element_id,session_token,arrival_date,departure_date,'
                'renewal_required_at,expires_at,created_at,updated_at) '
                "VALUES (:tenant,:element,'foreign','2038-06-01','2038-06-03',:renewal,:expiry,:created,:updated)",
                (other, eid, '2000-01-01', '2000-01-01', iso_now(), iso_now())).lastrowid)
        self.prepare()
        self.assertEqual(self.hold().status_code, 200)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant AND id=:hold', (other, hid)), 1)

    def test_history_failure_rolls_back_booking_payment_amendment_and_cancellation(self):
        enquiry_id = self.enquiry()
        original = self.db.write_audit
        def fail_action(action):
            def write(**kwargs):
                if kwargs['action'] == action:
                    raise RuntimeError('Injected history failure')
                return original(**kwargs)
            return patch.object(self.db, 'write_audit', side_effect=write)
        with fail_action('BOOKING_PAYMENT_RECORDED'):
            response = self.clients[0].post(f'/operations/enquiries/{enquiry_id}/confirm-payment',
                data=self.payment_payload(), follow_redirects=False)
            self.assertEqual(response.status_code, 500, response.headers.get('location', response.text))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM bookings WHERE company_id=:tenant', (self.cid,)), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM booking_payments WHERE company_id=:tenant', (self.cid,)), 0)
        self.assertEqual(self.scalar('SELECT status FROM enquiries WHERE company_id=:tenant AND id=:enquiry',
                                     (self.cid, enquiry_id)), 'new')
        booking_id = self.book(enquiry_id)
        with fail_action('BOOKING_PAYMENT_RECORDED'):
            response = self.clients[0].post(f'/operations/bookings/{booking_id}/payments',
                data=dict(self.payment_payload(), amount='1'), follow_redirects=False)
            self.assertEqual(response.status_code, 500, response.headers.get('location', response.text))
        self.assertEqual(self.scalar('SELECT SUM(amount) FROM booking_payments WHERE company_id=:tenant AND booking_id=:booking',
                                     (self.cid, booking_id)), 200)
        beid = int(self.scalar('SELECT id FROM booking_elements WHERE company_id=:tenant AND booking_id=:booking',
                              (self.cid, booking_id)))
        quote = amendment_quote(self.db, self.cid, booking_id, beid, '2038-06-01', '2038-06-04')
        with fail_action('BOOKING_AMENDED'):
            response = self.clients[0].post(f'/operations/bookings/{booking_id}/amend-apply',
                data={'csrf': self.csrf(), 'quote': json.dumps(quote)}, follow_redirects=False)
            self.assertEqual(response.status_code, 500, response.headers.get('location', response.text))
        self.assertEqual(self.scalar('SELECT total_amount FROM bookings WHERE company_id=:tenant AND id=:booking',
                                     (self.cid, booking_id)), 200)
        with fail_action('BOOKING_CANCELLED'):
            response = self.clients[0].post(f'/operations/bookings/{booking_id}/cancel', data={
                'csrf': self.csrf(), 'reason': 'Customer cancelled', 'refund_decision': 'no_refund',
                'refund_amount': '0', 'financial_note': 'Retain payment', 'acknowledge': 'yes'}, follow_redirects=False)
            self.assertEqual(response.status_code, 500, response.headers.get('location', response.text))
        self.assertNotEqual(self.scalar('SELECT status FROM bookings WHERE company_id=:tenant AND id=:booking',
                                        (self.cid, booking_id)), 'cancelled')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM booking_cancellations WHERE company_id=:tenant', (self.cid,)), 0)
        response = self.clients[0].post(f'/operations/bookings/{booking_id}/cancel', data={
            'csrf': self.csrf(), 'reason': 'Customer cancelled', 'refund_decision': 'full_refund',
            'refund_amount': '200', 'financial_note': 'Approved refund', 'acknowledge': 'yes'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        with fail_action('BOOKING_REFUND_RECORDED'):
            response = self.clients[0].post(f'/operations/bookings/{booking_id}/refunds', data={
                'csrf': self.csrf(), 'amount': '100', 'refund_date': '2038-05-02',
                'method': 'Cash', 'reference': 'Receipt', 'acknowledge': 'yes'}, follow_redirects=False)
            self.assertEqual(response.status_code, 500, response.headers.get('location', response.text))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM booking_refunds WHERE company_id=:tenant', (self.cid,)), 0)



if __name__ == '__main__':
    unittest.main(verbosity=2)
