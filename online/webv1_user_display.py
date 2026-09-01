from __future__ import annotations

from fastapi.responses import Response


def install_user_display_rules(app) -> None:
    """Apply presentation-only rules to every HTML screen.

    Internal/database dates remain ISO yyyy-mm-dd.  Only text the user sees is
    rendered dd/mm/yyyy.  Date inputs are explicitly British/European locale.
    Also collapse accidental double-escaping so an ampersand is shown as '&'.
    """

    @app.middleware('http')
    async def user_display_rules(request, call_next):
        response = await call_next(request)
        if (
            response.status_code >= 400
            or 'text/html' not in response.headers.get('content-type', '')
        ):
            return response

        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')

        # If a stored/display value was already escaped before esc() saw it,
        # the browser would otherwise show the literal text "&amp;".  One layer
        # of HTML escaping is correct and renders as a plain '&'.
        while '&amp;amp;' in text:
            text = text.replace('&amp;amp;', '&amp;')

        text = text.replace('<html>', '<html lang="en-GB">', 1)

        script = r'''<script id="directbooking-user-display-rules">
(() => {
  const isoDate = /\b(\d{4})-(\d{2})-(\d{2})\b/g;
  const formatText = value => String(value || '').replace(isoDate, (_, y, m, d) => `${d}/${m}/${y}`);

  const formatNode = node => {
    if (!node || node.nodeType !== Node.TEXT_NODE) return;
    const p = node.parentElement;
    if (!p || /^(SCRIPT|STYLE|TEXTAREA|OPTION)$/.test(p.tagName)) return;
    const next = formatText(node.nodeValue);
    if (next !== node.nodeValue) node.nodeValue = next;
  };

  const scan = root => {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) { formatNode(root); return; }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) formatNode(n);
  };

  // Keep native date controls and all submitted values ISO internally, but
  // force the user-facing control locale to dd/mm/yyyy.
  document.querySelectorAll('input[type="date"]').forEach(i => i.setAttribute('lang', 'en-GB'));
  scan(document.body);

  // Calendar popovers and other live UI are created after page load.
  const observer = new MutationObserver(records => {
    for (const record of records) {
      record.addedNodes.forEach(scan);
      if (record.type === 'characterData') formatNode(record.target);
    }
  });
  observer.observe(document.body, {subtree:true, childList:true, characterData:true});
})();
</script>'''
        text = text.replace('</body>', script + '</body>', 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
