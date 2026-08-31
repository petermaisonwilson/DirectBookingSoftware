from __future__ import annotations
import json
from datetime import date,timedelta
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from .app import COOKIE_NAME,esc,form_data,layout
from .setup015_calculator import _addon_rule
from .setup015_core import audit,context_for,require_csrf,rows,working_company
from .webv1_addon_person import addon_person_mode,addon_person_rates

def initialise_addon_when(database):
    with database.connect() as c:c.execute("INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) SELECT company_id,id,'every_day','Every day',1,1 FROM setup_addons ON CONFLICT DO NOTHING");c.execute("INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) SELECT company_id,id,'selected_days','Selected days',0,2 FROM setup_addons ON CONFLICT DO NOTHING")
def when_options(database,cid,aid,*,active_only=True):
    sql='SELECT * FROM setup_addon_when_options WHERE company_id=? AND addon_id=?';params=[cid,aid]
    if active_only:sql+=' AND active=1'
    return rows(database,sql+' ORDER BY sort_order,option_code',tuple(params))
def when_payload(database,cid,addons):return {str(int(a['id'])):[{'code':str(r['option_code']),'label':str(r['label'])} for r in when_options(database,cid,int(a['id']))] for a in addons}
def stay_dates(arrival,departure):
    try:start=date.fromisoformat(arrival);end=date.fromisoformat(departure)
    except ValueError:return []
    return [(start+timedelta(days=i)).isoformat() for i in range((end-start).days)] if end>start else []
def register_addon_when_routes(app):return None
