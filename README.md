# MailCrawl

Crawls a domain's publicly accessible pages, discovers published business contact
addresses, validates them, and exports the result as CSV or Excel. Database-free —
scan state lives in memory while a scan runs and on disk once it finishes.

---

## Features

- 🔍 **Domain Normalization & Crawl Discovery**: Resolves inputs like `https://www.example.com/about/` to `example.com`, parses per-host `robots.txt` and `sitemap.xml`, and prioritizes key pages (`/contact`, `/team`, `/about`).
- 🌍 **Full-Domain Coverage**: Enumerates every host under the registered domain from certificate transparency logs (crt.sh + Cert Spotter) plus common-host probing, then crawls every reachable site. There is no page cap — the domain's own page count is the crawl budget, bounded by crawl depth.
- ⚡ **Asynchronous Crawling**: `httpx` async crawler with controlled concurrency, a heap-ordered priority frontier, response caps, and fragment/tracking parameter stripping (`utm_*`, `gclid`, `fbclid`).
- 🌐 **Headless Browser Fallback**: Optional Playwright Chromium fallback for JavaScript-rendered pages.
- 📄 **PDF Contact Extraction**: Multi-engine pipeline (PyMuPDF → pdfplumber → pypdf) that reads AES/RC4-encrypted PDFs, pulls `mailto:` link annotations, and repairs addresses split across line breaks or spaced out by the page layout.
- 📇 **Standards-Based Contact Discovery**: Reads machine-readable records the domain owner maintains — RFC 9116 `security.txt`, DMARC `rua`/`ruf` and SOA responsible-person mailboxes, and RDAP registration contacts. These routinely expose working addresses that appear nowhere in the site's HTML.
- 🔗 **External Source Scanning**: Follows off-domain links to careers portals (Greenhouse, Lever, Workday, …), press releases and public profiles, and scans those readable without an account. Pages behind a login wall are reported as such; authentication is never bypassed.
- 🔒 **SSRF Protection**: Strict checks blocking private IPs (`10.0.0.0/8`, `127.0.0.0/8`, `169.254.169.254`, etc.) before any request leaves the process.
- ✅ **Domain-Matched Validation**: Every address is validated on syntax, MX deliverability, and whether it belongs to the domain being scanned (`Target Domain`, `Subdomain`, `External Domain`).
- 🗒️ **Scan Log & Resume**: Every search is logged to `data/scan_log.jsonl`. The crawl checkpoints itself every N pages, so an interrupted scan resumes from exactly where it stopped — including its in-flight batch.
- 🤖 **robots.txt Control**: Disallow enforcement is a per-scan setting. `Sitemap:` entries are always read either way, and any `Crawl-delay` a host asks for is honoured regardless, since that protects the server from load.
- 📊 **Exports**: UTF-8 CSV or a formatted 3-sheet Excel workbook (`Emails`, `Scan Summary`, `Errors`).

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # optional; only needed for JS rendering

python run_server.py                 # -> http://127.0.0.1:8000
```

Configuration is optional — `cp .env.example .env` and edit only what you want to
change. Every setting has a working default.

The web UI is plain HTML, CSS and JavaScript over a FastAPI backend: no frontend
framework, no build step. Scans stream over server-sent events, so addresses appear
as they are found. A scan runs on its own thread server-side and **survives closing
the browser tab** — reopen the page and it reattaches to the scan in progress.

### The provenance rail

Every address is stamped with a three-letter sigil for where it came from, coloured
by provenance class:

| Class                 | Meaning                                             | Sigils            |
| --------------------- | --------------------------------------------------- | ----------------- |
| **Declared** (teal)   | Published by the owner in a machine-readable record | `DNS` `RDP` `SEC` |
| **Page** (violet)     | Found in rendered web content                       | `MTO` `WEB` `OBF` |
| **Elsewhere** (amber) | Documents and off-domain presences                  | `PDF` `EXT`       |

---

## Deployment

### Docker (recommended)

```bash
docker compose up --build          # -> http://127.0.0.1:8000
```

Or without compose:

```bash
docker build -t domain-email-intelligence .
docker run -p 8000:8000 -v scan-data:/data --shm-size=1g domain-email-intelligence
```

The image installs Chromium and its system libraries, runs as an unprivileged user,
and exposes `/api/health` for orchestrator health checks.

**Three things a deployment must get right:**

1. **One worker per container.** The job registry, SSE fan-out and live feed are
   per-process state. `--workers 2` would let a client's stream request land on a
   process that has never heard of their scan. Scale out with more containers behind
   a proxy with sticky sessions, not with more workers.
2. **Mount a volume at `DATA_DIR`.** Without it, a container restart loses the scan
   log and every resumable checkpoint.
3. **Give Chromium shared memory and a shutdown grace period.** Docker's default
   64 MB `/dev/shm` crashes Chromium on content-heavy pages, and its 10-second stop
   timeout can kill a scan before it finishes checkpointing. The compose file sets
   `shm_size: 1gb` and `stop_grace_period: 45s`.

### Without Docker

```bash
DATA_DIR=/var/lib/domain-email-intelligence \
  uvicorn web.server:app --host 0.0.0.0 --port 8000 --workers 1
```

Run behind a reverse proxy that does not buffer responses — buffering breaks the SSE
stream. For nginx: `proxy_buffering off;` on the `/api/scans/*/stream` location.

Platforms that inject `$PORT` (Railway, Render, Heroku) work with `run_server.py`
directly: it reads `$HOST`/`$PORT`, and when `$PORT` is set it binds exactly that
port instead of scanning for a free one.

---

## Configuration

All settings are environment variables with working defaults; see `.env.example` for
the full annotated list. The ones that matter most in production:

| Variable                     | Default | What it does                                  |
| ---------------------------- | ------- | --------------------------------------------- |
| `DATA_DIR`                   | `data`  | Scan log, saved results, checkpoints          |
| `MAX_CONCURRENT_SCANS`       | `2`     | Capped by Chromium memory, not the event loop |
| `JOB_TTL_SECONDS`            | `3600`  | How long a finished scan stays in memory      |
| `MAX_PDF_SIZE_MB`            | `150`   | Skip PDFs larger than this                    |
| `SKIP_NON_PUBLIC_SUBDOMAINS` | `false` | Trade coverage for a faster scan              |
| `SHUTDOWN_JOIN_SECONDS`      | `30.0`  | Grace period for scans to checkpoint on exit  |

---

## Project structure

```
.
├── run_server.py               # Local dev launcher (pins DATA_DIR, runs uvicorn)
├── pyproject.toml              # pytest + ruff configuration
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # + test and lint tooling
├── Dockerfile                  # Multi-stage build, non-root, Chromium included
├── docker-compose.yml          # One-command deployment
├── .env.example                # Annotated configuration reference
│
├── src/                        # Scan engine — no web framework below this line
│   ├── crawler/
│   │   ├── crawler.py          # Async crawler; heap-ordered priority frontier
│   │   ├── discovery.py        # Sitemap & URL discovery with priority scoring
│   │   ├── subdomains.py       # Certificate-log subdomain enumeration & probing
│   │   ├── external_sources.py # Off-domain company presence scanning
│   │   ├── wellknown_sources.py# security.txt, DMARC/SOA DNS & RDAP contacts
│   │   ├── playwright_fetcher.py # Headless Chromium renderer
│   │   └── robots.py           # robots.txt parser
│   ├── extraction/
│   │   ├── email_extractor.py  # Regex & obfuscated email reconstruction
│   │   ├── html_extractor.py   # BeautifulSoup HTML & mailto parser
│   │   ├── pdf_extractor.py    # Multi-engine PDF text extraction
│   │   └── context_extractor.py# Surrounding context snippet generator
│   ├── validation/
│   │   ├── domain_validator.py # SSRF guard (cached), DNS MX & domain matching
│   │   └── email_validator.py  # Syntax check & false-positive filter
│   ├── processing/
│   │   ├── deduplicator.py     # Source prioritization & canonical deduplication
│   │   └── scoring.py          # 0-100 extraction confidence scoring
│   ├── export/
│   │   ├── csv_exporter.py     # CSV generator (stdlib csv)
│   │   └── excel_exporter.py   # openpyxl 3-sheet workbook
│   ├── models/                 # Pydantic email & scan models
│   └── utils/                  # URL utilities, scan store, logging, config
│
├── web/                        # FastAPI backend + framework-free frontend
│   ├── server.py               # App factory; StaticFiles mounted last
│   ├── settings.py             # Environment-tunable server knobs
│   ├── schemas.py              # Request bodies
│   ├── jobs/                   # Job registry, worker threads, SSE fan-out
│   ├── routes/                 # scans · stream · results · exports · history
│   ├── services/               # Filters, scan-store facade, serializers
│   └── static/                 # index.html + css/ + js/ (no build step)
│
├── data/                       # Scan log, results & checkpoints (gitignored)
│   ├── scan_log.jsonl
│   └── scans/<scan_id>/        # results.json + checkpoint.json
```

The `src/` layer knows nothing about HTTP; `web/` is the only thing that imports
FastAPI. That boundary is what lets the crawler be driven from a worker thread with
its own event loop while the server stays responsive.

---

## Development

```bash
pip install -r requirements-dev.txt

pytest                          # 192 tests
ruff check .                    # lint
ruff check . --fix              # autofix
```

`pyproject.toml` configures both. `pythonpath = ["."]` there is what makes `src` and
`web` importable from the tests — there is no `conftest.py` path hack.

---

## Responsible crawling & security

1. **Strictly public data.** Paywalls, CAPTCHAs and authentication are never
   bypassed; a page behind a login wall is recorded as such and skipped.
2. **robots.txt.** `Disallow` enforcement is a per-scan setting, but `Crawl-delay` is
   always honoured — that one protects the server being crawled.
3. **SSRF protection.** Hostnames are resolved and checked against loopback, private,
   carrier-grade NAT and cloud metadata ranges (`169.254.169.254`) before any
   request. Verdicts are cached per host for the duration of a scan and cleared when
   the server goes idle.
4. **Non-root container.** The headless browser renders untrusted pages, so it does
   not run as root.
