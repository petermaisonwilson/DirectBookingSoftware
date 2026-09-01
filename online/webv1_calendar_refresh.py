from __future__ import annotations

from starlette.responses import Response


CALENDAR_REFRESH_SCRIPT = '''<script id="hold-expiry-calendar-refresh">
(function(){
  let hadLiveHold = document.querySelectorAll('.cal-bar.held').length > 0;
  async function watchExpiredHold(){
    try {
      const r = await fetch('/availability/holds', {cache:'no-store'});
      if(!r.ok) return;
      const data = await r.json();
      const hasLiveHold = Array.isArray(data.holds) && data.holds.length > 0;
      if(hadLiveHold && !hasLiveHold){
        window.location.reload();
        return;
      }
      if(hasLiveHold) hadLiveHold = true;
    } catch(e) {}
  }
  setInterval(watchExpiredHold, 2000);
})();
</script>'''


def install_calendar_expiry_refresh(app) -> None:
    @app.middleware('http')
    async def calendar_expiry_refresh(request, call_next):
        response = await call_next(request)
        if request.url.path != '/availability/calendar-v2' or response.status_code != 200:
            return response
        content_type = response.headers.get('content-type', '')
        if 'text/html' not in content_type:
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        if 'hold-expiry-calendar-refresh' not in text:
            text = text.replace('</body>', CALENDAR_REFRESH_SCRIPT + '</body>')
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
