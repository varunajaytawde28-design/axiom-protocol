# CLAUDE.md — openfindata

## What is this project?

**openfindata** is an open-source Python library that gives clean, programmatic access to publicly available Indian financial data that is currently trapped in unusable government portals.

**v1 focus: PMS (Portfolio Management Services) data from SEBI.**

SEBI mandates ~500 Portfolio Managers to submit monthly reports including performance returns, AUM breakdowns, client counts, and investment approach data. This data is publicly available on SEBI's website but can only be viewed one manager at a time, one month at a time, with no download or comparison capability. We fix that.

## The Problem We Solve

- SEBI's PMS portal (https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doPmr=yes) requires manual selection of each PM + month — no bulk access
- No API, no CSV download, no historical comparison
- Investors putting ₹25 lakh+ into PMS have no way to compare schemes
- Existing solutions (PMSBazaar, Shepherd's Hill) are closed platforms
- Paid providers like Morningstar/Accord charge lakhs but don't even cover PMS well

## v1 Scope

### What we ship:
1. **SEBI PMS Scraper** — Automated extraction of monthly PMS reports from SEBI portal
2. **Standardized Data Schema** — Clean JSON/CSV output with consistent field names
3. **Python SDK** — `pip install openfindata` with simple API like `openfindata.pms.get_returns("marcellus", "2023-01", "2025-12")`
4. **Static Dataset** — Pre-scraped historical data hosted in the repo (updated monthly)
5. **GitHub Actions** — Monthly automated scrape to keep data fresh

### What we DON'T ship in v1:
- AIF data (v2)
- NPS data (v2)
- MF NAV data (Morningstar already does this)
- Web UI / dashboard
- Real-time data

## Tech Stack

- **Language**: Python 3.10+
- **HTTP**: `httpx` (async-capable, modern replacement for requests)
- **Parsing**: `beautifulsoup4` + `lxml`
- **Data**: `pandas` for transformation, output as JSON/CSV/DataFrame
- **Testing**: `pytest`
- **Packaging**: `pyproject.toml` (modern Python packaging, no setup.py)
- **CI/CD**: GitHub Actions for monthly scrape + PyPI publish
- **Linting**: `ruff`

## Project Structure

```
openfindata/
├── CLAUDE.md                    # This file
├── README.md                    # Project overview, install, usage examples
├── LICENSE                      # MIT License
├── pyproject.toml               # Package config, dependencies, metadata
├── .github/
│   └── workflows/
│       ├── scrape.yml           # Monthly SEBI PMS data scrape
│       └── publish.yml          # PyPI publish on release
├── src/
│   └── openfindata/
│       ├── __init__.py          # Package init, version
│       ├── pms/
│       │   ├── __init__.py      # PMS module public API
│       │   ├── scraper.py       # SEBI portal scraper logic
│       │   ├── parser.py        # HTML response → structured data
│       │   ├── schema.py        # Data models / schema definitions
│       │   └── constants.py     # PMS manager names, codes, mappings
│       ├── utils/
│       │   ├── __init__.py
│       │   ├── http.py          # HTTP client with retry/rate-limit
│       │   └── cache.py         # Local file caching
│       └── exceptions.py        # Custom exceptions
├── data/
│   └── pms/
│       └── raw/                 # Raw scraped HTML/JSON (gitignored in large form)
│       └── processed/           # Clean CSV/JSON datasets (committed)
├── tests/
│   ├── __init__.py
│   ├── test_pms_scraper.py
│   ├── test_pms_parser.py
│   └── fixtures/                # Sample SEBI HTML responses for testing
│       └── sample_pmr.html
├── scripts/
│   └── scrape_all.py            # CLI script to run full scrape
└── docs/
    └── data_dictionary.md       # Field descriptions, data types, caveats
```

## SEBI PMS Portal Technical Details

### How the portal works:
- URL: `https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doPmr=yes`
- It's a form with 3 dropdowns: PM Name, Year (2018-2026), Month (Jan-Dec)
- Submitting triggers a JS function `getPMR()` which makes a POST/GET request
- Response is an HTML table with the monthly report
- Data available from ~2013 onwards (patchy before 2018)

### Data fields we extract per PMS per month:
- Portfolio Manager name and SEBI registration number
- Investment approaches (each PM can have multiple)
- Per-approach: monthly return (%), benchmark return (%), AUM
- Client count (discretionary, non-discretionary, advisory)
- Fund inflows/outflows
- Asset allocation breakdown (equity, debt, MF, others)

### Rate limiting considerations:
- SEBI's site is slow — add 2-3 second delays between requests
- ~500 PMs × 12 months × ~8 years = ~48,000 requests for full historical scrape
- Full scrape will take several hours — design for resumability
- Monthly incremental scrape is just ~500 requests

## Code Style & Conventions

- Type hints on all public functions
- Docstrings in Google style
- All data returned as pandas DataFrames by default, with `.to_json()` / `.to_csv()` options
- Async-first design using httpx (with sync wrapper for simple usage)
- Comprehensive error handling — never crash on one bad response, log and continue
- All scraped data timestamped with scrape date

## Usage Examples (target API)

```python
import openfindata as ofd

# Get all PMS managers
managers = ofd.pms.list_managers()

# Get monthly returns for a specific PMS
returns = ofd.pms.get_returns("marcellus", start="2023-01", end="2025-12")

# Get AUM data
aum = ofd.pms.get_aum("motilal_oswal", start="2024-01")

# Get all data for a month across all PMS
monthly = ofd.pms.get_monthly_report(year=2025, month=1)

# Compare multiple PMS
comparison = ofd.pms.compare(["marcellus", "motilal_oswal", "alchemy"], metric="returns", period="1y")

# Export
returns.to_csv("marcellus_returns.csv")
```

## Key Design Decisions

1. **Offline-first**: Ship with pre-scraped data so users don't need to hit SEBI's portal themselves
2. **Plugin architecture**: PMS is the first "source" module — AIF, NPS will follow the same pattern
3. **Schema stability**: Once we define the output schema, don't break it — semver strictly
4. **No auth required**: Everything we scrape is public data, no login needed
5. **Resumable scrapes**: Track progress so interrupted scrapes can continue

## Future Roadmap (not for v1)

- v2: AIF data from SEBI (scheme-wise NAV, benchmarks)
- v2: NPS data from PFRDA (fund NAVs, returns)
- v3: RBI data (policy rates, forex reserves, banking stats)
- v3: Corporate filings from MCA (XBRL parsed financials)

<!-- BEGIN VT PROTOCOL GOVERNANCE -->
# CLAUDE.md — vt-protocol Governance Rules

<!-- Generated by VT Protocol. Do not edit manually. -->
<!-- Regenerate with: vt apply -->

## VT Protocol Governance Rules

IMPORTANT: These rules are binding. Follow them when making changes.

1. This project uses relational database via psycopg and asyncpg and sqlite3. Do not introduce MongoDB, DynamoDB, Cassandra, other NoSQL databases without explicit approval.
2. This project uses rest api via fastapi. Do not introduce GraphQL, gRPC, alternative HTTP frameworks without explicit approval.
3. This project uses data serialization via pydantic. Do not introduce alternative validation/serialization libraries without explicit approval.
4. This project uses encryption via pynacl. Do not introduce alternative cryptography libraries without explicit approval.
5. This project uses secrets management via python-dotenv. Do not introduce hardcoded secrets, alternative secrets managers without explicit approval.
6. This project uses unit testing via pytest and unittest and tests. Do not introduce alternative test frameworks without explicit approval.
7. This project uses input validation via pydantic. Do not introduce manual validation, alternative validation libraries without explicit approval.
8. This project uses concurrency via asyncio. Do not introduce threading, multiprocessing, alternative async frameworks without explicit approval.
9. This project uses package management via pyproject.toml. Do not introduce alternative package managers without explicit approval.
10. This project uses llm provider integration via anthropic and openai. Do not introduce other LLM providers, alternative LLM frameworks without explicit approval.
11. This project uses http server (stdlib) via http.server and socketserver. Do not introduce Flask, FastAPI, Django, external web frameworks without explicit approval.
12. This project uses monkey patching / aop via wrapt. Do not introduce unittest.mock.patch for production instrumentation, alternative AOP libraries without explicit approval.
13. This project uses similarity detection via datasketch. Do not introduce alternative similarity/ANN libraries without explicit approval.
14. This project uses ml embeddings via sentence-transformers and scikit-learn. Do not introduce alternative embedding/ML frameworks without explicit approval.

<!-- END VT PROTOCOL GOVERNANCE -->
