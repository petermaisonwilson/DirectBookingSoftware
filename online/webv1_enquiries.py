from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import esc, layout
from .setup015_core import context_for, one, rows, working_company


STATUSES = ('new', 'qualified', 'closed', 'converted')


def _customer_name(row) -> str:
    name = f"{row['first_name']} {row['last_name']}".strip()
    return name or '(Unnamed customer)'


def register_enquiry_routes(app) -> None:
    database = app.state.database

    @app.get('/operations/enquiries', response_class=HTMLResponse)
    def enquiry_register(
        request: Request,
        q: str = '',
        status: str = '',
        source: str = '',
        arrival_from: str = '',
        arrival_to: str = '',
        departure_from: str = '',
        departure_to: str = '',
    ):
        context = context_for(database, request)
        company_id = working_company(context)

        search = q.strip()
        status_filter = status.strip().lower()
        source_filter = source.strip()
        arrival_from = arrival_from.strip()
        arrival_to = arrival_to.strip()
        departure_from = departure_from.strip()
        departure_to = departure_to.strip()

        where = ['e.company_id=?']
        params: list[object] = [company_id]

        if search:
            term = f'%{search}%'
            where.append('''(
                c.first_name LIKE ? COLLATE NOCASE OR
                c.last_name LIKE ? COLLATE NOCASE OR
                c.email LIKE ? COLLATE NOCASE OR
                c.phone LIKE ? COLLATE NOCASE
            )''')
            params.extend([term, term, term, term])

        if status_filter in STATUSES:
            where.append('e.status=?')
            params.append(status_filter)
        if source_filter:
            where.append('e.source LIKE ? COLLATE NOCASE')
            params.append(f'%{source_filter}%')
        if arrival_from:
            where.append('e.arrival_date>=?')
            params.append(arrival_from)
        if arrival_to:
            where.append('e.arrival_date<=?')
            params.append(arrival_to)
        if departure_from:
            where.append('e.departure_date>=?')
            params.append(departure_from)
        if departure_to:
            where.append('e.departure_date<=?')
            params.append(departure_to)

        enquiries = rows(
            database,
            f'''SELECT e.*, c.first_name, c.last_name, c.email, c.phone,
                       er.element_type, er.element_id, er.provisional_total,
                       se.name AS element_name
                FROM enquiries e
                LEFT JOIN customer_records c ON c.id=e.customer_id AND c.company_id=e.company_id
                LEFT JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id
                LEFT JOIN setup_elements se ON se.id=er.element_id AND se.company_id=e.company_id
                WHERE {' AND '.join(where)}
                ORDER BY e.id DESC''',
            tuple(params),
        )

        status_options = '<option value="">All statuses</option>' + ''.join(
            f'<option value="{value}" {"selected" if value == status_filter else ""}>{esc(value.title())}</option>'
            for value in STATUSES
        )

        result_rows = ''.join(
            f'''<tr>
              <td><a href="/operations/enquiries/{int(row['id'])}">#{int(row['id'])}</a></td>
              <td>{esc(_customer_name(row))}</td>
              <td>{esc(row['status'].title())}</td>
              <td>{esc(row['arrival_date'] or '—')}</td>
              <td>{esc(row['departure_date'] or '—')}</td>
              <td>{esc(row['element_type'] or '—')}</td>
              <td>{esc(row['element_name'] or '—')}</td>
              <td>{'€%.2f' % float(row['provisional_total']) if row['provisional_total'] is not None else '—'}</td>
              <td>{esc(row['source'] or '—')}</td>
            </tr>'''
            for row in enquiries
        ) or '<tr><td colspan="9" class="muted">No matching enquiries.</td></tr>'

        body = f'''<h1>Enquiries</h1>
        <p><a href="/operations">← Operations</a></p>
        <div class="card">
          <form method="get" action="/operations/enquiries">
            <div class="grid">
              <div><label>Customer search</label><input name="q" value="{esc(search)}" placeholder="Name, email or telephone"></div>
              <div><label>Status</label><select name="status">{status_options}</select></div>
              <div><label>Source</label><input name="source" value="{esc(source_filter)}" placeholder="Phone, website, walk-in..."></div>
              <div><label>Arrival from</label><input type="date" name="arrival_from" value="{esc(arrival_from)}"></div>
              <div><label>Arrival to</label><input type="date" name="arrival_to" value="{esc(arrival_to)}"></div>
              <div><label>Departure from</label><input type="date" name="departure_from" value="{esc(departure_from)}"></div>
              <div><label>Departure to</label><input type="date" name="departure_to" value="{esc(departure_to)}"></div>
            </div>
            <p><button type="submit">Search Enquiries</button> <a class="button secondary" href="/operations/enquiries">Clear</a></p>
          </form>
        </div>
        <div class="card">
          <p><strong>{len(enquiries)}</strong> matching enquiry/enquiries</p>
          <table><thead><tr><th>No.</th><th>Customer</th><th>Status</th><th>Arrival</th><th>Departure</th><th>Element Type</th><th>Element</th><th>Provisional</th><th>Source</th></tr></thead>
          <tbody>{result_rows}</tbody></table>
        </div>'''
        return layout('Enquiries', body, context)

    @app.get('/operations/enquiries/{enquiry_id}', response_class=HTMLResponse)
    def enquiry_detail(enquiry_id: int, request: Request, saved: int = 0):
        context = context_for(database, request)
        company_id = working_company(context)
        enquiry = one(
            database,
            '''SELECT e.*, c.first_name, c.last_name, c.email, c.phone
               FROM enquiries e
               LEFT JOIN customer_records c ON c.id=e.customer_id AND c.company_id=e.company_id
               WHERE e.id=? AND e.company_id=?''',
            (enquiry_id, company_id),
        )
        if enquiry is None:
            return HTMLResponse(layout('Enquiry not found', '<div class="error">Enquiry not found.</div>', context), status_code=404)

        request_row = one(
            database,
            '''SELECT er.*, se.name AS element_name, se.pricing_method
               FROM enquiry_requests er
               LEFT JOIN setup_elements se ON se.id=er.element_id AND se.company_id=er.company_id
               WHERE er.enquiry_id=? AND er.company_id=?''',
            (enquiry_id, company_id),
        )
        people = rows(
            database,
            '''SELECT ep.quantity, pt.name FROM enquiry_people ep
               JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id
               WHERE ep.enquiry_id=? AND ep.company_id=? ORDER BY pt.name''',
            (enquiry_id, company_id),
        )
        addons = rows(
            database,
            '''SELECT ea.quantity, a.name FROM enquiry_addons ea
               JOIN setup_addons a ON a.id=ea.addon_id AND a.company_id=ea.company_id
               WHERE ea.enquiry_id=? AND ea.company_id=? ORDER BY a.name''',
            (enquiry_id, company_id),
        )

        customer_link = (
            f'<a href="/operations/customers/{int(enquiry["customer_id"])}">{esc(_customer_name(enquiry))}</a>'
            if enquiry['customer_id'] is not None else esc(_customer_name(enquiry))
        )
        notice = '<div class="ok">Enquiry request saved and provisional price recalculated from current Setup.</div>' if saved else ''

        if request_row is None:
            request_html = '''<div class="card"><h2>Requested stay</h2><p>No Element Type or Element has been attached to this enquiry yet.</p>
            <p><a class="button" href="/operations/enquiries/%d/build">Build / Price Enquiry</a></p></div>''' % enquiry_id
        else:
            people_text = ', '.join(f'{esc(r["name"])} × {int(r["quantity"])}' for r in people) or '—'
            addons_text = ', '.join(f'{esc(r["name"])} × {int(r["quantity"])}' for r in addons) or '—'
            total_text = f'€{float(request_row["provisional_total"]):.2f}' if request_row['provisional_total'] is not None else 'Not priced yet'
            breakdown = ''
            try:
                snapshot = json.loads(request_row['pricing_snapshot_json'] or '{}')
            except (TypeError, json.JSONDecodeError):
                snapshot = {}
            if snapshot.get('lines'):
                line_rows = ''.join(
                    f'<tr><td>{esc(line.get("item", ""))}</td><td>{esc(line.get("rule", ""))}</td><td>€{float(line.get("amount", 0)):.2f}</td></tr>'
                    for line in snapshot['lines']
                )
                breakdown = f'<h3>Provisional price breakdown</h3><table><thead><tr><th>Item</th><th>Rule used</th><th>Amount</th></tr></thead><tbody>{line_rows}</tbody></table>'
            request_html = f'''<div class="card"><h2>Requested stay</h2>
              <p><strong>Element Type:</strong> {esc(request_row['element_type'] or '—')}<br>
              <strong>Specific Element:</strong> {esc(request_row['element_name'] or 'Not selected')}<br>
              <strong>People:</strong> {people_text}<br>
              <strong>Add-ons:</strong> {addons_text}<br>
              <strong>Provisional total:</strong> {total_text}</p>
              {breakdown}
              <p><a class="button" href="/operations/enquiries/{enquiry_id}/build">Edit / Recalculate Enquiry</a></p>
              <p class="muted">This is still an Enquiry. No Offer has been created and no price has been frozen as a Booking.</p>
            </div>'''

        body = f'''<h1>Enquiry #{int(enquiry['id'])}</h1>
        <p><a href="/operations/enquiries">← Enquiry Search</a></p>{notice}
        <div class="grid">
          <div class="card"><h2>Customer</h2>
            <p><strong>{customer_link}</strong></p>
            <p>Email: {esc(enquiry['email'] or '—')}<br>Telephone: {esc(enquiry['phone'] or '—')}</p>
          </div>
          <div class="card"><h2>Enquiry</h2>
            <p><strong>Status:</strong> {esc(enquiry['status'].title())}<br>
            <strong>Arrival:</strong> {esc(enquiry['arrival_date'] or '—')}<br>
            <strong>Departure:</strong> {esc(enquiry['departure_date'] or '—')}<br>
            <strong>Party size:</strong> {esc(enquiry['party_size'] if enquiry['party_size'] is not None else '—')}<br>
            <strong>Source:</strong> {esc(enquiry['source'] or '—')}</p>
          </div>
        </div>
        <div class="card"><h2>Notes</h2><p>{esc(enquiry['notes'] or '—')}</p></div>
        {request_html}'''
        return layout(f'Enquiry #{int(enquiry["id"])}', body, context)
