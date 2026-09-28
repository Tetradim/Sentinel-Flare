"""In-memory Discord autopost subscription store."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Subscription:
    id: str
    channel_id: str
    topic: str
    symbols: list[str]
    min_score: float = 70.0
    provider: str = "finra"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "topic": self.topic,
            "symbols": self.symbols,
            "min_score": self.min_score,
            "provider": self.provider,
            "created_at": self.created_at,
        }


class SubscriptionStore:
    def __init__(self):
        self._subscriptions: dict[str, Subscription] = {}

    def create(
        self,
        channel_id: str,
        topic: str,
        symbols: list[str],
        min_score: float = 70.0,
        provider: str = "finra",
    ) -> Subscription:
        cleaned_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        subscription = Subscription(
            id=secrets.token_urlsafe(8),
            channel_id=channel_id,
            topic=topic.lower(),
            symbols=cleaned_symbols,
            min_score=min_score,
            provider=provider,
        )
        self._subscriptions[subscription.id] = subscription
        return subscription

    def list(self, channel_id: str | None = None) -> list[Subscription]:
        rows = list(self._subscriptions.values())
        if channel_id is not None:
            rows = [row for row in rows if row.channel_id == channel_id]
        return sorted(rows, key=lambda row: row.created_at)

    def delete(self, subscription_id: str, channel_id: str | None = None) -> bool:
        subscription = self._subscriptions.get(subscription_id)
        if not subscription:
            return False
        if channel_id is not None and subscription.channel_id != channel_id:
            return False
        del self._subscriptions[subscription_id]
        return True

    def clear(self):
        self._subscriptions.clear()
