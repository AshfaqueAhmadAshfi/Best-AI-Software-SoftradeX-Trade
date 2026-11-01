import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, BarChart3, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, X } from 'lucide-react';

type Candle = [number, number, number, number, number, number];
type Market = { symbol: string; label?: string; pair?: string; kind?: string; digits?: number };
type Health = { ok: boolean; hosted: boolean; uptime_engine: boolean; source?: string | null };

type Setup = { action: 'BUY' | 'SELL'; label: string; price: number };

const PASSWORD = '0186';

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

function formatPrice(value: number) {
  if (!Number.isFinite(value)) return '--';
  return value >= 1000 ? value.toLocaleString('en-US', { maximumFractionDigits: 2 }) : value.toFixed(4);
}

function buildSetups(candles: Candle[]): Setup[] {
  const valid = candles.filter((bar) => Number.isFinite(bar[2]) && Number.isFinite(bar[3]));
  if (!valid.length) return [];
  const current = valid[valid.length - 1];
  const highs = valid.map((bar) => bar[2]);
  const lows = valid.map((bar) => bar[3]);
  const high = Math.max(...highs);
  const low = Math.min(...lows);
  const middle = (high + low) / 2;
  return [
    { action: 'BUY', label: "Today's Lower Low", price: low },
    { action: 'SELL', label: "Today's Higher High", price: high },
    { action: 'BUY', label: 'Weekly Support', price: low + (middle - low) * 0.25 },
    { action: 'SELL', label: 'Weekly Resistance', price: high - (high - middle) * 0.25 },
    current[4] <= middle
      ? { action: 'BUY', label: 'Previous Week Floor', price: low }
      : { action: 'SELL', label: 'Previous Week Ceiling', price: high }
  ];
}

function GlassButton({ children, onClick, ghost = false }: { children: React.ReactNode; onClick?: () => void; ghost?: boolean }) {
  return <button className={`glass-button ${ghost ? 'ghost' : ''}`} onClick={onClick}>{children}</button>;
}

function AboutModal({ onClose }: { onClose: () => void }) {
  return <div className="modal-backdrop" onClick={onClose}>
    <section className="about-modal" onClick={(event) => event.stopPropagation()}>
      <button className="close-button" onClick={onClose} aria-label="Close"><X size={18} /></button>
      <img src="/static/my-image.jpg" alt="SoftradeX developer" />
      <div>
        <p className="eyebrow">SOFTRADEX · BUILT WITH PURPOSE</p>
        <h2>About the Developer</h2>
        <p><strong>SoftradeX Developer</strong> builds practical trading dashboards, market tools, and clean real-time interfaces.</p>
        <p><strong>My Father</strong> is my first inspiration, strongest support, and the reason I keep building with discipline.</p>
      </div>
    </section>
  </div>;
}

function PasswordModal({ onUnlock, onClose }: { onUnlock: () => void; onClose: () => void }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (value === PASSWORD) onUnlock();
    else setError('Incorrect password — dashboard access denied');
  };
  return <div className="modal-backdrop" onClick={onClose}>
    <form className="password-modal" onSubmit={submit} onClick={(event) => event.stopPropagation()}>
      <div className="lock-mark"><LockKeyhole size={21} /></div>
      <p className="eyebrow">SECURE DASHBOARD ACCESS</p>
      <h2>Unlock your terminal</h2>
      <p className="muted">Enter the access password to view live market tools.</p>
      <label>Password<input autoFocus type="password" value={value} onChange={(event) => setValue(event.target.value)} /></label>
      <p className="form-error">{error}</p>
      <div className="modal-actions"><GlassButton ghost onClick={onClose}>Cancel</GlassButton><button className="primary-button" type="submit">Unlock Dashboard</button></div>
    </form>
  </div>;
}

function Chart({ candles, setups }: { candles: Candle[]; setups: Setup[] }) {
  const width = 900;
  const height = 330;
  const padding = { top: 24, right: 70, bottom: 28, left: 18 };
  const values = candles.flatMap((bar) => [bar[2], bar[3]]).concat(setups.map((setup) => setup.price));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 0.0001);
  const x = (index: number) => padding.left + (index / Math.max(candles.length - 1, 1)) * (width - padding.left - padding.right);
  const y = (price: number) => padding.top + ((max - price) / range) * (height - padding.top - padding.bottom);
  const candleWidth = Math.max(2, (width - padding.left - padding.right) / Math.max(candles.length, 1) * 0.62);
  return <div className="chart-shell">
    <div className="chart-tools"><span className="chart-tool active">MT5 Chart</span><span className="chart-tool">5m</span><span className="chart-tool">＋</span><span className="chart-tool">−</span><span className="chart-tool">Indicators</span></div>
    <svg className="price-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Live candle chart">
      {[0, 1, 2, 3, 4].map((step) => { const price = max - (range * step) / 4; return <g key={step}><line x1={padding.left} x2={width - padding.right} y1={y(price)} y2={y(price)} className="grid-line" /><text x={width - padding.right + 8} y={y(price) + 4} className="axis-label">{formatPrice(price)}</text></g>; })}
      {candles.map((bar, index) => { const high = y(bar[2]); const low = y(bar[3]); const open = y(bar[1]); const close = y(bar[4]); const bullish = bar[4] >= bar[1]; return <g key={bar[0]}><line x1={x(index)} x2={x(index)} y1={high} y2={low} className={bullish ? 'wick buy' : 'wick sell'} /><rect x={x(index) - candleWidth / 2} y={Math.min(open, close)} width={candleWidth} height={Math.max(1.5, Math.abs(close - open))} className={bullish ? 'candle buy' : 'candle sell'} /></g>; })}
      {setups.map((setup) => <g key={`${setup.action}-${setup.label}`}><line x1={padding.left} x2={width - padding.right} y1={y(setup.price)} y2={y(setup.price)} className={`setup-line ${setup.action.toLowerCase()}`} /><rect x={padding.left + 8} y={y(setup.price) - 12} width={142} height={22} rx={6} className={`setup-label-bg ${setup.action.toLowerCase()}`} /><text x={padding.left + 16} y={y(setup.price) + 3} className="setup-label">{setup.action} · {formatPrice(setup.price)}</text></g>)}
    </svg>
  </div>;
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [symbol, setSymbol] = useState('XAUUSDm');
  const [candles, setCandles] = useState<Candle[]>([]);
  const [showDashboard, setShowDashboard] = useState(false);
  const [showAbout, setShowAbout] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');

  const setups = useMemo(() => buildSetups(candles), [candles]);
  const currentMarket = markets.find((market) => market.symbol === symbol);

  async function loadMarkets() {
    try {
      const data = await getJson<{ markets: Market[] }>('/api/chart/symbols');
      setMarkets(data.markets || []);
    } catch (reason) { setError(String(reason)); }
  }
  async function loadData(nextSymbol = symbol) {
    try {
      const [nextHealth, data] = await Promise.all([
        getJson<Health>('/api/health'),
        getJson<{ bars: Candle[] }>('/api/chart/candles?symbol=' + encodeURIComponent(nextSymbol) + '&tf=5m&count=90')
      ]);
      setHealth(nextHealth);
      setCandles(data.bars || []);
    } catch (reason) { setError(String(reason)); }
  }
  useEffect(() => { void loadMarkets(); void loadData(); const timer = window.setInterval(() => void loadData(), 5000); return () => window.clearInterval(timer); }, []);
  useEffect(() => { if (showDashboard) void loadData(symbol); }, [symbol, showDashboard]);

  const unlock = () => { setShowPassword(false); setShowDashboard(true); };
  return <div className="app-shell">
    <div className="ambient ambient-one" /><div className="ambient ambient-two" />
    <header className="topbar"><div className="brand"><div className="brand-mark"><BarChart3 size={21} /></div><div><strong>SoftradeX</strong><span>Glass market intelligence</span></div></div><div className="top-actions"><span className="status"><i />{health?.hosted ? 'PUBLIC FEED' : 'CONNECTING'}</span><button className="icon-button" onClick={() => setShowAbout(true)} title="About Me"><Sparkles size={17} /></button></div></header>
    {!showDashboard ? <main className="login-layout"><section className="hero-copy"><p className="eyebrow">REAL-TIME MARKET WORKSPACE</p><h1>Trade with a clearer<br /><em>point of view.</em></h1><p className="hero-text">A calm glass terminal for price structure, extreme setups, and market pressure.</p><div className="hero-points"><span><ShieldCheck size={16} /> Public market feed</span><span><RefreshCw size={16} /> Live HTTP polling</span></div></section><section className="login-card"><div className="card-icon"><BarChart3 size={22} /></div><p className="eyebrow">SOFTRADEX TERMINAL</p><h2>Open the dashboard</h2><p className="muted">Preview the glass trading workspace and live chart tools.</p><GlassButton onClick={() => setShowPassword(true)}>View Dashboard <ArrowUp size={16} /></GlassButton><button className="about-login" onClick={() => setShowAbout(true)}>About Me</button><div className="card-foot"><span>Engine</span><strong>{health?.uptime_engine ? 'Online' : 'Waking up'}</strong></div></section></main> : <main className="dashboard-layout"><aside className="side-rail"><button className="rail-button active"><BarChart3 size={17} /><span>MARKETS</span></button><button className="rail-button" onClick={() => setShowAbout(true)}><Sparkles size={17} /><span>ABOUT ME</span></button></aside><section className="dashboard-content"><div className="dashboard-heading"><div><p className="eyebrow">LIVE MARKET WORKSPACE</p><h1>Good morning, <em>trader.</em></h1></div><div className="market-select-wrap"><span>MARKET</span><select value={symbol} onChange={(event) => setSymbol(event.target.value)}>{(markets.length ? markets : [{ symbol: 'XAUUSDm', label: 'Gold Spot' }]).slice(0, 12).map((market) => <option key={market.symbol} value={market.symbol}>{market.label || market.symbol}</option>)}</select></div></div><div className="metric-row"><div className="metric-card"><span>MARKET</span><strong>{currentMarket?.label || symbol}</strong><small>Live public feed</small></div><div className="metric-card"><span>LAST PRICE</span><strong>{candles.length ? formatPrice(candles[candles.length - 1][4]) : '--'}</strong><small>5 minute candles</small></div><div className="metric-card"><span>SETUPS</span><strong>{setups.length} planned</strong><small>Extreme structure system</small></div></div><div className="main-grid"><section className="panel chart-panel"><div className="panel-heading"><div><span className="panel-kicker">PRICE ACTION</span><h2>{currentMarket?.label || symbol}</h2></div><span className="live-dot"><i /> LIVE</span></div><Chart candles={candles} setups={setups} /></section><section className="panel setup-panel"><div className="panel-heading"><div><span className="panel-kicker">NEXT SETUPS</span><h2>Extreme execution</h2></div><span className="setup-count">{setups.length}/5</span></div><div className="setup-list">{setups.map((setup, index) => <div className={`setup-row ${setup.action.toLowerCase()}`} key={`${setup.label}-${index}`}><div className="setup-index">0{index + 1}</div><div><strong>{setup.label}</strong><small>{setup.action === 'BUY' ? 'Support / lower extreme' : 'Resistance / upper extreme'}</small></div><b>{formatPrice(setup.price)}</b>{setup.action === 'BUY' ? <ArrowUp size={16} /> : <ArrowDown size={16} />}</div>)}</div></section></div></section></main>}
    {error && <div className="error-toast">{error}</div>}
    {showAbout && <AboutModal onClose={() => setShowAbout(false)} />}
    {showPassword && <PasswordModal onUnlock={unlock} onClose={() => setShowPassword(false)} />}
  </div>;
}

export default App;
