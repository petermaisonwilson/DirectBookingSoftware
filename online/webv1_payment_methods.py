from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_core import audit, context_for, require_csrf, rows, working_company

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_method_definitions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, name TEXT NOT NULL,
 method_type TEXT NOT NULL DEFAULT 'manual', display_order INTEGER NOT NULL DEFAULT 10,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(company_id,name COLLATE NOCASE));
CREATE INDEX IF NOT EXISTS idx_payment_methods_company ON payment_method_definitions(company_id,active,display_order,name);
CREATE TABLE IF NOT EXISTS payment_rules (
 company_id INTEGER PRIMARY KEY,
 deposit_type TEXT NOT NULL DEFAULT 'fixed',
 deposit_value REAL NOT NULL DEFAULT 0,
 full_payment_threshold REAL,
 balance_due_days INTEGER NOT NULL DEFAULT 0,
 always_require_full_payment INTEGER NOT NULL DEFAULT 0,
 updated_by_user_id INTEGER,
 updated_at TEXT NOT NULL);
"""
DEFAULTS=(("Card","card",10),("Cash","manual",20),("Cheque","manual",30),("Credit note","manual",40))


def initialise_payment_methods(database):
    with database.connect() as c:
        c.executescript(SCHEMA); now=iso_now()
        for company in c.execute("SELECT id FROM companies").fetchall():
            cid=int(company["id"])
            if not int(c.execute("SELECT COUNT(*) AS n FROM payment_method_definitions WHERE company_id=?",(cid,)).fetchone()["n"]):
                for name,kind,order_no in DEFAULTS:
                    c.execute("INSERT INTO payment_method_definitions(company_id,name,method_type,display_order,active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",(cid,name,kind,order_no,now,now))
            c.execute("""INSERT OR IGNORE INTO payment_rules
                (company_id,deposit_type,deposit_value,full_payment_threshold,balance_due_days,always_require_full_payment,updated_at)
                VALUES (?,'fixed',0,NULL,0,0,?)""",(cid,now))



def _currency_code(database, company_id:int) -> str:
    with database.connect() as c:
        row=c.execute('SELECT currency FROM companies WHERE id=?',(company_id,)).fetchone()
        return str(row['currency'] or 'EUR').upper() if row else 'EUR'


def _currency_symbol(database, company_id:int) -> str:
    code=_currency_code(database,company_id)
    symbols={'EUR':'€','GBP':'£','USD':'USD $','AUD':'AUD $','CAD':'CAD $','NZD':'NZD $','CHF':'CHF'}
    return symbols.get(code,code)


def payment_rule(database, company_id:int):
    result=rows(database,"SELECT * FROM payment_rules WHERE company_id=?",(company_id,))
    return result[0] if result else None


def register_payment_method_routes(app):
    database=app.state.database

    @app.get("/setup/payment-methods",response_class=HTMLResponse)
    def page(request:Request,saved:int=0,message:str=''):
        context=context_for(database,request); cid=int(working_company(context))
        methods=rows(database,"SELECT * FROM payment_method_definitions WHERE company_id=? ORDER BY display_order,name",(cid,)); currency_symbol=_currency_symbol(database,cid)
        rule=payment_rule(database,cid)
        trs="".join(f'<tr><td>{int(m["display_order"])}</td><td>{esc(m["name"])}</td><td>{"Card provider" if m["method_type"]=="card" else "Manual payment"}</td><td>{"Active" if int(m["active"]) else "Inactive"}</td><td><form method="post" action="/setup/payment-methods/toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{int(m["id"])}"><button class="secondary">{"Deactivate" if int(m["active"]) else "Reactivate"}</button></form></td></tr>' for m in methods)
        deposit_type=str(rule["deposit_type"] if rule else "fixed")
        deposit_value=float(rule["deposit_value"] if rule else 0)
        threshold='' if rule is None or rule["full_payment_threshold"] is None else f'{float(rule["full_payment_threshold"]):.2f}'
        balance_days=int(rule["balance_due_days"] if rule else 0)
        full_checked='checked' if rule and int(rule["always_require_full_payment"]) else ''
        error=f'<div class="error">{esc(message)}</div>' if message else ''
        body=f'''<h1>Payment Methods</h1><p><a class="button secondary" href="/setup">Setup home</a></p>{error}{"<div class='ok'>Saved.</div>" if saved else ""}
        <div class="card"><h2>Payment / Deposit Rules</h2><p>These rules determine the minimum payment required when a Booking is confirmed.</p>
        <form method="post" action="/setup/payment-rules"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid">
        <div><label>Deposit type</label><select name="deposit_type"><option value="fixed" {"selected" if deposit_type=="fixed" else ""}>Fixed {esc(currency_symbol)} amount</option><option value="percent" {"selected" if deposit_type=="percent" else ""}>Percentage of Booking total</option></select></div>
        <div><label>Deposit value</label><input type="number" min="0" step="0.01" name="deposit_value" value="{deposit_value:.2f}"></div>
        <div><label>Full-payment threshold ({esc(currency_symbol)})</label><input type="number" min="0" step="0.01" name="full_payment_threshold" value="{esc(threshold)}" placeholder="blank = disabled"></div>
        <div><label>Balance due before arrival (days)</label><input type="number" min="0" step="1" name="balance_due_days" value="{balance_days}"></div></div>
        <p><label style="display:inline-flex;align-items:center;gap:7px;width:auto"><input style="width:auto;margin:0" type="checkbox" name="always_require_full_payment" value="1" {full_checked}><span>Always Require Full Payment</span></label></p>
        <p class="muted">Precedence: Always Require Full Payment; then full-payment threshold; then balance-due period; otherwise the configured fixed or percentage deposit.</p>
        <p><button>Save Payment Rules</button></p></form></div>
        <div class="card"><h2>Add Payment Method</h2><p>These choices are used when an operator confirms a Booking. Card methods are for a payment provider; DBS does not store card numbers or security codes.</p><form method="post" action="/setup/payment-methods"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Type</label><select name="method_type"><option value="manual">Manual payment</option><option value="card">Card provider</option></select></div><div><label>Display order</label><input type="number" name="display_order" value="50"></div></div><p><button>Save Payment Method</button></p></form></div>
        <div class="card"><table><thead><tr><th>Order</th><th>Name</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{trs}</tbody></table></div>'''
        return layout("Payment Methods",body,context)

    @app.post("/setup/payment-rules")
    async def save_rules(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        kind=str(data.get("deposit_type","fixed")).strip()
        try:
            value=round(float(str(data.get("deposit_value","0")).replace(",",".")),2)
            threshold_raw=str(data.get("full_payment_threshold","")).strip()
            threshold=round(float(threshold_raw.replace(",",".")),2) if threshold_raw else None
            balance_days=int(str(data.get("balance_due_days","0")).strip() or "0")
        except ValueError:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        if kind not in {"fixed","percent"} or value<0 or (kind=="percent" and value>100) or (threshold is not None and threshold<0) or balance_days<0:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        always=1 if data.get("always_require_full_payment")=="1" else 0; now=iso_now()
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_rules WHERE company_id=?",(cid,)).fetchone()
            c.execute("""INSERT INTO payment_rules(company_id,deposit_type,deposit_value,full_payment_threshold,balance_due_days,always_require_full_payment,updated_by_user_id,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(company_id) DO UPDATE SET deposit_type=excluded.deposit_type,deposit_value=excluded.deposit_value,full_payment_threshold=excluded.full_payment_threshold,balance_due_days=excluded.balance_due_days,always_require_full_payment=excluded.always_require_full_payment,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
                (cid,kind,value,threshold,balance_days,always,context["user_id"],now))
        audit(database,context,cid,"PAYMENT_RULES_SAVED","payment_rules",cid,dict(before) if before else None,{"deposit_type":kind,"deposit_value":value,"full_payment_threshold":threshold,"balance_due_days":balance_days,"always_require_full_payment":always})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods")
    async def save(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        name=str(data.get("name","")).strip(); kind=str(data.get("method_type","manual"))
        try: order_no=int(data.get("display_order","50") or 50)
        except ValueError: order_no=50
        if not name or kind not in {"manual","card"}: return RedirectResponse("/setup/payment-methods",303)
        now=iso_now()
        with database.connect() as c: mid=int(c.execute("INSERT INTO payment_method_definitions(company_id,name,method_type,display_order,active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",(cid,name,kind,order_no,now,now)).lastrowid)
        audit(database,context,cid,"PAYMENT_METHOD_SAVED","payment_method",mid,after={"name":name,"method_type":kind,"display_order":order_no})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods/toggle")
    async def toggle(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try: mid=int(data.get("id",""))
        except ValueError: return RedirectResponse("/setup/payment-methods",303)
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_method_definitions WHERE company_id=? AND id=?",(cid,mid)).fetchone()
            if not before: return RedirectResponse("/setup/payment-methods",303)
            active=0 if int(before["active"]) else 1
            c.execute("UPDATE payment_method_definitions SET active=?,updated_at=? WHERE company_id=? AND id=?",(active,iso_now(),cid,mid))
        audit(database,context,cid,"PAYMENT_METHOD_TOGGLED","payment_method",mid,dict(before),{"active":active})
        return RedirectResponse("/setup/payment-methods",303)
,'AUD':'A
    result=rows(database,"SELECT * FROM payment_rules WHERE company_id=?",(company_id,))
    return result[0] if result else None


def register_payment_method_routes(app):
    database=app.state.database

    @app.get("/setup/payment-methods",response_class=HTMLResponse)
    def page(request:Request,saved:int=0,message:str=''):
        context=context_for(database,request); cid=int(working_company(context))
        methods=rows(database,"SELECT * FROM payment_method_definitions WHERE company_id=? ORDER BY display_order,name",(cid,))
        rule=payment_rule(database,cid)
        trs="".join(f'<tr><td>{int(m["display_order"])}</td><td>{esc(m["name"])}</td><td>{"Card provider" if m["method_type"]=="card" else "Manual payment"}</td><td>{"Active" if int(m["active"]) else "Inactive"}</td><td><form method="post" action="/setup/payment-methods/toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{int(m["id"])}"><button class="secondary">{"Deactivate" if int(m["active"]) else "Reactivate"}</button></form></td></tr>' for m in methods)
        deposit_type=str(rule["deposit_type"] if rule else "fixed")
        deposit_value=float(rule["deposit_value"] if rule else 0)
        threshold='' if rule is None or rule["full_payment_threshold"] is None else f'{float(rule["full_payment_threshold"]):.2f}'
        balance_days=int(rule["balance_due_days"] if rule else 0)
        full_checked='checked' if rule and int(rule["always_require_full_payment"]) else ''
        error=f'<div class="error">{esc(message)}</div>' if message else ''
        body=f'''<h1>Payment Methods</h1><p><a class="button secondary" href="/setup">Setup home</a></p>{error}{"<div class='ok'>Saved.</div>" if saved else ""}
        <div class="card"><h2>Payment / Deposit Rules</h2><p>These rules determine the minimum payment required when a Booking is confirmed.</p>
        <form method="post" action="/setup/payment-rules"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid">
        <div><label>Deposit type</label><select name="deposit_type"><option value="fixed" {"selected" if deposit_type=="fixed" else ""}>Fixed € amount</option><option value="percent" {"selected" if deposit_type=="percent" else ""}>Percentage of Booking total</option></select></div>
        <div><label>Deposit value</label><input type="number" min="0" step="0.01" name="deposit_value" value="{deposit_value:.2f}"></div>
        <div><label>Full-payment threshold (€)</label><input type="number" min="0" step="0.01" name="full_payment_threshold" value="{esc(threshold)}" placeholder="blank = disabled"></div>
        <div><label>Balance due before arrival (days)</label><input type="number" min="0" step="1" name="balance_due_days" value="{balance_days}"></div></div>
        <p><label><input type="checkbox" name="always_require_full_payment" value="1" {full_checked}> Always Require Full Payment</label></p>
        <p class="muted">Precedence: Always Require Full Payment; then full-payment threshold; then balance-due period; otherwise the configured fixed or percentage deposit.</p>
        <p><button>Save Payment Rules</button></p></form></div>
        <div class="card"><h2>Add Payment Method</h2><p>These choices are used when an operator confirms a Booking. Card methods are for a payment provider; DBS does not store card numbers or security codes.</p><form method="post" action="/setup/payment-methods"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Type</label><select name="method_type"><option value="manual">Manual payment</option><option value="card">Card provider</option></select></div><div><label>Display order</label><input type="number" name="display_order" value="50"></div></div><p><button>Save Payment Method</button></p></form></div>
        <div class="card"><table><thead><tr><th>Order</th><th>Name</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{trs}</tbody></table></div>'''
        return layout("Payment Methods",body,context)

    @app.post("/setup/payment-rules")
    async def save_rules(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        kind=str(data.get("deposit_type","fixed")).strip()
        try:
            value=round(float(str(data.get("deposit_value","0")).replace(",",".")),2)
            threshold_raw=str(data.get("full_payment_threshold","")).strip()
            threshold=round(float(threshold_raw.replace(",",".")),2) if threshold_raw else None
            balance_days=int(str(data.get("balance_due_days","0")).strip() or "0")
        except ValueError:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        if kind not in {"fixed","percent"} or value<0 or (kind=="percent" and value>100) or (threshold is not None and threshold<0) or balance_days<0:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        always=1 if data.get("always_require_full_payment")=="1" else 0; now=iso_now()
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_rules WHERE company_id=?",(cid,)).fetchone()
            c.execute("""INSERT INTO payment_rules(company_id,deposit_type,deposit_value,full_payment_threshold,balance_due_days,always_require_full_payment,updated_by_user_id,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(company_id) DO UPDATE SET deposit_type=excluded.deposit_type,deposit_value=excluded.deposit_value,full_payment_threshold=excluded.full_payment_threshold,balance_due_days=excluded.balance_due_days,always_require_full_payment=excluded.always_require_full_payment,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
                (cid,kind,value,threshold,balance_days,always,context["user_id"],now))
        audit(database,context,cid,"PAYMENT_RULES_SAVED","payment_rules",cid,dict(before) if before else None,{"deposit_type":kind,"deposit_value":value,"full_payment_threshold":threshold,"balance_due_days":balance_days,"always_require_full_payment":always})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods")
    async def save(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        name=str(data.get("name","")).strip(); kind=str(data.get("method_type","manual"))
        try: order_no=int(data.get("display_order","50") or 50)
        except ValueError: order_no=50
        if not name or kind not in {"manual","card"}: return RedirectResponse("/setup/payment-methods",303)
        now=iso_now()
        with database.connect() as c: mid=int(c.execute("INSERT INTO payment_method_definitions(company_id,name,method_type,display_order,active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",(cid,name,kind,order_no,now,now)).lastrowid)
        audit(database,context,cid,"PAYMENT_METHOD_SAVED","payment_method",mid,after={"name":name,"method_type":kind,"display_order":order_no})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods/toggle")
    async def toggle(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try: mid=int(data.get("id",""))
        except ValueError: return RedirectResponse("/setup/payment-methods",303)
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_method_definitions WHERE company_id=? AND id=?",(cid,mid)).fetchone()
            if not before: return RedirectResponse("/setup/payment-methods",303)
            active=0 if int(before["active"]) else 1
            c.execute("UPDATE payment_method_definitions SET active=?,updated_at=? WHERE company_id=? AND id=?",(active,iso_now(),cid,mid))
        audit(database,context,cid,"PAYMENT_METHOD_TOGGLED","payment_method",mid,dict(before),{"active":active})
        return RedirectResponse("/setup/payment-methods",303)
,'CAD':'C
    result=rows(database,"SELECT * FROM payment_rules WHERE company_id=?",(company_id,))
    return result[0] if result else None


def register_payment_method_routes(app):
    database=app.state.database

    @app.get("/setup/payment-methods",response_class=HTMLResponse)
    def page(request:Request,saved:int=0,message:str=''):
        context=context_for(database,request); cid=int(working_company(context))
        methods=rows(database,"SELECT * FROM payment_method_definitions WHERE company_id=? ORDER BY display_order,name",(cid,))
        rule=payment_rule(database,cid)
        trs="".join(f'<tr><td>{int(m["display_order"])}</td><td>{esc(m["name"])}</td><td>{"Card provider" if m["method_type"]=="card" else "Manual payment"}</td><td>{"Active" if int(m["active"]) else "Inactive"}</td><td><form method="post" action="/setup/payment-methods/toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{int(m["id"])}"><button class="secondary">{"Deactivate" if int(m["active"]) else "Reactivate"}</button></form></td></tr>' for m in methods)
        deposit_type=str(rule["deposit_type"] if rule else "fixed")
        deposit_value=float(rule["deposit_value"] if rule else 0)
        threshold='' if rule is None or rule["full_payment_threshold"] is None else f'{float(rule["full_payment_threshold"]):.2f}'
        balance_days=int(rule["balance_due_days"] if rule else 0)
        full_checked='checked' if rule and int(rule["always_require_full_payment"]) else ''
        error=f'<div class="error">{esc(message)}</div>' if message else ''
        body=f'''<h1>Payment Methods</h1><p><a class="button secondary" href="/setup">Setup home</a></p>{error}{"<div class='ok'>Saved.</div>" if saved else ""}
        <div class="card"><h2>Payment / Deposit Rules</h2><p>These rules determine the minimum payment required when a Booking is confirmed.</p>
        <form method="post" action="/setup/payment-rules"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid">
        <div><label>Deposit type</label><select name="deposit_type"><option value="fixed" {"selected" if deposit_type=="fixed" else ""}>Fixed € amount</option><option value="percent" {"selected" if deposit_type=="percent" else ""}>Percentage of Booking total</option></select></div>
        <div><label>Deposit value</label><input type="number" min="0" step="0.01" name="deposit_value" value="{deposit_value:.2f}"></div>
        <div><label>Full-payment threshold (€)</label><input type="number" min="0" step="0.01" name="full_payment_threshold" value="{esc(threshold)}" placeholder="blank = disabled"></div>
        <div><label>Balance due before arrival (days)</label><input type="number" min="0" step="1" name="balance_due_days" value="{balance_days}"></div></div>
        <p><label><input type="checkbox" name="always_require_full_payment" value="1" {full_checked}> Always Require Full Payment</label></p>
        <p class="muted">Precedence: Always Require Full Payment; then full-payment threshold; then balance-due period; otherwise the configured fixed or percentage deposit.</p>
        <p><button>Save Payment Rules</button></p></form></div>
        <div class="card"><h2>Add Payment Method</h2><p>These choices are used when an operator confirms a Booking. Card methods are for a payment provider; DBS does not store card numbers or security codes.</p><form method="post" action="/setup/payment-methods"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Type</label><select name="method_type"><option value="manual">Manual payment</option><option value="card">Card provider</option></select></div><div><label>Display order</label><input type="number" name="display_order" value="50"></div></div><p><button>Save Payment Method</button></p></form></div>
        <div class="card"><table><thead><tr><th>Order</th><th>Name</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{trs}</tbody></table></div>'''
        return layout("Payment Methods",body,context)

    @app.post("/setup/payment-rules")
    async def save_rules(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        kind=str(data.get("deposit_type","fixed")).strip()
        try:
            value=round(float(str(data.get("deposit_value","0")).replace(",",".")),2)
            threshold_raw=str(data.get("full_payment_threshold","")).strip()
            threshold=round(float(threshold_raw.replace(",",".")),2) if threshold_raw else None
            balance_days=int(str(data.get("balance_due_days","0")).strip() or "0")
        except ValueError:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        if kind not in {"fixed","percent"} or value<0 or (kind=="percent" and value>100) or (threshold is not None and threshold<0) or balance_days<0:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        always=1 if data.get("always_require_full_payment")=="1" else 0; now=iso_now()
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_rules WHERE company_id=?",(cid,)).fetchone()
            c.execute("""INSERT INTO payment_rules(company_id,deposit_type,deposit_value,full_payment_threshold,balance_due_days,always_require_full_payment,updated_by_user_id,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(company_id) DO UPDATE SET deposit_type=excluded.deposit_type,deposit_value=excluded.deposit_value,full_payment_threshold=excluded.full_payment_threshold,balance_due_days=excluded.balance_due_days,always_require_full_payment=excluded.always_require_full_payment,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
                (cid,kind,value,threshold,balance_days,always,context["user_id"],now))
        audit(database,context,cid,"PAYMENT_RULES_SAVED","payment_rules",cid,dict(before) if before else None,{"deposit_type":kind,"deposit_value":value,"full_payment_threshold":threshold,"balance_due_days":balance_days,"always_require_full_payment":always})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods")
    async def save(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        name=str(data.get("name","")).strip(); kind=str(data.get("method_type","manual"))
        try: order_no=int(data.get("display_order","50") or 50)
        except ValueError: order_no=50
        if not name or kind not in {"manual","card"}: return RedirectResponse("/setup/payment-methods",303)
        now=iso_now()
        with database.connect() as c: mid=int(c.execute("INSERT INTO payment_method_definitions(company_id,name,method_type,display_order,active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",(cid,name,kind,order_no,now,now)).lastrowid)
        audit(database,context,cid,"PAYMENT_METHOD_SAVED","payment_method",mid,after={"name":name,"method_type":kind,"display_order":order_no})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods/toggle")
    async def toggle(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try: mid=int(data.get("id",""))
        except ValueError: return RedirectResponse("/setup/payment-methods",303)
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_method_definitions WHERE company_id=? AND id=?",(cid,mid)).fetchone()
            if not before: return RedirectResponse("/setup/payment-methods",303)
            active=0 if int(before["active"]) else 1
            c.execute("UPDATE payment_method_definitions SET active=?,updated_at=? WHERE company_id=? AND id=?",(active,iso_now(),cid,mid))
        audit(database,context,cid,"PAYMENT_METHOD_TOGGLED","payment_method",mid,dict(before),{"active":active})
        return RedirectResponse("/setup/payment-methods",303)
,'NZD':'NZ
    result=rows(database,"SELECT * FROM payment_rules WHERE company_id=?",(company_id,))
    return result[0] if result else None


def register_payment_method_routes(app):
    database=app.state.database

    @app.get("/setup/payment-methods",response_class=HTMLResponse)
    def page(request:Request,saved:int=0,message:str=''):
        context=context_for(database,request); cid=int(working_company(context))
        methods=rows(database,"SELECT * FROM payment_method_definitions WHERE company_id=? ORDER BY display_order,name",(cid,))
        rule=payment_rule(database,cid)
        trs="".join(f'<tr><td>{int(m["display_order"])}</td><td>{esc(m["name"])}</td><td>{"Card provider" if m["method_type"]=="card" else "Manual payment"}</td><td>{"Active" if int(m["active"]) else "Inactive"}</td><td><form method="post" action="/setup/payment-methods/toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{int(m["id"])}"><button class="secondary">{"Deactivate" if int(m["active"]) else "Reactivate"}</button></form></td></tr>' for m in methods)
        deposit_type=str(rule["deposit_type"] if rule else "fixed")
        deposit_value=float(rule["deposit_value"] if rule else 0)
        threshold='' if rule is None or rule["full_payment_threshold"] is None else f'{float(rule["full_payment_threshold"]):.2f}'
        balance_days=int(rule["balance_due_days"] if rule else 0)
        full_checked='checked' if rule and int(rule["always_require_full_payment"]) else ''
        error=f'<div class="error">{esc(message)}</div>' if message else ''
        body=f'''<h1>Payment Methods</h1><p><a class="button secondary" href="/setup">Setup home</a></p>{error}{"<div class='ok'>Saved.</div>" if saved else ""}
        <div class="card"><h2>Payment / Deposit Rules</h2><p>These rules determine the minimum payment required when a Booking is confirmed.</p>
        <form method="post" action="/setup/payment-rules"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid">
        <div><label>Deposit type</label><select name="deposit_type"><option value="fixed" {"selected" if deposit_type=="fixed" else ""}>Fixed € amount</option><option value="percent" {"selected" if deposit_type=="percent" else ""}>Percentage of Booking total</option></select></div>
        <div><label>Deposit value</label><input type="number" min="0" step="0.01" name="deposit_value" value="{deposit_value:.2f}"></div>
        <div><label>Full-payment threshold (€)</label><input type="number" min="0" step="0.01" name="full_payment_threshold" value="{esc(threshold)}" placeholder="blank = disabled"></div>
        <div><label>Balance due before arrival (days)</label><input type="number" min="0" step="1" name="balance_due_days" value="{balance_days}"></div></div>
        <p><label><input type="checkbox" name="always_require_full_payment" value="1" {full_checked}> Always Require Full Payment</label></p>
        <p class="muted">Precedence: Always Require Full Payment; then full-payment threshold; then balance-due period; otherwise the configured fixed or percentage deposit.</p>
        <p><button>Save Payment Rules</button></p></form></div>
        <div class="card"><h2>Add Payment Method</h2><p>These choices are used when an operator confirms a Booking. Card methods are for a payment provider; DBS does not store card numbers or security codes.</p><form method="post" action="/setup/payment-methods"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Type</label><select name="method_type"><option value="manual">Manual payment</option><option value="card">Card provider</option></select></div><div><label>Display order</label><input type="number" name="display_order" value="50"></div></div><p><button>Save Payment Method</button></p></form></div>
        <div class="card"><table><thead><tr><th>Order</th><th>Name</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{trs}</tbody></table></div>'''
        return layout("Payment Methods",body,context)

    @app.post("/setup/payment-rules")
    async def save_rules(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        kind=str(data.get("deposit_type","fixed")).strip()
        try:
            value=round(float(str(data.get("deposit_value","0")).replace(",",".")),2)
            threshold_raw=str(data.get("full_payment_threshold","")).strip()
            threshold=round(float(threshold_raw.replace(",",".")),2) if threshold_raw else None
            balance_days=int(str(data.get("balance_due_days","0")).strip() or "0")
        except ValueError:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        if kind not in {"fixed","percent"} or value<0 or (kind=="percent" and value>100) or (threshold is not None and threshold<0) or balance_days<0:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        always=1 if data.get("always_require_full_payment")=="1" else 0; now=iso_now()
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_rules WHERE company_id=?",(cid,)).fetchone()
            c.execute("""INSERT INTO payment_rules(company_id,deposit_type,deposit_value,full_payment_threshold,balance_due_days,always_require_full_payment,updated_by_user_id,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(company_id) DO UPDATE SET deposit_type=excluded.deposit_type,deposit_value=excluded.deposit_value,full_payment_threshold=excluded.full_payment_threshold,balance_due_days=excluded.balance_due_days,always_require_full_payment=excluded.always_require_full_payment,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
                (cid,kind,value,threshold,balance_days,always,context["user_id"],now))
        audit(database,context,cid,"PAYMENT_RULES_SAVED","payment_rules",cid,dict(before) if before else None,{"deposit_type":kind,"deposit_value":value,"full_payment_threshold":threshold,"balance_due_days":balance_days,"always_require_full_payment":always})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods")
    async def save(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        name=str(data.get("name","")).strip(); kind=str(data.get("method_type","manual"))
        try: order_no=int(data.get("display_order","50") or 50)
        except ValueError: order_no=50
        if not name or kind not in {"manual","card"}: return RedirectResponse("/setup/payment-methods",303)
        now=iso_now()
        with database.connect() as c: mid=int(c.execute("INSERT INTO payment_method_definitions(company_id,name,method_type,display_order,active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",(cid,name,kind,order_no,now,now)).lastrowid)
        audit(database,context,cid,"PAYMENT_METHOD_SAVED","payment_method",mid,after={"name":name,"method_type":kind,"display_order":order_no})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods/toggle")
    async def toggle(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try: mid=int(data.get("id",""))
        except ValueError: return RedirectResponse("/setup/payment-methods",303)
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_method_definitions WHERE company_id=? AND id=?",(cid,mid)).fetchone()
            if not before: return RedirectResponse("/setup/payment-methods",303)
            active=0 if int(before["active"]) else 1
            c.execute("UPDATE payment_method_definitions SET active=?,updated_at=? WHERE company_id=? AND id=?",(active,iso_now(),cid,mid))
        audit(database,context,cid,"PAYMENT_METHOD_TOGGLED","payment_method",mid,dict(before),{"active":active})
        return RedirectResponse("/setup/payment-methods",303)
,'CHF':'CHF'}
    with database.connect() as c:
        columns={str(r['name']) for r in c.execute('PRAGMA table_info(companies)').fetchall()}
        if 'currency' in columns:
            row=c.execute('SELECT currency FROM companies WHERE id=?',(company_id,)).fetchone(); code=str(row['currency'] or 'EUR').upper() if row else 'EUR'
        elif 'currency_code' in columns:
            row=c.execute('SELECT currency_code FROM companies WHERE id=?',(company_id,)).fetchone(); code=str(row['currency_code'] or 'EUR').upper() if row else 'EUR'
        else: code='EUR'
    return symbols.get(code,code)

def payment_rule(database, company_id:int):
    result=rows(database,"SELECT * FROM payment_rules WHERE company_id=?",(company_id,))
    return result[0] if result else None


def register_payment_method_routes(app):
    database=app.state.database

    @app.get("/setup/payment-methods",response_class=HTMLResponse)
    def page(request:Request,saved:int=0,message:str=''):
        context=context_for(database,request); cid=int(working_company(context))
        methods=rows(database,"SELECT * FROM payment_method_definitions WHERE company_id=? ORDER BY display_order,name",(cid,))
        rule=payment_rule(database,cid)
        trs="".join(f'<tr><td>{int(m["display_order"])}</td><td>{esc(m["name"])}</td><td>{"Card provider" if m["method_type"]=="card" else "Manual payment"}</td><td>{"Active" if int(m["active"]) else "Inactive"}</td><td><form method="post" action="/setup/payment-methods/toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{int(m["id"])}"><button class="secondary">{"Deactivate" if int(m["active"]) else "Reactivate"}</button></form></td></tr>' for m in methods)
        deposit_type=str(rule["deposit_type"] if rule else "fixed")
        deposit_value=float(rule["deposit_value"] if rule else 0)
        threshold='' if rule is None or rule["full_payment_threshold"] is None else f'{float(rule["full_payment_threshold"]):.2f}'
        balance_days=int(rule["balance_due_days"] if rule else 0)
        full_checked='checked' if rule and int(rule["always_require_full_payment"]) else ''
        error=f'<div class="error">{esc(message)}</div>' if message else ''
        body=f'''<h1>Payment Methods</h1><p><a class="button secondary" href="/setup">Setup home</a></p>{error}{"<div class='ok'>Saved.</div>" if saved else ""}
        <div class="card"><h2>Payment / Deposit Rules</h2><p>These rules determine the minimum payment required when a Booking is confirmed.</p>
        <form method="post" action="/setup/payment-rules"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid">
        <div><label>Deposit type</label><select name="deposit_type"><option value="fixed" {"selected" if deposit_type=="fixed" else ""}>Fixed € amount</option><option value="percent" {"selected" if deposit_type=="percent" else ""}>Percentage of Booking total</option></select></div>
        <div><label>Deposit value</label><input type="number" min="0" step="0.01" name="deposit_value" value="{deposit_value:.2f}"></div>
        <div><label>Full-payment threshold (€)</label><input type="number" min="0" step="0.01" name="full_payment_threshold" value="{esc(threshold)}" placeholder="blank = disabled"></div>
        <div><label>Balance due before arrival (days)</label><input type="number" min="0" step="1" name="balance_due_days" value="{balance_days}"></div></div>
        <p><label><input type="checkbox" name="always_require_full_payment" value="1" {full_checked}> Always Require Full Payment</label></p>
        <p class="muted">Precedence: Always Require Full Payment; then full-payment threshold; then balance-due period; otherwise the configured fixed or percentage deposit.</p>
        <p><button>Save Payment Rules</button></p></form></div>
        <div class="card"><h2>Add Payment Method</h2><p>These choices are used when an operator confirms a Booking. Card methods are for a payment provider; DBS does not store card numbers or security codes.</p><form method="post" action="/setup/payment-methods"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Type</label><select name="method_type"><option value="manual">Manual payment</option><option value="card">Card provider</option></select></div><div><label>Display order</label><input type="number" name="display_order" value="50"></div></div><p><button>Save Payment Method</button></p></form></div>
        <div class="card"><table><thead><tr><th>Order</th><th>Name</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{trs}</tbody></table></div>'''
        return layout("Payment Methods",body,context)

    @app.post("/setup/payment-rules")
    async def save_rules(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        kind=str(data.get("deposit_type","fixed")).strip()
        try:
            value=round(float(str(data.get("deposit_value","0")).replace(",",".")),2)
            threshold_raw=str(data.get("full_payment_threshold","")).strip()
            threshold=round(float(threshold_raw.replace(",",".")),2) if threshold_raw else None
            balance_days=int(str(data.get("balance_due_days","0")).strip() or "0")
        except ValueError:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        if kind not in {"fixed","percent"} or value<0 or (kind=="percent" and value>100) or (threshold is not None and threshold<0) or balance_days<0:
            return RedirectResponse("/setup/payment-methods?message=Enter+valid+Payment+Rule+values",303)
        always=1 if data.get("always_require_full_payment")=="1" else 0; now=iso_now()
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_rules WHERE company_id=?",(cid,)).fetchone()
            c.execute("""INSERT INTO payment_rules(company_id,deposit_type,deposit_value,full_payment_threshold,balance_due_days,always_require_full_payment,updated_by_user_id,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(company_id) DO UPDATE SET deposit_type=excluded.deposit_type,deposit_value=excluded.deposit_value,full_payment_threshold=excluded.full_payment_threshold,balance_due_days=excluded.balance_due_days,always_require_full_payment=excluded.always_require_full_payment,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
                (cid,kind,value,threshold,balance_days,always,context["user_id"],now))
        audit(database,context,cid,"PAYMENT_RULES_SAVED","payment_rules",cid,dict(before) if before else None,{"deposit_type":kind,"deposit_value":value,"full_payment_threshold":threshold,"balance_due_days":balance_days,"always_require_full_payment":always})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods")
    async def save(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        name=str(data.get("name","")).strip(); kind=str(data.get("method_type","manual"))
        try: order_no=int(data.get("display_order","50") or 50)
        except ValueError: order_no=50
        if not name or kind not in {"manual","card"}: return RedirectResponse("/setup/payment-methods",303)
        now=iso_now()
        with database.connect() as c: mid=int(c.execute("INSERT INTO payment_method_definitions(company_id,name,method_type,display_order,active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",(cid,name,kind,order_no,now,now)).lastrowid)
        audit(database,context,cid,"PAYMENT_METHOD_SAVED","payment_method",mid,after={"name":name,"method_type":kind,"display_order":order_no})
        return RedirectResponse("/setup/payment-methods?saved=1",303)

    @app.post("/setup/payment-methods/toggle")
    async def toggle(request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try: mid=int(data.get("id",""))
        except ValueError: return RedirectResponse("/setup/payment-methods",303)
        with database.connect() as c:
            before=c.execute("SELECT * FROM payment_method_definitions WHERE company_id=? AND id=?",(cid,mid)).fetchone()
            if not before: return RedirectResponse("/setup/payment-methods",303)
            active=0 if int(before["active"]) else 1
            c.execute("UPDATE payment_method_definitions SET active=?,updated_at=? WHERE company_id=? AND id=?",(active,iso_now(),cid,mid))
        audit(database,context,cid,"PAYMENT_METHOD_TOGGLED","payment_method",mid,dict(before),{"active":active})
        return RedirectResponse("/setup/payment-methods",303)
