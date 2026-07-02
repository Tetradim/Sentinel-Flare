"""Pulse packet validation and adapter boundary."""

from __future__ import annotations


class PulsePacketRejected(ValueError):
    pass


def _normalized_packet_key(key: object) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


def _find_forbidden_live_order_keys(value: object, forbidden: set[str], path: str = "") -> list[str]:
    if isinstance(value, dict):
        matches = []
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if _normalized_packet_key(key_text) in forbidden:
                matches.append(child_path)
            matches.extend(_find_forbidden_live_order_keys(item, forbidden, child_path))
        return matches
    if isinstance(value, list):
        matches = []
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]" if path else f"[{index}]"
            matches.extend(_find_forbidden_live_order_keys(item, forbidden, child_path))
        return matches
    return []


class PulseGateway:
    allowed_packet_types = {"trade_intent"}
    forbidden_live_order_keys = {
        "accountid",
        "accountnumber",
        "accesstoken",
        "apikey",
        "apisecret",
        "bearertoken",
        "brokeraccountid",
        "brokerid",
        "brokerids",
        "brokerorderid",
        "credential",
        "credentials",
        "executeorder",
        "limitprice",
        "liveorder",
        "orderid",
        "ordertype",
        "password",
        "placeorder",
        "refreshtoken",
        "secret",
        "secretkey",
        "stoplimitprice",
        "timeinforce",
        "token",
    }

    def validate_packet(self, packet: dict) -> dict:
        if packet.get("packet_type") not in self.allowed_packet_types:
            raise PulsePacketRejected("Pulse packet must be a manual execution trade_intent packet")
        if packet.get("destination") != "sentinel-edge":
            raise PulsePacketRejected("Darkpool packets must be routed to Sentinel Edge before Pulse")
        if packet.get("final_target") != "sentinel-pulse":
            raise PulsePacketRejected("Pulse packet final target must be sentinel-pulse")
        if packet.get("edge_review_required") is not True:
            raise PulsePacketRejected("Pulse packet must require Sentinel Edge review")
        if packet.get("requires_manual_execution") is not True:
            raise PulsePacketRejected("Pulse packet must require manual execution")

        present = _find_forbidden_live_order_keys(packet, self.forbidden_live_order_keys)
        if present:
            raise PulsePacketRejected(f"Pulse packet contains forbidden live-order keys: {sorted(present)}")
        return packet
