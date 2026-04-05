# Contributing to Augur

## What Augur is

Augur is a swarm intelligence platform for ASX earnings prediction. 50 autonomous AI analyst agents — anchored to real financial data from yfinance, Perplexity Sonar, and official ASX filings — debate earnings outcomes through 3 rounds of structured negotiation. The result is a consensus prediction with conviction scores, swing factors, and full reasoning trails. Live at [augur.vercel.app](https://augur.vercel.app). Licensed under BSL 1.1.

## Architecture overview

```
POST /simulate → augur_api.py → pipeline.py →
  seed_harvester (6hr cache → yfinance + Perplexity + ASX PDFs + company IR) →
  persona_forge (50 agents, parallel, bias-anchored) →
  negotiation_runner (3 rounds) →
  prediction_synthesiser → results in Neon
```

| Layer | Tech |
|-------|------|
| Backend | Python FastAPI on Railway |
| Database | Neon PostgreSQL |
| Frontend | Next.js 14 on Vercel |
| Storage | Cloudflare R2 (seed cache) |
| Monitoring | Grafana Cloud (Loki logs, Prometheus metrics, Faro RUM) |
| LLM | Claude API (Sonnet for agents, Haiku for summaries) |
| Data | yfinance + Perplexity Sonar + ASX Appendix 4D/4E PDFs |

## Project structure

```
augur/
├── augur_api.py                    # FastAPI endpoints, job queue
├── pipeline.py                     # Orchestrates full simulation pipeline
├── seed_harvester/
│   ├── harvester.py                # Two-layer cache (6hr TTL)
│   ├── slow_layer.py               # yfinance + Sonnet analysis
│   ├── fast_layer.py               # Haiku sentiment + company intel + Perplexity
│   ├── perplexity_harvester.py     # Real-time financial news via Sonar
│   ├── structured_data.py          # 5-component ticker_bias_score
│   ├── cache.py                    # Cache logic
│   └── quality.py                  # Seed quality scoring
├── persona_forge/
│   └── forge.py                    # 50 agent creation (5 archetypes x 10, parallel)
├── negotiation_runner/
│   └── runner.py                   # 3-round structured debate engine
├── prediction_synthesiser/
│   └── synthesiser.py              # Final verdict, swing factors, report
├── asx_scraper/
│   ├── asx_api.py                  # ASX Markit Digital API client
│   ├── pdf_extractor.py            # EPS/revenue from Appendix 4D/4E PDFs
│   ├── ir_harvester.py             # Company IR page scraper
│   ├── company_intel.py            # Quarterly updates + investor presentations
│   ├── consensus_harvester.py      # Analyst consensus via yfinance + Perplexity
│   ├── price_scraper.py            # yfinance price reactions on earnings dates
│   ├── metrics_computer.py         # Beat rate, credibility scores
│   └── orchestrator.py             # Full scrape pipeline per ticker
├── monitoring/
│   └── grafana.py                  # Loki logging, Prometheus metrics, tracking decorator
├── db/
│   ├── schema.py                   # Neon schema (11 tables, 8 indexes)
│   └── retention.py                # Retention policy (7d failed, 24h batch)
├── frontend/                       # Next.js 14 App Router
│   ├── src/app/                    # Pages (/, /about, /simulation/[jobId])
│   ├── src/lib/grafana.ts          # Faro RUM + simulation event tracking
│   └── src/components/GrafanaInit.tsx  # Faro init (client component)
├── docs/
│   ├── Augur_Explainer.html        # GitHub Pages explainer page
│   └── Augur__The_Power_of_a_Debate.mp4  # Explainer video (37MB)
├── tests/
│   ├── unit/                       # Fast unit tests (no API calls)
│   ├── batch_test.py               # 20-ticker batch validation
│   └── date_validate.py            # Date-anchored integration tests
└── .env.example                    # Environment variable template
```

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Neon PostgreSQL database ([free tier](https://neon.tech) works)
- Anthropic API key (Claude access)
- Perplexity API key ([sonar](https://docs.perplexity.ai) — ~$0.005/query)
- Cloudflare R2 bucket (optional for local dev — seed cache falls back to Neon)

## Local setup

### Backend

```bash
git clone https://github.com/suhasatluri/Augur.git
cd Augur
pip install -r requirements.txt
cp .env.example .env
# Fill in your API keys in .env

# Run schema migration
python3 -m db.schema

# Start FastAPI backend
uvicorn augur_api:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
# Create .env.local with:
#   NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
# Visit http://localhost:3000
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `DATABASE_URL` | Yes | Neon PostgreSQL connection string |
| `AUGUR_API_KEYS` | Yes | Comma-separated valid API keys for authentication |
| `PERPLEXITY_API_KEY` | Yes | Perplexity Sonar API key |
| `STORAGE_ENDPOINT` | No | Cloudflare R2 endpoint (seed cache falls back to Neon) |
| `STORAGE_ACCESS_KEY` | No | R2 access key |
| `STORAGE_SECRET_KEY` | No | R2 secret key |
| `GRAFANA_LOKI_URL` | No | Grafana Loki log endpoint |
| `GRAFANA_LOKI_USER` | No | Grafana Loki user ID |
| `GRAFANA_API_KEY` | No | Grafana Cloud API key |
| `ENVIRONMENT` | No | Deployment environment (production/staging) |

`STORAGE_*` and `GRAFANA_*` vars are optional for local development. Without R2, seed caching uses the Neon `seed_data` JSONB column. Without Grafana, logging falls back to console.

## Running tests

```bash
# Unit tests — fast, no API calls, ~2s
pytest tests/unit/ -v

# Single ticker integration test (hits Claude API)
PYTHONUNBUFFERED=1 python3 tests/batch_test.py --tickers BHP

# Date-anchored validation (hits Claude API)
python3 tests/date_validate.py

# Full 20-ticker batch — costs ~$12 in API calls
# Do not run without checking with maintainers first
python3 tests/batch_test.py
```

All unit tests must pass before submitting a PR.

## Where to contribute

These are the highest-value areas, roughly in order of impact:

### 1. Data pipeline (`asx_scraper/`)
Beat/miss data accuracy is the single biggest lever for simulation quality. Historical beat rates from official ASX Appendix 4D/4E PDFs feed directly into the `ticker_bias_score`. Improving PDF extraction reliability and expanding coverage beyond the current top 20 tickers would meaningfully improve predictions.

### 2. Moderator agent
`moderator_agent.py` doesn't exist yet. The concept: a meta-agent that reads all 50 positions each round, identifies the weakest argument, and injects a challenge brief into the next round. This is Phase 4 on the roadmap — estimated +$0.10–0.15 per simulation in Claude API costs.

### 3. New agent archetypes
Currently 5 archetypes: Bull, Bear, Quant, Risk, Retail (10 agents each). Sector specialists — Mining Analyst, Tech Analyst, REIT Analyst — could improve verdict accuracy for tickers in those sectors. See `persona_forge/forge.py`.

### 4. Test coverage
`tests/unit/` covers core modules (seed quality, cache, persona forge, structured data). `asx_scraper/` and `seed_harvester/perplexity_harvester.py` need unit tests with mocked API responses.

### 5. Frontend
Next.js 14 frontend in `frontend/src/app/`. Open areas: batch simulation UI, historical results view, earnings calendar, simulation comparison.

## How to submit a PR

1. Fork the repo
2. Create a branch: `git checkout -b feat/your-feature`
3. Make your changes
4. Run unit tests: `pytest tests/unit/ -v` — all must pass
5. Keep PRs focused — one feature or fix per PR
6. Update `CLAUDE.md` if you add new modules or change key architecture
7. Open PR against `main` with a clear description of what changed and why

## Known limitations

- ASX 200 coverage only — smaller companies have thin yfinance data
- Simulation duration: ~125s (cache HIT) / ~175s (cache MISS)
- Beat/miss rate uses price reaction proxy (>+3% = BEAT, <-3% = MISS) — not perfect
- No user accounts in V1
- Company intel limited to top 20 tickers with known IR page URLs

## Licence

BSL 1.1 — free for non-commercial use. Converts to Apache 2.0 four years from first release date. Commercial use requires a licence — open a GitHub issue or contact via the repo.
