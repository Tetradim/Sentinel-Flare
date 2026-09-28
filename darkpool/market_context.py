"""Provider-backed market context assembly for dark pool workflows."""

from __future__ import annotations

from dataclasses import dataclass

from .alerting import build_alert_candidates
from .confluence import score_confluence
from .fixtures import get_stock, sample_exposure_nodes, sample_options_flow
from .level_engine import cluster_darkpool_levels
from .models import AlertCandidate, ConfluenceScore, DarkpoolLevel, DarkpoolPrint, ExposureNode, MarketRegime, OptionsFlowSignal
from .providers import ProviderResult, fetch_provider_result
from .regime import analyze_market_regime
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
    market_regime: MarketRegime
    scores: list[ConfluenceScore]
    alerts: list[AlertCandidate]
    confirmation_plan: TradeConfirmationPlan


async def build_market_context(
    symbol: str,
    provider: str = "finra",
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
    market_regime = analyze_market_regime(sym, provider_result.prints, spot)
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
        market_regime=market_regime,
        scores=scores,
        alerts=alerts,
        confirmation_plan=confirmation_plan,
    )
