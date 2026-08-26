from __future__ import annotations

from fastapi.responses import Response


def install_edit_action_guard(app) -> None:
    """Keep edit mode visually quiet until the user completes a replacement range."""

    @app.middleware('http')
    async def edit_action_guard(request, call_next):
        response = await call_next(request)
        if (
            request.url.path != '/availability/calendar-v2'
            or request.method != 'GET'
            or response.status_code != 200
            or 'text/html' not in response.headers.get('content-type', '')
            or not request.query_params.get('edit_hold', '').isdigit()
        ):
            return response

        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')

        guard = '''<style id="edit-action-guard-style">
        body.edit-pristine .selection-action,
        body.edit-pristine #quick-action { display:none !important; }
        </style>
        <script id="edit-action-guard">
        (()=>{
          const calendar=document.getElementById('calendar-scroll');
          const departure=document.getElementById('departure-date');
          if(!calendar||!departure)return;
          document.body.classList.add('edit-pristine');

          const sync=()=>{
            if(departure.value){
              document.body.classList.remove('edit-pristine');
            }else{
              document.body.classList.add('edit-pristine');
            }
          };

          let touched=false;
          document.addEventListener('click',(ev)=>{
            const cell=ev.target.closest('.date-pick');
            if(!cell||!calendar.contains(cell))return;
            if(!touched){
              touched=true;
              document.body.classList.add('edit-pristine');
              setTimeout(()=>document.body.classList.add('edit-pristine'),0);
              return;
            }
            setTimeout(sync,0);
          },true);
        })();
        </script>'''
        text = text.replace('</body>', guard + '</body>', 1)
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
