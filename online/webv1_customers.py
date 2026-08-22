from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company


def _customer_name(row) -> str:
    name = f"{row['first_name']} {row['last_name']}".strip()
    return name or '(Unnamed customer)'


def _customer_form(context, values: dict[str, str], error: str = '') -> str:
    error_html = f'<div class="error">{esc(error)}</div>' if error else ''
    return f'''<h1>New Customer</h1>
    <p><a href="/operations/customers">← Back to Client Register</a></p>
    {error_html}
    <div class="card"><form method="post" action="/operations/customers/new">
      <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
      <div class="grid">
        <div><label>First name</label><input name="first_name" value="{esc(values.get('first_name',''))}"></div>
        <div><label>Last name</label><input name="last_name" value="{esc(values.get('last_name',''))}"></div>
        <div><label>Email</label><input type="email" name="email" value="{esc(values.get('email',''))}"></div>
        <div><label>Telephone</label><input name="phone" value="{esc(values.get('phone',''))}"></div>
      </div>
      <label>Notes</label><textarea name="notes" rows="5" style="width:100%;padding:9px;border:1px solid #aeb8c4;border-radius:6px">{esc(values.get('notes',''))}</textarea>
      <p><button type="submit">Create Customer</button></p>
    </form></div>'''


def register_customer_routes(app) -> None:
    database = app.state.database

    @app.get('/operations/customers', response_class=HTMLResponse)
    def customer_register(request: Request, q: str = ''):
        context = context_for(database, request)
        company_id = working_company(context)
        search = q.strip()
        params: list[object] = [company_id]
        where = 'company_id=? AND active=1'
        if search:
            term = f'%{search}%'
            where += " AND (first_name LIKE ? COLLATE NOCASE OR last_name LIKE ? COLLATE NOCASE OR email LIKE ? COLLATE NOCASE OR phone LIKE ? COLLATE NOCASE)"
            params.extend([term, term, term, term])
        customers = rows(database, f'SELECT * FROM customer_records WHERE {where} ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE, id DESC', tuple(params))
        customer_rows = ''.join(
            f'''<tr><td><a href="/operations/customers/{int(row['id'])}">{esc(_customer_name(row))}</a></td>
            <td>{esc(row['email'])}</td><td>{esc(row['phone'])}</td></tr>'''
            for row in customers
        ) or '<tr><td colspan="3" class="muted">No matching customers.</td></tr>'
        body = f'''<h1>Client Register</h1>
        <div class="card"><form method="get" action="/operations/customers" style="display:flex;gap:8px;align-items:end;flex-wrap:wrap">
          <div style="flex:1;min-width:260px"><label>Find returning customer</label><input name="q" value="{esc(search)}" placeholder="Name, email or telephone"></div>
          <div><button type="submit">Search</button></div>
          <div><a class="button secondary" href="/operations/customers">Clear</a></div>
          <div><a class="button" href="/operations/customers/new">New Customer</a></div>
        </form></div>
        <div class="card"><table><thead><tr><th>Customer</th><th>Email</th><th>Telephone</th></tr></thead><tbody>{customer_rows}</tbody></table></div>'''
        return layout('Client Register', body, context)

    @app.get('/operations/customers/new', response_class=HTMLResponse)
    def customer_new(request: Request):
        context = context_for(database, request)
        return layout('New Customer', _customer_form(context, {}), context)

    @app.post('/operations/customers/new', response_class=HTMLResponse)
    async def customer_create(request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        data = await form_data(request)
        require_csrf(context, data)
        values = {key: data.get(key, '').strip() for key in ('first_name','last_name','email','phone','notes')}
        if not values['first_name'] and not values['last_name']:
            return HTMLResponse(layout('New Customer', _customer_form(context, values, 'Enter at least a first name or last name.'), context), status_code=400)
        now = iso_now()
        with database.connect() as connection:
            customer_id = int(connection.execute(
                '''INSERT INTO customer_records(company_id,first_name,last_name,email,phone,notes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (company_id, values['first_name'], values['last_name'], values['email'], values['phone'], values['notes'], now, now),
            ).lastrowid)
        audit(database, context, company_id, 'CUSTOMER_CREATED', 'customer', customer_id, after=values)
        return RedirectResponse(f'/operations/customers/{customer_id}?created=1', status_code=303)

    @app.get('/operations/customers/{customer_id}', response_class=HTMLResponse)
    def customer_detail(customer_id: int, request: Request, created: int = 0, enquiry_created: int = 0):
        context = context_for(database, request)
        company_id = working_company(context)
        customer = one(database, 'SELECT * FROM customer_records WHERE id=? AND company_id=? AND active=1', (customer_id, company_id))
        if customer is None:
            return HTMLResponse(layout('Customer not found', '<div class="error">Customer not found.</div>', context), status_code=404)
        enquiries = rows(database, 'SELECT * FROM enquiries WHERE customer_id=? AND company_id=? ORDER BY id DESC', (customer_id, company_id))
        enquiry_rows = ''.join(
            f'''<tr><td><a href="/operations/enquiries/{int(row['id'])}">#{int(row['id'])}</a></td><td>{esc(row['status'].title())}</td><td>{esc(row['arrival_date'] or '—')}</td><td>{esc(row['departure_date'] or '—')}</td><td>{esc(row['party_size'] if row['party_size'] is not None else '—')}</td><td>{esc(row['source'] or '—')}</td></tr>'''
            for row in enquiries
        ) or '<tr><td colspan="6" class="muted">No enquiries yet.</td></tr>'
        notice = '<div class="ok">Customer created.</div>' if created else ('<div class="ok">Enquiry created.</div>' if enquiry_created else '')
        body = f'''<h1>{esc(_customer_name(customer))}</h1><p><a href="/operations/customers">← Client Register</a></p>{notice}
        <div class="grid">
          <div class="card"><h2>Customer details</h2><p><strong>Email:</strong> {esc(customer['email'] or '—')}<br><strong>Telephone:</strong> {esc(customer['phone'] or '—')}</p><p>{esc(customer['notes'] or '')}</p></div>
          <div class="card"><h2>Next action</h2><p><a class="button" href="/operations/customers/{customer_id}/enquiries/new">New Enquiry</a></p><p class="muted">Create the complete Enquiry, including Element, People, Add-ons and provisional price, in one screen.</p></div>
        </div>
        <div class="card"><h2>Enquiry history</h2><table><thead><tr><th>No.</th><th>Status</th><th>Arrival</th><th>Departure</th><th>Party</th><th>Source</th></tr></thead><tbody>{enquiry_rows}</tbody></table></div>'''
        return layout(_customer_name(customer), body, context)
