# Augur

> Read the signal before it speaks.

Swarm intelligence platform for financial market simulation.

**V1.2: ASX Earnings Surprise Predictor** — spawns 50 autonomous
analyst agents, seeds them with proprietary ASX data extracted from
official earnings PDFs, runs a multi-round debate, returns a probability
distribution of earnings beat/miss before the company reports.

## Status
V1.2 — proprietary ASX data pipeline complete. Augur now reads official
Appendix 4D/4E PDFs directly from ASX announcements and company IR pages.
Company intel harvester fetches quarterly updates and investor presentations
as leading indicators between reporting seasons.

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
              seed_harvester (slow + fast layers)
                             │
                             ▼
              persona_forge (50 agents)
                             │
                             ▼
              negotiation_runner (3 rounds)
                             │
                             ▼
              prediction_synthesiser → verdict
```

## Data Sources

| Source | Data | Coverage |
|--------|------|----------|
| ASX Markit API | Revenue, NPAT, EPS, PE, dividends | All ASX tickers |
| PDFExtractor | Detailed financials from Appendix 4D/4E | Top 25+ ASX |
| IRHarvester | Earnings PDFs from company IR pages | Top 25 ASX |
| CompanyIntelHarvester | Quarterly updates, investor presentations | Top 20 ASX |
| ConsensusHarvester | Forward consensus EPS, beat/miss history | All ASX tickers |
| Perplexity Sonar | Real-time financial news, analyst sentiment | All ASX tickers |
| Price Reaction Proxy | Beat/miss fallback from market response | All ASX tickers |
| yfinance | Current prices, recommendations, growth | All ASX tickers |

## Stack
- Python FastAPI · Neon PostgreSQL · Claude API
- Next.js 14 · Cloudflare R2 · Railway
- ASX Markit Digital API · yfinance

## Architecture
- **asx_scraper/** — proprietary data pipeline (PDFs, ASX API, company intel)
- **seed_harvester/** — two-layer cache (slow: yfinance+Claude, fast: sentiment+intel)
- **persona_forge/** — 50 analyst agents with bias-anchored starting probabilities
- **negotiation_runner/** — 3-round structured debate
- **prediction_synthesiser/** — final verdict with confidence intervals

## Licence
BSL 1.1 — free for non-commercial use.
Converts to Apache 2.0 after 4 years.
See [LICENSE](LICENSE).

## Contributing
Contributors welcome. See CONTRIBUTING.md (coming soon).
