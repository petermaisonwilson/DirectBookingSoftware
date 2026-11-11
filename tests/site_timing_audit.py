"""Audit real operational button requests and report uncovered route templates.

Run on disposable SQLite or with DBS_TEST_POSTGRES_URL for genuine PostgreSQL.
No customer data, cookies, query strings or form payloads are recorded.
"""
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory() as temp:
        log = Path(temp)/'requests.jsonl'
        env = dict(os.environ,DBS_TIMING_LOG=str(log))
        result = subprocess.run([sys.executable,'tests/run_operational_acceptance.py'],env=env)
        if result.returncode: raise SystemExit(result.returncode)
        previous = os.environ.get('DBS_TIMING_LOG')
        os.environ['DBS_TIMING_LOG'] = str(log)
        from operational_concurrency_test import OperationalJourneyTests
        fixture = OperationalJourneyTests(); fixture.setUp()
        try:
            enquiry = fixture.enquiry()
            rid = fixture.scalar('SELECT id FROM reservations WHERE company_id=:tenant AND enquiry_id=:id',(fixture.cid,enquiry))
            paths = ['/', '/docs','/redoc','/docs/oauth2-redirect','/openapi.json', '/operations/customers/new',f'/operations/customers/{fixture.customer}/edit',
                f'/operations/enquiries/{enquiry}/confirm','/operations/foundation',
                f'/operations/master-reservations/{rid}',f'/operations/reservations/{rid}/build',
                '/setup/addon-rules','/setup/booking-statuses','/setup/price-test']
            for path in paths:
                response = fixture.clients[0].get(path,follow_redirects=False)
                assert response.status_code < 400,(path,response.status_code,response.text[:200])
            fixture.book(enquiry)
            for suffix in ('customer-matches','cancel'):
                response = fixture.clients[0].get(f'/operations/reservations/{rid}/{suffix}',follow_redirects=False)
                assert response.status_code < 400,(suffix,response.status_code,response.text[:200])
            from fastapi.testclient import TestClient
            with TestClient(fixture.app) as supervisor:
                response = supervisor.post('/login',data={'email':'supervisor@directbooking.test','password':'Supervisor013!'},follow_redirects=False)
                assert response.status_code == 303,response.text
                response = supervisor.get('/audit')
                assert response.status_code == 200,response.text
            fixture.prepare()
            held = fixture.hold(element=fixture.new_element('Audit basket pitch'))
            assert held.status_code == 200,held.text
            response = fixture.clients[0].get('/availability/basket/edit',params={'hold_id':held.json()['hold']['id']})
            assert response.status_code == 200,response.text
        finally:
            fixture.tearDown()
            if previous is None: os.environ.pop('DBS_TIMING_LOG',None)
            else: os.environ['DBS_TIMING_LOG'] = previous
        records = [json.loads(line) for line in log.read_text().splitlines()]
        groups = {}; routes = set()
        for row in records:
            routes.update(row['routes'])
            groups.setdefault(row['method']+' '+row['route'],[]).append(row)
        results = []
        for route, samples in groups.items():
            successful = [r['seconds'] for r in samples if 200 <= r['status'] < 400]
            if successful and route in routes:
                ordered = sorted(successful)
                results.append({'route':route,'samples':len(successful),'median':round(statistics.median(ordered),4),
                    'p95':round(ordered[min(len(ordered)-1,int(.95*len(ordered)))],4),'max':round(max(ordered),4)})
        print('SITE TIMING AUDIT: full server response, local test conditions, excludes browser rendering/network')
        print(json.dumps({'requests':len(records),'successfully_exercised':len(results),'registered':len(routes)},sort_keys=True))
        for row in sorted(results,key=lambda r:r['max'],reverse=True): print(json.dumps(row,sort_keys=True))
        print('ROUTES WITHOUT A SUCCESSFUL RESPONSE (not claimed as verified):')
        successful_routes = {r['route'] for r in results}
        for route in sorted(routes-successful_routes): print(route)


if __name__ == '__main__': main()
