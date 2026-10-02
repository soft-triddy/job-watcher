# Job Radar

A personal job-search pipeline that watches ~350 company career pages and a remote-jobs board,
keeps only the marketing roles I actually want, scores each new vacancy against my resumes,
and sends the result to Telegram. Runs entirely on GitHub Actions, costs nothing.

```text
• Acme: Growth Marketing Manager — Remote
  📄 Growth · fit 7/10 · dull 2/10
  🟢 remote · ✅ grade · ⚠️ Meta/TikTok buying
https://jobs.example.com/123
```

## How it works

```text
 company lists ──┬── API radar ─────── public ATS APIs (12 ATS incl. Workday)
                 ├── Browser radar ─── real Chromium for JS-only career pages
 Himalayas API ──┘
                          │
                          ▼
              marketing filter (title + location)
                          │
                          ▼
              seen-state → only new vacancies
                          │
                          ▼
              scorer (Gemini): which resume, fit, dull, remote, grade, blocker
                          │
                          ▼
                       Telegram
```

- **API radar** (`radar.py`) — Greenhouse, Lever, Ashby, Workable, Recruitee, BambooHR, Breezy,
  SmartRecruiters, Rippling, Teamtailor, Pinpoint, Workday. The company's ATS slug is guessed from
  the careers URL/page and verified with a live request; slugs that match an ATS's own name or a
  locale segment are rejected, so a bad guess can't pull another company's jobs.
- **Browser radar** (`browser_radar.py`) — Playwright opens pages that have no public API and reads
  jobs from the page's own JSON requests, JSON-LD `JobPosting`, or links. Chromium runs in a worker
  process; a site that hangs longer than 75 s is killed and skipped instead of stalling the run.
- **Himalayas** (`himalayas_radar.py`) — worldwide, full-time roles via the public Himalayas API,
  crypto companies dropped by category.
- **Filter** (`core.py`) — title must look like marketing/growth/CRM/SEO, and must not be sales,
  brand, content, analytics, junior grades, hybrid, or US-only.
- **Scorer** (`scorer.py`) — for each *new* vacancy fetches the full description (ATS API, JSON-LD
  or rendered page) and asks Gemini for a strict JSON verdict. Remote status is double-checked in code
  against the location field. Hard time budget per vacancy and per run; if the model is down, the
  vacancy is still delivered, marked "no score", with the reason at the bottom of the message.
- **Music-tech radar** (`music.yml`) — the same API + browser radars on a separate list of music-tech
  companies, with its own state and a scoring tweak for the industry.

## Source health

Every radar writes `state/health_*.json`: for each company (or Himalayas query) — how many
vacancies it returned this run, the error if any, how many runs in a row it has been broken and
when it last worked. Telegram gets one message with everything that is broken on the first run,
then only when a source has been broken 3 runs in a row, and when it recovers.
`state/rejected_*.json` lists titles that look like marketing but were cut by the filter, with the
rule that cut them — to check the filter isn't too greedy.

Companies the API radar could not read (bad slug, ATS down, unsupported ATS) go to
`state/api_fallback.csv`, and the browser radar checks them the same day.

New vacancies are marked as seen only after Telegram accepts the message, so a failed send
re-delivers them on the next run instead of losing them.

## Repository layout

```text
core.py              filter, Telegram, state, shared reporting logic
radar.py             API radar
browser_radar.py     browser radar
himalayas_radar.py   Himalayas radar
scorer.py            vacancy scoring
scoring_profile.md   condensed resume profile + preferences the scorer uses
discover.py          one-off tool: finds careers pages and ATS for a list of new companies

lists/               company lists (radar_*, browser_*, music_*) and LinkedIn watchlists
state/               what has already been seen, cached ATS slugs (committed by the bot)
discover/            input/output of discover.py
.github/workflows/   radar, browser, himalayas, music (daily); discover (on demand)
```

## Adding companies

1. Put `Name,Site` rows into `discover/input.csv` and push — the `discover` workflow finds each
   careers page, detects the ATS, checks the API live and writes `discover/result.csv`.
2. Move rows by bucket: `api` → `lists/radar_companies.csv` (Name, Link, ATS),
   `browser` → `lists/browser_companies.csv`, the rest → LinkedIn watchlist.

## Setup

Repository secrets: `TG_TOKEN`, `TG_CHAT` (Telegram bot), `GEMINI_API_KEY` (Google AI Studio, free tier).

Run locally:

```bash
pip install requests playwright && python -m playwright install chromium
python radar.py            # only new vacancies
python radar.py --all      # everything currently open, scored
```

Every radar sends a short summary on its first run and only new vacancies afterwards.

## Stack

Python · Requests · Playwright · Gemini API · Telegram Bot API · GitHub Actions · CSV/JSON as storage
