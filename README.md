# Augur

> Read the signal before it speaks.

Swarm intelligence platform for financial market simulation.

**V1.2: ASX Earnings Surprise Predictor** — spawns 50 autonomous
analyst agents, seeds them with proprietary ASX data extracted from
official earnings PDFs, runs a multi-round debate, returns a probability
distribution of earnings beat/miss before the company reports.

## Status
V1.2 — proprietary ASX data pipeline complete. Admin dashboard live with
token cost tracking (Sonnet/Haiku/Perplexity), daily activity, and
Grafana-style time range picker. Earnings calendar with dual-source date
discovery (yfinance + Perplexity Sonar) and confidence indicators.
Augur reads official Appendix 4D/4E PDFs directly from ASX announcements
and company IR pages. Company intel harvester fetches quarterly updates and
investor presentations as leading indicators between reporting seasons.

Live at [augur.vercel.app](https://augur.vercel.app)

## Learn How It Works
- [Interactive Explainer](https://suhasatluri.github.io/Augur/docs/Augur_Explainer.html) — animated walkthrough for non-technical users
- [Live Demo](https://augur.vercel.app)

## Data Pipeline

```
ASX Markit API ─────────────┐
                             ├─→ asx_companies (Neon)
Company IR Pages ──→ PDFs ──→├─→ asx_earnings (Neon)
                             ├─→ asx_commentary (Neon)
announcements.asx.com.au ──┘├─→ asx_metrics (Neon)
                             │
Quarterly Updates ──→ PDFs ──→─→ asx_company_intel (Neon)
Investor Presentations ─────┘
                             │
yfinance ──→ current prices ─┤
             recommendations ┤
                             ▼
              seed cache (6hr TTL in Neon)
              ├─ HIT ──→ skip harvest (~125s)
              └─ MISS ─→ seed_harvester (slow + fast layers)
                             │
                             ▼
              persona_forge (50 agents, 5 parallel Sonnet calls)
                             │
                             ▼
              negotiation_runner (3 rounds, parallel archetype batches)
                  ↕ moderator_agent (between rounds — Haiku)
                  │  extracts bull/bear arguments
                  │  challenges outliers, flags dissenters
                  │  tracks swing factors
                             │
                             ▼
              prediction_synthesiser → verdict + swing factors
```

## Data Sources

| Source | Data | Coverage |
|--------|------|----------|
| ASX Markit API | Revenue, NPAT, EPS, PE, dividends | All ASX tickers |
| PDFExtractor | Detailed financials from Appendix 4D/4E | Top 25+ ASX |
| IRHarvester | Earnings PDFs from company IR pages | Top 25 ASX |
| CompanyIntelHarvester | Quarterly updates, investor presentations | Top 20 ASX |
| ConsensusHarvester | Forward consensus EPS, beat/miss history | All ASX tickers |
| Perplexity Sonar | Real-time financial news, analyst sentiment, earnings calendar gap-fill | All ASX tickers |
| Price Reaction Proxy | Beat/miss fallback from market response | All ASX tickers |
| yfinance | Current prices, recommendations, growth | All ASX tickers |
| ASIC Short Interest | Daily short position data | 669 ASX tickers |
| Market Index | Director transactions, 10-year financials | ASX 100 |

## Stack
- Python FastAPI · Neon PostgreSQL · Claude API
- Next.js 14 · Cloudflare R2 · Railway · Upstash Redis
- ASX Markit Digital API · yfinance · curl_cffi

## Architecture
- **asx_scraper/** — proprietary data pipeline (PDFs, ASX API, company intel, ASIC shorts, Market Index)
- **seed_harvester/** — two-layer cache (slow: yfinance+Claude, fast: sentiment+intel)
- **persona_forge/** — 50 analyst agents with bias-anchored starting probabilities
- **negotiation_runner/** — 3-round structured debate with moderator agent
- **negotiation_runner/moderator.py** — structural moderator between rounds: extracts top arguments, challenges outliers, flags high-conviction dissenters, tracks swing factors
- **prediction_synthesiser/** — final verdict with confidence intervals and moderator-identified swing factors
- **scripts/** — earnings calendar harvester (dual-source: yfinance + Perplexity, confidence scoring)
- **admin dashboard** — protected `/admin` page with token cost breakdown (Sonnet/Haiku/Perplexity), daily activity, top tickers, recent simulations, feedback stats, Grafana-style time range picker
- **earnings calendar** — `/calendar` API + homepage widget + full `/calendar` page with search, sector filter, week-grouped season view. ~141 future ASX 200 reports populated; Simulate button deep-links to homepage with ticker + date pre-filled (`/?ticker=ORG&date=2026-04-10`). Confidence dots (green=multi-source, amber=single-source, red=estimated)

## Licence
BSL 1.1 — free for non-commercial use.
Converts to Apache 2.0 after 4 years.
See [LICENSE](LICENSE).

## Contributing
Contributors welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
