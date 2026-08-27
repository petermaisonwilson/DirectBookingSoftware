from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import Response

from .app import COOKIE_NAME
from .setup015_core import rows
from .webv1_booking_requirements_refinements import _addon_caps


def install_feature_booking_ui(app) -> None:
    database = app.state.database

    @app.middleware('http')
    async def feature_booking_ui(request: Request, call_next):
        response = await call_next(request)
        if response.status_code >= 400 or 'text/html' not in response.headers.get('content-type',''):
            return response
        if request.url.path not in {'/availability/start','/availability/requirements-v2','/availability/calendar-v2'}:
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk,bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        context = database.session_context(request.cookies.get(COOKIE_NAME))
        if not context:
            return Response(content=text,status_code=response.status_code,media_type='text/html')
        cid = context['acting_company_id'] if context['role']=='supervisor' else context['company_id']
        if not cid:
            return Response(content=text,status_code=response.status_code,media_type='text/html')
        cid = int(cid)

        if request.url.path in {'/availability/start','/availability/requirements-v2'} and 'Booking requirements' in text:
            caps = _addon_caps(database,cid)
            features = []
            for item in rows(database, '''SELECT id,name,feature_group FROM setup_addons
                                          WHERE company_id=? AND active=1 AND item_kind='Feature'
                                            AND ask_before_availability=1
                                          ORDER BY feature_group,name COLLATE NOCASE''',(cid,)):
                aid = int(item['id'])
                features.append({'id':aid,'name':str(item['name']),'group':str(item['feature_group'] or ''),'cap':int(caps.get(aid,0))})
            data = json.dumps(features,ensure_ascii=False).replace('</','<\\/')
            injection = f'''
            <style id="feature-booking-style">
              .generated-feature-card{{margin-top:16px}}
              .feature-group-box{{border:1px solid #d6dde5;border-radius:8px;padding:12px;margin:10px 0}}
              .feature-group-box h3{{margin:0 0 8px}}
              .feature-choice{{display:inline-flex;align-items:center;gap:5px;margin:4px 14px 4px 0;font-weight:normal}}
              .feature-single{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:8px 0}}
              .feature-single input[type=number]{{width:80px}}
            </style>
            <script id="feature-booking-script">
            (()=>{{
              const defs={data};
              const oldHeading=[...document.querySelectorAll('h2')].find(h=>h.textContent.trim()==='Must-have requirements');
              if(!oldHeading||!defs.length)return;
              const oldCard=oldHeading.closest('.card'); if(!oldCard)return;
              const oldValues={{}};
              oldCard.querySelectorAll('input[name^="addon_"]').forEach(i=>{{const id=i.name.slice(6); const n=Number(i.value||0); if(n>(oldValues[id]||0))oldValues[id]=n; i.disabled=true;}});
              oldCard.style.display='none';
              const card=document.createElement('div'); card.className='card generated-feature-card';
              card.innerHTML='<h2>What do you need?</h2><p class="muted">Choose only the Features you actually require. Grouped Features allow one choice only.</p>';
              const groups={{}}; const singles=[];
              defs.forEach(d=>{{if(d.group){{(groups[d.group]??=[]).push(d)}}else singles.push(d)}});
              Object.entries(groups).forEach(([name,items])=>{{
                const box=document.createElement('div');box.className='feature-group-box';box.innerHTML='<h3>'+name+'</h3>';
                const none=document.createElement('label');none.className='feature-choice';none.innerHTML='<input type="radio" name="feature_group_'+name.replace(/[^a-z0-9]+/gi,'_')+'" value=""> None / not required';box.appendChild(none);
                let selected=false;
                items.forEach(d=>{{
                  const label=document.createElement('label');label.className='feature-choice';const radio=document.createElement('input');radio.type='radio';radio.name='feature_group_'+name.replace(/[^a-z0-9]+/gi,'_');radio.value=String(d.id);if(Number(oldValues[d.id]||0)>0){{radio.checked=true;selected=true;}}const hidden=document.createElement('input');hidden.type='hidden';hidden.name='addon_'+d.id;hidden.value=radio.checked?'1':'0';radio.addEventListener('change',()=>{{items.forEach(x=>{{const h=box.querySelector('input[type=hidden][name="addon_'+x.id+'"]');if(h)h.value=String(x.id===d.id?1:0);}});}});label.append(radio,document.createTextNode(' '+d.name),hidden);box.appendChild(label);
                }});
                if(!selected)none.querySelector('input').checked=true;
                none.querySelector('input').addEventListener('change',()=>{{if(none.querySelector('input').checked)box.querySelectorAll('input[type=hidden][name^="addon_"]').forEach(h=>h.value='0');}});
                card.appendChild(box);
              }});
              singles.forEach(d=>{{
                const row=document.createElement('div');row.className='feature-single';const tick=document.createElement('input');tick.type='checkbox';tick.checked=Number(oldValues[d.id]||0)>0;tick.disabled=d.cap<=0;const label=document.createElement('strong');label.textContent=d.name;const qty=document.createElement('input');qty.type='number';qty.min='1';qty.max=String(Math.max(1,d.cap));qty.value=String(Math.max(1,Math.min(d.cap||1,Number(oldValues[d.id]||1))));qty.style.display=d.cap>1?'inline-block':'none';const hidden=document.createElement('input');hidden.type='hidden';hidden.name='addon_'+d.id;const note=document.createElement('small');note.className='muted';note.textContent=d.cap<=0?'Not available on any Element':(d.cap===1?'':'Maximum '+d.cap);const sync=()=>{{let n=Math.max(1,Math.min(d.cap||1,Number(qty.value||1)));qty.value=String(n);hidden.value=tick.checked?String(d.cap===1?1:n):'0';qty.disabled=!tick.checked;}};tick.addEventListener('change',sync);qty.addEventListener('input',sync);row.append(tick,label,qty,hidden,note);sync();card.appendChild(row);
              }});
              oldCard.parentNode.insertBefore(card,oldCard);
            }})();
            </script>'''
            text = text.replace('</body>',injection+'</body>',1)

        if request.url.path == '/availability/calendar-v2':
            # Reasons already appear beneath the Element name. Suppress the extra
            # purple special popover, while leaving the ordinary quick popup and
            # More Info available on unsuitable Elements.
            text = text.replace('</body>','<style id="no-special-unsuitable-popup">#party-unsuitable-popover{display:none!important}</style></body>',1)

        headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}}
        return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
