"""Sentinel Flare intelligence publisher for Sentinel Edge.

This module intentionally has no execution vocabulary.  It converts dark-pool
observations into an expiring ``flare.intelligence.v1`` document and sends that
document to Edge's authenticated intelligence inbox.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import secrets
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field


_FORBIDDEN_EXECUTION_FIELDS = {
    "action", "side", "quantity", "qty", "notional", "order_type", "limit_price",
    "stop_price", "take_profit", "reduce_only", "leverage", "broker", "exchange",
}


def _unit(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(0.0, min(1.0, number))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


class FlarePublishRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    direction: Literal["bullish", "bearish", "neutral"] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    ttl_seconds: int = Field(default=900, ge=30, le=21600)
    observed_at: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class EdgeIntelligencePublisher:
    def __init__(self) -> None:
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None

    def build(self, request: FlarePublishRequest) -> dict[str, Any]:
        evidence = dict(request.evidence)
        forbidden = sorted(key for key in evidence if str(key).lower() in _FORBIDDEN_EXECUTION_FIELDS)
        if forbidden:
            raise ValueError(f"Flare intelligence cannot contain execution fields: {', '.join(forbidden)}")

        buy_volume = max(0.0, _number(evidence.get("buy_volume")))
        sell_volume = max(0.0, _number(evidence.get("sell_volume")))
        total_volume = buy_volume + sell_volume
        flow_imbalance = (buy_volume - sell_volume) / total_volume if total_volume > 0 else _number(evidence.get("orderbook_imbalance"))
        flow_imbalance = max(-1.0, min(1.0, flow_imbalance))
        zscore = abs(_number(evidence.get("zscore")))
        adv_pct = max(0.0, _number(evidence.get("adv_pct")))

        if request.direction is not None:
            direction = request.direction
        elif flow_imbalance >= 0.10:
            direction = "bullish"
        elif flow_imbalance <= -0.10:
            direction = "bearish"
        else:
            direction = "neutral"

        anomaly_component = min(1.0, zscore / 4.0)
        liquidity_component = min(1.0, adv_pct / 5.0)
        flow_component = abs(flow_imbalance)
        derived_strength = max(flow_component, anomaly_component * 0.8, liquidity_component * 0.8)
        derived_confidence = min(0.98, 0.35 + flow_component * 0.35 + anomaly_component * 0.20 + liquidity_component * 0.10)
        confidence = _unit(request.confidence, derived_confidence)
        strength = _unit(request.strength, derived_strength)
        if direction == "neutral":
            strength = min(strength, 0.35)

        observed_at = request.observed_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        observed_at = observed_at.astimezone(timezone.utc)
        expires_at = observed_at + timedelta(seconds=request.ttl_seconds)
        canonical = {
            "symbol": request.symbol.strip().upper(),
            "direction": direction,
            "confidence": round(confidence, 6),
            "strength": round(strength, 6),
            "observed_at": observed_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "evidence": evidence,
        }
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {
            "contract_version": "flare.intelligence.v1",
            "intelligence_id": f"flare-intel:{digest[:32]}",
            "source_bot": "sentinel-flare",
            **canonical,
        }

    async def publish(self, document: dict[str, Any]) -> dict[str, Any]:
        base_url = os.getenv("EDGE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        operator_secret = os.getenv("EDGE_OPERATOR_ACTION_SECRET", "").strip()
        if not operator_secret:
            raise RuntimeError("EDGE_OPERATOR_ACTION_SECRET is required to publish Flare intelligence")
        timeout = max(0.5, float(os.getenv("FLARE_EDGE_TIMEOUT_SECONDS", "5") or 5))
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{base_url}/bus/intelligence/flare",
                    json=document,
                    headers={"X-Edge-Operator-Secret": operator_secret},
                )
            response.raise_for_status()
            result = response.json()
            self.last_result = {
                "status": "delivered",
                "intelligence_id": document["intelligence_id"],
                "symbol": document["symbol"],
                "delivered_at": datetime.now(timezone.utc).isoformat(),
                "edge_response": result,
            }
            self.last_error = None
            return result
        except Exception as exc:
            self.last_error = str(exc)
            self.last_result = {
                "status": "failed",
                "intelligence_id": document.get("intelligence_id"),
                "symbol": document.get("symbol"),
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }
            raise

    def status(self) -> dict[str, Any]:
        return {
            "contract_version": "flare.edge_bridge.status.v1",
            "edge_base_url": os.getenv("EDGE_BASE_URL", "http://127.0.0.1:8000"),
            "configured": bool(os.getenv("EDGE_OPERATOR_ACTION_SECRET", "").strip()),
            "last_result": self.last_result,
            "last_error": self.last_error,
        }


publisher = EdgeIntelligencePublisher()
router = APIRouter(prefix="/integration/edge", tags=["Sentinel Edge Integration"])


def _require_bridge_secret(request: Request) -> None:
    expected = os.getenv("FLARE_EDGE_BRIDGE_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="FLARE_EDGE_BRIDGE_SECRET is required")
    provided = request.headers.get("X-Flare-Bridge-Secret", "")
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Flare bridge secret")


@router.post("/intelligence/preview")
async def preview_intelligence(payload: FlarePublishRequest) -> dict[str, Any]:
    try:
        return {"status": "preview", "intelligence": publisher.build(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/intelligence/publish")
async def publish_intelligence(payload: FlarePublishRequest, request: Request) -> dict[str, Any]:
    _require_bridge_secret(request)
    try:
        document = publisher.build(payload)
        edge_response = await publisher.publish(document)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Edge intelligence delivery failed: {exc}") from exc
    return {"status": "delivered", "intelligence": document, "edge_response": edge_response}


@router.get("/status")
async def edge_bridge_status() -> dict[str, Any]:
    return publisher.status()


def register(app: Any) -> None:
    if getattr(app.state, "flare_edge_bridge_registered", False):
        return
    app.include_router(router)
    app.state.flare_edge_bridge_registered = True
