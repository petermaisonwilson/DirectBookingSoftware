from __future__ import annotations

import json
from urllib.parse import quote_plus

from fastapi.responses import Response

from .app import COOKIE_NAME, esc
from .setup015_core import rows


def install_calendar_edit_semantics(app) -> None:
    """Human-facing calendar corrections layered over the visual selector.

    * EDIT enters edit mode without releasing the existing hold.
    * Existing yellow cells remain clickable while editing.
    * A changed selection is committed with RESERVE CHANGES; CANCEL EDIT leaves it untouched.
    * Per-day Elements treat the second clicked date as the final occupied day.
      Per-night Elements treat it as the departure date.
    """
    database = app.state.database

    @app.middleware('http')
    async def calendar_edit_semantics(request, call_next):
        response = await call_next(request)
        if (
            request.url.path != '/availability/calendar-v2'
            or request.method != 'GET'
            or response.status_code != 200
            or 'text/html' not in response.headers.get('content-type', '')
        ):
            return response

        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')

        context = database.session_context(request.cookies.get(COOKIE_NAME))
        company_id = None
        if context:
            company_id = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
        if not company_id:
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
            return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
        company_id = int(company_id)

        # Give each calendar row its own booking basis so the browser can interpret
        # the second click correctly without hard-coding campsite/activity types.
        for element in rows(database, 'SELECT id,pricing_method FROM setup_elements WHERE company_id=? AND active=1', (company_id,)):
            marker = f'<div class="cal-row element-row" data-element="{int(element["id"])}"'
            replacement = marker + f' data-pricing-method="{esc(element["pricing_method"])}"'
            text = text.replace(marker, replacement, 1)

        edit_hold = request.query_params.get('edit_hold', '')
        editing = edit_hold.isdigit() and int(edit_hold) > 0
        if editing:
            text = text.replace('>UPDATE</button>', '>RESERVE CHANGES</button>')
            text = text.replace("qaction.textContent=editingHold?'UPDATE':'RESERVE'", "qaction.textContent=editingHold?'RESERVE CHANGES':'RESERVE'")
            old = ' — select new dates and/or another '
            if old in text:
                # Keep the existing held item safe until the replacement succeeds.
                a = request.query_params.get('arrival', '')
                d = request.query_params.get('departure', '')
                et = request.query_params.get('element_type', '')
                cancel = '/availability/calendar-v2?element_type=' + quote_plus(et)
                if a:
                    cancel += '&arrival=' + quote_plus(a)
                if d:
                    cancel += '&departure=' + quote_plus(d)
                text = text.replace(
                    '<strong>UPDATE</strong> on the coloured selection.</div>',
                    '<strong>RESERVE CHANGES</strong> on the coloured selection. '
                    f'<a class="button secondary" href="{esc(cancel)}">CANCEL EDIT</a></div>',
                    1,
                )

        text = text.replace(
            'Click a start day and then a later day on the same Element.',
            'Click the first booked day, then choose the end. For night-priced Elements the second click is the departure date; for day-priced Elements it is the last booked day.',
            1,
        )

        script = f'''<style>
        /* Keep the selected cells usable: the action no longer stretches across the whole selection. */
        .selection-action{{justify-self:center;width:max-content;max-width:90%;}}
        .cal-cell.editable-own:hover{{outline:2px solid #9a7a1f;outline-offset:-2px;}}
        </style>
        <script id="calendar-edit-semantics">
        (()=>{{
          const editMode={str(editing).lower()};
          const arrival=document.getElementById('arrival-date');
          const departure=document.getElementById('departure-date');
          const calendar=document.getElementById('calendar-scroll');
          if(!calendar||!arrival||!departure)return;

          let first=''; let chosenElement=0;
          const dayAfter=(iso)=>{{const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+1);return d.toISOString().slice(0,10);}};
          const dayBefore=(iso)=>{{const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()-1);return d.toISOString().slice(0,10);}};
          const isDay=(row)=>String(row?.dataset.pricingMethod||'').toLowerCase()==='per day';
          const dates=()=>[...calendar.querySelectorAll('.cal-date')].map(x=>x.dataset.date);
          const clearBars=()=>document.querySelectorAll('.selection-action').forEach(b=>b.hidden=true);
          const showBar=(row,a,internalEnd)=>{{
            clearBars(); const ds=dates(),s=ds.indexOf(a),e=ds.indexOf(internalEnd);
            if(s<0||e<0||e<=s)return;
            const b=row.querySelector('.selection-action'); if(!b)return;
            b.style.gridColumn=(s+2)+' / '+(e+2); b.style.gridRow='1';
            b.textContent=editMode?'RESERVE CHANGES':'RESERVE'; b.hidden=false;
          }};

          // The old EDIT view drew an action button over the whole yellow hold, which
          // made those dates impossible to click. Editing now starts with the hold
          // untouched and every own yellow date exposed for selection.
          if(editMode) clearBars();

          document.addEventListener('click',(ev)=>{{
            const cell=ev.target.closest('.date-pick');
            if(!cell||!calendar.contains(cell))return;
            ev.preventDefault(); ev.stopImmediatePropagation();
            const row=cell.closest('.element-row'); if(!row)return;
            const eid=Number(row.dataset.element), picked=cell.dataset.date;
            if(!first||chosenElement!==eid){{
              first=picked; chosenElement=eid; arrival.value=picked; departure.value=''; clearBars(); return;
            }}
            const dayMode=isDay(row);
            if((dayMode&&picked<first)||(!dayMode&&picked<=first)){{
              first=picked; arrival.value=picked; departure.value=''; clearBars(); return;
            }}
            // Storage stays end-exclusive. For a day booking, 1 May through 2 May is
            // therefore stored as 1 May -> 3 May; for a night booking, 1 May -> 3 May
            // naturally means two nights and departure on 3 May.
            const internalEnd=dayMode?dayAfter(picked):picked;
            arrival.value=first; departure.value=internalEnd;
            showBar(row,first,internalEnd); first='';
          }},true);

          // Correct the quick popup wording after the original hover code has run.
          document.querySelectorAll('.cal-cell.available').forEach(cell=>cell.addEventListener('mouseenter',()=>{{
            setTimeout(()=>{{
              const row=cell.closest('.element-row');
              const qdates=document.getElementById('quick-dates'), qaction=document.getElementById('quick-action');
              if(qaction&&!qaction.hidden&&editMode)qaction.textContent='RESERVE CHANGES';
              if(!qdates||!row||!arrival.value||!departure.value)return;
              const ms=(new Date(departure.value+'T12:00:00')-new Date(arrival.value+'T12:00:00'))/86400000;
              if(isDay(row))qdates.textContent=arrival.value+' to '+dayBefore(departure.value)+' · '+ms+' day'+(ms===1?'':'s');
              else qdates.textContent=arrival.value+' to '+departure.value+' · '+ms+' night'+(ms===1?'':'s');
            }},0);
          }}));
        }})();
        </script>'''
        text = text.replace('</body>', script + '</body>', 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
'''
