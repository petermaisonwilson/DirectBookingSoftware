from __future__ import annotations
import os
from sqlalchemy import delete,insert,select
from sqlalchemy.exc import IntegrityError
from online.config import load_runtime_config
from online.db_engine import create_database_engine
from online.db_schema import audit_log,companies,metadata,sessions,users
from online.security import hash_password

def main():
 config=load_runtime_config();assert config.database_url.startswith('postgresql+')
 engine=create_database_engine(config);metadata.drop_all(engine);metadata.create_all(engine)
 with engine.begin() as c:
  cid=c.execute(insert(companies).values(name='AWS Test Site',contact_email='test@example.com',phone='',active=1,created_at='2026-08-31T00:00:00+00:00').returning(companies.c.id)).scalar_one()
  uid=c.execute(insert(users).values(company_id=cid,role='operator',first_name='AWS',last_name='Tester',email='aws-test@example.com',password_hash=hash_password('temporary-test-password'),active=1,created_at='2026-08-31T00:00:00+00:00').returning(users.c.id)).scalar_one()
  c.execute(insert(sessions).values(token='aws-test-token',user_id=uid,acting_company_id=None,csrf_token='aws-test-csrf',created_at='2026-08-31T00:00:00+00:00',expires_at='2026-09-01T00:00:00+00:00'))
  c.execute(insert(audit_log).values(company_id=cid,actor_user_id=uid,actor_role='operator',acting_company_id=None,action='AWS_PORTABILITY_TEST',entity_type='company',entity_id=str(cid),before_json=None,after_json='{"ok": true}',created_at='2026-08-31T00:00:00+00:00'))
  row=c.execute(select(companies.c.name,users.c.email).join(users,users.c.company_id==companies.c.id).where(companies.c.id==cid)).mappings().one();assert row['name']=='AWS Test Site' and row['email']=='aws-test@example.com'
 try:
  with engine.begin() as c:c.execute(insert(companies).values(name='aws test site',contact_email='duplicate@example.com',phone='',active=1,created_at='2026-08-31T00:00:00+00:00'))
 except IntegrityError:pass
 else:raise AssertionError('Company names must be unique case-insensitively')
 metadata.drop_all(engine);engine.dispose();print('AWS PostgreSQL portability regression: passed')
if __name__=='__main__':main()
