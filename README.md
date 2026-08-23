# Job Search Agent

A multi-agent job search pipeline: it searches multiple job boards, scores
each listing against your actual resume, runs a deterministic ATS
keyword-match check the way a real applicant-tracking system would, drafts
a truthfully-tailored resume for anything that clears the ATS bar, tracks
what you've applied to, and audits your LinkedIn/Naukri profile against
your own work history — so the busywork of a job search (search, screen,
tailor, track) is automated, and the parts that carry real risk (editing
your live LinkedIn/Naukri profile, submitting applications) require your
explicit, one-time, typed acknowledgment before anything touches a real
account.

Backend: FastAPI + LangGraph. Frontend: React + Vite. Deployed on Render.

## How it works

```
search  →  rank by fit  →  ATS check  →  tailor resume (on demand, ATS ≥ 70)  →  track application
                                                                                        ↑
                                                                    profile audit (LinkedIn/Naukri)
```

1. **Search** — pulls listings from whichever sources are configured:
   Remotive, Adzuna, JSearch (RapidAPI), Apify actors (LinkedIn/Naukri
   scraping, opt-in), and arbitrary custom job-board URLs via a built-in
   scraper (`agents/url_scraper.py`) that tries a fast HTTP fetch first
   and only falls back to a headless-browser render if the page needs
   JavaScript to show its content.
2. **Rank by fit** — an LLM scores each listing against your resume
   (`agents/ranker.py`), producing a 0–100 fit score plus a short
   explanation. Below a threshold, a job is dropped and never shown.
3. **ATS check** — a second, *non-LLM* pass (`agents/ats_checker.py`)
   does literal keyword matching between the job description and your
   resume, the same blunt way real ATS software screens resumes before a
   human ever sees them. This produces a separate ATS score and a list of
   keywords you're missing.
4. **Tailor resume (on demand)** — for any job scoring ATS ≥ 70, you can
   click "Tailor Resume" to get an ATS-optimized headline/summary draft
   that truthfully incorporates your missing keywords. It never runs
   automatically for every job on every search — only when you ask for a
   specific one. A deterministic post-check (`agents/ats_checker.py`)
   strips any sentence containing a number that isn't literally present
   in your actual resume text, so a drafted metric can't sneak in a
   number the LLM invented.
5. **Track** — every job that clears the fit threshold is recorded in a
   local application tracker (`store.py`), independent of whether you
   apply. You can update its status (applied, interviewing, offer,
   rejected, ghosted) as things progress, or discard it.
6. **Profile audit** — a separate dashboard page reviews your LinkedIn
   and Naukri profile content (headline, About/summary, skills, keyword
   coverage) against what your resume actually says, and gives concrete,
   copy-pasteable rewrite suggestions. This is deliberately static,
   hand-refreshed content, not a live scrape of your profile — see
   [Safety](#safety) for why.

## Tech stack

- **Backend**: FastAPI, orchestrated with LangGraph/LangChain. The LLM
  backend is pluggable (`llm.py`) and tries providers in this order,
  falling through to the next if a key isn't set: your own
  Ollama-compatible service (`OLLAMA_SERVICE_URL`) → Anthropic → OpenAI →
  Groq → a direct local Ollama install as the final fallback. Nothing
  here requires a paid API key — a local Ollama model (the default if
  nothing else is configured) is enough to run the whole pipeline.
- **RAG/search infrastructure**: ChromaDB + fastembed power a similar-jobs
  vector search over past results (`api/vector_indexer.py`,
  `api/routers/similar_jobs.py`).
- **Frontend**: React 19 + Vite, plain CSS (no component framework),
  deployed as a static site.
- **Scraping**: BeautifulSoup + Playwright for custom job-board URLs
  (`agents/url_scraper.py`), Apify actors for LinkedIn/Naukri (opt-in,
  see below).
- **Storage**: no database — a JSON file for the application tracker
  (`data/applications.json`), a small JSON snapshot of the last completed
  pipeline run (`data/last_run.json`), and a generic SQLite key-value
  cache (`data/cache.sqlite3`) for expensive LLM calls and scrapes. All
  three are local files, gitignored, and rebuild themselves from scratch
  if deleted.

## Setup

1. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```
   (Optional, for local-only tooling not needed on Render: `pip install -r requirements-local-tools.txt`.)

2. **Configure environment**
   ```
   cp .env.example .env
   ```
   Edit `.env` and fill in whichever sections you actually want active —
   every source and feature auto-skips if its keys are missing, so you
   don't need all of them. At minimum, set up one LLM backend (a local
   Ollama install is the simplest zero-cost option: `OLLAMA_MODEL` alone
   is enough if Ollama is running on `localhost:11434`).

3. **Run the pipeline from the CLI** (safe mode — no live account writes)
   ```
   python main.py
   ```
   This searches, ranks, ATS-checks, updates the tracker, and writes a
   plain-text improvement report to `data/reports/`. Nothing is posted
   anywhere.

4. **Run the API + dashboard locally**
   ```
   uvicorn api.app:app --reload --port 8000
   ```
   In a second terminal:
   ```
   cd web
   npm install
   npm run dev
   ```
   The frontend defaults to `http://localhost:8020` for its API base
   (see `web/src/api/client.js`) — set `VITE_API_BASE` if you're running
   the backend on a different port, e.g. `8000` from the command above.

5. **Deploy** — `render.yaml` describes both services (Python web
   service for the API, static site for the frontend) as a starting
   point if you want to deploy your own copy on Render; the live
   deployment's actual environment variables are set directly in
   Render's dashboard rather than driven by this file.

## Safety

Two categories of action carry real, deliberate risk, and both are gated
the same way: **off by default, and it takes an explicit, typed
acknowledgment from a human — not a config flag — before either one will
touch a live account.**

### Automated profile edits (`agents/automation.py`)

This module can log into your real LinkedIn or Naukri account with a
visible (never headless) browser and update your headline/summary field
to a draft you've already reviewed. It is **opt-in, off by default**, and
gated like this on purpose:

- It only runs if you pass `--mode automation` at the CLI *and* then
  type the exact phrase `I ACCEPT THE BAN RISK` at an interactive prompt.
  There is no environment variable or config setting that can skip this
  — the confirmation is hardcoded to require real keyboard input, every
  time, with no bypass.
- The browser runs headed (visibly, on your screen), not headless — so
  you're present to solve any 2FA or CAPTCHA challenge yourself, rather
  than the script attempting a bypass. Attempting a headless bypass is
  exactly the pattern that gets automation-detection systems to flag an
  account, so this is a deliberate design choice, not an oversight.
- **Why this exists at all**: scripted login and profile editing violates
  LinkedIn's and Naukri's Terms of Service. The realistic outcome of
  getting caught is a permanently restricted account, with no appeal
  path. This gate exists so that using this feature is always a
  conscious, informed choice made in the moment — never something that
  runs because a flag was left on somewhere.

The same reasoning extends to `agents/apply_playwright.py`, which can
submit a real job application (LinkedIn Easy Apply, or open any other
posting for you to finish manually). It requires the same typed
confirmation phrase, passed through the dashboard's Apply button, and it
only makes sense to run against a *locally-running* copy of this API —
a deployed, headless server has no one present to clear a security
challenge, so pointing this feature at a hosted deployment will just
hang waiting for a human who isn't there.

### Reading LinkedIn/Naukri profile content

The profile audit page (`web/src/pages/ProfileAudit.jsx`) is deliberately
**static, hand-maintained content** — it is not fetched live from
LinkedIn or Naukri, and does not use `agents/automation.py` or any
scraper to read your profile automatically. Repeated automated *reads*
of an authenticated profile carry the same ToS/detection risk as
automated *writes*, just applied to the other direction — so this page
is refreshed by hand (based on a PDF export you check yourself) rather
than on a schedule.

### Everything else

Job *search* — pulling public listings from Remotive, Adzuna, JSearch,
Apify actors, or arbitrary job-board URLs — carries no comparable risk
and runs automatically as part of the normal pipeline. Apify-based
LinkedIn/Naukri *search* scraping is still gated behind an explicit
`APIFY_ALLOW_SCRAPING=true` opt-in (see `.env.example`), since it also
touches those platforms' ToS, but it doesn't require an interactive
confirmation the way an authenticated write action does — it's a
one-time config decision, not a per-use one.
