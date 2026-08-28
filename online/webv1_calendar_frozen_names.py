from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response


def install_calendar_frozen_names(app) -> None:
    """Keep the Element-name column visible while the availability grid scrolls horizontally."""

    @app.middleware('http')
    async def calendar_frozen_names(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != '/availability/calendar-v2':
            return response
        if response.status_code >= 400 or 'text/html' not in response.headers.get('content-type', ''):
            return response

        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')

        css = '''
        <style id="frozen-element-name-column">
          #calendar-scroll .cal-row > .cal-name,
          #progress-scroll .progress-row > .cal-name,
          #calendar-scroll .cal-head > .cal-name,
          #progress-scroll .cal-head > .cal-name {
            position: sticky !important;
            left: 0 !important;
            z-index: 40 !important;
            width: 190px;
            min-width: 190px;
            max-width: 190px;
            box-sizing: border-box;
            background: #fff !important;
            border-right: 2px solid #c4ccd4 !important;
            box-shadow: 5px 0 8px -7px rgba(0,0,0,.55);
          }
          #calendar-scroll .cal-head > .cal-name,
          #progress-scroll .cal-head > .cal-name {
            background: #f4f6f8 !important;
            z-index: 60 !important;
          }
        </style>
        '''
        text = text.replace('</head>', css + '</head>', 1)
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
