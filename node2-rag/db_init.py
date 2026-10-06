"""Apply the shared migrations in ``database/migrations`` (idempotent).

The crawler ships an equivalent runner (``jobfather_crawler.loader.apply_migrations``).
This is a deliberately small copy so node 2 works standalone -- its own venv has no
crawler dependencies -- while sharing the *same* folder and the *same*
``schema_migrations`` table, so neither node ever re-applies a file the other
already applied.  ``web-crawler/.venv`` does not exist on this machine yet, which
is the practical reason node 2 needs its own entry point.

Usage (from node2-rag/):
    .\\.venv\\Scripts\\python.exe db_init.py            # apply anything not yet recorded
    .\\.venv\\Scripts\\python.exe db_init.py --status   # diagnostics only, no writes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import (
    MIGRATIONS_DIR,
    ConfigError,
    connect,
    load_settings,
    redact,
    vector_extension_version,
)

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _hint(exc: Exception, path: Path) -> str:
    """Turn a raw psycopg error into something actionable."""
    message = str(exc).strip()
    sqlstate = getattr(exc, "sqlstate", "") or ""
    if sqlstate == "42501" or "superuser" in message.lower():
        return (
            f"{path.name} needs the postgres superuser (pgvector is not a trusted "
            "extension).  Run the one-time bootstrap from the repo root:\n"
            "  .\\database\\scripts\\pg_setup.ps1 -SuperuserPassword "
            "(Read-Host -AsSecureString 'postgres superuser password')"
        )
    return f"migration {path.name} failed: {message}"


def apply_migrations(conn, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every not-yet-recorded ``NNN_*.sql`` in *migrations_dir*.

    Returns the versions applied (empty list == "already up to date").  Each file
    runs inside its own transaction together with the ``schema_migrations`` insert,
    so a failure can never record a half-applied migration.
    """
    files = sorted(Path(migrations_dir).glob("*.sql"))
    if not files:
        raise ConfigError(f"no *.sql migrations found in {migrations_dir}")

    with conn.cursor() as cur:
        cur.execute(SCHEMA_MIGRATIONS_DDL)
        cur.execute("SELECT version FROM schema_migrations")
        done = {row[0] for row in cur.fetchall()}

    applied: list[str] = []
    for path in files:
        version = path.stem  # e.g. "003_resume_chunks"
        if version in done:
            print(f"  [skip]  {version} (already applied)")
            continue
        print(f"  [apply] {version} ...")
        try:
            with conn.transaction():  # BEGIN ... COMMIT, even on an autocommit conn
                with conn.cursor() as cur:
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s) "
                        "ON CONFLICT (version) DO NOTHING",
                        (version,),
                    )
        except Exception as exc:  # noqa: BLE001 - re-raised as ConfigError
            raise ConfigError(_hint(exc, path)) from exc
        applied.append(version)

    return applied


def show_status(conn) -> int:
    """Print what is installed without changing anything."""
    version = vector_extension_version(conn)
    print(f"  pgvector     : {version or 'NOT INSTALLED'}")

    if version:
        with conn.cursor() as cur:
            cur.execute("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector")
            distance = float(cur.fetchone()[0])
        verdict = "ok" if abs(distance - 1.0) < 1e-9 else "UNEXPECTED"
        print(f"  distance probe: {distance} (expected 1.0) -> {verdict}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
        )
        tables = [row[0] for row in cur.fetchall()]
        print(f"  tables       : {', '.join(tables) if tables else '(none)'}")

        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        tracked = [row[0] for row in cur.fetchall()]
        print(f"  migrations   : {', '.join(tracked) if tracked else '(none)'}")

        if "resume_chunks" in tables:
            cur.execute(
                "SELECT count(*) FILTER (WHERE embedding IS NULL), count(*) FROM resume_chunks"
            )
            pending, total = cur.fetchone()
            print(f"  resume_chunks: {total} row(s), {pending} without a vector")
        if "jobs" in tables:
            cur.execute(
                "SELECT count(*) FILTER (WHERE embedding IS NULL), count(*) FROM jobs"
            )
            pending, total = cur.fetchone()
            print(f"  jobs         : {total} row(s), {pending} without a vector")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply the shared database/migrations (node 2 copy of db-init)."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print pgvector version, tables, migrations and pending vectors; change nothing",
    )
    parser.add_argument(
        "--migrations-dir",
        default=str(MIGRATIONS_DIR),
        help=f"default: {MIGRATIONS_DIR}",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    print(f"database   : {redact(settings.dsn)}")
    print(f"migrations : {args.migrations_dir}")

    try:
        with connect(settings, autocommit=True) as conn:
            if args.status:
                return show_status(conn)
            applied = apply_migrations(conn, Path(args.migrations_dir))
            if applied:
                print(f"\nApplied {len(applied)} migration(s): {', '.join(applied)}")
            else:
                print("\nAlready up to date.")
            print()
            return show_status(conn)
    except ConfigError as exc:
        print(f"\n[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())