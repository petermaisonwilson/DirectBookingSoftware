from __future__ import annotations

from fastapi.responses import Response


def install_calendar_action_fixes(app) -> None:
    """Restore visible Availability actions and simplify the date controls."""

    @app.middleware('http')
    async def calendar_action_fixes(request, call_next):
        response = await call_next(request)
        if (
            request.url.path != '/availability/calendar-v2'
            or response.status_code >= 400
            or 'text/html' not in response.headers.get('content-type', '')
        ):
            return response

        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        headers = {
            k: v for k, v in response.headers.items()
            if k.lower() not in {'content-length', 'content-type'}
        }

        change = '<a class="button secondary" style="margin-left:10px" href="/availability/start">Change</a>'
        if change in text and 'id="requirements-add"' not in text:
            text = text.replace(
                change,
                change + ' <button type="button" id="requirements-add" class="button secondary">ADD</button>',
                1,
            )

        script = r'''<script id="calendar-action-fixes">
        (() => {
          const arrival = document.getElementById('arrival-date');
          const departure = document.getElementById('departure-date');
          const editHold = document.getElementById('edit-hold');
          const calendarStart = document.getElementById('calendar-start');
          if (calendarStart && calendarStart.parentElement) {
            calendarStart.parentElement.style.display = 'none';
          }
          const a = arrival ? arrival.value : '';
          const d = departure ? departure.value : '';

          function headerDates() {
            return [...document.querySelectorAll('#calendar-scroll .cal-date[data-date]')]
              .map(x => x.dataset.date);
          }

          function wholeRangeAvailable(row, start, end) {
            const cells = [...row.querySelectorAll('.cal-cell[data-date]')]
              .filter(cell => cell.dataset.date >= start && cell.dataset.date < end);
            return cells.length > 0 && cells.every(cell => cell.classList.contains('available'));
          }

          function showReserve(row, start, end) {
            const dates = headerDates();
            const s = dates.indexOf(start), e = dates.indexOf(end);
            if (s < 0 || e < 0 || e <= s) return;
            const button = row.querySelector('.selection-action');
            if (!button) return;
            button.style.gridColumn = (s + 2) + ' / ' + (e + 2);
            button.style.gridRow = '1';
            button.hidden = false;
          }

          if (a && d && (!editHold || !editHold.value)) {
            document.querySelectorAll('#calendar-scroll .element-row').forEach(row => {
              if (!row.classList.contains('party-unsuitable') && wholeRangeAvailable(row, a, d)) {
                showReserve(row, a, d);
              }
            });
          }

          const add = document.getElementById('requirements-add');
          if (add) {
            add.addEventListener('click', () => {
              const url = new URL(window.location.href);
              url.searchParams.delete('edit_hold');
              url.hash = 'calendar-scroll';
              window.location.href = url.toString();
            });
          }
        })();
        </script>'''
        if 'id="calendar-action-fixes"' not in text:
            text = text.replace('</body>', script + '</body>', 1)

        return Response(
            content=text,
            status_code=response.status_code,
            headers=headers,
            media_type='text/html',
        )
