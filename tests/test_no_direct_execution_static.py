"""Static guardrails for Sentinel Flare's signal-only role."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PRODUCTION_PATHS = [
    ROOT / "darkpool",
    ROOT / "routes",
    ROOT / "server.py",
    ROOT / "discord_bot.py",
]

DIRECT_PULSE_CALL_TOKENS = {
    "PULSE_API_URL",
    "PULSE_BASE_URL",
    "SENTINEL_PULSE_URL",
    "/api/edge/handoff",
    "/api/orders",
    "/api/orders/",
    "/api/trades",
    "/api/trades/",
    "/api/positions",
    "/api/positions/",
}

BROKER_EXECUTION_TOKENS = {
    "alpaca_trade_api",
    "alpaca.trading",
    "ib_insync",
    "tradier",
    ".submit_order(",
    ".place_order(",
    ".market_order(",
    ".limit_order(",
    ".replace_order(",
    ".cancel_order(",
}

ALLOWED_REVIEW_PACKET_FILES = {
    "darkpool/bot_event_bus.py",
    "darkpool/bot_event_bus_routes.py",
    "darkpool/pulse_gateway.py",
    "darkpool/source_catalog.py",
    "darkpool/trade_intent.py",
    "darkpool/trade_pipeline.py",
    "routes/darkpool_routes.py",
}


def _production_files():
    for path in PRODUCTION_PATHS:
        if path.is_file():
            yield path
            continue
        for child in path.rglob("*.py"):
            parts = set(child.relative_to(ROOT).parts)
            if "__pycache__" in parts:
                continue
            yield child


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_darkpool_does_not_call_pulse_or_broker_execution_apis_directly():
    offenders = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        for token in DIRECT_PULSE_CALL_TOKENS | BROKER_EXECUTION_TOKENS:
            if token in text:
                offenders.append(f"{_relative(path)}:{token}")

    assert offenders == []


def test_pulse_named_production_code_is_limited_to_edge_review_packet_boundary_files():
    offenders = []
    for path in _production_files():
        relative = _relative(path)
        if relative in ALLOWED_REVIEW_PACKET_FILES:
            continue
        text = path.read_text(encoding="utf-8").lower()
        if "pulse" in text:
            offenders.append(relative)

    assert offenders == []
