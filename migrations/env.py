from __future__ import annotations
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config,pool
from online.config import load_runtime_config
from online.db_schema import metadata
config=context.config
if config.config_file_name is not None:fileConfig(config.config_file_name)
configured=config.get_main_option('sqlalchemy.url').strip();settings=load_runtime_config();database_url=configured if configured and configured!='driver://user:pass@localhost/dbname' else settings.database_url;config.set_main_option('sqlalchemy.url',database_url.replace('%','%%'));target_metadata=metadata
def run_migrations_offline():
    context.configure(url=database_url,target_metadata=target_metadata,literal_binds=True,dialect_opts={'paramstyle':'named'},compare_type=True)
    with context.begin_transaction():context.run_migrations()
def run_migrations_online():
    connectable=engine_from_config(config.get_section(config.config_ini_section,{}),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
if context.is_offline_mode():run_migrations_offline()
else:run_migrations_online()
