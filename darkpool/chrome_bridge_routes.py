"""Local Chrome Discord bridge ingestion for Sentinel Flare."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .bot_event_bus import publish_event


LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
DEFAULT_TARGET = "sentinel-flare"
_lock = Lock()
_last_heartbeat: dict[str, Any] | None = None

router = APIRouter(prefix="/api/discord/chrome-bridge", tags=["chrome-discord-bridge"])


class ChromeBridgeEmbed(BaseModel):
    author_name: str | None = None
    title: str | None = None
    description: str | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    footer_text: str | None = None


class ChromeBridgeMessage(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=240)
    channel_id: str = Field(default="chrome-visible-discord", max_length=120)
    channel_name: str = Field(default="chrome-visible-discord", max_length=120)
    channel_url: str | None = Field(default=None, max_length=2048)
    author_id: str | None = Field(default=None, max_length=120)
    author_name: str = Field(default="Discord Chrome", max_length=120)
    content: str = Field(default="", max_length=12000)
    embeds: list[ChromeBridgeEmbed] = Field(default_factory=list)
    url: str | None = Field(default=None, max_length=2048)
    observed_at: str | None = Field(default=None, max_length=80)
    source: str = Field(default="chrome-discord-bridge", max_length=80)
    bridge_target_id: str | None = Field(default=None, max_length=120)
    bridge_target_name: str | None = Field(default=None, max_length=120)


class ChromeBridgeHeartbeat(BaseModel):
    status: str = Field(default="ok", max_length=80)
    bridge_enabled: bool = False
    url: str | None = Field(default=None, max_length=2048)
    channel_id: str | None = Field(default=None, max_length=120)
    channel_name: str | None = Field(default=None, max_length=120)
    channel_url: str | None = Field(default=None, max_length=2048)
    observed_at: str | None = Field(default=None, max_length=80)
    last_forward_at: str | None = Field(default=None, max_length=80)
    last_forward_status: str | None = Field(default=None, max_length=120)
    bridge_target_id: str | None = Field(default=None, max_length=120)
    bridge_target_name: str | None = Field(default=None, max_length=120)
    details: dict[str, Any] = Field(default_factory=dict)


@router.post("/message")
async def ingest_message(payload: ChromeBridgeMessage, request: Request) -> dict[str, Any]:
    _ensure_local_request(request)
    raw_text = _raw_text(payload)
    if not raw_text:
        raise HTTPException(status_code=400, detail="message content or embed text is required")
    event = publish_event(
        "signal.observed",
        _message_payload(payload, raw_text),
        source="chrome-discord-bridge",
        target=payload.bridge_target_id or DEFAULT_TARGET,
    )
    return {"status": "accepted", "event_id": payload.event_id, "raw_text": raw_text, "bus_event_id": event["event_id"]}


@router.post("/heartbeat")
async def ingest_heartbeat(payload: ChromeBridgeHeartbeat, request: Request) -> dict[str, Any]:
    _ensure_local_request(request)
    heartbeat = _heartbeat_payload(payload)
    with _lock:
        global _last_heartbeat
        _last_heartbeat = heartbeat
    publish_event(
        "bridge.health",
        heartbeat,
        source="chrome-discord-bridge",
        target=payload.bridge_target_id or DEFAULT_TARGET,
    )
    return get_bridge_health()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    _ensure_local_request(request)
    return get_bridge_health()


def get_bridge_health() -> dict[str, Any]:
    with _lock:
        heartbeat = dict(_last_heartbeat) if _last_heartbeat else None
    issues: list[str] = []
    if heartbeat is None:
        issues.append("chrome bridge has not sent a heartbeat")
    elif heartbeat.get("status") not in {"ok", "disabled"}:
        issues.append(f"chrome bridge reported {heartbeat.get('status')}")
    elif not heartbeat.get("bridge_enabled", False):
        issues.append("chrome bridge is disabled")
    return {"healthy": not issues, "status": "healthy" if not issues else "unhealthy", "issues": issues, "last_heartbeat": heartbeat}


def _ensure_local_request(request: Request) -> None:
    if os.environ.get("CHROME_BRIDGE_ALLOW_REMOTE", "").lower() in {"1", "true", "yes"}:
        return
    host = request.client.host if request.client else ""
    if host not in LOCAL_CLIENT_HOSTS:
        raise HTTPException(status_code=403, detail="chrome bridge endpoint only accepts local requests")


def _message_payload(payload: ChromeBridgeMessage, raw_text: str) -> dict[str, Any]:
    return {
        "contract_version": "chrome.discord.message.v1",
        "event_id": payload.event_id,
        "source": payload.source,
        "channel_id": payload.channel_id,
        "channel_name": payload.channel_name,
        "channel_url": payload.channel_url,
        "url": payload.url,
        "observed_at": payload.observed_at,
        "bridge_target_id": payload.bridge_target_id,
        "bridge_target_name": payload.bridge_target_name,
        "author_id": payload.author_id,
        "author_name": payload.author_name,
        "raw_text": raw_text,
    }


def _heartbeat_payload(payload: ChromeBridgeHeartbeat) -> dict[str, Any]:
    heartbeat = payload.model_dump(mode="json")
    heartbeat["observed_at"] = heartbeat.get("observed_at") or datetime.now(timezone.utc).isoformat()
    heartbeat["healthy"] = heartbeat.get("status") == "ok" and bool(heartbeat.get("bridge_enabled"))
    return heartbeat


def _raw_text(payload: ChromeBridgeMessage) -> str:
    parts = [payload.content]
    for embed in payload.embeds:
        parts.extend([embed.author_name or "", embed.title or "", embed.description or ""])
        parts.extend(f"{field.get('name', '')} {field.get('value', '')}" for field in embed.fields)
        parts.append(embed.footer_text or "")
    return "\n".join(part.strip() for part in parts if str(part or "").strip())
