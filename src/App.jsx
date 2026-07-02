import { Suspense, useState, useEffect, useRef, useMemo } from 'react';
import {
  Activity, TrendingUp, TrendingDown, Pause, Play, Filter,
  DollarSign, Clock, BarChart3, PieChart, Zap, RefreshCw, Bell,
  Download, AlertTriangle, Settings
} from 'lucide-react';
import {
  MAG7_STOCKS,
  generateTransaction,
  getWeightedRandomStock,
  generateHistoricalData,
  formatCurrency,
  formatMillionsCurrency,
  formatVolume
} from './dataGenerator';
import {
  clearNewTransactionHighlight,
  scheduleNewTransactionHighlight,
} from './dashboardHighlight';
import { getThemeStyle } from './themes';
import { summarizeDashboardPulse } from './dashboardPulse';
import { buildFeedSnapshotCards } from './feedSnapshot';
import { OPTION_GREEK_FILTERS } from './greekLabels';
import { computeZScore, rowsToCsv } from './flowEngine';
import {
  DASHBOARD_CONTROL_DEFAULTS,
  buildDashboardFilterChips,
  extractDashboardControls,
  hasCustomDashboardFilters,
  mergeDashboardControls,
  normalizePersistedSettings,
} from './settingsPersistence';
import {
  SettingsModal,
  StockSparkline,
  TransactionVolumeChart,
  getWorkspaceViewComponent,
} from './lazyViews';
import { VIEW_MODES } from './viewModes';

const STORAGE_KEY = 'sentinel-flare-settings-v3';
const LEGACY_STORAGE_KEYS = ['sentinel-flareitor-settings-v3'];
const TIMEFRAME_HOURS = { '1H': 1, '4H': 4, '1D': 24 };
const VIEW_MODE_ICONS = {
  dashboard: Activity,
  intent: Zap,
  options: PieChart,
  scanner: Filter,
  flowmap: BarChart3,
  alerts: Bell,
  watchlist: DollarSign,
  replay: Clock,
  admin: Settings,
  health: AlertTriangle,
};
const GREEK_FILTERS = OPTION_GREEK_FILTERS;

const exportTransactionsToCsv = (rows) => {
  const csv = rowsToCsv(rows);
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', `darkpool-transactions-${Date.now()}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

const readPersistedSettings = () => {
  try {
    if (typeof localStorage === 'undefined') return {};
    const persisted = localStorage.getItem(STORAGE_KEY)
      || LEGACY_STORAGE_KEYS.map((key) => localStorage.getItem(key)).find(Boolean);
    if (!persisted) return {};
    const parsed = JSON.parse(persisted);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    console.debug('Ignoring invalid persisted dashboard settings', error);
    return {};
  }
};

const writePersistedSettings = (settings) => {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch (error) {
    console.debug('Unable to persist dashboard settings', error);
  }
};

const createInitialStockPrices = () => (
  Object.fromEntries(
    Object.entries(MAG7_STOCKS).map(([symbol, stock]) => [symbol, { ...stock }])
  )
);

const createInitialChartData = (timeframe) => (
  Object.fromEntries(
    Object.keys(MAG7_STOCKS).map((symbol) => [
      symbol,
      generateHistoricalData(symbol, TIMEFRAME_HOURS[timeframe]),
    ])
  )
);

const StockCard = ({ stock, data, isActive, onClick, threshold = 50 }) => {
  const latestData = data[data.length - 1] || { buyVolume: 0, sellVolume: 0 };
  const totalVolume = latestData.totalVolume || 0;
  const intensity = Math.min(totalVolume / threshold, 1);

  const priceChange = data.length > 1
    ? ((data[data.length - 1].price - data[0].price) / data[0].price * 100)
    : 0;

  const isPositive = priceChange >= 0;

  return (
    <button
      type="button"
      onClick={onClick}
      className={`
      relative overflow-hidden rounded-xl p-4 transition-all duration-300 cursor-pointer text-left
      ${isActive ? 'bg-dark-700 ring-2 ring-accent-cyan' : 'bg-dark-800 hover:bg-dark-700'}
    `}
    >
      <div
        className="absolute top-0 right-0 w-20 h-20 opacity-20"
        style={{
          background: `conic-gradient(#00d4ff ${intensity * 360}deg, transparent ${intensity * 360}deg)`,
          borderRadius: '50%',
        }}
      />

      <div className="relative z-10">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="font-mono font-bold text-lg text-white">{stock.symbol}</h3>
            <p className="text-xs text-gray-500">{stock.name}</p>
          </div>
          <div className={`flex items-center gap-1 ${isPositive ? 'text-accent-green' : 'text-accent-red'}`}>
            {isPositive ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
            <span className="font-mono text-sm font-medium">
              {isPositive ? '+' : ''}
              {priceChange.toFixed(2)}%
            </span>
          </div>
        </div>

        <div className="flex items-end justify-between">
          <div>
            <p className="font-mono text-2xl font-bold text-white">
              ${stock.basePrice.toFixed(2)}
            </p>
            <p className="text-xs text-gray-500 mt-1">
              Flow: {totalVolume.toFixed(1)}M
            </p>
          </div>

          <div className="w-20 h-8">
            <Suspense fallback={<div className="h-full w-full rounded bg-dark-900/60" />}>
              <StockSparkline stock={stock} data={data} />
            </Suspense>
          </div>
        </div>
      </div>
    </button>
  );
};

const TransactionItem = ({ transaction, isNew }) => {
  const isBuy = transaction.direction === 'BUY';

  return (
    <div
      className={`
      flex items-center justify-between p-3 rounded-lg bg-dark-800 border border-dark-600
      transition-all duration-300
      ${isNew ? 'animate-pulse bg-dark-700 border-accent-cyan' : ''}
    `}
    >
      <div className="flex items-center gap-3">
        <div
          className={`
          w-10 h-10 rounded-full flex items-center justify-center font-mono font-bold text-sm
          ${isBuy ? 'bg-accent-green/20 text-accent-green' : 'bg-accent-red/20 text-accent-red'}
        `}
        >
          {transaction.symbol}
        </div>
        <div>
          <p className="font-mono font-semibold text-white">
            {transaction.symbol}
          </p>
          <p className="text-xs text-gray-500">
            {transaction.timestamp.toLocaleTimeString()}
          </p>
        </div>
      </div>

      <div className="text-right">
        <p className="font-mono font-bold text-white">
          {formatVolume(transaction.size)} sh
        </p>
        <p className={`text-xs font-medium ${isBuy ? 'text-accent-green' : 'text-accent-red'}`}>
          {transaction.direction}
        </p>
      </div>

      <div className="text-right">
        <p className="font-mono text-sm text-gray-400">
          @ ${transaction.price.toFixed(2)}
        </p>
        <p className="font-mono text-sm font-semibold text-white">
          {formatCurrency(transaction.value)}
        </p>
      </div>
    </div>
  );
};

const PulseCard = ({ icon: Icon, label, value, detail, toneClass, onClick, actionLabel }) => {
  const Component = onClick ? 'button' : 'div';
  const interactiveProps = onClick ? { type: 'button', onClick, 'aria-label': actionLabel, title: actionLabel } : {};

  return (
    <Component
      {...interactiveProps}
      className={`h-full w-full rounded-xl border p-4 text-left transition-all ${toneClass} ${
        onClick ? 'cursor-pointer hover:border-white/30 hover:bg-white/5 focus:outline-none focus:ring-2 focus:ring-accent-cyan/70' : ''
      }`}
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs font-semibold uppercase text-current/70">{label}</span>
        <Icon size={18} className="shrink-0" />
      </div>
      <div className="min-h-[2rem] font-mono text-2xl font-bold text-white">{value}</div>
      <p className="mt-2 min-h-[2.5rem] text-sm leading-5 text-current/80">{detail}</p>
    </Component>
  );
};

const FocusQueue = ({ queue, onSelectSymbol }) => (
  <section className="mb-6" aria-label="Operator focus queue">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 className="text-sm font-semibold uppercase text-gray-300">Operator Focus Queue</h2>
        <p className="mt-1 text-xs text-gray-500">Top symbols by live notional, side skew, and whale pressure</p>
      </div>
      <span className="rounded-lg bg-dark-900/70 px-2.5 py-1 font-mono text-xs text-gray-400">
        {queue.length} active
      </span>
    </div>

    {queue.length === 0 ? (
      <div className="rounded-lg border border-dashed border-dark-600 bg-dark-900/40 p-4 text-sm text-gray-500">
        Focus queue will populate once qualifying prints arrive.
      </div>
    ) : (
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {queue.map((item, index) => (
          <button
            key={item.symbol}
            type="button"
            onClick={() => onSelectSymbol(item.symbol)}
            aria-label={`Filter dashboard to ${item.symbol} from focus queue`}
            title={`Filter dashboard to ${item.symbol}`}
            className={`h-full w-full rounded-xl border p-4 text-left transition-all hover:border-white/30 hover:bg-white/5 focus:outline-none focus:ring-2 focus:ring-accent-cyan/70 ${item.toneClass}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-mono text-2xl font-bold text-white">{item.symbol}</div>
                <div className="mt-1 text-xs uppercase text-current/70">{item.label}</div>
              </div>
              <span className="rounded bg-dark-900/45 px-2 py-1 font-mono text-xs text-white">
                #{index + 1}
              </span>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg bg-dark-900/40 px-2 py-2">
                <div className="font-mono text-sm text-white">{formatMillionsCurrency(item.notional / 1000000)}</div>
                <div className="mt-1 text-[11px] text-current/60">notional</div>
              </div>
              <div className="rounded-lg bg-dark-900/40 px-2 py-2">
                <div className="font-mono text-sm text-white">{item.buyRatio}%</div>
                <div className="mt-1 text-[11px] text-current/60">buy</div>
              </div>
              <div className="rounded-lg bg-dark-900/40 px-2 py-2">
                <div className="font-mono text-sm text-white">{item.whaleCount}</div>
                <div className="mt-1 text-[11px] text-current/60">whales</div>
              </div>
            </div>
          </button>
        ))}
      </div>
    )}
  </section>
);

const ViewFallback = () => (
  <div className="rounded-xl border border-dark-600/80 bg-dark-800/80 p-8 text-center text-sm text-gray-400">
    Loading workspace...
  </div>
);

export default function App() {
  const [initialSettings] = useState(() => normalizePersistedSettings(readPersistedSettings()));
  const [initialDashboardControls] = useState(() => extractDashboardControls(initialSettings));
  const [isRunning, setIsRunning] = useState(initialDashboardControls.isRunning);
  const [transactions, setTransactions] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [selectedStock, setSelectedStock] = useState(initialDashboardControls.selectedStock);
  const [viewMode, setViewMode] = useState(initialDashboardControls.viewMode);
  const [timeframe, setTimeframe] = useState(initialDashboardControls.timeframe);
  const [threshold, setThreshold] = useState(initialDashboardControls.threshold);
  const [whaleThreshold, setWhaleThreshold] = useState(initialDashboardControls.whaleThreshold);
  const [settings, setSettings] = useState(initialSettings);
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);
  const [feedSort, setFeedSort] = useState(initialDashboardControls.feedSort);
  const [chartData, setChartData] = useState({});
  const [stockPrices, setStockPrices] = useState(() => createInitialStockPrices());
  const [newTransactionId, setNewTransactionId] = useState(null);
  const [currentTime, setCurrentTime] = useState(() => new Date());
  const feedRef = useRef(null);
  const lastAlertedTransactionId = useRef(null);
  const highlightTimeoutRef = useRef(null);
  const dashboardControls = useMemo(() => ({
    selectedStock,
    viewMode,
    timeframe,
    threshold,
    whaleThreshold,
    feedSort,
    isRunning,
  }), [selectedStock, viewMode, timeframe, threshold, whaleThreshold, feedSort, isRunning]);
  const currentSettings = useMemo(
    () => mergeDashboardControls(settings, dashboardControls),
    [settings, dashboardControls]
  );
  const activeFilterChips = useMemo(
    () => buildDashboardFilterChips(dashboardControls),
    [dashboardControls]
  );
  const hasCustomFilters = useMemo(
    () => hasCustomDashboardFilters(dashboardControls),
    [dashboardControls]
  );

  const appThemeStyle = useMemo(() => ({
    ...getThemeStyle(settings.theme),
    backgroundColor: 'var(--color-bg)',
    color: 'var(--color-text)',
  }), [settings.theme]);

  useEffect(() => {
    if (typeof document === 'undefined') return;

    Object.entries(getThemeStyle(settings.theme)).forEach(([key, value]) => {
      document.documentElement.style.setProperty(key, value);
    });
  }, [settings.theme]);

  useEffect(() => {
    writePersistedSettings(currentSettings);
  }, [currentSettings]);

  const handleSettingsChange = (newSettings) => {
    const normalized = normalizePersistedSettings(newSettings);
    const controls = extractDashboardControls(normalized);

    setSettings(normalized);
    setSelectedStock(controls.selectedStock);
    setViewMode(controls.viewMode);
    setTimeframe(controls.timeframe);
    setThreshold(controls.threshold);
    setWhaleThreshold(controls.whaleThreshold);
    setFeedSort(controls.feedSort);
    setIsRunning(controls.isRunning);
  };

  const handleClearDashboardFilters = () => {
    setSelectedStock(DASHBOARD_CONTROL_DEFAULTS.selectedStock);
    setTimeframe(DASHBOARD_CONTROL_DEFAULTS.timeframe);
    setThreshold(DASHBOARD_CONTROL_DEFAULTS.threshold);
    setWhaleThreshold(DASHBOARD_CONTROL_DEFAULTS.whaleThreshold);
    setFeedSort(DASHBOARD_CONTROL_DEFAULTS.feedSort);
  };

  useEffect(() => {
    setChartData(createInitialChartData(timeframe));
  }, [timeframe]);

  useEffect(() => {
    const clockTimer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(clockTimer);
  }, []);

  useEffect(() => {
    if (!isRunning) return undefined;

    const interval = setInterval(() => {
      const stock = getWeightedRandomStock();
      const transaction = generateTransaction(stock);

      scheduleNewTransactionHighlight(transaction.id, setNewTransactionId, highlightTimeoutRef);

      setTransactions((prev) => [transaction, ...prev].slice(0, 200));

      setStockPrices((prev) => ({
        ...prev,
        [stock.symbol]: {
          ...prev[stock.symbol],
          basePrice: Math.max(
            0.01,
            prev[stock.symbol].basePrice + (transaction.direction === 'BUY' ? 0.05 : -0.05)
          ),
        },
      }));

      setChartData((prev) => {
        const series = prev[stock.symbol] || [];
        const nextPoint = {
          time: Date.now(),
          label: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
          buyVolume: transaction.direction === 'BUY' ? transaction.value / 1000000 : 0,
          sellVolume: transaction.direction === 'SELL' ? transaction.value / 1000000 : 0,
          totalVolume: transaction.value / 1000000,
          price: transaction.price,
        };
        return {
          ...prev,
          [stock.symbol]: [...series.slice(-47), nextPoint],
        };
      });
    }, 1200 + Math.random() * 1100);

    return () => {
      clearInterval(interval);
      clearNewTransactionHighlight(highlightTimeoutRef);
    };
  }, [isRunning]);

  useEffect(() => {
    const transaction = transactions[0];
    if (!transaction || transaction.id === lastAlertedTransactionId.current) return;

    lastAlertedTransactionId.current = transaction.id;
    const symbolSizes = transactions
      .slice(1)
      .filter((txn) => txn.symbol === transaction.symbol)
      .slice(0, 25)
      .map((txn) => txn.size);
    const zScore = computeZScore(symbolSizes, transaction.size);
    const isWhale = transaction.size >= whaleThreshold * 1000; // Convert to actual shares

    if (!isWhale && zScore < 2.2) return;

    const reason = isWhale
      ? `Whale print ${formatVolume(transaction.size)} shares`
      : `Unusual size z-score ${zScore.toFixed(2)}`;
    const alert = {
      id: `${transaction.id}-ALERT`,
      symbol: transaction.symbol,
      direction: transaction.direction,
      size: transaction.size,
      reason,
      timestamp: new Date(),
    };

    setAlerts((prevAlerts) => {
      if (prevAlerts.some((existing) => existing.id === alert.id)) return prevAlerts;
      return [alert, ...prevAlerts].slice(0, 25);
    });
  }, [transactions, whaleThreshold]);

  const filteredTransactions = useMemo(() => {
    const baseFiltered = transactions.filter((transaction) => {
      const stockMatch = selectedStock === 'ALL' || transaction.symbol === selectedStock;
      return stockMatch && transaction.value >= threshold * 1000000;
    });

    if (feedSort === 'LARGEST') {
      return [...baseFiltered].sort((a, b) => b.value - a.value);
    }

    return baseFiltered;
  }, [transactions, selectedStock, threshold, feedSort]);
  const feedSnapshotCards = useMemo(
    () => buildFeedSnapshotCards(filteredTransactions),
    [filteredTransactions]
  );

  const totalVolume = transactions.reduce((acc, transaction) => acc + transaction.value / 1000000, 0);
  const buyVolume = transactions.filter((transaction) => transaction.direction === 'BUY').reduce((acc, transaction) => acc + transaction.value / 1000000, 0);
  const sellVolume = transactions.filter((transaction) => transaction.direction === 'SELL').reduce((acc, transaction) => acc + transaction.value / 1000000, 0);
  const avgTradeSize = transactions.length ? totalVolume / transactions.length : 0;
  const whaleTrades = transactions.filter((transaction) => transaction.size >= whaleThreshold * 1000).length;
  const buyRatio = totalVolume > 0 ? (buyVolume / totalVolume * 100).toFixed(1) : 50;
  const dashboardPulse = useMemo(
    () => summarizeDashboardPulse(transactions, alerts, { whaleThresholdK: whaleThreshold }),
    [transactions, alerts, whaleThreshold]
  );

  const mainChartData = selectedStock === 'ALL'
    ? Object.keys(MAG7_STOCKS).map((symbol) => {
      const stockTransactions = transactions.filter((transaction) => transaction.symbol === symbol);
      const buyVol = stockTransactions.filter((transaction) => transaction.direction === 'BUY').reduce((acc, transaction) => acc + transaction.value / 1000000, 0);
      const sellVol = stockTransactions.filter((transaction) => transaction.direction === 'SELL').reduce((acc, transaction) => acc + transaction.value / 1000000, 0);
      return {
        name: symbol,
        buy: buyVol,
        sell: sellVol,
      };
    })
    : chartData[selectedStock]?.map((point) => ({
      name: point.label,
      buy: point.buyVolume,
      sell: point.sellVolume,
    })) || [];
  const WorkspaceView = getWorkspaceViewComponent(viewMode);

  return (
    <div className="min-h-screen p-4 lg:p-6" style={appThemeStyle}>
      <header className="mb-6 grid gap-4 xl:grid-cols-[minmax(260px,auto)_1fr_auto] xl:items-center">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-accent-cyan to-accent-purple flex items-center justify-center">
            <Activity className="text-white" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight">SENTINEL FLARE</h1>
            <p className="text-xs text-gray-500">Real-time Institutional Activity (MAG7)</p>
          </div>
        </div>

        <nav
          className="flex min-w-0 items-center gap-1 overflow-x-auto rounded-xl border border-dark-600/70 bg-dark-800/80 p-1"
          aria-label="Primary views"
        >
          {VIEW_MODES.map((mode) => {
            const Icon = VIEW_MODE_ICONS[mode.id] || Activity;
            const isActive = viewMode === mode.id;
            return (
              <button
                key={mode.id}
                type="button"
                onClick={() => setViewMode(mode.id)}
                aria-current={isActive ? 'page' : undefined}
                title={mode.description}
                className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium transition-all ${
                  isActive
                    ? 'bg-dark-700 text-white shadow-sm'
                    : 'text-gray-400 hover:bg-dark-700/70 hover:text-white'
                }`}
                style={{
                  color: isActive ? 'var(--color-accent)' : undefined,
                }}
              >
                <Icon size={14} />
                <span>{mode.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="flex flex-wrap items-center gap-3 xl:justify-end">
          <div className="flex items-center gap-2 text-gray-400">
            <Clock size={16} />
            <span className="font-mono text-sm">{currentTime.toLocaleTimeString()}</span>
          </div>

          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-accent-green animate-pulse" />
            <span className="text-xs text-gray-400">LIVE</span>
          </div>

          <div className="flex items-center gap-2 bg-dark-800 rounded-lg px-3 py-1.5">
            <DollarSign size={14} className="text-accent-cyan" />
            <span className="font-mono text-sm text-white">{formatMillionsCurrency(totalVolume)}</span>
          </div>

          {/* Settings Button */}
          <button
            onClick={() => setSettingsModalOpen(true)}
            className="p-2 rounded-lg bg-dark-800 hover:bg-dark-700 text-gray-400 hover:text-white transition-all"
            title="Settings"
          >
            <Settings size={20} />
          </button>
        </div>
      </header>

      {viewMode === 'dashboard' && (
        <div className="mb-6 flex flex-wrap items-center gap-4 rounded-xl border border-dark-600/70 bg-dark-800/55 p-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setIsRunning(!isRunning)}
              className={`
                flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-all
                ${isRunning
                  ? 'bg-accent-red/20 text-accent-red hover:bg-accent-red/30'
                  : 'bg-accent-green/20 text-accent-green hover:bg-accent-green/30'}
              `}
            >
              {isRunning ? <Pause size={16} /> : <Play size={16} />}
              {isRunning ? 'Pause' : 'Resume'}
            </button>

            <button
              type="button"
              onClick={() => {
                setTransactions([]);
                setAlerts([]);
                setChartData(createInitialChartData(timeframe));
                setStockPrices(createInitialStockPrices());
                clearNewTransactionHighlight(highlightTimeoutRef);
                setNewTransactionId(null);
              }}
              className="p-2 rounded-lg bg-dark-800 text-gray-400 hover:text-white transition-all"
              aria-label="Reset simulation"
              title="Reset simulation"
            >
              <RefreshCw size={16} />
            </button>
          </div>

        <div className="flex items-center gap-2">
          {['1H', '4H', '1D'].map((frame) => (
            <button
              type="button"
              key={frame}
              onClick={() => setTimeframe(frame)}
              className={`
                px-3 py-1.5 rounded-lg font-mono text-sm transition-all
                ${timeframe === frame
                  ? 'bg-accent-cyan/20 text-accent-cyan'
                  : 'bg-dark-800 text-gray-400 hover:text-white'}
              `}
            >
              {frame}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <Filter size={14} className="text-gray-500" />
          <select
            value={selectedStock}
            onChange={(event) => setSelectedStock(event.target.value)}
            className="bg-dark-800 text-white rounded-lg px-3 py-1.5 font-mono text-sm border border-dark-600 focus:border-accent-cyan outline-none"
          >
            <option value="ALL">ALL STOCKS</option>
            {Object.values(MAG7_STOCKS).map((stock) => (
              <option key={stock.symbol} value={stock.symbol}>
                {stock.symbol} - {stock.name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">Whale:</span>
          <input
            type="range"
            min="10"
            max="200"
            step="10"
            value={whaleThreshold}
            onChange={(event) => setWhaleThreshold(Number(event.target.value))}
            className="w-24 accent-accent-yellow"
            style={{ accentColor: 'var(--color-accent-yellow)' }}
          />
          <span className="font-mono text-sm text-accent-yellow">{whaleThreshold}K</span>
        </div>

        {/* Greeks Display */}
        <div className="flex items-center gap-1 ml-2">
          {GREEK_FILTERS.map(({ key, symbol, name, codePoint }) => (
            <label
              key={key}
              className="flex items-center gap-1 px-2 py-1 rounded bg-dark-800 cursor-pointer hover:bg-dark-700"
              title={`${name} (${codePoint})`}
              aria-label={`${name} option Greek ${codePoint}`}
            >
              <input type="checkbox" className="sr-only" />
              <span
                className="font-mono font-bold text-[13px] leading-none"
                style={{ color: 'var(--color-accent)' }}
              >
                {symbol}
              </span>
            </label>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">Min:</span>
          <input
            type="range"
            min="1"
            max="50"
            value={threshold}
            onChange={(event) => setThreshold(Number(event.target.value))}
            className="w-24 accent-accent-cyan"
          />
          <span className="font-mono text-sm text-accent-cyan">${threshold}M</span>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={feedSort}
            onChange={(event) => setFeedSort(event.target.value)}
            className="bg-dark-800 text-white rounded-lg px-3 py-1.5 font-mono text-sm border border-dark-600 focus:border-accent-cyan outline-none"
          >
            <option value="LATEST">Latest first</option>
            <option value="LARGEST">Largest first</option>
          </select>

          <button
            type="button"
            onClick={() => exportTransactionsToCsv(filteredTransactions)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-dark-800 text-gray-300 hover:text-white transition-all"
            title="Export filtered feed"
          >
            <Download size={14} />
            Export CSV
          </button>
        </div>

         <div
           className="flex min-w-0 flex-1 flex-wrap items-center gap-2 rounded-lg border border-dark-600/60 bg-dark-900/45 px-3 py-2"
           aria-label="Active dashboard filters"
         >
           <span className="shrink-0 text-xs font-semibold uppercase text-gray-500">Active Filters</span>
           {activeFilterChips.map((chip) => (
             <span
               key={chip.label}
               className="inline-flex items-center gap-1 rounded-md border border-dark-600/80 bg-dark-800 px-2 py-1 text-xs text-gray-300"
             >
               <span className="text-gray-500">{chip.label}</span>
               <span className="font-mono text-white">{chip.value}</span>
             </span>
           ))}
           <button
             type="button"
             onClick={handleClearDashboardFilters}
             disabled={!hasCustomFilters}
             className={`ml-auto rounded-md px-2.5 py-1 text-xs font-medium transition-all ${
               hasCustomFilters
                 ? 'bg-accent-cyan/15 text-accent-cyan hover:bg-accent-cyan/25'
                 : 'cursor-not-allowed bg-dark-800 text-gray-600'
             }`}
           >
             Clear Filters
           </button>
         </div>
       </div>
       )}

      {/* Main Content Based on View Mode */}
      {WorkspaceView ? (
        <Suspense fallback={<ViewFallback />}>
          <WorkspaceView {...(viewMode === 'options' ? { settings: currentSettings } : {})} />
        </Suspense>
      ) : (
        <>
          <section className="mb-6 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="Session pulse">
            <PulseCard
              icon={dashboardPulse.bias.state === 'sell' ? TrendingDown : dashboardPulse.bias.state === 'buy' ? TrendingUp : Activity}
              label="Tape Bias"
              value={dashboardPulse.bias.label}
              detail={dashboardPulse.bias.summary}
              toneClass={dashboardPulse.bias.toneClass}
            />
            <PulseCard
              icon={DollarSign}
              label="Leader"
              value={dashboardPulse.leader.symbol}
              detail={`${formatCurrency(dashboardPulse.leader.notional)} across ${dashboardPulse.leader.trades} trade${dashboardPulse.leader.trades === 1 ? '' : 's'} (${dashboardPulse.leader.dominantSide})`}
              toneClass="border-cyan-500/30 bg-cyan-500/10 text-cyan-200"
              onClick={dashboardPulse.leader.symbol !== 'N/A' ? () => setSelectedStock(dashboardPulse.leader.symbol) : undefined}
              actionLabel={`Filter dashboard to ${dashboardPulse.leader.symbol}`}
            />
            <PulseCard
              icon={Zap}
              label="Whale Pressure"
              value={dashboardPulse.whales.label}
              detail={`Threshold ${formatVolume(dashboardPulse.whales.thresholdShares)} shares at current desk setting`}
              toneClass={dashboardPulse.whales.toneClass}
              onClick={dashboardPulse.whales.count > 0 ? () => setFeedSort('LARGEST') : undefined}
              actionLabel="Sort live transaction feed by largest prints"
            />
            <PulseCard
              icon={AlertTriangle}
              label={dashboardPulse.latestAlert.label}
              value={dashboardPulse.latestAlert.symbol}
              detail={dashboardPulse.latestAlert.reason}
              toneClass="border-purple-500/30 bg-purple-500/10 text-purple-200"
              onClick={dashboardPulse.latestAlert.symbol !== 'N/A' ? () => setSelectedStock(dashboardPulse.latestAlert.symbol) : undefined}
              actionLabel={`Filter dashboard to latest alert for ${dashboardPulse.latestAlert.symbol}`}
            />
          </section>

          <FocusQueue queue={dashboardPulse.focusQueue} onSelectSymbol={setSelectedStock} />

          {/* Stock Cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7 gap-3 mb-6">
            {Object.values(MAG7_STOCKS).map((stock) => (
              <StockCard
                key={stock.symbol}
                stock={stockPrices[stock.symbol] || stock}
                data={chartData[stock.symbol] || []}
                isActive={selectedStock === stock.symbol}
                onClick={() => setSelectedStock(stock.symbol)}
                threshold={whaleThreshold}
              />
            ))}
          </div>

          {/* Charts & Feed */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-dark-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <BarChart3 size={20} className="text-accent-cyan" />
              Transaction Volume
            </h2>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded bg-accent-green" />
                <span className="text-xs text-gray-500">BUY</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded bg-accent-red" />
                <span className="text-xs text-gray-500">SELL</span>
              </div>
            </div>
          </div>

          <div className="h-80">
            <Suspense fallback={<div className="h-full w-full animate-pulse rounded-lg bg-dark-900/60" />}>
              <TransactionVolumeChart data={mainChartData} />
            </Suspense>
          </div>
        </div>

        <div className="bg-dark-800 rounded-xl p-4">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-4">
            <PieChart size={20} className="text-accent-purple" />
            Summary
          </h2>

          <div className="space-y-4">
            <div className="bg-dark-700 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-2">BUY/SELL RATIO</p>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2 rounded-full overflow-hidden bg-dark-900 flex">
                  <div
                    className="h-full bg-accent-green transition-all duration-500"
                    style={{ width: `${buyRatio}%` }}
                  />
                  <div
                    className="h-full bg-accent-red transition-all duration-500"
                    style={{ width: `${100 - buyRatio}%` }}
                  />
                </div>
                <span className="font-mono text-sm text-white">{buyRatio}%</span>
              </div>
              <div className="flex justify-between mt-2 text-xs">
                <span className="text-accent-green">BUY {buyRatio}%</span>
                <span className="text-accent-red">SELL {100 - buyRatio}%</span>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="bg-dark-700 rounded-lg p-3">
                <p className="text-xs text-gray-500 mb-1">TOTAL VOLUME</p>
                <p className="font-mono text-xl font-bold text-white">{formatVolume(totalVolume)}M</p>
              </div>
              <div className="bg-dark-700 rounded-lg p-3">
                <p className="text-xs text-gray-500 mb-1">AVG TRADE</p>
                <p className="font-mono text-xl font-bold text-white">{avgTradeSize.toFixed(2)}M</p>
              </div>
              <div className="bg-accent-yellow/10 rounded-lg p-3">
                <p className="text-xs text-accent-yellow mb-1">WHALE PRINTS</p>
                <p className="font-mono text-lg font-bold text-accent-yellow">{whaleTrades}</p>
              </div>
              <div className="bg-accent-cyan/10 rounded-lg p-3">
                <p className="text-xs text-accent-cyan mb-1">TRANSACTIONS</p>
                <p className="font-mono text-lg font-bold text-accent-cyan">{transactions.length}</p>
              </div>
            </div>

            <div className="bg-dark-700 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-3">MOST ACTIVE</p>
              {Object.entries(
                transactions.reduce((acc, transaction) => {
                  acc[transaction.symbol] = (acc[transaction.symbol] || 0) + transaction.size;
                  return acc;
                }, {})
              )
                .sort((a, b) => b[1] - a[1])
                .slice(0, 3)
                .map(([symbol, volume]) => (
                  <div key={symbol} className="flex items-center justify-between py-2 border-b border-dark-600 last:border-0">
                    <span className="font-mono font-semibold text-white">{symbol}</span>
                    <span className="font-mono text-sm text-accent-cyan">{formatVolume(volume)}M</span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 bg-dark-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Zap size={20} className="text-accent-yellow" />
              Live Transaction Feed
            </h2>
            <span className="text-xs text-gray-500">
              Showing {filteredTransactions.length} of {transactions.length} transactions
            </span>
          </div>

          <div className="mb-4 grid grid-cols-2 gap-2 lg:grid-cols-4" aria-label="Filtered feed snapshot">
            {feedSnapshotCards.map((card) => (
              <div key={card.label} className="rounded-lg border border-dark-600/70 bg-dark-900/45 px-3 py-2">
                <div className="text-[11px] font-semibold uppercase text-gray-500">{card.label}</div>
                <div className="mt-1 font-mono text-sm font-semibold text-white">{card.value}</div>
                <div className="mt-1 text-[11px] text-gray-500">{card.detail}</div>
              </div>
            ))}
          </div>

          <div
            ref={feedRef}
            className="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-96 overflow-y-auto"
          >
            {filteredTransactions.length === 0 ? (
              <div className="col-span-full rounded-lg border border-dashed border-dark-600 bg-dark-900/35 py-8 text-center text-gray-500">
                <p>No transactions match the active filters.</p>
                {hasCustomFilters && (
                  <p className="mt-2 text-xs">Clear filters or lower the minimum print size to widen the tape.</p>
                )}
              </div>
            ) : (
              filteredTransactions.slice(0, 36).map((transaction) => (
                <TransactionItem
                  key={transaction.id}
                  transaction={transaction}
                  isNew={newTransactionId === transaction.id}
                />
              ))
            )}
          </div>
        </div>

        <div className="bg-dark-800 rounded-xl p-4">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-4">
            <Bell size={20} className="text-accent-yellow" />
            Smart Alerts
          </h2>

          <div className="space-y-3 max-h-96 overflow-y-auto">
            {alerts.length === 0 ? (
              <p className="text-sm text-gray-500">No anomalies yet. Alerts appear for whale prints and z-score spikes.</p>
            ) : (
              alerts.map((alert) => (
                <div key={alert.id} className="p-3 rounded-lg border border-accent-yellow/30 bg-accent-yellow/10">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <AlertTriangle size={14} className="text-accent-yellow" />
                      <span className="font-mono text-sm text-white">{alert.symbol}</span>
                    </div>
                    <span className="text-xs text-gray-400">{alert.timestamp.toLocaleTimeString()}</span>
                  </div>
                  <p className="text-xs text-gray-300 mt-2">{alert.reason}</p>
                  <p className="text-xs mt-1 font-mono text-white">
                    {alert.direction} {formatVolume(alert.size)} sh
                  </p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
        </>
      )}

      {settingsModalOpen && (
        <Suspense fallback={null}>
          <SettingsModal
            isOpen={settingsModalOpen}
            onClose={() => setSettingsModalOpen(false)}
            settings={currentSettings}
            onSettingsChange={handleSettingsChange}
          />
        </Suspense>
      )}
    </div>
  );
}
