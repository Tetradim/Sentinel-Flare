# Production Sentinel Flare Design

## Goal

Make Sentinel Flare a functional production-oriented alerting bot and dashboard for dark pool context, options-flow-style confluence, and Discord/webhook alerts. The bot must not auto-trade. It should produce explainable levels, scores, and alerts that a trader can review.

## Scope

The implementation stabilizes the existing FastAPI backend, Discord bot, and React dashboard. It selectively recovers working dashboard behavior from `origin/codex/improve-darkpool-bot-code`, keeps newer `main` endpoints where useful, and replaces broken placeholder imports with testable Python services.

## Architecture

Backend code is split into focused services under `darkpool/`:

- `models.py`: typed records for prints, levels, exposure nodes, confluence, alerts, and watchlists.
- `fixtures.py`: deterministic MAG7 sample data for local demos and tests.
- `level_engine.py`: clusters prints into price levels with volume, notional, freshness, and strength scoring.
- `confluence.py`: ranks dark pool levels against dealer exposure nodes and options-flow context.
- `providers.py`: FINRA provider wrapper plus a sample provider for offline demo mode.
- `alerting.py`: alert generation and deduplication.

The FastAPI app keeps existing endpoints but routes production-oriented endpoints through those services. FINRA weekly data remains supported, but local demo/sample data is available when provider data is missing or API calls fail. Discord uses the same services so slash commands and backend endpoints agree.

## Research-Informed Behavior

Dark pool prints are treated as contextual levels, not direct buy/sell signals. Stronger alerts require clustered prints, recency, notional size, and optional confluence with exposure nodes or options-flow direction. The Heatseeker-style logic identifies king nodes, floors, ceilings, gatekeepers, and air pockets from exposure maps, but uses explicit warnings and confidence scores instead of trade-entry instructions.

## Error Handling

External provider errors become structured `ProviderError` responses where possible. Demo endpoints remain usable offline. Alerts include reasons and component scores so users can understand why a signal fired.

## Testing

Tests cover dependency importability, app route smoke checks, FINRA helper imports, provider fallback behavior, dark pool clustering, confluence scoring, alert deduplication, Discord command payload handling, and frontend build/pure logic. Existing broken behavior is captured by failing tests first, then fixed.

## Documentation

README is updated with setup, environment variables, route list, bot commands, testing commands, data limitations, and production deployment notes. It avoids reference tags and presents external sources as plain links.
