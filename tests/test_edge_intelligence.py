from datetime import datetime, timezone

import pytest
from fastapi import FastAPI

from edge_intelligence import EdgeIntelligencePublisher, FlarePublishRequest, register


def test_builds_bounded_bullish_dark_pool_intelligence():
    publisher = EdgeIntelligencePublisher()
    document = publisher.build(
        FlarePublishRequest(
            symbol="nvda",
            evidence={"buy_volume": 900_000, "sell_volume": 100_000, "zscore": 2.8, "adv_pct": 3.0},
        )
    )

    assert document["contract_version"] == "flare.intelligence.v1"
    assert document["source_bot"] == "sentinel-flare"
    assert document["symbol"] == "NVDA"
    assert document["direction"] == "bullish"
    assert 0 <= document["confidence"] <= 1
    assert 0 <= document["strength"] <= 1
    assert document["intelligence_id"].startswith("flare-intel:")


def test_rejects_execution_fields_in_evidence():
    publisher = EdgeIntelligencePublisher()
    with pytest.raises(ValueError, match="execution fields"):
        publisher.build(FlarePublishRequest(symbol="AAPL", evidence={"quantity": 10, "buy_volume": 100}))


def test_registers_bridge_routes_once():
    app = FastAPI()
    register(app)
    register(app)
    paths = set(app.openapi()["paths"])

    assert "/integration/edge/intelligence/preview" in paths
    assert "/integration/edge/intelligence/publish" in paths
    assert "/integration/edge/status" in paths


def test_requires_timezone_on_explicit_observation():
    publisher = EdgeIntelligencePublisher()
    with pytest.raises(ValueError, match="timezone"):
        publisher.build(FlarePublishRequest(symbol="MSFT", observed_at=datetime(2026, 1, 2), evidence={}))
