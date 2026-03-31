## Project Overview
Augur — swarm intelligence platform for ASX earnings prediction. V1.1 live at augur.vercel.app.
50 autonomous AI analyst agents debate earnings outcomes, anchored to real yfinance financial data.
BSL 1.1 licensed. GitHub: github.com/suhasatluri/Augur

## Architecture
- Backend: Python FastAPI on Railway
- Database: Neon PostgreSQL (separate from Railway)
- Frontend: Next.js 14 on Vercel
- Storage: Cloudflare R2 (seed cache)
- Queue: Upstash Redis (job queue)
- LLM: Claude API (Sonnet for agents, Haiku for summaries)
- Data: yfinance for structured financial data
- Edge: Cloudflare (always stays regardless of cloud)

## Pipeline Flow
POST /simulate → augur_api.py → pipeline.py →
seed_harvester (yfinance + Claude web_search) →
persona_forge (50 agents, bias-anchored) →
negotiation_runner (3 rounds) →
prediction_synthesiser → results in Neon

## Key Files
- augur_api.py — FastAPI endpoints, job queue
- pipeline.py — orchestrates full simulation
- seed_harvester/harvester.py — two-layer cache
- seed_harvester/slow_layer.py — yfinance + Sonnet
- seed_harvester/fast_layer.py — Haiku, date-anchored
- seed_harvester/structured_data.py — yfinance wrapper, ticker_bias_score computation
- persona_forge/forge.py — 50 agent creation
- negotiation_runner/runner.py — 3-round debate
- prediction_synthesiser/synthesiser.py — final report
- db/schema.py — Neon PostgreSQL schema
- frontend/src/app/ — Next.js App Router pages

## Critical Rules
- NEVER commit .env files
- NEVER commit private/ directory
- Always run git status before committing
- Test with --tickers flag before full batch
- Do not run full 20-ticker batch without approval
- API keys live in Railway env vars only
- Database: Neon only — never Supabase

## Environment Variables Required
ANTHROPIC_API_KEY — Claude API
DATABASE_URL — Neon PostgreSQL connection string
AUGUR_API_KEYS — comma-separated API keys
STORAGE_ENDPOINT — Cloudflare R2 endpoint
STORAGE_ACCESS_KEY — R2 access key
STORAGE_SECRET_KEY — R2 secret key

## Key Decisions Made
- yfinance replaces Claude web_search for structured financial data (earnings_history empty for ASX — use growth/consensus instead)
- 3 rounds not 5 (diminishing returns after round 3)
- Neon not Supabase (India outage incident)
- BSL 1.1 not MIT (commercial protection)
- ticker_bias_score anchors agent starting probabilities to real financial data
- reporting_date flows through entire pipeline for date-anchored macro context

## Current Known Limitations
- ASX 200 only
- Beat/miss history unavailable via yfinance for ASX — using analyst consensus proxy
- Simulation duration ~170s
- No user accounts in V1

## Test Commands
```bash
# Quick 3-ticker validation
python3 tests/quick_validate.py

# Date-anchored validation
python3 tests/date_validate.py

# Seed harvest test
python3 seed_harvester/test_harvester.py BHP --force

# Full 20-ticker batch (ask before running)
python3 tests/batch_test.py
```

## GitHub Actions
CI runs on every push/PR to main. Weekly regression on Sunday 2am AEST.
GitHub Actions needs these secrets set in repository Settings -> Secrets:
- ANTHROPIC_API_KEY
- DATABASE_URL

## V2 Priorities
1. Unit test suite (tests/unit/)
2. Outcome tracking (outcomes table exists, needs ingestion)
3. User accounts + simulation history
4. Beat/miss history data source
5. Email alerts for upcoming earnings
