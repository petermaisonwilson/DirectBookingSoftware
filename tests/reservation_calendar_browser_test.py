"""Exercise the real calendar UI in installed Chrome on the Windows CI runner."""
import re
import socket
import threading
import time
from urllib.request import urlopen

import uvicorn
from playwright.sync_api import sync_playwright
from operational_concurrency_test import OperationalJourneyTests
from online.app import COOKIE_NAME


def main():
    fixture = OperationalJourneyTests()
    fixture.setUp()
    server = None
    thread = None
    try:
        enquiry = fixture.enquiry()
        rid = fixture.scalar('SELECT id FROM reservations WHERE company_id=:tenant AND enquiry_id=:id',(fixture.cid,enquiry))
        aid = fixture.scalar('SELECT id FROM reservation_elements WHERE reservation_id=:id',(rid,))
        second = fixture.new_element('Browser additional pitch')
        third = fixture.new_element('Browser blocked pitch')
        unsuitable = fixture.new_element('Browser unsuitable pitch')
        fishing = fixture.new_element('Browser fishing peg')
        from online.sql import execute_sql
        with fixture.db.connect() as c:
            execute_sql(c,"INSERT INTO setup_element_types(company_id,name) VALUES (:tenant,'Fishing')",(fixture.cid,))
            execute_sql(c,"UPDATE setup_elements SET element_type='Fishing',pricing_method='Per day' WHERE company_id=:tenant AND id=:id",(fixture.cid,fishing))
            execute_sql(c,'UPDATE setup_person_limits SET min_count=1,max_count=1 WHERE company_id=:tenant AND element_id=:id',(fixture.cid,fishing))
            execute_sql(c,'UPDATE setup_occupancy SET max_total=1 WHERE company_id=:tenant AND element_id=:id',(fixture.cid,fishing))
            execute_sql(c,'UPDATE setup_occupancy SET max_total=0 WHERE company_id=:tenant AND element_id=:id',(fixture.cid,unsuitable))
        closure = fixture.clients[0].post('/setup/elements/availability/save',data={
            'csrf':fixture.csrf(),'element_id':third,'start_date':'2038-06-02',
            'end_date':'2038-06-03','reason':'Browser test closure'},follow_redirects=False)
        assert closure.status_code == 303
        sock = socket.socket()
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(fixture.app,host='127.0.0.1',port=port,log_level='error'))
        thread = threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic()+20
        while not server.started:
            if time.monotonic() > deadline: raise AssertionError('Browser test application did not start')
            time.sleep(.05)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome',headless=True)
            context = browser.new_context(viewport={'width':1440,'height':1000})
            context.add_cookies([{'name':COOKIE_NAME,'value':fixture.clients[0].cookies.get(COOKIE_NAME),'url':base}])
            page = context.new_page()
            errors = []
            page.on('pageerror',lambda error: errors.append(str(error)))
            # Empty Master Calendar selection must collect a name and party first.
            fixture.clients[0].post('/availability/new-booking',data={'csrf':fixture.csrf()},follow_redirects=False)
            page.goto(base+'/availability/calendar-v2?start=2038-06-14')
            page.locator(f'.element-row[data-element="{second}"] .date-pick[data-date="2038-06-14"]').click()
            page.wait_for_url(re.compile('/availability/start'))
            assert fixture.scalar('SELECT COUNT(*) FROM element_holds WHERE company_id=:tenant',(fixture.cid,)) == 0
            assert page.locator('[name="lead_name"]').input_value() == ''
            assert page.locator('[name="lead_name"]').get_attribute('required') is not None
            assert page.locator('[name="element_type"]').input_value() == 'Camping'
            page.locator('[name="lead_name"]').fill('Calendar Guest')
            page.locator('[name="arrival"]').fill('2038-06-14')
            page.locator('[name="departure"]').fill('2038-06-16')
            page.locator(f'[name="person_{fixture.person}"]').fill('2')
            page.get_by_role('button',name='SEARCH AVAILABILITY',exact=True).click()
            page.wait_for_url(re.compile('/availability/calendar-v2'))
            assert 'party-unsuitable' in page.locator(f'.element-row[data-element="{unsuitable}"]').get_attribute('class')
            row = page.locator(f'.element-row[data-element="{second}"]')
            # Requirements already supplied the dates, so use the offered range.
            # The RESERVE button intentionally covers those selected calendar cells.
            assert page.locator('#arrival-date').input_value() == '2038-06-14'
            assert page.locator('#departure-date').input_value() == '2038-06-16'
            assert row.locator('.selection-action').is_visible()
            row.locator('.selection-action').click()
            page.wait_for_load_state('networkidle')
            assert fixture.scalar('SELECT lead_name FROM element_holds WHERE company_id=:tenant',(fixture.cid,)) == 'Calendar Guest'
            page.get_by_role('link',name='Add New Element',exact=True).first.click()
            assert page.locator('[name="lead_name"]').input_value() == 'Calendar Guest'
            page.locator('[name="element_type"]').select_option('Fishing')
            quantity = page.locator(f'[name="person_{fixture.person}"]')
            assert quantity.input_value() == '1'
            assert quantity.get_attribute('max') == '1'
            assert quantity.evaluate('(input)=>input.checkValidity()')
            assert page.locator('[name="arrival"]').input_value() == '2038-06-14'
            assert page.locator('[name="departure"]').input_value() == '2038-06-16'
            page.get_by_role('button',name='SEARCH AVAILABILITY',exact=True).click()
            page.wait_for_url(re.compile('/availability/calendar-v2'))
            page.locator(f'.element-row[data-element="{fishing}"] .selection-action').click()
            page.wait_for_load_state('networkidle')
            assert fixture.scalar('SELECT p.quantity FROM hold_requirement_people p JOIN element_holds h ON h.id=p.hold_id AND h.company_id=p.company_id WHERE h.company_id=:tenant AND h.element_id=:element AND p.person_type_id=:person',(fixture.cid,second,fixture.person)) == 2
            assert fixture.scalar('SELECT p.quantity FROM hold_requirement_people p JOIN element_holds h ON h.id=p.hold_id AND h.company_id=p.company_id WHERE h.company_id=:tenant AND h.element_id=:element AND p.person_type_id=:person',(fixture.cid,fishing,fixture.person)) == 1
            fixture.clients[0].post('/availability/new-booking',data={'csrf':fixture.csrf()},follow_redirects=False)
            page.goto(base+f'/operations/reservations/{rid}/edit')
            assert f'edit_reservation={rid}' in page.url
            assert page.get_by_role('heading',name='Master Calendar',exact=True).count() == 1
            # The highlighted allocation opens calendar editing, never a new basket.
            page.locator('.cal-bar.reservation-focus a').first.click()
            page.wait_for_url(re.compile(f'edit_allocation={aid}'))
            row = page.locator(f'.element-row[data-element="{fixture.element}"]')
            row.locator('.date-pick[data-date="2038-06-01"]').click()
            row.locator('.date-pick[data-date="2038-06-02"]').click()
            row.locator('.selection-action').click()
            page.wait_for_url(re.compile('details=1'))
            assert page.locator('[name="arrival_date"]').input_value() == '2038-06-01'
            assert page.locator('[name="departure_date"]').input_value() == '2038-06-03'
            assert page.get_by_text('EDIT EVERYTHING',exact=True).count() == 0
            page.get_by_role('link',name='Back to Master Calendar').click()
            # Add via free calendar space, then submit the existing requirements form.
            page.get_by_role('link',name='Add New Element',exact=True).first.click()
            occupied = page.locator(f'.element-row[data-element="{fixture.element}"] .date-pick[data-date="2038-06-01"]')
            assert occupied.count() == 0
            row = page.locator(f'.element-row[data-element="{second}"]')
            row.locator('.date-pick[data-date="2038-06-01"]').click()
            row.locator('.date-pick[data-date="2038-06-02"]').click()
            row.locator('.selection-action').click()
            page.wait_for_url(re.compile('details=1'))
            assert int(page.locator('[name="element_id"]').input_value()) == second
            assert page.locator(f'[name="person_{fixture.person}"]').input_value() == '1'
            page.get_by_role('button',name='Save Element',exact=True).click()
            page.wait_for_url(re.compile(f'edit_reservation={rid}'))
            assert fixture.scalar('SELECT COUNT(*) FROM enquiry_elements WHERE enquiry_id=:id',(enquiry,)) == 2
            assert fixture.scalar('SELECT element_id FROM reservation_elements WHERE id=:id',(aid,)) == fixture.element
            page.get_by_role('link',name='Return to Enquiry',exact=True).click()
            page.wait_for_url(re.compile(f'/operations/reservations/{rid}$'))
            actions=page.locator('.enquiry-actions')
            confirm=actions.get_by_role('button',name='CONFIRM BOOKING',exact=True)
            release=actions.get_by_role('button',name='Release Enquiry',exact=True)
            assert confirm.bounding_box()['x'] < release.bounding_box()['x']
            assert actions.bounding_box()['y'] < page.get_by_role('heading',name='Customer',exact=True).bounding_box()['y']
            confirm.click()
            assert page.get_by_role('heading',name='Confirm Booking',exact=True).count() == 1
            assert fixture.scalar('SELECT COUNT(*) FROM bookings WHERE enquiry_id=:id',(enquiry,)) == 0
            page.goto(base+f'/availability/calendar-v2?edit_reservation={rid}')
            # Selecting endpoints around an occupied day must never offer a save.
            page.get_by_role('link',name='Add New Element',exact=True).first.click()
            dialogs = []
            def accept(dialog):
                dialogs.append(dialog.message)
                dialog.accept()
            page.on('dialog',accept)
            row = page.locator(f'.element-row[data-element="{third}"]')
            row.locator('.date-pick[data-date="2038-06-01"]').click()
            row.locator('.date-pick[data-date="2038-06-03"]').click()
            assert dialogs and 'not available for every day' in dialogs[-1]
            assert row.locator('.selection-action').is_hidden()
            # Review dates show the final service day for fishing, retaining the
            # exclusive boundary used by pricing and inventory after preview/save.
            from online.sql import execute_sql
            with fixture.db.connect() as c:
                execute_sql(c,"UPDATE setup_elements SET pricing_method='Per day' WHERE company_id=:tenant AND id=:id",(fixture.cid,fixture.element))
            page.goto(base+f'/operations/reservations/{rid}/edit?details=1&allocation_id={aid}&chosen_element={fixture.element}&arrival=2038-06-14&departure=2038-06-18')
            assert page.locator('[name="display_departure_date"]').input_value() == '2038-06-17'
            assert page.locator('[name="departure_date"]').input_value() == '2038-06-18'
            page.locator('[name="display_departure_date"]').fill('2038-06-16')
            page.get_by_role('button',name='Calculate provisional price',exact=True).click()
            assert page.locator('[name="display_departure_date"]').input_value() == '2038-06-16'
            assert page.locator('[name="departure_date"]').input_value() == '2038-06-17'
            page.get_by_role('button',name='Save Element',exact=True).click()
            page.wait_for_url(re.compile(f'edit_reservation={rid}'))
            assert fixture.scalar('SELECT departure_date FROM reservation_elements WHERE id=:id',(aid,)) == '2038-06-17'
            page.goto(base+f'/operations/reservations/{rid}/edit?details=1&allocation_id={aid}')
            assert page.locator('[name="display_departure_date"]').input_value() == '2038-06-16'
            page.get_by_role('link',name='Return to Enquiry',exact=True).click()
            summary=page.locator('main').inner_text()
            assert re.search(r'End date changed from \d{2}/\d{2}/2038 to 16/06/2038',summary)
            assert 'Departure: 16/06/2038' in summary
            assert not errors, errors
            print('Chrome calendar reservation edit, add, occupied dates and range-gap safeguards passed.')
            browser.close()
    finally:
        if server: server.should_exit = True
        if thread: thread.join(timeout=10)
        fixture.tearDown()


if __name__ == '__main__':
    main()
