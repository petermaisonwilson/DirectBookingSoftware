from __future__ import annotations
from datetime import date,timedelta

def _table_exists(connection,table):return connection.table_exists(table)
def _season_for_date(connection,company_id,year,day):
    found=connection.execute('SELECT * FROM setup_seasons WHERE company_id=? AND year=? AND start_date<=? AND end_date>=?',(company_id,year,day.isoformat(),day.isoformat())).fetchall()
    return min(found,key=lambda r:(date.fromisoformat(r['end_date'])-date.fromisoformat(r['start_date'])).days) if found else None
def _addon_is_person_type(connection,company_id,addon_id):
    if not _table_exists(connection,'setup_addon_person_pricing'):return False
    row=connection.execute('SELECT pricing_mode FROM setup_addon_person_pricing WHERE company_id=? AND addon_id=?',(company_id,addon_id)).fetchone();return bool(row and str(row['pricing_mode'])=='person_type')
def _allowed_addon_rule(connection,company_id,year,element,addon_id):
    override=connection.execute('SELECT * FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?',(company_id,year,int(element['id']),addon_id)).fetchone()
    if override:return override
    return connection.execute('SELECT * FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=? AND addon_id=?',(company_id,year,str(element['element_type']),addon_id)).fetchone()
def element_missing_items(database,company_id,year,element_id):
    missing=[]
    with database.connect() as c:
        element=c.execute('SELECT * FROM setup_elements WHERE company_id=? AND id=?',(company_id,element_id)).fetchone()
        if element is None:return [{'category':'Element','text':'Element not found','href':'/setup/elements'}]
        seasons=c.execute('SELECT * FROM setup_seasons WHERE company_id=? AND year=? ORDER BY start_date',(company_id,year)).fetchall()
        if not seasons:missing.append({'category':'Season','text':'No Season configured','href':f'/setup/pricing?year={year}'})
        for season in seasons:
            rate=c.execute('SELECT rate FROM setup_element_rates WHERE company_id=? AND year=? AND element_id=? AND season_id=?',(company_id,year,element_id,int(season['id']))).fetchone()
            if rate is None:missing.append({'category':'Price','text':f'Missing price for {season["name"]}','href':f'/setup/pricing?year={year}'})
        if c.execute('SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?',(company_id,year,element_id)).fetchone() is None:missing.append({'category':'Occupancy','text':'Maximum occupancy not set','href':f'/setup/occupancy?year={year}'})
    return missing
def incomplete_elements(database,company_id,year):
    result=[]
    with database.connect() as c:elements=c.execute('SELECT id,name FROM setup_elements WHERE company_id=? AND active=1 ORDER BY lower(name)',(company_id,)).fetchall()
    for e in elements:
        count=len(element_missing_items(database,company_id,year,int(e['id'])))
        if count:result.append({'id':int(e['id']),'name':str(e['name']),'count':count})
    return result
def element_available_setup_ready(database,company_id,element_id,start,end):
    year=start.year;missing=element_missing_items(database,company_id,year,element_id)
    if missing:return False,missing[0]['text']
    with database.connect() as c:
        day=start
        while day<end:
            season=_season_for_date(c,company_id,year,day)
            if season is None:return False,f'No Season covers {day.isoformat()}.'
            if c.execute('SELECT rate FROM setup_element_rates WHERE company_id=? AND year=? AND element_id=? AND season_id=?',(company_id,year,element_id,int(season['id']))).fetchone() is None:return False,f'No price is configured for {season["name"]}.'
            day+=timedelta(days=1)
    return True,''
