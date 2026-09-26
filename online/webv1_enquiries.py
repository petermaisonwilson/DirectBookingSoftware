from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, layout
from .setup015_core import context_for, one, rows, working_company
from .webv1_bookings import enquiry_conversion_panel
from .webv1_status_availability import availability_state
from .database import iso_now
from .app import form_data
from .setup015_core import audit, require_csrf

STATUSES = ('new', 'closed', 'converted')


def _status_label(value: str) -> str:
    return 'Released' if str(value) == 'closed' else str(value).title()


def _customer_name(row) -> str:
    name = f"{row['first_name']} {row['last_name']}".strip()
    return name or '(Unnamed customer)'


def _fmt_day(value) -> str:
    if not value:
        return '—'
    parts = str(value).split('-')
    return f'{parts[2]}/{parts[1]}/{parts[0]}' if len(parts) == 3 else str(value)



def _enquiry_action(row, context) -> str:
    enquiry_id=int(row['id']); csrf=esc(context['csrf_token']); status=str(row['status'])
    if status == 'converted': return '—'
    if status == 'closed':
        return f'<form method="post" action="/operations/enquiries/{enquiry_id}/reopen" style="display:inline"><input type="hidden" name="csrf" value="{csrf}"><button class="secondary">REOPEN ENQUIRY</button></form>'
    return f'''<form method="post" action="/operations/enquiries/{enquiry_id}/release" style="display:inline" onsubmit="return confirm('Release this enquiry? The enquiry and its history will be retained, but its reserved availability will be released.')"><input type="hidden" name="csrf" value="{csrf}"><button class="secondary">RELEASE ENQUIRY</button></form>'''

def register_enquiry_routes(app) -> None:
    database = app.state.database

    @app.get('/operations/enquiries', response_class=HTMLResponse)
    def enquiry_register(request: Request, q: str = '', status: str = '', source: str = '', arrival_from: str = '', arrival_to: str = '', departure_from: str = '', departure_to: str = '', holding: str = ''):
        context = context_for(database, request); company_id = working_company(context)
        search = q.strip(); status_filter = status.strip().lower(); source_filter = source.strip()
        arrival_from = arrival_from.strip(); arrival_to = arrival_to.strip(); departure_from = departure_from.strip(); departure_to = departure_to.strip()
        where = ['e.company_id=?']; params: list[object] = [company_id]
        if search:
            term = f'%{search}%'; where.append('(c.first_name LIKE ? COLLATE NOCASE OR c.last_name LIKE ? COLLATE NOCASE OR c.email LIKE ? COLLATE NOCASE OR c.phone LIKE ? COLLATE NOCASE)'); params.extend([term, term, term, term])
        if status_filter in STATUSES: where.append('e.status=?'); params.append(status_filter)
        if source_filter: where.append('e.source LIKE ? COLLATE NOCASE'); params.append(f'%{source_filter}%')
        if arrival_from: where.append('e.arrival_date>=?'); params.append(arrival_from)
        if arrival_to: where.append('e.arrival_date<=?'); params.append(arrival_to)
        if departure_from: where.append('e.departure_date>=?'); params.append(departure_from)
        if departure_to: where.append('e.departure_date<=?'); params.append(departure_to)
        holding_clause = """e.status NOT IN ('closed','converted')
            AND NOT EXISTS (SELECT 1 FROM bookings bx WHERE bx.company_id=e.company_id AND bx.enquiry_id=e.id)
            AND EXISTS (SELECT 1 FROM booking_status_definitions esh WHERE esh.id=e.workflow_status_id AND esh.company_id=e.company_id AND esh.active=1 AND COALESCE(esh.blocks_availability,1)=1 AND (e.availability_expires_at IS NULL OR e.availability_expires_at>?))"""
        if holding.strip()=='1':
            where.append('(' + holding_clause + ')'); params.append(iso_now())
        elif holding.strip()=='0':
            where.append('NOT (' + holding_clause + ')'); params.append(iso_now())
        enquiries = rows(database, f'''SELECT e.*,c.first_name,c.last_name,c.email,c.phone,er.element_type,er.element_id,er.provisional_total,se.name AS element_name,
            CASE WHEN e.status NOT IN ('closed','converted') AND NOT EXISTS (SELECT 1 FROM bookings bx2 WHERE bx2.company_id=e.company_id AND bx2.enquiry_id=e.id) AND COALESCE(es.blocks_availability,1)=1 AND (e.availability_expires_at IS NULL OR e.availability_expires_at>?) THEN 1 ELSE 0 END AS holding_space
            FROM enquiries e LEFT JOIN customer_records c ON c.id=e.customer_id AND c.company_id=e.company_id
            LEFT JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id
            LEFT JOIN setup_elements se ON se.id=er.element_id AND se.company_id=e.company_id
            LEFT JOIN booking_status_definitions es ON es.id=e.workflow_status_id AND es.company_id=e.company_id
            WHERE {' AND '.join(where)} ORDER BY e.id DESC''', tuple([iso_now()]+params))
        status_options = '<option value="">All statuses</option>' + ''.join(f'<option value="{v}" {"selected" if v == status_filter else ""}>{esc(v.title())}</option>' for v in STATUSES)
        result_rows = ''.join(f'''<tr><td><a href="/operations/enquiries/{int(r['id'])}">#{int(r['id'])}</a></td><td>{esc(_customer_name(r))}</td><td>{esc(_status_label(r['status']))}</td><td>{_fmt_day(r['arrival_date'])}</td><td>{_fmt_day(r['departure_date'])}</td><td>{esc(r['element_type'] or '—')}</td><td>{esc(r['element_name'] or '—')}</td><td>{'€%.2f' % float(r['provisional_total']) if r['provisional_total'] is not None else '—'}</td><td>{esc(r['source'] or '—')}</td><td>{"Holding Space" if int(r["holding_space"] or 0) else "Released"}</td><td>{_enquiry_action(r, context)}</td></tr>''' for r in enquiries) or '<tr><td colspan="11" class="muted">No matching enquiries.</td></tr>'
        body = f'''<h1>Enquiries</h1><p><a href="/operations">← Operations</a></p><div class="card"><form method="get" action="/operations/enquiries"><div class="grid"><div><label>Customer search</label><input name="q" value="{esc(search)}" placeholder="Name, email or telephone"></div><div><label>Status</label><select name="status">{status_options}</select></div><div><label>Source</label><input name="source" value="{esc(source_filter)}"></div><div><label>Arrival from</label><input type="date" name="arrival_from" value="{esc(arrival_from)}"></div><div><label>Arrival to</label><input type="date" name="arrival_to" value="{esc(arrival_to)}"></div><div><label>Departure from</label><input type="date" name="departure_from" value="{esc(departure_from)}"></div><div><label>Departure to</label><input type="date" name="departure_to" value="{esc(departure_to)}"></div><div><label>Availability</label><select name="holding"><option value="">All</option><option value="1" {"selected" if holding=="1" else ""}>Holding Space</option><option value="0" {"selected" if holding=="0" else ""}>Released</option></select></div></div><p><button>Search Enquiries</button> <a class="button secondary" href="/operations/enquiries">Clear</a></p></form></div><div class="card"><p><strong>{len(enquiries)}</strong> matching enquiry/enquiries</p><table><thead><tr><th>No.</th><th>Customer</th><th>Status</th><th>Arrival</th><th>Departure</th><th>Element Type</th><th>Element</th><th>Provisional</th><th>Source</th><th>Availability</th><th>Action</th></tr></thead><tbody>{result_rows}</tbody></table></div>'''
        return layout('Enquiries', body, context)

    @app.get('/operations/enquiries/{enquiry_id}', response_class=HTMLResponse)
    def enquiry_detail(enquiry_id: int, request: Request, saved: int = 0, convert_error: str = ''):
        context = context_for(database, request); company_id = working_company(context)
        enquiry = one(database, '''SELECT e.*,c.first_name,c.last_name,c.email,c.phone FROM enquiries e LEFT JOIN customer_records c ON c.id=e.customer_id AND c.company_id=e.company_id WHERE e.id=? AND e.company_id=?''', (enquiry_id, company_id))
        if enquiry is None:
            return HTMLResponse(layout('Enquiry not found', '<div class="error">Enquiry not found.</div>', context), 404)
        request_row = one(database, '''SELECT er.*,se.name AS element_name,se.pricing_method FROM enquiry_requests er LEFT JOIN setup_elements se ON se.id=er.element_id AND se.company_id=er.company_id WHERE er.enquiry_id=? AND er.company_id=?''', (enquiry_id, company_id))
        element_rows = rows(database, '''SELECT ee.*,se.name AS element_name,se.pricing_method FROM enquiry_elements ee LEFT JOIN setup_elements se ON se.id=ee.element_id AND se.company_id=ee.company_id WHERE ee.enquiry_id=? AND ee.company_id=? ORDER BY ee.sort_order,ee.id''', (enquiry_id, company_id))
        people = rows(database, '''SELECT ep.quantity,pt.name FROM enquiry_people ep JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id WHERE ep.enquiry_id=? AND ep.company_id=? ORDER BY pt.name''', (enquiry_id, company_id))
        addons = rows(database, '''SELECT ea.quantity,a.name FROM enquiry_addons ea JOIN setup_addons a ON a.id=ea.addon_id AND a.company_id=ea.company_id WHERE ea.enquiry_id=? AND ea.company_id=? ORDER BY a.name''', (enquiry_id, company_id))
        customer_link = f'<a href="/operations/customers/{int(enquiry["customer_id"])}">{esc(_customer_name(enquiry))}</a>' if enquiry['customer_id'] is not None else esc(_customer_name(enquiry))
        notice = '<div class="ok">Enquiry saved.</div>' if saved else ''
        if convert_error: notice += f'<div class="error">{esc(convert_error)}</div>'
        if element_rows:
            element_cards=[]; grand_total=0.0
            for erow in element_rows:
                eeid=int(erow['id'])
                epeople=rows(database, '''SELECT ep.quantity,pt.name FROM enquiry_element_people ep JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id WHERE ep.enquiry_element_id=? AND ep.company_id=? ORDER BY pt.name''', (eeid,company_id))
                eaddons=rows(database, '''SELECT ea.quantity,a.name FROM enquiry_element_addons ea JOIN setup_addons a ON a.id=ea.addon_id AND a.company_id=ea.company_id WHERE ea.enquiry_element_id=? AND ea.company_id=? ORDER BY a.name''', (eeid,company_id))
                if not eaddons:
                    eaddons=rows(database, '''SELECT ea.quantity,a.name FROM enquiry_addons ea JOIN setup_addons a ON a.id=ea.addon_id AND a.company_id=ea.company_id WHERE ea.enquiry_id=? AND ea.company_id=? AND (ea.enquiry_element_id=? OR ea.enquiry_element_id IS NULL) ORDER BY a.name''', (enquiry_id,company_id,eeid))
                people_text=', '.join(f'{esc(r["name"])} × {int(r["quantity"])}' for r in epeople) or '—'
                addons_text=', '.join(f'{esc(r["name"])} × {int(r["quantity"])}' for r in eaddons) or '—'
                subtotal=float(erow['provisional_total'] or 0); grand_total+=subtotal
                element_cards.append(f'<h3>{esc(erow["element_name"] or "Element")}</h3><p><strong>Element Type:</strong> {esc(erow["element_type"] or "—")}<br><strong>Guest surname:</strong> {esc(erow["lead_name"] or "—")}<br><strong>Arrival:</strong> {_fmt_day(erow["arrival_date"])}<br><strong>Departure:</strong> {_fmt_day(erow["departure_date"])}<br><strong>People:</strong> {people_text}<br><strong>Add-ons:</strong> {addons_text}<br><strong>Element total:</strong> €{subtotal:.2f}</p>')
            request_html=f'<div class="card"><h2>Requested stay</h2>{"".join(element_cards)}<p><strong>Provisional total: €{grand_total:.2f}</strong></p><p><a class="button" href="/operations/enquiries/{enquiry_id}/edit">Edit / Recalculate Enquiry</a></p></div>'
        elif request_row is None:
            request_html = f'<div class="card"><h2>Requested stay</h2><p>No Element Type or Element has been attached yet.</p><p><a class="button" href="/operations/enquiries/{enquiry_id}/edit">Edit Enquiry</a></p></div>'
        else:
            people_text = ', '.join(f'{esc(r["name"])} × {int(r["quantity"])}' for r in people) or '—'
            addons_text = ', '.join(f'{esc(r["name"])} × {int(r["quantity"])}' for r in addons) or '—'
            total_text = f'€{float(request_row["provisional_total"]):.2f}' if request_row['provisional_total'] is not None else 'Not priced yet'
            breakdown = ''
            try: snapshot = json.loads(request_row['pricing_snapshot_json'] or '{}')
            except (TypeError, json.JSONDecodeError): snapshot = {}
            if snapshot.get('lines'):
                line_rows = ''.join(f'<tr><td>{esc(line.get("item", ""))}</td><td>{esc(line.get("rule", ""))}</td><td>€{float(line.get("amount", 0)):.2f}</td></tr>' for line in snapshot['lines'])
                breakdown = f'<h3>Provisional price breakdown</h3><table><thead><tr><th>Item</th><th>Rule used</th><th>Amount</th></tr></thead><tbody>{line_rows}</tbody></table>'
            request_html = f'''<div class="card"><h2>Requested stay</h2><p><strong>Element Type:</strong> {esc(request_row['element_type'] or '—')}<br><strong>Specific Element:</strong> {esc(request_row['element_name'] or 'Not selected')}<br><strong>People:</strong> {people_text}<br><strong>Add-ons:</strong> {addons_text}<br><strong>Provisional total:</strong> {total_text}</p>{breakdown}<p><a class="button" href="/operations/enquiries/{enquiry_id}/edit">Edit / Recalculate Enquiry</a></p></div>'''
        conversion = enquiry_conversion_panel(database, context, enquiry_id)
        body = f'''<h1>Enquiry #{int(enquiry['id'])}</h1><p><a href="/operations/enquiries">← Enquiry Search</a></p>{notice}<div class="grid"><div class="card"><h2>Customer</h2><p><strong>{customer_link}</strong></p><p>Email: {esc(enquiry['email'] or '—')}<br>Telephone: {esc(enquiry['phone'] or '—')}</p></div><div class="card"><h2>Enquiry</h2><p><strong>Status:</strong> {esc(_status_label(enquiry['status']))}<br><strong>Arrival:</strong> {_fmt_day(enquiry['arrival_date'])}<br><strong>Departure:</strong> {_fmt_day(enquiry['departure_date'])}<br><strong>Party size:</strong> {esc(enquiry['party_size'] if enquiry['party_size'] is not None else '—')}<br><strong>Source:</strong> {esc(enquiry['source'] or '—')}</p></div></div><div class="card"><h2>Notes</h2><p>{esc(enquiry['notes'] or '—')}</p></div>{request_html}{conversion}'''
        return layout(f'Enquiry #{int(enquiry["id"])}', body, context)

    @app.post('/operations/enquiries/{enquiry_id}/release')
    async def release_enquiry(enquiry_id: int, request: Request):
        context = context_for(database, request); company_id = int(working_company(context))
        data = await form_data(request); require_csrf(context, data)
        enquiry = one(database, 'SELECT * FROM enquiries WHERE id=? AND company_id=?', (enquiry_id, company_id))
        if enquiry is None:
            return RedirectResponse('/operations/enquiries', 303)
        if str(enquiry['status']) == 'converted':
            return RedirectResponse('/operations/enquiries', 303)
        if str(enquiry['status']) != 'closed':
            with database.connect() as c:
                c.execute("UPDATE enquiries SET status='closed',updated_at=? WHERE id=? AND company_id=?", (iso_now(), enquiry_id, company_id))
            audit(database, context, company_id, 'ENQUIRY_RELEASED', 'enquiry', enquiry_id, before={'status': enquiry['status']}, after={'status': 'closed', 'availability_released': True})
        return RedirectResponse('/operations/enquiries', 303)

    @app.post('/operations/enquiries/{enquiry_id}/reopen')
    async def reopen_enquiry(enquiry_id: int, request: Request):
        context = context_for(database, request); company_id = int(working_company(context))
        data = await form_data(request); require_csrf(context, data)
        enquiry = one(database, 'SELECT * FROM enquiries WHERE id=? AND company_id=?', (enquiry_id, company_id))
        if enquiry is None or str(enquiry['status']) != 'closed':
            return RedirectResponse('/operations/enquiries', 303)
        elements=rows(database,'SELECT * FROM enquiry_elements WHERE enquiry_id=? AND company_id=? ORDER BY sort_order,id',(enquiry_id,company_id))
        if not elements:
            legacy=one(database,'SELECT element_id FROM enquiry_requests WHERE enquiry_id=? AND company_id=?',(enquiry_id,company_id))
            if legacy is not None and legacy['element_id'] is not None and enquiry['arrival_date'] and enquiry['departure_date']:
                elements=[{'element_id':legacy['element_id'],'arrival_date':enquiry['arrival_date'],'departure_date':enquiry['departure_date']}]
        if not elements:
            return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Availability+must+be+rechecked+before+this+Enquiry+can+be+reopened', 303)
        for element in elements:
            state=availability_state(database,company_id,int(element['element_id']),str(element['arrival_date']),str(element['departure_date']),exclude_enquiry_id=enquiry_id)
            if not state.get('available'):
                return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Cannot+reopen:+one+or+more+original+Elements+are+no+longer+available',303)
        with database.connect() as c:
            c.execute("UPDATE enquiries SET status='new',updated_at=? WHERE id=? AND company_id=?", (iso_now(), enquiry_id, company_id))
        audit(database, context, company_id, 'ENQUIRY_REOPENED', 'enquiry', enquiry_id, before={'status': 'closed'}, after={'status': 'new', 'availability_rechecked': True})
        return RedirectResponse(f'/operations/enquiries/{enquiry_id}?saved=1', 303)
