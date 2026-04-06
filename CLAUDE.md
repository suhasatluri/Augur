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
- Monitoring: Sentry (sentry-sdk[fastapi] backend + @sentry/nextjs frontend)
- LLM: Claude API (Sonnet for agents + PDF extraction, Haiku for summaries)
- Data: ASX Markit API + PDFExtractor + yfinance + ASIC short interest + Market Index (curl_cffi)
- Edge: Cloudflare (always stays regardless of cloud)

## Pipeline Flow
POST /simulate → augur_api.py → pipeline.py →
seed_harvester (6hr cache → yfinance + Perplexity + ASX PDFs + ASIC + Market Index) →
persona_forge (50 agents parallel, bias-anchored) →
negotiation_runner (3 rounds + moderator between rounds) →
prediction_synthesiser → verdict + swing factors → Neon

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
- asx_scraper/sources/asic_short_interest.py — ASIC daily short position data (669 tickers, free)
- asx_scraper/sources/marketindex.py — Market Index scraper via curl_cffi (director trades + financials)
- asx_scraper/sources/director_trades.py — Appendix 3Y director trade extractor via ASX CDN
- seed_harvester/harvester.py — two-layer cache
- seed_harvester/slow_layer.py — yfinance + Sonnet
- seed_harvester/fast_layer.py — Haiku sentiment + company intel + Perplexity news
- seed_harvester/perplexity_harvester.py — Perplexity Sonar real-time financial news (module-level session cost accumulator)
- scripts/earnings_calendar_harvester.py — dual-source earnings calendar refresh (yfinance + Perplexity Sonar)
- seed_harvester/structured_data.py — 6-component ticker_bias_score (analyst, upside, growth, beat_rate, short_interest, director)
- persona_forge/forge.py — 50 agent creation (5 archetypes forged in parallel via asyncio.gather)
- negotiation_runner/runner.py — 3-round debate
- prediction_synthesiser/synthesiser.py — final report
- negotiation_runner/moderator.py — structural moderator between debate rounds (Haiku)
- db/schema.py — Neon PostgreSQL schema (13 tables, 10+ indexes, CASCADE deletes, seed_data JSONB, market signal columns, token cost tracking)
- db/retention.py — retention policy (7d failed, 24h batch, reasoning compression)
- conftest.py — pytest root path setup
- tests/batch_test.py — 20-ticker batch validation (--tickers flag for subset runs)
- frontend/src/app/ — Next.js App Router pages
- frontend/src/app/admin/page.tsx — Admin dashboard (token costs incl. Perplexity, daily activity, top tickers, feedback stats)
- frontend/src/components/EarningsCalendar.tsx — Upcoming earnings widget (5 entries, "View all" link to /calendar)
- frontend/src/app/calendar/page.tsx — Full /calendar page (search, sector filter, week grouping, Simulate deep-links)
- frontend/sentry.client.config.ts — Sentry frontend error tracking
- frontend/sentry.server.config.ts — Sentry server-side error tracking
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
PERPLEXITY_API_KEY — Perplexity Sonar (real-time financial news + earnings calendar, ~$0.005/query)
SENTRY_DSN_BACKEND — Sentry error tracking DSN (Railway)
ADMIN_SECRET — protects /admin/stats endpoint (X-Admin-Secret header)
FINNHUB_API_KEY — Finnhub.io (disabled, kept for potential US coverage)

### Frontend Environment Variables (Vercel)
NEXT_PUBLIC_SENTRY_DSN — Sentry error tracking DSN
NEXT_PUBLIC_API_URL — Backend API URL

## Key Decisions Made
- Built proprietary ASX data pipeline — PDFExtractor reads official Appendix 4D/4E documents directly. CompanyIntelHarvester fetches quarterly updates and presentations from company IR pages. No third-party data dependency for historical results.
- ASX Markit Digital API (asx.api.markitdigital.com) is the primary data source — free, no API key, official ASX data
- Finnhub disabled for ASX — US-listed consensus diverges from ASX analyst expectations (different market, currency, analyst pool)
- Price reaction proxy for beat/miss — measures actual ASX market response (>+3% BEAT, <-3% MISS)
- ticker_bias_score uses 6 components: analyst (25%), upside (20%), growth (15%), beat_rate (15%), short_interest (15%), director_signal (10%)
- yfinance is supplementary for current prices/recommendations — not primary data source
- 3 rounds not 5 (diminishing returns after round 3)
- Neon not Supabase (India outage incident)
- BSL 1.1 not MIT (commercial protection)
- reporting_date flows through entire pipeline for date-anchored macro context
- Parallel persona forge via asyncio.gather() — all 50 agents forged simultaneously (5 archetypes x 10 agents, one Sonnet call each), saving ~25-30s per simulation
- 6-hour seed cache — if ticker simulated in last 6hr, return cached seed from Neon (seed_data JSONB). Cache key is ticker only. Skips cache if seed quality < 0.6. BHP cache HIT confirmed at 124.5s vs 182s baseline
- Phase 3 complete — target duration now 125-180s depending on cache HIT or MISS
- Earnings calendar dual-source: yfinance primary (free, structured), Perplexity gap-fills where yfinance returns nothing. Top 15 large caps cross-checked by both. Both agree within 7 days → confidence=high. Never overwrites confirmed/manual entries. Harvester has 24h skip-recent TTL (resume support), per-call Perplexity timeout=8s, per-ticker asyncio.wait_for(15s) to prevent hangs. Cost ~$0.20/week. Current state: 141 future records (Apr–Aug 2026 reporting season), 11 sectors populated via yfinance backfill into asx_companies
- Perplexity cost tracking: module-level session accumulator in perplexity_harvester.py, reset per simulation. $0.005 flat + $1/M tokens input + $1/M tokens output

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

# Earnings calendar refresh — specific tickers
python3 -m scripts.earnings_calendar_harvester BHP XRO CBA

# Earnings calendar refresh — all ASX 200
python3 -m scripts.earnings_calendar_harvester
```

## GitHub Actions
CI runs on every push/PR to main. Weekly regression on Sunday 2am AEST.
Hourly DB retention cleanup via db_cleanup.yml (deletes failed sims >7d, batch sims >24h, compresses reasoning).
GitHub Actions needs these secrets set in repository Settings -> Secrets:
- ANTHROPIC_API_KEY
- DATABASE_URL

## Monitoring
- Sentry error tracking (sentry.io)
- Backend: sentry-sdk[fastapi] with FastApiIntegration + AsyncioIntegration + LoggingIntegration
  Errors auto-captured, WARNING+ logs sent to Sentry, 10% performance tracing
  Tagged per ticker + simulation_id in pipeline.py
- Frontend: @sentry/nextjs with client + server configs
  Production only, 10% tracing, source maps hidden
- DSNs: SENTRY_DSN_BACKEND (Railway), NEXT_PUBLIC_SENTRY_DSN (Vercel)

## Pages
- / — Homepage (simulation form, earnings calendar, community activity, video teaser)
- /about — Full explainer page (How It Works, embedded video from GitHub Pages CDN)
- /simulation/[jobId] — Simulation progress + results
- /admin — Admin dashboard (login via ADMIN_SECRET, Grafana-style time range picker, token cost breakdown incl. Perplexity Sonar, daily activity, top tickers, recent simulations, feedback stats)
- /calendar — Full ASX earnings calendar (search, sector pills, week-grouped, Simulate button → /?ticker=XYZ&date=YYYY-MM-DD deep-link to homepage)
- Explainer video: https://suhasatluri.github.io/Augur/Augur__The_Power_of_a_Debate.mp4
- GitHub Pages explainer: https://suhasatluri.github.io/Augur/Augur_Explainer.html

## V2 Priorities
1. ~~Sentry error tracking~~ — DONE (sentry-sdk[fastapi] + @sentry/nextjs)
2. ~~Moderator agent~~ — DONE (negotiation_runner/moderator.py, Haiku, ~$0.02-0.04/sim)
3. ~~Unit test suite (tests/unit/)~~ — DONE (23 tests)
4. ~~ASX data pipeline~~ — DONE (PDFExtractor, IRHarvester, CompanyIntelHarvester)
5. ~~ASX 100 bootstrap~~ — DONE (ASIC 89/100, Market Index 100/100, director signals for all)
6. ~~Admin dashboard + token cost tracking~~ — DONE (GET /admin/stats, Grafana-style time picker, Sonnet/Haiku/Perplexity cost breakdown)
7. ~~Earnings calendar~~ — DONE (dual-source: yfinance + Perplexity, GET /calendar, homepage component with confidence dots)
8. Outcome tracking (outcomes table exists, needs ingestion)
9. User accounts + simulation history
10. Schedule weekly asx_scraper + calendar refresh via GitHub Actions cron
11. Email alerts for upcoming earnings
