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
"""
DEFAULTS=(("Card","card",10),("Cash","manual",20),("Cheque","manual",30),("Credit note","manual",40))

def initialise_payment_methods(database):
    with database.connect() as c:
        c.executescript(SCHEMA); now=iso_now()
        for company in c.execute("SELECT id FROM companies").fetchall():
            cid=int(company["id"])
            if int(c.execute("SELECT COUNT(*) AS n FROM payment_method_definitions WHERE company_id=?",(cid,)).fetchone()["n"]): continue
            for name,kind,order_no in DEFAULTS:
                c.execute("INSERT INTO payment_method_definitions(company_id,name,method_type,display_order,active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",(cid,name,kind,order_no,now,now))

def register_payment_method_routes(app):
    database=app.state.database
    @app.get("/setup/payment-methods",response_class=HTMLResponse)
    def page(request:Request,saved:int=0):
        context=context_for(database,request); cid=int(working_company(context))
        methods=rows(database,"SELECT * FROM payment_method_definitions WHERE company_id=? ORDER BY display_order,name",(cid,))
        trs="".join(f'<tr><td>{int(m["display_order"])}</td><td>{esc(m["name"])}</td><td>{"Card provider" if m["method_type"]=="card" else "Manual payment"}</td><td>{"Active" if int(m["active"]) else "Inactive"}</td><td><form method="post" action="/setup/payment-methods/toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{int(m["id"])}"><button class="secondary">{"Deactivate" if int(m["active"]) else "Reactivate"}</button></form></td></tr>' for m in methods)
        body=f'''<h1>Payment Methods</h1><p><a class="button secondary" href="/setup">Setup home</a></p>{"<div class='ok'>Saved.</div>" if saved else ""}<div class="card"><h2>Add Payment Method</h2><p>These choices are used when an operator confirms a Booking. Card methods are for a payment provider; DBS does not store card numbers or security codes.</p><form method="post" action="/setup/payment-methods"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Type</label><select name="method_type"><option value="manual">Manual payment</option><option value="card">Card provider</option></select></div><div><label>Display order</label><input type="number" name="display_order" value="50"></div></div><p><button>Save Payment Method</button></p></form></div><div class="card"><table><thead><tr><th>Order</th><th>Name</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>{trs}</tbody></table></div>'''
        return layout("Payment Methods",body,context)
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
