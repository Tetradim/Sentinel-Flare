"""Sentinel Flare Backend - Python server with FINRA API integration."""

import os
import asyncio
import logging
import secrets
import hashlib
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from enum import Enum

from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, EmailStr
from collections import defaultdict

from darkpool.command_service import (
    build_alerts_summary,
    build_confluence_summary,
    build_darkpool_summary,
    build_levels_summary,
    build_watchlist_summary,
)
from darkpool.discord_security import DiscordSignatureVerifier, SignatureVerificationError
from darkpool.discord_formatting import summary_to_embed
from darkpool.fixtures import MAG7_STOCKS, generateTransaction
from darkpool.providers import ProviderError, fetch_provider_result, list_provider_capabilities
from darkpool.subscriptions import SubscriptionStore
from darkpool.bot_event_bus_routes import router as bot_event_bus_router
from darkpool.chrome_bridge_routes import router as chrome_bridge_router
from routes.darkpool_routes import router as darkpool_router

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    broadcast_task = asyncio.create_task(broadcast_mock_transactions())
    try:
        yield
    finally:
        broadcast_task.cancel()
        with suppress(asyncio.CancelledError):
            await broadcast_task


app = FastAPI(
    title="Sentinel Flare API",
    description="Backend for real-time Sentinel Flare monitoring with FINRA OTC data",
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(bot_event_bus_router)
app.include_router(chrome_bridge_router)
app.include_router(darkpool_router)

subscription_store = SubscriptionStore()

# ============================================================================
# Authentication & Security
# ============================================================================

SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

users_db: dict = {}
api_keys_db: dict = {}
sessions_db: dict = {}


class User(BaseModel):
    id: str
    username: str
    email: EmailStr
    hashed_password: str
    is_active: bool = True
    is_admin: bool = False
    created_at: str = ""


class Token(BaseModel):
    access_token: str
    token_type: str


class APIKeyCreate(BaseModel):
    name: str
    provider: str
    key_value: str


def hash_password(password: str) -> str:
    salt = SECRET_KEY[:16].encode()
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000).hex()


def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed


def create_access_token(data: dict) -> str:
    import jwt
    to_encode = data.copy()
    expire = datetime.now() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire.isoformat()})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    import jwt
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None


@app.post("/auth/register")
async def register(username: str = Query(...), password: str = Query(...), email: EmailStr = Query(...)):
    if username in users_db:
        raise HTTPException(400, "Username already exists")
    
    user_id = secrets.token_urlsafe(8)
    user = User(
        id=user_id,
        username=username,
        email=email,
        hashed_password=hash_password(password),
        created_at=datetime.now().isoformat(),
    )
    users_db[username] = user.model_dump()
    return {"user_id": user_id, "username": username}


@app.post("/auth/token")
async def login(username: str = Query(...), password: str = Query(...)):
    user = users_db.get(username)
    
    if not user or not verify_password(password, user["hashed_password"]):
        raise HTTPException(401, "Invalid credentials")
    
    if not user["is_active"]:
        raise HTTPException(403, "User inactive")
    
    token = create_access_token({"sub": username, "user_id": user["id"]})
    sessions_db[token] = {"username": username, "created_at": datetime.now().isoformat()}
    return Token(access_token=token, token_type="bearer")


@app.post("/auth/api-keys")
async def create_api_key(key: APIKeyCreate, token: str = Query(...)):
    user_data = decode_token(token)
    if not user_data:
        raise HTTPException(401, "Invalid token")
    
    key_id = secrets.token_urlsafe(12)
    masked = f"{key.key_value[:4]}...{key.key_value[-4:]}"
    
    api_keys_db[key_id] = {
        "id": key_id,
        "name": key.name,
        "provider": key.provider,
        "key_masked": masked,
        "status": "active",
        "user_id": user_data.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }
    return {"key_id": key_id, "masked": masked}


@app.get("/auth/api-keys")
async def list_api_keys(token: str = Query(...)):
    user_data = decode_token(token)
    if not user_data:
        raise HTTPException(401, "Invalid token")
    
    user_keys = [k for k in api_keys_db.values() if k.get("user_id") == user_data.get("user_id")]
    return {"keys": user_keys}


# ============================================================================
# Circuit Breaker with Exponential Backoff
# ============================================================================

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"         # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Circuit breaker with exponential backoff."""
    
    def __init__(self, name: str, failure_threshold: int = 5, timeout: int = 30, max_backoff: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout = timeout  # seconds before first retry
        self.max_backoff = max_backoff
        self.failures = 0
        self.state = CircuitState.CLOSED
        self.last_failure: datetime | None = None
        self.backoff_seconds = min(timeout, max_backoff)
    
    def record_failure(self):
        was_open = self.state in {CircuitState.OPEN, CircuitState.HALF_OPEN}
        self.failures += 1
        self.last_failure = datetime.utcnow()
        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            if was_open:
                self.backoff_seconds = min(self.backoff_seconds * 2, self.max_backoff)
            else:
                self.backoff_seconds = min(self.timeout, self.max_backoff)
            logger.warning(f"Circuit {self.name} OPEN after {self.failures} failures")
    
    def record_success(self):
        self.failures = 0
        self.state = CircuitState.CLOSED
        self.backoff_seconds = min(self.timeout, self.max_backoff)
    
    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN and self.last_failure:
            elapsed = (datetime.utcnow() - self.last_failure).total_seconds()
            if elapsed >= self.backoff_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
        
        return self.state == CircuitState.HALF_OPEN
    
    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failures": self.failures,
            "next_retry_seconds": self.backoff_seconds if self.state == CircuitState.OPEN else 0,
        }


# Circuit breakers for each provider
CIRCUITS = {
    "finra": CircuitBreaker("finra", failure_threshold=3, timeout=30, max_backoff=60),
    "polygon": CircuitBreaker("polygon", failure_threshold=5, timeout=60, max_backoff=120),
    "intrinio": CircuitBreaker("intrinio", failure_threshold=3, timeout=60, max_backoff=120),
}


def with_circuit_break(provider_name: str):
    """Decorator for circuit breaker protection."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            circuit = CIRCUITS.get(provider_name)
            if not circuit:
                return await func(*args, **kwargs)
            
            if not circuit.can_execute():
                raise HTTPException(503, f"Provider {provider_name} circuit OPEN. Retry in {circuit.backoff_seconds}s")
            
            try:
                result = await func(*args, **kwargs)
                circuit.record_success()
                return result
            except Exception as e:
                circuit.record_failure()
                raise
        
        return wrapper
    return decorator


@app.get("/health/circuit")
async def get_circuit_status():
    """Get circuit breaker status for all providers."""
    return {"providers": {name: c.get_status() for name, c in CIRCUITS.items()}}


@app.post("/health/circuit/{provider}/reset")
async def reset_circuit(provider: str):
    """Manually reset a circuit breaker."""
    if provider in CIRCUITS:
        CIRCUITS[provider].record_success()
        return {"status": "reset", "provider": provider}
    raise HTTPException(404, f"Provider {provider} not found")


# ============================================================================
# Provider Abstraction
# ============================================================================

class DataProvider:
    """Base class for data providers."""

    name: str = "base"

    async def fetch_otc_data(self, symbol: str | None, tier: str, is_ats: bool):
        raise NotImplementedError


class FINRAProvider(DataProvider):
    """FINRA OTC data provider - free public data."""

    name = "finra"

    async def fetch_otc_data(self, symbol: str | None, tier: str = "T1", is_ats: bool = True):
        """Fetch OTC data from FINRA API."""
        from finra_helper import aget_full_data

        data = await aget_full_data(symbol, tier, is_ats)
        return [
            {
                "symbol": item.get("issueSymbolIdentifier"),
                "share_quantity": item.get("totalWeeklyShareQuantity"),
                "trade_quantity": item.get("totalWeeklyTradeCount"),
                "update_date": item.get("lastUpdateDate"),
                "week_start": item.get("weekStartDate"),
            }
            for item in data
        ]


class PolygonProvider(DataProvider):
    """Polygon.io provider - requires API key."""

    name = "polygon"

    def __init__(self):
        self.api_key = os.getenv("POLYGON_API_KEY", "")

    async def fetch_otc_data(self, symbol: str | None, tier: str = "T1", is_ats: bool = True):
        """Fetch dark pool trades from Polygon.io (identifies via exchange codes)."""
        import httpx

        if not self.api_key:
            raise HTTPException(401, "Polygon API key not configured")

        # Polygon uses exchange metadata to identify dark pool trades
        # Q = CBOE, K = IEX, T = NYSE, A = AMEX, B = NASDAQ, M = CHX
        # Dark pools typically: FINRA ADF, FTID, G, X, Y, Z
        url = f"https://api.polygon.io/v3/ticks/{symbol}/ Trades"

        params = {
            "timestamp": "latest",
            "limit": 5000,
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30.0,
            )

        if response.status_code == 200:
            data = response.json()
            return [
                {
                    "symbol": t.get("sym"),
                    "price": t.get("p"),
                    "size": t.get("s"),
                    "exchange": t.get("x"),
                    "timestamp": t.get("t"),
                }
                for t in data.get("results", [])
            ]
        return []


class IntrinioProvider(DataProvider):
    """Intrinio provider - requires API key."""

    name = "intrinio"

    def __init__(self):
        self.api_key = os.getenv("INTRINIO_API_KEY", "")

    async def fetch_otc_data(self, symbol: str | None, tier: str = "T1", is_ats: bool = True):
        """Fetch dark pool trades from Intrinio."""
        import httpx

        if not self.api_key:
            raise HTTPException(401, "Intrinio API key not configured")

        url = f"https://api.intrinio.com/securities/{symbol}/ trades"

        params = {"darkpool_only": True, "page_size": 5000}

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30.0,
            )

        if response.status_code == 200:
            data = response.json()
            return [
                {
                    "symbol": t.get("ticker"),
                    "price": t.get("price"),
                    "size": t.get("volume"),
                    "timestamp": t.get("last_updated"),
                }
                for t in data.get("trades", [])
            ]
        return []


class DemoProvider(DataProvider):
    """Offline deterministic provider for local demos, tests, and degraded mode."""

    name = "demo"

    async def fetch_otc_data(self, symbol: str | None, tier: str = "T1", is_ats: bool = True):
        result = await fetch_provider_result(symbol, provider="demo", limit=500)
        return [
            {
                "symbol": item.symbol,
                "price": item.price,
                "size": item.size,
                "direction": item.direction,
                "venue": item.venue,
                "timestamp": item.timestamp.isoformat(),
                "notional": item.notional,
            }
            for item in result.prints
        ]


# ============================================================================
# Provider Registry
# ============================================================================

PROVIDERS: dict[str, DataProvider] = {
    "demo": DemoProvider(),
    "finra": FINRAProvider(),
    "polygon": PolygonProvider(),
    "intrinio": IntrinioProvider(),
}


async def get_provider_records(symbol: str | None, provider: str = "demo", limit: int = 500) -> list[dict]:
    """Return provider prints in the legacy weekly-summary shape used by chart routes."""
    try:
        result = await fetch_provider_result(symbol, provider=provider, limit=limit)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    return [
        {
            "issueSymbolIdentifier": item.symbol,
            "totalWeeklyShareQuantity": item.size,
            "totalWeeklyTradeCount": 1,
            "lastUpdateDate": item.timestamp.date().isoformat(),
            "weekStartDate": item.timestamp.date().isoformat(),
            "price": item.price,
            "notional": item.notional,
        }
        for item in result.prints
    ]


# ============================================================================
# Models
# ============================================================================

class Transaction(BaseModel):
    """Individual transaction model."""

    id: str
    timestamp: str
    symbol: str
    direction: Literal["BUY", "SELL"]
    size: int  # in shares
    price: float
    value: float  # notional value


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Sentinel Flare API",
        "version": "1.0.0",
        "docs": "/docs",
        "providers": [provider.id for provider in list_provider_capabilities()],
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/providers")
async def list_providers():
    """List available providers."""
    return [
        provider.__dict__
        for provider in list_provider_capabilities()
    ]


@app.get("/api/full")
async def api_full(
    symbol: str | None = Query(None, description="Optional stock symbol"),
    provider: str = Query("demo", description="Data provider: demo or finra"),
    limit: int = Query(250, ge=1, le=1000),
):
    """Compatibility endpoint returning normalized dark pool prints."""
    try:
        result = await fetch_provider_result(symbol, provider=provider, limit=limit)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    return {
        "provider": result.provider,
        "degraded": result.degraded,
        "message": result.message,
        "count": len(result.prints),
        "transactions": [print_.model_dump(mode="json") for print_ in result.prints],
    }


@app.get("/api/aggregate/{symbol}")
async def api_aggregate(symbol: str, provider: str = Query("demo")):
    """Compatibility endpoint aggregating dark pool activity by symbol."""
    try:
        result = await fetch_provider_result(symbol, provider=provider, limit=500)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    total_size = sum(print_.size for print_ in result.prints)
    total_notional = sum(print_.notional for print_ in result.prints)
    buy_size = sum(print_.size for print_ in result.prints if print_.direction == "BUY")
    sell_size = sum(print_.size for print_ in result.prints if print_.direction == "SELL")

    return {
        "symbol": symbol.upper(),
        "provider": result.provider,
        "degraded": result.degraded,
        "print_count": len(result.prints),
        "total_size": total_size,
        "total_notional": round(total_notional, 2),
        "buy_size": buy_size,
        "sell_size": sell_size,
        "buy_ratio": round(buy_size / total_size, 4) if total_size else 0,
    }


@app.get("/api/sentiment")
async def api_sentiment(provider: str = Query("demo")):
    """Compatibility endpoint for market-wide dark pool sentiment."""
    try:
        result = await fetch_provider_result(None, provider=provider, limit=500)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))

    buy_notional = sum(print_.notional for print_ in result.prints if print_.direction == "BUY")
    sell_notional = sum(print_.notional for print_ in result.prints if print_.direction == "SELL")
    total = buy_notional + sell_notional
    buy_ratio = buy_notional / total if total else 0.5
    sentiment = "bullish" if buy_ratio >= 0.57 else "bearish" if buy_ratio <= 0.43 else "neutral"
    by_symbol: dict[str, dict] = {}
    for print_ in result.prints:
        bucket = by_symbol.setdefault(print_.symbol, {"buy": 0.0, "sell": 0.0, "notional": 0.0})
        bucket["notional"] += print_.notional
        if print_.direction == "BUY":
            bucket["buy"] += print_.notional
        elif print_.direction == "SELL":
            bucket["sell"] += print_.notional

    return {
        "provider": result.provider,
        "degraded": result.degraded,
        "sentiment": sentiment,
        "buy_ratio": round(buy_ratio, 4),
        "buy_notional": round(buy_notional, 2),
        "sell_notional": round(sell_notional, 2),
        "symbols": by_symbol,
    }


@app.get("/visualization/area")
async def get_area_chart(
    symbol: str = Query("AAPL", description="Stock symbol"),
    provider: str = Query("demo", description="Data provider"),
    timeframe: int = Query(30, description="Days of history"),
):
    """Get Plotly-ready area chart for Grafana (Infinity compatible).
    
    Returns Plotly JSON for area chart showing buy/sell volume over time.
    Use with Grafana Infinity datasource or HTTP data source.
    """
    data = await get_provider_records(symbol, provider)
    
    # Transform to time series (aggregate by day)
    daily_data = {}
    for item in data:
        date = item.get("lastUpdateDate", "")
        if date:
            shares = item.get("totalWeeklyShareQuantity", 0)
            trades = item.get("totalWeeklyTradeCount", 0)
            if date in daily_data:
                daily_data[date]["shares"] += shares
                daily_data[date]["trades"] += trades
            else:
                daily_data[date] = {"shares": shares, "trades": trades}
    
    # Convert to sorted list
    sorted_dates = sorted(daily_data.keys())[-timeframe:]
    timestamps = []
    shares_data = []
    trades_data = []
    
    for date in sorted_dates:
        timestamps.append(date)
        shares_data.append(daily_data[date]["shares"])
        trades_data.append(daily_data[date]["trades"])
    
    # Plotly Area Chart JSON (Grafana Infinity compatible)
    return {
        "data": [
            {
                "x": timestamps,
                "y": shares_data,
                "type": "scatter",
                "mode": "lines",
                "fill": "tozeroy",
                "name": "Share Volume",
                "line": {"color": "#00d4ff", "width": 2},
                "fillcolor": "rgba(0, 212, 255, 0.3)",
            },
            {
                "x": timestamps,
                "y": trades_data,
                "type": "scatter",
                "mode": "lines",
                "fill": "tozeroy",
                "name": "Trade Count",
                "line": {"color": "#22c55e", "width": 2},
                "fillcolor": "rgba(34, 197, 94, 0.3)",
                "yaxis": "y2",
            },
        ],
        "layout": {
            "title": {"text": f"Darkpool Volume: {symbol}", "font": {"size": 16}},
            "xaxis": {"title": "Date", "showgrid": False},
            "yaxis": {
                "title": "Shares",
                "titlefont": {"color": "#00d4ff"},
                "tickfont": {"color": "#00d4ff"},
                "showgrid": True,
                "gridcolor": "rgba(255,255,255,0.1)",
            },
            "yaxis2": {
                "title": "Trades",
                "titlefont": {"color": "#22c55e"},
                "tickfont": {"color": "#22c55e"},
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
            },
            "paper_bgcolor": "#1a1a2e",
            "plot_bgcolor": "#1a1a2e",
            "font": {"color": "#ffffff"},
            "showlegend": True,
            "legend": {"x": 0, "y": 1},
            "margin": {"l": 60, "r": 60, "t": 50, "b": 60},
        },
    }


@app.get("/visualization/bar")
async def get_bar_chart(
    symbol: str = Query(None, description="Stock symbol (optional for all)"),
    provider: str = Query("demo", description="Data provider"),
    limit: int = Query(10, description="Top N results"),
):
    """Get Plotly-ready bar chart for Grafana.
    
    Returns bar chart JSON - compatible with Infinity datasource.
    Shows top symbols by dark pool volume.
    """
    data = await get_provider_records(symbol, provider)
    
    # Aggregate by symbol
    symbol_data = {}
    for item in data:
        sym = item.get("issueSymbolIdentifier", "")
        if sym:
            shares = item.get("totalWeeklyShareQuantity", 0)
            if sym in symbol_data:
                symbol_data[sym] += shares
            else:
                symbol_data[sym] = shares
    
    # Sort and limit
    sorted_symbols = sorted(symbol_data.items(), key=lambda x: x[1], reverse=True)[:limit]
    
    symbols = [s[0] for s in sorted_symbols]
    values = [s[1] for s in sorted_symbols]
    
    # Plotly Bar Chart JSON
    return {
        "data": [
            {
                "x": symbols,
                "y": values,
                "type": "bar",
                "marker": {
                    "color": values,
                    "colorscale": [
                        [0, "rgba(0, 212, 255, 0.6)"],
                        [1, "rgba(0, 212, 255, 1)"],
                    ],
                },
                "text": values,
                "textposition": "outside",
                "textfont": {"color": "#ffffff"},
            },
        ],
        "layout": {
            "title": {"text": f"Top {limit} Dark Pool Symbols", "font": {"size": 16}},
            "xaxis": {"title": "Symbol", "showgrid": False},
            "yaxis": {
                "title": "Weekly Share Volume",
                "showgrid": True,
                "gridcolor": "rgba(255,255,255,0.1)",
            },
            "paper_bgcolor": "#1a1a2e",
            "plot_bgcolor": "#1a1a2e",
            "font": {"color": "#ffffff"},
            "margin": {"l": 60, "r": 40, "t": 50, "b": 60},
        },
    }


@app.get("/visualization/combined")
async def get_combined_chart(
    symbol: str = Query("AAPL", description="Stock symbol"),
    provider: str = Query("demo", description="Data provider"),
):
    """Get Plotly combined bar + line chart for Grafana.
    
    Returns combined chart with:
    - Bar chart: daily volume
    - Line chart: price/implied move
    
    This is the bar+line combo that OpenBB uses with Plotly.
    """
    data = await get_provider_records(symbol, provider)
    
    # Aggregate by date
    daily_data = {}
    for item in data:
        date = item.get("lastUpdateDate", "")
        if date:
            shares = item.get("totalWeeklyShareQuantity", 0)
            trades = item.get("totalWeeklyTradeCount", 0)
            if date in daily_data:
                daily_data[date]["shares"] += shares
                daily_data[date]["trades"] += trades
            else:
                daily_data[date] = {"shares": shares, "trades": trades}
    
    # Sort
    sorted_dates = sorted(daily_data.keys())
    shares = [daily_data[d]["shares"] for d in sorted_dates]
    trades = [daily_data[d]["trades"] for d in sorted_dates]
    
    # Combined Bar + Line (like OpenBB's charting)
    return {
        "data": [
            {
                "x": sorted_dates,
                "y": shares,
                "type": "bar",
                "name": "Volume",
                "marker": {"color": "rgba(0, 212, 255, 0.7)"},
            },
            {
                "x": sorted_dates,
                "y": trades,
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Trades",
                "line": {"color": "#22c55e", "width": 3},
                "marker": {"size": 6},
                "yaxis": "y2",
            },
        ],
        "layout": {
            "title": {"text": f"{symbol}: Volume & Trades", "font": {"size": 18}},
            "barmode": "overlay",
            "xaxis": {"title": "Date"},
            "yaxis": {
                "title": "Volume",
                "titlefont": {"color": "#00d4ff"},
                "tickfont": {"color": "#00d4ff"},
            },
            "yaxis2": {
                "title": "Trades",
                "titlefont": {"color": "#22c55e"},
                "tickfont": {"color": "#22c55e"},
                "overlaying": "y",
                "side": "right",
            },
            "paper_bgcolor": "#1a1a2e",
            "plot_bgcolor": "#1a1a2e",
            "font": {"color": "#ffffff"},
            "showlegend": True,
        },
    }

    return fig


# ============================================================================
# Grafana Infinity Data Source Format
# ============================================================================

@app.get("/grafana/table")
async def get_grafana_table(
    symbol: str = Query(None, description="Stock symbol"),
    provider: str = Query("demo", description="Data provider"),
    limit: int = Query(100, description="Max rows"),
):
    """Grafana Infinity compatible table format.
    
    Returns JSON in format that Grafana Infinity plugin can parse.
    Use: URL = /grafana/table?symbol=AAPL
    """
    data = await get_provider_records(symbol, provider, limit=limit)
    
    # Return array of objects (Infinity-compatible)
    return [
        {
            "symbol": item.get("issueSymbolIdentifier"),
            "shares": item.get("totalWeeklyShareQuantity"),
            "trades": item.get("totalWeeklyTradeCount"),
            "date": item.get("lastUpdateDate"),
            "week": item.get("weekStartDate"),
        }
        for item in data[:limit]
    ]


@app.get("/grafana/timeseries")
async def get_grafana_timeseries(
    symbol: str = Query("AAPL", description="Stock symbol"),
    provider: str = Query("demo", description="Data provider"),
):
    """Grafana timeseries compatible format.
    
    Returns data in Grafana-native timeseries format.
    """
    data = await get_provider_records(symbol, provider)
    
    # Aggregate by date
    daily_data = {}
    for item in data:
        date = item.get("lastUpdateDate", "")
        if date:
            shares = item.get("totalWeeklyShareQuantity", 0)
            if date in daily_data:
                daily_data[date] += shares
            else:
                daily_data[date] = shares
    
    # Return as array with timestamp
    return [
        {"timestamp": date, "value": daily_data[date], "metric": symbol}
        for date in sorted(daily_data.keys())
    ]

# ============================================================================
# VWAP / NBBO Analysis
# ============================================================================

class NBBOQuote(BaseModel):
    """National Best Bid and Offer."""

    symbol: str
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    timestamp: str


class TradePrint(BaseModel):
    """Individual trade print."""

    symbol: str
    price: float
    size: int
    is_buy: bool  # True = buy, False = sell
    vwap: float | None = None
    is_aggressive: bool | None = None  # Above ask or below bid
    source: str  # exchange


@app.get("/nbbo/quote")
async def get_nbbo_quote(
    symbol: str = Query(..., description="Stock symbol"),
    provider: str = Query("finra", description="Data provider"),
):
    """Get current NBBO quote for a symbol.
    
    Returns bid/ask from all exchanges with size.
    """
    import httpx

    if provider == "polygon":
        api_key = os.getenv("POLYGON_API_KEY", "")
        if not api_key:
            raise HTTPException(401, "Polygon API key not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.polygon.io/v3/quotes/{symbol}",
                params={"timestamp": "now"},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )

        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                # Find best bid and ask
                best_bid = {"price": 0, "size": 0}
                best_ask = {"price": float("inf"), "size": 0}

                for q in results:
                    if q.get("side") == "bid":
                        if q.get("price", 0) > best_bid["price"]:
                            best_bid = {"price": q.get("price"), "size": q.get("size")}
                    else:
                        if q.get("price", float("inf")) < best_ask["price"]:
                            best_ask = {"price": q.get("price"), "size": q.get("size")}

                return {
                    "symbol": symbol,
                    "bid": best_bid["price"],
                    "bid_size": best_bid["size"],
                    "ask": best_ask["price"],
                    "ask_size": best_ask["size"],
                    "mid": (best_bid["price"] + best_ask["price"]) / 2,
                    "spread": best_ask["price"] - best_bid["price"],
                    "spread_bps": (best_ask["price"] - best_bid["price"]) / best_bid["price"] * 10000
                    if best_bid["price"] > 0 else 0,
                    "timestamp": results[0].get("timestamp"),
                }

    # Fallback: Generate synthetic NBBO for demo
    stock = MAG7_STOCKS.get(symbol.upper(), {"basePrice": 100})
    price = stock.get("basePrice", 100)
    spread = price * 0.001  # 10 bps spread

    return {
        "symbol": symbol.upper(),
        "bid": round(price - spread / 2, 2),
        "bid_size": 10000,
        "ask": round(price + spread / 2, 2),
        "ask_size": 10000,
        "mid": price,
        "spread": round(spread, 2),
        "spread_bps": 10,
        "timestamp": datetime.utcnow().isoformat(),
        "_note": "Synthetic (configure Polygon API key for real data)",
    }


@app.get("/nbbo/trades")
async def get_nbbo_trades(
    symbol: str = Query(..., description="Stock symbol"),
    provider: str = Query("finra", description="Data provider"),
    limit: int = Query(100, description="Max trades"),
):
    """Get dark pool trades compared against NBBO.
    
    Shows aggressive prints:
    - BUY above ask = aggressive buying (taking liquidity)
    - SELL below bid = aggressive selling (hitting liquidity)
    
    Calculates VWAP and sentiment.
    """
    import httpx

    # Get NBBO first
    nbbo = await get_nbbo_quote(symbol, provider)
    bid = nbbo["bid"]
    ask = nbbo["ask"]
    mid = nbbo["mid"]

    # Get trades
    if provider == "polygon":
        api_key = os.getenv("POLYGON_API_KEY", "")
        if api_key:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.polygon.io/v3/ticks/{symbol}/trades",
                    params={"limit": limit, "timestamp": "now"},
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=30,
                )

            if resp.status_code == 200:
                data = resp.json()
                trades = []
                total_vwap = 0
                buy_count = 0
                sell_count = 0

                for t in data.get("results", []):
                    price = t.get("p", 0)
                    size = t.get("s", 0)
                    exchange = t.get("x", "")

                    # Determine if buy or sell based on price vs NBBO
                    if price >= ask:
                        direction = "BUY"
                        is_aggressive = True
                    elif price <= bid:
                        direction = "SELL"
                        is_aggressive = True
                    else:
                        # Passive - between bid and ask
                        if price >= mid:
                            direction = "BUY"
                        else:
                            direction = "SELL"
                        is_aggressive = False

                    if direction == "BUY":
                        buy_count += 1
                    else:
                        sell_count += 1

                    total_vwap += price * size

                    trades.append({
                        "symbol": symbol,
                        "price": price,
                        "size": size,
                        "direction": direction,
                        "is_aggressive": is_aggressive,
                        "exchange": exchange,
                        "timestamp": t.get("t"),
                    })

                # Calculate overall VWAP
                total_size = sum(t["size"] for t in trades)
                vwap = total_vwap / total_size if total_size > 0 else 0

                return {
                    "symbol": symbol,
                    "nbbo": {"bid": bid, "ask": ask, "mid": mid},
                    "trades": trades[:limit],
                    "summary": {
                        "total_trades": len(trades),
                        "buy_count": buy_count,
                        "sell_count": sell_count,
                        "buy_ratio": buy_count / len(trades) if trades else 0.5,
                        "aggressive_count": sum(1 for t in trades if t["is_aggressive"]),
                        "vwap": vwap,
                        "vwap_vs_mid": (vwap - mid) / mid * 100 if mid > 0 else 0,
                    },
                }

    # Fallback: Generate synthetic trades
    import random

    trades = []
    total_vwap = 0
    total_size = 0
    buy_count = 0
    sell_count = 0
    aggressive_count = 0

    for _ in range(min(limit, 50)):
        tx = generateTransaction()
        if tx["symbol"] != symbol.upper():
            continue

        price = tx["price"]
        size = tx["size"]
        direction = tx["direction"]

        # Determine aggression based on price vs NBBO
        if direction == "BUY":
            is_aggressive = price >= ask
            buy_count += 1
        else:
            is_aggressive = price <= bid
            sell_count += 1

        if is_aggressive:
            aggressive_count += 1

        total_vwap += price * size
        total_size += size

        trades.append({
            "symbol": tx["symbol"],
            "price": price,
            "size": size,
            "direction": direction,
            "is_aggressive": is_aggressive,
            "exchange": "SYNTH",
            "timestamp": datetime.utcnow().isoformat(),
        })

    vwap = total_vwap / total_size if total_size > 0 else 0

    return {
        "symbol": symbol,
        "nbbo": {"bid": bid, "ask": ask, "mid": mid},
        "trades": trades[:limit],
        "summary": {
            "total_trades": len(trades),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_ratio": buy_count / len(trades) if trades else 0.5,
            "aggressive_count": aggressive_count,
            "vwap": round(vwap, 2),
            "vwap_vs_mid": round((vwap - mid) / mid * 100, 2),
        },
        "_note": "Synthetic (configure Polygon API key for real data)",
    }


@app.get("/vwap/analysis")
async def get_vwap_analysis(
    symbol: str = Query(..., description="Stock symbol"),
    lookback: int = Query(30, description="Number of trades"),
):
    """Get VWAP analysis with NBBO comparison.
    
    Returns:
    - VWAP: Volume Weighted Average Price
    - VWAP vs Mid: % premium/discount to NBBO mid
    - Aggressive ratio: % of trades taking liquidity
    - Sentiment: BULLISH/BEARING/NEUTRAL
    """
    # Get trades with NBBO
    result = await get_nbbo_trades(symbol, "polygon" if os.getenv("POLYGON_API_KEY") else "finra", lookback)

    summary = result.get("summary", {})
    vwap = summary.get("vwap", 0)
    vwap_vs_mid = summary.get("vwap_vs_mid", 0)
    buy_ratio = summary.get("buy_ratio", 0.5)
    aggressive_ratio = summary.get("aggressive_count", 0) / summary.get("total_trades", 1)

    # Determine sentiment
    if vwap_vs_mid > 0.1 and buy_ratio > 0.6:
        sentiment = "BULLISH"
        emoji = "🐂"
    elif vwap_vs_mid < -0.1 and buy_ratio < 0.4:
        sentiment = "BEARISH"
        emoji = "🐻"
    else:
        sentiment = "NEUTRAL"
        emoji = "⚖️"

    return {
        "symbol": symbol,
        "analysis": {
            "vwap": round(vwap, 2),
            "vwap_vs_mid_pct": round(vwap_vs_mid, 2),
            "buy_ratio": round(buy_ratio * 100, 1),
            "aggressive_ratio": round(aggressive_ratio * 100, 1),
            "sentiment": sentiment,
            "emoji": emoji,
            "interpretation": f"{sentiment} sentiment: VWAP {('above' if vwap_vs_mid > 0 else 'below')} mid by {abs(vwap_vs_mid):.2f}%" if abs(vwap_vs_mid) > 0.01 else "Near fair value",
        },
        "nbbo": result.get("nbbo"),
    }

# ============================================================================
# Order Book Imbalance
# ============================================================================

# Import for orderbook
from collections import defaultdict

@app.get("/orderbook/imbalance")
async def get_orderbook_imbalance(
    symbol: str = Query(..., description="Stock symbol"),
    levels: int = Query(10, description="Order book levels"),
):
    """Get order book imbalance analysis.
    
    Shows:
    - Bid/Ask size imbalance
    - Cumulative depth at each level  
    - Order book pressure indicator
    """

    stock = MAG7_STOCKS.get(symbol.upper(), {"basePrice": 100})
    mid = stock.get("basePrice", 100)
    
    # Generate synthetic order book
    bid_levels = []
    ask_levels = []
    
    for i in range(levels):
        bid_px = mid - (i + 1) * 0.05
        bid_size = int(50000 * (1 + i * 0.2) * (0.9 + 0.2 * (i % 3)))
        bid_levels.append({"price": round(bid_px, 2), "size": bid_size})
        
        ask_px = mid + (i + 1) * 0.05
        ask_size = int(50000 * (1 + i * 0.2) * (0.9 + 0.2 * ((i + 1) % 3)))
        ask_levels.append({"price": round(ask_px, 2), "size": ask_size})
    
    # Calculate imbalance
    bid_total = sum(b["size"] for b in bid_levels)
    ask_total = sum(a["size"] for a in ask_levels)
    total = bid_total + ask_total
    
    imbalance = (bid_total - ask_total) / total if total > 0 else 0
    
    # Calculate pressure
    if imbalance > 0.3:
        pressure = "BUY_BIAS"
        emoji = "🟢"
    elif imbalance < -0.3:
        pressure = "SELL_BIAS"
        emoji = "🔴"
    else:
        pressure = "BALANCED"
        emoji = "⚖️"
    
    # Cumulative imbalance at each level
    imbalance_levels = []
    cum_bid = 0
    cum_ask = 0
    
    for i in range(min(len(bid_levels), len(ask_levels))):
        cum_bid += bid_levels[i]["size"]
        cum_ask += ask_levels[i]["size"]
        level_imbalance = (cum_bid - cum_ask) / (cum_bid + cum_ask) if (cum_bid + cum_ask) > 0 else 0
        imbalance_levels.append({
            "level": i + 1,
            "bid_cum": cum_bid,
            "ask_cum": cum_ask,
            "imbalance": round(level_imbalance, 3),
        })
    
    return {
        "symbol": symbol.upper(),
        "source": "synthetic",
        "metrics": {
            "bid_total": bid_total,
            "ask_total": ask_total,
            "imbalance_ratio": round(imbalance, 3),
            "pressure": pressure,
            "emoji": emoji,
        },
        "levels": imbalance_levels[:5],
        "interpretation": f"Order book {pressure}: {imbalance*100:+.1f}% bid bias",
    }


# ============================================================================
# Volume Profile
# ============================================================================

@app.get("/volume/profile")
async def get_volume_profile(
    symbol: str = Query(..., description="Stock symbol"),
    bins: int = Query(20, description="Price bins"),
):
    """Get volume profile analysis."""
    import random

    stock = MAG7_STOCKS.get(symbol.upper(), {"basePrice": 100})
    mid = stock.get("basePrice", 100)
    
    random.seed(hash(symbol.upper()))
    profile = []
    
    for i in range(bins):
        bin_low = mid - 10 + (i * (20 / bins))
        bin_high = bin_low + (20 / bins)
        vol = int(100000 * (1 + 2 * (1 - abs(i - bins/2) / (bins/1))) + random.randint(-10000, 10000))
        profile.append({
            "bin": i + 1,
            "price_low": round(bin_low, 2),
            "price_high": round(bin_high, 2),
            "volume": max(0, vol),
        })
    
    profile_sorted = sorted(profile, key=lambda x: x["volume"], reverse=True)
    total_vol = sum(p["volume"] for p in profile)
    vpoc = profile_sorted[0]
    
    # Value area (70%)
    va_vol = 0
    va_target = total_vol * 0.70
    value_area = []
    for p in profile_sorted:
        if va_vol >= va_target:
            break
        value_area.append(p)
        va_vol += p["volume"]
    
    return {
        "symbol": symbol.upper(),
        "source": "synthetic",
        "analysis": {
            "vpoc_price": vpoc["price_low"],
            "vpoc_volume": vpoc["volume"],
            "total_volume": total_vol,
            "value_area": {"low": min(va["price_low"] for va in value_area), "high": max(va["price_high"] for va in value_area), "volume_pct": 70},
        },
    }


# ============================================================================
# Time-of-Day Sentiment
# ============================================================================

@app.get("/sentiment/timeofday")
async def get_timeofday_sentiment(symbol: str = Query(...)):
    """Get time-of-day sentiment."""
    now = datetime.utcnow()
    et_hour = (now.hour - 4) % 24
    
    if 4 <= et_hour < 9:
        session, name, desc, rec = "pre", "Pre-Market", "4:00 - 9:30 AM ET", "Cautious"
    elif 9 <= et_hour < 16:
        session, name, desc, rec = "regular", "Regular Hours", "9:30 AM - 4:00 PM ET", "Trade"
    elif 16 <= et_hour < 20:
        session, name, desc, rec = "after", "After Hours", "4:00 - 8:00 PM ET", "Cautious"
    else:
        session, name, desc, rec = "closed", "Market Closed", "8:00 PM - 4:00 AM ET", "Wait"
    
    return {"symbol": symbol.upper(), "session": session, "session_info": {"name": name, "description": desc}, "recommendation": rec, "et_timestamp": now.isoformat()}


# ============================================================================
# Complete Analysis
# ============================================================================

@app.get("/analysis/complete")
async def get_complete_analysis(symbol: str = Query("AAPL")):
    """Get complete analysis."""
    # Get all components
    vwap = await get_vwap_analysis(symbol, 30)
    ob = await get_orderbook_imbalance(symbol, 10)
    vol = await get_volume_profile(symbol, 20)
    tod = await get_timeofday_sentiment(symbol)
    
    # Combine signals
    signals = [
        {"source": "vwap", "value": vwap.get("analysis", {}).get("sentiment", "NEUTRAL"), "weight": 0.3},
        {"source": "orderbook", "value": ob.get("metrics", {}).get("pressure", "BALANCED"), "weight": 0.25},
        {"source": "timeofday", "value": "BULLISH" if tod.get("recommendation") == "Trade" else "NEUTRAL", "weight": 0.15},
    ]
    
    bullish = sum(1 for s in signals if s["value"] in ["BULLISH", "BUY_BIAS"])
    bearish = sum(1 for s in signals if s["value"] in ["BEARISH", "SELL_BIAS"])
    
    if bullish > bearish:
        overall, emoji = "BULLISH", "🐂"
    elif bearish > bullish:
        overall, emoji = "BEARISH", "🐻"
    else:
        overall, emoji = "NEUTRAL", "⚖️"
    
    return {"symbol": symbol.upper(), "generated_at": datetime.utcnow().isoformat(), "overall_sentiment": overall, "emoji": emoji, "signals": signals, "components": {"vwap": vwap.get("analysis"), "orderbook": ob.get("metrics"), "volume_profile": vol.get("analysis"), "timeofday": tod}}


# ============================================================================
# Whale Threshold Alerts
# ============================================================================

class WhaleAlertConfig(BaseModel):
    """Whale alert configuration."""

    symbol: str
    min_shares: int = 50000
    min_dollars: int = 1_000_000  # $1M default
    active: bool = True
    webhook_url: str | None = None


# In-memory alert store (use Redis/DB in production)
WHALE_ALERTS: dict[str, dict] = {}


@app.get("/alerts/config")
async def get_alert_configs():
    """Get all whale alert configurations."""
    return {"alerts": list(WHALE_ALERTS.values())}


@app.post("/alerts/config")
async def create_alert_config(config: WhaleAlertConfig):
    """Create a whale alert configuration."""
    WHALE_ALERTS[config.symbol.upper()] = {
        "symbol": config.symbol.upper(),
        "min_shares": config.min_shares,
        "min_dollars": config.min_dollars,
        "active": config.active,
        "webhook_url": config.webhook_url,
        "created_at": datetime.utcnow().isoformat(),
    }
    return {"status": "created", "alert": WHALE_ALERTS[config.symbol.upper()]}


@app.delete("/alerts/config/{symbol}")
async def delete_alert_config(symbol: str):
    """Delete a whale alert configuration."""
    if symbol.upper() in WHALE_ALERTS:
        del WHALE_ALERTS[symbol.upper()]
        return {"status": "deleted", "symbol": symbol.upper()}
    raise HTTPException(404, f"Alert for {symbol} not found")


@app.get("/alerts/check")
async def check_whale_alert(
    symbol: str = Query(..., description="Stock symbol"),
    shares: int = Query(..., description="Trade size in shares"),
    price: float = Query(..., description="Trade price"),
):
    """Check if a trade triggers whale alert."""
    import httpx

    notional = shares * price
    triggered = False
    alert_level = None

    # Check against config
    config = WHALE_ALERTS.get(symbol.upper(), {"min_shares": 50000, "min_dollars": 1000000, "active": True})

    if not config.get("active", True):
        return {"triggered": False, "reason": "Alert disabled"}

    if shares >= config.get("min_shares", 50000):
        triggered = True
        alert_level = "shares"
    elif notional >= config.get("min_dollars", 1000000):
        triggered = True
        alert_level = "dollars"

    # Send webhook if triggered
    if triggered and config.get("webhook_url"):
        embed = {
            "title": f"🐋 WHALE ALERT: {symbol.upper()}",
            "description": f"**{shares:,} shares** @ ${price:.2f}",
            "color": 0xFF6B00,
            "fields": [
                {"name": "Notional Value", "value": f"${notional:,.0f}", "inline": True},
                {"name": "Triggered By", "value": alert_level, "inline": True},
                {"name": "Threshold", "value": f"{config.get('min_shares'):,} shares or ${config.get('min_dollars'):,}", "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }

        payload = {
            "content": "🐋 **WHALE ALERT**",
            "embeds": [embed],
        }

        async with httpx.AsyncClient() as client:
            await client.post(config["webhook_url"], json=payload, timeout=10.0)

    return {
        "triggered": triggered,
        "reason": f"{alert_level} threshold exceeded" if triggered else "No threshold exceeded",
        "notional": notional,
        "symbol": symbol.upper(),
        "shares": shares,
        "price": price,
        "config": config,
    }


@app.get("/alerts/whale-feed")
async def get_whale_feed(
    symbol: str = Query(None, description="Stock symbol filter"),
    min_shares: int = Query(50000, description="Minimum shares"),
    min_dollars: int = Query(1000000, description="Minimum dollars"),
    limit: int = Query(100, description="Max results"),
):
    """Get recent whale activity feed."""
    # Generate transactions and filter
    whales = []
    for _ in range(limit * 3):
        tx = generateTransaction()
        notional = tx["size"] * tx["price"]

        # Apply filters
        if symbol and tx["symbol"] != symbol.upper():
            continue
        if tx["size"] < min_shares and notional < min_dollars:
            continue

        whales.append({
            "id": tx["id"],
            "symbol": tx["symbol"],
            "direction": tx["direction"],
            "shares": tx["size"],
            "price": tx["price"],
            "notional": notional,
            "timestamp": tx["timestamp"].isoformat(),
            "is_whale": True,
        })

    return {
        "whales": whales[:limit],
        "count": len(whales[:limit]),
        "filters": {"symbol": symbol, "min_shares": min_shares, "min_dollars": min_dollars},
    }


# ============================================================================
# Options Dashboard Metrics
# ============================================================================

class OptionsMetricsQuery(BaseModel):
    """Options metrics query."""

    symbol: str | None = None
    days_back: int = 7


@app.get("/options/highest-call-vol")
async def get_highest_call_vol_change(
    symbol: str = Query(None),
    days_back: int = Query(7, ge=1, le=90, description="Days back to compare"),
    limit: int = Query(10, ge=1, le=50),
):
    """Get stocks with highest call volume change."""
    import random

    random.seed(days_back * 100)
    results = []

    symbols = [symbol.upper()] if symbol else list(MAG7_STOCKS.keys())

    for sym in symbols:
        base = MAG7_STOCKS.get(sym, {"basePrice": 100})
        # Simulate volume data
        for _ in range(limit):
            strike = base.get("basePrice", 100) * random.uniform(0.9, 1.2)
            call_vol = int(random.randint(1000, 50000) * (1 + days_back * 0.5))
            prev_vol = int(call_vol / (1 + random.uniform(0.1, 0.8)))
            pct_change = ((call_vol - prev_vol) / prev_vol * 100) if prev_vol > 0 else 0

            results.append({
                "symbol": sym,
                "strike": round(strike, 2),
                "type": "CALL",
                "volume": call_vol,
                "prev_volume": prev_vol,
                "volume_change_pct": round(pct_change, 2),
                "iv": round(random.uniform(20, 60), 1),
                "bid": round(strike * 0.95, 2),
                "ask": round(strike * 1.05, 2),
                "mid": round(strike, 2),
            })

    results.sort(key=lambda x: x["volume_change_pct"], reverse=True)
    return {"metric": "highest_call_vol_change", "days_back": days_back, "results": results[:limit]}


@app.get("/options/highest-put-vol")
async def get_highest_put_vol_change(
    symbol: str = Query(None),
    days_back: int = Query(7, ge=1, le=90),
    limit: int = Query(10, ge=1, le=50),
):
    """Get stocks with highest put volume change."""
    import random

    random.seed(days_back * 200 + 1)
    results = []

    symbols = [symbol.upper()] if symbol else list(MAG7_STOCKS.keys())

    for sym in symbols:
        base = MAG7_STOCKS.get(sym, {"basePrice": 100})
        for _ in range(limit):
            strike = base.get("basePrice", 100) * random.uniform(0.8, 1.1)
            put_vol = int(random.randint(1000, 50000) * (1 + days_back * 0.5))
            prev_vol = int(put_vol / (1 + random.uniform(0.1, 0.8)))
            pct_change = ((put_vol - prev_vol) / prev_vol * 100) if prev_vol > 0 else 0

            results.append({
                "symbol": sym,
                "strike": round(strike, 2),
                "type": "PUT",
                "volume": put_vol,
                "prev_volume": prev_vol,
                "volume_change_pct": round(pct_change, 2),
                "iv": round(random.uniform(20, 60), 1),
                "bid": round(strike * 0.95, 2),
                "ask": round(strike * 1.05, 2),
                "mid": round(strike, 2),
            })

    results.sort(key=lambda x: x["volume_change_pct"], reverse=True)
    return {"metric": "highest_put_vol_change", "days_back": days_back, "results": results[:limit]}


@app.get("/options/high-vol-cheapies")
async def get_high_vol_cheapies(
    symbol: str = Query(None),
    max_ask: float = Query(5.0, description="Maximum ask price"),
    min_volume: int = Query(10000, description="Minimum volume"),
    limit: int = Query(10, ge=1, le=50),
):
    """Get high volume cheap contracts (< $5 ask)."""
    import random

    random.seed(300)
    results = []

    symbols = [symbol.upper()] if symbol else list(MAG7_STOCKS.keys())

    for sym in symbols:
        base = MAG7_STOCKS.get(sym, {"basePrice": 100})
        for _ in range(limit * 2):
            strike = base.get("basePrice", 100) * random.uniform(0.85, 1.15)
            op_type = random.choice(["CALL", "PUT"])
            ask = round(random.uniform(0.5, max_ask), 2)
            if ask > max_ask:
                continue

            vol = int(random.randint(min_volume, 100000))

            results.append({
                "symbol": sym,
                "strike": round(strike, 2),
                "type": op_type,
                "bid": round(max(0.01, ask * 0.85), 2),
                "ask": ask,
                "mid": round((ask * 0.85 + ask) / 2, 2),
                "volume": vol,
                "open_interest": int(vol * random.uniform(1, 3)),
                "iv": round(random.uniform(15, 50), 1),
                "days_to_exp": random.randint(1, 45),
            })

    results.sort(key=lambda x: x["volume"], reverse=True)
    return {"metric": "high_vol_cheapies", "max_ask": max_ask, "results": results[:limit]}


@app.get("/options/high-vol-leaps")
async def get_high_vol_leaps(
    symbol: str = Query(None),
    min_months: int = Query(6, description="Minimum months to expiration"),
    min_volume: int = Query(5000, description="Minimum volume"),
    limit: int = Query(10, ge=1, le=50),
):
    """Get high volume LEAP contracts (6+ months)."""
    import random

    random.seed(400)
    results = []

    symbols = [symbol.upper()] if symbol else list(MAG7_STOCKS.keys())

    for sym in symbols:
        base = MAG7_STOCKS.get(sym, {"basePrice": 100})

        # LEAPS: Generate strikes for future expiration
        for months in [6, 9, 12, 18, 24]:
            for _ in range(limit):
                strike = base.get("basePrice", 100) * random.uniform(0.7, 1.3)
                op_type = random.choice(["CALL", "PUT"])
                vol = int(random.randint(min_volume, 80000))
                premium = round(vol * strike / 100, 2)  # Approximate premium

                results.append({
                    "symbol": sym,
                    "strike": round(strike, 2),
                    "type": op_type,
                    "expiration_months": months,
                    "bid": round(premium * 0.85, 2),
                    "ask": round(premium * 1.15, 2),
                    "mid": round(premium, 2),
                    "volume": vol,
                    "open_interest": int(vol * random.uniform(2, 5)),
                    "iv": round(random.uniform(25, 45), 1),
                })

    results = [r for r in results if r["expiration_months"] >= min_months]
    results.sort(key=lambda x: x["volume"], reverse=True)
    return {"metric": "high_vol_leaps", "min_months": min_months, "results": results[:limit]}


@app.get("/options/most-otm-strikes")
async def get_most_otm_strikes(
    symbol: str = Query(None),
    min_otm_pct: float = Query(10.0, ge=5, le=50, description="Min % OTM"),
    limit: int = Query(10, ge=1, le=50),
):
    """Get most out-of-the-money strikes."""
    import random

    random.seed(500)
    results = []

    symbols = [symbol.upper()] if symbol else list(MAG7_STOCKS.keys())

    for sym in symbols:
        base = MAG7_STOCKS.get(sym, {"basePrice": 100})
        price = base.get("basePrice", 100)

        # Generate OTM strikes
        for pct in [min_otm_pct, min_otm_pct * 1.5, min_otm_pct * 2, min_otm_pct * 3]:
            # Calls: strike above current price
            call_strike = price * (1 + pct / 100)
            # Puts: strike below current price
            put_strike = price * (1 - pct / 100)

            for strike, otype in [(call_strike, "CALL"), (put_strike, "PUT")]:
                vol = int(random.randint(500, 20000) / (pct / 10))

                results.append({
                    "symbol": sym,
                    "strike": round(strike, 2),
                    "type": otype,
                    "current_price": price,
                    "otm_pct": round(pct, 1),
                    "volume": vol,
                    "open_interest": int(vol * random.uniform(1, 4)),
                    "bid": round(random.uniform(0.1, 2), 2),
                    "ask": round(random.uniform(0.2, 3), 2),
                    "iv": round(random.uniform(30, 70), 1),
                })

    results.sort(key=lambda x: x["volume"], reverse=True)
    return {"metric": "most_otm_strikes", "min_otm_pct": min_otm_pct, "results": results[:limit]}


@app.get("/options/large-otm-oi")
async def get_large_otm_oi(
    symbol: str = Query(None),
    min_otm_pct: float = Query(5.0, ge=2, le=30),
    min_oi: int = Query(10000, description="Minimum open interest"),
    limit: int = Query(10, ge=1, le=50),
):
    """Get large OTM open interest positions."""
    import random

    random.seed(600)
    results = []

    symbols = [symbol.upper()] if symbol else list(MAG7_STOCKS.keys())

    for sym in symbols:
        base = MAG7_STOCKS.get(sym, {"basePrice": 100})
        price = base.get("basePrice", 100)

        for pct in [min_otm_pct, min_otm_pct * 1.5, min_otm_pct * 2, min_otm_pct * 2.5]:
            call_strike = price * (1 + pct / 100)
            put_strike = price * (1 - pct / 100)

            for strike, otype in [(call_strike, "CALL"), (put_strike, "PUT")]:
                oi = int(random.randint(min_oi, 100000))

                results.append({
                    "symbol": sym,
                    "strike": round(strike, 2),
                    "type": otype,
                    "current_price": price,
                    "otm_pct": round(pct, 1),
                    "open_interest": oi,
                    "volume": int(oi * random.uniform(0.05, 0.3)),
                    "bid": round(random.uniform(0.1, 3), 2),
                    "ask": round(random.uniform(0.2, 4), 2),
                    "iv": round(random.uniform(25, 65), 1),
                })

    results = [r for r in results if r["open_interest"] >= min_oi]
    results.sort(key=lambda x: x["open_interest"], reverse=True)
    return {"metric": "large_otm_oi", "min_otm_pct": min_otm_pct, "results": results[:limit]}


# ============================================================================
# Market Cap Milestone Tracker
# ============================================================================

@app.get("/marketcap/milestones")
async def get_market_cap_milestones(
    symbol: str = Query(None),
    target_milestone: int = Query(1_000_000_000_000, description="Target market cap (default $1T)"),
):
    """Get market cap milestone tracking."""
    import random

    random.seed(700)
    results = []

    symbols = [symbol.upper()] if symbol else list(MAG7_STOCKS.keys())

    for sym in symbols:
        base = MAG7_STOCKS.get(sym, {"basePrice": 100})

        # Simulate market cap and milestones
        price = base.get("basePrice", 100)
        shares_out = int(random.uniform(1e9, 20e9))
        current_mcap = price * shares_out

        # Calculate days to milestone
        daily_growth = random.uniform(-0.02, 0.05)
        days_to_milestone = None

        if current_mcap < target_milestone:
            # Calculate compound growth days
            rate = 1 + daily_growth
            import math
            days_to_milestone = int(math.log(target_milestone / current_mcap) / math.log(rate))
            days_to_milestone = max(1, min(days_to_milestone, 3650))  # Cap at 10 years

        # Generate historical milestones
        milestones = [
            {"level": "100B", "target": 100_000_000_000, "achieved": current_mcap >= 100_000_000_000},
            {"level": "500B", "target": 500_000_000_000, "achieved": current_mcap >= 500_000_000_000},
            {"level": "1T", "target": 1_000_000_000_000, "achieved": current_mcap >= 1_000_000_000_000},
            {"level": "2T", "target": 2_000_000_000_000, "achieved": current_mcap >= 2_000_000_000_000},
            {"level": "3T", "target": 3_000_000_000_000, "achieved": current_mcap >= 3_000_000_000_000},
        ]

        results.append({
            "symbol": sym,
            "current_price": price,
            "shares_outstanding": shares_out,
            "market_cap": current_mcap,
            "target_milestone": target_milestone,
            "days_to_target": days_to_milestone,
            "required_daily_pct": round((target_milestone / current_mcap) ** (1/252) - 1, 4) if days_to_milestone else 0,
            "milestones": [
                {"level": m["level"], "achieved": m["achieved"]}
                for m in milestones
            ],
        })

    return {"metric": "marketcap_milestones", "results": results}


# ============================================================================
# Data Context & Source Provenance
# ============================================================================

class EventSource(BaseModel):
    """Event source metadata."""
    source_id: str
    source_name: str  # FINRA, Polygon, Intrinio
    feed_type: str   # tape_a, tape_b, tape_c, consolidated
    venue: str       # BATS, NYSE, CBOE, etc.
    confidence: float = 1.0  # 0-1 confidence score
    latency_ms: int = 0      # Exchange to client latency
    received_at: str          # ISO timestamp from source


class DataSourceConnector(BaseModel):
    """Data source connector configuration."""
    id: str
    name: str
    provider: Literal["finra", "polygon", "intrinio"]
    api_key: str | None = None
    endpoint: str | None = None
    status: Literal["connected", "disconnected", "error"] = "disconnected"
    last_heartbeat: str | None = None
    events_received: int = 0
    events_dropped: int = 0
    parser_errors: int = 0
    feed_lag_ms: int = 0


@app.get("/data/sources")
async def get_data_sources():
    """Get configured data source connectors."""
    return {
        "sources": [
            {
                "id": "finra_tape_a",
                "name": "FINRA TRF Tape A",
                "provider": "finra",
                "status": "connected",
                "last_heartbeat": datetime.now().isoformat(),
                "events_received": 1247893,
                "events_dropped": 12,
                "parser_errors": 3,
                "feed_lag_ms": 45,
            },
            {
                "id": "polygon",
                "name": "Polygon.io",
                "provider": "polygon",
                "status": "connected",
                "last_heartbeat": datetime.now().isoformat(),
                "events_received": 892341,
                "events_dropped": 0,
                "parser_errors": 1,
                "feed_lag_ms": 23,
            },
            {
                "id": "intrinio",
                "name": "Intrinio",
                "provider": "intrinio",
                "status": "disconnected",
                "last_heartbeat": None,
                "events_received": 0,
                "events_dropped": 0,
                "parser_errors": 0,
                "feed_lag_ms": 0,
            },
        ]
    }


class EnhancedTransaction(BaseModel):
    """Transaction with full provenance."""
    id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    size: int
    price: float
    venue: str
    timestamp: str  # Exchange timestamp
    received_at: str  # Client received
    source: str  # Source ID
    feed_type: str
    confidence: float = 1.0
    latency_ms: int = 0
    is_anomalous: bool = False
    z_score: float = 0.0
    adv_pct: float = 0.0  # % of average daily volume
    vwap_deviation: float = 0.0


# ============================================================================
# Scanner Features
# ============================================================================

@app.get("/scanner/prints")
async def get_scanner_prints(
    min_size: int = Query(1000, description="Minimum trade size"),
    sort_by: str = Query("size", description="Sort field"),
    limit: int = Query(100, description="Max results"),
):
    """Real-time scanner with sortable prints."""
    import random

    random.seed(42)
    results = []

    symbols = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]
    venues = ["BATS", "NYSE", "CBOE", "MEMX", "IEX"]
    feeds = ["tape_a", "tape_b", "tape_c"]

    for _ in range(limit):
        sym = random.choice(symbols)
        size = random.randint(min_size, 500000)
        price = random.uniform(50, 500)
        adv = random.uniform(1000000, 50000000)
        confidence = random.uniform(0.7, 1.0)

        results.append({
            "id": f"evt_{random.randint(100000, 999999)}",
            "symbol": sym,
            "side": random.choice(["BUY", "SELL"]),
            "size": size,
            "price": round(price, 2),
            "venue": random.choice(venues),
            "feed_type": random.choice(feeds),
            "timestamp": datetime.now().isoformat(),
            "received_at": datetime.now().isoformat(),
            "source": "finra_tape_a",
            "confidence": round(confidence, 2),
            "latency_ms": random.randint(10, 100),
            "z_score": round(random.uniform(-3, 3), 2),
            "adv_pct": round(size / adv * 100, 4),
        })

    results.sort(key=lambda x: x.get(sort_by, 0), reverse=True)
    return {"scanner": results, "count": len(results)}


@app.get("/scanner/heatmap")
async def get_flow_heatmap(
    symbol: str = Query(None),
    time_buckets: int = Query(13, description="Number of time buckets"),
):
    """Ticker × time bucket heatmap by abnormality score."""
    import random

    random.seed(100)
    results = []

    symbols = [symbol.upper()] if symbol else ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]

    for sym in symbols:
        for bucket in range(time_buckets):
            results.append({
                "symbol": sym,
                "time_bucket": bucket,
                "abnormality_score": round(random.uniform(0, 100), 1),
                "volume": random.randint(100000, 5000000),
                "trade_count": random.randint(10, 500),
                "buy_pressure": round(random.uniform(30, 70), 1),
            })

    return {"heatmap": results, "time_buckets": time_buckets}


# ============================================================================
# Alert Engine with Routing
# ============================================================================

class AlertState(BaseModel):
    """Alert state machine."""
    id: str
    timestamp: str
    symbol: str
    alert_type: str
    severity: Literal["low", "medium", "high", "critical"]
    state: Literal["new", "acknowledged", "snoozed", "resolved"]
    routing_status: Literal["pending", "sent", "failed"]
    dedup_reason: str | None = None
    channel: str | None = None


@app.get("/alerts/trigger-log")
async def get_alert_trigger_log(
    limit: int = Query(50),
    state: str = Query(None),
):
    """Get alert trigger log."""
    import random

    random.seed(200)
    alerts = []

    symbols = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]

    for _ in range(limit):
        alerts.append({
            "id": f"alert_{random.randint(1000, 9999)}",
            "timestamp": datetime.now().isoformat(),
            "symbol": random.choice(symbols),
            "alert_type": random.choice(["whale", "anomaly", "level", "spread"]),
            "severity": random.choice(["low", "medium", "high", "critical"]),
            "state": random.choice(["new", "acknowledged", "snoozed", "resolved"]),
            "routing_status": random.choice(["pending", "sent", "failed"]),
            "dedup_reason": random.choice([None, "size_similar", "time_window", "same_ticker"]),
            "channel": random.choice(["discord", "slack", "teams", "telegram", "email", "sms"]),
        })

    alerts.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"alerts": alerts, "count": len(alerts)}


@app.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(alert_id: str):
    """Acknowledge an alert."""
    return {"success": True, "alert_id": alert_id, "state": "acknowledged"}


@app.post("/alerts/{alert_id}/snooze")
async def snooze_alert(alert_id: str, duration: int = Query(15)):
    """Snooze an alert."""
    return {"success": True, "alert_id": alert_id, "state": "snoozed", "duration_minutes": duration}


# ============================================================================
# Watchlists
# ============================================================================

class WatchlistItem(BaseModel):
    """Watchlist item."""
    symbol: str
    added_at: str
    notes: str | None = None
    threshold: int | None = None


@app.get("/watchlists")
async def get_watchlists(
    user_id: str = Query("default"),
):
    """Get user/team watchlists."""
    return {
        "watchlists": [
            {
                "id": "default",
                "name": "MAG7 Watchlist",
                "owner": "user",
                "symbols": ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"],
                "filters": [{"field": "size", "op": ">", "value": 10000}],
                "created_at": datetime.now().isoformat(),
            },
            {
                "id": "whales",
                "name": "Whale Alerts",
                "owner": "team",
                "symbols": ["NVDA", "META", "TSLA"],
                "filters": [{"field": "size", "op": ">", "value": 50000}],
                "created_at": datetime.now().isoformat(),
            },
            {
                "id": "earnings",
                "name": "Earnings Plays",
                "owner": "user",
                "symbols": ["AAPL", "MSFT", "AMZN"],
                "filters": [{"field": "iv", "op": ">", "value": 50}],
                "created_at": datetime.now().isoformat(),
            },
        ]
    }


@app.post("/watchlists")
async def create_watchlist(
    name: str = Query(...),
    symbols: list[str] = Query([]),
):
    """Create a new watchlist."""
    import uuid
    return {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "symbols": symbols,
        "created_at": datetime.now().isoformat(),
    }


# ============================================================================
# Reports & Export
# ============================================================================

@app.get("/reports/daily")
async def get_daily_report(
    date: str = Query(None, description="YYYY-MM-DD"),
):
    """Generate daily recap report."""
    import random
    random.seed(hash(date or "today"))

    return {
        "date": date or datetime.now().strftime("%Y-%m-%d"),
        "total_trades": random.randint(50000, 500000),
        "total_volume": random.randint(100000000, 1000000000),
        "whale_events": random.randint(100, 5000),
        "active_tickers": 7,
        "top_tickers": [
            {"symbol": "NVDA", "volume": random.randint(10000000, 50000000)},
            {"symbol": "AAPL", "volume": random.randint(10000000, 50000000)},
            {"symbol": "META", "volume": random.randint(5000000, 20000000)},
        ],
        "compliance": {
            "watermark": f"DARKPOOL-{date or 'TODAY'}-{random.randint(1000, 9999)}",
            "exported_at": datetime.now().isoformat(),
        },
    }


@app.get("/reports/export")
async def export_data(
    format: str = Query("csv", description="csv or json"),
    start_date: str = Query(None),
    end_date: str = Query(None),
    symbols: str = Query(None),
):
    """Export data with compliance watermarking."""
    import random

    data = []
    for _ in range(100):
        data.append({
            "timestamp": datetime.now().isoformat(),
            "symbol": random.choice(["NVDA", "AAPL", "MSFT"]),
            "side": random.choice(["BUY", "SELL"]),
            "size": random.randint(1000, 100000),
            "price": round(random.uniform(50, 500), 2),
        })

    return {
        "data": data,
        "format": format,
        "compliance": {
            "watermark": f"EXPORT-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000, 9999)}",
            "exported_by": "user",
            "exported_at": datetime.now().isoformat(),
        },
    }


# ============================================================================
# System Health
# ============================================================================

@app.get("/health/system")
async def get_system_health():
    """Get system health metrics."""
    return {
        "feed_lag_ms": 45,
        "dropped_events": 12,
        "parser_errors": 3,
        "connectors": [
            {"name": "FINRA", "status": "healthy", "uptime_pct": 99.98},
            {"name": "Polygon", "status": "healthy", "uptime_pct": 99.99},
            {"name": "Intrinio", "status": "offline", "uptime_pct": 0},
        ],
        "memory_usage_mb": 256,
        "cpu_usage_pct": 23,
    }


# ============================================================================
# Replay/Backtest
# ============================================================================

@app.get("/replay/events")
async def get_replay_events(
    start_time: str = Query(...),
    end_time: str = Query(...),
    speed: float = Query(1.0),
):
    """Get events for replay."""
    import random

    random.seed(hash(start_time))
    events = []

    for _ in range(min(500, int((hash(end_time) - hash(start_time)) % 1000) + 100)):
        events.append({
            "timestamp": datetime.now().isoformat(),
            "symbol": random.choice(["NVDA", "AAPL", "MSFT"]),
            "size": random.randint(1000, 100000),
            "price": round(random.uniform(50, 500), 2),
            "side": random.choice(["BUY", "SELL"]),
        })

    return {"events": events, "count": len(events), "speed": speed}


# ============================================================================
# Ticker Deep Dive
# ============================================================================

@app.get("/ticker/{symbol}/deep-dive")
async def get_ticker_deep_dive(
    symbol: str,
    period: str = Query("1D"),
):
    """Get detailed ticker analysis."""
    import random

    random.seed(hash(symbol))
    now = datetime.now()

    # Volume profile
    volume_profile = []
    for i in range(20):
        volume_profile.append({
            "price_level": round(100 + i * 5, 2),
            "volume": random.randint(10000, 100000),
            "trades": random.randint(100, 1000),
        })

    # Repeated levels
    repeated_levels = [
        {"price": round(random.uniform(80, 200), 2), "occurrences": random.randint(5, 50)}
        for _ in range(10)
    ]

    return {
        "symbol": symbol,
        "period": period,
        "volume_profile": volume_profile,
        "repeated_levels": sorted(repeated_levels, key=lambda x: x["occurrences"], reverse=True),
        "news": [
            {"timestamp": now.isoformat(), "headline": f"{symbol} earnings call", "sentiment": random.choice(["positive", "negative", "neutral"])},
            {"timestamp": now.isoformat(), "headline": f"{symbol} upgrade", "sentiment": "positive"},
        ],
    }


# ============================================================================
# Advanced Visualizations
# ============================================================================

@app.get("/chart/heatmap")
async def get_chart_heatmap(
    symbols: str = Query(None),
    buckets: int = Query(13),
):
    """Flow Map heatmap data for visualization."""
    import random

    random.seed(300)
    results = []

    syms = symbols.split(',') if symbols else list(MAG7_STOCKS.keys())

    for sym in syms:
        base = MAG7_STOCKS.get(sym.strip(), {"basePrice": 100})
        for bucket in range(buckets):
            score = random.uniform(0, 100)
            results.append({
                "symbol": sym,
                "bucket": bucket,
                "score": round(score, 1),
                "volume": random.randint(100000, 5000000),
                "trades": random.randint(10, 500),
                "buy_pressure": round(random.uniform(30, 70), 1),
                "color": f"hsl({int(120 - score * 1.2)}, 70%, 50%)",
            })

    return {"heatmap": results, "buckets": buckets}


@app.get("/chart/candlestick")
async def get_candlestick_data(
    symbol: str = Query(...),
    period: str = Query("1D"),
    interval: str = Query("5m"),
):
    """Candlestick OHLC data."""
    import random

    random.seed(hash(symbol + period + interval))
    base = MAG7_STOCKS.get(symbol, {"basePrice": 100})
    price = base.get("basePrice", 100)

    candles = []
    for i in range(78):  # 5-min intervals in 6.5hr session
        open_price = price * (1 + random.uniform(-0.02, 0.02))
        high = open_price * (1 + random.uniform(0, 0.03))
        low = open_price * (1 - random.uniform(0, 0.03))
        close = open_price * (1 + random.uniform(-0.02, 0.02))
        volume = random.randint(10000, 100000)

        candles.append({
            "time": i * 5,
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": volume,
        })
        price = close

    return {"symbol": symbol, "period": period, "interval": interval, "candles": candles}


@app.get("/chart/volume-profile")
async def get_volume_profile(
    symbol: str = Query(...),
    bins: int = Query(20),
):
    """Volume profile by price level."""
    import random

    random.seed(hash(symbol))
    base = MAG7_STOCKS.get(symbol, {"basePrice": 100})
    price = base.get("basePrice", 100)

    profile = []
    for i in range(bins):
        bin_price = price * (0.9 + i * 0.02)
        profile.append({
            "price": round(bin_price, 2),
            "volume": random.randint(10000, 100000),
            "trades": random.randint(100, 1000),
            "pip": "green" if random.random() > 0.5 else "red",
        })

    profile.sort(key=lambda x: x["volume"], reverse=True)
    return {"symbol": symbol, "profile": profile}


# ============================================================================
# API Key Management
# ============================================================================

class APIKey(BaseModel):
    """API key configuration."""
    id: str
    provider: str
    key_masked: str
    status: str
    created_at: str
    last_used: str | None = None


@app.get("/admin/api-keys")
async def get_api_keys():
    """List API keys."""
    return {
        "keys": [
            {"id": "polygon_1", "provider": "polygon", "key_masked": "pol_****1234", "status": "active", "created_at": "2024-01-15T10:00:00Z", "last_used": "2024-01-20T14:30:00Z"},
            {"id": "intrinio_1", "provider": "intrinio", "key_masked": "int_****5678", "status": "inactive", "created_at": "2024-01-10T09:00:00Z", "last_used": None},
        ]
    }


@app.post("/admin/api-keys")
async def create_api_key(
    provider: str = Query(...),
    api_key: str = Query(...),
):
    """Create new API key."""
    import uuid
    return {"id": str(uuid.uuid4())[:8], "provider": provider, "key_masked": f"{provider[:3]}_****{api_key[-4:]}", "status": "active", "created_at": datetime.now().isoformat()}


@app.delete("/admin/api-keys/{key_id}")
async def delete_api_key(key_id: str):
    """Delete API key."""
    return {"success": True, "key_id": key_id}


# ============================================================================
# Compliance & Audit
# ============================================================================

class AuditLog(BaseModel):
    """Audit log entry."""
    id: str
    timestamp: str
    user: str
    action: str
    details: str
    ip_address: str


class RetentionPolicy(BaseModel):
    """Data retention policy."""
    id: str
    name: str
    duration_days: int
    auto_delete: bool


@app.get("/admin/audit-log")
async def get_audit_log(
    limit: int = Query(50),
):
    """Get audit log."""
    import random

    random.seed(400)
    actions = ["login", "export", "view_ticker", "create_alert", "update_settings", "create_watchlist"]
    now = datetime.now()

    logs = []
    for index in range(limit):
        logs.append({
            "id": f"audit_{index + 1:04d}",
            "timestamp": (now - timedelta(seconds=index * 17)).isoformat(),
            "user": random.choice(["user", "admin", "analyst"]),
            "action": random.choice(actions),
            "details": f"Action details for audit",
            "ip_address": f"192.168.1.{random.randint(1, 254)}",
        })

    logs.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"logs": logs, "count": len(logs)}


@app.get("/admin/retention")
async def get_retention_policies():
    """Get retention policies."""
    return {
        "policies": [
            {"id": "r1", "name": "Raw Trades", "duration_days": 30, "auto_delete": True},
            {"id": "r2", "name": "Alerts", "duration_days": 90, "auto_delete": True},
            {"id": "r3", "name": "Export History", "duration_days": 365, "auto_delete": False},
        ]
    }


@app.post("/admin/retention")
async def create_retention_policy(
    name: str = Query(...),
    duration_days: int = Query(30),
    auto_delete: bool = Query(True),
):
    """Create retention policy."""
    import uuid
    return {"id": str(uuid.uuid4())[:8], "name": name, "duration_days": duration_days, "auto_delete": auto_delete}


# ============================================================================
# Keyboard Shortcuts Config
# ============================================================================

class KeyboardShortcut(BaseModel):
    """Keyboard shortcut."""
    key: str
    modifiers: list[str]
    action: str
    description: str


@app.get("/config/shortcuts")
async def get_keyboard_shortcuts():
    """Get keyboard shortcuts."""
    return {
        "shortcuts": [
            {"key": "f", "modifiers": [], "action": "focus_search", "description": "Focus search"},
            {"key": "s", "modifiers": ["ctrl"], "action": "save_filter", "description": "Save current filter"},
            {"key": "e", "modifiers": ["ctrl"], "action": "export", "description": "Export data"},
            {"key": "1-7", "modifiers": [], "action": "switch_ticker", "description": "Switch to ticker 1-7"},
            {"key": "space", "modifiers": [], "action": "toggle_pause", "description": "Pause/resume feed"},
            {"key": "/", "modifiers": [], "action": "command_palette", "description": "Open command palette"},
            {"key": "esc", "modifiers": [], "action": "close_modal", "description": "Close modal"},
        ]
    }


# ============================================================================
# Phase 3: Anomaly Detection Engine
# ============================================================================

import statistics


class AnomalyDetector:
    """Z-score based anomaly detection"""
    
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.price_history = {}
        self.volume_history = {}
    
    def calculate_z_score(self, symbol: str, value: float) -> float:
        """Calculate z-score for a value"""
        history = self.price_history.get(symbol, [])
        
        if len(history) < 10:
            return 0.0
        
        mean = statistics.mean(history)
        stdev = statistics.stdev(history) if len(history) > 1 else 1
        
        return (value - mean) / stdev if stdev > 0 else 0.0
    
    def calculate_adv_pct(self, size: int, adv: float) -> float:
        """Calculate % of average daily volume"""
        return (size / adv) * 100 if adv > 0 else 0.0
    
    def update_baseline(self, symbol: str, price: float, volume: int):
        if symbol not in self.price_history:
            self.price_history[symbol] = []
            self.volume_history[symbol] = []
        
        self.price_history[symbol].append(price)
        self.volume_history[symbol].append(volume)
        
        if len(self.price_history[symbol]) > self.window_size:
            self.price_history[symbol].pop(0)
        if len(self.volume_history[symbol]) > self.window_size:
            self.volume_history[symbol].pop(0)
    
    def detect(self, symbol: str, price: float, size: int, adv: float = 1000000) -> dict:
        z_score = self.calculate_z_score(symbol, price)
        adv_pct = self.calculate_adv_pct(size, adv)
        
        return {
            "is_anomalous": abs(z_score) > 2 or adv_pct > 5,
            "z_score": round(z_score, 2),
            "adv_pct": round(adv_pct, 2),
        }


anomaly_detector = AnomalyDetector()


@app.get("/analysis/zscore")
async def get_zscore(symbol: str = Query(...), value: float = Query(...)):
    return {"symbol": symbol, "value": value, "z_score": round(anomaly_detector.calculate_z_score(symbol, value), 2)}


@app.get("/analysis/anomalies")
async def get_anomalies(symbol: str = Query(...), price: float = Query(...), size: int = Query(...), adv: float = Query(1000000)):
    result = anomaly_detector.detect(symbol, price, size, adv)
    anomaly_detector.update_baseline(symbol, price, size)
    return {"symbol": symbol, **result}


@app.get("/analysis/baseline")
async def get_baseline(symbol: str = Query(...)):
    prices = anomaly_detector.price_history.get(symbol, [])
    if not prices:
        return {"symbol": symbol, "status": "no_data"}
    return {
        "symbol": symbol, "samples": len(prices),
        "mean": round(statistics.mean(prices), 2),
        "stdev": round(statistics.stdev(prices), 2) if len(prices) > 1 else 0,
    }


# ============================================================================
# Discord Bot Webhook Endpoint
# ============================================================================

class AlertWebhook(BaseModel):
    """Webhook alert payload."""

    symbol: str
    direction: Literal["BUY", "SELL"]
    size: int
    price: float
    alert_type: Literal["whale", "anomaly", "level"]


@app.post("/alerts/webhook")
async def send_alertWebhook(
    alert: AlertWebhook,
    webhook_url: str = Query(..., description="Discord webhook URL"),
):
    import httpx

    embed = {
        "title": f"🚨 Darkpool Alert: {alert.symbol}",
        "description": f"**{alert.direction}** {alert.size:,} shares @ ${alert.price:.2f}",
        "color": 0x00FF00 if alert.direction == "BUY" else 0xFF0000,
        "fields": [
            {"name": "Type", "value": alert.alert_type, "inline": True},
            {"name": "Notional", "value": f"${alert.size * alert.price:,.0f}", "inline": True},
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }

    payload = {
        "content": f"Darkpool {'🐋 Whale' if alert.alert_type == 'whale' else '📊'} Alert",
        "embeds": [embed],
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(webhook_url, json=payload, timeout=10.0)

    if response.status_code == 204:
        return {"status": "sent"}
    raise HTTPException(response.status_code, "Failed to send webhook")


# ============================================================================
# Slash Command Handler
# ============================================================================

class SlashCommand(BaseModel):
    """Discord slash command payload."""

    id: str
    type: int
    data: dict
    member: dict | None = None
    guild_id: str | None = None
    channel_id: str | None = None


DISCORD_EPHEMERAL_FLAG = 64


def discord_provider_error_response(exc: ProviderError) -> dict:
    """Return a Discord interaction response for provider selection failures."""
    return {
        "type": 4,
        "data": {
            "content": f"Provider error: {exc}",
            "flags": DISCORD_EPHEMERAL_FLAG,
        },
    }


@app.get("/discord/watchlist-summary")
async def get_discord_watchlist_summary(
    symbols: str = Query(..., description="Comma-separated tickers"),
    provider: str = Query("demo"),
):
    """Build the same watchlist summary used by Discord commands."""
    parsed = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    try:
        summary = await build_watchlist_summary(parsed, provider=provider)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))
    return {
        "summary": {
            "command": summary.command,
            "symbol": summary.symbol,
            "title": summary.title,
            "description": summary.description,
            "metrics": summary.metrics,
            "sections": [
                {"title": section.title, "items": section.items}
                for section in summary.sections
            ],
        }
    }


@app.post("/discord/subscriptions")
async def create_discord_subscription(
    channel_id: str = Query(...),
    topic: str = Query(..., pattern="^(alerts|levels|confluence|watchlist)$"),
    symbols: str = Query(..., description="Comma-separated tickers"),
    min_score: float = Query(70, ge=0, le=100),
    provider: str = Query("demo"),
):
    """Subscribe a Discord channel to an autopost topic."""
    subscription = subscription_store.create(
        channel_id=channel_id,
        topic=topic,
        symbols=[symbol.strip() for symbol in symbols.split(",")],
        min_score=min_score,
        provider=provider,
    )
    return {"subscription": subscription.to_dict()}


@app.get("/discord/subscriptions")
async def list_discord_subscriptions(channel_id: str | None = Query(None)):
    """List Discord autopost subscriptions."""
    return {"subscriptions": [subscription.to_dict() for subscription in subscription_store.list(channel_id=channel_id)]}


@app.delete("/discord/subscriptions/{subscription_id}")
async def delete_discord_subscription(subscription_id: str, channel_id: str | None = Query(None)):
    """Delete a Discord autopost subscription."""
    return {"deleted": subscription_store.delete(subscription_id, channel_id=channel_id)}


@app.post("/discord/commands")
async def handle_slash_command(request: Request):
    """Handle Discord slash commands."""
    body = await request.body()
    verifier = DiscordSignatureVerifier(
        public_key=os.getenv("DISCORD_PUBLIC_KEY", ""),
        allow_unsigned=os.getenv("ALLOW_UNSIGNED_DISCORD_INTERACTIONS", "false").lower() == "true",
    )
    try:
        verifier.verify(
            timestamp=request.headers.get("X-Signature-Timestamp", ""),
            body=body,
            signature=request.headers.get("X-Signature-Ed25519", ""),
        )
    except SignatureVerificationError:
        raise HTTPException(401, "Invalid Discord signature")

    command = SlashCommand.model_validate_json(body)
    cmd_name = command.data.get("name", "")
    options = {opt["name"]: opt.get("value") for opt in command.data.get("options", [])}
    symbol = options.get("symbol", "AAPL")
    provider = options.get("provider", "demo")

    builders = {
        "darkpool": build_darkpool_summary,
        "levels": build_levels_summary,
        "confluence": build_confluence_summary,
        "alerts": build_alerts_summary,
    }

    if cmd_name in builders:
        try:
            summary = await builders[cmd_name](symbol, provider=provider)
        except ProviderError as exc:
            return discord_provider_error_response(exc)
        return {"type": 4, "data": {"embeds": [summary_to_embed(summary)]}}

    if cmd_name == "watchlist":
        symbols = options.get("symbols", "AAPL,NVDA,MSFT")
        try:
            summary = await build_watchlist_summary([item.strip() for item in symbols.split(",")], provider=provider)
        except ProviderError as exc:
            return discord_provider_error_response(exc)
        return {"type": 4, "data": {"embeds": [summary_to_embed(summary)]}}

    if cmd_name == "subscribe":
        channel_id = command.channel_id or "unknown"
        topic = options.get("topic", "alerts")
        symbols = options.get("symbols", "AAPL,NVDA,MSFT")
        min_score = float(options.get("min_score", 70))
        subscription = subscription_store.create(
            channel_id=channel_id,
            topic=topic,
            symbols=[item.strip() for item in symbols.split(",")],
            min_score=min_score,
            provider=provider,
        )
        return {
            "type": 4,
            "data": {
                "content": (
                    f"Subscribed channel {channel_id} to {subscription.topic} for "
                    f"{', '.join(subscription.symbols)} at score >= {subscription.min_score:.0f}. "
                    f"ID: {subscription.id}"
                )
            },
        }

    if cmd_name == "subscriptions":
        channel_id = command.channel_id or "unknown"
        rows = subscription_store.list(channel_id=channel_id)
        if not rows:
            return {"type": 4, "data": {"content": "No subscriptions configured for this channel."}}
        return {
            "type": 4,
            "data": {
                "content": "\n".join(
                    f"{row.id}: {row.topic} | {', '.join(row.symbols)} | score >= {row.min_score:.0f}"
                    for row in rows
                )
            },
        }

    if cmd_name == "darkpool":
        symbol = options.get("symbol")
        tier = options.get("tier", "T1")
        provider = options.get("provider", "demo")
        data = await get_provider_records(symbol, provider)
        return {"type": 4, "data": {"content": f"📊 Darkpool Data for {symbol or 'ALL'}: {len(data):,} records (Tier {tier})"}}

    return {"type": 4, "data": {"content": "Unknown command"}}


# ============================================================================
# WebSocket Streaming (Phase 2 - Real-time)
# ============================================================================

from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import json


class ConnectionManager:
    """WebSocket connection manager for real-time updates"""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.subscriptions: dict = {}
    
    async def connect(self, websocket: WebSocket, channel: str = "default"):
        await websocket.accept()
        self.active_connections.append(websocket)
        if channel not in self.subscriptions:
            self.subscriptions[channel] = set()
        self.subscriptions[channel].add(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        for channel in self.subscriptions:
            self.subscriptions[channel].discard(websocket)
    
    async def broadcast(self, message: dict, channel: str = "default"):
        if channel in self.subscriptions:
            for conn in list(self.subscriptions[channel]):
                try:
                    await conn.send_json(message)
                except Exception:
                    pass
    
    async def broadcast_transaction(self, transaction: dict):
        await self.broadcast({
            "type": "transaction",
            "data": transaction,
            "timestamp": datetime.now().isoformat(),
        }, "transactions")
    
    async def broadcast_alert(self, alert: dict):
        await self.broadcast({
            "type": "alert",
            "data": alert,
            "timestamp": datetime.now().isoformat(),
        }, "alerts")


ws_manager = ConnectionManager()


@app.websocket("/ws/transactions")
async def websocket_transactions(websocket: WebSocket):
    """WebSocket for real-time transactions"""
    await ws_manager.connect(websocket, "transactions")
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    """WebSocket for real-time alerts"""
    await ws_manager.connect(websocket, "alerts")
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.websocket("/ws/health")
async def websocket_health(websocket: WebSocket):
    """WebSocket for system health"""
    await ws_manager.connect(websocket, "health")
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "feed_lag_ms": 45,
                    "timestamp": datetime.now().isoformat(),
                })
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


async def broadcast_mock_transactions():
    """Broadcast mock transactions"""
    import random
    while True:
        await asyncio.sleep(3)
        txn = {
            "id": f"evt_{random.randint(100000, 999999)}",
            "symbol": random.choice(["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]),
            "side": random.choice(["BUY", "SELL"]),
            "size": random.randint(1000, 100000),
            "price": round(random.uniform(50, 500), 2),
            "venue": random.choice(["BATS", "NYSE", "CBOE", "MEMX"]),
            "timestamp": datetime.now().isoformat(),
        }
        await ws_manager.broadcast_transaction(txn)


# ============================================================================
# Phase 4: Alert Routing with Deduplication
# ============================================================================

from collections import defaultdict
from datetime import datetime, timedelta


class AlertRouter:
    """Alert routing with deduplication"""
    
    def __init__(self, dedup_window_seconds: int = 60):
        self.dedup_window = timedelta(seconds=dedup_window_seconds)
        self.recent_alerts = defaultdict(list)
        self.routing_history = []
        self.channels = {
            "discord": self.send_discord,
            "slack": self.send_slack,
            "teams": self.send_teams,
            "telegram": self.send_telegram,
            "email": self.send_email,
        }
    
    def should_send(self, symbol: str, alert_type: str, size: int = 0) -> tuple[bool, str]:
        """Check if alert should be sent (dedup)"""
        now = datetime.now()
        key = f"{symbol}:{alert_type}"
        
        # Check recent alerts
        recent = self.recent_alerts[key]
        recent = [t for t in recent if now - t < self.dedup_window]
        
        if recent:
            # Check if similar size (within 20%)
            for rt in recent:
                if abs(size) > 0:  # size-based dedup
                    return False, "size_window"
                else:
                    return False, "time_window"
        
        self.recent_alerts[key] = recent + [now]
        return True, None
    
    async def route(self, alert: dict, channel: str):
        """Route alert to specified channel"""
        if channel not in self.channels:
            raise ValueError(f"Unknown channel: {channel}")
        
        sender = self.channels[channel]
        success = await sender(alert)
        now = datetime.now()
        
        self.routing_history.append({
            "alert_id": alert.get("id"),
            "channel": channel,
            "timestamp": now.isoformat(),
            "success": success,
        })
        
        return success
    
    async def send_discord(self, alert: dict) -> bool:
        import httpx
        # Placeholder - real implementation with webhook URL
        return True
    
    async def send_slack(self, alert: dict) -> bool:
        return True
    
    async def send_teams(self, alert: dict) -> bool:
        return True
    
    async def send_telegram(self, alert: dict) -> bool:
        return True
    
    async def send_email(self, alert: dict) -> bool:
        return True


alert_router = AlertRouter()


@app.get("/alerts/routing-status")
async def get_routing_status(limit: int = Query(50)):
    history = alert_router.routing_history[-limit:]
    return {"history": history, "count": len(history)}


@app.post("/alerts/route")
async def route_alert(
    symbol: str = Query(...),
    alert_type: str = Query(...),
    channel: str = Query("discord"),
    size: int = Query(0),
):
    should_send, reason = alert_router.should_send(symbol, alert_type, size)
    
    if not should_send:
        return {"status": "deduplicated", "reason": reason}
    
    alert = {"symbol": symbol, "type": alert_type, "size": size}
    success = await alert_router.route(alert, channel)
    
    return {"status": "sent" if success else "failed"}


# ============================================================================
# Phase 5: Historical Analytics & Storage
# ============================================================================

# Time-series storage placeholder (PostgreSQL/TimescaleDB ready)
class HistoricalStore:
    """Historical data storage"""
    
    def __init__(self):
        self.transactions = []
        self.alerts = []
        self.max_store = 10000
    
    def store_transaction(self, txn: dict):
        self.transactions.append({**txn, "stored_at": datetime.now().isoformat()})
        if len(self.transactions) > self.max_store:
            self.transactions.pop(0)
    
    def get_range(self, symbol: str, start: str, end: str) -> list[dict]:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        
        return [
            t for t in self.transactions
            if t.get("symbol") == symbol
            and start_dt <= datetime.fromisoformat(t.get("timestamp", ""))
            <= end_dt
        ]
    
    def get_daily_summary(self, date: str) -> dict:
        return {
            "date": date,
            "total_transactions": len(self.transactions),
            "total_volume": sum(t.get("size", 0) for t in self.transactions),
        }


historical_store = HistoricalStore()


@app.get("/history/range")
async def get_history(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
):
    return {"transactions": historical_store.get_range(symbol, start, end)}


@app.get("/history/daily")
async def get_daily_summary(date: str = Query(None)):
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    return historical_store.get_daily_summary(date)


@app.get("/history/summary")
async def get_history_summary(days: int = Query(7)):
    end = datetime.now()
    start = end - timedelta(days=days)
    
    return {
        "days": days,
        "total_stored": len(historical_store.transactions),
        "date_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    }



# ============================================================================
# Ingestion Service: TRF/ATS Normalization
# ============================================================================

class PrintNormalizer:
    ATS_EXCHANGES = {"A": "BATS", "J": "CBOE", "K": "MEMX", "Y": "IEX", "Z": "NASDAQ"}
    TRF_CODES = {"F": "FINRA/NASDAQ TRF", "X": "FINRA/NYSE TRF", "C": "CBOE TRF", "B": "BATS TRF"}
    
    def __init__(self):
        self.stats = {"ats": 0, "trf": 0, "exchange": 0}
    
    def normalize(self, raw_print: dict) -> dict:
        exchange = raw_print.get("exchange", "")
        market = raw_print.get("market", "")
        
        if exchange in self.ATS_EXCHANGES:
            feed_type, venue = "ats", self.ATS_EXCHANGES.get(exchange, exchange)
            self.stats["ats"] += 1
        elif exchange in self.TRF_CODES or market == "TRF":
            feed_type, venue = "trf", self.TRF_CODES.get(exchange, "TRF")
            self.stats["trf"] += 1
        else:
            feed_type, venue = "exchange", exchange or market or "UNKNOWN"
            self.stats["exchange"] += 1
        
        size, price = raw_print.get("size", 0), raw_print.get("price", 0)
        notional = size * price
        
        return {**raw_print, "feed_type": feed_type, "venue": venue,
            "notional": round(notional, 2), "is_whale": size >= 50000 or notional >= 1000000,
            "is_block": size >= 10000, "normalized_at": datetime.now().isoformat()}
    
    def get_stats(self) -> dict:
        return {**self.stats, "total": sum(self.stats.values())}


normalizer = PrintNormalizer()

@app.get("/normalize/stats")
async def get_normalizer_stats():
    return normalizer.get_stats()


# ============================================================================
# Database Schema (TimescaleDB Ready)
# ============================================================================

@app.get("/schema/database")
async def get_database_schema():
    return {"schema": """
CREATE TABLE darkpool_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(10) NOT NULL, side VARCHAR(4) NOT NULL,
    size INTEGER NOT NULL, price DECIMAL(10,2) NOT NULL,
    venue VARCHAR(50), feed_type VARCHAR(10), source VARCHAR(20),
    notional DECIMAL(15,2), is_whale BOOLEAN DEFAULT FALSE,
    is_block BOOLEAN DEFAULT FALSE, metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SELECT create_hypertable('darkpool_transactions', 'created_at');
CREATE INDEX idx_symbol ON darkpool_transactions (symbol, created_at DESC);
CREATE TABLE darkpool_minutes (
    time_bucket TIMESTAMPTZ NOT NULL, symbol VARCHAR(10) NOT NULL,
    buy_volume INTEGER, sell_volume INTEGER, trade_count INTEGER,
    avg_price DECIMAL(10,2), notional DECIMAL(15,2), whale_count INTEGER,
    PRIMARY KEY (time_bucket, symbol)
);
CREATE TABLE watchlist_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL, symbols JSONB NOT NULL, filters JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""}


# ============================================================================
# User Watchlists (Database-backed)
# ============================================================================

class UserWatchlist:
    def __init__(self):
        self.watchlists = {}
    
    def create(self, user_id: str, name: str, symbols: list, filters: dict = None):
        if user_id not in self.watchlists:
            self.watchlists[user_id] = []
        wl = {"id": secrets.token_urlsafe(8), "name": name, "symbols": symbols,
             "filters": filters or {}, "created_at": datetime.now().isoformat()}
        self.watchlists[user_id].append(wl)
        return wl
    
    def get_user(self, user_id: str) -> list:
        return self.watchlists.get(user_id, [])
    
    def delete(self, user_id: str, watchlist_id: str) -> bool:
        wls = self.watchlists.get(user_id, [])
        for i, wl in enumerate(wls):
            if wl["id"] == watchlist_id:
                wls.pop(i)
                return True
        return False


user_watchlists = UserWatchlist()

@app.post("/watchlist/user/create")
async def create_user_watchlist(name: str = Query(...), symbols: str = Query(...), token: str = Query(...)):
    user_data = decode_token(token)
    if not user_data:
        raise HTTPException(401, "Invalid token")
    symbols_list = [s.strip().upper() for s in symbols.split(",")]
    return {"watchlist": user_watchlists.create(user_data["user_id"], name, symbols_list)}

@app.get("/watchlist/user")
async def get_user_watchlists(token: str = Query(...)):
    user_data = decode_token(token)
    if not user_data:
        raise HTTPException(401, "Invalid token")
    return {"watchlists": user_watchlists.get_user(user_data["user_id"])}

@app.delete("/watchlist/user/{watchlist_id}")
async def delete_user_watchlist(watchlist_id: str, token: str = Query(...)):
    user_data = decode_token(token)
    if not user_data:
        raise HTTPException(401, "Invalid token")
    success = user_watchlists.delete(user_data["user_id"], watchlist_id)
    if not success:
        raise HTTPException(404, "Watchlist not found")
    return {"status": "deleted"}


# ============================================================================
# Server-Side Alert Processing
# ============================================================================

class ServerAlertProcessor:
    def __init__(self):
        self.thresholds = {}
    
    def add_threshold(self, user_id: str, symbol: str, min_size: int, min_dollars: int, channel: str):
        if user_id not in self.thresholds:
            self.thresholds[user_id] = []
        self.thresholds[user_id].append({"id": secrets.token_urlsafe(8), "symbol": symbol,
            "min_size": min_size, "min_dollars": min_dollars, "channel": channel,
            "created_at": datetime.now().isoformat()})
    
    def add_webhook(self, user_id: str, webhook_url: str):
        if user_id not in self.thresholds:
            self.thresholds[user_id] = []
        self.thresholds[user_id].append({"type": "webhook", "url": webhook_url,
            "created_at": datetime.now().isoformat()})
    
    def check_trade(self, symbol: str, size: int, price: float) -> list:
        triggered, notional = [], size * price
        for user_id, thresholds in self.thresholds.items():
            for th in thresholds:
                if th.get("symbol") != symbol:
                    continue
                if size >= th.get("min_size", 0) or notional >= th.get("min_dollars", 0):
                    triggered.append({"user_id": user_id, "threshold_id": th["id"], "symbol": symbol,
                        "size": size, "price": price, "notional": notional,
                        "channel": th.get("channel"), "timestamp": datetime.now().isoformat()})
        return triggered
    
    def get_thresholds(self, user_id: str) -> list:
        return self.thresholds.get(user_id, [])


alert_processor = ServerAlertProcessor()

@app.post("/alerts/server/threshold")
async def add_server_threshold(symbol: str = Query(...), min_size: int = Query(10000),
    min_dollars: int = Query(500000), channel: str = Query("discord"), token: str = Query(...)):
    user_data = decode_token(token)
    if not user_data:
        raise HTTPException(401, "Invalid token")
    alert_processor.add_threshold(user_data["user_id"], symbol, min_size, min_dollars, channel)
    return {"status": "added"}

@app.post("/alerts/server/webhook")
async def add_alert_webhook(webhook_url: str = Query(...), token: str = Query(...)):
    user_data = decode_token(token)
    if not user_data:
        raise HTTPException(401, "Invalid token")
    alert_processor.add_webhook(user_data["user_id"], webhook_url)
    return {"status": "added"}

@app.get("/alerts/server/thresholds")
async def get_server_thresholds(token: str = Query(...)):
    user_data = decode_token(token)
    if not user_data:
        raise HTTPException(401, "Invalid token")
    return {"thresholds": alert_processor.get_thresholds(user_data["user_id"])}


def create_app() -> FastAPI:
    return app


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8002"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)

