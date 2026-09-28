"""Shared summary builders for API and Discord commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from .market_context import MarketContext, build_market_context


@dataclass
class SummarySection:
    title: str
    items: list[str]


@dataclass
class CommandSummary:
    command: str
    symbol: str
    title: str
    description: str
    metrics: dict[str, str] = field(default_factory=dict)
    sections: list[SummarySection] = field(default_factory=list)


async def _data_context(symbol: str, provider: str = "finra") -> MarketContext:
    return await build_market_context(symbol, provider=provider, limit=220)


async def build_levels_summary(symbol: str, provider: str = "finra", limit: int = 5) -> CommandSummary:
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


async def build_confluence_summary(symbol: str, provider: str = "finra", limit: int = 5) -> CommandSummary:
    context = await _data_context(symbol, provider)
    items = [
        f"${score.level_price:.2f} | {score.direction} | score {score.score:.1f} | {', '.join(score.reasons[:2])}"
        for score in context.scores[:limit]
    ]
    return CommandSummary(
        command="confluence",
        symbol=context.symbol,
        title=f"{context.symbol} Confluence",
        description="Dark pool levels scored against exposure nodes and options-flow context.",
        metrics={"provider": context.provider_result.provider, "spot": f"{context.spot_price:.2f}"},
        sections=[SummarySection("Highest Scores", items or ["No confluence scores found"])],
    )


async def build_alerts_summary(symbol: str, provider: str = "finra", limit: int = 5) -> CommandSummary:
    context = await _data_context(symbol, provider)
    items = [
        f"{alert.severity.upper()} | {alert.message} | score {alert.score:.1f}"
        for alert in context.alerts[:limit]
    ]
    return CommandSummary(
        command="alerts",
        symbol=context.symbol,
        title=f"{context.symbol} Alert Candidates",
        description="Explainable candidates for human review and optional autoposting.",
        metrics={"provider": context.provider_result.provider, "spot": f"{context.spot_price:.2f}"},
        sections=[SummarySection("Candidates", items or ["No alert candidates found"])],
    )


async def build_darkpool_summary(symbol: str, provider: str = "finra") -> CommandSummary:
    levels = await build_levels_summary(symbol, provider, limit=3)
    confluence = await build_confluence_summary(symbol, provider, limit=3)
    alerts = await build_alerts_summary(symbol, provider, limit=3)
    return CommandSummary(
        command="darkpool",
        symbol=levels.symbol,
        title=f"{levels.symbol} Sentinel Flare",
        description="Level, confluence, and alert context. Require price confirmation before acting.",
        metrics=levels.metrics,
        sections=[
            levels.sections[0],
            confluence.sections[0],
            alerts.sections[0],
        ],
    )


async def build_watchlist_summary(symbols: list[str], provider: str = "finra") -> CommandSummary:
    cleaned = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    rows: list[tuple[str, float, str]] = []
    for symbol in cleaned:
        summary = await build_alerts_summary(symbol, provider, limit=1)
        first = summary.sections[0].items[0]
        score = 0.0
        if "score " in first:
            try:
                score = float(first.rsplit("score ", 1)[1])
            except ValueError:
                score = 0.0
        rows.append((symbol, score, first))
    rows.sort(key=lambda item: item[1], reverse=True)

    return CommandSummary(
        command="watchlist",
        symbol=",".join(cleaned),
        title="Watchlist Darkpool Summary",
        description="Top alert candidate per ticker.",
        metrics={"provider": provider, "symbols": ",".join(cleaned)},
        sections=[SummarySection("Watchlist", [f"{symbol}: {text}" for symbol, _, text in rows])],
    )
