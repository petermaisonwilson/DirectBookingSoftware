from __future__ import annotations

import json
from datetime import date, datetime
from urllib.parse import quote_plus, unquote_plus

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

from .app import COOKIE_NAME, esc, form_data, layout
from .setup015_catalogue import setup_nav
from .setup015_core import audit, context_for, require_csrf, working_company, years
from .setup015_readiness import element_missing_items, incomplete_elements


CURRENCIES = {
    'EUR': ('€', 'Euro'),
    'GBP': ('£', 'Pound sterling'),
    'USD': ('$', 'US dollar'),
    'CHF': ('CHF ', 'Swiss franc'),
    'AUD': ('A$', 'Australian dollar'),
    'CAD': ('C$', 'Canadian dollar'),
    'NZD': ('NZ$', 'New Zealand dollar'),
}


def initialise_pricing_usability(database) -> None:
    with database.connect() as c:
        cols = {str(r['name']) for r in c.execute('PRAGMA table_info(companies)').fetchall()}
        if 'base_currency' not in cols:
            c.execute("ALTER TABLE companies ADD COLUMN base_currency TEXT NOT NULL DEFAULT 'EUR'")
        c.execute("UPDATE companies SET base_currency='EUR' WHERE base_currency IS NULL OR TRIM(base_currency)='' ")


def company_currency(database, company_id: int) -> str:
    with database.connect() as c:
        row = c.execute('SELECT base_currency FROM companies WHERE id=?', (company_id,)).fetchone()
    code = str(row['base_currency'] if row and row['base_currency'] else 'EUR').upper()
    return code if code in CURRENCIES else 'EUR'


def currency_symbol(database, company_id: int) -> str:
    return CURRENCIES[company_currency(database, company_id)][0]


def _append_duration_to_result(database, company_id: int, result: dict | None) -> None:
    if not result or not result.get('lines'):
        return
    nights = int(result.get('nights') or 0)
    element_id = int(result.get('element_id') or 0)
    with database.connect() as c:
        element = c.execute(
            'SELECT name,pricing_method FROM setup_elements WHERE company_id=? AND id=?',
            (company_id, element_id),
        ).fetchone()
        addons = {
            str(r['name']): (int(r['id']), str(r['pricing_method']))
            for r in c.execute(
                'SELECT id,name,pricing_method FROM setup_addons WHERE company_id=?',
                (company_id,),
            ).fetchall()
        }
    element_name = str(element['name']) if element else ''
    element_method = str(element['pricing_method']) if element else ''

    def method_basis(method: str) -> str:
        if method in {'Per night', 'Per person per night'}:
            return f'{nights} night(s)'
        if method == 'Per day':
            return f'{nights} day(s)'
        if method in {'Per stay', 'Per package'}:
            return '1 stay'
        if method == 'Per person':
            return 'per person for stay'
        if method in {'Per quantity per night'}:
            return f'{nights} night(s)'
        if method in {'Per quantity per day'}:
            return f'{nights} day(s)'
        if method == 'Fixed once':
            return '1 stay'
        if method == 'Per quantity':
            return 'quantity only'
        return f'{nights} night(s)' if nights else 'stay'

    for line in result['lines']:
        rule = str(line.get('rule') or '')
        if 'Duration:' in rule:
            continue
        item = str(line.get('item') or '')
        if item == element_name:
            basis = method_basis(element_method)
        elif item in addons:
            aid, method = addons[item]
            when = (result.get('addon_when') or {}).get(aid)
            if when is None:
                when = (result.get('addon_when') or {}).get(str(aid))
            if when == 'selected_days':
                used: set[str] = set()
                daily = (result.get('addon_days') or {}).get(aid)
                if daily is None:
                    daily = (result.get('addon_days') or {}).get(str(aid), {})
                for d, qty in (daily or {}).items():
                    if int(qty or 0) > 0:
                        used.add(str(d))
                person_days = (result.get('addon_person_days') or {}).get(aid)
                if person_days is None:
                    person_days = (result.get('addon_person_days') or {}).get(str(aid), {})
                for d, per_person in (person_days or {}).items():
                    if any(int(q or 0) > 0 for q in (per_person or {}).values()):
                        used.add(str(d))
                basis = f'{len(used)} selected day(s)'
            else:
                basis = method_basis(method)
        else:
            basis = method_basis(element_method)
        line['rule'] = (rule + '; ' if rule else '') + 'Duration: ' + basis


def install_pricing_calculation_transparency() -> None:
    from . import webv1_enquiry_builder as builder
    from . import setup015_calculator as calculator
    from . import webv1_bookings as bookings

    if not getattr(builder, '_duration_wrapper_installed', False):
        original_calculate = builder._calculate

        def calculate_with_duration(database, company_id: int, values: dict[str, str]):
            result, errors, message = original_calculate(database, company_id, values)
            _append_duration_to_result(database, company_id, result)
            return result, errors, message

        builder._calculate = calculate_with_duration
        builder._duration_wrapper_installed = True

    if not getattr(calculator, '_duration_wrapper_installed', False):
        original_page = calculator._page

        def page_with_duration(database, context, element_id: int = 0, submitted=None, errors=None, message='', result=None):
            if result:
                result = dict(result)
                result['lines'] = [dict(line) for line in result.get('lines', [])]
                result.setdefault('element_id', element_id)
                _append_duration_to_result(database, int(working_company(context)), result)
            return original_page(database, context, element_id, submitted, errors, message, result)

        calculator._page = page_with_duration
        calculator._duration_wrapper_installed = True

    if not getattr(bookings, '_base_currency_wrapper_installed', False):
        original_convert = bookings.convert_enquiry

        def convert_with_currency(database, context, cid: int, enquiry_id: int, workflow_status_id: int):
            booking_id = original_convert(database, context, cid, enquiry_id, workflow_status_id)
            code = company_currency(database, cid)
            with database.connect() as c:
                c.execute(
                    'UPDATE bookings SET currency=? WHERE id=? AND company_id=?',
                    (code, booking_id, cid),
                )
            return booking_id

        bookings.convert_enquiry = convert_with_currency
        bookings._base_currency_wrapper_installed = True


def _currency_card(database, context, company_id: int) -> str:
    code = company_currency(database, company_id)
    symbol, label = CURRENCIES[code]
    if context['role'] == 'supervisor':
        options = ''.join(
            f'<option value="{c}" {"selected" if c == code else ""}>{c} — {esc(name)} ({esc(sym.strip())})</option>'
            for c, (sym, name) in CURRENCIES.items()
        )
        return f'''<div class="card"><h2>Base currency</h2>
        <p>The Client's prices, Enquiries, Bookings and payments use this currency. Only the Supervisor can change it.</p>
        <form method="post" action="/company/base-currency"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
        <label>Base currency</label><select name="base_currency">{options}</select>
        <p><button>Save base currency</button></p></form></div>'''
    return f'''<div class="card"><h2>Base currency</h2><p><strong>{esc(code)} — {esc(label)} ({esc(symbol.strip())})</strong></p><p class="muted">Base currency is set by the Supervisor.</p></div>'''


def _setup_guidance_script() -> str:
    return '''<style id="setup-guidance-style">
#setup-guidance-box{position:fixed;right:18px;bottom:18px;width:min(310px,calc(100vw - 36px));z-index:1000;background:#fff;border:2px solid #c47a00;border-radius:10px;padding:14px;box-shadow:0 5px 20px rgba(0,0,0,.18)}
#setup-guidance-box.active{border-color:#3f8b4e}#setup-guidance-box h3{margin:0 0 8px}#setup-guidance-box p{margin:6px 0 10px}
</style><div id="setup-guidance-box" style="display:none"></div>
<script id="setup-guidance-script">(function(){
const box=document.getElementById('setup-guidance-box');if(!box)return;
const params=new URLSearchParams(location.search);const year=params.get('year')||'';
let focus=null;try{focus=JSON.parse(localStorage.getItem('directbookingSetupElement')||'null');}catch(e){}
let url='/setup/guidance'+(year?'?year='+encodeURIComponent(year):'');
if(focus&&focus.id)url+=(url.includes('?')?'&':'?')+'focus_element_id='+encodeURIComponent(focus.id);
fetch(url,{cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{
 if(!data)return;const chosen=data.focus&&data.focus.id?data.focus:(data.incomplete||[])[0];
 if(!chosen){box.style.display='none';return;}
 box.style.display='block';
 if(chosen.count===0){box.classList.add('active');box.innerHTML='<h3>✓ Element active</h3><button type="button" id="setup-guidance-close">CLOSE</button>';document.getElementById('setup-guidance-close').onclick=function(){localStorage.removeItem('directbookingSetupElement');box.style.display='none';};return;}
 box.classList.remove('active');box.innerHTML='<h3>⚠ Setup incomplete for “'+chosen.name.replace(/[&<>"']/g,'')+'”</h3><p><strong>'+chosen.count+' item(s) missing</strong></p><a class="button" id="setup-guidance-open" href="/setup/audit/element?year='+encodeURIComponent(data.year)+'&element_id='+encodeURIComponent(chosen.id)+'">View missing information</a>';
 document.getElementById('setup-guidance-open').onclick=function(){localStorage.setItem('directbookingSetupElement',JSON.stringify({id:chosen.id,name:chosen.name,year:data.year}));};
}).catch(()=>{});
})();</script>'''


def _addon_auto_one_script() -> str:
    return '''<script id="addon-auto-one">(function(){
function apply(){document.querySelectorAll('.addon-detail').forEach(function(d){if(d.style.display==='none')return;const rule=d.querySelector('[id^="addon-rule-"]');const input=d.querySelector('.addon-input');const when=d.querySelector('.addon-when');if(!rule||!input||input.disabled||(when&&when.value==='selected_days'))return;if(/min\s+1\s*,\s*max\s+1/i.test(rule.textContent||'')&&Number(input.value||0)===0)input.value='1';});}
const root=document.getElementById('integrated-enquiry-form');if(!root)return;new MutationObserver(apply).observe(root,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['style','disabled']});root.addEventListener('change',()=>setTimeout(apply,0));root.addEventListener('click',()=>setTimeout(apply,0));setTimeout(apply,0);
})();</script>'''


def _booking_duration_html(database, company_id: int, request_path: str) -> str:
    try:
        booking_id = int(request_path.rstrip('/').split('/')[-1])
    except ValueError:
        return ''
    with database.connect() as c:
        row = c.execute(
            'SELECT arrival_date,departure_date FROM bookings WHERE company_id=? AND id=?',
            (company_id, booking_id),
        ).fetchone()
    if not row or not row['arrival_date'] or not row['departure_date']:
        return ''
    try:
        nights = (date.fromisoformat(str(row['departure_date'])) - date.fromisoformat(str(row['arrival_date']))).days
    except ValueError:
        return ''
    return f'<p><strong>Duration:</strong> {nights} night(s)</p>'


def _move_season_maintenance(text: str) -> str:
    marker = '<div class="card"><h2>Season maintenance</h2>'
    start = text.find(marker)
    if start < 0:
        return text
    end = text.find('</div>', start)
    if end < 0:
        return text
    end += len('</div>')
    block = text[start:end]
    text = text[:start] + text[end:]
    pricing_form = '<form method="post" action="/setup/pricing">'
    where = text.find(pricing_form)
    if where < 0:
        return text + block
    return text[:where] + block + text[where:]


def register_pricing_usability_routes(app) -> None:
    database = app.state.database

    @app.post('/company/base-currency')
    async def set_base_currency(request: Request):
        context = context_for(database, request)
        if context['role'] != 'supervisor':
            raise HTTPException(status_code=403, detail='Supervisor only')
        data = await form_data(request)
        require_csrf(context, data)
        cid = int(working_company(context))
        new_code = str(data.get('base_currency', '')).upper().strip()
        if new_code not in CURRENCIES:
            return RedirectResponse('/company/settings?currency_error=' + quote_plus('Choose a valid base currency.'), 303)
        old_code = company_currency(database, cid)
        if new_code != old_code:
            with database.connect() as c:
                used = c.execute('SELECT COUNT(*) AS n FROM bookings WHERE company_id=?', (cid,)).fetchone()['n']
                if int(used):
                    return RedirectResponse('/company/settings?currency_error=' + quote_plus('Base currency is locked once this Client has Bookings.'), 303)
                c.execute('UPDATE companies SET base_currency=? WHERE id=?', (new_code, cid))
            audit(database, context, cid, 'BASE_CURRENCY_CHANGED', 'company', cid, {'base_currency': old_code}, {'base_currency': new_code})
        return RedirectResponse('/company/settings?saved=1', 303)

    @app.get('/setup/guidance')
    def setup_guidance(request: Request, year: str = '', focus_element_id: int = 0):
        context = context_for(database, request)
        cid = int(working_company(context))
        available = years(database, cid)
        try:
            selected = int(year) if year else (available[-1] if available else 0)
        except ValueError:
            selected = available[-1] if available else 0
        if selected not in available:
            selected = available[-1] if available else 0
        incomplete = incomplete_elements(database, cid, selected) if selected else []
        focus = None
        if focus_element_id and selected:
            with database.connect() as c:
                element = c.execute('SELECT id,name FROM setup_elements WHERE company_id=? AND id=? AND active=1', (cid, focus_element_id)).fetchone()
            if element:
                focus = {'id': int(element['id']), 'name': str(element['name']), 'count': len(element_missing_items(database, cid, selected, int(element['id'])))}
        return JSONResponse({'year': selected, 'incomplete': incomplete, 'focus': focus})

    @app.get('/setup/audit/element', response_class=HTMLResponse)
    def element_audit(request: Request, year: int, element_id: int):
        context = context_for(database, request)
        cid = int(working_company(context))
        with database.connect() as c:
            element = c.execute('SELECT id,name FROM setup_elements WHERE company_id=? AND id=? AND active=1', (cid, element_id)).fetchone()
        if element is None:
            return HTMLResponse(layout('Element Setup Audit', '<div class="error">Element not found.</div>', context), 404)
        missing = element_missing_items(database, cid, year, element_id)
        body = f'<h1>Setup Audit — {esc(element["name"])}</h1>{setup_nav()}'
        if missing:
            body += f'<div class="error"><strong>{len(missing)} missing item(s).</strong><br>Click an item to go to the Setup page containing the missing information.</div><div class="card"><h2>Missing information</h2><table><thead><tr><th>Area</th><th>Missing item</th></tr></thead><tbody>'
            for item in missing:
                body += f'<tr><td>{esc(item["category"])}</td><td><a href="{esc(item["href"])}">{esc(item["text"])}</a></td></tr>'
            body += '</tbody></table></div>'
        else:
            body += '<div class="ok"><h2>Element active</h2></div>'
        body += f'<p><a class="button secondary" href="/setup/elements">← Elements</a> <a class="button" href="/setup/audit/element?year={year}&element_id={element_id}">Re-run audit</a></p>'
        return layout('Element Setup Audit', body, context)

    @app.middleware('http')
    async def pricing_usability_html(request, call_next):
        response = await call_next(request)
        if response.status_code != 200 or 'text/html' not in response.headers.get('content-type', ''):
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        context = database.session_context(request.cookies.get(COOKIE_NAME))
        cid = None
        if context:
            cid = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
        if cid:
            cid = int(cid)
            symbol = currency_symbol(database, cid)
            if symbol != '€':
                text = text.replace('€', symbol)

            if request.url.path == '/company/settings':
                card = _currency_card(database, context, cid)
                marker = '<div class="card"><h3>Permission reminder</h3>'
                text = text.replace(marker, card + marker, 1) if marker in text else text.replace('</main>', card + '</main>', 1)
                err = request.query_params.get('currency_error', '')
                if err:
                    text = text.replace('<main>', '<main><div class="error">' + esc(unquote_plus(err)) + '</div>', 1)

            if request.url.path.startswith('/setup') and 'setup-guidance-script' not in text:
                text = text.replace('</body>', _setup_guidance_script() + '</body>', 1)

            if request.url.path == '/setup/pricing':
                text = _move_season_maintenance(text)

            if ('/enquiries/' in request.url.path or '/enquiries/new' in request.url.path) and 'integrated-enquiry-form' in text and 'addon-auto-one' not in text:
                text = text.replace('</body>', _addon_auto_one_script() + '</body>', 1)

            if request.url.path.startswith('/operations/bookings/') and request.method == 'GET':
                duration = _booking_duration_html(database, cid, request.url.path)
                if duration and '<div class="card"><h2>Frozen Booking</h2>' in text:
                    text = text.replace('<div class="card"><h2>Frozen Booking</h2>', '<div class="card"><h2>Frozen Booking</h2>' + duration, 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
