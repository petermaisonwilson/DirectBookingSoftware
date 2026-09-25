from __future__ import annotations

import json
from datetime import date, timedelta

from .setup015_calculator import _element_rate_for_date, _price_element
from .setup015_core import one, rows
from .webv1_duration_discounts import duration_discount
from .webv1_status_availability import availability_state


def _dates(a: str, d: str):
    start=date.fromisoformat(a); end=date.fromisoformat(d)
    if end<=start: raise ValueError('Departure date must be after arrival date.')
    return start,end


def amendment_quote(database, company_id: int, booking_id: int, booking_element_id: int,
                    new_arrival: str, new_departure: str, manual_discount: float=0.0) -> dict:
    be=one(database,'''SELECT be.*,se.name AS element_name,se.pricing_method
        FROM booking_elements be JOIN setup_elements se ON se.id=be.element_id AND se.company_id=be.company_id
        WHERE be.company_id=? AND be.booking_id=? AND be.id=?''',(company_id,booking_id,booking_element_id))
    booking=one(database,'SELECT * FROM bookings WHERE company_id=? AND id=?',(company_id,booking_id))
    if be is None or booking is None: raise ValueError('Booking element does not exist.')
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
        old_package+=sum(float(r['total_amount'] or 0) for r in rows(database,'SELECT total_amount FROM booking_people WHERE company_id=? AND booking_element_id=?',(company_id,booking_element_id)))
        old_package+=sum(float(r['total_amount'] or 0) for r in rows(database,'SELECT total_amount FROM booking_addons WHERE company_id=? AND booking_element_id=?',(company_id,booking_element_id)))
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
    for r in rows(database,'SELECT quantity,total_amount FROM booking_people WHERE company_id=? AND booking_element_id=?',(company_id,booking_element_id)):
        recurring_added+=float(r['total_amount'] or 0)/old_nights*len(added_dates) if old_nights else 0
    for r in rows(database,'SELECT total_amount,rule_snapshot_json FROM booking_addons WHERE company_id=? AND booking_element_id=?',(company_id,booking_element_id)):
        try: rule=json.loads(r['rule_snapshot_json'] or '{}')
        except (TypeError,json.JSONDecodeError): rule={}
        method=str(rule.get('pricing_method') or rule.get('method') or '')
        if method!='Fixed once' and old_nights:
            recurring_added+=float(r['total_amount'] or 0)/old_nights*len(added_dates)

    mixed_base=round(retained_historic+added_element+recurring_added,2)
    discount=duration_discount(database,company_id,int(be['element_id']),new_nights,mixed_base)
    manual=max(0.0,min(float(manual_discount or 0),float(discount['final_amount'])))
    element_old_final=float(snap.get('total') or (old_package-old_discount))
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
        be=c.execute('SELECT * FROM booking_elements WHERE company_id=? AND booking_id=? AND id=?',(cid,booking_id,beid)).fetchone()
        booking=c.execute('SELECT * FROM bookings WHERE company_id=? AND id=?',(cid,booking_id)).fetchone()
        if be is None or booking is None: raise ValueError('Booking element does not exist.')
        # Re-check availability inside the apply operation so a stale preview cannot overwrite a later booking.
        state=availability_state(database,cid,int(be['element_id']),quote['new_arrival'],quote['new_departure'],exclude_booking_id=booking_id)
        if not state['available']: raise ValueError('The amended dates are no longer available.')
        amendment_id=int(c.execute('''INSERT INTO booking_amendments
            (company_id,booking_id,booking_element_id,old_arrival_date,old_departure_date,new_arrival_date,new_departure_date,
             old_booking_total,new_booking_total,manual_discount,calculation_json,created_by_user_id,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (cid,booking_id,beid,quote['old_arrival'],quote['old_departure'],quote['new_arrival'],quote['new_departure'],
             quote['old_booking_total'],quote['new_booking_total'],quote['manual_discount'],calculation,context.get('user_id'),now)).lastrowid)
        c.execute('UPDATE booking_elements SET arrival_date=?,departure_date=? WHERE id=? AND company_id=?',
                  (quote['new_arrival'],quote['new_departure'],beid,cid))
        bounds=c.execute('SELECT MIN(arrival_date) AS a,MAX(departure_date) AS d FROM booking_elements WHERE booking_id=? AND company_id=?',(booking_id,cid)).fetchone()
        c.execute('UPDATE bookings SET arrival_date=?,departure_date=?,total_amount=?,updated_at=? WHERE id=? AND company_id=?',
                  (bounds['a'],bounds['d'],quote['new_booking_total'],now,booking_id,cid))
    return amendment_id
