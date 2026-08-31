from __future__ import annotations
import json
from datetime import date,datetime
from urllib.parse import quote_plus,unquote_plus
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from starlette.responses import Response
from .app import COOKIE_NAME,esc,form_data,layout
from .setup015_catalogue import setup_nav
from .setup015_core import audit,context_for,require_csrf,working_company,years
from .setup015_readiness import element_missing_items,incomplete_elements
CURRENCIES={'EUR':('€','Euro'),'GBP':('£','Pound sterling'),'USD':('$','US dollar'),'CHF':('CHF ','Swiss franc'),'AUD':('A$','Australian dollar'),'CAD':('C$','Canadian dollar'),'NZD':('NZ$','New Zealand dollar')}
def initialise_pricing_usability(database):
    with database.connect() as c:c.execute("UPDATE companies SET base_currency='EUR' WHERE base_currency IS NULL OR TRIM(base_currency)='' ")
def company_currency(database,company_id):
    with database.connect() as c:row=c.execute('SELECT base_currency FROM companies WHERE id=?',(company_id,)).fetchone()
    code=str(row['base_currency'] if row and row['base_currency'] else 'EUR').upper();return code if code in CURRENCIES else 'EUR'
def currency_symbol(database,company_id):return CURRENCIES[company_currency(database,company_id)][0]
def _append_duration_to_result(database,company_id,result):return None
def install_pricing_calculation_transparency():return None
def register_pricing_usability_routes(app):return None
