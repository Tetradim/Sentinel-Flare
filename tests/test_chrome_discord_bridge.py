from fastapi.testclient import TestClient


EVENT_BUS_SECRET = "test-event-secret-for-darkpool-bus-32chars"
EVENT_BUS_HEADER = {"X-Darkpool-Event-Bus-Secret": EVENT_BUS_SECRET}


def test_chrome_bridge_message_publishes_signal_observed(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))
    monkeypatch.setenv("DARKPOOL_EVENT_BUS_SECRET", EVENT_BUS_SECRET)

    import server

    client = TestClient(server.app)
    response = client.post(
        "/api/discord/chrome-bridge/message",
        json={
            "event_id": "darkpool-chrome-1",
            "channel_id": "123",
            "channel_name": "mike-alerts",
            "channel_url": "https://discord.com/channels/1/123",
            "bridge_target_id": "sentinel-flare",
            "bridge_target_name": "Sentinel Flare",
            "author_name": "MikeInvesting [MIKE]",
            "content": "$SPY\n$744 PUTS\nEXPIRATION 6/22/2026\n$.4 Entry\n@everyone alert",
            "observed_at": "2026-06-22T14:23:00+00:00",
        },
    )
    events = client.get("/api/bus/events?target=sentinel-flare", headers=EVENT_BUS_HEADER).json()["events"]

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    assert events[0]["event_type"] == "signal.observed"
    assert events[0]["source"] == "chrome-discord-bridge"
    assert events[0]["payload"]["contract_version"] == "chrome.discord.message.v1"
    assert events[0]["payload"]["bridge_target_id"] == "sentinel-flare"


def test_chrome_bridge_heartbeat_records_health(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_EVENT_BUS_DIR", str(tmp_path))
    monkeypatch.setenv("DARKPOOL_EVENT_BUS_SECRET", EVENT_BUS_SECRET)

    import server

    client = TestClient(server.app)
    response = client.post(
        "/api/discord/chrome-bridge/heartbeat",
        json={
            "status": "ok",
            "bridge_enabled": True,
            "channel_id": "123",
            "channel_url": "https://discord.com/channels/1/123",
            "bridge_target_id": "sentinel-flare",
            "observed_at": "2026-06-22T14:23:30+00:00",
        },
    )
    health = client.get("/api/discord/chrome-bridge/health").json()
    events = client.get("/api/bus/events?target=sentinel-flare", headers=EVENT_BUS_HEADER).json()["events"]

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "healthy"
    assert health["healthy"] is True
    assert events[0]["event_type"] == "bridge.health"
    assert events[0]["payload"]["bridge_target_id"] == "sentinel-flare"
