from __future__ import annotations
from .setup015_core import one, rows

def initialise_addon_person(database)->None:
    with database.connect() as c:c.execute("INSERT INTO setup_addon_person_pricing(company_id,addon_id,pricing_mode) SELECT company_id,id,'single' FROM setup_addons ON CONFLICT DO NOTHING")
def addon_person_mode(database,company_id:int,addon_id:int)->str:
    row=one(database,'SELECT pricing_mode FROM setup_addon_person_pricing WHERE company_id=? AND addon_id=?',(company_id,addon_id));return str(row['pricing_mode']) if row else 'single'
def addon_person_rates(database,company_id:int,addon_id:int,year:int)->dict[int,float]:
    return {int(row['person_type_id']):float(row['rate']) for row in rows(database,'SELECT person_type_id,rate FROM setup_addon_person_rates WHERE company_id=? AND addon_id=? AND year=?',(company_id,addon_id,year))}
def addon_person_payload(database,company_id:int,addons,years)->tuple[dict[str,str],dict[str,dict[str,dict[str,float]]]]:
    modes={};rates={}
    for addon in addons:
        aid=int(addon['id']);akey=str(aid);modes[akey]=addon_person_mode(database,company_id,aid);rates[akey]={}
        for year_row in years:
            year=int(year_row['year']);rates[akey][str(year)]={str(pid):rate for pid,rate in addon_person_rates(database,company_id,aid,year).items()}
    return modes,rates
