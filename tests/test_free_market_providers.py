from __future__ import annotations

from dataclasses import dataclass

import pytest

from darkpool.providers import ProviderError, fetch_provider_result


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP status {self.status_code}")


class _FakeAsyncClient:
    calls: list[dict] = []
    payload: dict = {}
    status_code: int = 200

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse(self.status_code, self.payload)


@pytest.fixture(autouse=True)
def _reset_fake_client():
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.payload = {}
    _FakeAsyncClient.status_code = 200


@pytest.mark.asyncio
async def test_alpaca_provider_fetches_delayed_sip_trades(monkeypatch):
    import darkpool.providers as providers

    monkeypatch.setenv("ALPACA_API_KEY", "alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "alpaca-secret")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.payload = {
        "trades": {
            "AAPL": [
                {
                    "i": "alpaca-trade-1",
                    "p": 187.5,
                    "s": 120000,
                    "t": "2026-07-30T14:45:00Z",
                    "x": "D",
                    "c": ["@"],
                    "z": "C",
                }
            ]
        }
    }

    result = await fetch_provider_result("AAPL", provider="alpaca", limit=1)

    assert result.provider == "alpaca"
    assert result.prints[0].id == "alpaca-trade-1"
    assert result.prints[0].symbol == "AAPL"
    assert result.prints[0].price == 187.5
    assert result.prints[0].size == 120000
    assert result.prints[0].direction == "NEUTRAL"
    assert result.records[0]["access_latency"] == "sip_15_minute_delayed_or_older"
    assert result.records[0]["trade_direction_certainty"] == "unknown"

    call = _FakeAsyncClient.calls[0]
    assert call["url"] == "https://data.alpaca.markets/v2/stocks/trades"
    assert call["headers"]["APCA-API-KEY-ID"] == "alpaca-key"
    assert call["headers"]["APCA-API-SECRET-KEY"] == "alpaca-secret"
    assert call["params"]["symbols"] == "AAPL"
    assert call["params"]["feed"] == "sip"


@pytest.mark.asyncio
async def test_massive_provider_preserves_trf_fields(monkeypatch):
    import darkpool.providers as providers

    monkeypatch.setenv("MASSIVE_API_KEY", "massive-key")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.payload = {
        "status": "OK",
        "results": [
            {
                "id": "massive-trade-1",
                "price": 101.25,
                "size": 250000,
                "sip_timestamp": 1785437100000000000,
                "exchange": 4,
                "trf_id": 12,
                "trf_timestamp": 1785437100001000000,
            }
        ],
    }

    result = await fetch_provider_result("AAPL", provider="massive", limit=1)

    assert result.provider == "massive"
    assert result.prints[0].venue == "MASSIVE:TRF:12"
    assert result.prints[0].direction == "NEUTRAL"
    assert result.records[0]["off_exchange_inference"] == "trf_reported"
    assert result.records[0]["trade_direction_certainty"] == "unknown"

    call = _FakeAsyncClient.calls[0]
    assert call["url"] == "https://api.massive.com/v3/trades/AAPL"
    assert call["params"]["apiKey"] == "massive-key"
    assert call["params"]["limit"] == 1


@pytest.mark.asyncio
async def test_tradier_provider_fetches_tick_time_and_sales(monkeypatch):
    import darkpool.providers as providers

    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "tradier-token")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.payload = {
        "series": {
            "data": [
                {
                    "time": "2026-07-30T14:45:00Z",
                    "timestamp": 1785437100,
                    "price": 410.12,
                    "volume": 50000,
                }
            ]
        }
    }

    result = await fetch_provider_result("MSFT", provider="tradier", limit=1)

    assert result.provider == "tradier"
    assert result.prints[0].symbol == "MSFT"
    assert result.prints[0].venue == "TRADIER:CONSOLIDATED"
    assert result.records[0]["access_latency"] == "real_time_for_brokerage_accounts_or_delayed_sandbox"

    call = _FakeAsyncClient.calls[0]
    assert call["url"] == "https://api.tradier.com/v1/markets/timesales"
    assert call["headers"]["Authorization"] == "Bearer tradier-token"
    assert call["params"]["symbol"] == "MSFT"
    assert call["params"]["interval"] == "tick"


@pytest.mark.asyncio
async def test_provider_http_entitlement_failures_raise_provider_error(monkeypatch):
    import darkpool.providers as providers

    monkeypatch.setenv("MASSIVE_API_KEY", "massive-key")
    monkeypatch.setattr(providers.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.status_code = 403
    _FakeAsyncClient.payload = {"error": "plan does not include endpoint"}

    with pytest.raises(ProviderError, match="Massive request failed with HTTP 403"):
        await fetch_provider_result("AAPL", provider="massive", limit=1)
