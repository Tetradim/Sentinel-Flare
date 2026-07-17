import { useEffect, useState } from 'react';
import { X, Monitor, Layout, Maximize2, Link, Activity, Bell, Globe, Info, Database } from 'lucide-react';
import { THEMES, CHART_TYPES, LAYOUTS, CARD_SIZES, PROVIDERS, GREEK_SYMBOLS, getThemeCSS } from './themes';

export default function SettingsModal({ isOpen, onClose, settings, onSettingsChange }) {
  const [activeTab, setActiveTab] = useState('appearance');
  const [generalApi, setGeneralApi] = useState(null);
  const [generalApiToken, setGeneralApiToken] = useState('');
  const [generalApiMessage, setGeneralApiMessage] = useState('');
  const [generalApiBusy, setGeneralApiBusy] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    fetch('/api/general-api').then((response) => response.json()).then((data) => setGeneralApi(data.settings)).catch((error) => setGeneralApiMessage(error.message));
  }, [isOpen]);

  if (!isOpen) return null;

  const tabs = [
    { id: 'appearance', label: 'Appearance', icon: Monitor },
    { id: 'layout', label: 'Layout', icon: Layout },
    { id: 'cards', label: 'Cards', icon: Maximize2 },
    { id: 'providers', label: 'Providers', icon: Globe },
    { id: 'integrations', label: 'Integrations', icon: Link },
    { id: 'general-api', label: 'General API', icon: Database },
    { id: 'alerts', label: 'Alerts', icon: Bell },
    { id: 'tutorial', label: 'Tutorial', icon: Info },
  ];

  const updateSetting = (key, value) => {
    onSettingsChange({ ...settings, [key]: value });
  };

  const updateGeneralApi = (patch) => setGeneralApi((current) => ({ ...current, ...patch }));
  const saveGeneralApi = async () => {
    if (!generalApi) return;
    setGeneralApiBusy(true);
    try {
      const payload = { ...generalApi };
      delete payload.token_configured;
      if (generalApiToken) payload.api_token = generalApiToken;
      const response = await fetch('/api/general-api', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Save failed');
      setGeneralApi(data.settings);
      setGeneralApiToken('');
      setGeneralApiMessage('General API settings saved.');
    } catch (error) { setGeneralApiMessage(error.message); } finally { setGeneralApiBusy(false); }
  };
  const generalApiAction = async (action) => {
    setGeneralApiBusy(true);
    try {
      if (generalApiToken) await saveGeneralApi();
      const response = await fetch(`/api/general-api/${action}`, { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `${action} failed`);
      setGeneralApiMessage(action === 'register' ? `Registered ${data.participant?.participant_id || generalApi.participant_id}; token stored privately.` : `Archive reachable (${data.contract || 'archive.general.v1'}).`);
      const refreshed = await fetch('/api/general-api').then((value) => value.json());
      setGeneralApi(refreshed.settings);
    } catch (error) { setGeneralApiMessage(error.message); } finally { setGeneralApiBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />
      
      {/* Modal */}
      <div 
        className="relative bg-dark-800 rounded-2xl shadow-2xl w-[90vw] max-w-5xl max-h-[85vh] flex overflow-hidden border border-dark-600"
        style={{ backgroundColor: 'var(--color-card)' }}
      >
        {/* Sidebar */}
        <div className="w-48 bg-dark-900/50 p-3 border-r border-dark-600">
          <h2 className="text-lg font-bold text-white mb-4 px-2">Settings</h2>
          <nav className="space-y-1">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left transition-all ${
                  activeTab === tab.id
                    ? 'bg-dark-700 text-accent-cyan'
                    : 'text-gray-400 hover:text-white hover:bg-dark-700/50'
                }`}
                style={{ color: activeTab === tab.id ? 'var(--color-accent)' : undefined }}
              >
                <tab.icon size={16} />
                <span className="text-sm">{tab.label}</span>
              </button>
            ))}
          </nav>
        </div>

        {/* Content */}
        <div className="flex-1 p-6 overflow-y-auto">
          {/* Appearance Tab */}
          {activeTab === 'appearance' && (
            <div className="space-y-6">
              <h3 className="text-xl font-bold text-white">Appearance</h3>
              
              {/* Theme */}
              <div>
                <label className="block text-sm text-gray-400 mb-2">Color Theme</label>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                  {Object.entries(THEMES).map(([key, theme]) => (
                    <button
                      key={key}
                      onClick={() => updateSetting('theme', key)}
                      className={`p-3 rounded-lg border transition-all text-left ${
                        settings.theme === key
                          ? 'border-accent-cyan bg-dark-700'
                          : 'border-dark-600 hover:border-dark-500'
                      }`}
                      style={{ 
                        borderColor: settings.theme === key ? 'var(--color-accent)' : undefined 
                      }}
                    >
                      <div className="flex gap-1 mb-2">
                        <div className="w-4 h-4 rounded" style={{ backgroundColor: theme.background }} />
                        <div className="w-4 h-4 rounded" style={{ backgroundColor: theme.accent }} />
                        <div className="w-4 h-4 rounded" style={{ backgroundColor: theme.accentGreen }} />
                        <div className="w-4 h-4 rounded" style={{ backgroundColor: theme.accentRed }} />
                      </div>
                      <span className="text-sm text-white">{theme.name}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Chart Type */}
              <div>
                <label className="block text-sm text-gray-400 mb-2">Chart Type</label>
                <div className="flex gap-2">
                  {Object.entries(CHART_TYPES).map(([key, value]) => (
                    <button
                      key={key}
                      onClick={() => updateSetting('chartType', value)}
                      className={`px-4 py-2 rounded-lg border transition-all ${
                        settings.chartType === value
                          ? 'border-accent-cyan bg-dark-700 text-white'
                          : 'border-dark-600 text-gray-400 hover:text-white'
                      }`}
                      style={{ 
                        borderColor: settings.chartType === value ? 'var(--color-accent)' : undefined 
                      }}
                    >
                      {key.charAt(0) + key.slice(1).toLowerCase()}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Layout Tab */}
          {activeTab === 'layout' && (
            <div className="space-y-6">
              <h3 className="text-xl font-bold text-white">Layout</h3>
              
              <div>
                <label className="block text-sm text-gray-400 mb-2">Layout Style</label>
                <div className="grid grid-cols-3 gap-3">
                  {Object.entries(LAYOUTS).map(([key, value]) => (
                    <button
                      key={key}
                      onClick={() => updateSetting('layout', value)}
                      className={`p-4 rounded-lg border transition-all ${
                        settings.layout === value
                          ? 'border-accent-cyan bg-dark-700'
                          : 'border-dark-600 hover:border-dark-500'
                      }`}
                      style={{ 
                        borderColor: settings.layout === value ? 'var(--color-accent)' : undefined 
                      }}
                    >
                      <Layout size={24} className="mx-auto mb-2" style={{ color: 'var(--color-accent)' }} />
                      <span className="text-sm text-white">{key}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Cards Tab */}
          {activeTab === 'cards' && (
            <div className="space-y-6">
              <h3 className="text-xl font-bold text-white">Card Size</h3>
              
              <div>
                <label className="block text-sm text-gray-400 mb-2">Default Card Size</label>
                <div className="grid grid-cols-3 gap-3">
                  {Object.entries(CARD_SIZES).map(([key, value]) => (
                    <button
                      key={key}
                      onClick={() => updateSetting('cardSize', value)}
                      className={`p-4 rounded-lg border transition-all ${
                        settings.cardSize === value
                          ? 'border-accent-cyan bg-dark-700'
                          : 'border-dark-600 hover:border-dark-500'
                      }`}
                      style={{ 
                        borderColor: settings.cardSize === value ? 'var(--color-accent)' : undefined 
                      }}
                    >
                      <Maximize2 size={24} className="mx-auto mb-2" style={{ color: 'var(--color-accent)' }} />
                      <span className="text-sm text-white">{key.toLowerCase()}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Resize Hint */}
              <div className="p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <p className="text-sm text-gray-300">
                  💡 <strong>Tip:</strong> Cards can be resized by dragging the bottom-right corner. 
                  Similar to Windows folder resizing in Sentinel Edge.
                </p>
              </div>
            </div>
          )}

          {/* Providers Tab */}
          {activeTab === 'providers' && (
            <div className="space-y-6">
              <h3 className="text-xl font-bold text-white">Data Providers</h3>
              
              <div>
                <label className="block text-sm text-gray-400 mb-2">Select Provider</label>
                <div className="space-y-2">
                  {Object.entries(PROVIDERS).map(([key, value]) => (
                    <button
                      key={key}
                      onClick={() => updateSetting('provider', value)}
                      className={`w-full p-3 rounded-lg border transition-all flex items-center justify-between ${
                        settings.provider === value
                          ? 'border-accent-cyan bg-dark-700'
                          : 'border-dark-600 hover:border-dark-500'
                      }`}
                      style={{ 
                        borderColor: settings.provider === value ? 'var(--color-accent)' : undefined 
                      }}
                    >
                      <span className="text-white">{key}</span>
                      {value === 'finra' && (
                        <span className="text-xs px-2 py-1 rounded bg-accent-green/20 text-accent-green">FREE</span>
                      )}
                      {(value === 'polygon' || value === 'intrinio') && (
                        <span className="text-xs px-2 py-1 rounded bg-yellow-500/20 text-yellow-400">API KEY</span>
                      )}
                    </button>
                  ))}
                </div>
              </div>

              {/* API Key Input */}
              {(settings.provider === 'polygon' || settings.provider === 'intrinio') && (
                <div>
                  <label className="block text-sm text-gray-400 mb-2">
                    {settings.provider.toUpperCase()} API Key
                  </label>
                  <input
                    type="password"
                    value={settings[`${settings.provider}ApiKey`] || ''}
                    onChange={(e) => updateSetting(`${settings.provider}ApiKey`, e.target.value)}
                    className="w-full p-3 rounded-lg bg-dark-900 border border-dark-600 text-white focus:border-accent-cyan outline-none"
                    placeholder={`Enter ${settings.provider.toUpperCase()} API key...`}
                  />
                </div>
              )}
            </div>
          )}

          {/* Integrations Tab */}
          {activeTab === 'integrations' && (
            <div className="space-y-6">
              <h3 className="text-xl font-bold text-white">Integrations</h3>
              
              {/* Grafana URL */}
              <div>
                <label className="block text-sm text-gray-400 mb-2">
                  <Link size={14} className="inline mr-1" />
                  Grafana Dashboard URL
                </label>
                <input
                  type="url"
                  value={settings.grafanaUrl || ''}
                  onChange={(e) => updateSetting('grafanaUrl', e.target.value)}
                  className="w-full p-3 rounded-lg bg-dark-900 border border-dark-600 text-white focus:border-accent-cyan outline-none"
                  placeholder="https://your-grafana-instance.com/d/..."
                />
                <p className="text-xs text-gray-500 mt-1">
                  Link your Grafana dashboard for external monitoring
                </p>
              </div>

              {/* Plotly URL */}
              <div>
                <label className="block text-sm text-gray-400 mb-2">
                  <Activity size={14} className="inline mr-1" />
                  Plotly Server URL
                </label>
                <input
                  type="url"
                  value={settings.plotlyUrl || ''}
                  onChange={(e) => updateSetting('plotlyUrl', e.target.value)}
                  className="w-full p-3 rounded-lg bg-dark-900 border border-dark-600 text-white focus:border-accent-cyan outline-none"
                  placeholder="https://your-plotly-server.com"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Plotly chart server for advanced visualizations
                </p>
              </div>
            </div>
          )}

          {activeTab === 'general-api' && generalApi && (
            <div className="space-y-6" data-testid="general-api-settings">
              <div><h3 className="text-xl font-bold text-white">General API</h3><p className="mt-1 text-sm text-gray-400">Connect Flare to an Archive replay as a market observer. Flare publishes its own dark-pool observations; Archive never creates signals.</p></div>
              <label className="flex items-center gap-2 text-sm text-gray-300"><input type="checkbox" checked={generalApi.enabled} onChange={(event) => updateGeneralApi({ enabled: event.target.checked })} /> Enabled</label>
              <div className="grid gap-4 md:grid-cols-2">
                <label className="text-sm text-gray-400">Archive General URL<input className="mt-2 w-full rounded-lg border border-dark-600 bg-dark-900 p-3 text-white" value={generalApi.base_url} onChange={(event) => updateGeneralApi({ base_url: event.target.value })} /></label>
                <label className="text-sm text-gray-400">Replay run ID<input className="mt-2 w-full rounded-lg border border-dark-600 bg-dark-900 p-3 text-white" value={generalApi.run_id} onChange={(event) => updateGeneralApi({ run_id: event.target.value })} placeholder="run-..." /></label>
                <label className="text-sm text-gray-400">Participant ID<input className="mt-2 w-full rounded-lg border border-dark-600 bg-dark-900 p-3 text-white" value={generalApi.participant_id} onChange={(event) => updateGeneralApi({ participant_id: event.target.value })} /></label>
                <label className="text-sm text-gray-400">Observed symbols<input className="mt-2 w-full rounded-lg border border-dark-600 bg-dark-900 p-3 text-white" value={generalApi.subscribed_symbols.join(', ')} onChange={(event) => updateGeneralApi({ subscribed_symbols: event.target.value.split(',').map((part) => part.trim().toUpperCase()).filter(Boolean) })} placeholder="SPY, TSLA" /></label>
                <label className="text-sm text-gray-400">Participant token<input type="password" className="mt-2 w-full rounded-lg border border-dark-600 bg-dark-900 p-3 text-white" value={generalApiToken} onChange={(event) => setGeneralApiToken(event.target.value)} placeholder={generalApi.token_configured ? 'Stored privately — enter to replace' : 'Paste token or register'} /></label>
                <label className="text-sm text-gray-400">Timeout seconds<input type="number" min="0.1" step="0.1" className="mt-2 w-full rounded-lg border border-dark-600 bg-dark-900 p-3 text-white" value={generalApi.timeout_seconds} onChange={(event) => updateGeneralApi({ timeout_seconds: Number(event.target.value) })} /></label>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button disabled={generalApiBusy} onClick={saveGeneralApi} className="rounded-lg bg-accent-cyan px-4 py-2 text-sm font-semibold text-dark-900">Save</button>
                <button disabled={generalApiBusy} onClick={() => generalApiAction('test')} className="rounded-lg border border-dark-600 px-4 py-2 text-sm text-white">Test</button>
                <button disabled={generalApiBusy || !generalApi.run_id} onClick={() => generalApiAction('register')} className="rounded-lg border border-accent-cyan px-4 py-2 text-sm text-accent-cyan">Register</button>
                {generalApi.token_configured && <span className="text-xs text-accent-green">Token configured</span>}
              </div>
              {generalApiMessage && <p className="text-sm text-gray-400">{generalApiMessage}</p>}
            </div>
          )}

          {/* Alerts Tab */}
          {activeTab === 'alerts' && (
            <div className="space-y-6">
              <h3 className="text-xl font-bold text-white">Notifications</h3>
              
              {/* Sound */}
              <div className="flex items-center justify-between p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <div>
                  <span className="text-white">Sound Alerts</span>
                  <p className="text-xs text-gray-400">Play sound on whale trades</p>
                </div>
                <button
                  onClick={() => updateSetting('soundEnabled', !settings.soundEnabled)}
                  className={`w-12 h-6 rounded-full transition-all ${
                    settings.soundEnabled ? 'bg-accent-cyan' : 'bg-dark-600'
                  }`}
                  style={{ 
                    backgroundColor: settings.soundEnabled ? 'var(--color-accent)' : undefined 
                  }}
                >
                  <div 
                    className={`w-5 h-5 rounded-full bg-white transition-transform ${
                      settings.soundEnabled ? 'translate-x-6' : 'translate-x-0.5'
                    }`} 
                  />
                </button>
              </div>

              {/* Desktop */}
              <div className="flex items-center justify-between p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <div>
                  <span className="text-white">Desktop Notifications</span>
                  <p className="text-xs text-gray-400">Browser push notifications</p>
                </div>
                <button
                  onClick={() => updateSetting('desktopAlerts', !settings.desktopAlerts)}
                  className={`w-12 h-6 rounded-full transition-all ${
                    settings.desktopAlerts ? 'bg-accent-cyan' : 'bg-dark-600'
                  }`}
                  style={{ 
                    backgroundColor: settings.desktopAlerts ? 'var(--color-accent)' : undefined 
                  }}
                >
                  <div 
                    className={`w-5 h-5 rounded-full bg-white transition-transform ${
                      settings.desktopAlerts ? 'translate-x-6' : 'translate-x-0.5'
                    }`} 
                  />
                </button>
              </div>

              {/* Discord Webhook */}
              <div>
                <label className="block text-sm text-gray-400 mb-2">Discord Webhook URL</label>
                <input
                  type="url"
                  value={settings.discordWebhook || ''}
                  onChange={(e) => updateSetting('discordWebhook', e.target.value)}
                  className="w-full p-3 rounded-lg bg-dark-900 border border-dark-600 text-white focus:border-accent-cyan outline-none"
                  placeholder="https://discord.com/api/webhooks/..."
                />
              </div>
            </div>
          )}

          {/* Tutorial Tab */}
          {activeTab === 'tutorial' && (
            <div className="space-y-6">
              <h3 className="text-xl font-bold text-white">Tutorial & Setup Guide</h3>
              
              {/* API Setup */}
              <div className="p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <h4 className="text-lg font-semibold text-white mb-2">🚀 Quick Start</h4>
                <ol className="list-decimal list-inside text-sm text-gray-300 space-y-2">
                  <li>Open the app - data generates automatically</li>
                  <li>Adjust whale threshold slider (10K-200K shares)</li>
                  <li>Click a stock card to view detailed charts</li>
                  <li>Set up alerts in Settings → Alerts</li>
                </ol>
              </div>

              {/* Grafana */}
              <div className="p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <h4 className="text-lg font-semibold text-white mb-2">
                  📊 Connect Grafana
                </h4>
                <ol className="list-decimal list-inside text-sm text-gray-300 space-y-2">
                  <li>Install Infinity plugin: <code className="bg-dark-800 px-1 rounded">grafana-cli plugins install yesoreyeram-infinity-datasource</code></li>
                  <li>Add Data Source → Infinity</li>
                  <li>URL: <code className="bg-dark-800 px-1 rounded">http://localhost:8000/grafana/table?symbol=AAPL</code></li>
                  <li>Parse with UQL: <code className="bg-dark-800 px-1 rounded">parse-json</code></li>
                </ol>
              </div>

              {/* Plotly */}
              <div className="p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <h4 className="text-lg font-semibold text-white mb-2">
                  📈 Connect Plotly
                </h4>
                <p className="text-sm text-gray-300 mb-2">
                  Use Plotly JSON from these endpoints:
                </p>
                <ul className="text-sm text-gray-300 space-y-1">
                  <li>• Area Chart: <code className="bg-dark-800 px-1 rounded">/visualization/area?symbol=AAPL</code></li>
                  <li>• Bar Chart: <code className="bg-dark-800 px-1 rounded">/visualization/bar?symbol=AAPL</code></li>
                  <li>• Combined: <code className="bg-dark-800 px-1 rounded">/visualization/combined?symbol=AAPL</code></li>
                </ul>
              </div>

              {/* Discord Bot */}
              <div className="p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <h4 className="text-lg font-semibold text-white mb-2">
                  💬 Discord Slash Commands
                </h4>
                <ul className="text-sm text-gray-300 space-y-1">
                  <li>• <code className="bg-dark-800 px-1 rounded">/darkpool symbol:AAPL tier:T1</code> - Get darkpool data</li>
                  <li>• <code className="bg-dark-800 px-1 rounded">/setalert symbol:NVDA threshold:100000</code> - Set whale alert</li>
                  <li>• <code className="bg-dark-800 px-1 rounded">/alertstatus</code> - Show active alerts</li>
                </ul>
              </div>

              {/* Images Placeholder */}
              <div className="p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <h4 className="text-lg font-semibold text-white mb-2">🖼️ Interface Guide</h4>
                <div className="grid grid-cols-2 gap-2 text-xs text-gray-400">
                  <div className="p-2 bg-dark-800 rounded">
                    <div className="text-accent-cyan mb-1">Stock Cards</div>
                    <p>Click to select, shows price/volume</p>
                  </div>
                  <div className="p-2 bg-dark-800 rounded">
                    <div className="text-accent-yellow mb-1">Whale Slider</div>
                    <p>Adjusts threshold (10K-200K)</p>
                  </div>
                  <div className="p-2 bg-dark-800 rounded">
                    <div className="text-accent-green mb-1">Buy (Green)</div>
                    <p>Long positions, buying pressure</p>
                  </div>
                  <div className="p-2 bg-dark-800 rounded">
                    <div className="text-accent-red mb-1">Sell (Red)</div>
                    <p>Short positions, selling pressure</p>
                  </div>
                </div>
              </div>

              {/* Greek Letters */}
              <div className="p-4 rounded-lg bg-dark-700/50 border border-dark-600">
                <h4 className="text-lg font-semibold text-white mb-2">
                  αβΓδ Options Greeks
                </h4>
                <div className="grid grid-cols-5 gap-2 text-center">
                  {Object.entries(GREEK_SYMBOLS).map(([key, symbol]) => (
                    <div key={key} className="p-2 bg-dark-800 rounded">
                      <span 
                        className="text-2xl font-bold"
                        style={{ color: 'var(--color-accent)' }}
                      >
                        {symbol}
                      </span>
                      <div className="text-xs text-gray-400 mt-1">{key}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-2 rounded-lg hover:bg-dark-700 text-gray-400 hover:text-white transition-all"
        >
          <X size={20} />
        </button>
      </div>
    </div>
  );
}
