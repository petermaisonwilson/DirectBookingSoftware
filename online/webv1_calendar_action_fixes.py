from __future__ import annotations

from fastapi.responses import Response


def install_calendar_action_fixes(app) -> None:
    """Restore visible Availability actions and make ADD start another Element selection."""

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

          function selectedAvailableCells(row) {
            return [...row.querySelectorAll('.cal-cell.selected-date, .cal-cell.selected-start')]
              .filter(cell => cell.classList.contains('available'));
          }

          function allSelectedCells(row) {
            return [...row.querySelectorAll('.cal-cell.selected-date, .cal-cell.selected-start')];
          }

          function showReserveFromYellowRange(row) {
            if (row.classList.contains('party-unsuitable')) return;
            const selected = allSelectedCells(row);
            if (!selected.length) return;
            if (!selected.every(cell => cell.classList.contains('available'))) return;
            const dates = [...document.querySelectorAll('#calendar-scroll .cal-date[data-date]')]
              .map(x => x.dataset.date);
            const selectedDates = selected.map(x => x.dataset.date).filter(Boolean).sort();
            if (!selectedDates.length) return;
            const first = dates.indexOf(selectedDates[0]);
            const last = dates.indexOf(selectedDates[selectedDates.length - 1]);
            if (first < 0 || last < first) return;
            const button = row.querySelector('.selection-action');
            if (!button) return;
            button.style.gridColumn = (first + 2) + ' / ' + (last + 3);
            button.style.gridRow = '1';
            button.hidden = false;
          }

          if (!editHold || !editHold.value) {
            document.querySelectorAll('#calendar-scroll .element-row').forEach(showReserveFromYellowRange);
          }

          const add = document.getElementById('requirements-add');
          if (add) {
            add.addEventListener('click', () => {
              const q = new URLSearchParams();
              if (arrival && arrival.value) q.set('arrival', arrival.value);
              if (departure && departure.value) q.set('departure', departure.value);
              q.set('add_element', '1');
              window.location.href = '/availability/calendar-v2?' + q.toString();
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
