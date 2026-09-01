from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data, layout
from .setup015_core import audit, context_for, require_csrf, rows, working_company


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
    def addon_when_setup(request: Request, saved: int = 0):
        context = context_for(database, request)
        company_id = working_company(context)
        addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? ORDER BY active DESC,name COLLATE NOCASE', (company_id,))
        notice = '<div class="ok">Add-on timing choices saved.</div>' if saved else ''
        body = f'''<h1>Add-on Timings</h1><p><a href="/setup/addons">← Add-ons</a> &nbsp; <a href="/setup">Setup home</a></p>{notice}
        <div class="card"><p>For each Add-on the Client chooses which <strong>When?</strong> choices appear to the Client and Customer. Labels are Client-defined.</p>
        <p class="muted">For a Breakfast-style Add-on, enable both Every day and Selected days. Every day is always shown first; Selected days is second.</p></div>'''
        for addon in addons:
            options = {str(r['option_code']): r for r in when_options(database, company_id, int(addon['id']), active_only=False)}
            every = options.get('every_day')
            selected = options.get('selected_days')
            body += f'''<div class="card" id="addon-{int(addon['id'])}"><h2>{esc(addon['name'])}</h2><p class="muted">Pricing method: {esc(addon['pricing_method'])}</p>
            <form method="post" action="/setup/addons/when"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="addon_id" value="{int(addon['id'])}">
            <div class="grid"><div><label><input style="width:auto" type="checkbox" name="every_active" value="1" {'checked' if every and every['active'] else ''}> Enable every-day choice</label><label>Label</label><input name="every_label" value="{esc(every['label'] if every else 'Every day')}"></div>
            <div><label><input style="width:auto" type="checkbox" name="selected_active" value="1" {'checked' if selected and selected['active'] else ''}> Enable selected-days choice</label><label>Label</label><input name="selected_label" value="{esc(selected['label'] if selected else 'Selected days')}"></div></div>
            <p><button type="submit">Save When? choices</button></p></form></div>'''
        return layout('Add-on Timings', body, context)

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
        addon = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND id=?', (company_id, addon_id))
        if not addon:
            raise HTTPException(status_code=404, detail='Add-on not found')
        every_label = data.get('every_label', '').strip() or 'Every day'
        selected_label = data.get('selected_label', '').strip() or 'Selected days'
        every_active = 1 if data.get('every_active') == '1' else 0
        selected_active = 1 if data.get('selected_active') == '1' else 0
        if not every_active and not selected_active:
            every_active = 1
        before = [dict(r) for r in when_options(database, company_id, addon_id, active_only=False)]
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) VALUES (?,?,?,?,?,1) ON CONFLICT(company_id,addon_id,option_code) DO UPDATE SET label=excluded.label,active=excluded.active,sort_order=1",
                (company_id, addon_id, 'every_day', every_label, every_active),
            )
            connection.execute(
                "INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) VALUES (?,?,?,?,?,2) ON CONFLICT(company_id,addon_id,option_code) DO UPDATE SET label=excluded.label,active=excluded.active,sort_order=2",
                (company_id, addon_id, 'selected_days', selected_label, selected_active),
            )
        after = [dict(r) for r in when_options(database, company_id, addon_id, active_only=False)]
        audit(database, context, company_id, 'ADDON_WHEN_OPTIONS_SAVED', 'addon', addon_id, before, after)
        return RedirectResponse('/setup/addons/when?saved=1', status_code=303)

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
        dates = stay_dates(arrival, departure)
        body = f'''<h1>{esc(company['name'])} — Direct booking preview</h1>
        <div class="card"><p>This is a Customer-facing preview only. It does not create a Booking yet.</p>
        <form method="get" action="/customer/direct-booking-preview"><div class="grid"><div><label>Arrival</label><input id="preview-arrival" type="date" name="arrival" value="{esc(arrival)}"></div><div><label>Departure</label><input id="preview-departure" type="date" name="departure" value="{esc(departure)}"></div></div><p><button type="submit">Show stay</button></p></form></div>
        <div class="card"><h2>Optional extras</h2><p class="muted">This is how the Customer will see the Client-defined Add-on timing choices.</p>'''
        if not dates:
            body += '<p>Choose valid Arrival and Departure dates first.</p>'
        else:
            for addon in addons:
                opts = list(when_options(database, company_id, int(addon['id'])))
                if not opts:
                    continue
                option_html = ''.join(f'<option value="{esc(o["option_code"])}">{esc(o["label"])}</option>' for o in opts)
                body += f'''<div style="border-top:1px solid #dde3e9;padding:12px 0"><div class="grid"><div><strong>{esc(addon['name'])}</strong><br><span class="muted">{esc(addon['pricing_method'])}</span></div><div class="preview-main-qty" data-addon="{int(addon['id'])}"><label>Quantity</label><input class="preview-qty" data-addon="{int(addon['id'])}" type="number" min="0" value="0"></div><div><label>When?</label><select class="preview-when" data-addon="{int(addon['id'])}">{option_html}</select></div></div><div class="preview-days" id="preview-days-{int(addon['id'])}" style="margin-top:10px"></div></div>'''
        body += '</div>'
        if dates:
            dates_json = json.dumps(dates)
            body += f'''<script>(function(){{
const dates={dates_json};
function pretty(s){{const p=s.split('-');return new Date(Date.UTC(Number(p[0]),Number(p[1])-1,Number(p[2]))).toLocaleDateString(undefined,{{weekday:'short',day:'numeric',month:'short'}});}}
function render(sel){{
 const aid=sel.dataset.addon;const box=document.getElementById('preview-days-'+aid);const qtyWrap=document.querySelector('.preview-main-qty[data-addon="'+aid+'"]');
 if(sel.value==='selected_days'){{
   qtyWrap.style.display='none';
   box.innerHTML='<strong>Selected days</strong><p class="muted">Enter the quantity wanted on each date. Leave a date at 0 if none are required.</p><div class="grid">'+dates.map(function(d){{return '<div><label>'+pretty(d)+'</label><input class="preview-day-qty" data-addon="'+aid+'" data-date="'+d+'" type="number" min="0" value="0"></div>';}}).join('')+'</div>';
 }} else {{
   qtyWrap.style.display='block';
   box.innerHTML='';
 }}
}}
document.querySelectorAll('.preview-when').forEach(function(s){{s.addEventListener('change',function(){{render(s);}});render(s);}});
}})();</script>'''
        return layout('Direct booking preview', body, context)
