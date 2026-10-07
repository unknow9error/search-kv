import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def test_conversation_key_migration_retains_legacy_rows_and_reverses(tmp_path):
    database = tmp_path / "migration.sqlite3"
    environment = {
        **os.environ,
        "MEKEN_ENV": "test",
        "MEKEN_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
    }
    backend = Path(__file__).resolve().parents[1]

    def alembic(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=backend,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    alembic("upgrade", "0006_relation")
    user_id, conversation_id, turn_id = str(uuid4()), str(uuid4()), str(uuid4())
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO users(id, created_at) VALUES (?, CURRENT_TIMESTAMP)", (user_id,))
        connection.execute(
            "INSERT INTO conversations(id,user_id,preferences,title,created_at,updated_at) "
            "VALUES (?,?,'{}','Legacy',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (conversation_id, user_id),
        )
        connection.execute(
            "INSERT INTO turns(id,conversation_id,client_turn_id,message,status,created_at) "
            "VALUES (?,?,?,'Legacy message','complete',CURRENT_TIMESTAMP)",
            (turn_id, conversation_id, str(uuid4())),
        )
        connection.execute(
            "INSERT INTO turn_events(id,turn_id,sequence,kind,payload) VALUES (?,?,1,'message',?)",
            (str(uuid4()), turn_id, '{"text":"Legacy response","citations":[]}'),
        )
    alembic("upgrade", "head")
    alembic("check")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT title,client_conversation_id,initial_preferences_hash FROM conversations WHERE id=?",
            (conversation_id,),
        ).fetchone() == ("Legacy", None, None)
        assert connection.execute("SELECT message FROM turns WHERE id=?", (turn_id,)).fetchone() == (
            "Legacy message",
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM turn_events WHERE turn_id=?", (turn_id,)
        ).fetchone() == (1,)
        key = str(uuid4())
        connection.execute(
            "UPDATE conversations SET client_conversation_id=?,initial_preferences_hash=? WHERE id=?",
            (key, "a" * 64, conversation_id),
        )
        try:
            connection.execute(
                "INSERT INTO conversations "
                "(id,user_id,preferences,title,created_at,updated_at,client_conversation_id,initial_preferences_hash) "
                "VALUES (?,?,'{}','Duplicate',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?)",
                (str(uuid4()), user_id, key, "a" * 64),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Migration omitted the per-user idempotency uniqueness constraint")
    alembic("downgrade", "0006_relation")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT title FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone() == ("Legacy",)
        assert connection.execute(
            "SELECT COUNT(*) FROM turn_events WHERE turn_id=?", (turn_id,)
        ).fetchone() == (1,)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(conversations)")}
        assert "client_conversation_id" not in columns
    alembic("upgrade", "head")
    alembic("check")
