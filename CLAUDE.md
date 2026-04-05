## Project Overview
Augur — swarm intelligence platform for ASX earnings prediction. V1.2 live at augur.vercel.app.
50 autonomous AI analyst agents debate earnings outcomes, anchored to proprietary ASX data pipeline.
BSL 1.1 licensed. GitHub: github.com/suhasatluri/Augur

## Architecture
- Backend: Python FastAPI on Railway
- Database: Neon PostgreSQL (8 indexes, CASCADE deletes, hourly retention cleanup)
- Frontend: Next.js 14 on Vercel
- Storage: Cloudflare R2 (seed cache)
- Queue: Upstash Redis (job queue)
- Monitoring: Grafana Cloud (Loki logs, Prometheus metrics at /metrics, Faro RUM in frontend)
- LLM: Claude API (Sonnet for agents + PDF extraction, Haiku for summaries)
- Data: ASX Markit API + PDFExtractor + yfinance (consensus EPS + supplementary)
- Edge: Cloudflare (always stays regardless of cloud)

## Pipeline Flow
POST /simulate → augur_api.py → pipeline.py →
seed_harvester (6hr cache → yfinance + Perplexity + ASX PDFs + company IR) →
persona_forge (50 agents parallel, bias-anchored) →
negotiation_runner (3 rounds) →
prediction_synthesiser → results in Neon

## Key Files
- augur_api.py — FastAPI endpoints, job queue
- pipeline.py — orchestrates full simulation (6-hour seed cache, parallel stages)
- asx_scraper/asx_api.py — ASX Markit Digital API client (official ASX data)
- asx_scraper/pdf_extractor.py — extracts EPS, revenue, management quotes from Appendix 4D/4E
- asx_scraper/ir_harvester.py — company IR pages, known PDF patterns for top 25
- asx_scraper/company_intel.py — quarterly updates + investor presentations from ASX companies
- asx_scraper/orchestrator.py — runs full scrape pipeline per ticker, stores to Neon
- asx_scraper/consensus_harvester.py — analyst consensus via yfinance + Perplexity Sonar
- asx_scraper/price_scraper.py — yfinance price reactions on earnings dates
- asx_scraper/metrics_computer.py — beat_rate, credibility scores from asx_earnings
- asx_scraper/finnhub_client.py — Finnhub API (disabled — US consensus, kept for reference)
- seed_harvester/harvester.py — two-layer cache
- seed_harvester/slow_layer.py — yfinance + Sonnet
- seed_harvester/fast_layer.py — Haiku sentiment + company intel + Perplexity news
- seed_harvester/perplexity_harvester.py — Perplexity Sonar real-time financial news
- seed_harvester/structured_data.py — 5-component ticker_bias_score
- persona_forge/forge.py — 50 agent creation (5 archetypes forged in parallel via asyncio.gather)
- negotiation_runner/runner.py — 3-round debate
- prediction_synthesiser/synthesiser.py — final report
- monitoring/grafana.py — Grafana Cloud: Loki logging, Prometheus metrics, simulation tracking decorator
- db/schema.py — Neon PostgreSQL schema (11 tables, 8 indexes, CASCADE deletes, seed_data JSONB on simulations)
- db/retention.py — retention policy (7d failed, 24h batch, reasoning compression)
- conftest.py — pytest root path setup
- tests/batch_test.py — 20-ticker batch validation (--tickers flag for subset runs)
- frontend/src/app/ — Next.js App Router pages
- frontend/src/lib/grafana.ts — Grafana Faro RUM + simulation event tracking
- frontend/src/components/GrafanaInit.tsx — Faro initialisation (client component in root layout)
- frontend/public/about.html — Full explainer page (How It Works) with embedded video
- docs/Augur_Explainer.html — GitHub Pages version of explainer (source of truth)
- docs/Augur__The_Power_of_a_Debate.mp4 — Explainer video (37MB, served from GitHub Pages CDN)
- CONTRIBUTING.md — Contributor guide: setup, tests, PR process, contribution areas

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
PERPLEXITY_API_KEY — Perplexity Sonar (real-time financial news in fast layer, ~$0.005/query)
GRAFANA_LOKI_URL — Grafana Loki log endpoint
GRAFANA_LOKI_USER — Grafana Loki user ID
GRAFANA_API_KEY — Grafana Cloud API key
ENVIRONMENT — deployment environment (production/staging)
FINNHUB_API_KEY — Finnhub.io (disabled, kept for potential US coverage)

### Frontend Environment Variables (Vercel)
NEXT_PUBLIC_GRAFANA_FARO_URL — Grafana Faro collector endpoint
NEXT_PUBLIC_GRAFANA_APP_NAME — Faro app name (augur-frontend)
NEXT_PUBLIC_API_URL — Backend API URL

## Key Decisions Made
- Built proprietary ASX data pipeline — PDFExtractor reads official Appendix 4D/4E documents directly. CompanyIntelHarvester fetches quarterly updates and presentations from company IR pages. No third-party data dependency for historical results.
- ASX Markit Digital API (asx.api.markitdigital.com) is the primary data source — free, no API key, official ASX data
- Finnhub disabled for ASX — US-listed consensus diverges from ASX analyst expectations (different market, currency, analyst pool)
- Price reaction proxy for beat/miss — measures actual ASX market response (>+3% BEAT, <-3% MISS)
- ticker_bias_score uses 5 components: recommendation (28%), upside (22%), growth (20%), beat rate (20%), company intel (10%)
- yfinance is supplementary for current prices/recommendations — not primary data source
- 3 rounds not 5 (diminishing returns after round 3)
- Neon not Supabase (India outage incident)
- BSL 1.1 not MIT (commercial protection)
- reporting_date flows through entire pipeline for date-anchored macro context
- Parallel persona forge via asyncio.gather() — all 50 agents forged simultaneously (5 archetypes x 10 agents, one Sonnet call each), saving ~25-30s per simulation
- 6-hour seed cache — if ticker simulated in last 6hr, return cached seed from Neon (seed_data JSONB). Cache key is ticker only. Skips cache if seed quality < 0.6. BHP cache HIT confirmed at 124.5s vs 182s baseline
- Phase 3 complete — target duration now 125-180s depending on cache HIT or MISS

## Current Known Limitations
- ASX 100 only (asx_scraper bootstrapped for top tickers)
- Beat/miss uses yfinance earnings_estimate consensus (forward EPS + yearAgoEps for latest beat/miss)
- BHP PDFs timeout from bhp.com CDN — ASX API provides fallback data
- Simulation duration: 125s (cache HIT) / 175s (cache MISS) — Phase 3 complete
- ALU yfinance data unavailable (404) — falls back to neutral bias, still completes
- No user accounts in V1
- Company intel limited to top 20 tickers with known IR page URLs

## Test Commands
```bash
# Unit tests (23 tests, runs in ~2s)
python3 -m pytest tests/unit/ -v

# ASX scraper — single ticker
python3 -m asx_scraper CBA

# ASX scraper — show stored data
python3 -m asx_scraper --show CBA

# Historical validation dry-run (no simulations)
python3 tests/historical_validate.py --dry-run

# Quick 3-ticker validation
python3 tests/quick_validate.py

# Seed harvest test
python3 seed_harvester/test_harvester.py BHP --force

# Batch test — subset (use before full batch)
python3 tests/batch_test.py --tickers XRO CSL BHP

# Full 20-ticker batch (ask before running)
python3 tests/batch_test.py
```

## GitHub Actions
CI runs on every push/PR to main. Weekly regression on Sunday 2am AEST.
Hourly DB retention cleanup via db_cleanup.yml (deletes failed sims >7d, batch sims >24h, compresses reasoning).
GitHub Actions needs these secrets set in repository Settings -> Secrets:
- ANTHROPIC_API_KEY
- DATABASE_URL

## Observability
- Grafana Cloud observability live in production (replaced Sentry plan)
- Backend: Loki structured logging + Prometheus metrics at /metrics
- Metrics: augur_simulations_total, augur_simulation_duration_seconds, augur_simulation_errors_total, augur_seed_quality_score, augur_active_simulations, augur_api_requests_total
- Frontend: Grafana Faro RUM + Web Vitals + console capture + tracing
- Frontend events: simulation_started, simulation_complete, simulation_error
- track_simulation() decorator in monitoring/grafana.py — not yet wired into pipeline.py

## Pages
- / — Homepage (simulation form, community activity, video teaser)
- /about — Full explainer page (How It Works, embedded video from GitHub Pages CDN)
- /simulation/[jobId] — Simulation progress + results
- Explainer video: https://suhasatluri.github.io/Augur/Augur__The_Power_of_a_Debate.mp4
- GitHub Pages explainer: https://suhasatluri.github.io/Augur/Augur_Explainer.html

## V2 Priorities
1. ~~Sentry error tracking~~ — DONE (replaced with Grafana Cloud: Loki + Prometheus + Faro)
2. Moderator agent — moderator_agent.py (Phase 4, +$0.10-0.15/sim)
3. ~~Unit test suite (tests/unit/)~~ — DONE (23 tests)
4. ~~ASX data pipeline~~ — DONE (PDFExtractor, IRHarvester, CompanyIntelHarvester)
5. Wire track_simulation() decorator into pipeline.py for per-simulation metrics
6. Outcome tracking (outcomes table exists, needs ingestion)
7. User accounts + simulation history
8. Bootstrap asx_scraper across full ASX 100
9. Schedule weekly asx_scraper refresh via GitHub Actions cron
10. Email alerts for upcoming earnings
