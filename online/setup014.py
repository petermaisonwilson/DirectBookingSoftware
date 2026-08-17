from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data

ELEMENT_PRICING_METHODS = (
    "Per night", "Per day", "Per stay", "Per person", "Per person per night", "Per package"
)
ADDON_PRICING_METHODS = (
    "Fixed once", "Per quantity", "Per night", "Per quantity per night", "Per day", "Per quantity per day"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_elements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, name TEXT NOT NULL,
 element_type TEXT NOT NULL, pricing_method TEXT NOT NULL, base_price REAL NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, UNIQUE(company_id,name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_person_types (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, name TEXT NOT NULL,
 short_name TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
 UNIQUE(company_id,name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_addons (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, name TEXT NOT NULL,
 pricing_method TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 UNIQUE(company_id,name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_years (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, copied_from_year INTEGER,
 PRIMARY KEY(company_id,year)
);
CREATE TABLE IF NOT EXISTS setup_seasons (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, year INTEGER NOT NULL,
 name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 UNIQUE(company_id,year,name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_element_rates (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_id INTEGER NOT NULL, season_id INTEGER NOT NULL,
 rate REAL NOT NULL, PRIMARY KEY(company_id,year,element_id,season_id)
);
CREATE TABLE IF NOT EXISTS setup_occupancy (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_id INTEGER NOT NULL, max_total INTEGER NOT NULL,
 PRIMARY KEY(company_id,year,element_id)
);
CREATE TABLE IF NOT EXISTS setup_person_limits (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_id INTEGER NOT NULL, person_type_id INTEGER NOT NULL,
 max_count INTEGER NOT NULL, PRIMARY KEY(company_id,year,element_id,person_type_id)
);
CREATE TABLE IF NOT EXISTS setup_type_addons (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_type TEXT NOT NULL, addon_id INTEGER NOT NULL,
 allowed INTEGER NOT NULL, min_qty INTEGER, max_qty INTEGER, rate REAL,
 PRIMARY KEY(company_id,year,element_type,addon_id)
);
CREATE TABLE IF NOT EXISTS setup_element_addons (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_id INTEGER NOT NULL, addon_id INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('Y','N')), min_qty INTEGER, max_qty INTEGER, rate REAL,
 PRIMARY KEY(company_id,year,element_id,addon_id)
);
"""


def initialise_setup014(database) -> None:
    with database.connect() as connection:
        connection.executescript(SCHEMA)


def _working_company(context) -> int | None:
    if context["role"] == "supervisor":
        return context["acting_company_id"]
    if context["role"] == "operator":
        return context["company_id"]
    return None


def _context(database, request: Request):
    context = database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None:
        raise HTTPException(status_code=401, detail="Login required")
    if context["role"] not in {"supervisor", "operator"}:
        raise HTTPException(status_code=403, detail="Setup is not available to customers")
    if not _working_company(context):
        raise HTTPException(status_code=403, detail="Select a client in Support Mode first")
    return context


def _csrf(context, data: dict[str, str]) -> None:
    if data.get("csrf") != context["csrf_token"]:
        raise HTTPException(status_code=403, detail="Invalid form token")


def _audit(database, context, company_id: int, action: str, entity_type: str, entity_id: Any, before=None, after=None):
    database.write_audit(
        action=action, entity_type=entity_type, entity_id=entity_id,
        actor_user_id=context["user_id"], actor_role=context["role"], company_id=company_id,
        acting_company_id=context["acting_company_id"], before=before, after=after,
    )


def _layout(page_title: str, body: str, context) -> str:
    import online.app as base
    return base.layout(page_title, body, context)


def _setup_nav() -> str:
    links = [
        ("Setup home", "/setup"), ("Elements", "/setup/elements"), ("Person Types", "/setup/person-types"),
        ("Add-ons", "/setup/addons"), ("Years", "/setup/years"), ("Seasonal pricing", "/setup/pricing"),
        ("Occupancy", "/setup/occupancy"), ("Add-on rules", "/setup/addon-rules"),
    ]
    return '<div class="card" style="display:flex;gap:8px;flex-wrap:wrap">' + ''.join(
        f'<a class="button secondary" href="{href}">{label}</a>' for label, href in links
    ) + '</div>'


def _rows(database, sql: str, params=()):
    with database.connect() as c:
        return c.execute(sql, params).fetchall()


def _one(database, sql: str, params=()):
    with database.connect() as c:
        return c.execute(sql, params).fetchone()


def _years(database, company_id: int) -> list[int]:
    return [int(r["year"]) for r in _rows(database, "SELECT year FROM setup_years WHERE company_id=? ORDER BY year", (company_id,))]


def _year_select(years: list[int], selected: int | None, path: str) -> str:
    if not years:
        return '<div class="error">Create a pricing year first.</div>'
    options = ''.join(f'<option value="{y}" {"selected" if y == selected else ""}>{y}</option>' for y in years)
    return f'<form method="get" action="{path}" class="card"><label>Pricing year</label><select name="year" onchange="this.form.submit()">{options}</select></form>'


def _selected_year(database, company_id: int, raw: str | int | None) -> int | None:
    years = _years(database, company_id)
    try:
        value = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        value = None
    return value if value in years else (years[-1] if years else None)


def _valid_whole(value: str) -> int:
    number = int(value)
    if number < 0:
        raise ValueError("Values cannot be negative")
    return number


def _valid_money(value: str) -> float:
    number = float(value.replace(",", "."))
    if number < 0:
        raise ValueError("Prices cannot be negative")
    return round(number, 2)


def _shift_date(value: str, target_year: int) -> str:
    old = date.fromisoformat(value)
    day = old.day
    while day > 27:
        try:
            return old.replace(year=target_year, day=day).isoformat()
        except ValueError:
            day -= 1
    return old.replace(year=target_year, day=day).isoformat()


def copy_previous_year(database, company_id: int, target_year: int) -> int:
    years = [y for y in _years(database, company_id) if y < target_year]
    if not years:
        raise ValueError("There is no previous year to copy")
    source = max(years)
    if target_year in _years(database, company_id):
        raise ValueError("That year already exists")
    with database.connect() as c:
        c.execute("INSERT INTO setup_years(company_id,year,copied_from_year) VALUES (?,?,?)", (company_id,target_year,source))
        season_map: dict[int,int] = {}
        for row in c.execute("SELECT * FROM setup_seasons WHERE company_id=? AND year=? ORDER BY id", (company_id,source)):
            new_id = c.execute(
                "INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)",
                (company_id,target_year,row["name"],_shift_date(row["start_date"],target_year),_shift_date(row["end_date"],target_year)),
            ).lastrowid
            season_map[int(row["id"])] = int(new_id)
        for row in c.execute("SELECT * FROM setup_element_rates WHERE company_id=? AND year=?", (company_id,source)):
            if int(row["season_id"]) in season_map:
                c.execute("INSERT INTO setup_element_rates VALUES (?,?,?,?,?)", (company_id,target_year,row["element_id"],season_map[int(row["season_id"])],row["rate"]))
        for table, cols in (
            ("setup_occupancy", "element_id,max_total"),
            ("setup_person_limits", "element_id,person_type_id,max_count"),
            ("setup_type_addons", "element_type,addon_id,allowed,min_qty,max_qty,rate"),
            ("setup_element_addons", "element_id,addon_id,state,min_qty,max_qty,rate"),
        ):
            rows = c.execute(f"SELECT {cols} FROM {table} WHERE company_id=? AND year=?", (company_id,source)).fetchall()
            names = cols.split(",")
            placeholders = ",".join("?" for _ in range(2 + len(names)))
            for row in rows:
                c.execute(f"INSERT INTO {table}(company_id,year,{cols}) VALUES ({placeholders})", (company_id,target_year,*[row[n] for n in names]))
    return source


def register_setup014(app) -> None:
    database = app.state.database
    initialise_setup014(database)

    import online.app as base
    if not getattr(base, "_setup014_layout_patched", False):
        original = base.layout
        def enhanced_layout(title: str, body: str, context=None):
            rendered = original(title, body, context)
            if context and context["role"] in {"supervisor","operator"} and (context["company_id"] or context["acting_company_id"]):
                rendered = rendered.replace('<a href="/company/settings">Client settings</a>', '<a href="/company/settings">Client settings</a><a href="/setup">Setup</a>')
            return rendered
        base.layout = enhanced_layout
        base._setup014_layout_patched = True

    @app.get("/setup", response_class=HTMLResponse)
    def setup_home(request: Request):
        context = _context(database, request); cid = _working_company(context)
        company = database.company(cid)
        body = f'<h1>{esc(company["name"])} — Setup</h1>{_setup_nav()}<div class="grid">'
        for title, text, href in (
            ("Elements", "Bookable things that have their own dates and Element Type.", "/setup/elements"),
            ("Person Types", "Adult, Child and any other occupant types you choose.", "/setup/person-types"),
            ("Add-ons", "Extras that inherit the dates of their parent Element.", "/setup/addons"),
            ("Annual setup", "Years, seasons, prices, occupancy and Add-on rules.", "/setup/years"),
        ):
            body += f'<div class="card"><h2>{title}</h2><p>{text}</p><a class="button" href="{href}">Open</a></div>'
        body += '</div>'
        return _layout("Setup", body, context)

    @app.get("/setup/elements", response_class=HTMLResponse)
    def elements(request: Request, edit: int = 0, saved: int = 0):
        context = _context(database, request); cid = _working_company(context)
        rows = _rows(database, "SELECT * FROM setup_elements WHERE company_id=? ORDER BY element_type,name", (cid,))
        current = next((r for r in rows if int(r["id"]) == edit), None)
        options = ''.join(f'<option {"selected" if current and current["pricing_method"]==m else ""}>{m}</option>' for m in ELEMENT_PRICING_METHODS)
        body = f'<h1>Elements</h1>{_setup_nav()}' + ('<div class="ok">Saved and audited.</div>' if saved else '')
        body += f'''<div class="card"><h2>{"Edit" if current else "Add"} Element</h2><form method="post" action="/setup/elements">
        <input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{esc(current["id"] if current else "")}">
        <div class="grid"><div><label>Name</label><input name="name" required value="{esc(current["name"] if current else "")}"></div>
        <div><label>Element Type</label><input name="element_type" required value="{esc(current["element_type"] if current else "")}" placeholder="Camping, Fishing, Gites..."></div>
        <div><label>Pricing method</label><select name="pricing_method">{options}</select></div>
        <div><label>Base price</label><input name="base_price" value="{esc(f'{float(current["base_price"]):.2f}' if current else '0.00')}"></div></div>
        <p><button>Save Element</button></p></form></div>'''
        body += '<div class="card"><table><thead><tr><th>Name</th><th>Element Type</th><th>Pricing</th><th>Base price</th><th></th></tr></thead><tbody>'
        body += ''.join(f'<tr><td>{esc(r["name"])}</td><td>{esc(r["element_type"])}</td><td>{esc(r["pricing_method"])}</td><td>€{float(r["base_price"]):.2f}</td><td><a href="/setup/elements?edit={r["id"]}">Edit</a></td></tr>' for r in rows) or '<tr><td colspan="5">No Elements yet.</td></tr>'
        body += '</tbody></table></div>'
        return _layout("Elements", body, context)

    @app.post("/setup/elements")
    async def elements_save(request: Request):
        context = _context(database, request); cid = _working_company(context); data = await form_data(request); _csrf(context,data)
        name = data.get("name","").strip(); etype = data.get("element_type","").strip(); method = data.get("pricing_method","")
        if not name or not etype or method not in ELEMENT_PRICING_METHODS: raise HTTPException(400,"Complete the Element details")
        price = _valid_money(data.get("base_price","0")); raw_id = data.get("id","")
        with database.connect() as c:
            if raw_id.isdigit():
                old = c.execute("SELECT * FROM setup_elements WHERE id=? AND company_id=?", (int(raw_id),cid)).fetchone()
                if not old: raise HTTPException(404,"Element not found")
                before = dict(old); c.execute("UPDATE setup_elements SET name=?,element_type=?,pricing_method=?,base_price=? WHERE id=? AND company_id=?", (name,etype,method,price,int(raw_id),cid)); eid=int(raw_id)
            else:
                before=None; eid=c.execute("INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (?,?,?,?,?)", (cid,name,etype,method,price)).lastrowid
        _audit(database,context,cid,"ELEMENT_SAVED","element",eid,before,{"name":name,"element_type":etype,"pricing_method":method,"base_price":price})
        return RedirectResponse("/setup/elements?saved=1",303)

    def simple_catalog_page(request: Request, table: str, title: str, action: str, methods=()):
        context = _context(database,request); cid=_working_company(context); rows=_rows(database,f"SELECT * FROM {table} WHERE company_id=? ORDER BY name",(cid,))
        method_html = ''
        if methods:
            method_html = '<div><label>Pricing method</label><select name="pricing_method">' + ''.join(f'<option>{m}</option>' for m in methods) + '</select></div>'
        short_html = '<div><label>Short name</label><input name="short_name" maxlength="8"></div>' if table=="setup_person_types" else ''
        body=f'<h1>{title}</h1>{_setup_nav()}<div class="card"><form method="post" action="{action}"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div>{short_html}{method_html}</div><p><button>Add {title[:-1] if title.endswith("s") else title}</button></p></form></div>'
        body += '<div class="card"><table><thead><tr><th>Name</th>' + ('<th>Short name</th>' if table=="setup_person_types" else '') + ('<th>Pricing method</th>' if methods else '') + '</tr></thead><tbody>'
        for r in rows:
            body += f'<tr><td>{esc(r["name"])}</td>' + (f'<td>{esc(r["short_name"])}</td>' if table=="setup_person_types" else '') + (f'<td>{esc(r["pricing_method"])}</td>' if methods else '') + '</tr>'
        body += '</tbody></table></div>'
        return _layout(title,body,context)

    @app.get("/setup/person-types", response_class=HTMLResponse)
    def person_types(request: Request): return simple_catalog_page(request,"setup_person_types","Person Types","/setup/person-types")

    @app.post("/setup/person-types")
    async def person_types_save(request: Request):
        context=_context(database,request); cid=_working_company(context); data=await form_data(request); _csrf(context,data); name=data.get("name","").strip()
        if not name: raise HTTPException(400,"Name required")
        with database.connect() as c: pid=c.execute("INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,?,?)",(cid,name,data.get("short_name","").strip())).lastrowid
        _audit(database,context,cid,"PERSON_TYPE_ADDED","person_type",pid,None,{"name":name}); return RedirectResponse("/setup/person-types",303)

    @app.get("/setup/addons", response_class=HTMLResponse)
    def addons(request: Request): return simple_catalog_page(request,"setup_addons","Add-ons","/setup/addons",ADDON_PRICING_METHODS)

    @app.post("/setup/addons")
    async def addons_save(request: Request):
        context=_context(database,request); cid=_working_company(context); data=await form_data(request); _csrf(context,data); name=data.get("name","").strip(); method=data.get("pricing_method","")
        if not name or method not in ADDON_PRICING_METHODS: raise HTTPException(400,"Complete the Add-on details")
        with database.connect() as c: aid=c.execute("INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (?,?,?)",(cid,name,method)).lastrowid
        _audit(database,context,cid,"ADDON_ADDED","addon",aid,None,{"name":name,"pricing_method":method}); return RedirectResponse("/setup/addons",303)

    @app.get("/setup/years", response_class=HTMLResponse)
    def years_page(request: Request):
        context=_context(database,request); cid=_working_company(context); years=_years(database,cid)
        body=f'<h1>Pricing years</h1>{_setup_nav()}<div class="card"><h2>Create blank year</h2><form method="post" action="/setup/years/new"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><label>Year</label><input type="number" name="year" value="{date.today().year}" required><p><button>Create blank year</button></p></form></div>'
        body+=f'<div class="card"><h2>Copy previous year</h2><form method="post" action="/setup/years/copy"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><label>New year</label><input type="number" name="year" value="{(max(years)+1) if years else date.today().year+1}" required><p><button>Copy previous year</button></p></form></div>'
        body+='<div class="card"><h2>Existing years</h2><p>'+(', '.join(str(y) for y in years) if years else 'None yet.')+'</p></div>'
        return _layout("Pricing years",body,context)

    @app.post("/setup/years/new")
    async def year_new(request: Request):
        context=_context(database,request); cid=_working_company(context); data=await form_data(request); _csrf(context,data); year=int(data["year"])
        with database.connect() as c:
            c.execute("INSERT INTO setup_years(company_id,year) VALUES (?,?)",(cid,year)); c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)",(cid,year,f"All Year {year}",f"{year}-01-01",f"{year}-12-31"))
        _audit(database,context,cid,"PRICING_YEAR_CREATED","pricing_year",year,None,{"year":year}); return RedirectResponse("/setup/years",303)

    @app.post("/setup/years/copy")
    async def year_copy(request: Request):
        context=_context(database,request); cid=_working_company(context); data=await form_data(request); _csrf(context,data); target=int(data["year"]); source=copy_previous_year(database,cid,target)
        _audit(database,context,cid,"PRICING_YEAR_COPIED","pricing_year",target,{"source":source},{"year":target}); return RedirectResponse("/setup/years",303)

    @app.get("/setup/pricing", response_class=HTMLResponse)
    def pricing_page(request: Request, year: str=""):
        context=_context(database,request); cid=_working_company(context); selected=_selected_year(database,cid,year); years=_years(database,cid)
        body=f'<h1>Seasonal Element pricing</h1>{_setup_nav()}{_year_select(years,selected,"/setup/pricing")}'
        if selected is None: return _layout("Seasonal pricing",body,context)
        seasons=_rows(database,"SELECT * FROM setup_seasons WHERE company_id=? AND year=? ORDER BY start_date",(cid,selected)); elements=_rows(database,"SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name",(cid,))
        body+=f'<div class="card"><h2>Add season</h2><form method="post" action="/setup/seasons"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Start</label><input type="date" name="start_date" required></div><div><label>End</label><input type="date" name="end_date" required></div></div><p><button>Add season</button></p></form></div>'
        body+=f'<form method="post" action="/setup/pricing"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="card" style="overflow:auto"><table><thead><tr><th>Element</th>'+''.join(f'<th>{esc(s["name"])}</th>' for s in seasons)+'</tr></thead><tbody>'
        for e in elements:
            body+=f'<tr><td>{esc(e["name"])}</td>'
            for s in seasons:
                r=_one(database,"SELECT rate FROM setup_element_rates WHERE company_id=? AND year=? AND element_id=? AND season_id=?",(cid,selected,e["id"],s["id"])); value='' if r is None else f'{float(r["rate"]):.2f}'
                body+=f'<td><input style="min-width:90px" name="r_{e["id"]}_{s["id"]}" value="{value}" placeholder="required"></td>'
            body+='</tr>'
        body+='</tbody></table><p><button>Save seasonal prices</button></p></div></form>'
        return _layout("Seasonal pricing",body,context)

    @app.post("/setup/seasons")
    async def season_save(request: Request):
        context=_context(database,request); cid=_working_company(context); data=await form_data(request); _csrf(context,data); year=int(data["year"])
        if date.fromisoformat(data["end_date"]) < date.fromisoformat(data["start_date"]): raise HTTPException(400,"Season end cannot be before start")
        with database.connect() as c: sid=c.execute("INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)",(cid,year,data["name"].strip(),data["start_date"],data["end_date"])).lastrowid
        _audit(database,context,cid,"SEASON_ADDED","season",sid,None,{"year":year,"name":data["name"]}); return RedirectResponse(f"/setup/pricing?year={year}",303)

    @app.post("/setup/pricing")
    async def pricing_save(request: Request):
        context=_context(database,request); cid=_working_company(context); data=await form_data(request); _csrf(context,data); year=int(data["year"])
        seasons=_rows(database,"SELECT id FROM setup_seasons WHERE company_id=? AND year=?",(cid,year)); elements=_rows(database,"SELECT id FROM setup_elements WHERE company_id=? AND active=1",(cid,))
        values=[]
        for e in elements:
            for s in seasons:
                key=f'r_{e["id"]}_{s["id"]}'; raw=data.get(key,"").strip()
                if raw=="": raise HTTPException(400,"Every seasonal price cell must be completed. Zero is valid.")
                values.append((e["id"],s["id"],_valid_money(raw)))
        with database.connect() as c:
            for eid,sid,rate in values: c.execute("INSERT OR REPLACE INTO setup_element_rates VALUES (?,?,?,?,?)",(cid,year,eid,sid,rate))
        _audit(database,context,cid,"SEASONAL_PRICING_SAVED","pricing_year",year,None,{"cells":len(values)}); return RedirectResponse(f"/setup/pricing?year={year}",303)

    @app.get("/setup/occupancy", response_class=HTMLResponse)
    def occupancy_page(request: Request, year: str=""):
        context=_context(database,request); cid=_working_company(context); selected=_selected_year(database,cid,year); years=_years(database,cid); body=f'<h1>Occupancy</h1>{_setup_nav()}{_year_select(years,selected,"/setup/occupancy")}'
        if selected is None: return _layout("Occupancy",body,context)
        elements=_rows(database,"SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name",(cid,)); people=_rows(database,"SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name",(cid,))
        body+=f'<form method="post" action="/setup/occupancy"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="card"><p><strong>0 is valid:</strong> for a Person Type it means that type is not allowed on that Element.</p><table><thead><tr><th>Element</th><th>Total max</th>'+''.join(f'<th>{esc(p["name"])}</th>' for p in people)+'</tr></thead><tbody>'
        for e in elements:
            total=_one(database,"SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?",(cid,selected,e["id"])); body+=f'<tr><td>{esc(e["name"])}</td><td><input name="t_{e["id"]}" value="{esc(total["max_total"] if total else "")}" required></td>'
            for p in people:
                lim=_one(database,"SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?",(cid,selected,e["id"],p["id"])); body+=f'<td><input name="p_{e["id"]}_{p["id"]}" value="{esc(lim["max_count"] if lim else "")}" required></td>'
            body+='</tr>'
        body+='</tbody></table><p><button>Save occupancy</button></p></div></form>'
        return _layout("Occupancy",body,context)

    @app.post("/setup/occupancy")
    async def occupancy_save(request: Request):
        context=_context(database,request); cid=_working_company(context); data=await form_data(request); _csrf(context,data); year=int(data["year"]); elements=_rows(database,"SELECT id FROM setup_elements WHERE company_id=? AND active=1",(cid,)); people=_rows(database,"SELECT id FROM setup_person_types WHERE company_id=? AND active=1",(cid,))
        with database.connect() as c:
            for e in elements:
                total=_valid_whole(data.get(f't_{e["id"]}',"")); c.execute("INSERT OR REPLACE INTO setup_occupancy VALUES (?,?,?,?)",(cid,year,e["id"],total))
                for p in people:
                    limit=_valid_whole(data.get(f'p_{e["id"]}_{p["id"]}',"")); c.execute("INSERT OR REPLACE INTO setup_person_limits VALUES (?,?,?,?,?)",(cid,year,e["id"],p["id"],limit))
        _audit(database,context,cid,"OCCUPANCY_SAVED","pricing_year",year,None,{"elements":len(elements)}); return RedirectResponse(f"/setup/occupancy?year={year}",303)

    @app.get("/setup/addon-rules", response_class=HTMLResponse)
    def addon_rules_page(request: Request, year: str=""):
        context=_context(database,request); cid=_working_company(context); selected=_selected_year(database,cid,year); years=_years(database,cid); body=f'<h1>Add-on rules</h1>{_setup_nav()}{_year_select(years,selected,"/setup/addon-rules")}'
        if selected is None: return _layout("Add-on rules",body,context)
        addons=_rows(database,"SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name",(cid,)); elements=_rows(database,"SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name",(cid,)); types=sorted({str(e["element_type"]) for e in elements},key=str.casefold)
        body+=f'<form method="post" action="/setup/addon-rules"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="card"><h2>Element Type defaults</h2><p>Tick = Y / available. Unticked = N / unavailable.</p><table><thead><tr><th>Element Type</th><th>Add-on</th><th>Y</th><th>Min</th><th>Max</th><th>Price €</th></tr></thead><tbody>'
        for typ in types:
            for a in addons:
                r=_one(database,"SELECT * FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=? AND addon_id=?",(cid,selected,typ,a["id"])); yes=bool(r and r["allowed"]); chk='checked' if yes else ''; mn='' if not r or r["min_qty"] is None else r["min_qty"]; mx='' if not r or r["max_qty"] is None else r["max_qty"]; rate='' if not r or r["rate"] is None else f'{float(r["rate"]):.2f}'
                body+=f'<tr><td>{esc(typ)}</td><td>{esc(a["name"])}</td><td><input style="width:auto" type="checkbox" name="ty_{a["id"]}_{esc(typ)}" {chk}></td><td><input name="tymin_{a["id"]}_{esc(typ)}" value="{mn}"></td><td><input name="tymax_{a["id"]}_{esc(typ)}" value="{mx}"></td><td><input name="tyrate_{a["id"]}_{esc(typ)}" value="{rate}"></td></tr>'
        body+='</tbody></table></div><div class="card"><h2>Individual Element overrides</h2><p><strong>I = Inherit Element Type rule &nbsp; Y = Yes &nbsp; N = No.</strong></p><table><thead><tr><th>Element</th><th>Add-on</th><th>I / Y / N</th><th>Min</th><th>Max</th><th>Price €</th></tr></thead><tbody>'
        for e in elements:
            for a in addons:
                r=_one(database,"SELECT * FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?",(cid,selected,e["id"],a["id"])); state='I' if not r else r["state"]; radios=' '.join(f'<label style="display:inline;font-weight:normal"><input style="width:auto" type="radio" name="ov_{e["id"]}_{a["id"]}" value="{s}" {"checked" if state==s else ""}> {s}</label>' for s in ('I','Y','N')); mn='' if not r or r["min_qty"] is None else r["min_qty"]; mx='' if not r or r["max_qty"] is None else r["max_qty"]; rate='' if not r or r["rate"] is None else f'{float(r["rate"]):.2f}'
                body+=f'<tr><td>{esc(e["name"])}</td><td>{esc(a["name"])}</td><td>{radios}</td><td><input name="ovmin_{e["id"]}_{a["id"]}" value="{mn}"></td><td><input name="ovmax_{e["id"]}_{a["id"]}" value="{mx}"></td><td><input name="ovrate_{e["id"]}_{a["id"]}" value="{rate}"></td></tr>'
        body+='</tbody></table><p><button>Save Add-on rules</button></p></div></form>'
        return _layout("Add-on rules",body,context)

    @app.post("/setup/addon-rules")
    async def addon_rules_save(request: Request):
        context=_context(database,request); cid=_working_company(context); data=await form_data(request); _csrf(context,data); year=int(data["year"]); addons=_rows(database,"SELECT * FROM setup_addons WHERE company_id=? AND active=1",(cid,)); elements=_rows(database,"SELECT * FROM setup_elements WHERE company_id=? AND active=1",(cid,)); types=sorted({str(e["element_type"]) for e in elements},key=str.casefold)
        with database.connect() as c:
            for typ in types:
                for a in addons:
                    key=f'ty_{a["id"]}_{typ}'; allowed=1 if key in data else 0
                    if allowed:
                        mn=_valid_whole(data.get(f'tymin_{a["id"]}_{typ}',"")); mx=_valid_whole(data.get(f'tymax_{a["id"]}_{typ}',"")); rate=_valid_money(data.get(f'tyrate_{a["id"]}_{typ}',""));
                        if mx<mn: raise HTTPException(400,"Add-on maximum cannot be less than minimum")
                    else: mn=mx=rate=None
                    c.execute("INSERT OR REPLACE INTO setup_type_addons VALUES (?,?,?,?,?,?,?,?)",(cid,year,typ,a["id"],allowed,mn,mx,rate))
            for e in elements:
                for a in addons:
                    state=data.get(f'ov_{e["id"]}_{a["id"]}','I')
                    if state=='I': c.execute("DELETE FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?",(cid,year,e["id"],a["id"])); continue
                    if state=='Y':
                        mn=_valid_whole(data.get(f'ovmin_{e["id"]}_{a["id"]}',"")); mx=_valid_whole(data.get(f'ovmax_{e["id"]}_{a["id"]}',"")); rate=_valid_money(data.get(f'ovrate_{e["id"]}_{a["id"]}',""));
                        if mx<mn: raise HTTPException(400,"Override maximum cannot be less than minimum")
                    else: mn=mx=rate=None
                    c.execute("INSERT OR REPLACE INTO setup_element_addons VALUES (?,?,?,?,?,?,?,?)",(cid,year,e["id"],a["id"],state,mn,mx,rate))
        _audit(database,context,cid,"ADDON_RULES_SAVED","pricing_year",year,None,{"element_types":len(types),"elements":len(elements),"addons":len(addons)}); return RedirectResponse(f"/setup/addon-rules?year={year}",303)
