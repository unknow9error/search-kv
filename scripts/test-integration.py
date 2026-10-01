"""Run tests in isolated PG schemas. Secrets are never printed or placed in argv."""
import os
import socket
from pathlib import Path
import subprocess

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
values = dotenv_values(root / ".env")
environment = os.environ.copy()
environment["MEKEN_TEST_DATABASE_URL"] = "postgresql+asyncpg://meken:" + values["POSTGRES_PASSWORD"] + "@127.0.0.1:55432/meken"
environment["MEKEN_TEST_REDIS_URL"] = "redis://127.0.0.1:56379/1"
for port in (55432, 56379):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    except OSError:
        raise SystemExit("Integration services are not ready. Run docker compose -f compose.yml -f infra/compose.test.yml up -d db redis first.") from None

raise SystemExit(subprocess.run([str(root / "backend/.venv/bin/pytest"), "-q", "--tb=short"], cwd=root / "backend", env=environment).returncode)
