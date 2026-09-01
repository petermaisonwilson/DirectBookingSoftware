from __future__ import annotations

import json


def hold_warning_markup(context) -> str:
    """Render the basket hold warning on every logged-in page for a selected Client."""
    if not context:
        return ''
    company_id = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
    if not company_id:
        return ''
    csrf = json.dumps(str(context['csrf_token']))
    return f'''<div id="global-hold-modal" class="global-hold-modal" hidden><div class="global-hold-dialog">
    <h2>Still want to hold these Elements?</h2><p id="global-hold-names"></p>
    <p>If KEEP is not clicked before the hold expires, everything is released automatically.</p>
    <p><button id="global-hold-keep" type="button">KEEP</button> <button id="global-hold-release" type="button" class="secondary">RELEASE</button></p>
    </div></div>
    <style>
    .global-hold-modal{{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:1000;display:flex;align-items:center;justify-content:center}}
    .global-hold-modal[hidden]{{display:none}} .global-hold-dialog{{background:white;padding:24px;border-radius:10px;max-width:620px;width:90%;max-height:80vh;overflow:auto}}
    </style>
    <script>
    (()=>{{
      const modal=document.getElementById('global-hold-modal');
      if(!modal || document.getElementById('hold-modal')) return;
      const names=document.getElementById('global-hold-names');
      const keep=document.getElementById('global-hold-keep');
      const release=document.getElementById('global-hold-release');
      const csrf={csrf};
      let hadItems=false,transition=false;
      async function post(url){{
        const body=new URLSearchParams();body.set('csrf',csrf);
        return fetch(url,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},body:body.toString()}});
      }}
      async function check(){{
        if(transition)return;
        let r;try{{r=await fetch('/availability/basket',{{cache:'no-store'}})}}catch(e){{return}}
        if(!r.ok)return;
        let data;try{{data=await r.json()}}catch(e){{return}}
        const items=Array.isArray(data.items)?data.items:[];
        if(items.length)hadItems=true;
        if(!items.length){{
          modal.hidden=true;
          if(hadItems){{transition=true;alert('Your held Elements have expired and have been released.');window.location.reload();}}
          return;
        }}
        if(items.some(x=>x.needs_confirmation)){{
          names.textContent=items.map(x=>(x.lead_name?x.lead_name+' — ':'')+x.element_name).join(', ');
          modal.hidden=false;
        }} else {{modal.hidden=true;}}
      }}
      keep.addEventListener('click',async()=>{{const r=await post('/availability/holds/renew');if(r.ok){{modal.hidden=true;hadItems=true}}else alert('Unable to renew holds.')}});
      release.addEventListener('click',async()=>{{if(transition)return;transition=true;const r=await post('/availability/holds/release');if(r.ok)window.location.reload();else{{transition=false;alert('Unable to release holds.')}}}});
      check();setInterval(check,5000);
    }})();
    </script>'''
