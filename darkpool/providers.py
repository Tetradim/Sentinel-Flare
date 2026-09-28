"""Data provider wrappers and capability metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from .fixtures import get_stock
from .models import DarkpoolPrint


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderResult:
    provider: str
    records: list[dict]
    prints: list[DarkpoolPrint]
    degraded: bool = False
    message: str | None = None


@dataclass(frozen=True)
class ProviderCapability:
    id: str
    label: str
    runnable: bool
    requires_api_key: bool
    has_api_key: bool
    status: str
    message: str


def list_provider_capabilities() -> list[ProviderCapability]:
    has_alpaca = _has_alpaca_credentials()
    has_massive = bool(_massive_api_key())
    has_tradier = bool(os.getenv("TRADIER_ACCESS_TOKEN"))
    return [
        ProviderCapability(
            id="demo",
            label="Demo Tape",
            runnable=False,
            requires_api_key=False,
            has_api_key=False,
            status="removed",
            message="Demo data has been removed from runtime trade-intent workflows.",
        ),
        ProviderCapability(
            id="finra",
            label="FINRA",
            runnable=True,
            requires_api_key=False,
            has_api_key=False,
            status="ready",
            message="FINRA OTC aggregate feed.",
        ),
        ProviderCapability(
            id="alpaca",
            label="Alpaca Basic SIP",
            runnable=has_alpaca,
            requires_api_key=True,
            has_api_key=has_alpaca,
            status="ready" if has_alpaca else "missing_api_key",
            message=(
                "Historical SIP trades are wired with Basic-compatible delayed access."
                if has_alpaca
                else "Set ALPACA_API_KEY/ALPACA_API_SECRET or ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY."
            ),
        ),
        ProviderCapability(
            id="massive",
            label="Massive Stocks",
            runnable=has_massive,
            requires_api_key=True,
            has_api_key=has_massive,
            status="ready" if has_massive else "missing_api_key",
            message=(
                "Stocks trades endpoint is wired; entitlement may be EOD, delayed, or real-time by plan."
                if has_massive
                else "Set MASSIVE_API_KEY. POLYGON_API_KEY is also accepted for legacy Massive/Polygon keys."
            ),
        ),
        ProviderCapability(
            id="tradier",
            label="Tradier Time & Sales",
            runnable=has_tradier,
            requires_api_key=True,
            has_api_key=has_tradier,
            status="ready" if has_tradier else "missing_api_key",
            message=(
                "Tick time-and-sales is wired for Tradier Brokerage access tokens."
                if has_tradier
                else "Set TRADIER_ACCESS_TOKEN from a Tradier Brokerage account."
            ),
        ),
        ProviderCapability(
            id="polygon",
            label="Polygon",
            runnable=False,
            requires_api_key=True,
            has_api_key=bool(os.getenv("POLYGON_API_KEY")),
            status="not_implemented",
            message="API key can be configured, but live execution is not wired yet.",
        ),
        ProviderCapability(
            id="intrinio",
            label="Intrinio",
            runnable=False,
            requires_api_key=True,
            has_api_key=bool(os.getenv("INTRINIO_API_KEY")),
            status="not_implemented",
            message="API key can be configured, but live execution is not wired yet.",
        ),
    ]


def get_provider_capability(provider: str) -> ProviderCapability | None:
    provider_key = provider.lower()
    return next((capability for capability in list_provider_capabilities() if capability.id == provider_key), None)


async def fetch_provider_result(symbol: str | None, provider: str = "finra", limit: int = 200) -> ProviderResult:
    provider = provider.lower()
    capability = get_provider_capability(provider)
    if capability and not capability.runnable:
        raise ProviderError(f"Provider {provider} is not available for execution: {capability.message}")

    if provider == "demo":
        raise ProviderError("Demo provider has been removed from runtime trade-intent workflows")

    if provider == "finra":
        try:
            from finra_helper import aget_full_data

            raw_records = await aget_full_data(symbol.upper() if symbol else None, "T1", True)
        except Exception as exc:
            raise ProviderError(f"FINRA unavailable and demo fallback has been removed: {exc}") from exc

        prints: list[DarkpoolPrint] = []
        for idx, record in enumerate(raw_records[:limit]):
            sym = record.get("issueSymbolIdentifier") or record.get("symbol") or symbol
            if not sym:
                continue
            stock = get_stock(sym)
            shares = int(record.get("totalWeeklyShareQuantity") or record.get("share_quantity") or 0)
            price = float(stock.get("basePrice", 100.0))
            timestamp = record.get("lastUpdateDate") or record.get("weekStartDate")
            from datetime import datetime, timezone

            try:
                parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")) if timestamp else datetime.now(timezone.utc)
            except ValueError:
                parsed = datetime.now(timezone.utc)
            prints.append(
                DarkpoolPrint(
                    id=f"finra-{sym}-{idx}",
                    symbol=str(sym).upper(),
                    price=price,
                    size=shares,
                    direction="NEUTRAL",
                    venue="FINRA",
                    timestamp=parsed,
                )
            )
        return ProviderResult(provider="finra", records=raw_records[:limit], prints=prints)

    if provider == "alpaca":
        return await _fetch_alpaca_trades(symbol, limit)

    if provider == "massive":
        return await _fetch_massive_trades(symbol, limit)

    if provider == "tradier":
        return await _fetch_tradier_timesales(symbol, limit)

    raise ProviderError(f"Unsupported provider: {provider}")


def _has_alpaca_credentials() -> bool:
    key, secret = _alpaca_credentials()
    return bool(key and secret)


def _alpaca_credentials() -> tuple[str, str]:
    key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_API_KEY") or ""
    secret = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET") or ""
    return key, secret


def _massive_api_key() -> str:
    return os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY") or ""


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000_000_000:
            numeric /= 1_000_000_000
        elif numeric > 10_000_000_000_000:
            numeric /= 1_000_000
        elif numeric > 10_000_000_000:
            numeric /= 1_000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return _parse_timestamp(float(value))
            except ValueError:
                pass
    return datetime.now(timezone.utc)


def _limited(limit: int, maximum: int) -> int:
    return max(1, min(limit, maximum))


def _normalize_symbol(symbol: str | None) -> str:
    if not symbol:
        raise ProviderError("A symbol is required for trade-level providers")
    return symbol.upper()


def _json_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return getattr(response, "text", "")[:200]
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            if payload.get(key):
                return str(payload[key])
    return str(payload)[:200]


def _raise_for_provider_http_error(response: httpx.Response, provider_label: str) -> None:
    status_code = getattr(response, "status_code", 200)
    if status_code < 400:
        return
    detail = _json_error_detail(response)
    suffix = f": {detail}" if detail else ""
    raise ProviderError(f"{provider_label} request failed with HTTP {status_code}{suffix}")


def _neutral_print(
    *,
    id_: str,
    symbol: str,
    price: object,
    size: object,
    venue: str,
    timestamp: object,
) -> DarkpoolPrint:
    return DarkpoolPrint(
        id=id_,
        symbol=symbol.upper(),
        price=float(price),
        size=int(float(size or 0)),
        direction="NEUTRAL",
        venue=venue,
        timestamp=_parse_timestamp(timestamp),
    )


async def _fetch_alpaca_trades(symbol: str | None, limit: int) -> ProviderResult:
    symbol_key = _normalize_symbol(symbol)
    key, secret = _alpaca_credentials()
    if not key or not secret:
        raise ProviderError("Alpaca provider requires ALPACA_API_KEY/ALPACA_API_SECRET credentials")

    now = datetime.now(timezone.utc)
    end = now - timedelta(minutes=16)
    start = end - timedelta(minutes=60)
    params = {
        "symbols": symbol_key,
        "feed": "sip",
        "limit": _limited(limit, 10_000),
        "sort": "desc",
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
    }
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get("https://data.alpaca.markets/v2/stocks/trades", params=params, headers=headers)
    _raise_for_provider_http_error(response, "Alpaca")
    payload = response.json()
    trades = payload.get("trades", {}).get(symbol_key, [])

    records: list[dict] = []
    prints: list[DarkpoolPrint] = []
    for idx, trade in enumerate(trades[:limit]):
        trade_id = str(trade.get("i") or f"alpaca-{symbol_key}-{idx}")
        venue = f"ALPACA:SIP:{trade.get('x') or 'UNKNOWN'}"
        record = dict(trade)
        record.update(
            {
                "symbol": symbol_key,
                "provider": "alpaca",
                "record_type": "equity_trade",
                "access_latency": "sip_15_minute_delayed_or_older",
                "trade_direction_certainty": "unknown",
                "venue": venue,
            }
        )
        records.append(record)
        prints.append(
            _neutral_print(
                id_=trade_id,
                symbol=symbol_key,
                price=trade.get("p"),
                size=trade.get("s"),
                venue=venue,
                timestamp=trade.get("t"),
            )
        )
    return ProviderResult(provider="alpaca", records=records, prints=prints)


async def _fetch_massive_trades(symbol: str | None, limit: int) -> ProviderResult:
    symbol_key = _normalize_symbol(symbol)
    api_key = _massive_api_key()
    if not api_key:
        raise ProviderError("Massive provider requires MASSIVE_API_KEY")

    params = {
        "apiKey": api_key,
        "limit": _limited(limit, 50_000),
        "order": "desc",
        "sort": "timestamp",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"https://api.massive.com/v3/trades/{symbol_key}", params=params)
    _raise_for_provider_http_error(response, "Massive")
    payload = response.json()
    trades = payload.get("results", [])

    records: list[dict] = []
    prints: list[DarkpoolPrint] = []
    for idx, trade in enumerate(trades[:limit]):
        trf_id = trade.get("trf_id")
        exchange = trade.get("exchange")
        venue = f"MASSIVE:TRF:{trf_id}" if trf_id is not None else f"MASSIVE:EXCHANGE:{exchange or 'UNKNOWN'}"
        record = dict(trade)
        record.update(
            {
                "symbol": symbol_key,
                "provider": "massive",
                "record_type": "equity_trade",
                "access_latency": "end_of_day_delayed_or_entitlement_dependent",
                "off_exchange_inference": "trf_reported" if trf_id is not None else "not_trf_reported",
                "trade_direction_certainty": "unknown",
                "venue": venue,
            }
        )
        records.append(record)
        prints.append(
            _neutral_print(
                id_=str(trade.get("id") or f"massive-{symbol_key}-{idx}"),
                symbol=symbol_key,
                price=trade.get("price"),
                size=trade.get("size") or trade.get("decimal_size"),
                venue=venue,
                timestamp=trade.get("sip_timestamp") or trade.get("participant_timestamp") or trade.get("trf_timestamp"),
            )
        )
    return ProviderResult(provider="massive", records=records, prints=prints)


async def _fetch_tradier_timesales(symbol: str | None, limit: int) -> ProviderResult:
    symbol_key = _normalize_symbol(symbol)
    token = os.getenv("TRADIER_ACCESS_TOKEN", "")
    if not token:
        raise ProviderError("Tradier provider requires TRADIER_ACCESS_TOKEN")

    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=15)
    params = {
        "symbol": symbol_key,
        "interval": "tick",
        "session_filter": "all",
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "end": now.strftime("%Y-%m-%d %H:%M"),
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get("https://api.tradier.com/v1/markets/timesales", params=params, headers=headers)
    _raise_for_provider_http_error(response, "Tradier")
    payload = response.json()
    series = payload.get("series") or {}
    trades = series.get("data") or []
    if isinstance(trades, dict):
        trades = [trades]

    records: list[dict] = []
    prints: list[DarkpoolPrint] = []
    for idx, trade in enumerate(trades[:limit]):
        timestamp = trade.get("time") or trade.get("timestamp")
        record = dict(trade)
        record.update(
            {
                "symbol": symbol_key,
                "provider": "tradier",
                "record_type": "equity_time_and_sales",
                "access_latency": "real_time_for_brokerage_accounts_or_delayed_sandbox",
                "trade_direction_certainty": "unknown",
                "venue": "TRADIER:CONSOLIDATED",
            }
        )
        records.append(record)
        prints.append(
            _neutral_print(
                id_=str(trade.get("id") or f"tradier-{symbol_key}-{idx}"),
                symbol=symbol_key,
                price=trade.get("price"),
                size=trade.get("volume") or trade.get("size"),
                venue="TRADIER:CONSOLIDATED",
                timestamp=timestamp,
            )
        )
    return ProviderResult(provider="tradier", records=records, prints=prints)
