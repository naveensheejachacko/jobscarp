# Job Search Automation Assistant

Turns Cutshort/Instahyre job-alert emails into a scored, prioritized, review-ready
pipeline — through the official Gmail API only. It never scrapes, logs into, or
mass-applies to any job site, and it **never submits an application**. Every
apply decision stays manual; this tool exists to help you find and prioritize
the right jobs faster, not to spam recruiters.

## 1. Architecture

```
Gmail (OAuth, read-only)
   │  GMAIL_QUERY per source
   ▼
EmailFetcher        (app/services/gmail.py)    — dedups by Gmail message id
   ▼
JobExtractor        (app/services/extractor.py) — per-source parser (Cutshort/Instahyre)
   ▼
Normalizer          (app/services/normalizer.py) — canonical salary/experience/location/skills
   ▼
Deduplicator        (app/services/deduplicator.py) — url → company+title → +location → fuzzy
   ▼
Matcher             (app/services/matcher.py)   — deterministic weighted score (0-100)
   ▼
AI Analyzer         (app/services/ai.py)        — LLM qualitative narrative (Gemini/Anthropic)
   ▼
Application Message (app/services/ai.py)        — only for jobs scoring ≥ 80
   ▼
Database            (Postgres via SQLAlchemy)
   ▼
Google Sheets sync  (app/services/sheets.py)
   ▼
FastAPI review queue (app/api/)
   ▼
MANUAL APPLICATION (you)
```

**Score authority split** — the most important design decision in this project:
`match_score` is always computed **deterministically** in `matcher.py` from
parsed structured fields (skills, experience, salary, etc.), using the exact
weights below. The LLM only adds qualitative narrative (why it's a good/bad
fit, red flags, a draft message) on top of that score — it never overrides it.
This keeps scoring testable, explainable, and immune to an LLM inventing or
forgetting your actual experience.

### Matching weights

| Factor | Weight |
|---|---|
| Technical skill match | 30% |
| Experience match | 15% |
| Role/title match | 10% |
| Salary match | 15% |
| Backend relevance | 10% |
| Cloud/infra match | 5% |
| Database match | 5% |
| Company/role quality | 5% |
| Location/work mode | 5% |

Priority: 🔥 90-100 HIGH · 🟢 80-89 STRONG · 🟡 70-79 CONSIDER · ⚪ 60-69 LOW · 🔴 <60 SKIP.

A title saying "Senior" is never auto-penalized — only the actual stated
experience requirement affects the score.

## 2. Project structure

```
job-automation/
├── app/
│   ├── main.py                 # FastAPI app + scheduler lifespan
│   ├── config.py                # Settings (.env) + profile.yaml loader
│   ├── db.py                    # SQLAlchemy engine/session
│   ├── api/                     # jobs.py, applications.py, health.py
│   ├── models/                  # job.py, application.py (SQLAlchemy ORM)
│   ├── schemas/                 # job.py (Pydantic request/response/LLM schemas)
│   ├── services/                # gmail, extractor, normalizer, deduplicator,
│   │                             # matcher, ai, sheets, notifier
│   └── workers/
│       └── job_processor.py     # orchestrates the full pipeline + scheduler
├── profile.yaml                  # your resume facts — the ONLY source the matcher/AI use
├── tests/                         # 84 tests, fixtures under tests/fixtures/
├── migrations/                    # Alembic
├── docker/                        # reserved for optional deployment extras
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── .gitignore
```

## 3. Setup

### 3.1 Prerequisites
- Python 3.12+
- Docker + Docker Compose (recommended path), or a local PostgreSQL 16 instance
- A Google account with Gmail (for the alerts) and a spare Google Sheet

### 3.2 Clone/configure
```bash
cp .env.example .env
```
Edit `.env` — see [Environment variables](#4-environment-variables) below.

Edit `profile.yaml` if any of your skills/experience/salary targets change —
the matcher and every LLM prompt read **only** this file.

### 3.3 Gmail OAuth setup
1. Go to the [Google Cloud Console](https://console.cloud.google.com/) → create
   a project (or reuse one).
2. Enable the **Gmail API** and the **Google Sheets API** for that project.
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
   - Application type: **Desktop app**.
   - Download the JSON — save it as `credentials.json` in the project root.
4. Go to **OAuth consent screen** and add your own Gmail address as a test user
   (unless you've published the app).
5. The first time the app runs (locally or via `--once`), it opens a browser
   window for you to log in and consent. A `token.json` is then written and
   reused automatically — you won't be prompted again unless it expires.

Both `credentials.json` and `token.json` are gitignored — never commit them.

### 3.4 Google Sheets setup
1. Create a new Google Sheet (any name).
2. Copy its ID from the URL: `https://docs.google.com/spreadsheets/d/<ID>/edit`.
3. Put that ID in `.env` as `GOOGLE_SHEET_ID`.
4. No sharing step needed — the same OAuth credentials used for Gmail also
   authorize Sheets access to sheets you own (the `spreadsheets` scope is
   requested alongside `gmail.readonly`/`gmail.send`).
5. Leave `GOOGLE_SHEET_ID` blank to skip Sheets sync entirely (jobs still land
   in Postgres and are fully usable via the API).

### 3.5 LLM API setup
The LLM is fully abstracted (`app/services/ai.py`) — pick one:

- **Gemini (default, free tier)**: get a key at [Google AI Studio](https://aistudio.google.com/app/apikey),
  set `GEMINI_API_KEY` in `.env`. `LLM_PROVIDER=gemini` is the default.
- **Anthropic**: set `ANTHROPIC_API_KEY` and `LLM_PROVIDER=anthropic` in `.env`.
  Useful if you have a company/work Claude key — no code changes needed.

If the configured provider is unreachable or misconfigured, the pipeline
degrades gracefully to deterministic-only scoring (no crash) — see
`app/services/ai.py::safe_analyze_job`.

## 4. Environment variables

All in `.env.example`, copy to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `GMAIL_CREDENTIALS_PATH` / `GMAIL_TOKEN_PATH` | OAuth client secret / cached token paths |
| `GMAIL_QUERY_CUTSHORT` / `GMAIL_QUERY_INSTAHYRE` | Gmail search queries per source — edit freely |
| `GOOGLE_SHEET_ID` | Target spreadsheet for the review sheet (blank = skip Sheets sync) |
| `LLM_PROVIDER` | `gemini` or `anthropic` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Gemini credentials/model |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Anthropic credentials/model |
| `NOTIFY_MIN_SCORE` | Score threshold for an email notification (default 90) |
| `NOTIFY_EMAIL_TO` | Where notifications are sent (via Gmail API) |
| `PROCESS_INTERVAL_MINUTES` | Pipeline run cadence (default 30) |
| `LOG_LEVEL` | Python logging level |
| `PROFILE_PATH` | Path to `profile.yaml` |

## 5. Running locally (no Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Point DATABASE_URL at a local Postgres, then:
alembic upgrade head

# One-off manual pipeline run (fetches real Gmail, needs credentials.json):
python -m app.workers.job_processor --once

# Or start the API + scheduler:
uvicorn app.main:app --reload
```
Then open http://localhost:8000/docs for interactive API docs, or
http://localhost:8000/health.

## 6. Running with Docker

```bash
cp .env.example .env        # fill in your keys
# place your real credentials.json in the project root (see 3.3)
touch token.json             # will be populated on first Gmail auth
docker compose up --build
```
This starts Postgres + the app, runs `alembic upgrade head` automatically, and
serves the API on `http://localhost:8000`. Postgres itself is reachable from
the host on `localhost:5433` (mapped to avoid clashing with a local Postgres
on the default 5432) if you want to inspect it with a DB client.

> Note: the Gmail OAuth browser-consent flow needs a local browser the first
> time. If running purely in Docker/headless, run `python -m app.workers.job_processor --once`
> locally once first to generate `token.json`, then mount that file into the
> container (already wired up in `docker-compose.yml`).

Verified end-to-end during development: `docker compose up --build` boots the
full stack, Alembic applies the schema against real Postgres, and
`curl localhost:8000/health` / `/jobs` / `/jobs/stats` all return 200.

## 7. Configuring job alerts

Edit `GMAIL_QUERY_CUTSHORT` / `GMAIL_QUERY_INSTAHYRE` in `.env` — these are
plain Gmail search queries, e.g.:
```
GMAIL_QUERY_CUTSHORT=from:(cutshort.io) newer_than:7d
GMAIL_QUERY_INSTAHYRE=from:(instahyre.com) subject:(job OR match)
```
No code changes needed — the scheduler picks up the new query on its next run.

## 8. Adding a new job source later (LinkedIn, Naukri, ...)

1. Add a `GMAIL_QUERY_<SOURCE>` entry to `.env` / `Settings` (`app/config.py`).
2. Write a new parser class in `app/services/extractor.py` implementing
   `BaseExtractor.parse(raw: RawEmail) -> ExtractedJob | ExtractionFailure`,
   and register it in the `EXTRACTORS` dict.
3. Add the new source to `_sources()` in `app/workers/job_processor.py`.

Nothing else in the pipeline (normalizer, deduplicator, matcher, AI, API)
needs to change — they all operate on the source-agnostic `NormalizedJob`/`Job`
shapes.

## 9. API reference

| Endpoint | Purpose |
|---|---|
| `GET /jobs?min_score=&source=&status=` | Filterable job list |
| `GET /jobs/{id}` | One job |
| `GET /jobs/high-priority` | Jobs scoring 80+ |
| `GET /jobs/stats` | Counts by status/priority/source, average score |
| `POST /jobs/{id}/analyze` | Re-run deterministic scoring + LLM analysis |
| `POST /jobs/{id}/generate-message` | Generate an application message (score ≥ 80 only) |
| `PATCH /jobs/{id}/status` | Manually move a job through the status lifecycle |
| `GET /applications` | Jobs you've acted on (shortlisted → offer/rejected) |
| `GET /applications/follow-ups` | Jobs with a due follow-up date |
| `GET /health` | Liveness check |

No endpoint — anywhere in this system — submits an application. `PATCH
/jobs/{id}/status` only records what *you* did elsewhere.

## 10. Test results

```
84 passed in 2.77s
```
Covering: extraction (both sources + malformed-email fallback), normalization
(salary/experience/location/skills parsing), deduplication (all 4 strategies),
matcher scoring (the exact worked examples from the spec — Senior
FastAPI/Postgres → 90%+, Django backend → 85%+, Django+React full-stack →
60-75%, Django 6+yrs → penalized for experience, "Senior" alone never
penalized), LLM response schema validation (valid/malformed/hallucinated-skill
filtering), Gmail message dedup, the full pipeline orchestration (fake
Gmail/LLM/notifier, no network), the scheduler (registers without firing
immediately), Sheets sync (upsert/sort), and all API endpoints/filters/status
transitions. Run them yourself:
```bash
pip install -r requirements.txt
pytest -v
```

## 11. Product boundaries (by design)

- No scraping, no logins, no CAPTCHA/anti-bot bypass, no rate-limit evasion —
  Gmail and Sheets are accessed only through their official APIs.
- No auto-apply, anywhere, ever. Every message this tool drafts is a suggestion
  you review and send yourself.
- The candidate profile (`profile.yaml`) is the only source of truth for your
  skills/experience — the LLM is explicitly instructed never to assume or
  invent anything beyond it, and any hallucinated skill in its response is
  filtered out in code before it reaches a job record or a message.
