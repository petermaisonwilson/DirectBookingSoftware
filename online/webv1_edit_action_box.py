from __future__ import annotations

from urllib.parse import quote_plus

from fastapi.responses import Response

from .app import esc


def install_edit_action_box(app) -> None:
    """Keep edit confirmation controls out of calendar cells.

    In edit mode the calendar is selection-only: one tentative Element/range at a time.
    RESERVE CHANGES and CANCEL EDIT live together in the edit notice above the calendar.
    """

    @app.middleware('http')
    async def edit_action_box(request, call_next):
        response = await call_next(request)
        if (
            request.url.path != '/availability/calendar-v2'
            or request.method != 'GET'
            or response.status_code != 200
            or 'text/html' not in response.headers.get('content-type', '')
        ):
            return response

        edit_hold = request.query_params.get('edit_hold', '')
        if not edit_hold.isdigit() or int(edit_hold) <= 0:
            return response

        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')

        et = request.query_params.get('element_type', '')
        a = request.query_params.get('arrival', '')
        d = request.query_params.get('departure', '')
        cancel = '/availability/calendar-v2?element_type=' + quote_plus(et)
        if a:
            cancel += '&arrival=' + quote_plus(a)
        if d:
            cancel += '&departure=' + quote_plus(d)

        # Replace the current edit notice with one clear action area.
        start = text.find('<div class="card edit-notice">')
        if start >= 0:
            end = text.find('</div>', start)
            if end >= 0:
                end += len('</div>')
                original = text[start:end]
                # Preserve the existing "Editing X" heading if available.
                heading_end = original.find('</strong>')
                heading = original[:heading_end + len('</strong>')] if heading_end >= 0 else '<strong>Editing booking item</strong>'
                replacement = (
                    '<div class="card edit-notice edit-action-box">'
                    f'{heading} — select one replacement date range on one Element below. '
                    '<span class="edit-action-buttons">'
                    '<button id="edit-reserve-changes" type="button" disabled>RESERVE CHANGES</button> '
                    f'<a class="button secondary" href="{esc(cancel)}">CANCEL EDIT</a>'
                    '</span></div>'
                )
                text = text[:start] + replacement + text[end:]

        injection = '''
        <style id="edit-action-box-style">
          .edit-action-box{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
          .edit-action-buttons{display:inline-flex;gap:8px;align-items:center}
          /* In edit mode there must never be text/buttons drawn over selectable date cells. */
          .element-row .selection-action{display:none !important}
        </style>
        <script id="edit-action-box-script">
        (()=>{
          const topButton=document.getElementById('edit-reserve-changes');
          const calendar=document.getElementById('calendar-scroll');
          if(!topButton||!calendar)return;
          let pendingAction=null;

          const sync=()=>{
            // The underlying selector exposes exactly one completed tentative range by
            // unhiding that row's selection-action. A new first click clears it again.
            const completed=[...calendar.querySelectorAll('.element-row .selection-action')]
              .find(btn=>!btn.hidden);
            pendingAction=completed||null;
            topButton.disabled=!pendingAction;
          };

          const observer=new MutationObserver(sync);
          calendar.querySelectorAll('.element-row .selection-action').forEach(btn=>{
            observer.observe(btn,{attributes:true,attributeFilter:['hidden','style']});
          });
          calendar.addEventListener('click',()=>setTimeout(sync,0),true);

          topButton.addEventListener('click',()=>{
            sync();
            if(!pendingAction)return;
            pendingAction.click();
          });
          sync();
        })();
        </script>
        '''
        text = text.replace('</body>', injection + '</body>', 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
