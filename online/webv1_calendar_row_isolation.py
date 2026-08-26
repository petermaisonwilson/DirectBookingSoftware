from __future__ import annotations

from fastapi.responses import Response


def install_calendar_row_isolation(app) -> None:
    """Keep calendar selection styling and geometry inside the chosen Element row."""

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
          .element-row .cal-cell.selected-start {
            box-shadow:none !important;
            outline:none !important;
          }

          /* Each Element is a visually separate lane.  Calendar cells are 52px
             high and the row reserves a further 4px white separator below them.
             This prevents a yellow selection/departure marker from visually
             joining the pale-green availability of the next Element. */
          #calendar-scroll .element-row {
            min-height:56px !important;
            height:56px;
            border-bottom:4px solid #fff !important;
            box-sizing:border-box;
            overflow:hidden;
          }
          #calendar-scroll .element-row .cal-cell {
            min-height:52px !important;
            height:52px !important;
            align-self:start;
            box-sizing:border-box;
          }
          #calendar-scroll .element-row .cal-name {
            height:52px;
            box-sizing:border-box;
            align-self:start;
          }
          #calendar-scroll .element-row .night-departure {
            max-height:26px;
          }

          /* A selection action is valid only after JavaScript has positioned it
             over a completed range.  Never allow an unpositioned action to fall
             into the Element-name column or the following row. */
          #calendar-scroll .element-row .selection-action:not([style*="grid-column"]) {
            display:none !important;
          }
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
