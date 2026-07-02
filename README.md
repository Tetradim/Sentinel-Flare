# Sentinel Flare

Sentinel Flare is a FastAPI, React, and Discord alerting system for monitoring large off-exchange prints, clustering them into price levels, and scoring confluence against dealer-exposure-style levels and options-flow context.

This is an intelligence and alerting tool. It does not auto-trade and should not be treated as financial advice. Dark pool prints are contextual levels, not standalone buy or sell signals.

## Current Capabilities

- FastAPI backend with health checks, provider discovery, compatibility routes, and dark pool intelligence endpoints.
- React dashboard with MAG7 simulation, live feed, trade-intent review, scanner, options dashboard, flow map, alerts, watchlists, replay, admin, settings, CSV export, and persisted filters.
- Discord bot with slash commands for dark pool level summaries and user-configured whale thresholds.
- Offline `demo` provider so the app can run without paid data keys.
- FINRA provider support for public weekly ATS/OTC aggregate data.
- Dark pool level engine that clusters prints by symbol and price bucket.
- Heatseeker-style context concepts: king node, floor, ceiling, gatekeepers, air pockets, and confluence scoring.
- Alert candidate generation with severity, reasons, score, notional, and deduplication.
- Trade-intent gate with user-adjustable score, distance, notional, freshness, risk, signal-quality, and source-confirmation controls before Sentinel Edge confirmation and Pulse packet preparation.
- Confidence attribution for trade intents, showing dark pool level strength, price proximity, exposure alignment, options flow, print clustering, and freshness contributions.
- Signal quality flags for trade intents, showing whether dark pool side bias, options flow, and exposure evidence support, conflict with, or are missing from the candidate action.
- Risk envelopes for trade intents with candidate side, entry, stop, target, whole-share sizing, share-rounded notional, per-share risk/reward, estimated loss, and estimated gain.
- Market-regime controls that classify trend-up, trend-down, range-bound, high-volatility, or insufficient-data conditions from recent prints before Sentinel review.
- Session drawdown, volatility-cap, allowed-regime, and volatility-adjusted-stop gates based on common crypto-bot risk-management patterns.
- Lightweight print follow-through backtest metrics for win rate, profit factor, expectancy, cumulative return, and max drawdown.
- Source-confirmation coverage planning for price/NBBO, liquidity/depth, halt/LULD, material-news, and optional options-flow confirmation before Edge-routed Pulse review packet preparation.
- Edge-routed Pulse review guardrails that require Sentinel Edge approval, explicit manual execution, risk checks, source-coverage audit metadata, and rejection of direct Pulse destinations or live-order packet keys.
- Python and frontend test coverage for provider behavior, route smoke checks, options endpoints, alerting, confluence, level clustering, Discord command handling, z-scores, CSV export, and frontend build.

## Production Posture

The project is now structured so the bot can run locally in demo mode immediately, then be pointed at live providers as credentials become available.

Use `provider=demo` for local development, tests, and UI demos.
Use `provider=finra` for public FINRA aggregate data. FINRA data is useful for aggregate venue context, but it is not a real-time dark pool tape.

The app intentionally requires price confirmation and human review. It should not place orders automatically from a single print, confluence score, Discord alert, or Pulse packet.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
npm install
```

Copy the environment template if you want live keys or a Discord bot:

```bash
copy .env.example .env
```

Run the backend:

```bash
python server.py
```

Run the frontend:

```bash
npm run dev
```

On Windows, double-click `Launch-Sentinel-Flare.bat` from the repo root to start the backend on `8002` and the frontend on `3002`.

Open http://localhost:3002 when using the launcher. Manual `npm run dev` still uses Vite's default port unless you pass `-- --port 3002`.

The Vite dev server proxies API-backed views to the FastAPI backend on `http://127.0.0.1:8002`.

## macOS Beta Installer

MacBook beta testers can install the local source build with the bundled macOS installer script. It creates the Python virtual environment, installs frontend dependencies, and adds a double-click launcher to the Desktop.

Prerequisites:

- macOS with Python 3.11+ on `PATH`
- Node.js with `npm`

From the repository root:

```bash
chmod +x install-macos.sh
./install-macos.sh
```

After installation, double-click `Sentinel Flare.command` on the Desktop. The launcher starts the backend on `8002`, starts the frontend on `3002`, and opens the dashboard. Logs are written to `~/Desktop/Sentinel-Flare.log`.

Manual launch options:

```bash
./install-macos.sh --launch
./install-macos.sh --launch --install-deps
./install-macos.sh --launch --backend-port 8002 --frontend-port 3002 --no-browser
```

Run the Discord bot:

```bash
python discord_bot.py
```

## Environment Variables

- `SECRET_KEY`: persistent secret used for JWTs and password hashing.
- `DARKPOOL_EVENT_BUS_SECRET`: required shared secret for `/api/bus/events` reads and writes. Requests must send it in the `X-Darkpool-Event-Bus-Secret` header; the bus fails closed when this value is unset, a known placeholder, or shorter than 32 characters.
- `DARKPOOL_EDGE_DECISION_SECRET`: required shared secret for Sentinel Edge decision callbacks. Requests to `/darkpool/trade-intent/edge-decision` must send it in `X-Darkpool-Edge-Secret`; the callback fails closed when this value is unset, a known placeholder, or shorter than 32 characters.
- `PORT`: FastAPI port, default `8002`.
- `FRONTEND_URL`: CORS origin for the Vite app.
- `POLYGON_API_KEY`: optional future market-data integration.
- `INTRINIO_API_KEY`: optional future market-data integration.
- `UNUSUAL_WHALES_API_KEY`: optional future options-flow/GEX integration.
- `NASDAQ_HALTS_RSS_ENABLED`: set to `true` when a halt/LULD confirmation adapter is configured.
- `SEC_EDGAR_USER_AGENT`: SEC EDGAR user-agent string used to mark material-news source coverage as configured.
- `DISCORD_BOT_TOKEN`: Discord bot token.
- `DISCORD_GUILD_ID`: optional guild ID for faster command sync during development.
- `DISCORD_PUBLIC_KEY`: Discord application public key for verifying HTTP interaction signatures.
- `ALLOW_UNSIGNED_DISCORD_INTERACTIONS`: set to `true` only for local unsigned Discord command payload tests.
- `DISCORD_WEBHOOK_URL`: optional webhook for backend alert routing.
- `WHALE_THRESHOLD`: default share threshold for whale alerts.

## Key API Routes

Core:

- `GET /`
- `GET /health`
- `GET /providers`
- `GET /health/circuit`
- `POST /health/circuit/{provider}/reset`

Compatibility:

- `GET /api/full?symbol=AAPL&provider=demo`
- `GET /api/aggregate/AAPL?provider=demo`
- `GET /api/sentiment?provider=demo`

Dark pool intelligence:

- `GET /darkpool/otc?symbol=AAPL&provider=demo`
- `GET /darkpool/trades?symbol=AAPL&provider=demo`
- `GET /darkpool/levels?symbol=AAPL&provider=demo`
- `GET /darkpool/confluence?symbol=AAPL&provider=demo`
- `GET /darkpool/alert-candidates?symbol=AAPL&provider=demo`
- `GET /darkpool/backtest?symbol=AAPL&provider=demo&fee_bps=2&trade_limit=50`
- `GET /darkpool/information-sources?active_provider=finra`
- `GET /darkpool/trade-intent?symbol=AAPL&provider=demo&min_score=75&max_distance_pct=1&min_notional=25000000&max_risk_dollars=500&stop_distance_pct=1&reward_risk_ratio=2&max_position_notional=50000&max_session_drawdown_pct=5&current_session_drawdown_pct=0&max_regime_volatility_pct=10&allow_trend_up=true&allow_trend_down=true&allow_range_bound=true&allow_high_volatility=false&use_volatility_adjusted_stop=true&max_quality_caution_flags=99&min_quality_support_flags=0&min_source_confirmation_weight=0&require_source_coverage_complete=true&price_confirmed=true&liquidity_confirmed=true&news_checked=true&observed_spread_bps=5&max_spread_bps=25`
- `POST /darkpool/trade-intent/edge-decision` with `X-Darkpool-Edge-Secret`

Scanner and visualization:

- `GET /scanner/prints`
- `GET /scanner/heatmap`
- `GET /chart/heatmap`
- `GET /visualization/area`
- `GET /visualization/bar`
- `GET /visualization/combined`
- `GET /grafana/table`
- `GET /grafana/timeseries`

Options dashboard:

- `GET /options/highest-call-vol`
- `GET /options/highest-put-vol`
- `GET /options/high-vol-cheapies`
- `GET /options/high-vol-leaps`
- `GET /options/most-otm-strikes`
- `GET /options/large-otm-oi`
- `GET /marketcap/milestones`

Alerts and watchlists:

- `GET /alerts/config`
- `POST /alerts/config`
- `DELETE /alerts/config/{symbol}`
- `GET /alerts/check`
- `GET /alerts/whale-feed`
- `POST /alerts/route`
- `POST /alerts/webhook`
- `GET /watchlists`
- `POST /watchlists`

Cross-bot event bus:

- `GET /api/bus/events?target=sentinel-edge` with `X-Darkpool-Event-Bus-Secret`.
- `POST /api/bus/events` with `X-Darkpool-Event-Bus-Secret`.

The event bus is for local bot-to-bot observation and review packets. It is not an order-entry surface, and it rejects unauthenticated reads or writes.

Discord integration:

- `GET /discord/watchlist-summary?symbols=AAPL,NVDA,MSFT`
- `POST /discord/subscriptions?channel_id=...&topic=alerts&symbols=AAPL,NVDA&min_score=70`
- `GET /discord/subscriptions?channel_id=...`
- `DELETE /discord/subscriptions/{subscription_id}?channel_id=...`
- `POST /discord/commands`

## Discord Commands

- `/darkpool symbol:AAPL provider:demo`: show a combined dark pool, confluence, and alert summary.
- `/levels symbol:AAPL provider:demo`: show clustered dark pool context levels.
- `/confluence symbol:AAPL provider:demo`: show dark pool, exposure-node, and options-flow confluence.
- `/alerts symbol:AAPL provider:demo`: show explainable alert candidates.
- `/watchlist symbols:AAPL,NVDA,MSFT provider:demo`: summarize the highest-ranked candidate per ticker.
- `/subscribe topic:alerts symbols:AAPL,NVDA min_score:70 provider:demo`: subscribe the current channel to an autopost topic.
- `/subscriptions`: list autopost subscriptions for the current channel.
- `/unsubscribe subscription_id:<id>`: remove an autopost subscription from the current channel.
- `/setalert symbol:AAPL threshold:100000`: set a whale threshold in shares.
- `/alertstatus`: list configured alert thresholds.
- `/removealert symbol:AAPL`: remove a threshold.

## Trade Intent Gate

`GET /darkpool/trade-intent` turns the strongest confluence score into a user-readable intent report. It applies user-controlled thresholds for minimum score, maximum distance from spot, minimum notional value, maximum level freshness, allowed buy/sell sides, max risk dollars, stop distance, reward/risk ratio, max position notional, maximum session drawdown, maximum market-regime volatility, allowed market regimes, volatility-adjusted stops, max quality caution flags, minimum quality support flags, minimum configured source confirmation weight, required source-coverage completion, and source-coverage override reason.

The React dashboard exposes the same workflow in the `Intent` view, with controls for symbol, provider, confidence threshold, distance threshold, notional threshold, level freshness, risk envelope, session drawdown, regime volatility cap, allowed regimes, volatility-adjusted stops, signal-quality gates, allowed buy/sell sides, required source-coverage completion, source-coverage override reason, Sentinel confirmation checks, spread guardrails, and Pulse packet inclusion. The view also shows the market regime, source confirmation plan, and Sentinel checklist used to approve or reject Pulse preparation.

Recommended operator flow:

1. Review `/darkpool/information-sources` or the dashboard source plan for missing required source families.
2. Check the market-regime summary for range, momentum, volume imbalance, and trend bias.
3. Build a trade intent with source coverage required and high-volatility regimes disabled by default.
4. If the intent is blocked, resolve missing price/NBBO, liquidity/depth, halt/LULD, material-news coverage, drawdown, or regime blockers before Sentinel review.
5. Use `require_source_coverage_complete=false` only for an audited demo or manual-vendor-review exception, and provide `source_coverage_override_reason`.
6. Confirm price, liquidity, news, and spread through Sentinel Edge before requesting any Pulse packet.
7. Treat a prepared Pulse packet as a manual review packet only. It is not a live order.

Audited source-coverage override example:

```text
GET /darkpool/trade-intent?symbol=AAPL&provider=demo&min_score=60&max_distance_pct=2&min_notional=1000000&require_source_coverage_complete=false&source_coverage_override_reason=manual-vendor-check-complete&price_confirmed=true&liquidity_confirmed=true&news_checked=true&observed_spread_bps=5&max_spread_bps=20&include_pulse_packet=true
```

The endpoint returns:

- `intent`: a readable `BUY`, `SELL`, or safe `HOLD` outcome with reasons and blockers.
- `market_regime`: print-derived trend and volatility context used by the regime gates.
- `intent.confidence_breakdown`: component-level score attribution for operator review before confirmation.
- `intent.source_confirmation_weight` and `intent.source_adjusted_confidence`: configured confirmation-source coverage on a normalized `0.00` to `1.00` scale and the raw confluence confidence discounted by that coverage. Raw `intent.confidence` is still returned so operators can separate pattern strength from source confirmation strength.
- `intent.missing_required_source_coverage`: structured labels for missing required source families when the source-coverage gate blocks the intent.
- `intent.quality_flags`: support, caution, and missing-data flags for dark pool side bias, options flow, and exposure evidence. `max_quality_caution_flags` and `min_quality_support_flags` can block an intent before Sentinel approval.
- `intent.risk_plan`: a planning envelope with the candidate `planned_action`, entry price, estimated whole shares, share-rounded notional, per-share risk/reward, entry-to-stop estimated loss, entry-to-target estimated gain, max risk, requested stop distance, effective stop distance, volatility-adjustment metadata, stop, and target. The stop is anchored from the darkpool level, can be widened by the regime volatility buffer, and the target is derived from entry-to-stop risk and the configured reward/risk ratio. When sizing inputs are valid, blocked intents can still include this envelope for review; it is not an order and does not bypass Sentinel or Pulse guardrails.
- `confirmation_sources`: source-quality plan showing delayed context sources, live confirmation sources, missing adapters, role-level coverage, and recommended next integrations. The available and missing confirmation weights are normalized to a `0.00` to `1.00` operator-readiness scale. `min_source_confirmation_weight` can block an intent until enough source coverage is configured.
- `confirmation_sources.coverage`: `met`, `partial`, or `missing` coverage for dark pool context, price/NBBO confirmation, liquidity/depth confirmation, options confirmation, halt/LULD blockers, and material-news context. Required coverage must be met before the plan is considered complete.
- `preferences.require_complete_source_coverage`: enabled by default. When true, missing required price, liquidity, halt/LULD, or material-news coverage blocks the intent before Sentinel can approve Pulse packet preparation.
- `preferences.source_coverage_override_reason`: required when required source coverage is incomplete and `require_source_coverage_complete=false`. The reason is carried into prepared Pulse packets for manual audit and does not bypass Sentinel confirmation or manual execution requirements.
- Source-coverage blockers name the missing required families, so Pulse status reasons show whether the operator needs price/NBBO, liquidity/depth, halt/LULD, material-news coverage, or multiple source families before review can proceed.
- `sentinel`: local Darkpool precheck metadata. It is not a substitute for an external Sentinel Edge decision.
- `sentinel.checks`: named pass/fail checklist entries for intent readiness, price confirmation, liquidity confirmation, news check, and spread guard.
- `pulse_packet`: withheld during local review. It can be prepared only through the authenticated Edge decision callback when `reviewer=sentinel-edge`, the decision is approved, and the callback secret matches. Approved packets use `destination=sentinel-edge`, `final_target=sentinel-pulse`, and `edge_review_required=true`, and include the risk plan, raw confidence, source-adjusted confidence, confidence breakdown, quality flags, optional source-coverage override reason, and Sentinel checklist for manual execution review.
- `pulse_status`: explicit Pulse readiness metadata with `prepared`, `withheld`, `not_requested`, or `unavailable` status, a user-readable message, blocker reasons, Sentinel status, and `requires_manual_execution=true`.

Pulse packets are not orders and are not direct Darkpool-to-Pulse handoffs. They carry `requires_manual_execution=true`, route through Sentinel Edge for final review, and are intended for confirmation workflow wiring, not autonomous live trading. If any Sentinel confirmation check is missing or the spread is too wide, the packet is withheld and `pulse_status.reasons` explains why.

Sentinel Edge can post an approved decision back to `POST /darkpool/trade-intent/edge-decision` with the original `intent`, `sentinel_decision`, optional `expires_at`, and optional `edge_signature`. Darkpool rejects missing, weak, placeholder, or wrong secrets; expired decisions; local precheck decisions; and non-approved decisions before it returns any packet metadata. Accepted callbacks are recorded on the local event bus with `execution=none`.

The dashboard Pulse summary shows raw confidence alongside source-adjusted confidence when a packet is available, so manual reviewers can see how much confirmation-source coverage discounted the original confluence score before considering any Pulse handoff. If a packet was prepared while required source coverage remains incomplete, the summary names the missing source families and the operator override reason so override-based review cannot look fully confirmed.

## Testing

Run backend tests:

```bash
pytest tests -q
```

Run frontend tests:

```bash
npm test
```

Run production build:

```bash
npm run build
```

Runtime smoke coverage includes a subprocess check for the documented `python server.py` startup path, including routes declared late in the module.

Run dependency audit:

```bash
npm audit --audit-level=moderate
```

Frontend build note: the production build uses lazy workspace views plus manual vendor chunks for chart, icon, and React dependencies, so Vite should not emit the old large-chunk warning during normal builds.

## Data and Interpretation Limits

FINRA public OTC/ATS data is delayed and aggregate. It is not an omniscient real-time view of dark-pool intent.

Real-time options flow, GEX, VEX, and live off-lit trade feeds require licensed providers. The app is prepared for those integrations, but demo mode uses deterministic synthetic data.

The source confirmation plan treats FINRA OTC transparency as context only when the active workflow is using FINRA or another configured provider explicitly supplies it. Demo workflows do not claim FINRA context availability. Higher-confidence trade confirmation requires separate coverage for real-time price/NBBO, liquidity/depth, halt/LULD, and material-news sources before the source plan is complete; options-flow confirmation is shown as a strong optional signal. By default, `require_source_coverage_complete=true` blocks Pulse preparation until required coverage is complete, even if manual Sentinel checkboxes are ticked. Operators can set it to `false` for demo review or raise `min_source_confirmation_weight` from `0` to enforce configured source coverage as an additional hard gate. The dashboard also shows source-adjusted confidence and role-level coverage so an unconfirmed but high-confluence setup is visibly weaker than one backed by live confirmation sources.

Required source coverage can be satisfied by configuring `POLYGON_API_KEY` for price/NBBO and depth coverage, `NASDAQ_HALTS_RSS_ENABLED=true` for Nasdaq Trade Halt RSS halt/LULD coverage, and `SEC_EDGAR_USER_AGENT` for SEC EDGAR material-news context. When those adapters are missing, the confirmation plan recommends the specific required source family that needs to be added. If an operator disables the source-coverage gate while required coverage is still missing, an override reason is required before the intent can reach Sentinel approval or Pulse packet preparation. These settings only mark confirmation adapters as configured; Sentinel confirmation and manual execution review are still required before any Pulse packet is prepared.

Dark pool prints can identify areas where institutional volume occurred. They do not prove intent. The level engine ranks areas of interest; it does not issue trade entries.

Confluence scores are stronger when a dark pool level aligns with exposure nodes, options-flow direction, and proximity to spot. Even high scores require price-action confirmation, liquidity checks, spread checks, news awareness, and risk controls.

## Research Inputs

Feature direction was informed by public documentation and product behavior from:

- Skylit Heatseeker docs: https://docs.skylit.ai/
- Skylit Heatseeker node workflow: https://www.skylit.ai/learn/reading-heatseeker
- Unusual Whales API docs: https://api.unusualwhales.com/docs
- Unusual Whales Discord bot: https://unusualwhales.com/discord-bot
- FINRA Weekly Summary API: https://developer.finra.org/docs/api-explorer/query_api-equity-weekly_summary
- FINRA OTC transparency overview: https://www.finra.org/filing-reporting/otc-transparency
- FINRA Reg SHO Daily Short Sale Volume: https://developer.finra.org/docs/api-explorer/query_api-equity-reg_sho_daily_short_sale_volume
- NYSE Daily TAQ / CTA-UTP trade and quote history: https://www.nyse.com/market-data/historical/daily-taq
- Nasdaq TotalView-ITCH: https://data.nasdaq.com/databases/NTV
- Cboe LiveVol API: https://api.livevol.com/
- Nasdaq current trading halts: https://www.nasdaqtrader.com/trader.aspx?id=tradehalts
- SEC EDGAR search: https://www.sec.gov/edgar/search/
- SEC Form ATS-N information: https://www.sec.gov/about/divisions-offices/division-trading-markets/alternative-trading-systems/form-ats-n-filings-information
- FlowAlgo: https://flowalgo.com/
- Cheddar Flow Discord bot: https://www.cheddarflow.com/cheddar-flow-discord-bot/
- Tradytics Discord bots: https://tradytics.com/discord
- InsiderFinance Discord bot docs: https://www.insiderfinance.io/docs/discord-bot
- BlackBoxStocks features: https://blackboxstocks.com/features/
- Crypto bot risk-management patterns: https://3commas.io/blog/ai-trading-bot-risk-management-guide
- Crypto bot setting discipline for ranges, stop loss, and take profit: https://bitsgap.com/blog/how-to-choose-crypto-trading-bot-settings-in-2026-range-investment-stop-loss-and-take-profit
- Trend-following and mean-reversion in Bitcoin research: https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/
- AI trading bot architecture and backtesting guidance: https://www.alchemy.com/blog/how-to-build-an-ai-trading-bot

## Next Production Steps

- Add a paid provider adapter for Unusual Whales or another licensed source for live dark pool prints, options flow, GEX, VEX, halts, and news.
- Add crypto exchange provider adapters for market data, paper trading, and strict read-only/live-trading separation.
- Expand strategy backtesting with Sharpe-like risk-adjusted return, walk-forward tests, slippage modeling, and out-of-sample validation.
- Persist normalized events and aggregates in PostgreSQL or TimescaleDB.
- Add Docker Compose for backend, frontend, and database.
- Add websocket push from backend alert candidates to the dashboard.
- Add code splitting for the React advanced views.
- Move in-memory auth, watchlists, thresholds, and alert state into persistent storage.
