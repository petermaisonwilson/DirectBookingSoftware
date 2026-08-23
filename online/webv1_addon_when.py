from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data, layout
from .setup015_calculator import _addon_rule
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
        connection.execute("""INSERT OR IGNORE INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) SELECT company_id,id,'every_day','Every day',1,1 FROM setup_addons""")
        connection.execute("""INSERT OR IGNORE INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) SELECT company_id,id,'selected_days','Selected days',0,2 FROM setup_addons""")


def when_options(database, company_id: int, addon_id: int, *, active_only: bool = True):
    sql = 'SELECT * FROM setup_addon_when_options WHERE company_id=? AND addon_id=?'; params: list[object] = [company_id, addon_id]
    if active_only: sql += ' AND active=1'
    sql += ' ORDER BY sort_order, option_code'
    return rows(database, sql, tuple(params))


def when_payload(database, company_id: int, addons) -> dict[str, list[dict[str, object]]]:
    return {str(int(a['id'])):[{'code':str(r['option_code']),'label':str(r['label'])} for r in when_options(database,company_id,int(a['id']))] for a in addons}


def stay_dates(arrival: str, departure: str) -> list[str]:
    if not arrival or not departure: return []
    try: start=date.fromisoformat(arrival); end=date.fromisoformat(departure)
    except ValueError: return []
    if end<=start: return []
    return [(start+timedelta(days=i)).isoformat() for i in range((end-start).days)]


def register_addon_when_routes(app) -> None:
    database=app.state.database

    @app.get('/setup/addons/when', response_class=HTMLResponse)
    def addon_when_setup(request: Request, saved: int = 0, year: int = 0, error: str = ''):
        context=context_for(database,request); cid=working_company(context); addons=rows(database,'SELECT * FROM setup_addons WHERE company_id=? ORDER BY active DESC,name COLLATE NOCASE',(cid,)); people=rows(database,'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE',(cid,)); year_rows=rows(database,'SELECT year FROM setup_years WHERE company_id=? ORDER BY year DESC',(cid,)); available=[int(r['year']) for r in year_rows]; selected_year=year if year in available else (available[0] if available else 0)
        notice='<div class="ok">Add-on options saved.</div>' if saved else ''; error_html=f'<div class="error">{esc(error)}</div>' if error else ''; year_links=' '.join(f'<a class="button {"" if y==selected_year else "secondary"}" href="/setup/addons/when?year={y}">{y}</a>' for y in available) or '<span class="muted">Create a pricing year before entering Person Type rates.</span>'
        body=f'''<h1>Add-on Timings &amp; Person Pricing</h1><p><a href="/setup/addons">← Add-ons</a> &nbsp; <a href="/setup">Setup home</a></p>{notice}{error_html}<div class="card"><p>Keep ordinary extras on <strong>One price for everyone</strong>. For Breakfast-style extras, choose <strong>Price by Person Type</strong>.</p><p class="muted">Person Type short codes are used on compact booking screens. Keep them clear and descriptive, maximum 8 characters.</p><div>{year_links}</div></div>'''
        for addon in addons:
            aid=int(addon['id']); opts={str(r['option_code']):r for r in when_options(database,cid,aid,active_only=False)}; every=opts.get('every_day'); selected=opts.get('selected_days'); mode=addon_person_mode(database,cid,aid); rates=addon_person_rates(database,cid,aid,selected_year) if selected_year else {}; quantity_based=str(addon['pricing_method']) in {'Per quantity','Per quantity per night','Per quantity per day'}; rate_inputs=''
            for p in people:
                pid=int(p['id']); value='' if pid not in rates else f'{rates[pid]:.2f}'; short=str(p['short_name'] or p['name'])[:8]; rate_inputs+=f'<div><label>{esc(short)} price</label><input name="person_rate_{pid}" inputmode="decimal" value="{esc(value)}" placeholder="0.00"></div>'
            disabled='' if quantity_based else 'disabled'; note='' if quantity_based else '<p class="muted">This pricing method does not use quantities, so Person Type pricing is unavailable.</p>'
            body+=f'''<div class="card"><h2>{esc(addon['name'])}</h2><p class="muted">Pricing method: {esc(addon['pricing_method'])}</p>{note}<form method="post" action="/setup/addons/when"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="addon_id" value="{aid}"><input type="hidden" name="year" value="{selected_year}"><h3>When?</h3><div class="grid"><div><label><input style="width:auto" type="checkbox" name="every_active" value="1" {'checked' if every and every['active'] else ''}> Enable every-day choice</label><label>Label</label><input name="every_label" value="{esc(every['label'] if every else 'Every day')}"></div><div><label><input style="width:auto" type="checkbox" name="selected_active" value="1" {'checked' if selected and selected['active'] else ''}> Enable selected-days choice</label><label>Label</label><input name="selected_label" value="{esc(selected['label'] if selected else 'Selected days')}"></div></div><h3>Price</h3><label>How is this Add-on priced?</label><select class="addon-pricing-mode" data-addon="{aid}" name="pricing_mode" {disabled}><option value="single" {'selected' if mode=='single' else ''}>One price for everyone</option><option value="person_type" {'selected' if mode=='person_type' else ''}>Price by Person Type</option></select><div class="person-rate-panel" data-addon="{aid}" style="margin-top:12px"><div class="grid">{rate_inputs}</div></div><p><button>Save Add-on options</button></p></form></div>'''
        body+='''<script>(function(){function refresh(s){const p=document.querySelector('.person-rate-panel[data-addon="'+s.dataset.addon+'"]');if(p)p.style.display=s.value==='person_type'?'block':'none';}document.querySelectorAll('.addon-pricing-mode').forEach(function(s){s.addEventListener('change',function(){refresh(s);});refresh(s);});})();</script>'''
        return layout('Add-on Timings & Person Pricing',body,context)

    @app.post('/setup/addons/when')
    async def addon_when_save(request: Request):
        context=context_for(database,request); cid=working_company(context); data=await form_data(request); require_csrf(context,data)
        try: aid=int(data.get('addon_id',''))
        except ValueError: raise HTTPException(status_code=400,detail='Invalid Add-on')
        addon_rows=rows(database,'SELECT * FROM setup_addons WHERE company_id=? AND id=?',(cid,aid))
        if not addon_rows: raise HTTPException(status_code=404,detail='Add-on not found')
        addon=addon_rows[0]; every_label=data.get('every_label','').strip() or 'Every day'; selected_label=data.get('selected_label','').strip() or 'Selected days'; every_active=1 if data.get('every_active')=='1' else 0; selected_active=1 if data.get('selected_active')=='1' else 0
        if not every_active and not selected_active: every_active=1
        quantity_based=str(addon['pricing_method']) in {'Per quantity','Per quantity per night','Per quantity per day'}; pricing_mode=data.get('pricing_mode','single') if quantity_based else 'single'
        if pricing_mode not in {'single','person_type'}: pricing_mode='single'
        try: selected_year=int(data.get('year','0') or 0)
        except ValueError: selected_year=0
        people=rows(database,'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE',(cid,)); parsed={}
        if pricing_mode=='person_type':
            if not selected_year: return RedirectResponse('/setup/addons/when?error=Create+a+pricing+year+before+entering+Person+Type+rates',303)
            for p in people:
                raw=data.get(f'person_rate_{int(p["id"])}','').strip()
                try:
                    rate=float(raw)
                    if rate<0: raise ValueError
                except ValueError: return RedirectResponse(f'/setup/addons/when?year={selected_year}&error=Enter+a+valid+price+for+every+active+Person+Type',303)
                parsed[int(p['id'])]=rate
        before={'timing':[dict(r) for r in when_options(database,cid,aid,active_only=False)],'pricing_mode':addon_person_mode(database,cid,aid),'rates':addon_person_rates(database,cid,aid,selected_year) if selected_year else {}}
        with database.connect() as c:
            c.execute("INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) VALUES (?,?,?,?,?,1) ON CONFLICT(company_id,addon_id,option_code) DO UPDATE SET label=excluded.label,active=excluded.active,sort_order=1",(cid,aid,'every_day',every_label,every_active)); c.execute("INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) VALUES (?,?,?,?,?,2) ON CONFLICT(company_id,addon_id,option_code) DO UPDATE SET label=excluded.label,active=excluded.active,sort_order=2",(cid,aid,'selected_days',selected_label,selected_active)); c.execute("INSERT INTO setup_addon_person_pricing(company_id,addon_id,pricing_mode) VALUES (?,?,?) ON CONFLICT(company_id,addon_id) DO UPDATE SET pricing_mode=excluded.pricing_mode",(cid,aid,pricing_mode))
            if selected_year and pricing_mode=='person_type':
                for pid,rate in parsed.items(): c.execute("INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (?,?,?,?,?) ON CONFLICT(company_id,addon_id,year,person_type_id) DO UPDATE SET rate=excluded.rate",(cid,aid,selected_year,pid,rate))
        after={'timing':[dict(r) for r in when_options(database,cid,aid,active_only=False)],'pricing_mode':addon_person_mode(database,cid,aid),'rates':addon_person_rates(database,cid,aid,selected_year) if selected_year else {}}; audit(database,context,cid,'ADDON_OPTIONS_SAVED','addon',aid,before,after); suffix=f'&year={selected_year}' if selected_year else ''; return RedirectResponse(f'/setup/addons/when?saved=1{suffix}',303)

    @app.get('/customer/direct-booking-preview', response_class=HTMLResponse)
    def customer_direct_booking_preview(request: Request, arrival: str = '', departure: str = '', element: int = 0):
        context=database.session_context(request.cookies.get(COOKIE_NAME))
        if context is None: raise HTTPException(status_code=401,detail='Login required')
        if context['role']!='customer' or not context['company_id']: raise HTTPException(status_code=403,detail='Customer only')
        cid=int(context['company_id']); company=database.company(cid); addons=rows(database,'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE',(cid,)); people=rows(database,'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE',(cid,)); elements=rows(database,'SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name COLLATE NOCASE',(cid,)); dates=stay_dates(arrival,departure); year=int(arrival[:4]) if len(arrival)>=4 and arrival[:4].isdigit() else 0; selected_element=next((e for e in elements if int(e['id'])==int(element or 0)),None); person_counts={int(p['id']):max(0,int(request.query_params.get(f'person_{int(p["id"])}','0') or 0)) for p in people}
        element_options='<option value="">-- choose Element --</option>'+''.join(f'<option value="{int(e["id"])}" {"selected" if selected_element and int(e["id"])==int(selected_element["id"]) else ""}>{esc(e["element_type"])} — {esc(e["name"])}</option>' for e in elements)
        body=f'''<h1>{esc(company['name'])} — Direct booking preview</h1><div class="card"><p>This is a Customer-facing preview only. It does not create a Booking yet.</p><form method="get" action="/customer/direct-booking-preview"><div class="grid"><div><label>Arrival</label><input type="date" name="arrival" value="{esc(arrival)}"></div><div><label>Departure</label><input type="date" name="departure" value="{esc(departure)}"></div><div><label>Element</label><select name="element">{element_options}</select></div>'''
        for p in people:
            short=str(p['short_name'] or p['name'])[:8]; body+=f'<div><label>{esc(short)}</label><input type="number" min="0" name="person_{int(p["id"])}" value="{person_counts[int(p["id"])]}"></div>'
        body+='</div><p><button>Show stay</button></p></form></div><div class="card"><h2>Add-ons</h2><p class="muted">✕ N/A = Not available for selected Element.</p>'
        if not dates or not selected_element: body+='<p>Choose valid dates and an Element first.</p>'
        else:
            body+='<div style="display:grid;grid-template-columns:minmax(0,2fr) minmax(220px,1fr);gap:18px"><div id="preview-selected"><p class="muted" id="preview-none">No Add-ons selected.</p>'
            preview_meta={}; active_people=[p for p in people if person_counts[int(p['id'])]>0]
            for a in addons:
                aid=int(a['id']); mode=addon_person_mode(database,cid,aid); rates=addon_person_rates(database,cid,aid,year) if year else {}; opts=list(when_options(database,cid,aid)); option_html=''.join(f'<option value="{esc(o["option_code"])}">{esc(o["label"])}</option>' for o in opts); rule=_addon_rule(database,cid,year,selected_element,aid); preview_meta[str(aid)]={'allowed':bool(rule['allowed']),'mode':mode,'rates':{str(pid):rate for pid,rate in rates.items()}}
                if mode=='person_type': qty_html='<div class="grid">'+''.join(f'<div><label>{esc(str(p["short_name"] or p["name"])[:8])} <span class="muted">€{rates.get(int(p["id"]),0):.2f}</span></label><input type="number" min="0" max="{person_counts[int(p["id"])]}" value="0"></div>' for p in active_people)+'</div>'
                else: qty_html='<label>Quantity</label><input type="number" min="0" value="0">'
                body+=f'<div class="preview-detail card" data-addon="{aid}" style="display:none;margin:0 0 12px"><div style="display:flex;justify-content:space-between"><h3 style="margin:0">{esc(a["name"])}</h3><button type="button" class="secondary preview-remove" data-addon="{aid}">Remove</button></div><div class="preview-main" data-addon="{aid}">{qty_html}</div><div><label>When?</label><select class="preview-when" data-addon="{aid}">{option_html}</select></div><div id="preview-days-{aid}"></div></div>'
            body+='</div><aside class="card" style="margin:0"><h3 style="margin-top:0">Available Add-ons</h3><div id="preview-picker"></div></aside></div>'
            body+=f'''<script>(function(){{const dates={json.dumps(dates)},people={json.dumps([{'id':int(p['id']),'short':str(p['short_name'] or p['name'])[:8],'count':person_counts[int(p['id'])]} for p in people])},addons={json.dumps([{'id':int(a['id']),'name':str(a['name'])} for a in addons])},meta={json.dumps(preview_meta)},ename={json.dumps(str(selected_element['name']))};function pretty(s){{const p=s.split('-');return new Date(Date.UTC(+p[0],+p[1]-1,+p[2])).toLocaleDateString(undefined,{{weekday:'short',day:'numeric',month:'short'}});}}function activePeople(){{return people.filter(p=>p.count>0);}}function render(aid){{const d=document.querySelector('.preview-detail[data-addon="'+aid+'"]'),sel=d.querySelector('.preview-when'),box=document.getElementById('preview-days-'+aid),main=d.querySelector('.preview-main');if(sel.value==='selected_days'){{main.style.display='none';if(meta[aid].mode==='person_type')box.innerHTML=dates.map(dt=>'<div style="border-top:1px solid #eee;padding:8px 0"><strong>'+pretty(dt)+'</strong><div class="grid">'+activePeople().map(p=>'<div><label>'+p.short+'</label><input type="number" min="0" max="'+p.count+'" value="0"></div>').join('')+'</div></div>').join('');else box.innerHTML='<div class="grid">'+dates.map(dt=>'<div><label>'+pretty(dt)+'</label><input type="number" min="0" value="0"></div>').join('')+'</div>';}}else{{main.style.display='block';box.innerHTML='';}}}}function select(aid,on){{const d=document.querySelector('.preview-detail[data-addon="'+aid+'"]');d.style.display=on?'block':'none';if(on){{document.getElementById('preview-selected').appendChild(d);render(aid);}}document.getElementById('preview-none').style.display=document.querySelectorAll('.preview-detail[style*="block"]').length?'none':'block';}}const box=document.getElementById('preview-picker');addons.forEach(a=>{{const row=document.createElement('label');row.style.cssText='display:block;padding:7px 0;border-bottom:1px solid #eee';if(meta[String(a.id)].allowed)row.innerHTML='<input style="width:auto" class="preview-check" data-addon="'+a.id+'" type="checkbox"> '+a.name;else row.innerHTML='<span style="color:#b42318">'+a.name+' ✕ — N/A: '+ename+'</span>';box.appendChild(row);}});box.querySelectorAll('.preview-check').forEach(c=>c.addEventListener('change',()=>select(c.dataset.addon,c.checked)));document.querySelectorAll('.preview-remove').forEach(b=>b.addEventListener('click',()=>{{const c=document.querySelector('.preview-check[data-addon="'+b.dataset.addon+'"]');if(c)c.checked=false;select(b.dataset.addon,false);}}));document.querySelectorAll('.preview-when').forEach(s=>s.addEventListener('change',()=>render(s.dataset.addon)));}})();</script>'''
        body+='</div>'
        return layout('Direct booking preview',body,context)
