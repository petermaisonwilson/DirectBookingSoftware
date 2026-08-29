from __future__ import annotations

import json
from urllib.parse import quote_plus

from fastapi.responses import Response

from .app import COOKIE_NAME, esc
from .setup015_core import rows


def install_calendar_edit_semantics(app) -> None:
    """Human-facing calendar selection and edit behaviour.

    * Initial selections turn yellow before they are reserved.
    * EDIT opens with the original yellow hold visible but no action button.
    * The edited hold's own yellow cells stay clickable.
    * A new selection visually replaces the old yellow range.
    * RESERVE CHANGES appears only after a new range has been chosen.
    * CANCEL EDIT leaves the original hold untouched.
    * Per-day and per-night selection both use first booked day + last booked day.
      Storage remains end-exclusive; night bookings add a departure-morning half cell.
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

        for element in rows(database, 'SELECT id,pricing_method FROM setup_elements WHERE company_id=? AND active=1', (company_id,)):
            marker = f'<div class="cal-row element-row" data-element="{int(element["id"])}"'
            replacement = marker + f' data-pricing-method="{esc(element["pricing_method"])}"'
            text = text.replace(marker, replacement, 1)

        night_holds = [
            {
                'hold_id': int(r['hold_id']),
                'element_id': int(r['element_id']),
                'element_name': str(r['element_name']),
                'arrival_date': str(r['arrival_date']),
                'departure_date': str(r['departure_date']),
            }
            for r in rows(
                database,
                '''SELECT h.id AS hold_id,h.element_id,h.arrival_date,h.departure_date,e.name AS element_name
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
                    'Choose a new range on this or another Element. '
                    f'<a class="button secondary" href="{esc(cancel)}">CANCEL EDIT</a></div>',
                    1,
                )

        text = text.replace(
            'Click a start day and then a later day on the same Element.',
            'Click the first day you want and then the last full day you want. Your selected range turns yellow immediately. Night-priced Elements add a half-square on the following departure morning.',
            1,
        )

        script = f"""<style>
        .selection-action{{justify-self:center;width:max-content;max-width:90%;}}
        .cal-cell.editable-own:hover{{outline:2px solid #9a7a1f;outline-offset:-2px;}}
        .cal-cell.preview-selected{{background:#ffe39a !important;box-shadow:inset 0 0 0 2px #c59a2a;}}
        .cal-cell.edit-original-suppressed{{background:#dff2df !important;}}
        .cal-cell.edit-original-suppressed.preview-selected{{background:#ffe39a !important;}}
        .night-departure{{align-self:start;height:50%;z-index:5;pointer-events:none;background:#ffe39a;border-left:1px solid rgba(255,255,255,.65);border-right:1px solid rgba(255,255,255,.65);box-sizing:border-box;}}
        .progress-row .night-departure{{z-index:5;}}
        </style>
        <script id="calendar-edit-semantics">
        (()=>{{
          const editMode={str(editing).lower()};
          const editedHold={int(edit_hold or 0)};
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
          const clearPreview=()=>document.querySelectorAll('.cal-cell.preview-selected').forEach(x=>x.classList.remove('preview-selected'));
          const clearBars=()=>{{document.querySelectorAll('.selection-action').forEach(b=>b.hidden=true);clearTempDeparture();}};
          const editedNight=nightHolds.find(h=>h.hold_id===editedHold);

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

          const paintRange=(row,a,lastDay)=>{{
            clearPreview();
            const ds=dates(),s=ds.indexOf(a),e=ds.indexOf(lastDay);
            if(s<0||e<0||e<s)return;
            [...row.querySelectorAll('.cal-cell[data-date]')].forEach(cell=>{{
              const idx=ds.indexOf(cell.dataset.date);
              if(idx>=s&&idx<=e){{
                cell.classList.remove('edit-original-suppressed');
                cell.classList.add('preview-selected');
              }}
            }});
          }};

          const suppressEditedOriginal=()=>{{
            if(!editMode)return;
            document.querySelectorAll('.cal-cell.editable-own').forEach(x=>x.classList.add('edit-original-suppressed'));
            if(editedNight)document.querySelectorAll('.night-departure:not(.temp)').forEach(x=>{{
              if(x.dataset.date===editedNight.departure_date&&x.closest('.element-row')?.dataset.element===String(editedNight.element_id))x.style.display='none';
            }});
          }};

          const restoreEditedOriginal=()=>{{
            document.querySelectorAll('.cal-cell.edit-original-suppressed').forEach(x=>x.classList.remove('edit-original-suppressed'));
            document.querySelectorAll('.night-departure:not(.temp)').forEach(x=>x.style.display='');
          }};

          const showBar=(row,a,lastDay,internalEnd)=>{{
            clearBars();
            paintRange(row,a,lastDay);
            const ds=dates(),s=ds.indexOf(a),last=ds.indexOf(lastDay);
            if(s<0||last<0||last<s)return;
            let e=ds.indexOf(internalEnd);
            if(e<0)e=last+1;
            if(e<=s)e=last+1;
            const b=row.querySelector('.selection-action'); if(!b)return;
            b.style.gridColumn=(s+2)+' / '+(e+2); b.style.gridRow='1';
            b.textContent=editMode?'RESERVE CHANGES':'RESERVE'; b.hidden=false;
            if(!isDay(row))addDeparture(row,internalEnd,true);
          }};

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

          if(editMode){{
            document.querySelectorAll('.selection-action').forEach(b=>b.hidden=true);
            document.querySelectorAll('.element-row .cal-cell.selected-date,.element-row .cal-cell.selected-start').forEach(cell=>cell.classList.remove('selected-date','selected-start'));
            document.querySelectorAll('.cal-cell.editable-own').forEach(cell=>{{cell.style.pointerEvents='auto';cell.style.cursor='pointer';}});
          }}

          document.addEventListener('click',(ev)=>{{
            const cell=ev.target.closest('.date-pick');
            if(!cell||!calendar.contains(cell))return;
            ev.preventDefault(); ev.stopImmediatePropagation();
            const pop=document.getElementById('quick-popover'); if(pop)pop.hidden=true;
            const row=cell.closest('.element-row'); if(!row)return;
            const eid=Number(row.dataset.element), picked=cell.dataset.date;

            if(!first||chosenElement!==eid){{
              suppressEditedOriginal();
              clearBars(); clearPreview();
              first=picked; chosenElement=eid; arrival.value=picked; departure.value='';
              cell.classList.remove('edit-original-suppressed');
              cell.classList.add('preview-selected');
              return;
            }}

            if(picked<first){{
              first=picked; arrival.value=picked; departure.value=''; clearBars(); clearPreview();
              cell.classList.remove('edit-original-suppressed');
              cell.classList.add('preview-selected');
              return;
            }}

            const internalEnd=dayAfter(picked);
            arrival.value=first; departure.value=internalEnd;
            showBar(row,first,picked,internalEnd); first='';
          }},true);

          document.querySelectorAll('.cal-cell.available,.cal-cell.editable-own').forEach(cell=>cell.addEventListener('mouseenter',()=>{{
            if(cell.classList.contains('preview-selected')||cell.classList.contains('selected-date')||cell.classList.contains('selected-start'))return;
            setTimeout(()=>{{
              const row=cell.closest('.element-row');
              const qdates=document.getElementById('quick-dates'), qaction=document.getElementById('quick-action');
              if(qaction&&!qaction.hidden&&editMode)qaction.textContent='RESERVE CHANGES';
              if(!qdates||!row||!arrival.value||!departure.value)return;
              const ms=(new Date(departure.value+'T12:00:00')-new Date(arrival.value+'T12:00:00'))/86400000;
              if(isDay(row))qdates.textContent=arrival.value+' to '+dayBefore(departure.value)+' · '+ms+' day'+(ms===1?'':'s');
              else qdates.textContent=arrival.value+' to '+dayBefore(departure.value)+' · '+ms+' night'+(ms===1?'':'s')+' · departs '+departure.value;
            }},0);
          }}));

          window.addEventListener('pageshow',()=>{{if(editMode&&!document.querySelector('.preview-selected'))restoreEditedOriginal();}});
        }})();
        </script>"""
        text = text.replace('</body>', script + '</body>', 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
