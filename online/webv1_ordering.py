from __future__ import annotations

import html

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .app import COOKIE_NAME, form_data
from .setup015_core import working_company


ORDERING_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_menu_order (
    user_id INTEGER NOT NULL,
    page_key TEXT NOT NULL,
    item_key TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY(user_id,page_key,item_key)
);
CREATE TABLE IF NOT EXISTS setup_item_order (
    company_id INTEGER NOT NULL,
    list_key TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY(company_id,list_key,item_id)
);
"""


def initialise_ordering(database) -> None:
    with database.connect() as c:
        c.executescript(ORDERING_SCHEMA)


def order_menu_items(database, user_id: int, page_key: str, items: list[tuple]) -> list[tuple]:
    with database.connect() as c:
        saved = {
            str(r['item_key']): int(r['position'])
            for r in c.execute(
                'SELECT item_key,position FROM user_menu_order WHERE user_id=? AND page_key=?',
                (int(user_id), page_key),
            ).fetchall()
        }
    original = {str(item[0]): i for i, item in enumerate(items)}
    return sorted(items, key=lambda item: (0, saved[str(item[0])]) if str(item[0]) in saved else (1, original[str(item[0])]))


def setup_rows(database, company_id: int, list_key: str, records) -> list:
    values = list(records)
    with database.connect() as c:
        saved = {
            int(r['item_id']): int(r['position'])
            for r in c.execute(
                'SELECT item_id,position FROM setup_item_order WHERE company_id=? AND list_key=?',
                (int(company_id), list_key),
            ).fetchall()
        }
    original = {int(row['id']): i for i, row in enumerate(values)}
    return sorted(values, key=lambda row: (0, saved[int(row['id'])]) if int(row['id']) in saved else (1, original[int(row['id'])]))


def person_type_rows(database, company_id: int, *, active_only: bool = False) -> list:
    sql = 'SELECT * FROM setup_person_types WHERE company_id=?'
    if active_only:
        sql += ' AND active=1'
    sql += ' ORDER BY name COLLATE NOCASE'
    with database.connect() as c:
        records = c.execute(sql, (int(company_id),)).fetchall()
    return setup_rows(database, company_id, 'person_types', records)


def sortable_menu_html(database, context, page_key: str, items: list[tuple[str, str]]) -> str:
    """Return a draggable card grid whose order is personal to this login."""
    ordered = order_menu_items(database, int(context['user_id']), page_key, items)
    cards = ''.join(
        f'<div class="card sortable-card" data-sort-key="{html.escape(str(key), quote=True)}">'
        f'<div class="sort-handle" draggable="true" title="Drag to reorder">☰ Drag</div>{inner}</div>'
        for key, inner in ordered
    )
    csrf = html.escape(str(context['csrf_token']), quote=True)
    page = html.escape(page_key, quote=True)
    return f'''<div class="sortable-help"><span class="muted">Drag the boxes into your preferred order. This layout is saved for your login.</span> <button type="button" class="secondary sort-reset" data-page="{page}">Reset layout</button></div>
    <div class="grid sortable-grid" data-page="{page}">{cards}</div>
    <style>
      .sortable-help{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 12px;flex-wrap:wrap}}
      .sortable-card{{position:relative;cursor:default}}
      .sortable-card.dragging{{opacity:.45}}
      .sort-handle{{font-size:12px;color:#66717f;cursor:grab;user-select:none;text-align:right;margin:-8px -4px 8px 0}}
      .sort-handle:active{{cursor:grabbing}}
    </style>
    <script>
    (()=>{{
      const grid=document.querySelector('.sortable-grid[data-page="{page}"]'); if(!grid)return;
      let dragged=null;
      const save=()=>fetch('/ui/menu-order',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:new URLSearchParams({{csrf:'{csrf}',page_key:'{page}',order:[...grid.querySelectorAll('[data-sort-key]')].map(x=>x.dataset.sortKey).join(',')}})}});
      grid.querySelectorAll('.sort-handle').forEach(handle=>{{
        handle.addEventListener('dragstart',e=>{{dragged=handle.closest('.sortable-card');if(!dragged)return;dragged.classList.add('dragging');e.dataTransfer.effectAllowed='move';}});
        handle.addEventListener('dragend',()=>{{if(dragged)dragged.classList.remove('dragging');dragged=null;save();}});
      }});
      grid.addEventListener('dragover',e=>{{
        e.preventDefault();if(!dragged)return;
        const card=e.target.closest('.sortable-card');if(!card||card===dragged)return;
        const r=card.getBoundingClientRect();
        const before=e.clientY<r.top+r.height/2;
        grid.insertBefore(dragged,before?card:card.nextSibling);
      }});
      const reset=document.querySelector('.sort-reset[data-page="{page}"]'); if(reset)reset.addEventListener('click',async()=>{{await fetch('/ui/menu-order/reset',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:new URLSearchParams({{csrf:'{csrf}',page_key:'{page}'}})}});location.reload();}});
    }})();
    </script>'''


def setup_sortable_table_bits(context, list_key: str) -> tuple[str, str]:
    """Return the Order heading and drag script for a canonical Setup list."""
    csrf = html.escape(str(context['csrf_token']), quote=True)
    key = html.escape(list_key, quote=True)
    heading = '<th style="width:70px">Order</th>'
    script = f'''<style>
      #setup-sort-{key} tr.dragging{{opacity:.45}}
      #setup-sort-{key} .row-sort-handle{{cursor:grab;user-select:none;color:#66717f;font-size:12px;white-space:nowrap}}
    </style><script>
    (()=>{{
      const body=document.getElementById('setup-sort-{key}');if(!body)return;let dragged=null;
      const save=()=>fetch('/setup/item-order',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:new URLSearchParams({{csrf:'{csrf}',list_key:'{key}',order:[...body.querySelectorAll('tr[data-item-id]')].map(x=>x.dataset.itemId).join(',')}})}});
      body.querySelectorAll('.row-sort-handle').forEach(handle=>{{
        handle.setAttribute('draggable','true');
        handle.addEventListener('dragstart',e=>{{dragged=handle.closest('tr[data-item-id]');if(!dragged)return;dragged.classList.add('dragging');e.dataTransfer.effectAllowed='move';}});
        handle.addEventListener('dragend',()=>{{if(dragged)dragged.classList.remove('dragging');dragged=null;save();}});
      }});
      body.addEventListener('dragover',e=>{{e.preventDefault();if(!dragged)return;const row=e.target.closest('tr[data-item-id]');if(!row||row===dragged)return;const r=row.getBoundingClientRect();body.insertBefore(dragged,e.clientY<r.top+r.height/2?row:row.nextSibling);}});
    }})();
    </script>'''
    return heading, script


def register_ordering_routes(app) -> None:
    database = app.state.database

    def context(request: Request):
        ctx = database.session_context(request.cookies.get(COOKIE_NAME))
        if ctx is None:
            raise HTTPException(status_code=401, detail='Login required')
        if str(ctx['role']) not in {'supervisor', 'operator'}:
            raise HTTPException(status_code=403, detail='Not permitted')
        return ctx

    @app.post('/ui/menu-order')
    async def menu_order(request: Request):
        ctx = context(request); data = await form_data(request)
        if data.get('csrf') != ctx['csrf_token']:
            raise HTTPException(status_code=403, detail='Invalid form token')
        page_key = str(data.get('page_key','')).strip()
        keys = [x.strip() for x in str(data.get('order','')).split(',') if x.strip()]
        if not page_key or not keys or len(keys) != len(set(keys)):
            raise HTTPException(status_code=400, detail='Invalid menu order')
        with database.connect() as c:
            c.execute('DELETE FROM user_menu_order WHERE user_id=? AND page_key=?', (int(ctx['user_id']), page_key))
            for position, item_key in enumerate(keys):
                c.execute('INSERT INTO user_menu_order(user_id,page_key,item_key,position) VALUES (?,?,?,?)',
                          (int(ctx['user_id']), page_key, item_key, position))
        return JSONResponse({'ok': True})

    @app.post('/ui/menu-order/reset')
    async def menu_order_reset(request: Request):
        ctx = context(request); data = await form_data(request)
        if data.get('csrf') != ctx['csrf_token']:
            raise HTTPException(status_code=403, detail='Invalid form token')
        page_key = str(data.get('page_key','')).strip()
        with database.connect() as c:
            c.execute('DELETE FROM user_menu_order WHERE user_id=? AND page_key=?', (int(ctx['user_id']), page_key))
        return JSONResponse({'ok': True})

    @app.post('/setup/item-order')
    async def setup_item_order(request: Request):
        ctx = context(request); data = await form_data(request)
        if data.get('csrf') != ctx['csrf_token']:
            raise HTTPException(status_code=403, detail='Invalid form token')
        company_id = working_company(ctx)
        if not company_id:
            raise HTTPException(status_code=403, detail='Select a Client first')
        list_key = str(data.get('list_key','')).strip()
        ids = [int(x) for x in str(data.get('order','')).split(',') if x.strip().isdigit()]
        if list_key != 'person_types' or not ids or len(ids) != len(set(ids)):
            raise HTTPException(status_code=400, detail='Invalid Setup order')
        with database.connect() as c:
            valid = {int(r['id']) for r in c.execute('SELECT id FROM setup_person_types WHERE company_id=?', (int(company_id),)).fetchall()}
            if any(item_id not in valid for item_id in ids):
                raise HTTPException(status_code=400, detail='Invalid Setup item')
            c.execute('DELETE FROM setup_item_order WHERE company_id=? AND list_key=?', (int(company_id), list_key))
            for position, item_id in enumerate(ids):
                c.execute('INSERT INTO setup_item_order(company_id,list_key,item_id,position) VALUES (?,?,?,?)',
                          (int(company_id), list_key, item_id, position))
        database.write_audit(action='SETUP_ORDER_CHANGED', entity_type=list_key, entity_id=None,
                             actor_user_id=int(ctx['user_id']), actor_role=str(ctx['role']),
                             company_id=int(company_id), acting_company_id=ctx['acting_company_id'],
                             after={'order': ids})
        return JSONResponse({'ok': True})
