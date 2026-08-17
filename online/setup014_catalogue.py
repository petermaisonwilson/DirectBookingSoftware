from __future__ import annotations

from datetime import date

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .setup014_core import ADDON_PRICING_METHODS, ELEMENT_PRICING_METHODS, audit, context_for, copy_previous_year, require_csrf, rows, valid_money, working_company, years


def setup_nav() -> str:
    links = [("Setup home", "/setup"), ("Elements", "/setup/elements"), ("Person Types", "/setup/person-types"), ("Add-ons", "/setup/addons"), ("Years", "/setup/years"), ("Seasonal pricing", "/setup/pricing"), ("Occupancy", "/setup/occupancy"), ("Add-on rules", "/setup/addon-rules")]
    return '<div class="card" style="display:flex;gap:8px;flex-wrap:wrap">' + ''.join(f'<a class="button secondary" href="{href}">{label}</a>' for label, href in links) + '</div>'


def register_catalogue_routes(app) -> None:
    database = app.state.database

    @app.get("/setup", response_class=HTMLResponse)
    def setup_home(request: Request):
        context = context_for(database, request); cid = working_company(context); company = database.company(cid)
        body = f'<h1>{esc(company["name"])} — Setup</h1>{setup_nav()}<div class="grid">'
        for title, text, href in (("Elements", "Bookable things that have their own dates and Element Type.", "/setup/elements"), ("Person Types", "Adult, Child and any other occupant types you choose.", "/setup/person-types"), ("Add-ons", "Extras that inherit the dates of their parent Element.", "/setup/addons"), ("Annual setup", "Years, seasons, prices, occupancy and Add-on rules.", "/setup/years")):
            body += f'<div class="card"><h2>{title}</h2><p>{text}</p><a class="button" href="{href}">Open</a></div>'
        return layout("Setup", body + '</div>', context)

    @app.get("/setup/elements", response_class=HTMLResponse)
    def elements(request: Request, edit: int = 0, saved: int = 0):
        context=context_for(database,request); cid=working_company(context); element_rows=rows(database,"SELECT * FROM setup_elements WHERE company_id=? ORDER BY element_type,name",(cid,)); current=next((r for r in element_rows if int(r["id"])==edit),None)
        options=''.join(f'<option {"selected" if current and current["pricing_method"]==m else ""}>{m}</option>' for m in ELEMENT_PRICING_METHODS)
        body=f'<h1>Elements</h1>{setup_nav()}' + ('<div class="ok">Saved and audited.</div>' if saved else '')
        body+=f'''<div class="card"><h2>{"Edit" if current else "Add"} Element</h2><form method="post" action="/setup/elements"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{esc(current["id"] if current else "")}"><div class="grid"><div><label>Name</label><input name="name" required value="{esc(current["name"] if current else "")}"></div><div><label>Element Type</label><input name="element_type" required value="{esc(current["element_type"] if current else "")}" placeholder="Camping, Fishing, Gites..."></div><div><label>Pricing method</label><select name="pricing_method">{options}</select></div><div><label>Base price</label><input name="base_price" value="{esc(f'{float(current["base_price"]):.2f}' if current else '0.00')}"></div></div><p><button>Save Element</button></p></form></div>'''
        body+='<div class="card"><table><thead><tr><th>Name</th><th>Element Type</th><th>Pricing</th><th>Base price</th><th></th></tr></thead><tbody>'
        body+=''.join(f'<tr><td>{esc(r["name"])}</td><td>{esc(r["element_type"])}</td><td>{esc(r["pricing_method"])}</td><td>€{float(r["base_price"]):.2f}</td><td><a href="/setup/elements?edit={r["id"]}">Edit</a></td></tr>' for r in element_rows) or '<tr><td colspan="5">No Elements yet.</td></tr>'
        return layout("Elements",body+'</tbody></table></div>',context)

    @app.post("/setup/elements")
    async def elements_save(request: Request):
        context=context_for(database,request); cid=working_company(context); data=await form_data(request); require_csrf(context,data); name=data.get("name","").strip(); etype=data.get("element_type","").strip(); method=data.get("pricing_method","")
        if not name or not etype or method not in ELEMENT_PRICING_METHODS: raise HTTPException(400,"Complete the Element details")
        try: price=valid_money(data.get("base_price","0"))
        except (TypeError,ValueError): raise HTTPException(400,"Base price must be zero or a valid positive price")
        raw_id=data.get("id","")
        with database.connect() as c:
            if raw_id.isdigit():
                old=c.execute("SELECT * FROM setup_elements WHERE id=? AND company_id=?",(int(raw_id),cid)).fetchone()
                if not old: raise HTTPException(404,"Element not found")
                before=dict(old); c.execute("UPDATE setup_elements SET name=?,element_type=?,pricing_method=?,base_price=? WHERE id=? AND company_id=?",(name,etype,method,price,int(raw_id),cid)); eid=int(raw_id)
            else: before=None; eid=c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (?,?,?,?,?)",(cid,name,etype,method,price)).lastrowid
        audit(database,context,cid,"ELEMENT_SAVED","element",eid,before,{"name":name,"element_type":etype,"pricing_method":method,"base_price":price}); return RedirectResponse("/setup/elements?saved=1",303)

    def simple_catalog_page(request: Request, table: str, title: str, action: str, methods=()):
        context=context_for(database,request); cid=working_company(context); catalog_rows=rows(database,f"SELECT * FROM {table} WHERE company_id=? ORDER BY name",(cid,)); method_html=''
        if methods: method_html='<div><label>Pricing method</label><select name="pricing_method">'+''.join(f'<option>{m}</option>' for m in methods)+'</select></div>'
        short_html='<div><label>Short name</label><input name="short_name" maxlength="8"></div>' if table=="setup_person_types" else ''
        body=f'<h1>{title}</h1>{setup_nav()}<div class="card"><form method="post" action="{action}"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div>{short_html}{method_html}</div><p><button>Add {title[:-1] if title.endswith("s") else title}</button></p></form></div><div class="card"><table><thead><tr><th>Name</th>'+('<th>Short name</th>' if table=="setup_person_types" else '')+('<th>Pricing method</th>' if methods else '')+'</tr></thead><tbody>'
        for r in catalog_rows: body+=f'<tr><td>{esc(r["name"])}</td>'+(f'<td>{esc(r["short_name"])}</td>' if table=="setup_person_types" else '')+(f'<td>{esc(r["pricing_method"])}</td>' if methods else '')+'</tr>'
        return layout(title,body+'</tbody></table></div>',context)

    @app.get("/setup/person-types", response_class=HTMLResponse)
    def person_types(request: Request): return simple_catalog_page(request,"setup_person_types","Person Types","/setup/person-types")

    @app.post("/setup/person-types")
    async def person_types_save(request: Request):
        context=context_for(database,request); cid=working_company(context); data=await form_data(request); require_csrf(context,data); name=data.get("name","").strip()
        if not name: raise HTTPException(400,"Name required")
        with database.connect() as c: pid=c.execute("INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,?,?)",(cid,name,data.get("short_name","").strip())).lastrowid
        audit(database,context,cid,"PERSON_TYPE_ADDED","person_type",pid,None,{"name":name}); return RedirectResponse("/setup/person-types",303)

    @app.get("/setup/addons", response_class=HTMLResponse)
    def addons(request: Request): return simple_catalog_page(request,"setup_addons","Add-ons","/setup/addons",ADDON_PRICING_METHODS)

    @app.post("/setup/addons")
    async def addons_save(request: Request):
        context=context_for(database,request); cid=working_company(context); data=await form_data(request); require_csrf(context,data); name=data.get("name","").strip(); method=data.get("pricing_method","")
        if not name or method not in ADDON_PRICING_METHODS: raise HTTPException(400,"Complete the Add-on details")
        with database.connect() as c: aid=c.execute("INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (?,?,?)",(cid,name,method)).lastrowid
        audit(database,context,cid,"ADDON_ADDED","addon",aid,None,{"name":name,"pricing_method":method}); return RedirectResponse("/setup/addons",303)

    @app.get("/setup/years", response_class=HTMLResponse)
    def years_page(request: Request):
        context=context_for(database,request); cid=working_company(context); available=years(database,cid); body=f'<h1>Pricing years</h1>{setup_nav()}<div class="card"><h2>Create blank year</h2><form method="post" action="/setup/years/new"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><label>Year</label><input type="number" name="year" value="{date.today().year}" required><p><button>Create blank year</button></p></form></div><div class="card"><h2>Copy previous year</h2><form method="post" action="/setup/years/copy"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><label>New year</label><input type="number" name="year" value="{(max(available)+1) if available else date.today().year+1}" required><p><button>Copy previous year</button></p></form></div><div class="card"><h2>Existing years</h2><p>'+(', '.join(str(y) for y in available) if available else 'None yet.')+'</p></div>'; return layout("Pricing years",body,context)

    @app.post("/setup/years/new")
    async def year_new(request: Request):
        context=context_for(database,request); cid=working_company(context); data=await form_data(request); require_csrf(context,data); year=int(data["year"])
        with database.connect() as c: c.execute("INSERT INTO setup_years(company_id,year) VALUES (?,?)",(cid,year)); c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)",(cid,year,f"All Year {year}",f"{year}-01-01",f"{year}-12-31"))
        audit(database,context,cid,"PRICING_YEAR_CREATED","pricing_year",year,None,{"year":year}); return RedirectResponse("/setup/years",303)

    @app.post("/setup/years/copy")
    async def year_copy(request: Request):
        context=context_for(database,request); cid=working_company(context); data=await form_data(request); require_csrf(context,data); target=int(data["year"]); source=copy_previous_year(database,cid,target); audit(database,context,cid,"PRICING_YEAR_COPIED","pricing_year",target,{"source":source},{"year":target}); return RedirectResponse("/setup/years",303)
