/**
 * Sentinel Flare - Type Schemas & API Contracts
 * 
 * Provides typed schemas for all data structures.
 * Used for validation, documentation, and code generation.
 */

export const TransactionSchema = {
  type: 'object',
  required: ['id', 'symbol', 'side', 'size', 'price', 'venue', 'timestamp'],
  properties: {
    id: { type: 'string', description: 'Unique transaction ID' },
    symbol: { type: 'string', enum: ['NVDA', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'TSLA'] },
    side: { type: 'string', enum: ['BUY', 'SELL'] },
    size: { type: 'integer', minimum: 1 },
    price: { type: 'number', minimum: 0 },
    venue: { type: 'string' },
    timestamp: { type: 'string', format: 'date-time' },
    source: { type: 'string' },
  },
};

export const ScannerPrintSchema = {
  type: 'object',
  required: ['id', 'symbol', 'side', 'size', 'price', 'venue', 'feed_type'],
  properties: {
    id: { type: 'string' },
    symbol: { type: 'string' },
    side: { type: 'string', enum: ['BUY', 'SELL'] },
    size: { type: 'integer' },
    price: { type: 'number' },
    venue: { type: 'string' },
    feed_type: { type: 'string', enum: ['tape_a', 'tape_b', 'tape_c'] },
    timestamp: { type: 'string', format: 'date-time' },
    received_at: { type: 'string', format: 'date-time' },
    source: { type: 'string' },
    confidence: { type: 'number', minimum: 0, maximum: 1 },
    latency_ms: { type: 'integer' },
    z_score: { type: 'number' },
    adv_pct: { type: 'number' },
  },
};

export const AlertSchema = {
  type: 'object',
  required: ['id', 'timestamp', 'symbol', 'alert_type', 'severity', 'state'],
  properties: {
    id: { type: 'string' },
    timestamp: { type: 'string', format: 'date-time' },
    symbol: { type: 'string' },
    alert_type: { type: 'string', enum: ['whale', 'anomaly', 'level', 'spread'] },
    severity: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] },
    state: { type: 'string', enum: ['new', 'acknowledged', 'snoozed', 'resolved'] },
    routing_status: { type: 'string', enum: ['pending', 'sent', 'failed'] },
    dedup_reason: { type: ['string', 'null'] },
    channel: { type: ['string', 'null'] },
  },
};

export const WatchlistSchema = {
  type: 'object',
  required: ['id', 'name', 'owner', 'symbols'],
  properties: {
    id: { type: 'string' },
    name: { type: 'string' },
    owner: { type: 'string', enum: ['user', 'team'] },
    symbols: { type: 'array', items: { type: 'string' } },
    filters: { 
      type: 'array', 
      items: {
        type: 'object',
        properties: {
          field: { type: 'string' },
          op: { type: 'string' },
          value: { type: ['number', 'string'] },
        },
      },
    },
    created_at: { type: 'string', format: 'date-time' },
  },
};

export const SystemHealthSchema = {
  type: 'object',
  required: ['feed_lag_ms', 'dropped_events', 'parser_errors'],
  properties: {
    feed_lag_ms: { type: 'integer' },
    dropped_events: { type: 'integer' },
    parser_errors: { type: 'integer' },
    connectors: { 
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          status: { type: 'string', enum: ['healthy', 'degraded', 'offline'] },
          uptime_pct: { type: 'number' },
        },
      },
    },
    memory_usage_mb: { type: 'integer' },
    cpu_usage_pct: { type: 'number' },
  },
};

export const SettingsSchema = {
  type: 'object',
  properties: {
    theme: { type: 'string', enum: ['settrader', 'cyberpunk', 'matrix', 'fire', 'monochrome'] },
    chartType: { type: 'string', enum: ['bar', 'area', 'line', 'candlestick'] },
    layout: { type: 'string', enum: ['grid', 'list', 'heatmap'] },
    cardSize: { type: 'string', enum: ['compact', 'normal', 'expanded'] },
    whaleThreshold: { type: 'integer', default: 50000 },
    selectedStock: { type: 'string' },
    timeframe: { type: 'string', enum: ['1H', '4H', '1D'] },
    threshold: { type: 'number', default: 1 },
  },
};

export const APIErrorSchema = {
  type: 'object',
  required: ['error'],
  properties: {
    error: { type: 'string' },
    code: { type: 'string' },
    details: { type: 'object' },
  },
};

// Export all schemas as named exports
export const AllSchemas = {
  Transaction: TransactionSchema,
  ScannerPrint: ScannerPrintSchema,
  Alert: AlertSchema,
  Watchlist: WatchlistSchema,
  SystemHealth: SystemHealthSchema,
  Settings: SettingsSchema,
  APIError: APIErrorSchema,
};

export default AllSchemas;
