"""Real HTTP journey, concurrent writers and audit-failure rollback on either backend."""
from concurrent.futures import ThreadPoolExecutor
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

    def prepare(self, index=0):
        response = self.clients[index].post('/availability/requirements', data={
            'csrf': self.csrf(index), 'lead_name': 'Customer', 'element_type': 'Camping',
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

    def test_hold_and_enquiry_history_failures_leave_no_partial_allocations(self):
        self.prepare()
        original = self.db.write_audit
        def fail_hold(**kwargs):
            if kwargs['action'] == 'ELEMENT_HOLD_SAVED':
                raise RuntimeError('Injected hold history failure')
            return original(**kwargs)
        with patch.object(self.db, 'write_audit', side_effect=fail_hold):
            response = self.hold()
            self.assertEqual(response.status_code, 500)
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
            self.assertEqual(response.status_code, 500)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM enquiries WHERE company_id=:tenant', (self.cid,)), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant', (self.cid,)), 1)
        responses = self.race([lambda: self.clients[0].post('/availability/basket/customer',
            data=self.basket_payload(hold_id), follow_redirects=False) for _ in range(2)])
        self.assertEqual(sum('/operations/enquiries/' in response.headers.get('location', '') for response in responses), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM enquiries WHERE company_id=:tenant', (self.cid,)), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant', (self.cid,)), 0)

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
            self.assertEqual(response.status_code, 500)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM bookings WHERE company_id=:tenant', (self.cid,)), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM booking_payments WHERE company_id=:tenant', (self.cid,)), 0)
        self.assertEqual(self.scalar('SELECT status FROM enquiries WHERE company_id=:tenant AND id=:enquiry',
                                     (self.cid, enquiry_id)), 'new')
        booking_id = self.book(enquiry_id)
        with fail_action('BOOKING_PAYMENT_RECORDED'):
            response = self.clients[0].post(f'/operations/bookings/{booking_id}/payments',
                data=dict(self.payment_payload(), amount='1'), follow_redirects=False)
            self.assertEqual(response.status_code, 500)
        self.assertEqual(self.scalar('SELECT SUM(amount) FROM booking_payments WHERE company_id=:tenant AND booking_id=:booking',
                                     (self.cid, booking_id)), 200)
        beid = int(self.scalar('SELECT id FROM booking_elements WHERE company_id=:tenant AND booking_id=:booking',
                              (self.cid, booking_id)))
        quote = amendment_quote(self.db, self.cid, booking_id, beid, '2038-06-01', '2038-06-04')
        with fail_action('BOOKING_AMENDED'):
            response = self.clients[0].post(f'/operations/bookings/{booking_id}/amend-apply',
                data={'csrf': self.csrf(), 'quote': json.dumps(quote)}, follow_redirects=False)
            self.assertEqual(response.status_code, 500)
        self.assertEqual(self.scalar('SELECT total_amount FROM bookings WHERE company_id=:tenant AND id=:booking',
                                     (self.cid, booking_id)), 200)
        with fail_action('BOOKING_CANCELLED'):
            response = self.clients[0].post(f'/operations/bookings/{booking_id}/cancel', data={
                'csrf': self.csrf(), 'reason': 'Customer cancelled', 'refund_decision': 'no_refund',
                'refund_amount': '0', 'financial_note': 'Retain payment', 'acknowledge': 'yes'}, follow_redirects=False)
            self.assertEqual(response.status_code, 500)
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
            self.assertEqual(response.status_code, 500)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM booking_refunds WHERE company_id=:tenant', (self.cid,)), 0)



if __name__ == '__main__':
    unittest.main(verbosity=2)
