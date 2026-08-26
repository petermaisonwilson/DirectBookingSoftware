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
    * Night bookings show the departure morning as the top half of the departure-day cell.
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

        token = request.cookies.get(COOKIE_NAME, '')
        context = database.session_context(token)
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

        # Existing night-priced holds in this booking need a visible departure morning.
        night_holds = [
            {
                'element_id': int(r['element_id']),
                'element_name': str(r['element_name']),
                'departure_date': str(r['departure_date']),
            }
            for r in rows(
                database,
                '''SELECT h.element_id,h.departure_date,e.name AS element_name
                   FROM element_holds h
                   JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
                   WHERE h.company_id=? AND h.session_token=?
                     AND h.expires_at>datetime('now')
                     AND lower(e.pricing_method)='per night'
                   ORDER BY h.created_at,h.id''',
                (company_id, token),
            )
        ]

        edit_hold = request.query_params.get('edit_hold', '')
        editing = edit_hold.isdigit() and int(edit_hold) > 0
        if editing:
            text = text.replace('>UPDATE</button>', '>RESERVE CHANGES</button>')
            text = text.replace("qaction.textContent=editingHold?'UPDATE':'RESERVE'", "qaction.textContent=editingHold?'RESERVE CHANGES':'RESERVE'")
            old = ' — select new dates and/or another '
            if old in text:
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
            'Click the first booked day, then choose the end. For night-priced Elements the second click is the departure date; for day-priced Elements it is the last booked day. Night bookings show the departure morning as half a square.',
            1,
        )

        script = f"""<style>
        .selection-action{{justify-self:center;width:max-content;max-width:90%;}}
        .cal-cell.editable-own:hover{{outline:2px solid #9a7a1f;outline-offset:-2px;}}
        .night-departure{{align-self:start;height:50%;z-index:5;pointer-events:none;background:#ffe39a;border-left:1px solid rgba(255,255,255,.65);border-right:1px solid rgba(255,255,255,.65);box-sizing:border-box;}}
        .progress-row .night-departure{{z-index:5;}}
        </style>
        <script id="calendar-edit-semantics">
        (()=>{{
          const editMode={str(editing).lower()};
          const nightHolds={json.dumps(night_holds)};
          const arrival=document.getElementById('arrival-date');
          const departure=document.getElementById('departure-date');
          const calendar=document.getElementById('calendar-scroll');
          if(!calendar||!arrival||!departure)return;

          let first=''; let chosenElement=0;
          const dayAfter=(iso)=>{{const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+1);return d.toISOString().slice(0,10);}};
          const dayBefore=(iso)=>{{const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()-1);return d.toISOString().slice(0,10);}};
          const isDay=(row)=>String(row?.dataset.pricingMethod||'').toLowerCase()==='per day';
          const dates=()=>[...calendar.querySelectorAll('.cal-date')].map(x=>x.dataset.date);
          const clearTempDeparture=()=>document.querySelectorAll('.night-departure.temp').forEach(x=>x.remove());
          const clearBars=()=>{{document.querySelectorAll('.selection-action').forEach(b=>b.hidden=true);clearTempDeparture();}};
          const addDeparture=(row,iso,temp=false)=>{{
            if(!row||!iso)return;
            const ds=dates(),idx=ds.indexOf(iso); if(idx<0)return;
            if(row.querySelector('.night-departure[data-date="'+iso+'"]'))return;
            const m=document.createElement('span');
            m.className='night-departure'+(temp?' temp':'');
            m.dataset.date=iso; m.title='Departure morning';
            m.style.gridColumn=String(idx+2); m.style.gridRow='1';
            row.appendChild(m);
          }};
          const showBar=(row,a,internalEnd)=>{{
            clearBars(); const ds=dates(),s=ds.indexOf(a),e=ds.indexOf(internalEnd);
            if(s<0||e<0||e<=s)return;
            const b=row.querySelector('.selection-action'); if(!b)return;
            b.style.gridColumn=(s+2)+' / '+(e+2); b.style.gridRow='1';
            b.textContent=editMode?'RESERVE CHANGES':'RESERVE'; b.hidden=false;
            if(!isDay(row))addDeparture(row,internalEnd,true);
          }};

          // Existing night holds show full occupied nights plus the top half of the
          // departure date, making "departs on the 3rd" visually distinct from a
          // day booking that simply ends on the 2nd.
          nightHolds.forEach(h=>{{
            const lower=document.querySelector('.element-row[data-element="'+h.element_id+'"]');
            addDeparture(lower,h.departure_date,false);
            const progress=[...document.querySelectorAll('.progress-row')].find(r=>r.querySelector('.progress-name strong')?.textContent.trim()===h.element_name);
            if(progress){{
              const headerDates=[...document.querySelectorAll('#progress-scroll .cal-date')].map(x=>x.dataset.date);
              const idx=headerDates.indexOf(h.departure_date);
              if(idx>=0&&!progress.querySelector('.night-departure[data-date="'+h.departure_date+'"]')){{
                const m=document.createElement('span');m.className='night-departure';m.dataset.date=h.departure_date;m.title='Departure morning';m.style.gridColumn=String(idx+2);m.style.gridRow='1';progress.appendChild(m);
              }}
            }}
          }});

          if(editMode) document.querySelectorAll('.selection-action').forEach(b=>b.hidden=true);

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
            const internalEnd=dayMode?dayAfter(picked):picked;
            arrival.value=first; departure.value=internalEnd;
            showBar(row,first,internalEnd); first='';
          }},true);

          document.querySelectorAll('.cal-cell.available').forEach(cell=>cell.addEventListener('mouseenter',()=>{{
            setTimeout(()=>{{
              const row=cell.closest('.element-row');
              const qdates=document.getElementById('quick-dates'), qaction=document.getElementById('quick-action');
              if(qaction&&!qaction.hidden&&editMode)qaction.textContent='RESERVE CHANGES';
              if(!qdates||!row||!arrival.value||!departure.value)return;
              const ms=(new Date(departure.value+'T12:00:00')-new Date(arrival.value+'T12:00:00'))/86400000;
              if(isDay(row))qdates.textContent=arrival.value+' to '+dayBefore(departure.value)+' · '+ms+' day'+(ms===1?'':'s');
              else qdates.textContent=arrival.value+' to '+departure.value+' · '+ms+' night'+(ms===1?'':'s')+' · departs '+departure.value;
            }},0);
          }}));
        }})();
        </script>"""
        text = text.replace('</body>', script + '</body>', 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
