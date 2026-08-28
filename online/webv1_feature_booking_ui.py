from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import Response

from .app import COOKIE_NAME
from .setup015_core import one, rows
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
            definitions = []
            for item in rows(database, '''SELECT id,name,item_kind,feature_group FROM setup_addons
                                          WHERE company_id=? AND active=1 AND ask_before_availability=1
                                          ORDER BY item_kind,feature_group,name COLLATE NOCASE''',(cid,)):
                aid = int(item['id'])
                definitions.append({
                    'id':aid,'name':str(item['name']),'kind':str(item['item_kind'] or 'Extra'),
                    'group':str(item['feature_group'] or ''),'cap':int(caps.get(aid,0))
                })
            token = request.cookies.get(COOKIE_NAME, '')
            saved_session = one(database, 'SELECT ready FROM booking_requirement_sessions WHERE company_id=? AND session_token=?', (cid, token))
            restore_saved = bool(saved_session and int(saved_session['ready'] or 0))
            data = json.dumps(definitions,ensure_ascii=False).replace('</','<\\/')
            injection = f'''
            <style id="feature-booking-style">
              .generated-requirements-card{{margin-top:16px}}
              .requirements-section{{margin-top:14px}}
              .requirements-section:first-of-type{{margin-top:0}}
              .requirements-section h2{{margin-bottom:8px}}
              .feature-group-box{{border:1px solid #d6dde5;border-radius:8px;padding:12px;margin:10px 0}}
              .feature-group-box h3{{margin:0 0 8px}}
              .requirement-choice{{display:flex;align-items:center;min-height:34px;gap:9px;margin:2px 0;font-weight:normal}}
              .requirement-choice input[type=radio],.requirement-choice input[type=checkbox]{{width:18px!important;height:18px;margin:0;flex:0 0 18px}}
              .requirement-choice .choice-name{{min-width:180px}}
              .requirement-choice input[type=number]{{width:80px;margin:0}}
              .requirement-choice small{{margin-left:4px}}
              .feature-group-choices{{display:flex;flex-wrap:wrap;column-gap:22px;row-gap:2px}}
              .feature-group-choices .requirement-choice{{margin-right:4px}}
            </style>
            <script id="feature-booking-script">
            (()=>{{
              const defs={data};
              const restoreSaved={str(restore_saved).lower()};
              const oldHeading=[...document.querySelectorAll('h2')].find(h=>h.textContent.trim()==='Must-have requirements');
              if(!oldHeading||!defs.length)return;
              const oldCard=oldHeading.closest('.card'); if(!oldCard)return;
              const oldValues={{}};
              oldCard.querySelectorAll('input[name^="addon_"]').forEach(i=>{{const id=i.name.slice(6);const n=Number(i.value||0);if(n>(oldValues[id]||0))oldValues[id]=n;i.disabled=true;}});
              oldCard.style.display='none';

              const card=document.createElement('div');card.className='card generated-requirements-card';
              const features=defs.filter(d=>d.kind==='Feature'), extras=defs.filter(d=>d.kind==='Extra');

              const addTickRow=(parent,d,restoreCurrent)=>{{
                const row=document.createElement('label');row.className='requirement-choice';
                const tick=document.createElement('input');tick.type='checkbox';tick.checked=restoreCurrent&&Number(oldValues[d.id]||0)>0;tick.disabled=d.cap<=0;
                const name=document.createElement('span');name.className='choice-name';name.textContent=d.name;
                const qty=document.createElement('input');qty.type='number';qty.min='1';qty.max=String(Math.max(1,d.cap));qty.value=String(Math.max(1,Math.min(d.cap||1,restoreCurrent?Number(oldValues[d.id]||1):1)));qty.style.display=d.cap>1?'inline-block':'none';
                const hidden=document.createElement('input');hidden.type='hidden';hidden.name='addon_'+d.id;
                const note=document.createElement('small');note.className='muted';note.textContent=d.cap<=0?'Not available on any Element':(d.cap>1?'Maximum '+d.cap:'');
                const sync=()=>{{let n=Math.max(1,Math.min(d.cap||1,Number(qty.value||1)));qty.value=String(n);hidden.value=tick.checked?String(d.cap===1?1:n):'0';qty.disabled=!tick.checked;}};
                tick.addEventListener('change',sync);qty.addEventListener('input',sync);row.append(tick,name,qty,hidden,note);sync();parent.appendChild(row);
              }};

              if(features.length){{
                const section=document.createElement('section');section.className='requirements-section';section.innerHTML='<h2>Features needed</h2><p class="muted">Choose the Features your booking requires.</p>';
                const groups={{}}, singles=[];features.forEach(d=>{{if(d.group)(groups[d.group]??=[]).push(d);else singles.push(d)}});
                Object.entries(groups).forEach(([groupName,items])=>{{
                  const box=document.createElement('div');box.className='feature-group-box';box.innerHTML='<h3>'+groupName+'</h3>';
                  const choices=document.createElement('div');choices.className='feature-group-choices';
                  const inputName='feature_group_'+groupName.replace(/[^a-z0-9]+/gi,'_').replace(/^_+|_+$/g,'');
                  const none=document.createElement('label');none.className='requirement-choice';const nr=document.createElement('input');nr.type='radio';nr.name=inputName;nr.value='';nr.required=true;nr.checked=!restoreSaved;none.append(nr,document.createTextNode(' None / not required'));choices.appendChild(none);
                  let restoredChoice=false;
                  items.forEach(d=>{{const label=document.createElement('label');label.className='requirement-choice';const radio=document.createElement('input');radio.type='radio';radio.name=inputName;radio.value=String(d.id);if(restoreSaved&&Number(oldValues[d.id]||0)>0){{radio.checked=true;restoredChoice=true;}}const hidden=document.createElement('input');hidden.type='hidden';hidden.name='addon_'+d.id;hidden.value='0';label.append(radio,document.createTextNode(' '+d.name),hidden);choices.appendChild(label);}});
                  if(restoreSaved&&!restoredChoice)nr.checked=true;
                  const sync=()=>{{const checked=choices.querySelector('input[type=radio]:checked'),chosen=checked?checked.value:'';items.forEach(x=>{{const h=choices.querySelector('input[type=hidden][name="addon_'+x.id+'"]');if(h)h.value=chosen===String(x.id)?'1':'0';}});}};
                  choices.querySelectorAll('input[type=radio]').forEach(r=>r.addEventListener('change',sync));sync();box.appendChild(choices);section.appendChild(box);
                }});
                singles.forEach(d=>addTickRow(section,d,restoreSaved));card.appendChild(section);
              }}

              if(extras.length){{
                const section=document.createElement('section');section.className='requirements-section';section.innerHTML='<h2>Extras needed</h2><p class="muted">Please tick any items you wish to add to your search</p>';
                extras.forEach(d=>addTickRow(section,d,restoreSaved));card.appendChild(section);
              }}
              oldCard.parentNode.insertBefore(card,oldCard);
            }})();
            </script>'''
            text = text.replace('</body>',injection+'</body>',1)

        if request.url.path == '/availability/calendar-v2':
            text = text.replace('</body>','<style id="no-special-unsuitable-popup">#party-unsuitable-popover{display:none!important}</style></body>',1)

        headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}}
        return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
