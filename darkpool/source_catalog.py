"""Market information source catalog for trade confirmation planning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SourceRole = Literal[
    "darkpool_context",
    "price_confirmation",
    "liquidity_confirmation",
    "options_confirmation",
    "risk_blocker",
    "news_context",
]
SourceCadence = Literal["real_time", "delayed", "historical", "on_demand"]
SourcePriority = Literal["required", "strong", "context"]
SourceStatus = Literal["available", "configured", "missing"]
CoverageStatus = Literal["met", "partial", "missing"]


class MarketInformationSource(BaseModel):
    id: str
    name: str
    provider: str
    role: SourceRole
    cadence: SourceCadence
    priority: SourcePriority
    confirmation_weight: float = Field(ge=0, le=1)
    status: SourceStatus = "missing"
    official_url: str
    confirms: list[str]
    limitations: list[str]
    current_integration: str


class TradeConfirmationPlan(BaseModel):
    sources: list[MarketInformationSource]
    coverage: list["ConfirmationCoverage"]
    available_confirmation_weight: float
    missing_confirmation_weight: float
    required_coverage_complete: bool
    recommended_next_sources: list[str]
    summary: str


class ConfirmationCoverage(BaseModel):
    role: SourceRole
    label: str
    required: bool
    status: CoverageStatus
    configured_source_ids: list[str]
    missing_source_ids: list[str]
    explanation: str


ROLE_LABELS: dict[SourceRole, str] = {
    "darkpool_context": "Dark pool context",
    "price_confirmation": "Real-time price/NBBO confirmation",
    "liquidity_confirmation": "Liquidity and depth confirmation",
    "options_confirmation": "Options flow confirmation",
    "risk_blocker": "Trading halt/LULD blocker",
    "news_context": "Material news context",
}

REQUIRED_CONFIRMATION_ROLES: set[SourceRole] = {
    "price_confirmation",
    "liquidity_confirmation",
    "risk_blocker",
    "news_context",
}

COVERAGE_ROLE_ORDER: list[SourceRole] = [
    "darkpool_context",
    "price_confirmation",
    "liquidity_confirmation",
    "options_confirmation",
    "risk_blocker",
    "news_context",
]


def list_market_information_sources() -> list[MarketInformationSource]:
    return [
        MarketInformationSource(
            id="finra_otc_transparency",
            name="FINRA OTC Transparency",
            provider="FINRA",
            role="darkpool_context",
            cadence="delayed",
            priority="context",
            confirmation_weight=0.0,
            official_url="https://www.finra.org/filing-reporting/otc-transparency",
            confirms=["ATS and non-ATS weekly aggregate activity", "historical darkpool participation context"],
            limitations=["not a real-time execution confirmation source"],
            current_integration="implemented for weekly aggregate/demo-compatible context",
        ),
        MarketInformationSource(
            id="finra_reg_sho",
            name="FINRA Reg SHO Daily Short Sale Volume",
            provider="FINRA",
            role="liquidity_confirmation",
            cadence="delayed",
            priority="context",
            confirmation_weight=0.05,
            official_url="https://developer.finra.org/docs/api-explorer/query_api-equity-reg_sho_daily_short_sale_volume",
            confirms=["daily short-sale pressure context", "sell-side volume participation"],
            limitations=["daily aggregate, not intraday confirmation"],
            current_integration="planned",
        ),
        MarketInformationSource(
            id="alpaca_delayed_sip_trades",
            name="Alpaca Basic SIP Trade History",
            provider="Alpaca",
            role="price_confirmation",
            cadence="delayed",
            priority="context",
            confirmation_weight=0.05,
            official_url="https://docs.alpaca.markets/us/docs/about-market-data-api",
            confirms=["full-SIP historical last-sale context outside the most recent 15 minutes"],
            limitations=["delayed; does not provide live NBBO or displayed depth"],
            current_integration="implemented for credentialed delayed SIP trade history",
        ),
        MarketInformationSource(
            id="massive_trf_trades",
            name="Massive Stocks Trades",
            provider="Massive",
            role="darkpool_context",
            cadence="delayed",
            priority="context",
            confirmation_weight=0.0,
            official_url="https://massive.com/docs/rest/stocks/trades-quotes/trades",
            confirms=["trade prints with exchange and TRF identifiers when plan entitlement permits"],
            limitations=["free access is end-of-day/backfill oriented; entitlement may not include tick trades"],
            current_integration="implemented for credentialed trade backfill",
        ),
        MarketInformationSource(
            id="tradier_timesales",
            name="Tradier Tick Time & Sales",
            provider="Tradier",
            role="price_confirmation",
            cadence="real_time",
            priority="context",
            confirmation_weight=0.1,
            official_url="https://docs.tradier.com/reference/brokerage-api-markets-get-timesales",
            confirms=["consolidated tick time-and-sales for eligible brokerage accounts"],
            limitations=["not a dark-pool-only feed and does not provide full NBBO/depth by itself"],
            current_integration="implemented for brokerage access tokens",
        ),
        MarketInformationSource(
            id="sip_nbbo",
            name="SIP/NBBO feed",
            provider="CTA/UTP or licensed market data vendor",
            role="price_confirmation",
            cadence="real_time",
            priority="required",
            confirmation_weight=0.35,
            official_url="https://www.ctaplan.com/index",
            confirms=["last sale", "NBBO spread", "price acceptance near level"],
            limitations=["requires licensed real-time CTA/UTP SIP or equivalent vendor access"],
            current_integration="missing adapter",
        ),
        MarketInformationSource(
            id="nasdaq_totalview",
            name="Nasdaq TotalView-ITCH",
            provider="Nasdaq",
            role="liquidity_confirmation",
            cadence="real_time",
            priority="strong",
            confirmation_weight=0.2,
            official_url="https://data.nasdaq.com/databases/NTV",
            confirms=["order book depth", "displayed liquidity", "imbalance around level"],
            limitations=["venue-specific direct feed; does not cover every market center alone"],
            current_integration="missing adapter",
        ),
        MarketInformationSource(
            id="opra_options",
            name="OPRA/Cboe LiveVol options data",
            provider="OPRA or Cboe LiveVol",
            role="options_confirmation",
            cadence="real_time",
            priority="strong",
            confirmation_weight=0.25,
            official_url="https://www.opraplan.com/",
            confirms=["options trades", "options quotes", "implied volatility and flow alignment"],
            limitations=["requires licensed options data and normalization"],
            current_integration="demo fixtures only",
        ),
        MarketInformationSource(
            id="trading_halts",
            name="Nasdaq/NYSE trading halts",
            provider="Nasdaq Trader and NYSE",
            role="risk_blocker",
            cadence="real_time",
            priority="required",
            confirmation_weight=0.2,
            official_url="https://www.nasdaqtrader.com/trader.aspx?id=tradehalts",
            confirms=["current halt state", "LULD/news-pending risk", "trade eligibility"],
            limitations=["requires polling or feed adapter for timely gating"],
            current_integration="missing adapter",
        ),
        MarketInformationSource(
            id="news_events",
            name="Material news and events",
            provider="Exchange halt feeds, SEC EDGAR, licensed news vendor",
            role="news_context",
            cadence="real_time",
            priority="required",
            confirmation_weight=0.15,
            official_url="https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
            confirms=["material filings", "news-pending risk", "event context"],
            limitations=["needs vendor-specific relevance scoring before automated gating"],
            current_integration="manual Sentinel checkbox only",
        ),
    ]


def build_trade_confirmation_plan(
    active_provider: str = "demo",
    configured_providers: list[str] | None = None,
) -> TradeConfirmationPlan:
    configured = {provider.lower() for provider in (configured_providers or [])}
    configured.add(active_provider.lower())
    sources: list[MarketInformationSource] = []
    for source in list_market_information_sources():
        status: SourceStatus = "missing"
        if source.id == "finra_otc_transparency" and "finra" in configured:
            status = "available"
        elif source.id == "alpaca_delayed_sip_trades" and "alpaca" in configured:
            status = "configured"
        elif source.id == "massive_trf_trades" and "massive" in configured:
            status = "configured"
        elif source.id == "tradier_timesales" and "tradier" in configured:
            status = "configured"
        elif source.id in {"sip_nbbo", "nasdaq_totalview"} and "polygon" in configured:
            status = "configured"
        elif source.id == "opra_options" and ("livevol" in configured or "intrinio" in configured):
            status = "configured"
        elif source.id == "trading_halts" and ("nasdaq_halts" in configured or "nyse_halts" in configured):
            status = "configured"
        elif source.id == "news_events" and ("sec_edgar" in configured or "news_vendor" in configured):
            status = "configured"
        sources.append(source.model_copy(update={"status": status}))

    confirmation_sources = [source for source in sources if source.confirmation_weight > 0]
    available_raw_weight = sum(
        source.confirmation_weight for source in confirmation_sources if source.status in {"available", "configured"}
    )
    available_weight = round(min(1.0, available_raw_weight), 2)
    missing_weight = round(max(0.0, 1.0 - available_weight), 2)
    recommendations = _build_recommendations(sources)
    coverage = _build_coverage(sources)
    required_coverage_complete = all(item.status == "met" for item in coverage if item.required)
    context_available = any(
        source.role == "darkpool_context" and source.status in {"available", "configured"} for source in sources
    )
    missing_required = [item.role for item in coverage if item.required and item.status != "met"]
    summary = (
        f"{'context source available' if context_available else 'no darkpool context source available'}; "
        f"{available_weight:.2f} confirmation weight configured, {missing_weight:.2f} missing; "
        f"{'required confirmations complete' if not missing_required else 'missing required confirmations: ' + ', '.join(missing_required)}."
    )
    return TradeConfirmationPlan(
        sources=sources,
        coverage=coverage,
        available_confirmation_weight=available_weight,
        missing_confirmation_weight=missing_weight,
        required_coverage_complete=required_coverage_complete,
        recommended_next_sources=recommendations,
        summary=summary,
    )


def _is_actionable_confirmation_source(source: MarketInformationSource) -> bool:
    if source.status not in {"available", "configured"}:
        return False
    if source.role == "darkpool_context":
        return True
    return source.cadence == "real_time" and source.priority in {"required", "strong"}


def _build_coverage(sources: list[MarketInformationSource]) -> list[ConfirmationCoverage]:
    coverage: list[ConfirmationCoverage] = []
    for role in COVERAGE_ROLE_ORDER:
        role_sources = [source for source in sources if source.role == role]
        active_sources = [source for source in role_sources if source.status in {"available", "configured"}]
        actionable_sources = [source for source in role_sources if _is_actionable_confirmation_source(source)]
        missing_sources = [source for source in role_sources if source.status == "missing"]

        if actionable_sources:
            status: CoverageStatus = "met"
        elif active_sources:
            status = "partial"
        else:
            status = "missing"

        label = ROLE_LABELS[role]
        configured_ids = [source.id for source in active_sources]
        missing_ids = [source.id for source in missing_sources]
        if status == "met":
            explanation = f"{label} covered by {', '.join(source.name for source in actionable_sources)}."
        elif status == "partial":
            explanation = f"{label} has only delayed or context coverage; real-time confirmation is still needed."
        else:
            explanation = f"{label} is missing; configure {', '.join(source.name for source in role_sources)}."

        coverage.append(
            ConfirmationCoverage(
                role=role,
                label=label,
                required=role in REQUIRED_CONFIRMATION_ROLES,
                status=status,
                configured_source_ids=configured_ids,
                missing_source_ids=missing_ids,
                explanation=explanation,
            )
        )
    return coverage


def _build_recommendations(sources: list[MarketInformationSource]) -> list[str]:
    missing = {source.id for source in sources if source.status == "missing"}
    recommendations: list[str] = []
    if "sip_nbbo" in missing:
        recommendations.append("Add real-time price/NBBO feed for spread and price confirmation.")
    if "trading_halts" in missing:
        recommendations.append(
            "Add Nasdaq Trade Halt RSS or exchange halt feed for halt/LULD confirmation before Pulse review."
        )
    if "news_events" in missing:
        recommendations.append("Configure SEC EDGAR or a licensed news vendor for material-news confirmation.")
    if "opra_options" in missing:
        recommendations.append("Add OPRA or Cboe LiveVol options data for options-flow confirmation.")
    if "nasdaq_totalview" in missing:
        recommendations.append("Add depth-of-book feed for liquidity confirmation around darkpool levels.")
    return recommendations
