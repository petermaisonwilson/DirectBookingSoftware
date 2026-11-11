"""Measure the editing stages on disposable data on either genuine backend.

Query budgets, rather than machine-specific timing limits, prevent per-cell and
per-Element setup query growth. --baseline prints the old implementation too.
"""
from contextlib import contextmanager
from time import perf_counter
import json
import re
import sys
from sqlalchemy import event
from operational_concurrency_test import OperationalJourneyTests
from online.sql import execute_sql, insert_sql


def main():
    fixture = OperationalJourneyTests()
    fixture.setUp()
    try:
        enquiry = fixture.enquiry()
        rid = fixture.scalar('SELECT id FROM reservations WHERE company_id=:tenant AND enquiry_id=:id',(fixture.cid,enquiry))
        aid = fixture.scalar('SELECT id FROM reservation_elements WHERE reservation_id=:id',(rid,))
        for i in range(29): fixture.new_element(f'Performance pitch {i+2:02d}')
        with fixture.db.connect() as c:
            execute_sql(c,'INSERT INTO setup_years(company_id,year) VALUES (:tenant,2039)',(fixture.cid,))
            for i in range(5):
                addon = int(insert_sql(c,"INSERT INTO setup_addons(company_id,name,pricing_method,ask_before_availability) VALUES (:tenant,:name,'Fixed once',1)",(fixture.cid,f'Performance extra {i}')).lastrowid)
                execute_sql(c,"INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:tenant,2038,'Camping',:addon,1,0,5,2)",(fixture.cid,addon))
        queries = []
        db = fixture.db
        original = db.connect
        if db.dialect_name == 'sqlite':
            @contextmanager
            def counted():
                with original() as c:
                    c.set_trace_callback(queries.append)
                    yield c
            db.connect = counted
        else:
            def counted_sql(c,cursor,statement,parameters,context,executemany): queries.append(statement)
            event.listen(db.engine,'before_cursor_execute',counted_sql)
        samples = {}
        def measure(name, method, url, **kwargs):
            queries.clear()
            started = perf_counter()
            response = getattr(fixture.clients[0],method)(url,**kwargs)
            elapsed = perf_counter()-started
            assert response.status_code in (200,303),(name,response.status_code,response.text[:200])
            count = sum(q.lstrip().upper().startswith('SELECT') for q in queries)
            samples[name] = {'seconds':round(elapsed,4),'selects':count}
            print(name,json.dumps(samples[name]),flush=True)
            return response
        measure('requirements','get','/availability/start')
        measure('new_booking','post','/availability/new-booking',data={'csrf':fixture.csrf()})
        measure('calendar','get',f'/availability/calendar-v2?edit_reservation={rid}')
        measure('calendar_edit_60_days','get',f'/availability/calendar-v2?edit_reservation={rid}&edit_allocation={aid}&date_from=2038-06-01&date_to=2038-07-30')
        form = measure('review','get',f'/operations/reservations/{rid}/edit?details=1&allocation_id={aid}')
        token = re.search(r'name="version" value="([^"]+)"',form.text).group(1)
        payload = {'csrf':fixture.csrf(),'allocation_id':aid,'version':token,'element_id':fixture.element,
            'element_type':'Camping','arrival_date':'2038-06-01','departure_date':'2038-06-03',f'person_{fixture.person}':'1'}
        measure('preview','post',f'/operations/reservations/{rid}/edit',data=dict(payload,action='calculate'))
        measure('save','post',f'/operations/reservations/{rid}/edit',data=dict(payload,action='save'),follow_redirects=False)
        summary = measure('summary','get',f'/operations/reservations/{rid}')
        assert 'Performance pitch' not in summary.text
        print(json.dumps({'backend':db.dialect_name,'elements':30,'days':60,'stages':samples}),flush=True)
        if '--baseline' not in sys.argv:
            assert samples['requirements']['selects'] < 80, samples
            assert samples['new_booking']['selects'] < 100, samples
            assert samples['calendar_edit_60_days']['selects'] < 2500, samples
            assert samples['review']['selects'] < 200, samples
            assert samples['preview']['selects'] < 400, samples
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    main()
