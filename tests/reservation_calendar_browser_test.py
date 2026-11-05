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
            assert not errors, errors
            print('Chrome calendar reservation edit, add, occupied dates and range-gap safeguards passed.')
            browser.close()
    finally:
        if server: server.should_exit = True
        if thread: thread.join(timeout=10)
        fixture.tearDown()


if __name__ == '__main__':
    main()
