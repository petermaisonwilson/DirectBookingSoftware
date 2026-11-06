"""Connect the existing requirement form and confirmation writer to reservation edits.

All commands retain the operational source and canonical allocation identity and
run in the existing tenant inventory transaction. No collection replacement.
"""
import hashlib
import json

from fastapi.responses import HTMLResponse, RedirectResponse
from .app import esc, layout
from .database import iso_now
from .sql import execute_sql, insert_sql
from .setup015_core import audit
from .webv1_enquiry_builder import _calculate, _customer, _form_page, write_element_requirements, displayed_end_date
from .webv1_operational_reservations import save_enquiry, save_booking, write_booking_element, insert_financial
from .webv1_booking_finance import sync_payment_status
from .webv1_status_availability import availability_state


def allocations(c, master):
    return [dict(r) for r in execute_sql(c,
        'SELECT re.*,se.name AS element_name,se.element_type,se.pricing_method FROM reservation_elements re '
        'JOIN setup_elements se ON se.company_id=re.company_id AND se.id=re.element_id '
        'WHERE re.company_id=:tenant AND re.reservation_id=:reservation ORDER BY re.id',
        (master['company_id'], master['id'])).fetchall()]


def version(master, items):
    return hashlib.sha256(json.dumps([dict(master), items], sort_keys=True, default=str).encode()).hexdigest()


def editable(master):
    if master['state'] == 'CANCELLED':
        raise ValueError('Cancelled reservations cannot be amended.')


def form_values(database, master, item):
    values = {'source': '', 'notes': master['notes'] or ''}
    with database.connect() as c:
        source = execute_sql(c,'SELECT source FROM enquiries WHERE company_id=:tenant AND id=:id',(master['company_id'],master['enquiry_id'])).fetchone()
        if source: values['source'] = str(source['source'] or '')
    if item is None:
        return values
    values.update({k: str(item[k] or '') for k in ('element_type','element_id','arrival_date','departure_date')})
    bid = master['booking_id']
    table, field, key = ('booking_people','booking_element_id','booking_element_id') if bid else ('enquiry_element_people','enquiry_element_id','enquiry_element_id')
    with database.connect() as c:
        for p in execute_sql(c, f'SELECT person_type_id,quantity FROM {table} WHERE company_id=:tenant AND {field}=:element',
                             (master['company_id'], item[key])).fetchall():
            values[f'person_{p["person_type_id"]}'] = str(p['quantity'])
        table = 'booking_addons' if bid else 'enquiry_element_addons'
        for a in execute_sql(c, f'SELECT addon_id,quantity FROM {table} WHERE company_id=:tenant AND {field}=:element',
                             (master['company_id'], item[key])).fetchall():
            values[f'addon_{a["addon_id"]}'] = str(a['quantity'])
    snap = json.loads(item['pricing_snapshot_json'] or '{}')
    for aid in snap.get('selected_addons', []):
        values[f'addon_selected_{aid}'] = '1'
    for aid, when in snap.get('addon_when', {}).items():
        values[f'addon_when_{aid}'] = str(when)
    for aid, days in snap.get('addon_days', {}).items():
        for day, qty in days.items(): values[f'addon_day_{aid}_{day}'] = str(qty)
    for aid, people in snap.get('addon_people', {}).items():
        for pid, qty in people.items(): values[f'addon_person_{aid}_{pid}'] = str(qty)
    for aid, days in snap.get('addon_person_days', {}).items():
        for day, people in days.items():
            for pid, qty in people.items(): values[f'addon_day_person_{aid}_{pid}_{day}'] = str(qty)
    amendment = snap.get('amendment', {})
    values['manual_discount'] = str(amendment.get('manual_discount', 0))
    values['manual_discount_reason'] = str(amendment.get('manual_discount_reason', ''))
    return values


def return_to_summary(master):
    label = 'Return to Booking' if master['booking_id'] else 'Return to Enquiry'
    return f'<a class="button return-reservation-summary" href="/operations/reservations/{int(master["id"])}">{label}</a>'


def page(database, context, master, items, *, selected=None, adding=False, values=None, result=None, message=''):
    base = f'/operations/reservations/{master["id"]}'
    stamp = version(master, items)
    body = f'<p>{return_to_summary(master)} <a class="button secondary" href="{calendar_url(master,allocation_id=selected["id"] if selected else 0,adding=adding)}">Back to Master Calendar</a></p>'
    if message: body += f'<div class="error">{esc(message)}</div>'
    if selected is None and not adding:
        return layout('Edit Reservation Elements',body+calendar_panel(context,master,items),context)
    values = dict(form_values(database, master, selected) if values is None else values)
    customer = _customer(database, master['company_id'], master['customer_id'])
    rendered = _form_page(database, context, customer, values, enquiry_id=None, result=result, display_service_end=True,
        editor_path=base+'/edit',editor_back=base,
        editor_title='Add Reservation Element' if adding else ('Edit Booking Element' if master['booking_id'] else 'Edit Enquiry Element'))
    # Retain the established form, setup rules, add-on picker and calculation UI.
    hidden = (f'<input type="hidden" name="allocation_id" value="{selected["id"] if selected else ""}">'
              f'<input type="hidden" name="version" value="{esc(values.get("version", stamp))}">')
    if master['booking_id']:
        hidden += ('<label>Manual discount (booking currency)</label><input type="number" min="0" step="0.01" name="manual_discount" '
                   f'value="{esc(values.get("manual_discount", "0"))}"><label>Discount explanation (max 40 characters)</label>'
                   f'<input name="manual_discount_reason" maxlength="40" value="{esc(values.get("manual_discount_reason", ""))}">')
    form_tag = f'<form method="post" action="{base}/edit" id="integrated-enquiry-form">'
    from .webv1_payment_methods import _currency_symbol
    return rendered.replace(form_tag, body + form_tag + hidden, 1).replace('Save Enquiry', 'Save Element').replace('€', _currency_symbol(database,master['company_id']))


def calculate(database, cid, values):
    from .reservation_money import finite_amount
    result, errors, message = _calculate(database, cid, values)
    if message: raise ValueError(message)
    manual = finite_amount(values.get('manual_discount') or 0, 'manual discount')
    reason = str(values.get('manual_discount_reason') or '').strip()
    if manual < 0 or manual > float(result['total']): raise ValueError('Manual discount must be between zero and the Element total.')
    if len(reason) > 40: raise ValueError('Manual discount explanation must not exceed 40 characters.')
    result = dict(result, manual_discount=manual, manual_discount_reason=reason if manual else '',
                  total=round(float(result['total'])-manual, 2))
    return result


def save(database, context, master, values):
    cid, rid = master['company_id'], master['id']
    action = values.get('action', 'save')
    allocation_id = int(values.get('allocation_id') or 0)
    # Calculation is performed again inside the inventory lock before persistence.
    with database.reservation_write_transaction(cid) as c:
        current = dict(execute_sql(c, 'SELECT * FROM reservations WHERE company_id=:tenant AND id=:id', (cid,rid)).fetchone())
        editable(current)
        items = allocations(c, current)
        if values.get('version') != version(current, items):
            raise ValueError('This reservation has changed. Reopen the editor and review the current Elements before saving.')
        old = next((r for r in items if r['id'] == allocation_id), None)
        if allocation_id and old is None: raise ValueError('Element does not belong to this reservation.')
        if action == 'remove' and (old is None or len(items) <= 1):
            raise ValueError('A reservation must contain at least one Element.')
        result = None if action == 'remove' else calculate(database, cid, values)
        bid, eid = current['booking_id'], current['enquiry_id']
        if eid is None:
            raise ValueError('This reservation has no operational enquiry requirements to edit.')
        if bid:
            from .webv1_booking_amendments import _assert_amendable_booking
            _assert_amendable_booking(c,cid,bid)
        now = iso_now()
        if result:
            notes = str(values.get('notes',current['notes'] or ''))
            if bid:
                execute_sql(c,'UPDATE bookings SET notes=:notes WHERE company_id=:tenant AND id=:id',(notes,cid,bid))
            else:
                execute_sql(c,'UPDATE enquiries SET notes=:notes,source=:source WHERE company_id=:tenant AND id=:id',(notes,values.get('source',''),cid,eid))
        before = dict(old) if old else {}
        if old:
            try: before['pricing_method'] = json.loads(old.get('pricing_snapshot_json') or '{}').get('pricing_method') or old['pricing_method']
            except (TypeError,ValueError): before['pricing_method'] = old['pricing_method']
        before['provisional_total'] = old['total_amount'] if old else 0
        if old:
            prefix = 'booking' if bid else 'enquiry_element'
            field = 'booking_element_id' if bid else 'enquiry_element_id'
            for suffix,key in (('people','person_type_id'),('addons','addon_id')):
                before[suffix] = {str(r[key]):int(r['quantity']) for r in execute_sql(c,f'SELECT {key},quantity FROM {prefix}_{suffix} WHERE company_id=:tenant AND {field}=:id',(cid,old[field])).fetchall()}
        if result:
            validate_selection(database,context,current,items,old,result,values,connection=c)
        if action == 'remove':
            execute_sql(c,'DELETE FROM reservation_elements WHERE company_id=:tenant AND reservation_id=:reservation AND id=:id',(cid,rid,allocation_id))
        if bid:
            beid = old['booking_element_id'] if old else None
            if result:
                # The existing confirmation writer consumes the authoritative enquiry
                # requirements. Supply an in-transaction requirements row for this Element.
                ee = write_requirements(c,cid,eid,old['enquiry_element_id'] if old else None,values,result,now)
                beid = write_booking_element(c,database,cid,bid,{'id':eid},ee,beid=beid)
                remaining = sum(float(r['total_amount']) for r in items if r['id'] != allocation_id)
                new_total = round(remaining+result['total'],2)
                quote = dict(new_element_total=result['total'],manual_discount=result['manual_discount'],
                    manual_discount_reason=result['manual_discount_reason'], before=before)
                insert_financial(c,'booking_amendments',cid,bid,beid,
                    old['arrival_date'] if old else values['arrival_date'],old['departure_date'] if old else values['departure_date'],
                    values['arrival_date'],values['departure_date'],current['total_amount'],new_total,result['manual_discount'],
                    json.dumps(quote,separators=(',',':')),context['user_id'],now)
            else:
                new_total = round(float(current['total_amount'])-float(old['total_amount']),2)
                for table in ('booking_people','booking_addons'):
                    execute_sql(c,f'DELETE FROM {table} WHERE company_id=:tenant AND booking_element_id=:id',(cid,beid))
                execute_sql(c,'DELETE FROM booking_elements WHERE company_id=:tenant AND booking_id=:booking AND id=:id',(cid,bid,beid))
            source, parent, source_id = 'booking_elements', 'booking_id', bid
        else:
            if result: write_requirements(c,cid,eid,old['enquiry_element_id'] if old else None,values,result,now)
            source, parent, source_id = 'enquiry_elements','enquiry_id',eid
        if action == 'remove':
            eeid = old['enquiry_element_id']
            if eeid:
                for table in ('enquiry_element_people','enquiry_element_addons','enquiry_element_addon_days','enquiry_element_addon_people','enquiry_element_addon_person_days','enquiry_element_selected_addons'):
                    execute_sql(c,f'DELETE FROM {table} WHERE company_id=:tenant AND enquiry_element_id=:id',(cid,eeid))
                execute_sql(c,'DELETE FROM enquiry_elements WHERE company_id=:tenant AND enquiry_id=:enquiry AND id=:id',(cid,eid,eeid))
        bounds = execute_sql(c,f'SELECT MIN(arrival_date) a,MAX(departure_date) d FROM {source} WHERE company_id=:tenant AND {parent}=:parent',(cid,source_id)).fetchone()
        if bid:
            execute_sql(c,'UPDATE bookings SET arrival_date=:a,departure_date=:d,total_amount=:total,updated_at=:now WHERE company_id=:tenant AND id=:id',
                        (bounds['a'],bounds['d'],new_total,now,cid,bid))
            save_booking(c,cid,bid)
            if result:
                execute_sql(c,'UPDATE reservation_elements SET enquiry_element_id=:enquiry_element WHERE company_id=:tenant AND reservation_id=:reservation AND booking_element_id=:booking_element',(ee['id'],cid,rid,beid))
            sync_payment_status(database,cid,bid,connection=c,context=context)
        else:
            party = execute_sql(c,'SELECT SUM(party_size) n FROM enquiry_elements WHERE company_id=:tenant AND enquiry_id=:id',(cid,eid)).fetchone()['n']
            execute_sql(c,'UPDATE enquiries SET party_size=:party WHERE company_id=:tenant AND id=:id',(party,cid,eid))
            execute_sql(c,'UPDATE enquiries SET arrival_date=:a,departure_date=:d,updated_at=:now WHERE company_id=:tenant AND id=:id',
                        (bounds['a'],bounds['d'],now,cid,eid))
            # The request row is a compatibility summary, never a collection writer.
            total = execute_sql(c,'SELECT SUM(provisional_total) n FROM enquiry_elements WHERE company_id=:tenant AND enquiry_id=:id',(cid,eid)).fetchone()['n']
            execute_sql(c,'UPDATE enquiry_requests SET provisional_total=:total,pricing_snapshot_json=:snapshot,updated_at=:now WHERE company_id=:tenant AND enquiry_id=:id',
                        (total,json.dumps({'multi_element':True,'total':total}),now,cid,eid))
            save_enquiry(c,cid,eid)
        after = dict(result or {})
        after.update(operation='remove' if action == 'remove' else ('change' if old else 'add'),
            allocation_id=allocation_id or None, calculation=result,
            element_id=result['element_id'] if result else None,
            arrival_date=values.get('arrival_date') if result else None,
            departure_date=values.get('departure_date') if result else None,
            provisional_total=result['total'] if result else 0,
            people={str(k):v for k,v in result['people_counts'].items()} if result else {},
            addons={str(k):v for k,v in result['addon_counts'].items()} if result else {},
            old_booking_total=current['total_amount'],new_booking_total=new_total if bid else total,
            new_arrival=bounds['a'],new_departure=bounds['d'],
            manual_discount_private=result.get('manual_discount',0) if result else 0,
            elements=allocations(c,current))
        audit(database,context,cid,'BOOKING_AMENDED' if bid else 'ENQUIRY_UPDATED','booking' if bid else 'enquiry',bid or eid,
            before=before,after=after,connection=c)



def write_requirements(c,cid,eid,eeid,values,result,now):
    snapshot = dict(result)
    # Existing snapshots and confirmation use these string-keyed maps.
    snapshot_json = json.dumps(snapshot,separators=(',',':'))
    args = (result['element_type'],result['element_id'],values['arrival_date'],values['departure_date'],result['people_total'],result['total'],snapshot_json,now)
    if eeid:
        execute_sql(c,'UPDATE enquiry_elements SET element_type=:type,element_id=:element,arrival_date=:arrival,departure_date=:departure,party_size=:people,provisional_total=:total,pricing_snapshot_json=:snapshot,updated_at=:updated,recovery_state=\'held\' WHERE company_id=:tenant AND enquiry_id=:enquiry AND id=:id',args+(cid,eid,eeid))
    else:
        eeid = int(insert_sql(c,'INSERT INTO enquiry_elements(element_type,element_id,arrival_date,departure_date,party_size,provisional_total,pricing_snapshot_json,updated_at,company_id,enquiry_id,created_at,sort_order,lead_name) VALUES (:type,:element,:arrival,:departure,:people,:total,:snapshot,:updated,:tenant,:enquiry,:created,1,\'\')',args+(cid,eid,now)).lastrowid)
    write_element_requirements(c,cid,eeid,result)
    return dict(execute_sql(c,'SELECT ee.*,se.name element_name,se.pricing_method FROM enquiry_elements ee JOIN setup_elements se ON se.company_id=ee.company_id AND se.id=ee.element_id WHERE ee.company_id=:tenant AND ee.enquiry_id=:enquiry AND ee.id=:id',(cid,eid,eeid)).fetchone())


def calendar_url(master, *, allocation_id=0, adding=False):
    query = f'edit_reservation={int(master["id"])}'
    if allocation_id: query += f'&edit_allocation={int(allocation_id)}'
    if adding: query += '&reservation_add=1'
    return '/availability/calendar-v2?' + query


def calendar_context(database, cid, reservation_id, allocation_id=0):
    from fastapi import HTTPException
    with database.connect() as c:
        row = execute_sql(c,'SELECT * FROM reservations WHERE company_id=:tenant AND id=:id',(cid,reservation_id)).fetchone()
        if row is None: raise HTTPException(404,'Reservation not found')
        master = dict(row)
        editable(master)
        items = allocations(c,master)
    selected = next((r for r in items if r['id'] == allocation_id),None)
    if allocation_id and selected is None: raise HTTPException(404,'Reservation Element not found')
    return master, items, selected


def calendar_panel(context, master, items, selected=None, adding=False):
    base = f'/operations/reservations/{master["id"]}'
    cards = []
    for item in items:
        cards.append(f'<div class="reservation-calendar-item"><strong>{esc(item["element_name"])}</strong> '
            f'{esc(item["arrival_date"])} → {esc(displayed_end_date(item["departure_date"],item["pricing_method"]))} '
            f'<a class="button secondary" href="{calendar_url(master,allocation_id=item["id"])}">Change on calendar</a> '
            f'<a class="button secondary" href="{base}/edit?details=1&amp;allocation_id={item["id"]}">People / Add-ons</a> '
            f'<form method="post" action="{base}/edit" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}">'
            f'<input type="hidden" name="version" value="{version(master,items)}">'
            f'<input type="hidden" name="allocation_id" value="{item["id"]}">'
            '<button class="secondary" name="action" value="remove">Remove Element</button></form></div>')
    instruction = ('Choose free space below for the additional Element.' if adding else
        f'Choose a replacement range for {esc(selected["element_name"])}. Its current space remains reserved until you save.' if selected else
        'Click one of this reservation’s highlighted allocations to change it, or choose Add New Element.')
    return (f'<section class="card reservation-calendar-context" data-reservation="{master["id"]}">'
        f'<h2>Edit Reservation Elements — {esc(master["reference"])}</h2><p>{return_to_summary(master)}</p><p>{instruction}</p>'+''.join(cards)+
        f'<p><a class="button" href="{calendar_url(master,adding=True)}">Add New Element</a> '
        f'<a class="button secondary" href="{calendar_url(master)}">Cancel selection</a></p></section>')


def validate_selection(database, context, master, items, old, result, values, *, connection):
    """One final inventory validator for calendar choices and all direct form posts."""
    cid = master['company_id']
    start, end = str(values['arrival_date']), str(values['departure_date'])
    element = int(result['element_id'])
    state = availability_state(database,cid,element,start,end,session_token=str(context['token']),
        exclude_booking_id=master['booking_id'],exclude_enquiry_id=master['enquiry_id'])
    if not state['available']: raise ValueError(str(state['reason']))
    if any(r['id'] != (old['id'] if old else 0) and int(r['element_id']) == element
           and str(r['arrival_date']) < end and str(r['departure_date']) > start for r in items):
        raise ValueError('This Element is already allocated to overlapping dates in this reservation.')
    # Source-owned allocations also participate: excluding the whole reservation
    # from external availability must never exclude its sibling Elements.
    bid = master['booking_id']
    table, parent, owner, field = ('booking_elements','booking_id',bid,'booking_element_id') if bid else ('enquiry_elements','enquiry_id',master['enquiry_id'],'enquiry_element_id')
    conflict = execute_sql(connection,f'SELECT id FROM {table} WHERE company_id=:tenant AND {parent}=:owner '
        'AND element_id=:element AND arrival_date<:departure AND departure_date>:arrival AND id<>:selected LIMIT 1',
        (cid,owner,element,end,start,old[field] if old and old[field] else 0)).fetchone()
    if conflict: raise ValueError('This Element is already allocated to overlapping dates in this reservation.')


def normalise_review_dates(database, cid, values):
    """Convert the submitted service end once, using the tenant-owned Element method.

    Internal callers and old forms already supply an exclusive boundary. The
    explicit display field distinguishes the human date from that boundary.
    """
    if 'display_departure_date' not in values: return values
    from datetime import date, timedelta
    values = dict(values)
    with database.connect() as c:
        element = execute_sql(c,'SELECT pricing_method FROM setup_elements WHERE company_id=:tenant AND id=:id',(cid,int(values.get('element_id') or 0))).fetchone()
    if element is None: raise ValueError('Choose an active Element for this company.')
    try: end = date.fromisoformat(str(values['display_departure_date']))
    except (TypeError,ValueError): raise ValueError('Enter a valid end date.')
    if str(element['pricing_method']).strip().lower() in {'per day'}:
        end += timedelta(days=1)
    values['departure_date'] = end.isoformat()
    return values
