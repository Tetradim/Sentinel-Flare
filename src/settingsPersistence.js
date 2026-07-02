import { DEFAULT_SETTINGS } from './themes';
import { VIEW_MODES, getViewModeLabel } from './viewModes';

export const DASHBOARD_CONTROL_DEFAULTS = {
  selectedStock: 'ALL',
  viewMode: 'dashboard',
  timeframe: DEFAULT_SETTINGS.timeframe,
  threshold: DEFAULT_SETTINGS.minTradeSize,
  whaleThreshold: DEFAULT_SETTINGS.whaleThreshold,
  feedSort: 'LATEST',
  isRunning: true,
};

const isPlainObject = (value) => (
  value !== null &&
  typeof value === 'object' &&
  !Array.isArray(value)
);

const numberOrDefault = (value, fallback) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
};

const VIEW_MODE_IDS = new Set(VIEW_MODES.map((mode) => mode.id));

const viewModeOrDefault = (value) => {
  const normalized = String(value || DASHBOARD_CONTROL_DEFAULTS.viewMode);
  return VIEW_MODE_IDS.has(normalized) ? normalized : DASHBOARD_CONTROL_DEFAULTS.viewMode;
};

export const normalizePersistedSettings = (persisted) => ({
  ...DEFAULT_SETTINGS,
  ...DASHBOARD_CONTROL_DEFAULTS,
  ...(isPlainObject(persisted) ? persisted : {}),
});

export const extractDashboardControls = (settings) => {
  const normalized = normalizePersistedSettings(settings);

  return {
    selectedStock: normalized.selectedStock || DASHBOARD_CONTROL_DEFAULTS.selectedStock,
    viewMode: viewModeOrDefault(normalized.viewMode),
    timeframe: normalized.timeframe || DASHBOARD_CONTROL_DEFAULTS.timeframe,
    threshold: numberOrDefault(normalized.threshold, DASHBOARD_CONTROL_DEFAULTS.threshold),
    whaleThreshold: numberOrDefault(normalized.whaleThreshold, DASHBOARD_CONTROL_DEFAULTS.whaleThreshold),
    feedSort: normalized.feedSort || DASHBOARD_CONTROL_DEFAULTS.feedSort,
    isRunning: typeof normalized.isRunning === 'boolean'
      ? normalized.isRunning
      : DASHBOARD_CONTROL_DEFAULTS.isRunning,
  };
};

export const mergeDashboardControls = (settings, controls) => ({
  ...normalizePersistedSettings(settings),
  ...extractDashboardControls({ ...settings, ...controls }),
});

export const serializeSettingsProfile = (settings, options = {}) => {
  const exportedAt = options.exportedAt || new Date().toISOString();

  return JSON.stringify(
    {
      app: 'Sentinel Flare',
      schemaVersion: 1,
      exportedAt,
      settings: normalizePersistedSettings(settings),
    },
    null,
    2
  );
};

export const parseSettingsProfile = (profileJson) => {
  try {
    const parsed = JSON.parse(profileJson);
    if (!isPlainObject(parsed)) {
      return { ok: false, error: 'Profile JSON must contain an object.' };
    }

    const settings = isPlainObject(parsed.settings) ? parsed.settings : parsed;
    return {
      ok: true,
      settings: normalizePersistedSettings(settings),
    };
  } catch {
    return {
      ok: false,
      error: 'Profile JSON could not be parsed.',
    };
  }
};

export const previewSettingsProfile = (profileJson) => {
  const result = parseSettingsProfile(profileJson);
  if (!result.ok) return result;

  return {
    ok: true,
    settings: result.settings,
    summary: summarizeSettingsProfile(result.settings),
  };
};

export const buildSettingsProfileImportState = (profileJson) => {
  const trimmed = String(profileJson || '').trim();
  if (!trimmed) {
    return {
      canApply: false,
      preview: null,
    };
  }

  const preview = previewSettingsProfile(trimmed);
  return {
    canApply: preview.ok,
    preview,
  };
};

const filenamePart = (value, fallback) => {
  const part = String(value || fallback)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

  return part || fallback;
};

export const buildSettingsProfileFilename = (settings = {}, date = new Date()) => {
  const normalized = normalizePersistedSettings(settings);
  const timestamp = date.toISOString()
    .replace(/\.\d{3}Z$/, '')
    .replace('T', '-')
    .replace(/:/g, '');

  return [
    'sentinel-flare-profile',
    filenamePart(normalized.selectedStock, 'all'),
    filenamePart(normalized.provider, 'provider'),
    timestamp,
  ].join('-') + '.json';
};

const formatFeedSort = (feedSort) => (
  String(feedSort || '').toUpperCase() === 'LARGEST' ? 'Largest first' : 'Latest first'
);

const DASHBOARD_FILTER_KEYS = [
  'selectedStock',
  'timeframe',
  'threshold',
  'whaleThreshold',
  'feedSort',
];

export const buildDashboardFilterChips = (controls = {}) => {
  const normalized = extractDashboardControls(controls);

  return [
    { label: 'Symbol', value: normalized.selectedStock },
    { label: 'Window', value: normalized.timeframe },
    { label: 'Min Print', value: `$${normalized.threshold}M+` },
    { label: 'Whale Gate', value: `${normalized.whaleThreshold}K+` },
    { label: 'Sort', value: formatFeedSort(normalized.feedSort) },
  ];
};

export const hasCustomDashboardFilters = (controls = {}) => {
  const normalized = extractDashboardControls(controls);

  return DASHBOARD_FILTER_KEYS.some(
    (key) => normalized[key] !== DASHBOARD_CONTROL_DEFAULTS[key]
  );
};

const summarizeAlertDelivery = (settings) => {
  if (settings.soundEnabled && settings.desktopAlerts) return 'Sound + desktop';
  if (settings.desktopAlerts) return 'Desktop only';
  if (settings.soundEnabled) return 'Sound only';
  return 'Muted';
};

const summarizeIntegrations = (settings) => {
  if (settings.grafanaUrl) return 'Grafana linked';
  if (settings.plotlyUrl) return 'Plotly linked';
  return 'No external dashboards';
};

export const summarizeSettingsProfile = (settings = {}) => {
  const normalized = normalizePersistedSettings(settings);
  const threshold = numberOrDefault(normalized.threshold, DASHBOARD_CONTROL_DEFAULTS.threshold);
  const whaleThreshold = numberOrDefault(normalized.whaleThreshold, DASHBOARD_CONTROL_DEFAULTS.whaleThreshold);

  return [
    {
      label: 'Theme',
      value: String(normalized.theme || DEFAULT_SETTINGS.theme).toUpperCase(),
      detail: `Chart style ${normalized.chartType || DEFAULT_SETTINGS.chartType}`,
    },
    {
      label: 'Workspace',
      value: getViewModeLabel(normalized.viewMode),
      detail: 'Last opened desk view',
    },
    {
      label: 'Provider',
      value: String(normalized.provider || DEFAULT_SETTINGS.provider).toUpperCase(),
      detail: 'External data source',
    },
    {
      label: 'Tape Filter',
      value: `${normalized.selectedStock || DASHBOARD_CONTROL_DEFAULTS.selectedStock} / $${threshold}M+`,
      detail: formatFeedSort(normalized.feedSort),
    },
    {
      label: 'Whale Gate',
      value: `${whaleThreshold}K shares`,
      detail: normalized.isRunning ? 'Simulation live' : 'Simulation paused',
    },
    {
      label: 'Alerts',
      value: summarizeAlertDelivery(normalized),
      detail: normalized.discordWebhook ? 'Discord webhook configured' : 'Discord webhook not set',
    },
    {
      label: 'Integrations',
      value: summarizeIntegrations(normalized),
      detail: normalized.plotlyUrl ? 'Plotly linked' : 'Plotly not linked',
    },
  ];
};
