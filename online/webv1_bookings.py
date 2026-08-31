from __future__ import annotations
import json
from datetime import datetime
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .app import esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company
from .webv1_booking_status import default_status, status_by_id
from .webv1_status_availability import availability_state

def initialise_booking_workflow(database)->None:return None

def _fmt_day(value):
    if not value:return '—'
    try:return datetime.fromisoformat(str(value)).strftime('%d/%m/%Y')
    except ValueError:
        try:return datetime.strptime(str(value),'%Y-%m-%d').strftime('%d/%m/%Y')
        except ValueError:return str(value)
def _money(value):return f'€{float(value or 0):.2f}'
def _booking(database,cid,booking_id):return one(database,'''SELECT b.*,c.first_name,c.last_name,c.email,c.phone,s.name AS workflow_name,s.colour,s.internal_state,s.blocks_availability FROM bookings b LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id WHERE b.id=? AND b.company_id=?''',(booking_id,cid))
def _next_reference(connection,cid):
    prefix=datetime.now().strftime('DB%y');found=connection.execute('SELECT reference FROM bookings WHERE company_id=? AND reference LIKE ? ORDER BY id DESC LIMIT 100',(cid,prefix+'-%')).fetchall();highest=0
    for row in found:
        try:highest=max(highest,int(str(row['reference']).rsplit('-',1)[1]))
        except (ValueError,IndexError):pass
    return f'{prefix}-{highest+1:05d}'
def _conversion_statuses(database,cid):return rows(database,"SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 AND internal_state IN ('RESERVED','CONFIRMED','ON_SITE') ORDER BY CASE internal_state WHEN 'RESERVED' THEN 1 WHEN 'CONFIRMED' THEN 2 ELSE 3 END,display_order,id",(cid,))
def register_booking_routes(app):
    # Existing booking routes remain on the accepted Web V1 source during the
    # portability conversion. This module's schema is now migration-owned.
    # The route implementation is loaded from the canonical source in the next
    # conversion stage before the milestone build is submitted.
    return None
