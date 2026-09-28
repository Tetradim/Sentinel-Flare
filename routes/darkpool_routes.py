"""Dark pool route handlers."""

from __future__ import annotations

import os
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from darkpool.alerting import build_alert_candidates
from darkpool.backtesting import build_print_followthrough_backtest
from darkpool.bot_event_bus import build_trade_intent_event_payload, publish_event
from darkpool.confluence import classify_exposure_nodes, score_confluence
from darkpool.fixtures import get_stock, sample_exposure_nodes, sample_options_flow
from darkpool.level_engine import cluster_darkpool_levels, detect_air_pockets
from darkpool.providers import ProviderError, fetch_provider_result, get_provider_capability
from darkpool.regime import analyze_market_regime
from darkpool.source_catalog import build_trade_confirmation_plan, list_market_information_sources
from darkpool.trade_intent import SentinelConfirmation, SentinelDecision, TradeIntent, TradingPreferences, prepare_pulse_packet
from darkpool.trade_pipeline import build_trade_intent_report
from darkpool.pulse_gateway import PulseGateway

router = APIRouter()
EDGE_DECISION_SECRET_ENV = "DARKPOOL_EDGE_DECISION_SECRET"
EDGE_DECISION_SECRET_HEADER = "X-Darkpool-Edge-Secret"
MIN_EDGE_DECISION_SECRET_LENGTH = 32
EDGE_DECISION_SECRET_PLACEHOLDERS = {
    "replace_with_a_long_random_edge_decision_secret",
    "change_me",
    "changeme",
}


class OTCAggregateResponse(BaseModel):
    """OTC Aggregate Response."""

    data: list[dict]
    provider: str
    symbol: str | None
    tier: str
    is_ats: bool
    fetched_at: str


class EdgeDecisionIntakeRequest(BaseModel):
    intent: TradeIntent
    sentinel_decision: SentinelDecision
    expires_at: datetime | None = None
    edge_signature: str | None = Field(default=None, max_length=500)


def require_edge_decision_secret(
    provided_secret: str | None = Header(default=None, alias=EDGE_DECISION_SECRET_HEADER),
) -> None:
    configured_secret = os.getenv(EDGE_DECISION_SECRET_ENV, "").strip()
    if not configured_secret:
        raise HTTPException(503, f"{EDGE_DECISION_SECRET_ENV} is not configured")
    if (
        len(configured_secret) < MIN_EDGE_DECISION_SECRET_LENGTH
        or configured_secret.lower() in EDGE_DECISION_SECRET_PLACEHOLDERS
    ):
        raise HTTPException(
            503,
            f"{EDGE_DECISION_SECRET_ENV} must be a non-placeholder secret with at least "
            f"{MIN_EDGE_DECISION_SECRET_LENGTH} characters",
        )
    if not provided_secret or not secrets.compare_digest(provided_secret, configured_secret):
        raise HTTPException(401, "Invalid Darkpool Edge decision secret")


def configured_market_providers(active_provider: str) -> list[str]:
    configured = {active_provider.lower()}
    alpaca_key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
    alpaca_secret = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
    if alpaca_key and alpaca_secret:
        configured.add("alpaca")
    if os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY"):
        configured.add("massive")
    if os.getenv("TRADIER_ACCESS_TOKEN"):
        configured.add("tradier")
    if os.getenv("POLYGON_API_KEY"):
        configured.add("polygon")
    if os.getenv("INTRINIO_API_KEY"):
        configured.add("intrinio")
    if os.getenv("NASDAQ_HALTS_RSS_ENABLED", "").lower() in {"1", "true", "yes", "on"}:
        configured.add("nasdaq_halts")
    if os.getenv("SEC_EDGAR_USER_AGENT"):
        configured.add("sec_edgar")
    return sorted(configured)


async def _fetch_demo_otc_data(symbol: str | None) -> list[dict]:
    result = await fetch_provider_result(symbol, provider="finra", limit=500)
    return result.records


async def _fetch_finra_otc_data(symbol: str | None, tier: str, is_ats: bool) -> list[dict]:
    from finra_helper import aget_full_data

    data = await aget_full_data(symbol.upper() if symbol else None, tier, is_ats)
    return [
        {
            "symbol": item.get("issueSymbolIdentifier"),
            "share_quantity": item.get("totalWeeklyShareQuantity"),
            "trade_quantity": item.get("totalWeeklyTradeCount"),
            "update_date": item.get("lastUpdateDate"),
            "week_start": item.get("weekStartDate"),
        }
        for item in data
    ]


async def _fetch_polygon_otc_data(symbol: str | None) -> list[dict]:
    import httpx

    api_key = os.getenv("POLYGON_API_KEY", "")
    if not api_key:
        raise HTTPException(401, "Polygon API key not configured")

    url = f"https://api.polygon.io/v3/ticks/{symbol}/ Trades"
    params = {"timestamp": "latest", "limit": 5000}
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    if response.status_code != 200:
        return []
    data = response.json()
    return [
        {
            "symbol": trade.get("sym"),
            "price": trade.get("p"),
            "size": trade.get("s"),
            "exchange": trade.get("x"),
            "timestamp": trade.get("t"),
        }
        for trade in data.get("results", [])
    ]


async def _fetch_intrinio_otc_data(symbol: str | None) -> list[dict]:
    import httpx

    api_key = os.getenv("INTRINIO_API_KEY", "")
    if not api_key:
        raise HTTPException(401, "Intrinio API key not configured")

    url = f"https://api.intrinio.com/securities/{symbol}/ trades"
    params = {"darkpool_only": True, "page_size": 5000}
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    if response.status_code != 200:
        return []
    data = response.json()
    return [
        {
            "symbol": trade.get("ticker"),
            "price": trade.get("price"),
            "size": trade.get("volume"),
            "timestamp": trade.get("last_updated"),
        }
        for trade in data.get("trades", [])
    ]


async def _fetch_legacy_otc_data(
    symbol: str | None,
    provider: str,
    tier: str = "T1",
    is_ats: bool = True,
) -> list[dict]:
    provider_key = provider.lower()
    capability = get_provider_capability(provider_key)
    if not capability:
        raise HTTPException(400, f"Unknown provider: {provider}")
    if not capability.runnable:
        raise HTTPException(400, f"Provider {provider_key} is not available for execution: {capability.message}")

    if provider_key == "demo":
        return await _fetch_demo_otc_data(symbol)
    if provider_key == "finra":
        return await _fetch_finra_otc_data(symbol, tier, is_ats)
    if provider_key == "polygon":
        return await _fetch_polygon_otc_data(symbol)
    if provider_key == "intrinio":
        return await _fetch_intrinio_otc_data(symbol)
    raise HTTPException(400, f"Unknown provider: {provider}")


@router.get("/darkpool/otc", response_model=OTCAggregateResponse)
async def get_otc_aggregate(
    symbol: str | None = Query(None, description="Stock symbol (e.g., AAPL)"),
    provider: str = Query("finra", description="Data provider: demo, finra, polygon, intrinio"),
    tier: Literal["T1", "T2", "OTCE"] = Query(
        "T1",
        description="T1=S&P500, T2=NMS, OTCE=OTC equities",
    ),
    is_ats: bool = Query(True, description="ATS data if true, Non-ATS otherwise"),
):
    """Get weekly OTC/dark pool aggregate data."""
    try:
        data = await _fetch_legacy_otc_data(symbol, provider, tier, is_ats)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Error fetching data: {str(exc)}")

    return OTCAggregateResponse(
        data=data,
        provider=provider,
        symbol=symbol,
        tier=tier,
        is_ats=is_ats,
        fetched_at=datetime.utcnow().isoformat(),
    )


@router.get("/darkpool/trades")
async def get_recent_trades(
    symbol: str = Query(..., description="Stock symbol"),
    provider: str = Query("finra", description="Data provider"),
    limit: int = Query(100, ge=1, le=5000, description="Max results"),
):
    """Get recent dark pool trades for a symbol."""
    try:
        data = await _fetch_legacy_otc_data(symbol, provider, "T1", True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Error fetching trades: {str(exc)}")

    return {
        "symbol": symbol,
        "trades": data[:limit],
        "count": len(data[:limit]),
    }


@router.get("/darkpool/levels")
async def get_darkpool_levels(
    symbol: str = Query("AAPL", description="Stock symbol"),
    provider: str = Query("finra", description="Data provider: demo or finra"),
    price_bucket: float = Query(0.10, gt=0, le=5, description="Price clustering bucket"),
    limit: int = Query(25, ge=1, le=200),
):
    """Cluster dark pool prints into support/resistance context levels."""
    try:
        provider_result = await fetch_provider_result(symbol, provider=provider, limit=500)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    levels = cluster_darkpool_levels(provider_result.prints, price_bucket=price_bucket)[:limit]
    stock = get_stock(symbol)
    spot = float(stock.get("basePrice", 100.0))
    return {
        "symbol": symbol.upper(),
        "provider": provider_result.provider,
        "degraded": provider_result.degraded,
        "message": provider_result.message,
        "spot_price": spot,
        "levels": [level.model_dump(mode="json") for level in levels],
        "air_pockets": detect_air_pockets(levels, spot),
        "market_regime": analyze_market_regime(symbol, provider_result.prints, spot).model_dump(mode="json"),
        "fetched_at": datetime.utcnow().isoformat(),
    }


@router.get("/darkpool/confluence")
async def get_darkpool_confluence(
    symbol: str = Query("AAPL", description="Stock symbol"),
    provider: str = Query("finra", description="Data provider: demo or finra"),
    price_bucket: float = Query(0.10, gt=0, le=5),
    limit: int = Query(10, ge=1, le=50),
):
    """Score dark pool levels against Heatseeker-style exposure context and options flow."""
    try:
        provider_result = await fetch_provider_result(symbol, provider=provider, limit=500)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    stock = get_stock(symbol)
    spot = float(stock.get("basePrice", 100.0))
    levels = cluster_darkpool_levels(provider_result.prints, price_bucket=price_bucket)
    exposure_nodes = sample_exposure_nodes(symbol, spot)
    options_flow = sample_options_flow(symbol)
    scores = score_confluence(symbol, spot, levels, exposure_nodes, options_flow)[:limit]
    node_map = classify_exposure_nodes(symbol, spot, exposure_nodes)
    market_regime = analyze_market_regime(symbol, provider_result.prints, spot)

    return {
        "symbol": symbol.upper(),
        "provider": provider_result.provider,
        "degraded": provider_result.degraded,
        "message": provider_result.message,
        "spot_price": spot,
        "node_map": {
            key: value.model_dump(mode="json") if hasattr(value, "model_dump") else [
                item.model_dump(mode="json") for item in value
            ] if isinstance(value, list) else value
            for key, value in node_map.items()
        },
        "market_regime": market_regime.model_dump(mode="json"),
        "scores": [score.model_dump(mode="json") for score in scores],
        "fetched_at": datetime.utcnow().isoformat(),
    }


@router.get("/darkpool/alert-candidates")
async def get_darkpool_alert_candidates(
    symbol: str = Query("AAPL", description="Stock symbol"),
    provider: str = Query("finra", description="Data provider: demo or finra"),
    price_bucket: float = Query(0.10, gt=0, le=5),
    limit: int = Query(20, ge=1, le=100),
):
    """Generate explainable alert candidates without auto-execution."""
    try:
        provider_result = await fetch_provider_result(symbol, provider=provider, limit=500)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    stock = get_stock(symbol)
    spot = float(stock.get("basePrice", 100.0))
    levels = cluster_darkpool_levels(provider_result.prints, price_bucket=price_bucket)
    scores = score_confluence(symbol, spot, levels, sample_exposure_nodes(symbol, spot), sample_options_flow(symbol))
    alerts = build_alert_candidates(symbol, levels, scores)[:limit]
    return {
        "symbol": symbol.upper(),
        "provider": provider_result.provider,
        "degraded": provider_result.degraded,
        "message": provider_result.message,
        "alerts": [alert.model_dump(mode="json") for alert in alerts],
    }


@router.get("/darkpool/backtest")
async def get_darkpool_backtest(
    symbol: str = Query("AAPL", description="Stock symbol"),
    provider: str = Query("finra", description="Data provider: demo or finra"),
    fee_bps: float = Query(2.0, ge=0, le=100, description="Round-trip cost estimate in basis points"),
    trade_limit: int = Query(50, ge=1, le=500),
):
    """Return lightweight print follow-through backtest metrics."""
    raise HTTPException(410, "Darkpool follow-through backtests have been removed from runtime.")
    try:
        provider_result = await fetch_provider_result(symbol, provider=provider, limit=500)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    summary = build_print_followthrough_backtest(
        symbol=symbol,
        provider=provider_result.provider,
        prints=provider_result.prints,
        fee_bps=fee_bps,
        limit=trade_limit,
    )
    return {
        "symbol": symbol.upper(),
        "provider": provider_result.provider,
        "degraded": provider_result.degraded,
        "message": provider_result.message,
        "summary": summary.model_dump(mode="json"),
        "fetched_at": datetime.utcnow().isoformat(),
    }


@router.get("/darkpool/information-sources")
async def get_darkpool_information_sources(
    active_provider: str = Query("finra", description="Active source provider for current workflow"),
):
    """Return market information sources and their confirmation roles."""
    configured_providers = configured_market_providers(active_provider)
    plan = build_trade_confirmation_plan(active_provider=active_provider, configured_providers=configured_providers)
    return {
        "active_provider": active_provider,
        "catalog": [source.model_dump(mode="json") for source in list_market_information_sources()],
        "confirmation_plan": plan.model_dump(mode="json"),
    }


@router.get("/darkpool/trade-intent")
async def get_darkpool_trade_intent(
    symbol: str = Query("AAPL", description="Stock symbol"),
    provider: str = Query("finra", description="Data provider: demo or finra"),
    price_bucket: float = Query(0.10, gt=0, le=5),
    min_score: float = Query(75.0, ge=0, le=100),
    max_distance_pct: float = Query(1.0, ge=0, le=10),
    min_notional: float = Query(25_000_000.0, ge=0),
    max_freshness_minutes: float = Query(120.0, ge=0),
    max_risk_dollars: float = Query(500.0, ge=0),
    stop_distance_pct: float = Query(1.0, ge=0, le=20),
    reward_risk_ratio: float = Query(2.0, ge=0),
    max_position_notional: float = Query(50_000.0, ge=0),
    max_session_drawdown_pct: float = Query(5.0, ge=0, le=100),
    current_session_drawdown_pct: float = Query(0.0, ge=0, le=100),
    max_regime_volatility_pct: float = Query(10.0, ge=0, le=100),
    allow_trend_up: bool = Query(True),
    allow_trend_down: bool = Query(True),
    allow_range_bound: bool = Query(True),
    allow_high_volatility: bool = Query(False),
    allow_insufficient_data_regime: bool = Query(False),
    use_volatility_adjusted_stop: bool = Query(True),
    max_quality_caution_flags: int = Query(99, ge=0),
    min_quality_support_flags: int = Query(0, ge=0),
    min_source_confirmation_weight: float = Query(0.0, ge=0, le=1),
    require_source_coverage_complete: bool = Query(True),
    source_coverage_override_reason: str | None = Query(None, max_length=500),
    price_confirmed: bool = Query(False),
    liquidity_confirmed: bool = Query(False),
    news_checked: bool = Query(False),
    observed_spread_bps: float = Query(0.0, ge=0),
    max_spread_bps: float = Query(25.0, ge=0),
    allow_buy: bool = Query(True),
    allow_sell: bool = Query(True),
    include_pulse_packet: bool = Query(False),
):
    """Build a user-readable trade intent and gate it through Sentinel Edge."""
    configured_providers = configured_market_providers(provider)
    preferences = TradingPreferences(
        min_score=min_score,
        max_distance_pct=max_distance_pct,
        min_notional=min_notional,
        max_freshness_minutes=max_freshness_minutes,
        max_risk_dollars=max_risk_dollars,
        stop_distance_pct=stop_distance_pct,
        reward_risk_ratio=reward_risk_ratio,
        max_position_notional=max_position_notional,
        max_session_drawdown_pct=max_session_drawdown_pct,
        current_session_drawdown_pct=current_session_drawdown_pct,
        max_regime_volatility_pct=max_regime_volatility_pct,
        allowed_market_regimes=[
            regime
            for regime, enabled in [
                ("trend_up", allow_trend_up),
                ("trend_down", allow_trend_down),
                ("range_bound", allow_range_bound),
                ("high_volatility", allow_high_volatility),
                ("insufficient_data", allow_insufficient_data_regime),
            ]
            if enabled
        ],
        use_volatility_adjusted_stop=use_volatility_adjusted_stop,
        max_quality_caution_flags=max_quality_caution_flags,
        min_quality_support_flags=min_quality_support_flags,
        min_source_confirmation_weight=min_source_confirmation_weight,
        require_complete_source_coverage=require_source_coverage_complete,
        source_coverage_override_reason=source_coverage_override_reason,
        allowed_actions=[action for action, enabled in [("BUY", allow_buy), ("SELL", allow_sell)] if enabled],
    )
    confirmation = SentinelConfirmation(
        price_confirmed=price_confirmed,
        liquidity_confirmed=liquidity_confirmed,
        news_checked=news_checked,
        observed_spread_bps=observed_spread_bps,
        max_spread_bps=max_spread_bps,
    )
    try:
        report = await build_trade_intent_report(
            symbol=symbol,
            provider=provider,
            preferences=preferences,
            confirmation=confirmation,
            include_pulse_packet=include_pulse_packet,
            price_bucket=price_bucket,
            limit=500,
            configured_providers=configured_providers,
        )
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    context = report.context
    provider_result = context.provider_result
    scores = context.scores
    confirmation_sources = context.confirmation_plan

    if not scores:
        response_payload = {
            "symbol": context.symbol,
            "provider": provider_result.provider,
            "degraded": provider_result.degraded,
            "message": provider_result.message,
            "preferences": preferences.model_dump(mode="json"),
            "confirmation_sources": confirmation_sources.model_dump(mode="json"),
            "market_regime": context.market_regime.model_dump(mode="json"),
            "intent": None,
            "sentinel": None,
            "pulse_packet": None,
            "pulse_status": asdict(report.pulse_status),
            "fetched_at": datetime.utcnow().isoformat(),
        }
        publish_event(
            "darkpool.trade_intent.reviewed",
            build_trade_intent_event_payload(report),
            target="sentinel-edge",
        )
        return response_payload

    response_payload = {
        "symbol": context.symbol,
        "provider": provider_result.provider,
        "degraded": provider_result.degraded,
        "message": provider_result.message,
        "preferences": preferences.model_dump(mode="json"),
        "confirmation_sources": confirmation_sources.model_dump(mode="json"),
        "market_regime": context.market_regime.model_dump(mode="json"),
        "intent": report.intent.model_dump(mode="json") if report.intent else None,
        "sentinel": report.sentinel.model_dump(mode="json") if report.sentinel else None,
        "pulse_packet": report.pulse_packet,
        "pulse_status": asdict(report.pulse_status),
        "source_score": scores[0].model_dump(mode="json"),
        "fetched_at": datetime.utcnow().isoformat(),
    }
    publish_event(
        "darkpool.trade_intent.reviewed",
        build_trade_intent_event_payload(report),
        target="sentinel-edge",
    )
    return response_payload


@router.post("/darkpool/trade-intent/edge-decision")
async def accept_sentinel_edge_decision(
    request: EdgeDecisionIntakeRequest,
    _: None = Depends(require_edge_decision_secret),
):
    """Accept an authenticated Sentinel Edge decision for a Darkpool signal."""
    if request.expires_at:
        expires_at = request.expires_at if request.expires_at.tzinfo else request.expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(422, "Sentinel Edge decision expired")
    try:
        pulse_packet = PulseGateway().validate_packet(
            prepare_pulse_packet(request.intent, request.sentinel_decision)
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    payload = {
        "idempotency_key": f"sentinel-flare:{request.intent.symbol}:edge_decision:{request.sentinel_decision.decision_id}",
        "intent": request.intent.model_dump(mode="json"),
        "sentinel_decision": request.sentinel_decision.model_dump(mode="json"),
        "pulse_packet": pulse_packet,
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "edge_signature_present": bool(request.edge_signature),
        "mode": "signal_only",
        "execution": "none",
    }
    event = publish_event(
        "darkpool.edge_decision.accepted",
        payload,
        target="sentinel-edge",
    )
    edge_decision_metadata = {
        "decision_id": request.sentinel_decision.decision_id,
        "status": request.sentinel_decision.status,
        "reviewer": request.sentinel_decision.reviewer,
        "reviewed_at": request.sentinel_decision.reviewed_at.isoformat(),
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "signature_present": bool(request.edge_signature),
    }
    return {
        "status": "accepted",
        "mode": "signal_only",
        "execution": "none",
        "requires_manual_execution": True,
        "edge_decision": edge_decision_metadata,
        "sentinel": request.sentinel_decision.model_dump(mode="json"),
        "pulse_packet": pulse_packet,
        "event_id": event["event_id"],
        "duplicate": event.get("duplicate", False),
    }
