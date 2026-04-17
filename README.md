# CareerAI-as-a-Service (CaaS)

> **SC4052 Cloud Computing — Topic 8: X-as-a-Service**  
> A cloud-native, API-first career intelligence platform built on FastAPI, OpenAI, Databricks Delta Lake, and Render.
>
> Juannisa Putri Sunarya
> Muhammad Wisnu Darmawan

---

## 1. Project Overview

**CareerAI-as-a-Service (CaaS)** turns complex AI-powered career tasks into simple, stateless HTTP calls — anyone with an internet connection can score their resume, analyse a job posting, or generate a cover letter by calling a REST endpoint, exactly like calling any cloud API.

The project is framed around **Topic 8: X-as-a-Service**. The "X" here is *career intelligence* — a domain that is currently slow (manual review), expensive (professional resume writers, career coaches), and inaccessible (most tools require subscriptions or proprietary formats). CaaS democratises this by packaging five distinct capabilities as independent, composable microservices behind a clean REST API.

### What the app does

| Feature | Endpoint | Description |
|---|---|---|
| Job Analysis | `POST /api/v1/job-analysis` | Breaks down a job description into skills, keywords, seniority, industry, and cultural signals |
| ATS Scoring | `POST /api/v1/ats-score` | Scores a resume against a job description on a 1–10 scale with detailed feedback and a crowd-sourced percentile rank |
| Resume Optimisation | `POST /api/v1/fine-tune` | Returns a fully rewritten, ATS-optimised version of the resume in Markdown |
| Cover Letter Generation | `POST /api/v1/cover-letter` | Generates a tailored, ATS-friendly cover letter from resume + job description |
| Market Intelligence | `GET /api/v1/market-trends` | Returns aggregated, anonymised market signals from all past submissions: top skills, keyword trends (Delta time travel), remote-work breakdown, experience distribution, and seniority split |

A web frontend (HTML/CSS/Vanilla JS) provides a point-and-click interface to all features and includes a collapsible **API Explorer** panel on every results page, exposing the raw JSON request and response to demonstrate the SaaS API model.

---

## 2. The Dual-Utility Design

This project implements the **crowdsourcing / dual-utility** principle described in the assignment brief: *"every unit of contributed human or computational effort yields value for two separate objectives simultaneously."*

```
                        User submits a job description
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
    UTILITY 1 (Individual)   UTILITY 2 (Collective)   Databricks Delta
    ─────────────────────    ────────────────────────  ─────────────────
    AI analyses the JD       Anonymised signals are    MERGE upsert into
    and returns structured   extracted (skills,        skill_counts,
    insight, ATS keywords,   keywords, industry,       keyword_counts,
    and candidate advice     seniority, remote type)   job_submissions,
    instantly to the user    and written to Delta      ats_scores tables
                             tables silently in the
                             background
```

Every job analysis call therefore serves two users at once:
- **The person who submitted it** gets immediate AI-powered feedback.
- **Every future user** benefits from a richer, more accurate Market Trends dashboard because one more data point was added to the collective pool.

No personally identifiable information is ever stored. Only aggregate signals (skill names, keyword strings, categorical labels) flow into Databricks.

---

## 3. Live Deployment

| Resource | URL |
|---|---|
| **Web UI** | `https://careerforge-y7lm.onrender.com` |
| **Interactive API Docs (Swagger)** | `https://careerforge-y7lm.onrender.com/docs` |

> **Note for markers:** The app is hosted on Render's free tier. If it has been idle for more than 15 minutes, the first request will take up to 60 seconds to wake the instance. Subsequent requests respond normally. This is expected behaviour on free-tier hosting and is documented as a known limitation in [Section 13](#13-architectural-decisions--tradeoffs).

---

## 4. Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                             │
│  Web Browser (HTML/CSS/JS)   │   External API consumers         │
│  fetch() calls to /api/v1/*  │   curl, Postman, Python scripts  │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────────────┐
│                   RENDER (Cloud Hosting)                        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    FastAPI Application                   │   │
│  │                       (main.py)                          │   │
│  │                                                          │   │
│  │   ┌──────────────┐        ┌──────────────────────────┐  │   │
│  │   │  Web Routes  │        │      REST API Routes      │  │   │
│  │   │ (web_routes) │        │      (api_routes)         │  │   │
│  │   │  Jinja2 HTML │        │  /ats-score /fine-tune    │  │   │
│  │   │  templates   │        │  /cover-letter            │  │   │
│  │   └──────────────┘        │  /job-analysis            │  │   │
│  │                           │  /market-trends           │  │   │
│  │                           └──────────┬───────────────┘  │   │
│  │                                      │                   │   │
│  │   ┌──────────────────────────────────▼─────────────┐    │   │
│  │   │                 Utils / Services                │    │   │
│  │   │  openai_service.py    analytics_service.py      │    │   │
│  │   │  resume_processing.py                           │    │   │
│  │   └──────────┬───────────────────────┬─────────────┘    │   │
│  └──────────────│───────────────────────│──────────────────┘   │
└─────────────────│───────────────────────│────────────────────── ┘
                  │                       │
       ┌──────────▼──────┐     ┌──────────▼──────────────┐
       │   OpenAI API    │     │  Databricks Free Edition │
       │  (gpt-5.4-nano)  │     │   Delta Lake (SQL)       │
       │  ats_score_rule │     │   skill_counts           │
       │  resume_opt_rule│     │   keyword_counts         │
       │  cover_ltr_rule │     │   job_submissions        │
       │  job_anlys_rule │     │   ats_scores             │
       └─────────────────┘     └──────────────────────────┘
```

### Request Lifecycle (stateless by design)

```
1. Client sends POST /api/v1/ats-score
   Body: { "resume_text": "...", "jd_text": "..." }
   
2. FastAPI validates request body against ATSScoreRequest Pydantic model
   → 422 Unprocessable Entity if validation fails (automatic)

3. Route handler calls get_ats_score(resume_text, jd_text)
   → openai_service.py sends prompt + text to gpt-5.4-nano
   → Returns (score: float, feedback: str)

4. Route handler calls record_ats_score(score) [background, non-blocking]
   → analytics_service.py upserts into Databricks ats_scores table
   → Wrapped in try/except — failure here NEVER blocks the response

5. Route handler calls get_ats_benchmark(score)
   → Computes percentile rank from ats_scores table
   → Returns None values gracefully if Databricks is unavailable

6. FastAPI validates response against ATSScoreResponse Pydantic model
   → Returns JSON: { score, feedback, benchmark_avg, benchmark_percentile, benchmark_total }
```

No data is stored in server memory between requests. Every request is fully self-contained.

---

## 5. Technology Stack

| Layer | Technology | Why |
|---|---|---|
| **Backend framework** | FastAPI (Python 3.11) | Async, auto-generates OpenAPI/Swagger docs from type hints, faster than Flask |
| **WSGI server** | Uvicorn + Gunicorn | Production-grade ASGI server; Render uses `$PORT` from env |
| **AI provider** | OpenAI `gpt-5.4-nano` | Best price-to-quality ratio for structured text generation tasks |
| **PDF parsing** | PyPDF2 | Lightweight, pure-Python PDF text extraction |
| **Web scraper** | Jina Reader | Fetch and extract clean text from job posting URLs |
| **Cloud data store** | Databricks Free Edition (Delta Lake) | Delta tables with MERGE, TIME TRAVEL, schema evolution — real enterprise-grade cloud database |
| **App hosting** | Render (free tier) | GitHub auto-deploy; Singapore region; |
| **Version control** | GitHub |
| **Frontend** | HTML5 + CSS3 + Vanilla JS | No build step required; `fetch()` API for async calls |
| **Templating** | Jinja2 | Server-side rendering for initial page loads |
| **Document export** | python-docx | Generates `.docx` files from Markdown in memory |
| **Request validation** | Pydantic v2 | Enforces schema at runtime; powers Swagger UI |
| **Config management** | python-dotenv | Loads `.env` locally; Render injects env vars in production |
| **Session middleware** | itsdangerous (Starlette SessionMiddleware) | Flash messages for web UI |

---

## 6. API Reference

All endpoints are documented interactively at `/docs`. 

### Authentication

No authentication is required. This is a demonstration platform for the SC4052 module.

### Base URL

```
https://careerforge-y7lm.onrender.com
```

---

## 7. Repository Structure

```
CC_GrpProj/
├── .env.example                    # Template for environment variables
├── .gitignore
├── config.py                       # All config loaded from environment variables
├── main.py                         # FastAPI app entrypoint
├── render.yaml                     # Infrastructure-as-code for Render
├── requirements.txt
│
├── api/
│   ├── __init__.py
│   ├── schemas.py                  # All Pydantic request/response models
│   └── routes/
│       ├── __init__.py
│       ├── api_routes.py           # REST API endpoints (/api/v1/*)
│       └── web_routes.py           # HTML page routes (/, /upload, /tools, etc.)
│
├── utils/
│   ├── __init__.py
│   ├── openai_service.py           # All OpenAI API calls
│   ├── analytics_service.py        # All Databricks operations
│   └── resume_processing.py        # PDF text extraction
│
├── prompts/
│   ├── ats_score_rule.txt          # System prompt for ATS scoring
│   ├── resume_optimization_rule.txt
│   ├── cover_letter_rule.txt
│   └── job_analysis_rule.txt
│
├── templates/                      # Jinja2 HTML templates
│   ├── index.html
│   ├── upload.html
│   ├── tools.html
│   ├── ats_score.html
│   ├── optimization_report.html
│   ├── cover_letter.html
│   ├── job_analysis.html
│   ├── dashboard.html
│   └── about.html
│
├── static/
│   ├── style.css
│   └── assets/images/
│
└── uploads/                        # Temporary upload directory (git-ignored)
```

**Key design principles encoded in this structure:**

- `api/routes/` contains only HTTP concerns (parse request → call service → return response). No business logic.
- `utils/` contains all business logic. Routes import from utils, never the reverse.
- `api/schemas.py` defines all input/output contracts. Every endpoint is typed.
- `prompts/` separates AI prompts from code — prompts can be tuned without touching Python.
- `config.py` is the single source of truth for all environment variables. Nothing else reads `os.environ` directly.

---

## 8. Local Development Setup

Follow these steps exactly. Do not skip any step.

### Prerequisites

- Python 3.11
- Git installed
- A text editor (VS Code recommended)
- An OpenAI API key (get one at [platform.openai.com](https://platform.openai.com))

### Step 1 — Clone the repository

```bash
git clone https://github.com/<your-username>/CC_GrpProj.git
cd CC_GrpProj
```

### Step 2 — Create a virtual environment

```bash
# macOS / Linux
python -m venv venv
source venv/bin/activate

# Windows (Command Prompt)
python -m venv venv
venv\Scripts\activate

# Windows (PowerShell)
python -m venv venv
venv\Scripts\Activate.ps1
```

You should see `(venv)` at the start of your terminal prompt. If you do not, the virtual environment is not active — do not proceed.

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

This will take 1–3 minutes. Expected output ends with something like:
```
Successfully installed fastapi-0.115.0 uvicorn-0.30.6 openai-1.60.0 ...
```

### Step 4 — Configure environment variables

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in your values:

```env
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_MODEL=gpt-5.4-nano

DATABRICKS_HOST=https://adb-xxxxxxxxxxxxxxxx.azuredatabricks.net
DATABRICKS_TOKEN=dapixxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxxxxxxxxxxxxxxx
DATABRICKS_CATALOG=main
DATABRICKS_SCHEMA=careerai

SECRET_KEY=any-random-string-you-invent-here
```

> **Important:** The `.env` file is in `.gitignore`. It will never be committed. Never paste API keys directly into code files.

### Step 5 — Run the development server

```bash
python -m uvicorn main:app --reload --port 8000
```

Expected output:
```
INFO:     Databricks Delta Tables initialised successfully.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [xxxxx] using WatchFiles
```

If you see `WARNING: Databricks credentials not configured.` that is fine — the app runs without Databricks. Market Trends will return empty lists.

### Step 6 — Verify the app is running

Open your browser and check all three of these:

| URL | Expected |
|---|---|
| `http://localhost:8000` | Homepage loads with the CaaS landing page |
| `http://localhost:8000/docs` | Swagger UI with all 5 endpoints listed |
| `http://localhost:8000/api/v1/market-trends` | JSON response (empty lists if no Databricks) |

The `--reload` flag means the server restarts automatically when you save a Python file. You do not need to restart it manually during development.

---

## 9. Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | Your OpenAI API key. Get from [platform.openai.com](https://platform.openai.com/api-keys) |
| `OPENAI_MODEL` | ❌ | Default: `gpt-5.4-nano`. |
| `DATABRICKS_HOST` | ❌ | Full URL of your Databricks workspace, e.g. `https://adb-xxxxx.azuredatabricks.net` |
| `DATABRICKS_TOKEN` | ❌ | Databricks personal access token |
| `DATABRICKS_HTTP_PATH` | ❌ | HTTP path of your SQL Warehouse, e.g. `/sql/1.0/warehouses/xxxxx` |
| `DATABRICKS_CATALOG` | ❌ | Default: `main`. The Unity Catalog name |
| `DATABRICKS_SCHEMA` | ❌ | Default: `careerai`. The schema/database name inside the catalog |
| `SECRET_KEY` | ✅ | Any random string. Used to sign session cookies. Render can auto-generate this |

The app runs in **degraded mode** if Databricks variables are absent — all AI features work, but Market Trends returns empty data. This is by design.

---

## 10. Databricks Setup

Databricks provides the cloud data platform for the Market Intelligence feature. Follow these steps exactly.

### Step 1 — Create a free Databricks account

1. Go to [signup.databricks.com](https://signup.databricks.com)
2. Choose **Community Edition** (free, no credit card required) or **Free Trial** on AWS/Azure
3. Complete signup and wait for your workspace to be provisioned (~2 minutes)
4. Log in to your workspace

### Step 2 — Create the schema and tables

1. In the left sidebar, click **SQL Editor**
2. Run each of these statements one at a time (click the Run button after each):

**Create the schema:**
```sql
CREATE SCHEMA IF NOT EXISTS main.careerai;
```

**Create the job submissions table:**
```sql
CREATE TABLE IF NOT EXISTS main.careerai.job_submissions (
    id           BIGINT GENERATED ALWAYS AS IDENTITY,
    job_title    STRING,
    industry     STRING,
    seniority    STRING,
    submitted_at STRING
)
USING DELTA;
```

**Create the skill counts table:**
```sql
CREATE TABLE IF NOT EXISTS main.careerai.skill_counts (
    skill       STRING,
    skill_type  STRING,
    count       BIGINT
)
USING DELTA;
```

**Create the keyword counts table:**
```sql
CREATE TABLE IF NOT EXISTS main.careerai.keyword_counts (
    keyword STRING,
    count   BIGINT
)
USING DELTA;
```

**Create the ATS scores table:**
```sql
CREATE TABLE IF NOT EXISTS main.careerai.ats_scores (
    score        FLOAT,
    industry     STRING,
    seniority    STRING,
    submitted_at STRING
)
USING DELTA;
```

> **Note:** The app's `init_db()` function creates these tables automatically at startup if they don't exist. You only need to run the SQL above if `init_db()` fails (e.g. permissions issue).

### Step 3 — Get your connection credentials

1. In the left sidebar, click **SQL Warehouses**
2. Click on the **Starter Warehouse** (the one that exists by default)
3. Click the **Connection Details** tab
4. Copy the **Server Hostname** value — it looks like `adb-xxxxxxxxxxxxxxxx.azuredatabricks.net`
5. Copy the **HTTP Path** value — it looks like `/sql/1.0/warehouses/xxxxxxxxxxxxxxxx`

### Step 4 — Generate an access token

1. Click your username in the top-right corner → **Settings**
2. Click **Developer** in the left sidebar
3. Click **Manage** next to "Access tokens"
4. Click **Generate new token**
5. Give it a name: `careerai-app`
6. Set the expiry to **90 days** (or longer if your submission deadline allows)
7. Click **Generate**
8. **Copy the token immediately** — it is only shown once. If you lose it, you must generate a new one.

### Step 5 — Add credentials to your environment

Add these to your `.env` file locally, and to Render's environment variables in production:

```env
DATABRICKS_HOST=https://adb-xxxxxxxxxxxxxxxx.azuredatabricks.net
DATABRICKS_TOKEN=dapixxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxxxxxxxxxxxxxxx
```

### Step 6 — Verify the connection

With your `.env` file populated, restart the development server. Look for this line in the console:

```
Databricks Delta Tables initialised successfully.
```

Then hit `http://localhost:8000/api/v1/market-trends`. You should see a JSON response with `total_submissions: 0` and empty lists (because no data has been submitted yet). Submit a job analysis through the web UI, then call the endpoint again — counts should increment.

### Delta Lake features used in this project

| Feature | Where used | Why |
|---|---|---|
| **MERGE (upsert)** | `analytics_service.py` | Atomically increments skill/keyword counts without duplicates |
| **TIME TRAVEL** | `get_skill_trend_comparison()` | Compares current keyword counts against a Delta snapshot from 7 days ago — not possible in standard SQL |
| **Schema evolution** | `init_db()` | `ALTER TABLE ADD COLUMN` to add `remote_type`, `required_years`, `salary_range` to `job_submissions` without losing existing data |
| **USING DELTA** | All `CREATE TABLE` statements | Enables the above features; standard Parquet/CSV tables do not support them |

---

## 11. Cloud Computing Concepts Demonstrated

This section maps project features to SC4052 module concepts for assignment marking.

### SaaS Architecture (Topic 8 Core)

The entire application is structured as a Software-as-a-Service platform:
- Five distinct AI capabilities exposed as independent REST endpoints
- Any client (browser, curl, Python script, mobile app) can consume any endpoint
- No software installation required by the consumer — just HTTP
- The Swagger UI at `/docs` acts as a self-service API catalogue

### Stateless, Horizontally Scalable API Design

Every endpoint is **fully stateless**. The request body contains everything needed to process the request — no server-side sessions, no global variables, no disk files between requests. This means:
- Multiple instances of the app could run behind a load balancer with zero coordination
- Each request is independently retryable
- No user's data can "contaminate" another user's request (the critical flaw in the predecessor app, ResumeHero, which used `resume_text_global` — a global variable that would have returned wrong data under concurrent users)

### Infrastructure-as-Code

The `render.yaml` file defines the complete deployment infrastructure declaratively. A new deployment can be reproduced exactly by running `render up` — no manual configuration steps required.

### Cloud-Native Data Platform (Databricks Delta Lake)

Databricks provides enterprise-grade features beyond what a traditional SQL database offers:

1. **Delta format (ACID transactions)**: The `MERGE` statements in `analytics_service.py` run as atomic transactions — if two users submit job analyses simultaneously, the skill counts increment correctly with no race conditions.

2. **Time Travel**: The `get_skill_trend_comparison()` function queries the `keyword_counts` table at two points in time — now, and 7 days ago — using Delta's `VERSION AS OF` or `TIMESTAMP AS OF` syntax. This is impossible in standard SQL and powers the "trending skills" feature.

3. **Schema Evolution**: New columns (`remote_type`, `required_years`, `salary_range`) were added to `job_submissions` via `ALTER TABLE ADD COLUMN` after the table was already in use. Delta preserves existing rows and fills new columns with `NULL`. This is managed schema evolution with no data loss.

4. **Graceful degradation**: Every Databricks call is wrapped in `try/except`. If the warehouse is sleeping or credentials expire, the app continues to serve all AI features — only Market Trends returns empty lists. This is a deliberate cloud resilience pattern.

### Environment-Based Configuration

All secrets (API keys, tokens) are injected as environment variables at runtime. The running code never contains a secret. This follows the [Twelve-Factor App methodology](https://12factor.net/config) and means:
- The same codebase runs locally (reads from `.env`) and in production (reads from Render's environment)
- Rotating a key requires only updating the environment variable, not modifying and redeploying code
- The repository can be fully public without exposing credentials

### Crowdsourcing / Dual-Utility Pattern

As described in [Section 2](#2-the-dual-utility-design), every job analysis submission produces two outputs simultaneously — immediate individual value and an incremental contribution to the collective market intelligence dataset. This is the "killing two birds with one stone" architecture described in the assignment brief.

---

## 12. Architectural Decisions & Tradeoffs

| Decision | Why | Tradeoff |
|---|---|---|
| **FastAPI over Flask** | Auto-generates OpenAPI/Swagger docs from type annotations; native async support; Pydantic validation is built-in | Slightly steeper learning curve for first-timers; Flask is more familiar to the Python community |
| **Pydantic models for every endpoint** | Runtime input validation catches bad requests at the boundary; Swagger UI shows schema automatically | More boilerplate code per endpoint compared to untyped Flask |
| **Databricks Free Edition** | Real enterprise Delta Lake with time travel, MERGE, schema evolution — not a toy database | Free tier's SQL warehouse sleeps after inactivity; first query takes 30–60 seconds to wake it. The 10-second `_socket_timeout` in `get_connection()` means market trends load slowly after idle periods |
| **Render free tier** | Zero cost, GitHub auto-deploy, Singapore region | Instance sleeps after 15 minutes of inactivity; first request takes ~60 seconds to cold-start. Not suitable for production SLAs |
| **Stateless API (no user accounts)** | Eliminates authentication complexity; any client can call any endpoint; scales horizontally | No personalisation; users cannot retrieve their history; each session starts fresh |
| **Text extracted from PDF server-side** | Avoids sending binary to OpenAI; gives us control over extraction quality | PyPDF2 fails on scanned PDFs (image-only). Mitigation: clear error message returned to user |
| **Prompt files in `prompts/` directory** | Separates AI prompt engineering from application code; prompts can be iterated without touching Python | Prompts are now visible in the public repository — no IP protection if prompts are proprietary |
| **Background analytics recording** | The `record_ats_score()` call after ATS scoring does not block the response — even if Databricks is slow | Analytics may be delayed or lost if the app restarts between the API call and the Databricks write. Acceptable for a demonstration platform |

---

## 13. Troubleshooting

### The app takes 60-120 seconds to respond on the first request

**Cause:** Render's free tier spins down idle instances. The first request after a period of inactivity cold-starts the server.  
**Solution:** This is expected behaviour. Wait for the response. Subsequent requests will be fast.

### `ModuleNotFoundError` when running locally

**Cause:** Virtual environment is not activated, or `pip install -r requirements.txt` was not run.  
**Solution:**
```bash
source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### `WARNING: Databricks credentials not configured`

**Cause:** One or more of `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, or `DATABRICKS_HTTP_PATH` is missing from `.env`.  
**Solution:** Check your `.env` file. All three must be set. Market Trends will be empty until they are.

### Market Trends endpoint is slow (30+ seconds)

**Cause:** Databricks Free Edition SQL Warehouse sleeps after inactivity. The first query after sleep must wake the warehouse (~30–60 seconds).  
**Solution:** Submit a job analysis (which writes to Databricks, keeping the warehouse awake), then immediately call the market trends endpoint. In production, you would use a scheduled keep-alive query or a paid tier that does not sleep.

### PDF parse returns empty text

**Cause:** PyPDF2 cannot extract text from scanned PDFs (image-only documents).  
**Solution:** Use a digitally created PDF (e.g. saved directly from Word, Google Docs, or a LaTeX compiler). Scanned documents require OCR which is outside the scope of this project.

### Render build fails with `pip install` error

**Cause:** A package version conflict, or a package that requires system-level dependencies not available on Render.  
**Solution:** Check the build log on Render's dashboard. Paste the full error message here for diagnosis. Common fix: ensure `requirements.txt` uses exact version pins (`==`) not ranges.

### OpenAI returns a 429 error

**Cause:** Your OpenAI API account has hit its rate limit or has no remaining credits.  
**Solution:** Check your usage at [platform.openai.com/usage](https://platform.openai.com/usage). Add credits if required. The free tier has a very low rate limit; the paid tier has significantly higher limits.

---
