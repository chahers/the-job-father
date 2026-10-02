# jobfather-crawler

URL-driven, human-in-the-loop job ingestion for **the-job-father**.

You paste specific job-posting URLs (JobStreet, Hiredly, Indeed, LinkedIn); the
crawler fetches each one, extracts structured data, converts the job description
to clean Markdown, and **stages everything to disk**. A separate `load` step then
upserts the staged jobs into the shared local **PostgreSQL + pgvector** database
for the LangGraph tailoring agent to consume.

Design rules this module follows:

* **URL-driven only** - no search/discovery crawling.
* **No LLM extraction** - schema.org JSON-LD first, CSS selectors second.
* **The full JD is stored**, never a summary.
* **Stage first, load second** - the whole crawl pipeline is testable with no DB.
* **Dedup by URL** (`url_hash`), change detection by `content_hash`.

---

## Status

| Milestone | State |
|---|---|
| M0 scaffold, config, scripts | Done |
| M1 Hiredly end-to-end | Done (validated against a loopback server, not a live board) |
| Shared `database/` restructure | Done - schema is now repo-root shared, migration runner in place |
| Role + database bootstrap | **Pending - needs your `postgres` superuser password (one time)** |
| Loader against live Postgres | **Pending - `load` has never run against a real cluster** |
| M2 JobStreet (CDP) | Not started |
| M3 LinkedIn | Not started |
| M4 Indeed | Not started |
| Resume vectors / LangGraph / frontend | Not started |

---

## Layout

```
web-crawler/
├── config/          settings.yaml | sources.yaml | filters.yaml
├── src/jobfather_crawler/
│   ├── cli.py       crawl | inspect | load | db-init | validate
│   ├── crawler.py   Crawl4AI wiring: browser/run config, dispatchers, retries
│   ├── extract.py   schema.org JobPosting JSON-LD + CSS payload mapping
│   ├── markdown.py  HTML -> Markdown, front-matter assembly
│   ├── dedup.py     URL normalisation, url_hash / content_hash / slug
│   ├── staging.py   run directory writer (json + md + raw_html + dead-letter)
│   ├── loader.py    idempotent upsert + embeddings + crawl_runs / failures
│   └── sources/     base + jobstreet | hiredly | indeed | linkedin adapters
└── tests/           pure-stdlib/pydantic tests (no browser, no DB)
```

> The **database is not inside this folder**. The schema, migrations and cluster
> scripts are shared by every agent in the repository and live in the top-level
> [`../database`](../database) directory. This module owns the `jobs`,
> `crawl_runs` and `crawl_failures` tables (defined in
> `../database/migrations/002_crawl.sql`).

---

## Prerequisites already verified on this machine

| Component | Status |
|---|---|
| PostgreSQL | **16.10** at `%LOCALAPPDATA%\pgsql16\pgsql` (binaries, no installer), data dir `%LOCALAPPDATA%\pgsql16\data`, listening on `127.0.0.1:5432` |
| pgvector | **0.8.6 already installed** (`pgsql\lib\vector.dll`, `pgsql\share\extension\vector.control`) |
| Auth | `scram-sha-256` -> the `postgres` superuser password is needed **once** for bootstrap |
| Python | only 3.14 is on PATH -> create a 3.13 venv with `uv` (the project targets 3.12/3.13) |

---

## Setup

### 1. Python environment (no admin needed)

```powershell
# install uv for the current user, then a 3.13 interpreter + venv
powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
uv python install 3.13
uv venv --python 3.13 .venv
uv pip install -e ".[dev]"
crawl4ai-setup      # installs the Playwright browsers
crawl4ai-doctor     # optional sanity check
```

### 2. Database (one time)

Run the bootstrap from the **repository root** (it bootstraps the shared
database, not the crawler):

```powershell
# creates role `jobfather`, database `jobfather`, and the `vector` extension.
# Prompts for the postgres superuser password; nothing is written to disk.
.\database\scripts\pg_setup.ps1 -SuperuserPassword (Read-Host -AsSecureString 'postgres superuser password')

# from web-crawler/: apply the numbered migrations
cd web-crawler
.\.venv\Scripts\python.exe -m jobfather_crawler db-init
```

Lifecycle (always use graceful stop - a previous force-kill triggered crash recovery):

```powershell
.\database\scripts\pg.ps1 status | start | stop | restart | log | shell
```

Never edit an already-applied migration - add a new numbered one. The migration
rules and the table-ownership map are in [`../database/README.md`](../database/README.md).

### 3. Secrets

`.env` is git-ignored. Fill in `OPENAI_API_KEY` before running `load`:

```
DATABASE_URL=postgresql+psycopg://jobfather:the-great-job-father@127.0.0.1:5432/jobfather
OPENAI_API_KEY=sk-...
```

## Usage

### Check configuration

```powershell
python -m jobfather_crawler validate --urls my_job_urls.txt
```

### Crawl

Two interchangeable inputs - a JSON run request (what the future web frontend
will send) or plain flags.

```json
// run_request.json
{
  "run_id": "2026-10-01-kuala-lumpur",
  "state": "Kuala Lumpur",
  "work_arrangement": "hybrid",
  "expected_salary": { "min": 6000, "max": 9000, "currency": "MYR", "period": "monthly" },
  "urls": [
    "https://my.hiredly.com/jobs/acme-jobs-senior-software-engineer-12345",
    "https://my.jobstreet.com/job/91234567"
  ],
  "options": { "force_refresh": false, "max_concurrency": 8, "save_html": "failures" }
}
```

```powershell
python -m jobfather_crawler crawl --input run_request.json

# or with flags (state/arrangement/salary default from config/filters.yaml)
python -m jobfather_crawler crawl --urls my_job_urls.txt --state "Kuala Lumpur" --arrangement hybrid --salary-min 6000 --salary-max 9000
```

Output lands in `data/runs/<run_id>/`:

```
jobs/<slug>.json      canonical JobPosting
jobs/<slug>.md        YAML front-matter + Markdown JD (LLM-ready)
raw_html/<slug>.html  only when save_html = always | failures
manifest.json         config snapshot + per-URL status + counts
dead_letter.jsonl     every failed URL with error + attempts
run_request.json      exact input, for auditability
```

### Load into Postgres + pgvector

```powershell
python -m jobfather_crawler load --run-id 2026-10-01-kuala-lumpur
python -m jobfather_crawler load            # latest run
python -m jobfather_crawler load --no-embed # skip OpenAI embeddings
```

Idempotent: `ON CONFLICT (url_hash) DO UPDATE`, re-runnable any number of times.

### Calibrate selectors

```powershell
python -m jobfather_crawler inspect "https://my.hiredly.com/jobs/some-job-123"
# writes data/inspect/<slug>.html + .md -> build selectors from real markup
```

---

## Which URLs to paste

Paste **direct links to individual job postings**. Home pages, search result
pages and company pages are not supported (there is no discovery crawl).

| Source | Example shape | Cap/run | Browser |
|---|---|---|---|
| JobStreet | `https://my.jobstreet.com/job/<id>` | **100** | CDP-attached Chrome |
| Hiredly | `https://my.hiredly.com/jobs/<slug>` | **50** | managed profile |
| Indeed | `https://malaysia.indeed.com/viewjob?jk=<key>` | **50** | managed profile (best-effort) |
| LinkedIn | `https://www.linkedin.com/jobs/view/<id>/` | **50** | CDP-attached Chrome |
| | | ceiling **250** | |

Caps live in `config/sources.yaml` (`max_jobs`), the ceiling in
`config/settings.yaml` (`run.max_jobs_total`). Over-quota URLs are truncated with
a warning - never silently dropped.

---

## CDP runbook (JobStreet + LinkedIn)

These sites block headless browsers, so the crawler drives **your own logged-in
Chrome**.

1. Close every Chrome window.
2. Launch a dedicated profile with remote debugging:

   ```powershell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:LOCALAPPDATA\jobfather-chrome"
   ```
3. In that window, sign in to JobStreet and LinkedIn and clear any CAPTCHA.
   **Leave the window open.**
4. Verify the endpoint responds: <http://localhost:9222/json/version>
5. Run the crawl. The crawler attaches over CDP and never closes your browser.

Hiredly and Indeed use a persistent managed profile
(`CRAWLER_CHROME_USER_DATA_DIR`, default `.profiles/jobfather`); log in once the
first time the browser opens.

---

## How failures are handled

* **Transient** errors (timeouts, `429`, `503`, navigation/browser drops) are
  retried with exponential backoff + jitter (`config/settings.yaml` -> `retry`).
* **Permanent** errors (`404`, `410`, login/CAPTCHA/challenge pages) skip
  retries and are dead-lettered immediately.
* Every failed URL is recorded in `dead_letter.jsonl` **and** (on `load`) in the
  `crawl_failures` table.
* A page that crawls but yields no title or a very short description is treated
  as a failure so challenge/empty pages never pollute the dataset.

---

## Database schema (owned by this module)

| Table | Purpose |
|---|---|
| `jobs` | one row per posting; `url_hash` unique, `embedding vector(1536)` + HNSW cosine index |
| `crawl_runs` | per-run audit row (counts, request metadata, timings) |
| `crawl_failures` | dead-letter mirror |
| `schema_migrations` | shared - which `NNN_*.sql` files have already been applied |

The sibling resume agent will own its own `resumes` table in the same database
(`../database/migrations/003_resumes.sql`, not created yet).

---

## Done

Built and verified:

- **Sources**: JobStreet (100/run, CDP-attached Chrome), Hiredly (50), Indeed (50, best-effort), LinkedIn (50, CDP + deliberately low concurrency). Global ceiling **250/run**; per-source caps live in `config/sources.yaml`.
- **Extraction**: schema.org `JobPosting` JSON-LD first (stdlib, very stable), CSS schema fallback configured per source in YAML. **No LLM extraction**, and the **full JD is stored**, never summarised.
- **Crawl4AI wiring**: `AsyncWebCrawler` + `arun_many`, `MemoryAdaptiveDispatcher` / `SemaphoreDispatcher`, `RateLimiter` (429/503 backoff), `CrawlerMonitor`, per-source browser configs (CDP attach vs. managed persistent profile).
- **Pipeline**: URL normalisation and dedup by `url_hash`, change detection by `content_hash`, staging to `data/runs/<run_id>/` (JSON + Markdown + raw HTML + manifest + dead-letter), idempotent `ON CONFLICT (url_hash) DO UPDATE` loader, OpenAI `text-embedding-3-small` embeddings into `vector(1536)` with an HNSW cosine index.
- **Resilience**: transient-vs-permanent error classification, exponential backoff with jitter, dead-letter to both `dead_letter.jsonl` and the `crawl_failures` table, and rejection of challenge/empty pages so they never pollute the dataset.
- **Interface**: CLI (`crawl`, `inspect`, `load`, `db-init`, `validate`) plus a JSON `run_request.json` contract that the future frontend will write.
- **Database**: schema lives in the shared top-level [`../database`](../database) directory. This module owns `jobs`, `crawl_runs` and `crawl_failures`; `db-init` runs numbered migrations tracked in `schema_migrations`, so each file applies exactly once.
- **Tests**: **50 passing**, including a Crawl4AI API contract test and 8 static checks on the shared migrations.

Verified on this machine:

- 50 tests pass (Python 3.13.15 venv, Crawl4AI **0.9.4**, Playwright browsers installed).
- A real crawl runs end to end - browser launch, navigation, extraction, staged JSON - producing the correct title, company, location, salary, employment type, posted date, inferred seniority, skills and Markdown body.
- `find_database_dir()` resolves the shared `database/` at the repo root, and a stale-reference grep for `db_dir` / `schema_sql` / `001_init` / `apply_schema` returns zero hits.
- `db-init` resolves the new path, finds both migrations, and reaches Postgres. It currently stops at `password authentication failed for user "jobfather"`, which is expected until the bootstrap below has been run.
- PostgreSQL **16.10** and pgvector **0.8.6** were found **already installed** at `%LOCALAPPDATA%\pgsql16`, so no installer or admin rights are needed.

---

## To do next

### 1. Finish the database, then prove the loader end to end

Two parts: a one-time bootstrap that only you can run (it needs your interactive
`postgres` superuser password), then the first real load through this code.

```powershell
# 1) from the REPOSITORY ROOT: create role `jobfather`, database `jobfather`, extension `vector`
.\database\scripts\pg_setup.ps1 -SuperuserPassword (Read-Host -AsSecureString 'postgres superuser password')

# 2) from web-crawler/: apply the numbered migrations
cd web-crawler
.\.venv\Scripts\python.exe -m jobfather_crawler db-init   # expect: applied 001_extensions / 002_crawl
.\.venv\Scripts\python.exe -m jobfather_crawler db-init   # expect: already up to date

# 3) still unproven: the loader against a real cluster
.\.venv\Scripts\python.exe -m jobfather_crawler load --no-embed   # expect inserted / updated counts
.\.venv\Scripts\python.exe -m jobfather_crawler load --no-embed   # expect 0 inserted, N updated
```

Then add `OPENAI_API_KEY` to `.env` and re-run `load` (without `--no-embed`) to
prove embeddings land in `embedding vector(1536)`.

Known gap to fold in while you are there: a failed `db-init` currently dumps a raw
traceback instead of a one-line message.

### 2. First live crawl per source, and selector calibration

- Start with **Hiredly** (server-rendered, friendliest), then **JobStreet** and
  **LinkedIn** through the CDP-attached Chrome (see the CDP runbook above), then
  **Indeed** (best-effort).
- For each source run `python -m jobfather_crawler inspect "<job-url>"` and update
  the CSS fallback schemas in `config/sources.yaml` against the real markup.
- The JSON-LD extraction path is already validated; the CSS selectors are still
  unverified starting points.

### 3. Remaining ingestion work

- M2 JobStreet live + batch dispatcher tuning; M3 LinkedIn; M4 Indeed.
- Human-in-the-loop pause when a login/CAPTCHA page is detected (`--interactive`).
- Re-crawl only the failures of a previous run (`crawl --retry-failed --run-id ...`).
- Optional selector-drift check against saved raw HTML from earlier runs.

### 4. Downstream layers (not started)

- **Resume ingestion**: a `resumes` table in the same database plus embeddings for the master resume (`resume-samples/`).
- **Orchestration**: LangGraph workflow with drafting and self-correction nodes.
- **Frontend**: Streamlit / CLI that writes `run_request.json` and calls the crawler.

---

Re-verify the suite at any time from `web-crawler/`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```


