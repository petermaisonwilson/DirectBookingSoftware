"""Identical disposable SQLite calendar fixture for before/after source comparison."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

parser = argparse.ArgumentParser()
parser.add_argument('--source-root', default=str(Path(__file__).resolve().parents[1]))
args = parser.parse_args()
sys.path.insert(0, args.source_root)
from fastapi.testclient import TestClient
from online.app import create_app
from online.database import iso_now
from online.webv1 import register_web_v1

with TemporaryDirectory() as directory:
    app = create_app(Path(directory) / 'calendar.sqlite', seed_demo=True)
    register_web_v1(app)
    db = app.state.database
    with db.connect() as c:
        cid = int(c.execute("SELECT id FROM companies WHERE name='Forest View Campsite'").fetchone()['id'])
        c.execute("INSERT INTO setup_element_types(company_id,name) VALUES (?,?)", (cid, 'Camping'))
        c.execute('INSERT INTO setup_years(company_id,year) VALUES (?,2038)', (cid,))
        season = int(c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,2038,'Season','2038-01-01','2038-12-31')", (cid,)).lastrowid)
        person = int(c.execute("INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,'Adult','A')", (cid,)).lastrowid)
        customer = int(c.execute("INSERT INTO customer_records(company_id,first_name,last_name,created_at,updated_at) VALUES (?,'Test','Guest',?,?)", (cid, iso_now(), iso_now())).lastrowid)
        status = int(c.execute("SELECT id FROM booking_status_definitions WHERE company_id=? AND internal_state='CONFIRMED' ORDER BY id LIMIT 1", (cid,)).fetchone()['id'])
        for n in range(30):
            eid = int(c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method) VALUES (?,?,'Camping','Per night')", (cid, f'Pitch {n+1:02d}')).lastrowid)
            c.execute('INSERT INTO setup_element_rates VALUES (?,2038,?,?,20)', (cid, eid, season))
            c.execute('INSERT INTO setup_occupancy VALUES (?,2038,?,6)', (cid, eid))
            c.execute('INSERT INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (?,2038,?,?,6,0)', (cid, eid, person))
            c.execute('INSERT INTO setup_person_prices VALUES (?,2038,?,?,0)', (cid, eid, person))
            if n < 10:
                bid = int(c.execute("INSERT INTO bookings(company_id,reference,customer_id,status,workflow_status_id,arrival_date,departure_date,created_at,updated_at) VALUES (?,?,?,'confirmed',?,'2038-06-10','2038-06-20',?,?)", (cid, f'BENCH-{n}', customer, status, iso_now(), iso_now())).lastrowid)
                c.execute("INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot) VALUES (?,?,?,'2038-06-10','2038-06-20','Per night')", (cid, bid, eid))
    client = TestClient(app)
    assert client.post('/login', data={'email':'operator@forestview.test','password':'Operator013!'}, follow_redirects=False).status_code == 303
    original = db.connect
    queries = []
    @contextmanager
    def counted():
        with original() as c:
            c.set_trace_callback(queries.append)
            yield c
    db.connect = counted
    times = []
    counts = []
    hashes = []
    for _ in range(3):
        queries.clear()
        started = perf_counter()
        result = client.get('/availability/calendar-v2', params={'date_from':'2038-06-01','date_to':'2038-07-30'})
        times.append(perf_counter()-started)
        assert result.status_code == 200
        counts.append(sum(q.lstrip().upper().startswith('SELECT') for q in queries))
        # Ignore the per-session CSRF and generated UI nonce by counting the
        # observable inventory/selection records rather than byte identity.
        hashes.append({'booking_bars':result.text.count('BENCH-'), 'available_cells':result.text.count('class="cal-cell available date-pick')})
    print(json.dumps({'source':args.source_root,'elements':30,'days':60,'seconds':times,
        'median_seconds':statistics.median(times),'select_counts':counts,'visible_inventory':hashes}))
