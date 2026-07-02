from fastapi.testclient import TestClient
from types import SimpleNamespace

from darkpool.trade_pipeline import PulseStatus


EVENT_BUS_SECRET = "test-event-secret-for-darkpool-bus-32chars"
EVENT_BUS_HEADER = {"X-Darkpool-Event-Bus-Secret": EVENT_BUS_SECRET}


class _Dumpable:
    def __init__(self, **data):
        self.__dict__.update(data)

    def model_dump(self, *args, **kwargs):
        return dict(self.__dict__)


def test_darkpool_event_store_publishes_and_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))

    from darkpool.bot_event_bus import EventBusStore, publish_event

    publish_event("darkpool.trade_intent.prepared", {"symbol": "AAPL"}, target="sentinel-edge")
    publish_event("darkpool.operator.note", {"symbol": "MSFT"}, target="sentinel-flare")

    events = EventBusStore().list_events(limit=10, target="sentinel-edge")

    assert len(events) == 1
    assert events[0]["schema_version"] == "bot-event.v1"
    assert events[0]["source"] == "sentinel-flare"
    assert events[0]["payload"] == {"symbol": "AAPL"}


def test_darkpool_event_store_dedupes_signal_observations_by_idempotency_key(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))

    from darkpool.bot_event_bus import EventBusStore, publish_event

    payload = {
        "symbol": "AAPL",
        "signal_observation": {
            "contract_version": "signal_observation.v1",
            "idempotency_key": "sentinel-flare:AAPL:trade_intent:intent-1",
        },
    }

    first = publish_event("darkpool.trade_intent.reviewed", payload, target="sentinel-edge")
    second = publish_event("darkpool.trade_intent.reviewed", payload, target="sentinel-edge")
    events = EventBusStore().list_events(limit=10, target="sentinel-edge")

    assert first["event_id"] == second["event_id"]
    assert second["duplicate"] is True
    assert len(events) == 1
    assert events[0]["payload"]["signal_observation"]["idempotency_key"] == "sentinel-flare:AAPL:trade_intent:intent-1"


def test_darkpool_bus_routes_are_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))
    monkeypatch.setenv("DARKPOOL_EVENT_BUS_SECRET", EVENT_BUS_SECRET)

    import server

    client = TestClient(server.app)
    response = client.post(
        "/api/bus/events",
        headers=EVENT_BUS_HEADER,
        json={
            "event_type": "edge.action",
            "source": "sentinel-edge",
            "target": "sentinel-flare",
            "payload": {"action": "stand_down"},
        },
    )

    assert response.status_code == 200, response.text
    event = response.json()["event"]
    assert event["target"] == "sentinel-flare"
    assert event["payload"]["action"] == "stand_down"

    response = client.get("/api/bus/events?target=sentinel-flare", headers=EVENT_BUS_HEADER)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1


def test_darkpool_bus_routes_fail_closed_without_configured_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))
    monkeypatch.delenv("DARKPOOL_EVENT_BUS_SECRET", raising=False)

    import server

    client = TestClient(server.app)
    response = client.post(
        "/api/bus/events",
        headers=EVENT_BUS_HEADER,
        json={
            "event_type": "edge.action",
            "source": "sentinel-edge",
            "target": "sentinel-flare",
            "payload": {"action": "stand_down"},
        },
    )

    assert response.status_code == 503
    assert "DARKPOOL_EVENT_BUS_SECRET" in response.text

    response = client.get("/api/bus/events?target=sentinel-flare", headers=EVENT_BUS_HEADER)
    assert response.status_code == 503
    assert "DARKPOOL_EVENT_BUS_SECRET" in response.text


def test_darkpool_bus_routes_fail_closed_with_weak_configured_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))
    monkeypatch.setenv("DARKPOOL_EVENT_BUS_SECRET", "test-event-secret")

    import server

    client = TestClient(server.app)
    response = client.post(
        "/api/bus/events",
        headers={"X-Darkpool-Event-Bus-Secret": "test-event-secret"},
        json={
            "event_type": "edge.action",
            "source": "sentinel-edge",
            "target": "sentinel-flare",
            "payload": {"action": "stand_down"},
        },
    )

    assert response.status_code == 503
    assert "DARKPOOL_EVENT_BUS_SECRET" in response.text
    assert "at least 32 characters" in response.text


def test_darkpool_bus_routes_require_matching_secret_header(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))
    monkeypatch.setenv("DARKPOOL_EVENT_BUS_SECRET", EVENT_BUS_SECRET)

    import server

    client = TestClient(server.app)
    request_body = {
        "event_type": "edge.action",
        "source": "sentinel-edge",
        "target": "sentinel-flare",
        "payload": {"action": "stand_down"},
    }

    missing = client.post("/api/bus/events", json=request_body)
    wrong = client.post(
        "/api/bus/events",
        headers={"X-Darkpool-Event-Bus-Secret": "wrong-secret"},
        json=request_body,
    )
    accepted = client.post("/api/bus/events", headers=EVENT_BUS_HEADER, json=request_body)

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200, accepted.text

    unauthenticated_read = client.get("/api/bus/events?target=sentinel-flare")
    authenticated_read = client.get("/api/bus/events?target=sentinel-flare", headers=EVENT_BUS_HEADER)

    assert unauthenticated_read.status_code == 401
    assert authenticated_read.status_code == 200, authenticated_read.text
    assert authenticated_read.json()["count"] == 1


def test_trade_intent_route_publishes_intel_event(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))
    monkeypatch.setenv("DARKPOOL_EVENT_BUS_SECRET", EVENT_BUS_SECRET)

    import server

    client = TestClient(server.app)
    response = client.get("/darkpool/trade-intent?symbol=AAPL&provider=demo")
    assert response.status_code == 200, response.text

    events = client.get("/api/bus/events?target=sentinel-edge", headers=EVENT_BUS_HEADER).json()["events"]
    assert events
    assert events[0]["event_type"] == "darkpool.trade_intent.reviewed"
    assert events[0]["payload"]["symbol"] == "AAPL"
    assert events[0]["target"] == "sentinel-edge"


def test_trade_intent_event_payload_embeds_signal_observation_contract():
    from darkpool.bot_event_bus import build_trade_intent_event_payload

    intent = _Dumpable(
        id="intent-aapl-1",
        action="BUY",
        confidence=91.2,
        source_adjusted_confidence=68.4,
        created_at="2026-06-23T20:54:00+00:00",
    )
    score = _Dumpable(direction="BULLISH", score=91.2, reasons=["dark pool cluster strength"])
    context = SimpleNamespace(
        symbol="AAPL",
        provider_result=SimpleNamespace(provider="demo", degraded=False, message="ok"),
        scores=[score],
        confirmation_plan=_Dumpable(required_coverage_complete=True),
    )
    report = SimpleNamespace(
        context=context,
        intent=intent,
        sentinel=_Dumpable(status="approved"),
        pulse_packet=None,
        pulse_status=PulseStatus(
            status="not_requested",
            message="Pulse communication was not requested for this review.",
            reasons=["include_pulse_packet=false"],
            requires_manual_execution=True,
            sentinel_status="approved",
        ),
    )

    payload = build_trade_intent_event_payload(report)
    observation = payload["signal_observation"]

    assert observation["contract_version"] == "signal_observation.v1"
    assert observation["source_bot"] == "sentinel-flare"
    assert observation["symbol"] == "AAPL"
    assert observation["asset_class"] == "equity"
    assert observation["signal_type"] == "darkpool_trade_intent"
    assert observation["direction"] == "bullish"
    assert observation["confidence"] == 68.4
    assert observation["idempotency_key"] == "sentinel-flare:AAPL:trade_intent:intent-aapl-1"
    assert observation["payload"]["provider"] == "demo"
    assert observation["payload"]["intent_id"] == "intent-aapl-1"
    assert observation["payload"]["pulse_status"]["requires_manual_execution"] is True
