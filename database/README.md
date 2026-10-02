# database

Shared PostgreSQL 16 + pgvector infrastructure for every agent in this
repository. It lives at the repo root (not inside an agent folder) because
**one database is shared by all agents**.

## Why it is not inside `web-crawler/`

The crawler owns `jobs`, `crawl_runs` and `crawl_failures`, but the resume agent
will own a `resumes` table **in the same database** - LangGraph needs to compare
resume vectors against job vectors with a single `<=>` query. A schema directory
nested inside one agent would have no correct home for the second agent's
migration, so the schema is now top-level.

## Table ownership

| Table | Owner | Notes |
| --- | --- | --- |
| `jobs` | `web-crawler/` | one row per posting; `url_hash` UNIQUE, `embedding vector(1536)` + HNSW cosine index |
| `crawl_runs` | `web-crawler/` | per-run audit row |
| `crawl_failures` | `web-crawler/` | dead-letter mirror |
| `schema_migrations` | shared | created automatically by `db-init` |
| `resumes` | resume agent | **not created yet** - add `003_resumes.sql` |

**Rule:** an agent only ever changes migrations for the tables it owns. Never
edit an already-applied migration file - add a new numbered one.

## Layout

```
database/
├── README.md
├── migrations/
│   ├── 001_extensions.sql   CREATE EXTENSION vector (shared)
│   ├── 002_crawl.sql        jobs / crawl_runs / crawl_failures (crawler)
│   └── 003_resumes.sql      (future) resumes (resume agent)
└── scripts/
    ├── pg_setup.ps1         one-time role + database + extension bootstrap
    └── pg.ps1               start | stop | restart | status | log | shell
```

Migration files follow `NNN_snake_name.sql`, are applied in sorted order, and
must be written safely for re-run (`IF NOT EXISTS`). Applied versions are
recorded in `schema_migrations`, so a migration is only executed once.

## One-time bootstrap

`CREATE EXTENSION vector` needs a **superuser** the first time (pgvector's
`vector.control` is not `trusted`). `pg_setup.ps1` handles that; afterwards
`db-init` runs unprivileged as the `jobfather` app role.

```powershell
# run from the repository root
.\database\scripts\pg_setup.ps1 -SuperuserPassword (Read-Host -AsSecureString 'postgres superuser password')
```

This is idempotent and prints the ready-to-paste `DATABASE_URL`. The superuser
password is used only for this bootstrap and is never written to disk.

## Applying migrations

The crawler ships the migration runner:

```powershell
cd web-crawler
.\.venv\Scripts\python.exe -m jobfather_crawler db-init
```

It prints each migration applied, or `already up to date`.

## Cluster lifecycle

The server is a portable PostgreSQL 16.10 cluster at
`%LOCALAPPDATA%\pgsql16` (data dir `%LOCALAPPDATA%\pgsql16\data`, log
`%LOCALAPPDATA%\pgsql16\server.log`). **Always stop it gracefully** - a previous
force-kill (`^C`) triggered crash recovery.

```powershell
.\database\scripts\pg.ps1 status | start | stop | restart | log | shell
```

## Adding a new agent's schema

1. `database/migrations/003_<agent>.sql` with a header naming the owning agent.
2. Use `IF NOT EXISTS` everywhere so re-runs are safe.
3. `python -m jobfather_crawler db-init` from `web-crawler/` applies it.