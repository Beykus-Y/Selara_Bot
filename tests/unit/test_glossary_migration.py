import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_alias_migration_preserves_old_rows_and_can_be_downgraded():
    path = Path(__file__).resolve().parents[2] / 'alembic/versions/0063_glossary_aliases.py'
    spec = importlib.util.spec_from_file_location('glossary_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE chats (telegram_chat_id BIGINT PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE llm_chat_glossary (id INTEGER PRIMARY KEY, chat_id BIGINT, term VARCHAR(256), definition TEXT)')
        connection.exec_driver_sql('CREATE TABLE llm_chat_glossary_history (id INTEGER PRIMARY KEY, chat_id BIGINT, term VARCHAR(256), previous_definition TEXT)')
        connection.exec_driver_sql("INSERT INTO llm_chat_glossary VALUES (1, 2, 'рест', 'original')")
        connection.exec_driver_sql("INSERT INTO llm_chat_glossary_history VALUES (1, 2, 'рест', 'previous')")
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        assert connection.exec_driver_sql('SELECT definition FROM llm_chat_glossary').scalar_one() == 'original'
        assert connection.exec_driver_sql('SELECT previous_aliases FROM llm_chat_glossary_history').scalar_one() is None
        connection.exec_driver_sql("INSERT INTO llm_chat_glossary_aliases (glossary_id, chat_id, alias) VALUES (1, 2, 'ресты')")
        assert connection.exec_driver_sql('SELECT alias FROM llm_chat_glossary_aliases').scalar_one() == 'ресты'
        with Operations.context(context):
            migration.downgrade()
        assert 'llm_chat_glossary_aliases' not in sa.inspect(connection).get_table_names()
        assert connection.exec_driver_sql('SELECT previous_definition FROM llm_chat_glossary_history').scalar_one() == 'previous'
    engine.dispose()
