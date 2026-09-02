from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company


def _customer_name(row) -> str:
    name = f"{row['first_name']} {row['last_name']}".strip()
    return name or '(Unnamed customer)'


def _normal_phone(value: str) -> str:
    return re.sub(r'\D', '', str(value or ''))


def _ensure_customer_contact_columns(database) -> None:
    with database.connect() as connection:
        columns = {str(r['name']) for r in connection.execute('PRAGMA table_info(customer_records)').fetchall()}
        if 'mobile_phone' not in columns:
            connection.execute("ALTER TABLE customer_records ADD COLUMN mobile_phone TEXT NOT NULL DEFAULT ''")
        if 'fixed_phone' not in columns:
            connection.execute("ALTER TABLE customer_records ADD COLUMN fixed_phone TEXT NOT NULL DEFAULT ''")
        connection.execute('CREATE INDEX IF NOT EXISTS idx_customer_records_mobile ON customer_records(company_id, mobile_phone)')
        connection.execute('CREATE INDEX IF NOT EXISTS idx_customer_records_fixed ON customer_records(company_id, fixed_phone)')


def customer_matches(database, company_id: int, *, email: str = '', mobile_phone: str = '', fixed_phone: str = '', exclude_customer_id: int | None = None):
    email_key = str(email or '').strip().lower()
    mobile_key = _normal_phone(mobile_phone)
    fixed_key = _normal_phone(fixed_phone)
    if not any((email_key, mobile_key, fixed_key)):
        return []
    candidates = rows(database, '''SELECT c.*,
        (SELECT COUNT(*) FROM bookings b WHERE b.company_id=c.company_id AND b.customer_id=c.id) AS booking_count,
        (SELECT COUNT(*) FROM enquiries e WHERE e.company_id=c.company_id AND e.customer_id=c.id) AS enquiry_count
        FROM customer_records c WHERE c.company_id=? AND c.active=1 ORDER BY c.last_name COLLATE NOCASE,c.first_name COLLATE NOCASE,c.id''', (company_id,))
    found = []
    for row in candidates:
        if exclude_customer_id is not None and int(row['id']) == int(exclude_customer_id):
            continue
        reasons = []
        if email_key and str(row['email'] or '').strip().lower() == email_key:
            reasons.append('Email')
        row_mobile = _normal_phone(row['mobile_phone'] or row['phone'])
        row_fixed = _normal_phone(row['fixed_phone'])
        if mobile_key and row_mobile and row_mobile == mobile_key:
            reasons.append('Mobile')
        if fixed_key and ((row_fixed and row_fixed == fixed_key) or (_normal_phone(row['phone']) and _normal_phone(row['phone']) == fixed_key)):
            reasons.append('Fixed telephone')
        if reasons:
            found.append((row, reasons))
    found.sort(key=lambda item: (-len(item[1]), -int(item[0]['booking_count'] or 0), str(item[0]['last_name'] or '').lower(), str(item[0]['first_name'] or '').lower()))
    return found


def _match_table(matches, *, choose_prefix: str = '') -> str:
    if not matches:
        return '<p class="muted">No matching Customer records found.</p>'
    trs = []
    for row, reasons in matches:
        history = f"{int(row['booking_count'] or 0)} previous Booking(s), {int(row['enquiry_count'] or 0)} Enquiry(ies)"
        action = f'<a class="button" href="{choose_prefix}{int(row["id"])}">Use this Customer</a>' if choose_prefix else f'<a class="button" href="/operations/customers/{int(row["id"])}">Open Customer</a>'
        trs.append(f'''<tr><td>{esc(_customer_name(row))}</td><td>{esc(' + '.join(reasons))}</td><td>{esc(history)}</td><td>{action}</td></tr>''')
    return '<table><thead><tr><th>Possible existing Customer</th><th>Match</th><th>History</th><th>Action</th></tr></thead><tbody>' + ''.join(trs) + '</tbody></table>'


def _customer_form(context, values: dict[str, str], error: str = '', matches=None) -> str:
    error_html = f'<div class="error">{esc(error)}</div>' if error else ''
    matches_html = ''
    if matches:
        matches_html = f'''<div class="card"><h2>Possible existing Customers</h2><p>These records match the email or telephone details entered. Check them before creating a duplicate Customer.</p>{_match_table(matches)}<p class="muted">Creating a separate Customer is still a Client decision.</p></div>'''
    return f'''<h1>New Customer</h1>
    <p><a href="/operations/customers">← Back to Client Register</a></p>
    {error_html}{matches_html}
    <div class="card"><form method="post" action="/operations/customers/new">
      <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
      <input type="hidden" name="confirm_new" value="{esc(values.get('confirm_new',''))}">
      <div class="grid">
        <div><label>Family name *</label><input name="last_name" required value="{esc(values.get('last_name',''))}"></div>
        <div><label>First name *</label><input name="first_name" required value="{esc(values.get('first_name',''))}"></div>
        <div><label>Email address *</label><input type="email" name="email" required value="{esc(values.get('email',''))}"></div>
        <div><label>Mobile telephone</label><input name="mobile_phone" value="{esc(values.get('mobile_phone',''))}"></div>
        <div><label>Fixed telephone</label><input name="fixed_phone" value="{esc(values.get('fixed_phone',''))}"></div>
        <div><label>Address line 1</label><input name="address1" value="{esc(values.get('address1',''))}"></div>
        <div><label>Address line 2</label><input name="address2" value="{esc(values.get('address2',''))}"></div>
        <div><label>Town / City</label><input name="town" value="{esc(values.get('town',''))}"></div>
        <div><label>Postcode</label><input name="postcode" value="{esc(values.get('postcode',''))}"></div>
        <div><label>Country</label><input name="country" value="{esc(values.get('country',''))}"></div>
      </div>
      <label>Notes</label><textarea name="notes" rows="5" style="width:100%;padding:9px;border:1px solid #aeb8c4;border-radius:6px">{esc(values.get('notes',''))}</textarea>
      <p><button type="submit">Create Customer</button></p>
    </form></div>'''


def register_customer_routes(app) -> None:
    database = app.state.database
    _ensure_customer_contact_columns(database)

    @app.get('/operations/customers', response_class=HTMLResponse)
    def customer_register(request: Request, q: str = ''):
        context = context_for(database, request)
        company_id = working_company(context)
        search = q.strip()
        params: list[object] = [company_id]
        where = 'company_id=? AND active=1'
        if search:
            term = f'%{search}%'
            digits = _normal_phone(search)
            where += " AND (first_name LIKE ? COLLATE NOCASE OR last_name LIKE ? COLLATE NOCASE OR email LIKE ? COLLATE NOCASE OR phone LIKE ? COLLATE NOCASE OR mobile_phone LIKE ? OR fixed_phone LIKE ?)"
            params.extend([term, term, term, term, f'%{digits}%' if digits else term, f'%{digits}%' if digits else term])
        customers = rows(database, f'SELECT * FROM customer_records WHERE {where} ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE, id DESC', tuple(params))
        customer_rows = ''.join(
            f'''<tr><td><a href="/operations/customers/{int(row['id'])}">{esc(_customer_name(row))}</a></td>
            <td>{esc(row['email'])}</td><td>{esc(row['mobile_phone'] or row['phone'])}</td><td>{esc(row['fixed_phone'])}</td></tr>'''
            for row in customers
        ) or '<tr><td colspan="4" class="muted">No matching customers.</td></tr>'
        body = f'''<h1>Client Register</h1>
        <div class="card"><form method="get" action="/operations/customers" style="display:flex;gap:8px;align-items:end;flex-wrap:wrap">
          <div style="flex:1;min-width:260px"><label>Find returning Customer</label><input name="q" value="{esc(search)}" placeholder="Name, email, mobile or fixed telephone"></div>
          <div><button type="submit">Search</button></div>
          <div><a class="button secondary" href="/operations/customers">Clear</a></div>
          <div><a class="button" href="/operations/customers/new">New Customer</a></div>
        </form></div>
        <div class="card"><table><thead><tr><th>Customer</th><th>Email</th><th>Mobile</th><th>Fixed telephone</th></tr></thead><tbody>{customer_rows}</tbody></table></div>'''
        return layout('Client Register', body, context)

    @app.get('/operations/customers/new', response_class=HTMLResponse)
    def customer_new(request: Request):
        context = context_for(database, request)
        return layout('New Customer', _customer_form(context, {}), context)

    @app.post('/operations/customers/new', response_class=HTMLResponse)
    async def customer_create(request: Request):
        context = context_for(database, request)
        company_id = int(working_company(context))
        data = await form_data(request)
        require_csrf(context, data)
        keys = ('first_name','last_name','email','mobile_phone','fixed_phone','address1','address2','town','postcode','country','notes','confirm_new')
        values = {key: data.get(key, '').strip() for key in keys}
        if not values['first_name'] or not values['last_name']:
            return HTMLResponse(layout('New Customer', _customer_form(context, values, 'Enter both Family name and First name.'), context), status_code=400)
        if not values['email']:
            return HTMLResponse(layout('New Customer', _customer_form(context, values, 'Email address is compulsory.'), context), status_code=400)
        if not values['mobile_phone'] and not values['fixed_phone']:
            return HTMLResponse(layout('New Customer', _customer_form(context, values, 'Enter a mobile or fixed telephone number.'), context), status_code=400)
        matches = customer_matches(database, company_id, email=values['email'], mobile_phone=values['mobile_phone'], fixed_phone=values['fixed_phone'])
        if matches and values['confirm_new'] != '1':
            values['confirm_new'] = '1'
            return HTMLResponse(layout('Possible existing Customer', _customer_form(context, values, 'Possible existing Customer found. Open the matching master file or submit again to deliberately create a separate Customer.', matches), context), status_code=409)
        now = iso_now()
        with database.connect() as connection:
            customer_id = int(connection.execute(
                '''INSERT INTO customer_records(company_id,first_name,last_name,email,phone,mobile_phone,fixed_phone,address1,address2,town,postcode,country,notes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (company_id, values['first_name'], values['last_name'], values['email'], values['mobile_phone'] or values['fixed_phone'], values['mobile_phone'], values['fixed_phone'], values['address1'], values['address2'], values['town'], values['postcode'], values['country'], values['notes'], now, now),
            ).lastrowid)
        audit(database, context, company_id, 'CUSTOMER_CREATED', 'customer', customer_id, after={key: values[key] for key in keys if key != 'confirm_new'})
        return RedirectResponse(f'/operations/customers/{customer_id}?created=1', status_code=303)

    @app.get('/operations/customers/{customer_id}', response_class=HTMLResponse)
    def customer_detail(customer_id: int, request: Request, created: int = 0, enquiry_created: int = 0):
        context = context_for(database, request)
        company_id = int(working_company(context))
        customer = one(database, 'SELECT * FROM customer_records WHERE id=? AND company_id=? AND active=1', (customer_id, company_id))
        if customer is None:
            return HTMLResponse(layout('Customer not found', '<div class="error">Customer not found.</div>', context), status_code=404)
        enquiries = rows(database, 'SELECT * FROM enquiries WHERE customer_id=? AND company_id=? ORDER BY id DESC', (customer_id, company_id))
        bookings = rows(database, 'SELECT * FROM bookings WHERE customer_id=? AND company_id=? ORDER BY arrival_date DESC,id DESC', (customer_id, company_id))
        enquiry_rows = ''.join(
            f'''<tr><td><a href="/operations/enquiries/{int(row['id'])}">#{int(row['id'])}</a></td><td>{esc(row['status'].title())}</td><td>{esc(row['arrival_date'] or '—')}</td><td>{esc(row['departure_date'] or '—')}</td><td>{esc(row['party_size'] if row['party_size'] is not None else '—')}</td><td>{esc(row['source'] or '—')}</td></tr>'''
            for row in enquiries
        ) or '<tr><td colspan="6" class="muted">No Enquiries yet.</td></tr>'
        booking_rows = ''.join(
            f'''<tr><td><a href="/operations/bookings/{int(row['id'])}">{esc(row['reference'])}</a></td><td>{esc(row['status'].title())}</td><td>{esc(row['arrival_date'])}</td><td>{esc(row['departure_date'])}</td><td>€{float(row['total_amount'] or 0):.2f}</td></tr>'''
            for row in bookings
        ) or '<tr><td colspan="5" class="muted">No Bookings yet.</td></tr>'
        notice = '<div class="ok">Customer created.</div>' if created else ('<div class="ok">Enquiry created.</div>' if enquiry_created else '')
        address = '<br>'.join(esc(x) for x in (customer['address1'], customer['address2'], customer['town'], customer['postcode'], customer['country']) if str(x or '').strip()) or '—'
        body = f'''<h1>{esc(_customer_name(customer))}</h1><p><a href="/operations/customers">← Client Register</a></p>{notice}
        <div class="grid">
          <div class="card"><h2>Customer master details</h2><p><strong>Email:</strong> {esc(customer['email'] or '—')}<br><strong>Mobile:</strong> {esc(customer['mobile_phone'] or customer['phone'] or '—')}<br><strong>Fixed telephone:</strong> {esc(customer['fixed_phone'] or '—')}</p><p><strong>Address</strong><br>{address}</p><p>{esc(customer['notes'] or '')}</p></div>
          <div class="card"><h2>Next action</h2><p><a class="button" href="/operations/customers/{customer_id}/enquiries/new">New Enquiry</a></p><p class="muted">New Enquiries and Bookings should be linked to this master record only when the Client chooses to do so.</p></div>
        </div>
        <div class="card"><h2>Booking history</h2><table><thead><tr><th>Reference</th><th>Status</th><th>Arrival</th><th>Departure</th><th>Total</th></tr></thead><tbody>{booking_rows}</tbody></table></div>
        <div class="card"><h2>Enquiry history</h2><table><thead><tr><th>No.</th><th>Status</th><th>Arrival</th><th>Departure</th><th>Party</th><th>Source</th></tr></thead><tbody>{enquiry_rows}</tbody></table></div>'''
        return layout(_customer_name(customer), body, context)

    @app.get('/operations/bookings/{booking_id}/customer-matches', response_class=HTMLResponse)
    def booking_customer_matches(booking_id: int, request: Request):
        context = context_for(database, request)
        company_id = int(working_company(context))
        booking = one(database, 'SELECT * FROM bookings WHERE id=? AND company_id=?', (booking_id, company_id))
        if booking is None:
            return HTMLResponse(layout('Booking not found', '<div class="error">Booking not found.</div>', context), 404)
        current = one(database, 'SELECT * FROM customer_records WHERE id=? AND company_id=?', (booking['customer_id'], company_id)) if booking['customer_id'] else None
        if current is None:
            body = f'''<h1>Customer match for {esc(booking['reference'])}</h1><div class="error">This Booking does not yet have Customer contact details to match.</div><p><a href="/operations/bookings/{booking_id}">← Booking</a></p>'''
            return layout('Customer match', body, context)
        matches = customer_matches(database, company_id, email=current['email'], mobile_phone=current['mobile_phone'] or current['phone'], fixed_phone=current['fixed_phone'], exclude_customer_id=int(current['id']))
        rows_html = []
        for row, reasons in matches:
            rows_html.append(f'''<tr><td>{esc(_customer_name(row))}</td><td>{esc(' + '.join(reasons))}</td><td>{int(row['booking_count'] or 0)} previous Booking(s)</td><td><form method="post" action="/operations/bookings/{booking_id}/link-customer"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="customer_id" value="{int(row['id'])}"><button type="submit">Add Booking to this Customer master file</button></form></td></tr>''')
        table = '<table><thead><tr><th>Possible existing Customer</th><th>Match</th><th>History</th><th>Client decision</th></tr></thead><tbody>' + ''.join(rows_html) + '</tbody></table>' if rows_html else '<p class="muted">No existing Customer matches the booking contact details.</p>'
        body = f'''<h1>Customer match for {esc(booking['reference'])}</h1><p><a href="/operations/bookings/{booking_id}">← Booking</a></p><div class="card"><p><strong>Current booking contact:</strong> {esc(_customer_name(current))} — {esc(current['email'])} — {esc(current['mobile_phone'] or current['phone'])}</p><p>DBS will never merge or relink this Booking automatically.</p>{table}</div>'''
        return layout('Customer match', body, context)

    @app.post('/operations/bookings/{booking_id}/link-customer')
    async def booking_link_customer(booking_id: int, request: Request):
        context = context_for(database, request)
        company_id = int(working_company(context))
        data = await form_data(request)
        require_csrf(context, data)
        try:
            customer_id = int(data.get('customer_id', ''))
        except ValueError:
            return HTMLResponse(layout('Customer match', '<div class="error">Choose a valid Customer.</div>', context), 400)
        booking = one(database, 'SELECT * FROM bookings WHERE id=? AND company_id=?', (booking_id, company_id))
        target = one(database, 'SELECT * FROM customer_records WHERE id=? AND company_id=? AND active=1', (customer_id, company_id))
        if booking is None or target is None:
            return HTMLResponse(layout('Customer match', '<div class="error">Booking or Customer not found.</div>', context), 404)
        previous_customer_id = booking['customer_id']
        with database.connect() as connection:
            connection.execute('UPDATE bookings SET customer_id=?,updated_at=? WHERE id=? AND company_id=?', (customer_id, iso_now(), booking_id, company_id))
        audit(database, context, company_id, 'BOOKING_LINKED_TO_CUSTOMER', 'booking', booking_id, before={'customer_id': previous_customer_id}, after={'customer_id': customer_id})
        return RedirectResponse(f'/operations/bookings/{booking_id}?message=Booking+added+to+existing+Customer+master+file', 303)
