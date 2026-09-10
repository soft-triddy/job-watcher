# Job Radar

A lightweight job monitoring system that automatically finds relevant marketing roles across multiple job boards and sends new matches to Telegram.

Built as a personal job-search automation to avoid manually checking dozens of company career pages every day.

## What it does

Job Radar runs automatically on GitHub Actions and:

* monitors company career pages across multiple ATS platforms;
* collects currently open positions through public APIs;
* uses a browser-based scraper for JavaScript-heavy career pages;
* searches **Himalayas** for worldwide full-time remote roles;
* filters vacancies by marketing-related keywords;
* removes irrelevant roles and unwanted locations/work formats;
* remembers previously seen jobs;
* sends only **new matching vacancies** to Telegram.

The system is designed to be low-maintenance: once configured, it runs on a schedule without requiring a local machine.

## Sources

The main radar currently supports these ATS platforms:

* Greenhouse
* Lever
* Ashby
* Workable
* Recruitee
* BambooHR
* Breezy
* SmartRecruiters
* Rippling
* Teamtailor
* Pinpoint

There is also a separate browser-based layer for career pages where the regular API approach does not work.

### Himalayas

The Himalayas radar uses the public Himalayas jobs API to search for worldwide full-time roles.

It currently searches several broad queries:

* marketing
* growth
* demand
* SEO
* CRM
* lifecycle

The results are then passed through the same marketing and location filters used by the main radar.

## Filtering

The main filtering logic is intentionally conservative.

### Included roles

The radar looks for titles containing marketing-related terms such as:

* Marketing
* Growth
* CRM
* Lifecycle
* Demand
* SEO
* PMM
* MarTech
* Paid
* Performance
* Digital

### Excluded roles

The filter removes roles that are technically related to the keywords but are not relevant to the target search.

Examples include:

* Marketing Analyst
* Brand
* Email Marketing
* Social Media
* Content
* Product Marketing
* Business Development
* PR
* Customer Success
* Product Manager
* Recruiter
* Community
* Account Management
* Engineering
* Crypto / Web3
* Affiliate
* Copywriting

The filter also excludes:

* hybrid positions;
* US-only positions;
* positions restricted to US states.

The goal is to prioritize genuinely relevant remote marketing opportunities rather than maximize the number of results.

## Architecture

The project consists of three main collection layers.

```text
                    ┌─────────────────────┐
                    │  Company career     │
                    │  pages / ATS        │
                    └──────────┬──────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
        Public ATS APIs                Browser scraper
                │                         Playwright
                │                             │
                └──────────────┬──────────────┘
                               │
                               ▼
                     Marketing filter
                               │
                               ▼
                       Deduplication
                               │
                               ▼
                       Seen job state
                               │
                               ▼
                         Telegram
```

Himalayas works as an additional independent source using its public API.

## Project structure

```text
.
├── radar.py
├── browser_radar.py
├── himalayas_radar.py
├── browser_diag.py
│
├── radar_companies.csv
├── browser_companies.csv
│
├── seen.json
├── seen_browser.json
├── seen_himalayas.json
├── slugs.json
│
└── .github/
    └── workflows/
        ├── radar.yml
        ├── browser.yml
        ├── himalayas.yml
        └── diag.yml
```

### `radar.py`

The main API-based job collector.

It:

1. reads the company list from `radar_companies.csv`;
2. identifies the appropriate ATS;
3. fetches current vacancies;
4. applies the marketing and location filters;
5. compares results with the previous run;
6. sends new vacancies to Telegram;
7. saves the current state.

### `browser_radar.py`

A Playwright-based scraper for career pages that cannot be reliably accessed through a public ATS API.

It extracts jobs from:

* JSON responses loaded by the page;
* JSON-LD `JobPosting` structured data;
* rendered DOM content.

The same filtering and Telegram notification logic is reused from `radar.py`.

### `himalayas_radar.py`

A dedicated collector for Himalayas.

It searches worldwide full-time positions and applies the same marketing filters used by the main radar.

Crypto/Web3 companies are additionally filtered at the company/category level.

### `browser_diag.py`

A diagnostic utility for investigating career pages where the browser scraper fails to find jobs.

It is intended for manual runs through GitHub Actions.

## State management

The radar keeps track of previously processed vacancies using JSON state files:

```text
seen.json
seen_browser.json
seen_himalayas.json
```

This prevents the same vacancy from being sent repeatedly.

On the first run, the system initializes its state instead of sending a large list of existing vacancies.

Subsequent runs only send newly discovered matching positions.

The main radar also stores resolved ATS slugs in:

```text
slugs.json
```

This reduces the need to rediscover company identifiers on every run.

## Telegram notifications

When new matching jobs are found, the radar sends a Telegram message containing:

* company;
* job title;
* location, when available;
* direct application URL.

Example:

```text
🆕 Новые маркетинг-вакансии (3):

• Company: Growth Marketing Manager — Remote
https://example.com/job/123

• Another Company: Performance Marketing Manager
https://example.com/job/456
```

Telegram credentials are provided through environment variables:

```text
TG_TOKEN
TG_CHAT
```

They should **never be committed to the repository**.

## GitHub Actions

The project is designed to run entirely through GitHub Actions.

There are three scheduled workflows:

### Main radar

```text
.github/workflows/radar.yml
```

Runs daily and checks API-based ATS sources.

### Browser radar

```text
.github/workflows/browser.yml
```

Runs daily and uses Playwright + Chromium to check JavaScript-heavy career pages.

### Himalayas radar

```text
.github/workflows/himalayas.yml
```

Runs daily and checks worldwide full-time roles on Himalayas.

All workflows can also be triggered manually with `workflow_dispatch`.

The workflows automatically commit updated state files back to the repository so the radar can remember what it has already seen between runs.

## Setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd job-watcher
```

### 2. Install dependencies

For the API-based radar:

```bash
pip install requests
```

For the browser radar:

```bash
pip install requests playwright
python -m playwright install chromium
```

### 3. Configure Telegram

Create a Telegram bot and obtain:

* bot token;
* target chat ID.

Set them as environment variables:

```bash
export TG_TOKEN="your-token"
export TG_CHAT="your-chat-id"
```

For GitHub Actions, add them as repository secrets:

```text
Settings → Secrets and variables → Actions
```

Create:

```text
TG_TOKEN
TG_CHAT
```

### 4. Configure companies

Add companies to:

```text
radar_companies.csv
```

The expected format is:

```csv
Name,Link,ATS
Company Name,https://company.com/careers,Greenhouse
Another Company,https://jobs.example.com,Lever
```

Browser-based sources are configured separately in:

```text
browser_companies.csv
```

## Running locally

Run the main radar:

```bash
python radar.py
```

Run the browser radar:

```bash
python browser_radar.py
```

Run the Himalayas radar:

```bash
python himalayas_radar.py
```

To send all currently matching vacancies instead of only new ones:

```bash
python radar.py --all
```

The same `--all` option is supported by the browser and Himalayas radars.

## Why this project exists

Job searching is often less about finding *a* job board and more about continuously checking a large number of individual company career pages.

This project automates that repetitive part of the process.

Instead of manually checking dozens of websites, the workflow continuously collects vacancies, applies a personal definition of a relevant role, removes obvious false positives, and delivers only new matches to Telegram.

The result is a small personal information pipeline:

**collect → filter → deduplicate → notify → repeat**

## Tech stack

* Python
* Requests
* Playwright
* Telegram Bot API
* GitHub Actions
* CSV / JSON for configuration and state

## Status

This is a personal automation project rather than a general-purpose job aggregation service.

The company lists, filtering rules and geographic constraints are intentionally tailored to a specific job-search use case and can be modified directly in the source files.
