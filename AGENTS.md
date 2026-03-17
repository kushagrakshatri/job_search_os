# Job Search OS — AGENTS.md

> This file is the single source of truth for this project.
> Read it at the start of every Codex session before writing any code.

---

## What this is

A personal job search automation tool for Kushagra — MS AI Engineering, SJSU, graduating May 2026.
No web UI. Telegram-only interface. Two modules built sequentially.

**Module 1:** python-jobspy scraper + LLM-based scorer + Telegram alerts
**Module 2:** Per-role pipeline tracker with state machine + Telegram command interface

---

## Candidate profile (used in scoring prompt)

- **Name:** Kushagra
- **Degree:** MS AI Engineering, SJSU — May 2026
- **GPA:** 3.50
- **Immigration:** F-1 OPT → STEM OPT eligible. Requires E-Verify employer. No clearance or citizenship eligibility.
- **Experience 1 — F5 Networks:** LangGraph multi-agent orchestration, MCP servers, RAG eval pipelines (Ragas + LangFuse), Faithfulness 0.74, Context Precision 0.68, P95 latency reduction
- **Experience 2 — ASML:** OpenTelemetry observability pipeline, distributed tracing, MTTR reduction
- **Target roles:** AI Engineer, Applied ML Engineer, AI Platform Engineer, MLE, SWE (AI-focused), Agentic AI Systems Engineer, AI Infrastructure Engineer
- **Open to:** relocation, any industry, any company size

---

## Repository structure

```
job-search-os/
├── AGENTS.md
├── pyproject.toml
├── .env.example
├── config.yaml
├── alembic/
│   └── versions/
├── jobsearch/
│   ├── __init__.py
│   ├── config.py          # pydantic-settings, loads .env
│   ├── db.py              # SQLAlchemy engine + session factory
│   ├── models.py          # ORM models: Job, (later) PipelineRole
│   ├── prompts.py         # SCORER_SYSTEM_PROMPT constant — edit here to recalibrate
│   ├── scraper.py         # jobspy wrapper + title pre-filter + dedup
│   ├── scorer.py          # LLM scoring engine (GPT via OpenAI SDK)
│   ├── alerts.py          # Telegram alert formatter + sender
│   ├── scheduler.py       # APScheduler + run_pipeline() orchestration
│   └── bot/
│       ├── __init__.py
│       ├── main.py        # Application builder + handler registration
│       └── commands.py    # Telegram command handlers
└── scripts/
    ├── seed.py            # Insert 5 fake jobs for local dev/testing
    └── audit_scores.py    # Print scored jobs with breakdown (calibration tool)
```

---

## Tech stack

| Concern | Library |
|---|---|
| Package manager | uv |
| Scraping | python-jobspy |
| Telegram bot | python-telegram-bot v20 (async, job-queue extras) |
| Scheduler | APScheduler with SQLAlchemyJobStore |
| Database | SQLite + SQLAlchemy + Alembic |
| Scoring | Codex backend Responses API → configurable via SCORER_MODEL (default: gpt-5.4, via Codex auth token) |
| Logging | structlog (JSON to file + stderr) |
| Config | pydantic-settings (reads .env + config.yaml) |

---

## Environment variables (.env)

```
TELEGRAM_TOKEN=
CHAT_ID=
SCORER_MODEL=gpt-5.4
DAILY_SCORE_LIMIT=150
SCRAPE_SCHEDULE_MORNING=0 8 * * 1-5
SCRAPE_SCHEDULE_EVENING=0 18 * * 1-5
SCHEDULER_TIMEZONE=America/Los_Angeles
```

> Note: Scoring reads the Codex auth token from `~/.codex/auth.json`. Do not hardcode secrets anywhere.

---

## config.yaml schema

```yaml
scraper:
  sources: [linkedin, indeed, glassdoor, zip_recruiter]
  search_terms:
    - "AI Engineer"
    - "Applied ML Engineer"
    - "LLM Engineer"
    - "AI Platform Engineer"
    - "Agentic AI Engineer"
    - "ML Engineer"
  locations: ["United States"]
  remote_only: false
  results_wanted_per_source: 25

scoring:
  growth_default: 7
  e_verify_employers:
    - google
    - meta
    - amazon
    - microsoft
    - apple
    - nvidia
    - openai
    - anthropic
    - databricks
    - snowflake
    - stripe
    - airbnb
    - uber
    - linkedin
    - salesforce

alerts:
  tier_threshold: B
  chat_id: "${CHAT_ID}"
```

---

## Data model — Job table

```
id                    str        uuid4, primary key
title                 str
company               str
location              str
is_remote             bool
url                   str        unique index
url_hash              str        sha256(url)[:16], indexed
slug_hash             str        sha256(company_slug + "|" + title_slug)[:16], indexed
source                str        linkedin / indeed / glassdoor / zip_recruiter
scraped_at            datetime
jd_text               str        cleaned, max 4000 chars

knocked_out           bool       default False
knockout_reason       str        nullable

score_tech_stack      int        0–25,  nullable until scored
score_role_fit        int        0–20,  nullable until scored
score_work_auth       int        0–20,  nullable until scored
score_interviewability int       0–15,  nullable until scored
score_ai_signal       int        0–10,  nullable until scored
score_growth          int        0–10,  nullable until scored
total_score           int        sum of above, nullable until scored
tier                  str        A / B / C / skip

score_breakdown       JSON       full LLM response including rationale strings
llm_scored            bool       default False
llm_scored_at         datetime   nullable

alerted_at            datetime   nullable
```

---

## Scoring system — LLM only, no regex

All semantic matching is done via a single LLM call per job using the Codex backend Responses API.
Do not use regex, keyword lists, or rule engines for any scoring or knockout logic.
The only exception is the title pre-filter in scraper.py — a coarse volume filter before scoring.

### Model and client setup

```python
from jobsearch.codex_client import complete

result_text = complete(system_prompt, user_message)
```

### API call pattern

```python
result_text = complete(SCORER_SYSTEM_PROMPT, user_message)
result = json.loads(result_text)
```

The Codex backend returns Responses-style output, and the client extracts the assistant text from the final message item.

### Knockout filter (detected by LLM)

Return `knocked_out: true` if any of the following are present — even implicitly:
- Sponsorship explicitly or implicitly denied ("will not sponsor", "must be authorized without employer assistance")
- US citizenship required
- Any security clearance required
- Export control restrictions (ITAR, EAR)
- Start date incompatible with May 2026 graduation

### Scoring dimensions

| Dimension | Max | Criteria |
|---|---|---|
| tech_stack | 25 | Overlap with: LangGraph, LangChain, agentic systems, RAG, Ragas, LangFuse, OpenTelemetry, distributed tracing, Python, FastAPI, vector DBs. Partial credit for adjacent LLM/MLOps skills. |
| role_fit | 20 | Full for AI Engineer / Applied ML / Agentic AI / AI Platform. Partial for AI-focused SWE. Low for generic SWE with no AI context. |
| work_auth | 20 | Full if E-Verify explicit or known large tech employer. Partial for mid-size no-mention. Low for small startups with visa-avoidance signals. |
| interviewability | 15 | Full for new grad / entry-level / 0–2 YOE. Partial for 2–3 YOE. Low for senior/5+ YOE. |
| ai_signal | 10 | Full for core AI/LLM product companies or ML platform teams. Partial for AI-as-feature. Low for AI-as-buzzword. |
| growth | 10 | Default 7. Adjust for team quality, tech sophistication, portfolio upside signals. |

### Tier assignment

| Score | Tier |
|---|---|
| ≥ 75 | A |
| 60–74 | B |
| 40–59 | C |
| < 40 or knocked_out | skip |

Only Tier A and B trigger Telegram alerts.

### LLM output schema

```json
{
  "knocked_out": false,
  "knockout_reason": null,
  "scores": {
    "tech_stack": 0,
    "role_fit": 0,
    "work_auth": 0,
    "interviewability": 0,
    "ai_signal": 0,
    "growth": 0
  },
  "rationale": {
    "tech_stack": "one sentence",
    "role_fit": "one sentence",
    "work_auth": "one sentence",
    "interviewability": "one sentence",
    "ai_signal": "one sentence",
    "growth": "one sentence"
  },
  "total_score": 0,
  "tier": "A"
}
```

### Scorer behavior

- One Codex backend API call per job
- On JSONDecodeError or RuntimeError: set llm_scored=False, log warning, do not raise
- Respect DAILY_SCORE_LIMIT — abort batch with Telegram warning if limit exceeded
- Retry queue: nightly re-attempt of all jobs where llm_scored=False and scraped_at within last 7 days

---

## Scraper behavior

- Sources and search_terms from config.yaml
- Title pre-filter (only place simple string matching is used): keep if title (lowercased) contains any of [ai, ml, machine learning, llm, nlp, engineer, swe, software]
- Dedup: skip job if url_hash OR slug_hash already exists in DB
- Clean jd_text: strip HTML, collapse whitespace, truncate to 4000 chars
- Return only newly inserted Job objects

---

## Telegram alert format

```
🔴 TIER A  |  84pts
━━━━━━━━━━━━━━━━━━━━━
AI Engineer — Acme Corp
📍 San Francisco, CA  (Remote OK)
🔗 linkedin.com/jobs/...

Tech Stack     ████████░░  22/25
Role Fit       ████████░░  18/20
Work Auth      ████████░░  16/20  ✅
Interviewable  ████████░░  12/15
AI Signal      ██████████  10/10
Growth         ███████░░░   7/10
━━━━━━━━━━━━━━━━━━━━━
[ ➕ Add to pipeline ]   [ 🔕 Dismiss ]
```

- MarkdownV2 parse mode
- Progress bars: Unicode blocks scaled to 10 chars (█ filled, ░ empty)
- ✅ suffix on Work Auth line if score_work_auth >= 16
- Inline keyboard: "➕ Add to pipeline" (callback: add_pipeline:{job_id}), "🔕 Dismiss" (callback: dismiss:{job_id})

---

## Scheduler

- APScheduler AsyncIOScheduler with SQLAlchemyJobStore (reuses same SQLite DB)
- Two CronTriggers from config: morning (default 8am PT weekdays) + evening (6pm PT weekdays)
- run_pipeline(): fetch_all → score_pending → send_pending_alerts → log summary
- Starts inside bot/main.py before run_polling()

---

## Module 1 Telegram commands

| Command | Behavior |
|---|---|
| /ping | Reply "pong 🟢" |
| /scrape_now | Run full pipeline, reply with summary (X scraped, Y scored, Z alerted) |
| /stats | Today's counts: scraped, Tier A, Tier B, last run time |

---

## Module 2 additions (Weeks 5–6 — do not implement during Module 1)

```
PipelineRole table:
id                str        uuid4
job_id            str        FK → jobs.id
state             str        Discovered / Applied / Human Touched / Screen / Loop / Closed
state_entered_at  datetime
state_history     JSON       list of {state, entered_at, note}
danger_flag       bool       default False
danger_reason     str        nullable
contacts          JSON       list of {name, title, linkedin, added_at}
outreach_log      JSON       list of {type, note, logged_at}
closed_reason     str        nullable (offer / reject / ghost / withdrawn)
```

Module 2 commands: /pipeline, /role, /update, /danger, /add_contact, /log, /close

---

## Hard rules — never violate

- No web UI or REST API
- No regex or keyword lists for scoring or knockout logic
- No scoring transport other than `jobsearch.codex_client.complete()` for scoring
- No Anthropic SDK — use the Codex backend client only
- No OpenAI SDK for scoring
- No heavy ML libraries (torch, transformers) — this is orchestration, not training
- Scoring prompt lives only in jobsearch/prompts.py — never inline it elsewhere
- Module 2 tables and commands are not implemented during Module 1 sprints

---

## Done-when convention

Every prompt ends with a "Done when:" block. Do not mark a task complete until that check passes exactly as written.
