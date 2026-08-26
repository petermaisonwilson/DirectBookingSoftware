from __future__ import annotations

from fastapi.responses import Response


def install_calendar_row_isolation(app) -> None:
    """Prevent the global date-range highlight from appearing on every Element row."""

    @app.middleware('http')
    async def calendar_row_isolation(request, call_next):
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

        injection = '''
        <style id="calendar-row-isolation-style">
          /* Header may show the requested window, but Element rows only show the
             actual tentative yellow selection on the Element being chosen. */
          .element-row .cal-cell.selected-date,
          .element-row .cal-cell.selected-start { box-shadow:none !important; outline:none !important; }
        </style>
        <script id="calendar-row-isolation-script">
        (()=>{
          const clearCrossRowRange=()=>{
            document.querySelectorAll('.element-row .cal-cell.selected-date,.element-row .cal-cell.selected-start')
              .forEach(cell=>cell.classList.remove('selected-date','selected-start'));
          };
          clearCrossRowRange();
          const cal=document.getElementById('calendar-scroll');
          if(cal){
            const observer=new MutationObserver(clearCrossRowRange);
            observer.observe(cal,{subtree:true,attributes:true,attributeFilter:['class']});
          }
        })();
        </script>
        '''
        text = text.replace('</body>', injection + '</body>', 1)
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
