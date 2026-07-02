"""End-to-end trade-intent report assembly."""

from __future__ import annotations

from dataclasses import dataclass

from .market_context import MarketContext, build_market_context
from .pulse_gateway import PulseGateway
from .source_catalog import TradeConfirmationPlan
from .trade_intent import (
    LocalSentinelEdgeAdapter,
    SentinelConfirmation,
    SentinelDecision,
    TradeIntent,
    TradingPreferences,
    build_trade_intent,
    is_external_sentinel_edge_decision,
    prepare_pulse_packet,
)


@dataclass(frozen=True)
class PulseStatus:
    status: str
    message: str
    reasons: list[str]
    requires_manual_execution: bool
    sentinel_status: str | None = None


@dataclass(frozen=True)
class TradeIntentReport:
    context: MarketContext
    preferences: TradingPreferences
    intent: TradeIntent | None
    sentinel: SentinelDecision | None
    pulse_packet: dict | None
    pulse_status: PulseStatus


def _pulse_status(
    include_pulse_packet: bool,
    intent: TradeIntent | None,
    sentinel: SentinelDecision | None,
    pulse_packet: dict | None,
) -> PulseStatus:
    if intent is None or sentinel is None:
        return PulseStatus(
            status="unavailable",
            message="Pulse communication unavailable because no trade intent could be built.",
            reasons=["no trade intent available"],
            requires_manual_execution=True,
            sentinel_status=None,
        )
    if not include_pulse_packet:
        return PulseStatus(
            status="not_requested",
            message="Pulse communication was not requested for this review.",
            reasons=["include_pulse_packet=false"],
            requires_manual_execution=True,
            sentinel_status=sentinel.status,
        )
    if pulse_packet is not None:
        return PulseStatus(
            status="prepared",
            message="Pulse communication packet prepared for manual execution review.",
            reasons=sentinel.reasons,
            requires_manual_execution=True,
            sentinel_status=sentinel.status,
        )
    return PulseStatus(
        status="withheld",
        message="Pulse communication withheld until Sentinel Edge approval is complete.",
        reasons=sentinel.reasons or intent.blockers or ["Sentinel Edge approval is required"],
        requires_manual_execution=True,
        sentinel_status=sentinel.status,
    )


def _missing_required_coverage_labels(plan: TradeConfirmationPlan) -> list[str]:
    return [item.label for item in plan.coverage if item.required and item.status != "met"]


def _attach_source_coverage(packet: dict, plan: TradeConfirmationPlan, missing_required: list[str]) -> dict:
    return {
        **packet,
        "required_source_coverage_complete": plan.required_coverage_complete,
        "missing_required_source_coverage": missing_required,
        "source_coverage": [item.model_dump(mode="json") for item in plan.coverage],
    }


async def build_trade_intent_report(
    symbol: str,
    provider: str,
    preferences: TradingPreferences,
    confirmation: SentinelConfirmation,
    include_pulse_packet: bool = False,
    price_bucket: float = 0.10,
    limit: int = 500,
    configured_providers: list[str] | None = None,
) -> TradeIntentReport:
    context = await build_market_context(
        symbol,
        provider=provider,
        limit=limit,
        price_bucket=price_bucket,
        configured_providers=configured_providers,
    )
    if not context.scores:
        return TradeIntentReport(
            context=context,
            preferences=preferences,
            intent=None,
            sentinel=None,
            pulse_packet=None,
            pulse_status=_pulse_status(include_pulse_packet, None, None, None),
        )

    missing_required_coverage = _missing_required_coverage_labels(context.confirmation_plan)
    intent = build_trade_intent(
        context.scores[0],
        preferences,
        source_confirmation_weight=context.confirmation_plan.available_confirmation_weight,
        source_coverage_complete=context.confirmation_plan.required_coverage_complete,
        missing_required_source_coverage=missing_required_coverage,
        market_regime=context.market_regime,
    )
    sentinel = LocalSentinelEdgeAdapter().review(intent, confirmation)
    pulse_packet = None
    if include_pulse_packet and is_external_sentinel_edge_decision(sentinel):
        pulse_packet = PulseGateway().validate_packet(
            _attach_source_coverage(
                prepare_pulse_packet(intent, sentinel),
                context.confirmation_plan,
                missing_required_coverage,
            )
        )

    return TradeIntentReport(
        context=context,
        preferences=preferences,
        intent=intent,
        sentinel=sentinel,
        pulse_packet=pulse_packet,
        pulse_status=_pulse_status(include_pulse_packet, intent, sentinel, pulse_packet),
    )
