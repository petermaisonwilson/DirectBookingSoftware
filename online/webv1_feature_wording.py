from __future__ import annotations

from fastapi.responses import Response


def install_feature_wording(app) -> None:
    @app.middleware('http')
    async def feature_wording(request, call_next):
        response = await call_next(request)
        if response.status_code >= 400 or 'text/html' not in response.headers.get('content-type',''):
            return response
        if not (request.url.path.startswith('/setup') or request.url.path == '/operations'):
            return response
        body=b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk,bytes) else str(chunk).encode('utf-8')
        text=body.decode('utf-8')
        replacements=(
            ('Add-on rules','Feature / Extra Rules'),
            ('Add-on Timings','Feature / Extra Timings'),
            ('Add-ons','Features & Extras'),
            ('Add-on','Feature / Extra'),
        )
        for old,new in replacements:
            text=text.replace(old,new)
        headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}}
        return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
