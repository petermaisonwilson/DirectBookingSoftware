from __future__ import annotations
from .sql import lock_inventory, execute_sql, insert_sql

import json
from datetime import date, timedelta

from .setup015_calculator import _element_rate_for_date, _price_element
from .setup015_core import one, rows
from .webv1_duration_discounts import duration_discount
from .webv1_status_availability import availability_state
from .webv1_booking_finance import sync_payment_status


def _assert_amendable_booking(connection, company_id: int, booking_id: int) -> None:
    """Cancellation is terminal for accommodation amendments, not financial records."""
    booking = execute_sql(connection,
        'SELECT b.id, s.internal_state, s.blocks_availability\n           FROM bookings b LEFT JOIN booking_status_definitions s\n             ON s.id=b.workflow_status_id AND s.company_id=b.company_id\n           WHERE b.company_id=:q528_0 AND b.id=:q528_1',
        (company_id, booking_id),
    ).fetchone()
    if booking is None:
        raise ValueError('Booking does not exist.')
    if str(booking['internal_state'] or '') == 'RELEASED':
        raise ValueError('This cancelled or released Booking cannot be amended. Its financial and communication records remain available.')


def _dates(a: str, d: str):
    start=date.fromisoformat(a); end=date.fromisoformat(d)
    if end<=start: raise ValueError('Departure date must be after arrival date.')
    return start,end


def amendment_quote(database, company_id: int, booking_id: int, booking_element_id: int,
                    new_arrival: str, new_departure: str, manual_discount: float | None=None) -> dict:
    be=one(database,'SELECT be.*,se.name AS element_name,se.pricing_method\n        FROM booking_elements be JOIN setup_elements se ON se.id=be.element_id AND se.company_id=be.company_id\n        WHERE be.company_id=:q520_0 AND be.booking_id=:q520_1 AND be.id=:q520_2',(company_id,booking_id,booking_element_id))
    booking=one(database,'SELECT * FROM bookings WHERE company_id=:q521_0 AND id=:q521_1',(company_id,booking_id))
    if be is None or booking is None: raise ValueError('Booking element does not exist.')
    with database.connect() as guard:
        _assert_amendable_booking(guard, company_id, booking_id)
    old_start,old_end=_dates(str(be['arrival_date']),str(be['departure_date']))
    new_start,new_end=_dates(new_arrival,new_departure)
    if new_start.year!=(new_end-timedelta(days=1)).year:
        raise ValueError('The amended stay must remain within one pricing year.')
    state=availability_state(database,company_id,int(be['element_id']),new_arrival,new_departure,exclude_booking_id=booking_id)
    if not state['available']: raise ValueError('The amended dates are not available.')

    try: snap=json.loads(be['pricing_snapshot_json'] or '{}')
    except (TypeError,json.JSONDecodeError): snap={}
    old_nights=(old_end-old_start).days; new_nights=(new_end-new_start).days
    old_package=float(snap.get('base_amount') or snap.get('total') or 0)
    old_discount=float(snap.get('discount_amount') or 0)
    if old_package<=0:
        old_package=float(be['total_amount'] or 0)
        old_package+=sum(float(r['total_amount'] or 0) for r in rows(database,'SELECT total_amount FROM booking_people WHERE company_id=:q534_0 AND booking_element_id=:q534_1',(company_id,booking_element_id)))
        old_package+=sum(float(r['total_amount'] or 0) for r in rows(database,'SELECT total_amount FROM booking_addons WHERE company_id=:q535_0 AND booking_element_id=:q535_1',(company_id,booking_element_id)))
        old_package+=old_discount

    overlap_start=max(old_start,new_start); overlap_end=min(old_end,new_end)
    retained_nights=max(0,(overlap_end-overlap_start).days)
    # Historical snapshots before per-night storage can only be allocated proportionally.
    retained_historic=round(old_package*retained_nights/old_nights,2) if old_nights else 0.0
    added_dates=[new_start+timedelta(days=i) for i in range(new_nights)
                 if not (old_start <= new_start+timedelta(days=i) < old_end)]
    added_element=0.0
    for day in added_dates:
        season,rate=_element_rate_for_date(database,company_id,int(be['element_id']),day)
        if season is None or rate is None: raise ValueError(f'Pricing Setup is incomplete for {day.isoformat()}.')
        added_element+=_price_element(str(be['pricing_method']),[float(rate)],1)

    # Existing people/add-ons are historical package components. For added nights,
    # recurring frozen components extend at their frozen per-night average; fixed-once
    # components are not charged again.
    recurring_added=0.0
    for r in rows(database,'SELECT quantity,total_amount FROM booking_people WHERE company_id=:q522_0 AND booking_element_id=:q522_1',(company_id,booking_element_id)):
        recurring_added+=float(r['total_amount'] or 0)/old_nights*len(added_dates) if old_nights else 0
    for r in rows(database,'SELECT total_amount,rule_snapshot_json FROM booking_addons WHERE company_id=:q523_0 AND booking_element_id=:q523_1',(company_id,booking_element_id)):
        try: rule=json.loads(r['rule_snapshot_json'] or '{}')
        except (TypeError,json.JSONDecodeError): rule={}
        method=str(rule.get('pricing_method') or rule.get('method') or '')
        if method!='Fixed once' and old_nights:
            recurring_added+=float(r['total_amount'] or 0)/old_nights*len(added_dates)

    mixed_base=round(retained_historic+added_element+recurring_added,2)
    discount=duration_discount(database,company_id,int(be['element_id']),new_nights,mixed_base)
    prior=one(database,'SELECT calculation_json,manual_discount FROM booking_amendments WHERE company_id=:q524_0 AND booking_id=:q524_1 AND booking_element_id=:q524_2 ORDER BY id DESC LIMIT 1',(company_id,booking_id,booking_element_id))
    prior_manual=float(prior['manual_discount'] or 0) if prior is not None else 0.0
    requested_manual=prior_manual if manual_discount is None else float(manual_discount or 0)
    manual=max(0.0,min(requested_manual,float(discount['final_amount'])))
    element_old_final=float(snap.get('total') or (old_package-old_discount))
    if prior is not None:
        try: prior_calc=json.loads(prior['calculation_json'] or '{}')
        except (TypeError,json.JSONDecodeError): prior_calc={}
        element_old_final=float(prior_calc.get('new_element_total') or element_old_final)
    other_total=float(booking['total_amount'])-element_old_final
    new_element_total=round(float(discount['final_amount'])-manual,2)
    new_booking_total=round(other_total+new_element_total,2)
    return {
        'booking_id':booking_id,'booking_element_id':booking_element_id,'element_id':int(be['element_id']),
        'old_arrival':str(be['arrival_date']),'old_departure':str(be['departure_date']),
        'new_arrival':new_arrival,'new_departure':new_departure,'old_nights':old_nights,'new_nights':new_nights,
        'retained_nights':retained_nights,'added_nights':len(added_dates),'retained_historic_base':retained_historic,
        'added_current_element':round(added_element,2),'added_recurring':round(recurring_added,2),
        'base_amount':mixed_base,'duration_discount':float(discount['discount_amount']),'duration_rule':discount['rule'],
        'manual_discount':round(manual,2),'new_element_total':new_element_total,
        'old_booking_total':round(float(booking['total_amount']),2),'new_booking_total':new_booking_total,
    }


def apply_amendment(database, context, quote: dict) -> int:
    """Persist one already-validated amendment as a single database transaction."""
    cid=int(context['company_id']); booking_id=int(quote['booking_id']); beid=int(quote['booking_element_id'])
    now=__import__('online.database',fromlist=['iso_now']).iso_now()
    calculation=json.dumps(quote,separators=(',',':'),sort_keys=True)
    with database.connect() as c:
        be=execute_sql(c, 'SELECT * FROM booking_elements WHERE company_id=:q529_0 AND booking_id=:q529_1 AND id=:q529_2',(cid,booking_id,beid)).fetchone()
        booking=execute_sql(c, 'SELECT * FROM bookings WHERE company_id=:q530_0 AND id=:q530_1',(cid,booking_id)).fetchone()
        if be is None or booking is None: raise ValueError('Booking element does not exist.')
        # Lock before checking competing allocations: a quote is not a reservation.
        # The checks and subsequent update must be one serialised write operation.
        from .webv1_status_availability import _booking_conflict, _enquiry_conflict
        from .webv1_availability import _closure_conflict
        lock_inventory(c, cid)
        _assert_amendable_booking(c, cid, booking_id)
        current = execute_sql(c, 'SELECT arrival_date,departure_date FROM booking_elements WHERE id=:q531_0 AND company_id=:q531_1', (beid,cid)).fetchone()
        if current is None or str(current['arrival_date']) != str(quote['old_arrival']) or str(current['departure_date']) != str(quote['old_departure']):
            raise ValueError('This Booking has changed since the amendment was calculated. Recalculate before saving.')
        from .webv1_reservation_core import migrated_inventory_conflict
        if migrated_inventory_conflict(c, cid, int(be['element_id']), quote['new_arrival'], quote['new_departure']):
            raise ValueError('The amended dates overlap a central reservation.')
        if _booking_conflict(c,cid,int(be['element_id']),quote['new_arrival'],quote['new_departure'],exclude_booking_id=booking_id):
            raise ValueError('The amended dates overlap another Booking.')
        if _enquiry_conflict(c,cid,int(be['element_id']),quote['new_arrival'],quote['new_departure']):
            raise ValueError('The amended dates overlap an Enquiry holding this Element.')
        if _closure_conflict(c,cid,int(be['element_id']),quote['new_arrival'],quote['new_departure']):
            raise ValueError('The amended dates overlap an Element closure.')
        for other in execute_sql(c, 'SELECT arrival_date,departure_date FROM booking_elements WHERE company_id=:q525_0 AND booking_id=:q525_1 AND element_id=:q525_2 AND id<>:q525_3', (cid,booking_id,int(be['element_id']),beid)):
            if str(other['arrival_date']) < str(quote['new_departure']) and str(other['departure_date']) > str(quote['new_arrival']):
                raise ValueError('The amended dates overlap another Element allocation in this Booking.')
        amendment_id=int(insert_sql(c, 'INSERT INTO booking_amendments\n            (company_id,booking_id,booking_element_id,old_arrival_date,old_departure_date,new_arrival_date,new_departure_date,\n             old_booking_total,new_booking_total,manual_discount,calculation_json,created_by_user_id,created_at)\n            VALUES (:q532_0,:q532_1,:q532_2,:q532_3,:q532_4,:q532_5,:q532_6,:q532_7,:q532_8,:q532_9,:q532_10,:q532_11,:q532_12)',
            (cid,booking_id,beid,quote['old_arrival'],quote['old_departure'],quote['new_arrival'],quote['new_departure'],
             quote['old_booking_total'],quote['new_booking_total'],quote['manual_discount'],calculation,context['user_id'],now)).lastrowid)
        execute_sql(c, 'UPDATE booking_elements SET arrival_date=:q526_0,departure_date=:q526_1 WHERE id=:q526_2 AND company_id=:q526_3',
                  (quote['new_arrival'],quote['new_departure'],beid,cid))
        bounds=execute_sql(c, 'SELECT MIN(arrival_date) AS a,MAX(departure_date) AS d FROM booking_elements WHERE booking_id=:q533_0 AND company_id=:q533_1',(booking_id,cid)).fetchone()
        execute_sql(c, 'UPDATE bookings SET arrival_date=:q527_0,departure_date=:q527_1,total_amount=:q527_2,updated_at=:q527_3 WHERE id=:q527_4 AND company_id=:q527_5',
                  (bounds['a'],bounds['d'],quote['new_booking_total'],now,booking_id,cid))
        sync_payment_status(database,cid,booking_id,connection=c,context=context)
    return amendment_id
