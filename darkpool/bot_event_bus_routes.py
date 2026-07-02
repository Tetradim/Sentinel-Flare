"""FastAPI routes for Sentinel Flare Cross Bot Event Bus access."""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .bot_event_bus import EventBusStore, publish_event


router = APIRouter(prefix="/api/bus", tags=["cross-bot-event-bus"])
EVENT_BUS_SECRET_ENV = "DARKPOOL_EVENT_BUS_SECRET"
EVENT_BUS_SECRET_HEADER = "X-Darkpool-Event-Bus-Secret"
MIN_EVENT_BUS_SECRET_LENGTH = 32
EVENT_BUS_SECRET_PLACEHOLDERS = {
    "replace_with_a_long_random_event_bus_secret",
    "change_me",
    "changeme",
}


class EventRequest(BaseModel):
    event_type: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "sentinel-flare"
    target: str | None = None


def require_event_bus_secret(
    provided_secret: str | None = Header(default=None, alias=EVENT_BUS_SECRET_HEADER),
) -> None:
    configured_secret = os.getenv(EVENT_BUS_SECRET_ENV, "").strip()
    if not configured_secret:
        raise HTTPException(503, f"{EVENT_BUS_SECRET_ENV} is not configured")
    if (
        len(configured_secret) < MIN_EVENT_BUS_SECRET_LENGTH
        or configured_secret.lower() in EVENT_BUS_SECRET_PLACEHOLDERS
    ):
        raise HTTPException(
            503,
            f"{EVENT_BUS_SECRET_ENV} must be a non-placeholder secret with at least "
            f"{MIN_EVENT_BUS_SECRET_LENGTH} characters",
        )
    if not provided_secret or not secrets.compare_digest(provided_secret, configured_secret):
        raise HTTPException(401, "Invalid Darkpool event bus secret")


@router.get("/events")
async def list_events(
    limit: int = Query(100, ge=1, le=1000),
    target: str | None = Query(None),
    _: None = Depends(require_event_bus_secret),
) -> dict[str, Any]:
    events = EventBusStore().list_events(limit=limit, target=target)
    return {"events": events, "count": len(events)}


@router.post("/events")
async def create_event(
    request: EventRequest,
    _: None = Depends(require_event_bus_secret),
) -> dict[str, Any]:
    event = publish_event(
        request.event_type,
        request.payload,
        source=request.source,
        target=request.target,
    )
    return {"event": event}
