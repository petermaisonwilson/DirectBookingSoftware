"""Stable, tenant-scoped operational URLs for the reservation lifecycle.

Existing command controllers retain their established validation and forms.
Compatibility URLs resolve the same domain identity; they do not create another
reservation when the enquiry becomes confirmed.
"""
import inspect

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from .setup015_core import context_for, working_company
from .sql import execute_sql
from .webv1_operational_reservations import identity


def operational_path(database, company_id, *, enquiry_id=None, booking_id=None):
    with database.connect() as connection:
        master = identity(connection, company_id, enquiry_id=enquiry_id, booking_id=booking_id)
    if master is not None:
        return f'/operations/reservations/{int(master["id"])}'
    # Read-only compatibility for an old source record awaiting additive adoption.
    return f'/operations/enquiries/{int(enquiry_id)}' if enquiry_id is not None else f'/operations/bookings/{int(booking_id)}'


def booking_path(database, company_id, booking_id):
    return operational_path(database, company_id, booking_id=booking_id)


def enquiry_path(database, company_id, enquiry_id):
    return operational_path(database, company_id, enquiry_id=enquiry_id)


def register_reservation_routes(app):
    database = app.state.database
    endpoints = {}
    for route in app.routes:
        for method in getattr(route, 'methods', ()):
            # Match the first controller, as normal router dispatch does.
            endpoints.setdefault((route.path, method), route.endpoint)

    def master_for(request, reservation_id):
        context = context_for(database, request)
        company_id = int(working_company(context))
        with database.connect() as connection:
            row = execute_sql(connection, 'SELECT * FROM reservations WHERE company_id=:tenant AND id=:reservation',
                              (company_id, reservation_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail='Reservation not found')
        request.state.reservation_id = int(row['id'])
        request.state.reservation_booking_id = row['booking_id']
        request.state.reservation_enquiry_id = row['enquiry_id']
        return dict(row)

    async def invoke(endpoint, request, **arguments):
        for name, parameter in inspect.signature(endpoint).parameters.items():
            if name not in arguments and name != 'request' and name in request.query_params:
                raw = request.query_params[name]
                if parameter.annotation in (int, 'int'):
                    try:
                        raw = int(raw)
                    except ValueError:
                        raise HTTPException(status_code=422, detail=f'Invalid {name}')
                arguments[name] = raw
        arguments['request'] = request
        if inspect.iscoroutinefunction(endpoint):
            return await endpoint(**arguments)
        return await run_in_threadpool(endpoint, **arguments)

    @app.get('/operations/reservations/{reservation_id:int}', response_class=HTMLResponse)
    async def detail(reservation_id: int, request: Request):
        master = master_for(request, reservation_id)
        if master['booking_id'] is not None:
            return await invoke(endpoints[('/operations/bookings/{booking_id}', 'GET')], request, booking_id=master['booking_id'])
        if master['enquiry_id'] is not None:
            return await invoke(endpoints[('/operations/enquiries/{enquiry_id}', 'GET')], request, enquiry_id=master['enquiry_id'])
        return await invoke(endpoints[('/operations/master-reservations/{reservation_id:int}', 'GET')], request, reservation_id=reservation_id)

    def register_command(kind, suffix, method):
        source = 'booking_id' if kind == 'bookings' else 'enquiry_id'
        endpoint = endpoints[(f'/operations/{kind}/{{{source}}}/{suffix}', method)]
        async def command(reservation_id: int, request: Request):
            master = master_for(request, reservation_id)
            if master[source] is None:
                raise HTTPException(status_code=409, detail='This action is not available in the current reservation state')
            return await invoke(endpoint, request, **{source: master[source]})
        command.__name__ = 'reservation_' + suffix.replace('-', '_') + '_' + method.lower()
        app.add_api_route('/operations/reservations/{reservation_id:int}/' + suffix, command, methods=[method], response_class=HTMLResponse)

    for suffix in ('payments', 'refunds', 'cancel', 'amend-preview', 'amend-apply'):
        register_command('bookings', suffix, 'POST')
    register_command('bookings', 'cancel', 'GET')
    register_command('bookings', 'customer-matches', 'GET')
    register_command('bookings', 'link-customer', 'POST')
    for suffix in ('convert', 'confirm-payment', 'confirm-without-payment', 'release', 'reopen'):
        register_command('enquiries', suffix, 'POST')
    for suffix in ('confirm', 'build'):
        register_command('enquiries', suffix, 'GET')
    @app.get('/operations/reservations/{reservation_id:int}/edit', response_class=HTMLResponse)
    async def edit_elements(reservation_id: int, request: Request):
        from .webv1_reservation_editor import allocations, editable, page
        master = master_for(request, reservation_id)
        context = context_for(database, request)
        try:
            editable(master)
            with database.connect() as c:
                items = allocations(c, master)
            adding = request.query_params.get('add') == '1'
            selected_id = int(request.query_params.get('allocation_id') or 0)
            selected = next((r for r in items if r['id'] == selected_id), None)
            if selected_id and selected is None:
                raise HTTPException(status_code=404, detail='Reservation Element not found')
            if len(items) == 1 and not adding and not selected_id:
                selected = items[0]
            return HTMLResponse(page(database,context,master,items,selected=selected,adding=adding))
        except ValueError as exc:
            return HTMLResponse(str(exc),409)

    @app.post('/operations/reservations/{reservation_id:int}/edit', response_class=HTMLResponse)
    async def save_elements(reservation_id: int, request: Request):
        from .webv1_reservation_editor import allocations, editable, page, calculate, save
        from .app import form_data
        from .setup015_core import require_csrf
        from fastapi.responses import RedirectResponse
        master = master_for(request, reservation_id)
        context = context_for(database, request)
        data = dict(await form_data(request))
        require_csrf(context, data)
        items = []
        try:
            editable(master)
            with database.connect() as c:
                items = allocations(c, master)
            selected_id = int(data.get('allocation_id') or 0)
            selected = next((r for r in items if r['id'] == selected_id), None)
            if selected_id and selected is None:
                raise ValueError('Element does not belong to this reservation.')
            if data.get('action') == 'calculate':
                result = calculate(database,master['company_id'],data)
                return HTMLResponse(page(database,context,master,items,selected=selected,adding=not selected,values=data,result=result))
            await run_in_threadpool(save,database,context,master,data)
            return RedirectResponse(f'/operations/reservations/{reservation_id}?saved=1',303)
        except (ValueError, TypeError) as exc:
            return HTMLResponse(page(database,context,master,items,message=str(exc)),409)


    @app.post('/operations/reservations/{reservation_id:int}/elements/{allocation_id:int}/replace', response_class=HTMLResponse)
    async def replace_element(reservation_id: int, allocation_id: int, request: Request):
        master = master_for(request, reservation_id)
        if master['enquiry_id'] is None:
            raise HTTPException(status_code=409, detail='This reservation has no enquiry requirements to replace')
        with database.connect() as connection:
            allocation = execute_sql(connection,
                'SELECT enquiry_element_id FROM reservation_elements WHERE company_id=:tenant AND reservation_id=:reservation AND id=:allocation',
                (master['company_id'], reservation_id, allocation_id)).fetchone()
        if allocation is None or allocation['enquiry_element_id'] is None:
            raise HTTPException(status_code=404, detail='Reservation element not found')
        endpoint = endpoints[('/operations/enquiries/{enquiry_id}/elements/{enquiry_element_id}/replace', 'POST')]
        return await invoke(endpoint, request, enquiry_id=master['enquiry_id'], enquiry_element_id=allocation['enquiry_element_id'])
