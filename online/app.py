from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import BUILD
from .config import RuntimeConfig, load_runtime_config
from .database import OnlineDatabase
from .security import verify_password

COOKIE_NAME = "directbooking_session"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def role_label(role: str) -> str:
    return {"supervisor": "Supervisor", "operator": "Client / Operator", "customer": "Customer"}.get(role, role)


def can_view_booking_log(context) -> bool:
    return bool(context and context["role"] in {"supervisor", "operator"})


def can_view_global_audit(context) -> bool:
    return bool(context and context["role"] == "supervisor")


async def form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def css() -> str:
    return """
    :root { font-family: Arial, Helvetica, sans-serif; color:#1f2937; background:#f3f5f7; }
    * { box-sizing:border-box; }
    body { margin:0; }
    header { background:#17324d; color:white; padding:14px 22px; display:flex; justify-content:space-between; align-items:center; gap:20px; }
    header a { color:white; }
    nav { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    nav a, .link-button { color:white; text-decoration:none; border:0; background:transparent; cursor:pointer; font:inherit; padding:0; }
    main { max-width:1180px; margin:24px auto; padding:0 18px 40px; }
    .card { background:white; border:1px solid #d6dde5; border-radius:10px; padding:18px; margin-bottom:18px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
    .support { background:#fff2cc; border:1px solid #d6aa00; padding:10px 16px; font-weight:bold; text-align:center; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
    h1,h2,h3 { margin-top:0; }
    label { display:block; font-weight:bold; margin:10px 0 5px; }
    input, select { width:100%; padding:9px; border:1px solid #aeb8c4; border-radius:6px; background:white; }
    button, .button { display:inline-block; background:#225d8f; color:white; border:0; border-radius:6px; padding:9px 13px; cursor:pointer; text-decoration:none; }
    button.secondary, .button.secondary { background:#5b6570; }
    button.warning { background:#9a6700; }
    table { width:100%; border-collapse:collapse; font-size:14px; }
    th, td { text-align:left; border-bottom:1px solid #dde3e9; padding:9px 8px; vertical-align:top; }
    th { background:#f4f6f8; }
    .muted { color:#66717f; }
    .error { background:#fde8e8; border:1px solid #e6aaaa; padding:10px; border-radius:6px; margin-bottom:12px; }
    .ok { background:#e9f7ec; border:1px solid #9acaa2; padding:10px; border-radius:6px; margin-bottom:12px; }
    .login { max-width:520px; margin:50px auto; }
    code { background:#eef1f4; padding:2px 5px; border-radius:4px; }
    """


def layout(title: str, body: str, context=None) -> str:
    support = ""
    nav = ""
    if context:
        support = (
            f'<div class="support">SUPPORT MODE — Viewing {esc(context["acting_company_name"])}. '
            'Changes are recorded as the Supervisor, not as the client.</div>'
            if context["role"] == "supervisor" and context["acting_company_id"]
            else ""
        )
        links = ['<a href="/dashboard">Dashboard</a>']
        if context["role"] in {"operator", "supervisor"} and (context["company_id"] or context["acting_company_id"]):
            links.append('<a href="/operations">Operations</a>')
            links.append('<a href="/company/settings">Client settings</a>')
            links.append('<a href="/setup">Setup</a>')
        if context["role"] == "supervisor":
            links.append('<a href="/audit">Global Audit</a>')
        links.append(
            '<form method="post" action="/logout" style="display:inline"><button class="link-button" type="submit">Log out</button></form>'
        )
        nav = "<nav>" + "".join(links) + "</nav>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — Direct Booking</title><style>{css()}</style></head>
<body><header><div><strong>Direct Booking Software</strong> <span class="muted" style="color:#c9d5df">Online Build {BUILD}</span></div>{nav}</header>
{support}<main>{body}</main></body></html>"""


def _sqlite_path(settings: RuntimeConfig, db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        raise RuntimeError(
            "The legacy DBS runtime is still being converted to PostgreSQL. "
            "Use SQLite for the application until the portability milestone is complete."
        )
    return Path(settings.database_url[len(prefix):])


def create_app(
    db_path: str | Path | None = None,
    *,
    seed_demo: bool | None = None,
    runtime_config: RuntimeConfig | None = None,
) -> FastAPI:
    settings = runtime_config or load_runtime_config()
    actual_seed_demo = settings.seed_demo if seed_demo is None else bool(seed_demo)
    if settings.production and actual_seed_demo:
        raise RuntimeError("Demo data is forbidden in production")

    database = OnlineDatabase(_sqlite_path(settings, db_path))
    database.initialise(seed_demo=actual_seed_demo)
    app = FastAPI(title=f"Direct Booking Software Online Build {BUILD}")
    app.state.database = database
    app.state.runtime_config = settings

    def context_from(request: Request):
        return database.session_context(request.cookies.get(COOKIE_NAME))

    def require_login(request: Request):
        context = context_from(request)
        if context is None:
            raise HTTPException(status_code=401, detail="Login required")
        return context

    def require_csrf(context, data: dict[str, str]) -> None:
        if not data.get("csrf") or data["csrf"] != context["csrf_token"]:
            raise HTTPException(status_code=403, detail="Invalid form token")

    def working_company_id(context) -> int | None:
        if context["role"] == "supervisor":
            return context["acting_company_id"]
        if context["role"] == "operator":
            return context["company_id"]
        return None

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "build": BUILD,
            "mode": "online-foundation",
            "environment": settings.environment,
        }

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        if context_from(request):
            return RedirectResponse("/dashboard", status_code=303)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, error: str = ""):
        error_html = f'<div class="error">{esc(error)}</div>' if error else ""
        demo_html = ""
        if actual_seed_demo:
            demo_html = """
        <hr><p><strong>Local Build 013 test accounts</strong></p>
        <p><code>supervisor@directbooking.test</code> / <code>Supervisor013!</code><br>
        <code>operator@forestview.test</code> / <code>Operator013!</code><br>
        <code>customer@forestview.test</code> / <code>Customer013!</code></p>
        <p class="muted">These accounts are for local development only and are never enabled in production.</p>"""
        environment_text = (
            "Direct Booking Software"
            if settings.production
            else f"Direct Booking Software — {esc(settings.environment.title())}"
        )
        body = f"""
        <div class="login card"><h1>Online Build {BUILD}</h1>
        <p>{environment_text}</p>{error_html}
        <form method="post" action="/login">
          <label>Email</label><input name="email" type="email" required autofocus>
          <label>Password</label><input name="password" type="password" required>
          <p><button type="submit">Log in</button></p>
        </form>{demo_html}</div>"""
        return layout("Login", body)

    @app.post("/login")
    async def login_submit(request: Request):
        data = await form_data(request)
        email = data.get("email", "").strip()
        user = database.user_by_email(email)
        if user is None or not verify_password(data.get("password", ""), user["password_hash"]):
            database.write_audit(action="LOGIN_FAILED", entity_type="authentication", entity_id=email or None, actor_user_id=None, actor_role=None, company_id=None, acting_company_id=None, after={"email": email})
            return RedirectResponse("/login?error=Email+or+password+not+recognised", status_code=303)
        session = database.create_session(int(user["id"]))
        database.write_audit(action="LOGIN_SUCCESS", entity_type="user", entity_id=user["id"], actor_user_id=user["id"], actor_role=user["role"], company_id=user["company_id"], acting_company_id=None, after={"email": user["email"], "role": user["role"]})
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            session["token"],
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            max_age=12 * 3600,
        )
        return response

    @app.post("/logout")
    def logout(request: Request):
        token = request.cookies.get(COOKIE_NAME); context = context_from(request)
        if token and context:
            database.write_audit(action="LOGOUT", entity_type="user", entity_id=context["user_id"], actor_user_id=context["user_id"], actor_role=context["role"], company_id=context["company_id"], acting_company_id=context["acting_company_id"])
            database.delete_session(token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, secure=settings.secure_cookies, samesite="lax")
        return response

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request):
        context = require_login(request)
        if context["role"] == "supervisor":
            cards=[]
            for company in database.companies():
                cards.append(f'<div class="card"><h3>{esc(company["name"])}</h3><p>{esc(company["contact_email"])}</p><form method="post" action="/support/start/{company["id"]}"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><button type="submit">View as Client</button></form></div>')
            stop=""
            if context["acting_company_id"]:
                stop=f'<form method="post" action="/support/stop"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><button class="warning" type="submit">Leave Support Mode</button></form>'
            body=f"""<h1>Supervisor Dashboard</h1><div class="card"><p>Logged in as <strong>{esc(context['first_name'])} {esc(context['last_name'])}</strong>.</p><p>Choose a client to enter Support Mode. Any changes remain attributed to your Supervisor account.</p>{stop}</div><div class="grid">{''.join(cards)}</div>"""
        elif context["role"] == "operator":
            body=f"""<h1>{esc(context['company_name'])}</h1><div class="card"><h2>Client / Operator Dashboard</h2><p>Welcome {esc(context['first_name'])}. You can only see this client's information.</p><p><a class="button" href="/operations">Open Operations</a> <a class="button secondary" href="/company/settings">Client settings</a></p><p class="muted">Client Register and Enquiry Search are available in Operations. Offers, Bookings and the availability calendar follow in later milestones.</p></div>"""
        else:
            body=f"""<h1>Customer Area</h1><div class="card"><p>Welcome {esc(context['first_name'])}.</p><p>This confirms the separate Customer permission level. Customer booking history and availability search will be added later.</p><p><strong>Customers cannot see Booking Logs or the Global Audit.</strong></p></div>"""
        return layout("Dashboard", body, context)

    @app.post("/support/start/{company_id}")
    async def support_start(company_id: int, request: Request):
        context=require_login(request); data=await form_data(request); require_csrf(context,data)
        if context["role"]!="supervisor": raise HTTPException(status_code=403,detail="Supervisor only")
        company=database.company(company_id)
        if company is None: raise HTTPException(status_code=404,detail="Client not found")
        token=request.cookies.get(COOKIE_NAME); database.set_acting_company(token,company_id)
        database.write_audit(action="SUPPORT_MODE_STARTED",entity_type="company",entity_id=company_id,actor_user_id=context["user_id"],actor_role=context["role"],company_id=company_id,acting_company_id=company_id,after={"company":company["name"]})
        return RedirectResponse("/company/settings",status_code=303)

    @app.post("/support/stop")
    async def support_stop(request: Request):
        context=require_login(request); data=await form_data(request); require_csrf(context,data)
        if context["role"]!="supervisor": raise HTTPException(status_code=403,detail="Supervisor only")
        token=request.cookies.get(COOKIE_NAME); previous=context["acting_company_id"]
        if previous: database.write_audit(action="SUPPORT_MODE_STOPPED",entity_type="company",entity_id=previous,actor_user_id=context["user_id"],actor_role=context["role"],company_id=previous,acting_company_id=previous)
        database.set_acting_company(token,None); return RedirectResponse("/dashboard",status_code=303)

    @app.get("/company/settings", response_class=HTMLResponse)
    def company_settings(request: Request, saved: int = 0):
        context=require_login(request); company_id=working_company_id(context)
        if not company_id: raise HTTPException(status_code=403,detail="Select a client in Support Mode first")
        company=database.company(company_id)
        if company is None: raise HTTPException(status_code=404,detail="Client not found")
        saved_html='<div class="ok">Saved. The change has been written to the permanent audit trail.</div>' if saved else ""
        body=f"""<h1>{esc(company['name'])}</h1>{saved_html}<div class="card"><h2>Client settings</h2><p>This deliberately simple form gives Build 013 something safe to change and audit.</p><form method="post" action="/company/settings"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><label>Contact email</label><input name="contact_email" type="email" value="{esc(company['contact_email'])}"><label>Telephone</label><input name="phone" value="{esc(company['phone'])}"><p><button type="submit">Save client settings</button></p></form></div><div class="card"><h3>Permission reminder</h3><p>Booking Log will later be visible here to Supervisor and Client/Operator users only. Customers will never see it.</p></div>"""
        return layout("Client settings",body,context)

    @app.post("/company/settings")
    async def company_settings_save(request: Request):
        context=require_login(request); data=await form_data(request); require_csrf(context,data); company_id=working_company_id(context)
        if context["role"] not in {"supervisor","operator"} or not company_id: raise HTTPException(status_code=403,detail="Not permitted")
        before,after=database.update_company_contact(company_id,contact_email=data.get("contact_email",""),phone=data.get("phone",""))
        database.write_audit(action="COMPANY_CONTACT_UPDATED",entity_type="company",entity_id=company_id,actor_user_id=context["user_id"],actor_role=context["role"],company_id=company_id,acting_company_id=context["acting_company_id"],before=before,after=after)
        return RedirectResponse("/company/settings?saved=1",status_code=303)

    @app.get("/audit", response_class=HTMLResponse)
    def global_audit(request: Request, company_id: str = "", date_from: str = "", date_to: str = ""):
        context=require_login(request)
        if not can_view_global_audit(context): raise HTTPException(status_code=403,detail="Supervisor only")
        selected_company=int(company_id) if company_id.isdigit() else None; audit_rows=database.audit_rows(company_id=selected_company,date_from=date_from,date_to=date_to); companies=database.companies()
        options=['<option value="">All clients</option>']+[f'<option value="{c["id"]}" {"selected" if selected_company==c["id"] else ""}>{esc(c["name"])}</option>' for c in companies]; table_rows=[]
        for row in audit_rows:
            actor="System / unknown" if not row["actor_user_id"] else f'{row["first_name"]} {row["last_name"]} ({role_label(row["actor_role"])})'; client=row["company_name"] or "—"; acting=row["acting_company_name"] or "—"
            table_rows.append(f'<tr><td>{esc(row["created_at"])}</td><td>{esc(client)}</td><td>{esc(actor)}</td><td>{esc(acting)}</td><td>{esc(row["action"])}</td><td>{esc(row["entity_type"])} {esc(row["entity_id"] or "")}</td><td><small>{esc(row["before_json"] or "")}</small></td><td><small>{esc(row["after_json"] or "")}</small></td></tr>')
        body=f"""<h1>Global Audit</h1><div class="card"><p><strong>Supervisor only.</strong> Search by client and date.</p><form method="get" action="/audit"><div class="grid"><div><label>Client</label><select name="company_id">{''.join(options)}</select></div><div><label>From date</label><input type="date" name="date_from" value="{esc(date_from)}"></div><div><label>To date</label><input type="date" name="date_to" value="{esc(date_to)}"></div></div><p><button type="submit">Search Audit</button> <a class="button secondary" href="/audit">Clear</a></p></form></div><div class="card" style="overflow:auto"><table><thead><tr><th>When</th><th>Client</th><th>User</th><th>Acting in</th><th>Action</th><th>Item</th><th>Before</th><th>After</th></tr></thead><tbody>{''.join(table_rows) if table_rows else '<tr><td colspan="8">No audit entries match.</td></tr></tbody></table></div>"""
        return layout("Global Audit",body,context)

    return app


app = create_app()
