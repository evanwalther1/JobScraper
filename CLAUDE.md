# JobScraper — Project Cheat Sheet

A personal job tracker. It pulls postings straight from companies' career pages, which are
usually hosted on an ATS such as Greenhouse, Lever or Ashby. It stores them in Postgres,
shows what's new since yesterday, and tracks where I've applied. Later it will tailor my
resume to a job description.

**Core idea:** most "custom" career pages are hosted by an ATS (Applicant Tracking System).
Those platforms serve their listings as **public JSON APIs**. We call those APIs directly
instead of scraping HTML. It's legitimate, stable, and far less brittle.

**Target area:** the Boise metro / Treasure Valley (Boise, Meridian, Eagle, Nampa, Star,
Kuna, Caldwell, Garden City). Only jobs located there are stored; see `app/location.py`. Most of the big local tech employers
(Micron, Clearwater Analytics, SEL, HP) hire through **Workday**. Smaller local companies
(Truckstop, CompuNet, VYNYL) use **Greenhouse**. The seed list is in `app/seed_companies.py`.

**We don't scrape** LinkedIn, Indeed or Handshake. Their terms forbid it, they block bots,
and Handshake sits behind school SSO. Jobs from those sites get added by hand.

---

## Tech stack at a glance

| Layer | Tool | What it does here |
|---|---|---|
| Language | **Python 3.14** (local venv) | Everything is Python |
| Database | **PostgreSQL 16** | Stores companies, jobs, applications |
| DB runtime | **Docker + Docker Compose** | Runs Postgres in a container; no Windows install |
| ORM | **SQLAlchemy 2.x** | Python classes ⇄ database tables; builds queries |
| DB driver | **psycopg 3** | The low-level library that talks to Postgres (SQLAlchemy uses it) |
| Migrations | **Alembic** | Version control for the schema. Each schema change is a migration file |
| Config | **python-dotenv** + `.env` | Loads `DATABASE_URL` and secrets; `.env` is never committed |
| HTTP client | **httpx** | Calls the ATS JSON APIs |
| Web server | **FastAPI** + **uvicorn** | Serves the pages and HTMX fragments |
| UI | **Jinja + HTMX + Pico.css** | Server-rendered pages with no JS build step |
| HTML sanitizer | **nh3** | Cleans third-party job descriptions before display |
| Tests | **pytest** | Location, relevance, connector parsing, radar matching, health |
| Discovery | **Adzuna API** + Clearbit autocomplete | Finds employers hiring locally; name -> domain |
| CI / Deploy | **GitHub Actions**, cheap host *(planned)* | Lint, test, build images, deploy |

### How the pieces fit together

```
  ATS APIs (Greenhouse, Lever, Ashby, ...)
        │  JSON over HTTPS (httpx)
        ▼
  Connectors ──► normalized Job objects
        │
        ▼
  refresh command ──► upsert into Postgres (SQLAlchemy + psycopg)
                              ▲
                              │ reads/writes
  FastAPI + Jinja/HTMX UI ────┘  ◄── me, in the browser

  Alembic  ── creates and changes the tables (schema)
  Docker   ── runs Postgres (and later the API + worker)
```

- **Docker** runs the Postgres *server* in a container. The data lives in a Docker volume
  (`jobscraper_pgdata`), so it survives restarts.
- **SQLAlchemy models** (`app/models.py`) are the source of truth for what the tables should look like.
- **Alembic** compares the models to the live database and writes migration files that make
  them match. `upgrade head` applies them.
- **psycopg** is the driver. SQLAlchemy hands it SQL, and it sends that to Postgres.

---

## Project layout

```
docker-compose.yml     Postgres service (later: api + worker services)
.env / .env.example    DB credentials + DATABASE_URL (.env is gitignored)
requirements.txt       Python dependencies
app/
  db.py                engine + SessionLocal (reads DATABASE_URL from .env)
  models.py            SQLAlchemy models = the schema
  seed_companies.py    the company list to poll (upsert, safe to re-run)
  detect_ats.py        find which ATS a company uses: `python -m app.detect_ats "Name" domain.com`
  refresh.py           runs every connector and syncs results into jobs
  location.py          the Treasure Valley filter (in_area); edit this to change the search area
  relevance.py         labels jobs match / senior / non_tech; `python -m app.relevance` relabels
  radar.py             discovery radar: Adzuna postings -> companies we don't track -> ATS detection
  main.py              FastAPI app: Jobs + Applications routes (includes the routers below)
  routes_companies.py  Companies page: health, favorite/hidden, notes, add by hand
  routes_radar.py      Radar page: coverage, untracked companies, connector payoff, add & track
  web.py               shared web plumbing: templates, DB session dependency, template filters
  templates/           Jinja HTML: base, jobs, applications, companies, radar
    partials/          fragments HTMX swaps in place (job_card, application_row, ...)
  static/style.css     layout on top of Pico.css
tests/                 pytest: location, relevance rules (MUST_MATCH = jobs never to hide), connector parsing
data/
  treasure_valley_candidates.csv   every company considered: ATS, status (added / needs connector / unknown)
  connectors/
    base.py            JobPosting (normalized shape), Connector base class, retry/backoff
    greenhouse.py      Greenhouse public API: one request per company, descriptions included
    workday.py         Workday career-site API: paged list (parallel) + one detail request per job
    ukg.py             UKG/UltiPro job board API: JSON search + description embedded in detail page
    paylocity.py       Paylocity job board: window.pageData list + JSON-LD description per job
    oracle.py          Oracle Recruiting Cloud REST API: radius search around Boise + detail per job
    __init__.py        CONNECTORS registry: Source -> connector class
alembic.ini            Alembic config (DB URL is overridden from .env in env.py)
alembic/
  env.py               wires Alembic to app.models.Base and DATABASE_URL
  versions/            migration files, one per schema change (commit these!)
```

---

## Database schema

**companies**: who we poll.
- `name`, `source` (greenhouse | lever | ashby | workday | ukg | paylocity | oracle | manual), `ats_slug`
  (e.g. `stripe` in `boards-api.greenhouse.io/v1/boards/stripe/jobs`), `careers_url`,
  `active`, `last_fetched_at`
- `preference` (favorite | normal | hidden) + `notes`: set on the Companies page. Hidden
  companies are still refreshed; their jobs are just left out of the default Jobs view.
- Health from the last refresh: `last_success_at`, `last_error`/`last_error_at`,
  `last_fetched`, `last_in_area`, `last_new`, `last_closed`
- `location_aliases`: optional per-company regex that also counts as "in the area", for
  boards that label locations by building or omit the state ("Meridian - Main", "City Hall")
- Unique on `(source, ats_slug)`

**jobs**: one row per posting, upserted on every refresh.
- `company_id` → companies, `company_name` (denormalized copy, re-set every refresh),
  `source`, `external_id` (the ATS's own ID for the posting)
- `relevance` (match | senior | non_tech) + `relevance_reason`: see "Relevance filtering" below
- `title`, `location`, `remote`, `department`, `employment_type`, `url`,
  `description_html`, `posted_at`
- **Change detection:** `first_seen_at` ("new since yesterday"), `last_seen_at` (bumped
  on each refresh), `closed_at` (set when a posting disappears)
- `content_hash` (to detect edits), `raw` (JSONB, the untouched API response)
- **Unique on `(source, external_id)`** is the upsert key

**applications**: my status for a job I care about.
- `job_id` (unique, one per job), `status` (saved | applied | interviewing | offer |
  rejected | withdrawn), `applied_at`, `notes`, timestamps

**excluded_postings**: only IDs (+ location) of postings whose details showed they're outside
the area. Refresh skips their detail requests next time; rows are pruned once the posting is gone.

**radar_companies / radar_postings**: what the discovery radar saw on Adzuna. Postings are
only used for discovery and the coverage number; they're never shown as jobs. A radar company
is linked to a tracked company (`company_id`) by normalized name or by hand, can be `ignored`,
and stores ATS detection results (`domain`, `detected_ats`, `detected_slug`).

**job_matches** (a view, not a table): open jobs with `relevance = 'match'`, newest first.
It's the list to browse in the VS Code extension.

### Relevance filtering (`app/relevance.py`)

**Rule #1: never hide a job I could actually do.** So irrelevant jobs are **labeled, not
deleted**, and every rule leans toward `match`.

`classify(title, description_html)` returns `(label, reason)`:
1. **senior**: the title says senior/sr/staff/principal/lead/manager/director/architect/
   SMTS/DMTS or a III+ level, *unless* it also has an entry-level marker (intern, new grad,
   junior, co-op, "Associate ... Manager"). Entry level always wins.
2. **match**: the title has a tech keyword (software, developer, data engineer/analyst,
   IT, cloud, security analyst, firmware, QA, ...), unless it's in a non-tech context
   (facilities, gas, legal, ...) with no strong software word.
3. **match (rescue)**: no tech keyword in the title, but the description names 3 or more
   programming tools (Python, SQL, C#, AWS, ...).
4. **non_tech**: everything else.

`relevance_reason` shows which rule decided, e.g. `senior title: 'Sr'` or
`description mentions c#, python, sql`.

**When a job is mislabeled:**
1. Add its title to `tests/test_relevance.py`, in `MUST_MATCH` for a job that was wrongly hidden.
2. Adjust the regexes in `app/relevance.py` until `pytest` passes.
3. Run `python -m app.relevance` to relabel every stored job with the new rules.

### Future note: descriptions and qualifications (not built yet)

Goal: keep each job's full description and pull out its qualifications, so I can filter
("no 5+ years required", "mentions Python") and feed them to resume tailoring later.

- **Size isn't a problem.** Postgres `TEXT` holds up to 1 GB per value. Anything over ~2 KB
  is automatically compressed and stored out-of-line (called **TOAST**), so big descriptions
  don't slow down scans of the `jobs` table. 10,000 jobs × ~8 KB ≈ 80 MB, which is tiny.
- **Planned columns:**
  - `description_html` (already exists): the original, for display.
  - `description_text`: HTML stripped, used for search and LLM input.
  - `qualifications` (JSONB): structured, e.g.
    `{"required": [...], "preferred": [...], "min_years": 2, "degree": "BS CS", "skills": ["Python", "SQL"]}`.
    Extract it with section-header parsing first ("Requirements", "Qualifications", "What you'll need"),
    then use an LLM as a fallback. JSONB with a GIN index makes the skills searchable.
  - `search_vector` (`tsvector`, generated from title + description_text) with a GIN index,
    for Postgres full-text search: `WHERE search_vector @@ plainto_tsquery('python backend')`.
- **Optional split:** move the large fields into a 1:1 `job_details` table if the `jobs`
  table starts to feel heavy. TOAST probably makes this unnecessary.
- Treat description text as **untrusted input** when it goes into LLM prompts.

**Design choices worth being able to explain:**
- Enums are stored as `VARCHAR` + `CHECK` constraints rather than native Postgres ENUMs,
  because adding a value to a native ENUM is awkward in migrations.
- A naming convention in `Base.metadata` gives every constraint a predictable name
  (`uq_jobs_source_external_id`, `ck_jobs_source`, ...), so migrations can refer to them.
- `raw` JSONB means we can re-normalize old data if a connector improves, without refetching.
- The upsert is `INSERT ... ON CONFLICT (source, external_id) DO UPDATE`, which makes
  refreshes **idempotent**: running one twice doesn't create duplicates.

---

## How a refresh works (`app/refresh.py`)

**Only Treasure Valley jobs are ever stored.** Filtering happens before detail requests
and before any writes, so refresh time scales with *local* jobs, not with a company's global
headcount. The rule lives in `app/location.py`:
- **Unambiguous names** match anywhere: Boise, Nampa, Treasure Valley.
- **Ambiguous names** (Meridian, Eagle, Star, Kuna, Caldwell, Garden City, Middleton,
  Emmett) only count next to "ID"/"Idaho". That rules out Meridian MS, Eagle CO, and so on.
- **Not included:** other Idaho cities (Idaho Falls, Lewiston, Moscow), statewide "Idaho",
  and plain "Remote". Edit `location.py` to change the area.

Companies are refreshed **4 at a time** (`COMPANY_WORKERS`); a full run of ~40 companies
takes about 20 seconds. For each active company, in its own transaction:
1. **fetch**: the connector returns open postings as `JobPosting`s.
   - **Workday filters server-side.** The first list response includes the board's location
     filter values, each with a tenant-specific ID (e.g. Micron's "Boise, ID - Main Site",
     SEL's "Idaho - Boise"). We pick the ones in the area and re-request with
     `appliedFacets`. If a board has location filters but none in the area (HP), we stop
     after one request.
     Order of preference: city/site values in the area, then a state value of "Idaho"
     (KLA), then "has city/state filters but none local" (skip). A board with only a
     *country* filter gets a full scan, because "USA" alone can hide Boise jobs.
   - **Greenhouse** and **Paylocity** return a company's whole board in one request.
   - **UKG** returns structured addresses (city + state) in the list.
   - **Oracle** (Albertsons) searches a 30-mile radius around the company's Boise location.
     Its location facets only list the top 100 locations, so filtering by facet would miss
     small towns (Nampa, Caldwell, Star, Kuna, Emmett); the radius search catches them.
2. **filter**: drop postings whose location isn't in the area. Workday sometimes hides
   locations as "3 Locations"; those are kept until their details reveal the real locations.
3. **details**: Workday, UKG and Paylocity lists have no (full) descriptions, so we fetch
   details only for in-area postings we've never stored a description for. Parallelism is set
   per connector (`detail_workers`: 5 by default, 2 for Paylocity, which rate-limits).
   Hidden-location postings that turn out to be out of area go into `excluded_postings`.
4. **upsert**: `INSERT ... ON CONFLICT (source, external_id) DO UPDATE`. New rows get
   `first_seen_at`. Every row seen gets `last_seen_at = run time`, and `closed_at` is
   cleared if a job reappears. `first_seen_at` is never overwritten.
5. **close**: any of this company's open jobs with `last_seen_at < run time` weren't in
   this fetch, so they get `closed_at = run time`.

Other details:
- **Isolation:** one failing company is logged and skipped; the others still commit.
- **Backoff:** `Connector.request` retries 429 and 5xx responses after 1s, 2s, 4s and 8s.
- **Workday quirks:**
  - Pages hold at most 20 jobs, and only the first page reports the real `total`.
  - Req IDs are only unique within a tenant, so `external_id = "tenant:reqid"`.
  - Some list entries have no `externalPath`; they're skipped.
- **Greenhouse quirk:** `content` is HTML-escaped HTML. Call `html.unescape` on it.
- **UKG quirk:** the detail page embeds the job as `CandidateOpportunityDetail({...})`;
  `json.JSONDecoder().raw_decode` parses just that object out of the HTML.
- **Paylocity quirk:** its JSON feed endpoint returns nothing unless the company enables it,
  so we read the job board page's embedded `window.pageData` instead.

### robots.txt check (do this before writing any new connector)
Our rule is "public/official APIs only, nothing a site forbids". So check the host's
`robots.txt` for the exact paths a connector would call:

| Platform | Status |
|---|---|
| Greenhouse, Workday, Paylocity, Oracle | OK (paths we use are allowed, or no robots.txt) |
| Adzuna | Blocks crawlers, but we use its official keyed API under its own terms |
| **UKG** | **Disallows `*/JobBoardView`, which our job-list call uses** (detail pages are allowed). Kept anyway by my decision, because Idaho Central Credit Union (ICCU) is on UKG and I want its jobs. Low volume: one list request per company per refresh |
| NEOGOV (governmentjobs.com) | Allows only search engines/aggregators (an RSS feed exists at `/SearchEngine/JobsFeed?agency=...`). Not built |
| iCIMS | `Disallow: /` for all bots. Not built |

### Adding a company
1. `python -m app.detect_ats "Company Name" company.com` finds its ATS and the ID it uses there.
2. Check the result by hand (the name-guessing fallback can hit a different company with a
   similar name; "bsu" on Workday is Ball State, not Boise State).
3. Add it to `app/seed_companies.py`, run `python -m app.seed_companies`, then
   `python -m app.refresh --company "Name"`.
4. If `in_area` is 0 but the company is local, look at how its board labels locations and
   add a `location_aliases` regex.

---

## Discovery radar (`app/radar.py`)

**Goal: never miss a job I'd want.** Instead of guessing which companies to add, let the jobs
point to the companies.
1. **Adzuna** (a job aggregator with an official API; key in `.env`) is asked for recent tech
   postings within ~30 miles of Boise: the IT category plus a software keyword query. The free
   tier allows ~250 requests/day, so a run is capped at 40 requests, spaced ~2.6s apart.
2. Postings are grouped by company. Names are normalized (`normalize_name`:
   "Micron Technology, Inc." -> "micron") and matched to tracked companies. Parenthetical
   names count too ("Kount (Equifax)" matches both).
3. For untracked companies with relevant local postings: look up the **domain** (Clearbit's
   free autocomplete; it picks the closest name match, so check it), then run the **ATS
   detector**. Each page load is capped at 10s / 2MB, and each company at ~40s, because some
   sites stall or trickle bytes forever. Results are saved per company.
4. **Coverage** = relevant local postings from tracked companies / all relevant local postings
   (ignored companies excluded). It answers "how much am I missing?"

**What we deliberately don't do:** follow Adzuna's redirect links to find the employer's
posting. That landing page is a JavaScript tracking page, and Adzuna's terms require sending
people through it. We only use the company name.

The Radar page lists untracked companies by relevant posting count, with **Add & track**
(when the detected ATS has a connector), **Detect ATS** with a corrected domain, **Link** (same
company as a tracked one, e.g. "Trinity Health" = Saint Alphonsus), and **Ignore**. Its
**Connector payoff** table groups unsupported ATSes by how many relevant companies use them.
That's how we decide which connector to build next, and we skip ATSes used by only one company.

Companies added from the UI live only in the database, not in `seed_companies.py`. Back up
with `pg_dump` (see "Everyday commands").

---

## Web UI (`app/main.py`)

**Stack:**
- **FastAPI** handles routing.
- **Jinja** renders HTML on the server.
- **HTMX** handles partial page updates.
- **Pico.css** is a small stylesheet that styles plain HTML, in light and dark mode.

There's no JavaScript build step. HTMX attributes (`hx-get`, `hx-post`, `hx-target`,
`hx-swap`) tell the browser to request a URL and swap the returned HTML fragment into the
page. A full page load returns the whole page; an HTMX request (header `HX-Request: true`)
gets back only the fragment that changed.

**Jobs page (`/`)**
- Cards for open jobs: title (links to the posting), company, location, posted date, and a **New** badge.
- **Description** expands inline. It's lazy-loaded from `/jobs/{id}/description` and
  **sanitized with `nh3`**, because it's third-party HTML; `nh3` strips scripts and event handlers.
- **Save / Mark applied / Not interested** post to `/jobs/{id}/status`, and the card
  re-renders itself. A dismissed job disappears from the list.
- **Filters:** search (title/company/location), company, relevance view (Relevant /
  Hidden: senior / Hidden: non-tech / Everything), Hide tracked. They update as you type and
  keep the URL in sync (`hx-push-url`).
- **Refresh jobs** runs `run_refresh()` (the same code as the CLI), shows the counts, then
  triggers a `jobs-changed` event that reloads the list.
- **New badges:** cookies remember your last visit, and a visit more than 1 hour after the
  previous one moves the "new since" line forward.

**Companies page (`/companies`)**: every tracked company, with a Favorite / Normal / Hide
control, local job and match counts, a health badge (OK, Failing, Dropped to 0, Many closed,
No local jobs), notes, per-company Refresh and Pause, and an "add by hand" form.
The Jobs page has a matching filter: all but hidden (default), favorites only, include hidden.

**Applications page (`/applications`)**
- Status tabs show counts, with Dismissed kept separate from "All".
- The table has the job (links to the posting), a status dropdown, the applied date
  (set automatically on "applied"), and notes (saved when the field loses focus).
- A **Posting closed** badge appears when a job you're tracking disappears from the company's board.
- **Remove** stops tracking a job, and it goes back to the Jobs list.

---

## Everyday commands (PowerShell, from the project root)

```powershell
# Database
docker compose up -d          # start Postgres in the background
docker compose ps             # is it running / healthy?
docker compose logs db        # Postgres logs
docker compose down           # stop (data is KEPT)
docker compose down -v        # stop AND DELETE all data (careful)
docker compose exec db psql -U jobs -d jobs   # SQL shell inside the container

# Python env
.\.venv\Scripts\Activate.ps1                  # activate venv (then drop the .\.venv\Scripts\ prefix)
pip install -r requirements.txt

# Data
python -m app.seed_companies  # load/update the Treasure Valley company list (safe to re-run)
python -m app.refresh         # pull all active companies' jobs into Postgres
python -m app.refresh --company micron   # just one company (name contains "micron")
python -m app.relevance       # relabel all stored jobs after editing relevance rules
python -m app.radar           # discovery radar: Adzuna -> untracked companies -> ATS detection
python -m app.radar --detect-only   # just (re)detect ATS, no Adzuna requests

# Backup (applications + companies added from the UI exist only in the database)
docker compose exec db pg_dump -U jobs jobs > backup.sql

# Web UI
uvicorn app.main:app --reload   # then open http://localhost:8000 (Ctrl+C to stop)

# Tests
python -m pytest              # location + relevance rules (fast, no DB needed)

# Migrations
alembic revision --autogenerate -m "describe change"   # after editing models.py
alembic upgrade head          # apply all pending migrations
alembic downgrade -1          # undo the last migration
alembic current               # which migration the DB is on
alembic check                 # do models and DB match? ("No new upgrade operations" = yes)
```

**Connecting the VS Code PostgreSQL extension:** host `localhost`, port `5432`, user
`jobs`, password `jobs`, database `jobs`, SSL off.

### Workflow: changing the schema
1. Edit `app/models.py`.
2. `alembic revision --autogenerate -m "..."`
3. **Read the generated file in `alembic/versions/`** before applying it (see gotchas).
4. `alembic upgrade head`, then `alembic check`.
5. Commit the model change and the migration file together.

---

## Gotchas

- **Duplicate CHECK constraints from autogenerate.** Alembic writes an enum-backed column's
  CHECK constraint twice: once from `sa.Enum(..., create_constraint=True)` and again as
  separate `sa.CheckConstraint(...)` lines. Postgres then fails with "constraint already
  exists". Fix: in the migration, set `create_constraint=False` on the `sa.Enum(...)` and
  delete the `CheckConstraint` whose name is just `'source'`/`'status'`. Keep the
  `op.f('ck_...')` one.
- **Failed migrations roll back.** Postgres supports transactional DDL, so a failed migration
  leaves nothing half-created. Fix the file and rerun.
- **`docker` not recognized in the terminal.** Docker Desktop is installed per-user
  (`%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin`). Restart VS Code after installing
  so terminals pick up the PATH. The Docker Desktop app must also show "Engine running".
- **Port 5432 already in use.** That means another Postgres is running locally. Change the
  host side of the compose port mapping (e.g. `"5433:5432"`) and update `DATABASE_URL`.

---

## Postgres vs SQL Server (quick translation)

| | Postgres | SQL Server |
|---|---|---|
| GUI client | pgAdmin, DBeaver, VS Code extension | SSMS |
| CLI client | `psql` | `sqlcmd` |
| Dialect | standard SQL + extras | T-SQL |
| Auto-increment | `SERIAL` / `GENERATED AS IDENTITY` | `IDENTITY(1,1)` |
| First N rows | `LIMIT 10` | `TOP 10` |
| Upsert | `INSERT ... ON CONFLICT DO UPDATE` | `MERGE` |
| JSON | `JSONB` (indexed, queryable) | `NVARCHAR` + JSON functions |

---

## Glossary

- **ATS:** Applicant Tracking System (Greenhouse, Lever, Ashby, Workday). Hosts companies' job boards.
- **Connector:** one class per ATS. It fetches that ATS's JSON and converts it into our standard `Job` shape.
- **Normalization:** mapping each ATS's differently named fields onto one schema.
- **Upsert:** insert the row, or update it if it already exists (matched on a unique key).
- **Idempotent:** doing it twice has the same result as doing it once.
- **Migration:** a versioned script that changes the schema (create table, add column, ...).
- **ORM:** Object-Relational Mapper. Lets you work with rows as Python objects.

---

## Roadmap

- [x] Postgres in Docker, schema, first Alembic migration
- [x] Seed list of Treasure Valley companies (`app/seed_companies.py`)
- [x] Greenhouse + Workday connectors, `refresh` command
- [x] Treasure Valley location filter before storage
- [x] `company_name` on jobs; relevance labels (match/senior/non_tech) + `job_matches` view + tests
- [x] UKG + Paylocity connectors, ATS detector, 20 more companies (28 total)
- [x] Oracle Recruiting Cloud connector (Albertsons)
- [ ] More connectors, chosen from the Radar page's payoff table (2+ real companies each, and
      allowed by robots.txt). As of the first radar run, nothing qualified: NEOGOV and iCIMS
      block bots, ADP's matches were wrong companies, Phenom is a front end over other ATSes.
- [ ] Lever connector (no Treasure Valley companies found on it yet)
- [ ] Store `description_text` + structured `qualifications` + full-text search (see schema note)
- [x] Web UI: Jobs page (filters, new badges, save/applied/dismiss, refresh) + Applications tracker
- [x] Companies page: health, favorite/hidden, notes, add by hand
- [x] Discovery radar (Adzuna) with coverage metric, ATS detection, add & track
- [ ] Scheduled refresh (not wanted yet: clicking Refresh is part of the fun)
- [ ] Resume tailoring: suggested bullet rewrites, human-approved, versioned per job.
      Treat job descriptions as **untrusted input** (prompt injection).
- [ ] Tests, GitHub Actions, deploy

## Conventions for Claude

- The user does all git operations. Don't commit, push, or offer to.
- Keep code simple and explainable. This project is also interview material.
- Always review autogenerated migrations before applying them.
- Only use public or official APIs. No scraping of sites whose terms forbid it.
