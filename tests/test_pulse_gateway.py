import pytest


def test_pulse_gateway_rejects_order_like_packet():
    from darkpool.pulse_gateway import PulseGateway, PulsePacketRejected

    gateway = PulseGateway()

    with pytest.raises(PulsePacketRejected, match="manual execution"):
        gateway.validate_packet({"packet_type": "order", "requires_manual_execution": False})


def test_pulse_gateway_accepts_manual_trade_intent_packet():
    from darkpool.pulse_gateway import PulseGateway

    gateway = PulseGateway()
    packet = gateway.validate_packet(
        {
            "packet_type": "trade_intent",
            "destination": "sentinel-edge",
            "final_target": "sentinel-pulse",
            "edge_review_required": True,
            "requires_manual_execution": True,
        }
    )

    assert packet["packet_type"] == "trade_intent"
    assert packet["destination"] == "sentinel-edge"
    assert packet["requires_manual_execution"] is True


def test_pulse_gateway_rejects_direct_pulse_destination():
    from darkpool.pulse_gateway import PulseGateway, PulsePacketRejected

    gateway = PulseGateway()

    with pytest.raises(PulsePacketRejected, match="Sentinel Edge"):
        gateway.validate_packet(
            {
                "packet_type": "trade_intent",
                "destination": "pulse",
                "requires_manual_execution": True,
            }
        )


def test_pulse_gateway_rejects_live_order_keys():
    from darkpool.pulse_gateway import PulseGateway, PulsePacketRejected

    gateway = PulseGateway()

    with pytest.raises(PulsePacketRejected, match="forbidden live-order keys"):
        gateway.validate_packet(
            {
                "packet_type": "trade_intent",
                "destination": "sentinel-edge",
                "final_target": "sentinel-pulse",
                "edge_review_required": True,
                "requires_manual_execution": True,
                "place_order": True,
            }
        )


def test_pulse_gateway_rejects_nested_live_order_keys():
    from darkpool.pulse_gateway import PulseGateway, PulsePacketRejected

    gateway = PulseGateway()

    with pytest.raises(PulsePacketRejected, match="forbidden live-order keys"):
        gateway.validate_packet(
            {
                "packet_type": "trade_intent",
                "destination": "sentinel-edge",
                "final_target": "sentinel-pulse",
                "edge_review_required": True,
                "requires_manual_execution": True,
                "risk_plan": {
                    "planned_action": "BUY",
                    "broker_order_id": "broker-live-1",
                },
            }
        )


def test_pulse_gateway_rejects_camel_case_live_order_and_secret_keys():
    from darkpool.pulse_gateway import PulseGateway, PulsePacketRejected

    gateway = PulseGateway()

    with pytest.raises(PulsePacketRejected, match="forbidden live-order keys"):
        gateway.validate_packet(
            {
                "packet_type": "trade_intent",
                "destination": "sentinel-edge",
                "final_target": "sentinel-pulse",
                "edge_review_required": True,
                "requires_manual_execution": True,
                "metadata": {
                    "brokerOrderId": "broker-live-1",
                    "credentials": {
                        "apiKey": "secret-key",
                    },
                },
            }
        )
