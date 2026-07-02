# Darkpool Robust Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Sentinel Flare into a production-ready intelligence workflow where provider-backed market context, trade intent scoring, Sentinel Edge review, and Pulse packet preparation are isolated, testable, and safe from accidental live order behavior.

**Architecture:** Move the current route-heavy `server.py` toward thin route modules over deeper domain modules. The main leverage comes from a provider-backed `MarketContext` module, a single trade-intent orchestration module, explicit Sentinel/Pulse adapters, and runtime smoke tests that exercise the documented `python server.py` startup path.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, pytest-asyncio, React 18, Vite, Vitest.

---

## Current State Summary

The branch is already meaningfully stronger than `main`:

- `f127de5 fix: register all server routes before startup` moved the `python server.py` entrypoint after all route decorators and fixed `AlertRouter.route()` timestamp recording.
- `cfaba1b feat: add source-adjusted trade confidence` separates raw confluence confidence from source-confirmed confidence and carries both into Pulse packets.
- Sentinel Edge approval is required before Pulse packet preparation, and Pulse packets still carry `requires_manual_execution=true`.
- The Intent UI exposes user-customizable thresholds, signal-quality gates, source confirmation requirements, Sentinel checks, risk plan, and Pulse packet state.

Remaining robust-fix targets:

- `darkpool.command_service` still labels summaries as `provider=finra` or any arbitrary provider while using demo fixtures internally.
- `server.py` is still too large and owns unrelated authentication, route, websocket, alert, Discord, watchlist, and trade-intent behavior in one module.
- Trade intent orchestration is spread across `server.py`, `darkpool.trade_intent`, `darkpool.source_catalog`, provider functions, and frontend formatting.
- Discord interaction HTTP endpoint lacks request signature verification if deployed publicly.
- Source confirmation status is static and can overstate context availability in demo workflows.
- Tests mostly import `server.app`; only one new source-order test guards documented startup, but there is no subprocess-based runtime route smoke.

---

## Target File Structure

### Backend Domain Modules

- Create `darkpool/market_context.py`
  - Owns provider-backed market context construction.
  - Calls `fetch_provider_result()` instead of using demo fixtures directly.
  - Produces levels, exposure nodes, options flow, confluence scores, alerts, and source confirmation plan in one typed object.

- Create `darkpool/trade_pipeline.py`
  - Owns end-to-end trade-intent report assembly.
  - Accepts symbol, provider, user preferences, Sentinel confirmation, and Pulse inclusion request.
  - Returns a typed `TradeIntentReport` used by routes and future UI/backtesting flows.

- Create `darkpool/sentinel_gateway.py`
  - Owns Sentinel adapter selection.
  - Keeps `LocalSentinelEdgeAdapter` for offline development.
  - Adds an HTTP adapter only for confirmation decisions, never live execution.

- Create `darkpool/pulse_gateway.py`
  - Owns Pulse packet serialization and future outbound adapter.
  - Enforces `requires_manual_execution=true` and rejects all direct order verbs.

- Create `darkpool/discord_security.py`
  - Verifies Discord interaction signatures using the public key when `DISCORD_PUBLIC_KEY` is configured.
  - Allows unsigned payloads only in explicit local/test mode.

### Backend Route Modules

- Create `routes/__init__.py`
- Create `routes/darkpool_routes.py`
- Create `routes/discord_routes.py`
- Create `routes/alert_routes.py`
- Create `routes/watchlist_routes.py`
- Create `routes/health_routes.py`

Each route module exports an `APIRouter`. `server.py` becomes an app factory plus route registration.

### Frontend Modules

- Create `src/tradeIntentControls.js`
  - Owns default settings, URL serialization, and input coercion.

- Create `src/TradeIntentView.jsx`
  - Moves the current Intent UI out of `ProductionViews.jsx`.
  - Keeps `ProductionViews.jsx` as navigation/composition only.

- Create `src/TradeIntentSummary.jsx`
  - Pure display component for score, source adjustment, blockers, quality flags, Sentinel checks, and Pulse packet state.

### Tests

- Add `tests/test_market_context.py`
- Add `tests/test_trade_pipeline.py`
- Add `tests/test_runtime_startup.py`
- Add `tests/test_discord_security.py`
- Extend `tests/test_provider_and_discord.py`
- Extend `src/tradeIntent.test.js`

---

## Phase 1: Provider-Backed Market Context

### Task 1: Add Provider-Backed Market Context

**Files:**
- Create: `darkpool/market_context.py`
- Test: `tests/test_market_context.py`

- [x] **Step 1: Write the failing tests**

```python
import pytest

from darkpool.market_context import build_market_context


@pytest.mark.asyncio
async def test_market_context_uses_requested_provider_for_levels_and_scores():
    context = await build_market_context("AAPL", provider="demo", limit=50)

    assert context.symbol == "AAPL"
    assert context.provider_result.provider == "demo"
    assert context.prints
    assert context.levels
    assert context.scores
    assert context.confirmation_plan.available_confirmation_weight == 0.0


@pytest.mark.asyncio
async def test_market_context_rejects_unknown_provider():
    with pytest.raises(Exception, match="Unsupported provider"):
        await build_market_context("AAPL", provider="missing-provider", limit=20)
```

- [x] **Step 2: Run the tests to verify RED**

Run:

```bash
pytest tests/test_market_context.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'darkpool.market_context'
```

- [x] **Step 3: Implement `darkpool/market_context.py`**

```python
"""Provider-backed market context assembly for dark pool workflows."""

from __future__ import annotations

from dataclasses import dataclass

from .alerting import build_alert_candidates
from .confluence import score_confluence
from .fixtures import get_stock, sample_exposure_nodes, sample_options_flow
from .level_engine import cluster_darkpool_levels
from .models import AlertCandidate, ConfluenceScore, DarkpoolLevel, DarkpoolPrint, ExposureNode, OptionsFlowSignal
from .providers import ProviderResult, fetch_provider_result
from .source_catalog import TradeConfirmationPlan, build_trade_confirmation_plan


@dataclass(frozen=True)
class MarketContext:
    symbol: str
    provider_result: ProviderResult
    spot_price: float
    prints: list[DarkpoolPrint]
    levels: list[DarkpoolLevel]
    exposure_nodes: list[ExposureNode]
    options_flow: list[OptionsFlowSignal]
    scores: list[ConfluenceScore]
    alerts: list[AlertCandidate]
    confirmation_plan: TradeConfirmationPlan


async def build_market_context(
    symbol: str,
    provider: str = "demo",
    limit: int = 500,
    price_bucket: float = 0.10,
    configured_providers: list[str] | None = None,
) -> MarketContext:
    sym = symbol.upper()
    provider_result = await fetch_provider_result(sym, provider=provider, limit=limit)
    stock = get_stock(sym)
    spot = float(stock.get("basePrice", 100.0))
    levels = cluster_darkpool_levels(provider_result.prints, price_bucket=price_bucket)
    exposure_nodes = sample_exposure_nodes(sym, spot)
    options_flow = sample_options_flow(sym)
    scores = score_confluence(sym, spot, levels, exposure_nodes, options_flow)
    alerts = build_alert_candidates(sym, levels, scores)
    confirmation_plan = build_trade_confirmation_plan(
        active_provider=provider_result.provider,
        configured_providers=configured_providers or [provider_result.provider],
    )
    return MarketContext(
        symbol=sym,
        provider_result=provider_result,
        spot_price=spot,
        prints=provider_result.prints,
        levels=levels,
        exposure_nodes=exposure_nodes,
        options_flow=options_flow,
        scores=scores,
        alerts=alerts,
        confirmation_plan=confirmation_plan,
    )
```

- [x] **Step 4: Run the tests to verify GREEN**

Run:

```bash
pytest tests/test_market_context.py -q
```

Expected:

```text
2 passed
```

- [x] **Step 5: Commit**

```bash
git add darkpool/market_context.py tests/test_market_context.py
git commit -m "feat: add provider-backed market context"
```

### Task 2: Make Discord Command Summaries Use Market Context

**Files:**
- Modify: `darkpool/command_service.py`
- Modify: `server.py`
- Modify: `discord_bot.py`
- Test: `tests/test_provider_and_discord.py`
- Test: `tests/test_discord_features.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/test_provider_and_discord.py`:

```python
@pytest.mark.asyncio
async def test_command_summary_rejects_unknown_provider_instead_of_labeling_demo_data():
    from darkpool.command_service import build_levels_summary

    with pytest.raises(ProviderError):
        await build_levels_summary("AAPL", provider="missing-provider")
```

Update the Discord command TestClient test to async-aware route behavior:

```python
def test_discord_interaction_levels_command_returns_embed():
    client = TestClient(server.app)
    response = client.post(
        "/discord/commands",
        json={
            "id": "2",
            "type": 2,
            "data": {
                "name": "levels",
                "options": [
                    {"name": "symbol", "value": "AAPL"},
                    {"name": "provider", "value": "demo"},
                ],
            },
            "member": None,
            "guild_id": "guild-1",
            "channel_id": "channel-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == 4
    assert body["data"]["embeds"][0]["title"].startswith("AAPL")
```

- [x] **Step 2: Run the tests to verify RED**

Run:

```bash
pytest tests/test_provider_and_discord.py::test_command_summary_rejects_unknown_provider_instead_of_labeling_demo_data -q
```

Expected:

```text
Failed: DID NOT RAISE <class 'darkpool.providers.ProviderError'>
```

- [x] **Step 3: Refactor command service to async provider-backed builders**

Replace `_data_context()` and builders with async versions:

```python
from .market_context import MarketContext, build_market_context


async def _data_context(symbol: str, provider: str = "demo") -> MarketContext:
    return await build_market_context(symbol, provider=provider, limit=220)


async def build_levels_summary(symbol: str, provider: str = "demo", limit: int = 5) -> CommandSummary:
    context = await _data_context(symbol, provider)
    items = [
        f"${level.price:.2f} | score {level.strength_score:.1f} | {level.total_size:,} sh | {level.print_count} print(s)"
        for level in context.levels[:limit]
    ]
    return CommandSummary(
        command="levels",
        symbol=context.symbol,
        title=f"{context.symbol} Dark Pool Levels",
        description="Clustered dark pool context levels. Not trade entries.",
        metrics={
            "provider": context.provider_result.provider,
            "spot": f"{context.spot_price:.2f}",
            "prints": f"{len(context.prints)}",
            "degraded": str(context.provider_result.degraded).lower(),
        },
        sections=[SummarySection("Top Levels", items or ["No levels found"])],
    )
```

Apply the same async pattern to:

```python
async def build_confluence_summary(...)
async def build_alerts_summary(...)
async def build_darkpool_summary(...)
async def build_watchlist_summary(...)
```

- [x] **Step 4: Update route and bot call sites**

In `server.py`:

```python
summary = await builders[cmd_name](symbol, provider=provider)
```

In `discord_bot.py`:

```python
await _send_summary(interaction, await build_darkpool_summary(symbol, provider=provider))
await _send_summary(interaction, await build_levels_summary(symbol, provider=provider))
await _send_summary(interaction, await build_confluence_summary(symbol, provider=provider))
await _send_summary(interaction, await build_alerts_summary(symbol, provider=provider))
await _send_summary(interaction, await build_watchlist_summary(parsed, provider=provider))
```

- [x] **Step 5: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_provider_and_discord.py tests/test_discord_features.py -q
```

Expected:

```text
all selected tests passed
```

- [x] **Step 6: Commit**

```bash
git add darkpool/command_service.py server.py discord_bot.py tests/test_provider_and_discord.py tests/test_discord_features.py
git commit -m "fix: use requested provider in command summaries"
```

---

## Phase 2: Trade Intent Pipeline and Stronger Sentinel/Pulse Boundaries

### Task 3: Add a Trade Intent Report Orchestrator

**Files:**
- Create: `darkpool/trade_pipeline.py`
- Modify: `server.py`
- Test: `tests/test_trade_pipeline.py`

- [x] **Step 1: Write the failing tests**

```python
import pytest

from darkpool.trade_intent import SentinelConfirmation, TradingPreferences
from darkpool.trade_pipeline import build_trade_intent_report


@pytest.mark.asyncio
async def test_trade_pipeline_prepares_pulse_only_after_sentinel_approval():
    report = await build_trade_intent_report(
        symbol="AAPL",
        provider="demo",
        preferences=TradingPreferences(min_score=60, max_distance_pct=2, min_notional=1_000_000),
        confirmation=SentinelConfirmation(
            price_confirmed=True,
            liquidity_confirmed=True,
            news_checked=True,
            observed_spread_bps=5,
            max_spread_bps=25,
        ),
        include_pulse_packet=True,
    )

    assert report.intent is not None
    assert report.sentinel is not None
    assert report.sentinel.status == "approved"
    assert report.pulse_packet is not None
    assert report.pulse_packet["requires_manual_execution"] is True


@pytest.mark.asyncio
async def test_trade_pipeline_withholds_pulse_when_sentinel_rejects():
    report = await build_trade_intent_report(
        symbol="AAPL",
        provider="demo",
        preferences=TradingPreferences(min_score=60, max_distance_pct=2, min_notional=1_000_000),
        confirmation=SentinelConfirmation(price_confirmed=False, liquidity_confirmed=True, news_checked=True),
        include_pulse_packet=True,
    )

    assert report.sentinel.status == "rejected"
    assert report.pulse_packet is None
```

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_trade_pipeline.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'darkpool.trade_pipeline'
```

- [x] **Step 3: Implement `darkpool/trade_pipeline.py`**

```python
"""End-to-end trade-intent report assembly."""

from __future__ import annotations

from dataclasses import dataclass

from .market_context import MarketContext, build_market_context
from .trade_intent import (
    LocalSentinelEdgeAdapter,
    SentinelConfirmation,
    SentinelDecision,
    TradeIntent,
    TradingPreferences,
    build_trade_intent,
    prepare_pulse_packet,
)


@dataclass(frozen=True)
class TradeIntentReport:
    context: MarketContext
    preferences: TradingPreferences
    intent: TradeIntent | None
    sentinel: SentinelDecision | None
    pulse_packet: dict | None


async def build_trade_intent_report(
    symbol: str,
    provider: str,
    preferences: TradingPreferences,
    confirmation: SentinelConfirmation,
    include_pulse_packet: bool = False,
    price_bucket: float = 0.10,
) -> TradeIntentReport:
    context = await build_market_context(symbol, provider=provider, price_bucket=price_bucket)
    if not context.scores:
        return TradeIntentReport(
            context=context,
            preferences=preferences,
            intent=None,
            sentinel=None,
            pulse_packet=None,
        )

    intent = build_trade_intent(
        context.scores[0],
        preferences,
        source_confirmation_weight=context.confirmation_plan.available_confirmation_weight,
    )
    sentinel = LocalSentinelEdgeAdapter().review(intent, confirmation)
    pulse_packet = None
    if include_pulse_packet and sentinel.status == "approved":
        pulse_packet = prepare_pulse_packet(intent, sentinel)

    return TradeIntentReport(
        context=context,
        preferences=preferences,
        intent=intent,
        sentinel=sentinel,
        pulse_packet=pulse_packet,
    )
```

- [x] **Step 4: Replace route orchestration in `server.py`**

The `/darkpool/trade-intent` route should:

```python
report = await build_trade_intent_report(
    symbol=symbol,
    provider=provider,
    preferences=preferences,
    confirmation=confirmation,
    include_pulse_packet=include_pulse_packet,
    price_bucket=price_bucket,
)
```

Return:

```python
return {
    "symbol": report.context.symbol,
    "provider": report.context.provider_result.provider,
    "degraded": report.context.provider_result.degraded,
    "message": report.context.provider_result.message,
    "preferences": report.preferences.model_dump(mode="json"),
    "confirmation_sources": report.context.confirmation_plan.model_dump(mode="json"),
    "intent": report.intent.model_dump(mode="json") if report.intent else None,
    "sentinel": report.sentinel.model_dump(mode="json") if report.sentinel else None,
    "pulse_packet": report.pulse_packet,
    "source_score": report.context.scores[0].model_dump(mode="json") if report.context.scores else None,
    "fetched_at": datetime.utcnow().isoformat(),
}
```

- [x] **Step 5: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_trade_pipeline.py tests/test_trade_intent.py -q
```

Expected:

```text
all selected tests passed
```

- [x] **Step 6: Commit**

```bash
git add darkpool/trade_pipeline.py server.py tests/test_trade_pipeline.py
git commit -m "refactor: add trade intent pipeline"
```

### Task 4: Add Pulse Gateway Guardrails

**Files:**
- Create: `darkpool/pulse_gateway.py`
- Modify: `darkpool/trade_pipeline.py`
- Test: `tests/test_pulse_gateway.py`

- [x] **Step 1: Write the failing tests**

```python
import pytest

from darkpool.pulse_gateway import PulseGateway, PulsePacketRejected


def test_pulse_gateway_rejects_order_like_packet():
    gateway = PulseGateway()

    with pytest.raises(PulsePacketRejected, match="manual execution"):
        gateway.validate_packet({"packet_type": "order", "requires_manual_execution": False})


def test_pulse_gateway_accepts_manual_trade_intent_packet():
    gateway = PulseGateway()
    packet = gateway.validate_packet({"packet_type": "trade_intent", "requires_manual_execution": True})

    assert packet["packet_type"] == "trade_intent"
    assert packet["requires_manual_execution"] is True
```

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_pulse_gateway.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'darkpool.pulse_gateway'
```

- [x] **Step 3: Implement `darkpool/pulse_gateway.py`**

```python
"""Pulse packet validation and adapter boundary."""

from __future__ import annotations


class PulsePacketRejected(ValueError):
    pass


class PulseGateway:
    allowed_packet_types = {"trade_intent"}

    def validate_packet(self, packet: dict) -> dict:
        if packet.get("packet_type") not in self.allowed_packet_types:
            raise PulsePacketRejected("Pulse packet must be a manual execution trade_intent packet")
        if packet.get("requires_manual_execution") is not True:
            raise PulsePacketRejected("Pulse packet must require manual execution")
        forbidden_keys = {"order_id", "broker_order_id", "live_order", "place_order", "execute_order"}
        present = forbidden_keys.intersection(packet)
        if present:
            raise PulsePacketRejected(f"Pulse packet contains forbidden live-order keys: {sorted(present)}")
        return packet
```

- [x] **Step 4: Use gateway in `trade_pipeline.py`**

```python
from .pulse_gateway import PulseGateway

...

if include_pulse_packet and sentinel.status == "approved":
    pulse_packet = PulseGateway().validate_packet(prepare_pulse_packet(intent, sentinel))
```

- [x] **Step 5: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_pulse_gateway.py tests/test_trade_pipeline.py tests/test_trade_intent.py -q
```

Expected:

```text
all selected tests passed
```

- [x] **Step 6: Commit**

```bash
git add darkpool/pulse_gateway.py darkpool/trade_pipeline.py tests/test_pulse_gateway.py
git commit -m "feat: add pulse packet guardrails"
```

---

## Phase 3: Server Composition Refactor

### Task 5: Add App Factory Without Moving Routes Yet

**Files:**
- Modify: `server.py`
- Test: `tests/test_runtime_startup.py`

- [x] **Step 1: Write the failing test**

```python
import server


def test_create_app_returns_registered_fastapi_app():
    app = server.create_app()
    paths = {route.path for route in app.routes}

    assert "/health" in paths
    assert "/darkpool/trade-intent" in paths
    assert "/alerts/route" in paths
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_runtime_startup.py::test_create_app_returns_registered_fastapi_app -q
```

Expected:

```text
AttributeError: module 'server' has no attribute 'create_app'
```

- [x] **Step 3: Add factory wrapper**

Because the current module already creates `app`, start with a compatibility-preserving factory:

```python
def create_app() -> FastAPI:
    return app
```

The final entrypoint remains:

```python
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
```

- [x] **Step 4: Run test to verify GREEN**

Run:

```bash
pytest tests/test_runtime_startup.py::test_create_app_returns_registered_fastapi_app -q
```

Expected:

```text
1 passed
```

- [x] **Step 5: Commit**

```bash
git add server.py tests/test_runtime_startup.py
git commit -m "refactor: add FastAPI app factory"
```

### Task 6: Move Darkpool Routes Into Router

**Files:**
- Create: `routes/__init__.py`
- Create: `routes/darkpool_routes.py`
- Modify: `server.py`
- Test: `tests/test_app_smoke.py`

- [x] **Step 1: Write route registration test**

```python
def test_darkpool_router_routes_remain_registered():
    client = TestClient(server.app)

    response = client.get("/darkpool/trade-intent?symbol=AAPL&provider=demo")

    assert response.status_code == 200, response.text
    assert response.json()["symbol"] == "AAPL"
```

- [x] **Step 2: Run test before moving code**

Run:

```bash
pytest tests/test_app_smoke.py::test_darkpool_router_routes_remain_registered -q
```

Expected:

```text
1 passed
```

This is a characterization test. It protects the refactor.

- [x] **Step 3: Create `routes/darkpool_routes.py`**

Move these route handlers out of `server.py`:

```python
@router.get("/darkpool/otc")
@router.get("/darkpool/trades")
@router.get("/darkpool/levels")
@router.get("/darkpool/confluence")
@router.get("/darkpool/alert-candidates")
@router.get("/darkpool/information-sources")
@router.get("/darkpool/trade-intent")
```

Use:

```python
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
```

Imports should come from domain modules, not from `server.py`.

- [x] **Step 4: Register router in `server.py`**

```python
from routes.darkpool_routes import router as darkpool_router

app.include_router(darkpool_router)
```

- [x] **Step 5: Run selected tests**

Run:

```bash
pytest tests/test_app_smoke.py tests/test_trade_intent.py tests/test_source_catalog.py -q
```

Expected:

```text
all selected tests passed
```

- [x] **Step 6: Commit**

```bash
git add server.py routes/__init__.py routes/darkpool_routes.py tests/test_app_smoke.py
git commit -m "refactor: move darkpool routes into router"
```

---

## Phase 4: Source Confirmation Accuracy

### Task 7: Stop Marking FINRA Context Available In Demo Workflows

**Files:**
- Modify: `server.py`
- Modify: `darkpool/source_catalog.py`
- Test: `tests/test_source_catalog.py`
- Test: `tests/test_trade_intent.py`

- [x] **Step 1: Write failing tests**

```python
def test_demo_confirmation_plan_does_not_claim_finra_context_available():
    plan = build_trade_confirmation_plan(active_provider="demo", configured_providers=["demo"])
    by_id = {source.id: source for source in plan.sources}

    assert by_id["finra_otc_transparency"].status == "missing"
    assert "no darkpool context source available" in plan.summary


def test_finra_confirmation_plan_marks_finra_context_available():
    plan = build_trade_confirmation_plan(active_provider="finra", configured_providers=["finra"])
    by_id = {source.id: source for source in plan.sources}

    assert by_id["finra_otc_transparency"].status == "available"
    assert "context source available" in plan.summary
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_source_catalog.py -q
```

Expected:

```text
AssertionError: expected missing but got available
```

- [x] **Step 3: Remove unconditional FINRA configured provider injection**

In route code or `MarketContext`, build configured providers from actual runtime state:

```python
configured_providers = [provider_result.provider]
for name, provider_obj in PROVIDERS.items():
    if bool(getattr(provider_obj, "api_key", False)):
        configured_providers.append(name)
```

Do not add `"finra"` unless the active provider is FINRA or a real FINRA adapter is explicitly configured.

- [x] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_source_catalog.py tests/test_trade_intent.py -q
```

Expected:

```text
all selected tests passed
```

- [x] **Step 5: Commit**

```bash
git add server.py darkpool/source_catalog.py tests/test_source_catalog.py tests/test_trade_intent.py
git commit -m "fix: report source confirmation availability accurately"
```

---

## Phase 5: Discord HTTP Security

### Task 8: Verify Discord Interaction Signatures

**Files:**
- Create: `darkpool/discord_security.py`
- Modify: `.env.example`
- Modify: `server.py` or `routes/discord_routes.py`
- Test: `tests/test_discord_security.py`

- [x] **Step 1: Write failing tests**

```python
import pytest

from darkpool.discord_security import DiscordSignatureVerifier, SignatureVerificationError


def test_discord_signature_verifier_allows_unsigned_local_mode():
    verifier = DiscordSignatureVerifier(public_key="", allow_unsigned=True)

    assert verifier.verify(timestamp="1", body=b"{}", signature="") is True


def test_discord_signature_verifier_rejects_unsigned_payload_when_required():
    verifier = DiscordSignatureVerifier(public_key="", allow_unsigned=False)

    with pytest.raises(SignatureVerificationError):
        verifier.verify(timestamp="1", body=b"{}", signature="")
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_discord_security.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'darkpool.discord_security'
```

- [x] **Step 3: Implement verifier**

```python
"""Discord interaction signature verification."""

from __future__ import annotations


class SignatureVerificationError(ValueError):
    pass


class DiscordSignatureVerifier:
    def __init__(self, public_key: str, allow_unsigned: bool = False):
        self.public_key = public_key.strip()
        self.allow_unsigned = allow_unsigned

    def verify(self, timestamp: str, body: bytes, signature: str) -> bool:
        if self.allow_unsigned:
            return True
        if not self.public_key or not timestamp or not signature:
            raise SignatureVerificationError("Discord signature verification is required")

        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError

        try:
            verify_key = VerifyKey(bytes.fromhex(self.public_key))
            verify_key.verify(timestamp.encode("utf-8") + body, bytes.fromhex(signature))
        except (ValueError, BadSignatureError) as exc:
            raise SignatureVerificationError("Invalid Discord interaction signature") from exc
        return True
```

- [x] **Step 4: Add dependency and env docs**

In `requirements.txt`:

```text
PyNaCl>=1.5.0
```

In `.env.example`:

```text
DISCORD_PUBLIC_KEY=your_discord_application_public_key_here
ALLOW_UNSIGNED_DISCORD_INTERACTIONS=false
```

- [x] **Step 5: Wire route verification**

For the route implementation, accept `Request` and read raw body before parsing:

```python
from fastapi import Request


@router.post("/discord/commands")
async def handle_slash_command(request: Request):
    body = await request.body()
    verifier = DiscordSignatureVerifier(
        public_key=os.getenv("DISCORD_PUBLIC_KEY", ""),
        allow_unsigned=os.getenv("ALLOW_UNSIGNED_DISCORD_INTERACTIONS", "false").lower() == "true",
    )
    try:
        verifier.verify(
            timestamp=request.headers.get("X-Signature-Timestamp", ""),
            body=body,
            signature=request.headers.get("X-Signature-Ed25519", ""),
        )
    except SignatureVerificationError:
        raise HTTPException(401, "Invalid Discord signature")
    command = SlashCommand.model_validate_json(body)
```

Tests that post unsigned commands should set:

```python
monkeypatch.setenv("ALLOW_UNSIGNED_DISCORD_INTERACTIONS", "true")
```

- [x] **Step 6: Run tests**

Run:

```bash
pytest tests/test_discord_security.py tests/test_provider_and_discord.py -q
```

Expected:

```text
all selected tests passed
```

- [x] **Step 7: Commit**

```bash
git add darkpool/discord_security.py server.py requirements.txt .env.example tests/test_discord_security.py tests/test_provider_and_discord.py
git commit -m "feat: verify discord interaction signatures"
```

---

## Phase 6: Frontend Refactor for Readability

### Task 9: Extract Trade Intent View

**Files:**
- Create: `src/TradeIntentView.jsx`
- Create: `src/TradeIntentSummary.jsx`
- Create: `src/tradeIntentControls.js`
- Modify: `src/ProductionViews.jsx`
- Modify: `src/App.jsx`
- Test: `src/tradeIntent.test.js`
- Test: `tests/test_frontend_trade_intent_integration.py`

- [x] **Step 1: Add integration test expectation**

```python
def test_trade_intent_view_is_split_into_dedicated_modules():
    assert Path("src/TradeIntentView.jsx").exists()
    assert Path("src/TradeIntentSummary.jsx").exists()
    assert Path("src/tradeIntentControls.js").exists()
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_frontend_trade_intent_integration.py::test_trade_intent_view_is_split_into_dedicated_modules -q
```

Expected:

```text
AssertionError: expected src/TradeIntentView.jsx to exist
```

- [x] **Step 3: Move URL controls into `src/tradeIntentControls.js`**

Move from `src/tradeIntent.js`:

```javascript
export const DEFAULT_TRADE_INTENT_SETTINGS = { ... };
export const buildTradeIntentUrl = (settings = {}) => { ... };
```

Keep formatting helpers in `src/tradeIntent.js`.

- [x] **Step 4: Create `src/TradeIntentSummary.jsx`**

```jsx
import {
  formatConfirmationSummary,
  formatConfidenceBreakdown,
  formatIntentMoney,
  formatQualityFlags,
  formatRiskPlanSummary,
  formatSentinelChecks,
  formatSourceAdjustedConfidence,
  formatSourceConfirmationPlan,
  summarizePulsePacket,
} from './tradeIntent';

export const TradeIntentSummary = ({ intent, sentinel, pulsePacket, confirmationSources }) => {
  if (!intent) {
    return <div className="rounded-lg bg-dark-700 p-6 text-center text-gray-400">No trade intent loaded</div>;
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg bg-dark-700 p-4">
        <p className="text-sm text-gray-200 leading-6">{intent.readable_summary}</p>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <div className="bg-dark-700 rounded-lg p-3">
          <div className="text-xs text-gray-500 mb-1">Confidence</div>
          <div className="text-xl font-mono text-white">{Number(intent.confidence).toFixed(1)}</div>
        </div>
        <div className="bg-dark-700 rounded-lg p-3">
          <div className="text-xs text-gray-500 mb-1">Source-Adjusted</div>
          <div className="text-xl font-mono text-white">{Number(intent.source_adjusted_confidence).toFixed(1)}</div>
          <div className="text-xs text-gray-500 mt-1">{formatSourceAdjustedConfidence(intent)}</div>
        </div>
      </div>
      <div className="bg-dark-700 rounded-lg p-4">
        <div className="text-xs text-gray-500 mb-3">Sentinel Checklist</div>
        {formatSentinelChecks(sentinel?.checks).map((line) => (
          <div key={line} className="rounded bg-dark-800 px-3 py-2 text-sm text-gray-200">{line}</div>
        ))}
      </div>
      <div className="bg-dark-700 rounded-lg p-4">
        <div className="text-xs text-gray-500 mb-2">Pulse</div>
        <p className="text-sm text-gray-200">{summarizePulsePacket(pulsePacket)}</p>
      </div>
    </div>
  );
};
```

- [x] **Step 5: Create `src/TradeIntentView.jsx`**

Move the current `TradeIntentView` body from `ProductionViews.jsx` into the new file and import:

```jsx
import { DEFAULT_TRADE_INTENT_SETTINGS, buildTradeIntentUrl } from './tradeIntentControls';
import { getIntentTone } from './tradeIntent';
import { TradeIntentSummary } from './TradeIntentSummary';
```

- [x] **Step 6: Simplify `ProductionViews.jsx`**

Remove the inline `TradeIntentView` implementation and import:

```jsx
import { TradeIntentView } from './TradeIntentView';
```

- [x] **Step 7: Run tests**

Run:

```bash
npm test
pytest tests/test_frontend_trade_intent_integration.py -q
npm run build
```

Expected:

```text
Vitest passes, pytest frontend integration passes, Vite build exits 0
```

- [x] **Step 8: Commit**

```bash
git add src/TradeIntentView.jsx src/TradeIntentSummary.jsx src/tradeIntentControls.js src/ProductionViews.jsx src/App.jsx src/tradeIntent.js src/tradeIntent.test.js tests/test_frontend_trade_intent_integration.py
git commit -m "refactor: split trade intent frontend view"
```

---

## Phase 7: Runtime Verification and Docs

### Task 10: Add Subprocess Startup Smoke Test

**Files:**
- Create: `tests/test_runtime_startup.py`
- Modify: `README.md`

- [x] **Step 1: Write the failing runtime smoke test**

```python
import os
import socket
import subprocess
import sys
import time

import httpx


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_python_server_registers_late_routes_at_runtime():
    port = _free_port()
    env = os.environ.copy()
    env["PORT"] = str(port)
    process = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 15
        last_error = None
        while time.time() < deadline:
            try:
                response = httpx.post(
                    f"http://127.0.0.1:{port}/alerts/route",
                    params={"symbol": "AAPL", "alert_type": "runtime", "channel": "discord", "size": 1},
                    timeout=1,
                )
                if response.status_code == 200:
                    break
            except Exception as exc:
                last_error = exc
                time.sleep(0.25)
        else:
            raise AssertionError(f"server did not expose /alerts/route: {last_error}")

        assert response.json() == {"status": "sent"}
    finally:
        process.terminate()
        process.wait(timeout=10)
```

- [x] **Step 2: Run test**

Run:

```bash
pytest tests/test_runtime_startup.py -q
```

Expected:

```text
1 passed
```

The current branch should already pass after `f127de5`, but this test makes the runtime contract permanent.

- [x] **Step 3: Update README testing section**

Add:

```markdown
Runtime smoke coverage includes a subprocess check for the documented `python server.py` startup path, including routes declared late in the module.
```

- [x] **Step 4: Commit**

```bash
git add tests/test_runtime_startup.py README.md
git commit -m "test: add runtime startup smoke coverage"
```

### Task 11: Final Verification Sweep

**Files:**
- No production files unless verification exposes a bug.

- [x] **Step 1: Run backend tests**

```bash
pytest tests -q
```

Expected:

```text
all tests passed
```

- [x] **Step 2: Run frontend tests**

```bash
npm test
```

Expected:

```text
all tests passed
```

- [x] **Step 3: Build frontend**

```bash
npm run build
```

Expected:

```text
build exits 0
```

The frontend split is complete: workspace views load lazily and chart/icon/React dependencies are assigned to explicit vendor chunks.

- [x] **Step 4: Run diff hygiene**

```bash
git diff --check main...HEAD
git status --short
```

Expected:

```text
no diff-check errors
clean worktree
```

- [x] **Step 5: Manual smoke**

Start backend:

```bash
python server.py
```

Probe:

```bash
curl http://127.0.0.1:8000/health
curl "http://127.0.0.1:8000/darkpool/trade-intent?symbol=AAPL&provider=demo"
curl -X POST "http://127.0.0.1:8000/alerts/route?symbol=AAPL&alert_type=manual&channel=discord&size=1"
```

Expected:

```text
health is healthy
trade intent returns intent/sentinel/pulse_packet fields
alert route returns {"status":"sent"}
```

---

## Detailed Summary of Robust Fix Code

### 1. Provider Context Fix

The robust fix is to stop letting each caller assemble market data in its own way. Today, server routes use `fetch_provider_result()`, while Discord summaries ignore providers and call `sample_darkpool_prints()` directly. The new code creates one deep module:

```python
context = await build_market_context("AAPL", provider="finra")
```

Callers then use:

```python
context.provider_result.provider
context.provider_result.degraded
context.prints
context.levels
context.scores
context.alerts
context.confirmation_plan
```

This prevents fake provider labels and concentrates fallback/degraded behavior in one place.

### 2. Trade Pipeline Fix

The robust fix is to make `/darkpool/trade-intent` a thin adapter over:

```python
report = await build_trade_intent_report(...)
```

The pipeline does exactly this:

1. Fetch provider-backed market context.
2. Select strongest confluence score.
3. Build intent with user preferences and source confirmation weight.
4. Send the intent through Sentinel.
5. Prepare a Pulse packet only when Sentinel approves.
6. Validate Pulse packet guardrails.

This keeps Pulse communication behind explicit confirmation and risk checks.

### 3. Pulse Guardrail Fix

The robust fix is to add an explicit `PulseGateway` that rejects anything that looks like live order placement:

```python
if packet.get("requires_manual_execution") is not True:
    raise PulsePacketRejected("Pulse packet must require manual execution")
```

Forbidden keys:

```python
{"order_id", "broker_order_id", "live_order", "place_order", "execute_order"}
```

The adapter boundary remains testable without any live brokerage integration.

### 4. Runtime Startup Fix

The committed `f127de5` fix moved:

```python
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=port)
```

to the bottom of `server.py`, after all route decorators. The next robust step is a subprocess test proving the documented `python server.py` startup exposes late routes like `/alerts/route`.

### 5. Discord Security Fix

The robust fix is to verify Discord signatures on the HTTP interaction endpoint. Local unsigned commands are allowed only when explicitly configured:

```python
ALLOW_UNSIGNED_DISCORD_INTERACTIONS=true
```

Production should require:

```python
DISCORD_PUBLIC_KEY=...
ALLOW_UNSIGNED_DISCORD_INTERACTIONS=false
```

### 6. Frontend Readability Fix

The robust fix is to split the large `ProductionViews.jsx` trade-intent section into:

```text
src/tradeIntentControls.js
src/TradeIntentView.jsx
src/TradeIntentSummary.jsx
```

This keeps user input customization separate from display formatting and prevents the Intent view from becoming unreviewable.

---

## Execution Order

Recommended order:

1. Provider-backed market context.
2. Provider-backed Discord command summaries.
3. Trade intent pipeline.
4. Pulse gateway guardrails.
5. Runtime startup smoke test.
6. Source confirmation accuracy.
7. Discord signature verification.
8. Frontend split.
9. Final full verification.

This order fixes correctness before cosmetic structure. It also keeps each commit independently working.

---

## Final Verification Contract

No phase is complete until these pass:

```bash
pytest tests -q
npm test
npm run build
git diff --check main...HEAD
```

For runtime-sensitive changes, also run:

```bash
python server.py
```

and probe:

```bash
curl http://127.0.0.1:8000/health
curl "http://127.0.0.1:8000/darkpool/trade-intent?symbol=AAPL&provider=demo"
curl -X POST "http://127.0.0.1:8000/alerts/route?symbol=AAPL&alert_type=runtime&channel=discord&size=1"
```
