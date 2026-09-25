from __future__ import annotations

from .setup015_core import one, rows


def duration_discount(database, company_id: int, element_id: int, nights: int, base_amount: float) -> dict:
    amount=round(max(0.0,float(base_amount or 0)),2)
    if nights < 1:
        return {'base_amount':amount,'discount_amount':0.0,'final_amount':amount,'rule':None}
    element=one(database,'SELECT id,element_type,pricing_method FROM setup_elements WHERE company_id=? AND id=?',(company_id,element_id))
    if element is None:
        raise ValueError('Element does not exist.')
    candidates=rows(database,'''SELECT * FROM setup_duration_discounts
        WHERE company_id=? AND active=1 AND min_nights<=?
          AND (scope_type='All elements' OR (scope_type='Element Type' AND element_type=?)
               OR (scope_type='Element' AND element_id=?))
        ORDER BY min_nights DESC,id''',(company_id,nights,str(element['element_type']),element_id))
    best=None; best_amount=0.0
    for rule in candidates:
        value=float(rule['discount_value'] or 0)
        if str(rule['discount_type'])=='Percentage': discount=amount*value/100.0
        elif str(rule['discount_type'])=='Fixed amount': discount=value
        else:
            if str(element['pricing_method']) not in {'Per night','Per quantity per night','Per day','Per quantity per day'}: continue
            discount=amount*min(value,float(nights))/float(nights)
        discount=round(max(0.0,min(amount,discount)),2)
        if discount>best_amount:
            best_amount=discount; best=rule
    snapshot=None
    if best is not None:
        snapshot={k:best[k] for k in ('id','name','min_nights','discount_type','discount_value','scope_type','element_type','element_id')}
    return {'base_amount':amount,'discount_amount':best_amount,'final_amount':round(amount-best_amount,2),'rule':snapshot}
