"""Refresh jobs into the SERVER's database, running from this laptop.

  ./refresh                    (PowerShell, from the project root; see refresh.ps1)
  ./refresh --company micron

Why: Workday's Cloudflare rate-limits the server's AWS datacenter IP, but not a home or
campus connection. So the refresh runs here, and only the database writes go to the server.

How:
  1. Read the server's database credentials from its .env over SSH (nothing stored here).
  2. Open an SSH tunnel: localhost:15432 on this laptop -> Postgres on the server.
     Postgres is never exposed to the internet; the tunnel rides on the SSH connection.
  3. Check the server's schema matches this code's migrations (deploy first if it doesn't).
  4. Run the normal `python -m app.refresh` with DATABASE_URL pointed at the tunnel.
  5. Close the tunnel.
Uses the `jobradar` host from ~/.ssh/config.
"""

import os
import socket
import subprocess
import sys
import time
from urllib.parse import quote_plus

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

SSH_HOST = "jobradar"
LOCAL_PORT = 15432  # not 5432: the laptop's own Postgres container uses that


def _server_db_url() -> str:
    """Build a DATABASE_URL from the POSTGRES_* lines in the server's .env."""
    out = subprocess.run(
        ["ssh", SSH_HOST, "grep '^POSTGRES_' ~/JobScraper/.env"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    env = dict(line.strip().split("=", 1) for line in out.splitlines() if "=" in line)
    user = env.get("POSTGRES_USER", "jobs")
    password = env.get("POSTGRES_PASSWORD", "jobs")
    db = env.get("POSTGRES_DB", "jobs")
    return f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}@localhost:{LOCAL_PORT}/{db}"


def _open_tunnel() -> subprocess.Popen:
    tunnel = subprocess.Popen(
        ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{LOCAL_PORT}:localhost:5432", SSH_HOST]
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            raise SystemExit("SSH tunnel failed. Does `ssh jobradar` work? (Your IP may need adding to jobradar-sg.)")
        try:
            socket.create_connection(("localhost", LOCAL_PORT), timeout=1).close()
            return tunnel
        except OSError:
            time.sleep(0.3)
    tunnel.terminate()
    raise SystemExit("SSH tunnel didn't come up within 15 seconds.")


def _check_schema(url: str) -> None:
    """Refuse to write with code whose migrations don't match the server's database."""
    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    engine = create_engine(url)
    with engine.connect() as conn:
        actual = conn.execute(text("select version_num from alembic_version")).scalar()
    engine.dispose()
    if actual != expected:
        raise SystemExit(
            f"Server database is at migration {actual}, but this code expects {expected}.\n"
            "Deploy first (push, then on the server: git pull && docker compose up -d --build), "
            "or git pull here if the server is ahead."
        )


def main() -> None:
    print("Connecting to the server...", flush=True)
    url = _server_db_url()
    tunnel = _open_tunnel()
    try:
        _check_schema(url)
        print("Refreshing into the server's database (from this laptop's connection)...\n", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "app.refresh", *sys.argv[1:]],
            env={**os.environ, "DATABASE_URL": url},
        )
        sys.exit(result.returncode)
    finally:
        tunnel.terminate()


if __name__ == "__main__":
    main()
