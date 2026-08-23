from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data, layout
from .setup015_core import audit, context_for, require_csrf, rows, working_company
from .webv1_addon_person import addon_person_mode, addon_person_rates


ADDON_WHEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_addon_when_options (
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    option_code TEXT NOT NULL CHECK(option_code IN ('every_day','selected_days')),
    label TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(company_id, addon_id, option_code)
);

CREATE TABLE IF NOT EXISTS enquiry_addon_days (
    enquiry_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    service_date TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    PRIMARY KEY(enquiry_id, addon_id, service_date)
);
"""


def initialise_addon_when(database) -> None:
    with database.connect() as connection:
        connection.executescript(ADDON_WHEN_SCHEMA)
        connection.execute(
            """INSERT OR IGNORE INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order)
               SELECT company_id,id,'every_day','Every day',1,1 FROM setup_addons"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order)
               SELECT company_id,id,'selected_days','Selected days',0,2 FROM setup_addons"""
        )


def when_options(database, company_id: int, addon_id: int, *, active_only: bool = True):
    sql = 'SELECT * FROM setup_addon_when_options WHERE company_id=? AND addon_id=?'
    params: list[object] = [company_id, addon_id]
    if active_only:
        sql += ' AND active=1'
    sql += ' ORDER BY sort_order, option_code'
    return rows(database, sql, tuple(params))


def when_payload(database, company_id: int, addons) -> dict[str, list[dict[str, object]]]:
    payload: dict[str, list[dict[str, object]]] = {}
    for addon in addons:
        payload[str(int(addon['id']))] = [
            {'code': str(row['option_code']), 'label': str(row['label'])}
            for row in when_options(database, company_id, int(addon['id']))
        ]
    return payload


def stay_dates(arrival: str, departure: str) -> list[str]:
    if not arrival or not departure:
        return []
    try:
        start = date.fromisoformat(arrival)
        end = date.fromisoformat(departure)
    except ValueError:
        return []
    if end <= start:
        return []
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]


def register_addon_when_routes(app) -> None:
    database = app.state.database

    @app.get('/setup/addons/when', response_class=HTMLResponse)
    def addon_when_setup(request: Request, saved: int = 0, year: int = 0, error: str = ''):
        context = context_for(database, request)
        company_id = working_company(context)
        addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? ORDER BY active DESC,name COLLATE NOCASE', (company_id,))
        people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
        year_rows = rows(database, 'SELECT year FROM setup_years WHERE company_id=? ORDER BY year DESC', (company_id,))
        available_years = [int(r['year']) for r in year_rows]
        selected_year = year if year in available_years else (available_years[0] if available_years else 0)
        notice = '<div class="ok">Add-on options saved.</div>' if saved else ''
        error_html = f'<div class="error">{esc(error)}</div>' if error else ''
        year_links = ' '.join(
            f'<a class="button {"" if y == selected_year else "secondary"}" href="/setup/addons/when?year={y}">{y}</a>'
            for y in available_years
        ) or '<span class="muted">Create a pricing year before entering Person Type rates.</span>'
        body = f'''<h1>Add-on Timings &amp; Person Pricing</h1><p><a href="/setup/addons">← Add-ons</a> &nbsp; <a href="/setup">Setup home</a></p>{notice}{error_html}
        <div class="card"><p>Keep ordinary extras on <strong>One price for everyone</strong>. For Breakfast-style extras, choose <strong>Price by Person Type</strong> so Adult and Child can have different prices.</p>
        <p class="muted">Person Type pricing is available for quantity-based Add-ons. Every day remains compact; Selected days expands only when the dates are needed.</p><div>{year_links}</div></div>'''
        for addon in addons:
            aid = int(addon['id'])
            options = {str(r['option_code']): r for r in when_options(database, company_id, aid, active_only=False)}
            every = options.get('every_day')
            selected = options.get('selected_days')
            mode = addon_person_mode(database, company_id, aid)
            rates = addon_person_rates(database, company_id, aid, selected_year) if selected_year else {}
            quantity_based = str(addon['pricing_method']) in {'Per quantity', 'Per quantity per night', 'Per quantity per day'}
            rate_inputs = ''
            for person in people:
                pid = int(person['id'])
                value = '' if pid not in rates else f'{rates[pid]:.2f}'
                rate_inputs += f'<div><label>{esc(person["name"])} price</label><input name="person_rate_{pid}" inputmode="decimal" value="{esc(value)}" placeholder="0.00"></div>'
            person_disabled = '' if quantity_based else 'disabled'
            mode_note = '' if quantity_based else '<p class="muted">This pricing method does not use quantities, so Person Type pricing is unavailable.</p>'
            body += f'''<div class="card" id="addon-{aid}"><h2>{esc(addon['name'])}</h2><p class="muted">Pricing method: {esc(addon['pricing_method'])}</p>{mode_note}
            <form method="post" action="/setup/addons/when"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="addon_id" value="{aid}"><input type="hidden" name="year" value="{selected_year}">
            <h3>When?</h3><div class="grid"><div><label><input style="width:auto" type="checkbox" name="every_active" value="1" {'checked' if every and every['active'] else ''}> Enable every-day choice</label><label>Label</label><input name="every_label" value="{esc(every['label'] if every else 'Every day')}"></div>
            <div><label><input style="width:auto" type="checkbox" name="selected_active" value="1" {'checked' if selected and selected['active'] else ''}> Enable selected-days choice</label><label>Label</label><input name="selected_label" value="{esc(selected['label'] if selected else 'Selected days')}"></div></div>
            <h3 style="margin-top:20px">Price</h3><label>How is this Add-on priced?</label><select class="addon-pricing-mode" data-addon="{aid}" name="pricing_mode" {person_disabled}><option value="single" {'selected' if mode == 'single' else ''}>One price for everyone</option><option value="person_type" {'selected' if mode == 'person_type' else ''}>Price by Person Type</option></select>
            <div class="person-rate-panel" data-addon="{aid}" style="margin-top:12px"><p><strong>Person Type prices {selected_year if selected_year else ''}</strong></p><div class="grid">{rate_inputs}</div></div>
            <p><button type="submit">Save Add-on options</button></p></form></div>'''
        body += '''<script>(function(){function refresh(s){const p=document.querySelector('.person-rate-panel[data-addon="'+s.dataset.addon+'"]');if(p)p.style.display=s.value==='person_type'?'block':'none';}document.querySelectorAll('.addon-pricing-mode').forEach(function(s){s.addEventListener('change',function(){refresh(s);});refresh(s);});})();</script>'''
        return layout('Add-on Timings & Person Pricing', body, context)

    @app.post('/setup/addons/when')
    async def addon_when_save(request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        data = await form_data(request)
        require_csrf(context, data)
        try:
            addon_id = int(data.get('addon_id', ''))
        except ValueError:
            raise HTTPException(status_code=400, detail='Invalid Add-on')
        addon_rows = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND id=?', (company_id, addon_id))
        if not addon_rows:
            raise HTTPException(status_code=404, detail='Add-on not found')
        addon = addon_rows[0]
        every_label = data.get('every_label', '').strip() or 'Every day'
        selected_label = data.get('selected_label', '').strip() or 'Selected days'
        every_active = 1 if data.get('every_active') == '1' else 0
        selected_active = 1 if data.get('selected_active') == '1' else 0
        if not every_active and not selected_active:
            every_active = 1
        quantity_based = str(addon['pricing_method']) in {'Per quantity', 'Per quantity per night', 'Per quantity per day'}
        pricing_mode = data.get('pricing_mode', 'single') if quantity_based else 'single'
        if pricing_mode not in {'single', 'person_type'}:
            pricing_mode = 'single'
        try:
            selected_year = int(data.get('year', '0') or 0)
        except ValueError:
            selected_year = 0
        people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
        parsed_rates: dict[int, float] = {}
        if pricing_mode == 'person_type':
            if not selected_year:
                return RedirectResponse('/setup/addons/when?error=Create+a+pricing+year+before+entering+Person+Type+rates', 303)
            for person in people:
                raw = data.get(f'person_rate_{int(person["id"])}', '').strip()
                try:
                    rate = float(raw)
                    if rate < 0:
                        raise ValueError
                except ValueError:
                    return RedirectResponse(f'/setup/addons/when?year={selected_year}&error=Enter+a+valid+price+for+every+active+Person+Type', 303)
                parsed_rates[int(person['id'])] = rate
        before = {
            'timing': [dict(r) for r in when_options(database, company_id, addon_id, active_only=False)],
            'pricing_mode': addon_person_mode(database, company_id, addon_id),
            'rates': addon_person_rates(database, company_id, addon_id, selected_year) if selected_year else {},
        }
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) VALUES (?,?,?,?,?,1) ON CONFLICT(company_id,addon_id,option_code) DO UPDATE SET label=excluded.label,active=excluded.active,sort_order=1",
                (company_id, addon_id, 'every_day', every_label, every_active),
            )
            connection.execute(
                "INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) VALUES (?,?,?,?,?,2) ON CONFLICT(company_id,addon_id,option_code) DO UPDATE SET label=excluded.label,active=excluded.active,sort_order=2",
                (company_id, addon_id, 'selected_days', selected_label, selected_active),
            )
            connection.execute(
                "INSERT INTO setup_addon_person_pricing(company_id,addon_id,pricing_mode) VALUES (?,?,?) ON CONFLICT(company_id,addon_id) DO UPDATE SET pricing_mode=excluded.pricing_mode",
                (company_id, addon_id, pricing_mode),
            )
            if selected_year and pricing_mode == 'person_type':
                for person_id, rate in parsed_rates.items():
                    connection.execute(
                        "INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (?,?,?,?,?) ON CONFLICT(company_id,addon_id,year,person_type_id) DO UPDATE SET rate=excluded.rate",
                        (company_id, addon_id, selected_year, person_id, rate),
                    )
        after = {
            'timing': [dict(r) for r in when_options(database, company_id, addon_id, active_only=False)],
            'pricing_mode': addon_person_mode(database, company_id, addon_id),
            'rates': addon_person_rates(database, company_id, addon_id, selected_year) if selected_year else {},
        }
        audit(database, context, company_id, 'ADDON_OPTIONS_SAVED', 'addon', addon_id, before, after)
        suffix = f'&year={selected_year}' if selected_year else ''
        return RedirectResponse(f'/setup/addons/when?saved=1{suffix}', status_code=303)

    @app.get('/customer/direct-booking-preview', response_class=HTMLResponse)
    def customer_direct_booking_preview(request: Request, arrival: str = '', departure: str = ''):
        context = database.session_context(request.cookies.get(COOKIE_NAME))
        if context is None:
            raise HTTPException(status_code=401, detail='Login required')
        if context['role'] != 'customer' or not context['company_id']:
            raise HTTPException(status_code=403, detail='Customer only')
        company_id = int(context['company_id'])
        company = database.company(company_id)
        addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
        people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
        dates = stay_dates(arrival, departure)
        pricing_year = int(arrival[:4]) if len(arrival) >= 4 and arrival[:4].isdigit() else 0
        people_json = json.dumps([{'id': int(p['id']), 'name': str(p['name'])} for p in people])
        mode_map: dict[str, str] = {}
        rate_map: dict[str, dict[str, float]] = {}
        body = f'''<h1>{esc(company['name'])} — Direct booking preview</h1>
        <div class="card"><p>This is a Customer-facing preview only. It does not create a Booking yet.</p>
        <form method="get" action="/customer/direct-booking-preview"><div class="grid"><div><label>Arrival</label><input id="preview-arrival" type="date" name="arrival" value="{esc(arrival)}"></div><div><label>Departure</label><input id="preview-departure" type="date" name="departure" value="{esc(departure)}"></div></div><p><button type="submit">Show stay</button></p></form></div>
        <div class="card"><h2>Optional extras</h2><p class="muted">This is how the Customer will see the Client-defined Add-on timing and Person Type pricing choices.</p>'''
        if not dates:
            body += '<p>Choose valid Arrival and Departure dates first.</p>'
        else:
            for addon in addons:
                aid = int(addon['id'])
                opts = list(when_options(database, company_id, aid))
                if not opts:
                    continue
                mode = addon_person_mode(database, company_id, aid)
                rates = addon_person_rates(database, company_id, aid, pricing_year) if pricing_year else {}
                mode_map[str(aid)] = mode
                rate_map[str(aid)] = {str(pid): rate for pid, rate in rates.items()}
                option_html = ''.join(f'<option value="{esc(o["option_code"])}">{esc(o["label"])}</option>' for o in opts)
                if mode == 'person_type':
                    qty_html = '<div class="grid">' + ''.join(f'<div><label>{esc(p["name"])} <span class="muted">€{rates.get(int(p["id"]), 0):.2f}</span></label><input class="preview-person-qty" data-addon="{aid}" data-person="{int(p["id"])}" type="number" min="0" value="0"></div>' for p in people) + '</div>'
                else:
                    qty_html = f'<label>Quantity</label><input class="preview-qty" data-addon="{aid}" type="number" min="0" value="0">'
                body += f'''<div style="border-top:1px solid #dde3e9;padding:12px 0"><div class="grid"><div><strong>{esc(addon['name'])}</strong><br><span class="muted">{esc(addon['pricing_method'])}{' — priced by Person Type' if mode == 'person_type' else ''}</span></div><div class="preview-main-qty" data-addon="{aid}">{qty_html}</div><div><label>When?</label><select class="preview-when" data-addon="{aid}">{option_html}</select></div></div><div class="preview-days" id="preview-days-{aid}" style="margin-top:10px"></div></div>'''
        body += '</div>'
        if dates:
            body += f'''<script>(function(){{
const dates={json.dumps(dates)};const people={people_json};const modes={json.dumps(mode_map)};const rates={json.dumps(rate_map)};
function pretty(s){{const p=s.split('-');return new Date(Date.UTC(Number(p[0]),Number(p[1])-1,Number(p[2]))).toLocaleDateString(undefined,{{weekday:'short',day:'numeric',month:'short'}});}}
function personInputs(aid,d){{return people.map(function(p){{const rate=((rates[aid]||{{}})[String(p.id)]);return '<div><label>'+p.name+(rate===undefined?'':' <span class="muted">€'+Number(rate).toFixed(2)+'</span>')+'</label><input class="preview-day-person-qty" data-addon="'+aid+'" data-person="'+p.id+'" data-date="'+d+'" type="number" min="0" value="0"></div>';}}).join('');}}
function render(sel){{
 const aid=sel.dataset.addon;const box=document.getElementById('preview-days-'+aid);const qtyWrap=document.querySelector('.preview-main-qty[data-addon="'+aid+'"]');
 if(sel.value==='selected_days'){{
   qtyWrap.style.display='none';
   if(modes[aid]==='person_type') box.innerHTML='<strong>Selected days</strong><p class="muted">Enter the Adult, Child or other Person Type quantity wanted on each date. Leave unwanted dates at 0.</p>'+dates.map(function(d){{return '<div style="border-top:1px solid #eee;padding:10px 0"><strong>'+pretty(d)+'</strong><div class="grid">'+personInputs(aid,d)+'</div></div>';}}).join('');
   else box.innerHTML='<strong>Selected days</strong><p class="muted">Enter the quantity wanted on each date. Leave a date at 0 if none are required.</p><div class="grid">'+dates.map(function(d){{return '<div><label>'+pretty(d)+'</label><input class="preview-day-qty" data-addon="'+aid+'" data-date="'+d+'" type="number" min="0" value="0"></div>';}}).join('')+'</div>';
 }} else {{qtyWrap.style.display='block';box.innerHTML='';}}
}}
document.querySelectorAll('.preview-when').forEach(function(s){{s.addEventListener('change',function(){{render(s);}});render(s);}});
}})();</script>'''
        return layout('Direct booking preview', body, context)
