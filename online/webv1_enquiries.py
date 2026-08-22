from __future__ import annotations

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
            f'''SELECT e.*, c.first_name, c.last_name, c.email, c.phone
                FROM enquiries e
                LEFT JOIN customer_records c
                  ON c.id=e.customer_id AND c.company_id=e.company_id
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
              <td>{esc(row['party_size'] if row['party_size'] is not None else '—')}</td>
              <td>{esc(row['source'] or '—')}</td>
            </tr>'''
            for row in enquiries
        ) or '<tr><td colspan="7" class="muted">No matching enquiries.</td></tr>'

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
          <table><thead><tr><th>No.</th><th>Customer</th><th>Status</th><th>Arrival</th><th>Departure</th><th>Party</th><th>Source</th></tr></thead>
          <tbody>{result_rows}</tbody></table>
        </div>'''
        return layout('Enquiries', body, context)

    @app.get('/operations/enquiries/{enquiry_id}', response_class=HTMLResponse)
    def enquiry_detail(enquiry_id: int, request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        enquiry = one(
            database,
            '''SELECT e.*, c.first_name, c.last_name, c.email, c.phone
               FROM enquiries e
               LEFT JOIN customer_records c
                 ON c.id=e.customer_id AND c.company_id=e.company_id
               WHERE e.id=? AND e.company_id=?''',
            (enquiry_id, company_id),
        )
        if enquiry is None:
            return HTMLResponse(layout('Enquiry not found', '<div class="error">Enquiry not found.</div>', context), status_code=404)

        customer_link = (
            f'<a href="/operations/customers/{int(enquiry["customer_id"])}">{esc(_customer_name(enquiry))}</a>'
            if enquiry['customer_id'] is not None
            else esc(_customer_name(enquiry))
        )
        body = f'''<h1>Enquiry #{int(enquiry['id'])}</h1>
        <p><a href="/operations/enquiries">← Enquiry Search</a></p>
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
        <div class="card"><p class="muted">Editing, availability, pricing and Offer conversion will be added in later milestones.</p></div>'''
        return layout(f'Enquiry #{int(enquiry["id"])}', body, context)
