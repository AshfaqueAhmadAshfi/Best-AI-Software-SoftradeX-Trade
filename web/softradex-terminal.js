/**
 * Exness MT5 Pro Chart Terminal
 * Candles, 24h statistics and ticks come straight from the connected Exness
 * MetaTrader 5 account. No exchange feed, no simulated prices, no seed data.
 * Features:
 * - Real-time Candlestick & Volume Canvas Chart with High-DPI support
 * - SMA(7) and SMA(25) Technical Overlays
 * - Dedicated low-latency WebSocket tick stream (/ws/chart)
 * - 24h ticker ribbon (price, change, high, low, tick activity)
 * - Timeframe switching (1m, 5m, 15m, 1h, 4h, 1D)
 * - 100% native, 0% TradingView dependency
 */
(function () {
  'use strict';

  // --- Feed identity (additive hook) -----------------------------------
  // The default page keeps the Exness URLs; a page can pre-set
  // window.SOFTRADEX_CONFIG (see bd.html) to feed this same terminal from
  // another backend catalogue without touching any Exness behaviour.
  const FEED_CFG = (typeof window.SOFTRADEX_CONFIG === 'object' && window.SOFTRADEX_CONFIG) || {};
  const FEED_NAME = FEED_CFG.feedName || 'Exness';
  const SYMBOLS_URL = FEED_CFG.symbolsUrl || '/api/chart/symbols';
  const FEED_WS_PATH = FEED_CFG.wsPath || '/ws/chart';

  // --- Configuration & State ---
  const MARKETS = {};              // filled live from /api/chart/symbols
  const TF_SECONDS = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 };
  const PRICE_AXIS_W = 76;
  let marketsReady = false;
  let feedSource = 'mt5';          // 'public' when no MT5 terminal is connected
  let pendingSymbol = '';
  let currentSymbol = '';
  let currentInterval = '5m';
  let klines = []; // Array of { time, open, high, low, close, volume, whale?: {...} }
  let tickerData = { price: 0, open24: 0, change: 0, high: 0, low: 0, vol: 0 };
  let chartReady = false;
  let serverOffsetSec = 0;
  let lastTickAt = 0;
  let lastWhaleScan = 0;
  let feed = null;
  let feedReconnectTimer = null;
  let feedRetry = 0;
  let lastFrameAt = 0;             // any frame (tick or heartbeat) proves the socket is alive
  let feedErrorRetryTimer = null;
  let marketsRetryTimer = null;
  let marketsRetryMs = 2000;
  let canvas = null;
  let ctx = null;
  let crosshair = null; // { x, y, candle }
  let dpr = window.devicePixelRatio || 1;

  // Viewport & Interactive Pan / Zoom Navigation State
  let visibleCandlesCount = 55; // Zoom level: how many candles fit in viewport (16 to 220)
  let panOffset = 0;            // Pixels panned back into history (0 = live, > 0 = history)
  let isDragging = false;
  let dragStartX = 0;
  let dragStartPan = 0;
  let hasMovedDuringDrag = false;

  let lastWhaleAlert = null; // kept for the chart alert banner (set from big prints)

  // Canvas Rounded Rectangle helper
  function drawRoundRect(c, x, y, w, h, r) {
    if (w < 2 * r) r = w / 2;
    if (h < 2 * r) r = h / 2;
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  // Detect Institutional / Whale volume anomalies in historical & incoming klines
  function detectWhaleActivities(list) {
    if (!list || list.length < 5) return;
    const vols = list.map(c => c.volume).filter(v => v > 0);
    if (vols.length < 5) return;
    const sum = vols.reduce((a, b) => a + b, 0);
    const mean = sum / vols.length;
    const variance = vols.reduce((a, v) => a + Math.pow(v - mean, 2), 0) / vols.length;
    const stdDev = Math.sqrt(variance);
    const threshold = mean + 1.25 * stdDev;

    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      // Keep real-time WebSocket verified whale data and the real whale-tape
      // prints intact - those say WHO bought, the volume scan only says WHEN
      // volume was unusual.
      if (c.whale && (c.whale.realtime || c.whale.tape)) continue;

      if (c.volume >= threshold && c.volume > mean * 1.35) {
        const isBull = c.close >= c.open;
        const body = Math.abs(c.close - c.open);
        const lowerWick = Math.min(c.open, c.close) - c.low;
        const upperWick = c.high - Math.max(c.open, c.close);

        let side = isBull ? 'BUY' : 'SELL';
        // Significant wick absorption analysis
        if (lowerWick > body * 1.2 && lowerWick > upperWick) {
          side = 'BUY';
        } else if (upperWick > body * 1.2 && upperWick > lowerWick) {
          side = 'SELL';
        }

        c.whale = {
          side: side,
          vol: c.volume,
          ratio: (c.volume / mean).toFixed(1),
          realtime: false
        };
      }
    }
  }

  // Real big prints (the whale tape) put a 🐋 BUY / 🐋 SELL marker on the exact
  // candle they happened in, at the side the whales took. The tape is the same
  // one the whale board reads, so chart and board can never disagree.
  let whaleTape = null;

  // Candle times arrive in ms from the public feed (and in seconds from MT5),
  // so everything is compared in ms here.
  function tapeMs(v) {
    const n = Number(v);
    if (!(n > 0)) return 0;
    return n < 1e12 ? n * 1000 : n;
  }

  function applyWhaleTape(prints, sym) {
    if (!Array.isArray(prints) || !prints.length) return;
    if (sym && currentSymbol && String(sym) !== String(currentSymbol)) return;
    if (!klines.length) return;
    for (let i = 0; i < prints.length; i++) {
      const p = prints[i];
      const t = tapeMs(p.tm);
      if (!t) continue;
      let idx = -1;
      for (let k = klines.length - 1; k >= 0; k--) {
        if (tapeMs(klines[k].time) <= t) { idx = k; break; }
      }
      if (idx < 0) continue;
      const c = klines[idx];
      if (c.whale && c.whale.realtime) continue;
      c.whale = {
        side: String(p.side || '').toUpperCase() === 'SELL' ? 'SELL' : 'BUY',
        vol: Number(p.ticks) || 0,
        ratio: '',
        px: Number(p.px) || 0,
        tm: Number(p.tm) || 0,
        realtime: false,
        tape: true,
      };
    }
  }

  function $(id) {
    return document.getElementById(id);
  }

  function formatNumber(num, decimals = 2) {
    if (isNaN(num) || num === null || num === undefined) return '--';
    return Number(num).toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    });
  }

  function formatCompact(num) {
    const n = Number(num);
    if (isNaN(n)) return '--';
    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(2) + 'K';
    return n.toFixed(2);
  }

  function candleOrderFlow(candle) {
    const terminal = window.TradersTerminal;
    if (!terminal || typeof terminal.getCandleFlow !== 'function' || !candle) {
      return { buy: 0, sell: 0 };
    }
    const seconds = TF_SECONDS[currentInterval] || 300;
    try {
      return terminal.getCandleFlow(candle.time, seconds) || { buy: 0, sell: 0 };
    } catch (e) {
      return { buy: 0, sell: 0 };
    }
  }

  // BDT markets (BD Stock Market dashboard) must never print a dollar sign.
  function currencyMark(symbol) {
    const meta = MARKETS[symbol] || {};
    const quote = String(meta.pair || meta.currency || 'USD').toUpperCase();
    return quote === 'BDT' ? '৳' : '$';
  }

  function formatTime(timestamp, interval) {
    // MT5 stamps are server-clock epochs; shift them back to the local clock.
    const d = new Date(Number(timestamp) - serverOffsetSec * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    if (interval === '1d') {
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${month}/${day}`;
    }
    return `${hh}:${mm}`;
  }

  // --- Calculate Simple Moving Average (SMA) ---
  function computeSMA(data, period) {
    const sma = new Array(data.length).fill(null);
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      sum += data[i].close;
      if (i >= period) {
        sum -= data[i - period].close;
        sma[i] = sum / period;
      } else if (i === period - 1) {
        sma[i] = sum / period;
      }
    }
    return sma;
  }

  // --------------------------------------------------------------- helpers
  function escHtml(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
  }

  function normSym(v) {
    return String(v == null ? '' : v).toUpperCase().replace(/[^A-Z0-9]/g, '');
  }

  function feedLabel() {
    return feedSource === 'public' ? 'Public Feed' : FEED_NAME;
  }

  function setStatus(title, sub, kind) {
    const overlay = $('chart-status-overlay');
    if (!overlay) return;
    if (!title) {
      overlay.hidden = true;
      return;
    }
    overlay.hidden = false;
    overlay.className = 'chart-status-overlay ' + (kind || 'loading');
    const t = $('chart-status-title');
    const s = $('chart-status-sub');
    if (t) t.textContent = title;
    if (s) s.textContent = sub || '';
  }

  let feedPulseStale = false;

  function setFeedPulse(state) {
    const pulse = $('b-term-pulse');
    if (!pulse) return;
    feedPulseStale = (state === 'stale');
    pulse.classList.remove('online', 'stale');
    if (state === 'stale') {
      pulse.classList.add('stale');
      pulse.title = 'Exness ticks paused - market may be closed or the feed is slow';
    } else if (state) {
      pulse.classList.add('online');
      pulse.title = 'Live Exness MT5 tick stream';
    } else {
      pulse.title = 'Waiting for the Exness terminal…';
    }
  }

  // ------------------------------------------------------- Exness catalog
  async function apiJson(path, timeoutMs) {
    // A wedged request must never stall the chart (or a polling host): every
    // call is bounded, so a slow public feed just fails this round.
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl
      ? setTimeout(() => { try { ctrl.abort(); } catch (e) {} }, Number(timeoutMs) || 15000)
      : null;
    try {
      const resp = await fetch(path, {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: ctrl ? ctrl.signal : undefined
      });
      if (!resp.ok) {
        let detail = 'HTTP ' + resp.status;
        try {
          const j = await resp.json();
          if (j && j.detail) detail = j.detail;
        } catch (e) {}
        throw new Error(detail);
      }
      return await resp.json();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  function resolveSymbol(sym) {
    const raw = normSym(sym);
    if (!raw) return '';
    const keys = Object.keys(MARKETS);
    for (const key of keys) {
      if (normSym(key) === raw) return key;
      const alts = (MARKETS[key].alts || []).map(normSym);
      if (alts.indexOf(raw) !== -1) return key;
    }
    const base = raw.replace(/M$/, '').replace(/247$/, '');
    for (const key of keys) {
      if (normSym(key).replace(/M$/, '') === base) return key;
    }
    // Same instrument, different broker suffix (XAUUSD247m -> XAUUSDm, ...)
    if (raw.indexOf('XAU') === 0 && raw.indexOf('GBP') === -1 && MARKETS['XAUUSDm']) return 'XAUUSDm';
    if (raw.indexOf('BTC') === 0 && MARKETS['BTCUSDTm']) return 'BTCUSDTm';
    if (raw.indexOf('ETH') === 0 && MARKETS['ETHUSDm']) return 'ETHUSDm';
    if ((raw.indexOf('NAS') === 0 || raw.indexOf('US100') === 0) && MARKETS['USTECm']) return 'USTECm';
    return '';
  }

  let currentCategory = 'all';
  let marketSearchQuery = '';

  function renderSymbolTabs() {
    const tabs = $('binance-symbol-tabs');
    if (!tabs) return;
    const q = (marketSearchQuery || '').trim().toLowerCase();
    const allKeys = Object.keys(MARKETS);
    const keys = allKeys.filter(key => {
      const m = MARKETS[key] || {};
      const kind = (m.kind || 'other').toLowerCase();
      // A search is global: typing "gold" must find gold even while the
      // CRYPTO tab is selected. The category only narrows the plain list.
      if (!q && currentCategory !== 'all' && kind !== currentCategory) {
        return false;
      }
      if (q) {
        const text = (key + ' ' + (m.label || '') + ' ' + (m.pair || '') + ' ' + (m.description || '')).toLowerCase();
        if (text.indexOf(q) === -1) return false;
      }
      return true;
    });

    if (keys.length === 0) {
      const emptyLabel = q ? 'No markets match your search'
        : ('No markets found in ' + currentCategory.toUpperCase());
      tabs.innerHTML = '<span style="font-size:11px;color:#64748b;padding:4px 8px;white-space:nowrap;">' + escHtml(emptyLabel) + '</span>';
      return;
    }

    const rows = keys.map(key => {
      const m = MARKETS[key] || {};
      const isActive = key === currentSymbol;
      return '<button type="button" class="b-tab-btn' + (isActive ? ' active' : '') + '" data-symbol="' + escHtml(key) + '" data-kind="' + escHtml(m.kind || '') + '" title="' +
        escHtml((m.description || key) + ' · ' + feedLabel()) + '">' +
        '<span class="b-icon">' + escHtml(m.icon || '📊') + '</span> ' +
        '<span class="b-sym">' + escHtml(m.label || key) + '</span>' +
        '<span class="b-pair">/' + escHtml(m.pair || 'USD') + '</span></button>';
    }).join('');
    tabs.innerHTML = rows;
    tabs.dataset.ready = '1';
    updateActiveTabs();
  }

  function updateActiveTabs() {
    let activeBtn = null;
    document.querySelectorAll('#binance-symbol-tabs .b-tab-btn').forEach(btn => {
      const isAct = btn.dataset.symbol === currentSymbol;
      btn.classList.toggle('active', isAct);
      if (isAct) activeBtn = btn;
    });
    document.querySelectorAll('#binance-tf-tabs .b-tf-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.interval === currentInterval);
    });
    document.querySelectorAll('#exness-cat-tabs .exness-cat-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.cat === currentCategory);
    });
    if (activeBtn && typeof activeBtn.scrollIntoView === 'function') {
      try {
        activeBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
      } catch (e) {}
    }
  }

  async function loadMarkets() {
    try {
      const data = await apiJson(SYMBOLS_URL);
      if (!data || !data.available || !data.symbols || !data.symbols.length) {
        setStatus(FEED_NAME + ' terminal not connected',
          (data && data.message) || 'Open the Exness MetaTrader 5 terminal and log in once.', 'error');
        scheduleMarketsRetry();
        return false;
      }
      feedSource = data.source === 'public' ? 'public' : 'mt5';
      const proTag = document.querySelector('#binance-terminal-section .term-pro-tag');
      if (proTag && feedSource === 'public') proTag.textContent = 'PUBLIC CHART';
      Object.keys(MARKETS).forEach(k => { delete MARKETS[k]; });
      data.symbols.forEach(m => {
        MARKETS[m.symbol] = Object.assign({}, m, {
          decimals: Number(m.digits) || 2,
          pairLabel: (m.label || m.symbol) + ' / ' + (m.pair || 'USD')
        });
      });
      marketsReady = true;
      marketsRetryMs = 2000;
      if (marketsRetryTimer) {
        clearTimeout(marketsRetryTimer);
        marketsRetryTimer = null;
      }
      renderSymbolTabs();
      const wanted = pendingSymbol ? resolveSymbol(pendingSymbol) : '';
      const first = wanted || resolveSymbol('XAUUSDm') || Object.keys(MARKETS)[0];
      pendingSymbol = '';
      if (first) switchMarket(first, { silent: true });
      if (feedSource === 'public') scheduleSourceRecheck();
      return true;
    } catch (err) {
      setStatus(FEED_NAME + ' feed unavailable', String(err && err.message ? err.message : err), 'error');
      scheduleMarketsRetry();
      return false;
    }
  }

  // The terminal can come back (or the account can log in) after the page has
  // already loaded. Keep asking until the catalog is here instead of leaving
  // the chart empty until a manual reload.
  function scheduleMarketsRetry() {
    if (marketsRetryTimer || marketsReady) return;
    marketsRetryTimer = setTimeout(() => {
      marketsRetryTimer = null;
      loadMarkets();
    }, marketsRetryMs);
    marketsRetryMs = Math.min(marketsRetryMs * 2, 10000);
  }

  // While the chart is on public data, keep looking for the MT5 terminal. As
  // soon as it appears the catalogue is reloaded and the live chart is
  // resubscribed, so the terminal takes over without a page reload.
  let sourceRecheckTimer = null;
  function scheduleSourceRecheck() {
    if (sourceRecheckTimer) return;
    sourceRecheckTimer = setTimeout(async () => {
      sourceRecheckTimer = null;
      if (feedSource !== 'public') return;
      pendingSymbol = currentSymbol || 'XAUUSDm';
      const ok = await loadMarkets();
      if (!ok) return;
      if (feedSource === 'mt5' && currentSymbol) {
        if (feed && feed.readyState === WebSocket.OPEN) sendSubscription();
        else connectFeed();
      }
      if (feedSource === 'public') scheduleSourceRecheck();
    }, 30000);
  }

  let chartRafPending = false;

  function scheduleChartRender() {
    if (chartRafPending) return;
    chartRafPending = true;
    requestAnimationFrame(() => {
      chartRafPending = false;
      renderChart();
    });
  }

  // SMA of just the trailing window (identical value to computeSMA's last
  // element, without sweeping the whole candle array on every mouse move).
  function tailSMA(data, period) {
    const n = data.length;
    if (n < period) return null;
    let sum = 0;
    for (let i = n - period; i < n; i++) sum += data[i].close;
    return sum / period;
  }

  // ------------------------------------------------ HTTP polling fallback
  // cPanel / Passenger cannot hold a WebSocket open, so on such a host the same
  // chart is built from the REST endpoints. Everything downstream is shared:
  // a poll produces the exact frame shape the socket sends, so the renderer,
  // whale scan, legend and ticker ribbon need no special case.
  let feedWsFails = 0;
  let pollTimer = null;
  let pollBusy = false;
  let pollCycle = 0;
  let pollTicker = null;
  let pollTickerSymbol = '';

  async function pollFeedOnce() {
    if (pollBusy || !currentSymbol) return;
    pollBusy = true;
    const symbol = currentSymbol;
    const tf = currentInterval;
    // While the TradingView chart draws the candles itself (tv-chart.js),
    // this host does not have to fetch 360 bars every five seconds - only
    // the tiny ticker request the price ribbon needs still runs. That is
    // the difference between a chart that feels slow on a shared host and
    // one that does not.
    if (window.SOFT_TV_ACTIVE) {
      try {
        const ticker = await apiJson('/api/chart/ticker?symbol=' + encodeURIComponent(symbol), 12000);
        if (symbol !== currentSymbol || !ticker) return;
        pollTicker = ticker;
        pollTickerSymbol = symbol;
        lastTickAt = Date.now();
        setFeedPulse(true);
        updateTickerDisplay(ticker);
      } catch (e) {
        setFeedPulse(false);
      } finally {
        // this branch returns early - it must release the guard itself or
        // every later cycle is skipped and the price ribbon freezes
        pollBusy = false;
      }
      return;
    }
    try {
      const data = await apiJson('/api/chart/candles?symbol=' + encodeURIComponent(symbol) +
        '&tf=' + encodeURIComponent(tf) + '&count=360', 20000);
      // The 24h ribbon changes slowly: refreshing it every third cycle keeps
      // the shared host from serving two chart requests every five seconds.
      pollCycle += 1;
      if (pollTickerSymbol !== symbol || pollCycle % 3 === 1) {
        try {
          pollTicker = await apiJson('/api/chart/ticker?symbol=' + encodeURIComponent(symbol), 12000);
          pollTickerSymbol = symbol;
        } catch (e) { /* keep the previous ribbon */ }
      }
      const ticker = pollTickerSymbol === symbol ? pollTicker : null;
      // a symbol/timeframe switch while the request was in flight wins
      if (symbol !== currentSymbol || tf !== currentInterval) return;
      if (!data || !data.bars || !data.bars.length) {
        setStatus('No candles from ' + feedLabel(),
          'The public feed returned no bars for ' + symbol + '.', 'error');
        return;
      }
      lastFrameAt = Date.now();
      lastTickAt = Date.now();
      setFeedPulse(true);
      handleFeedFrame({
        t: 'snap', s: symbol, tf: tf, meta: data.meta, bars: data.bars, ticker: ticker
      });
    } catch (err) {
      setFeedPulse(false);
    } finally {
      pollBusy = false;
    }
  }

  function startPolling() {
    if (pollTimer) return;
    setFeedPulse(true);
    void pollFeedOnce();
    pollTimer = setInterval(() => { void pollFeedOnce(); }, 5000);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function pollNow() {
    if (pollTimer) void pollFeedOnce();
  }

  // tv-chart.js turns its TradingView chart off (user toggle, or a market
  // TradingView has no symbol for) and asks for one fresh frame: without it
  // the built-in chart would stay on the last picture it had, because the
  // candle fetches were skipped while TradingView was on screen.
  window.addEventListener('chart:refresh', function () {
    if (feed && feed.readyState === WebSocket.OPEN) sendSubscription();
    else pollNow();
  });

  // chart-overlay.js publishes the whale tape every time it refreshes it. The
  // candle chart paints the markers from it (the TradingView frame cannot take
  // drawings, which is why this lives here and not in the overlay).
  window.addEventListener('whale:prints', function (ev) {
    const d = (ev && ev.detail) || {};
    whaleTape = { symbol: d.symbol || '', prints: Array.isArray(d.prints) ? d.prints : [] };
    applyWhaleTape(whaleTape.prints, whaleTape.symbol);
    // Only the candle chart paints these - a repaint while TradingView owns
    // the box would cost a frame and show nothing.
    if (!window.SOFT_TV_ACTIVE && window.SoftradeXTerminal &&
        typeof window.SoftradeXTerminal.refresh === 'function') {
      window.SoftradeXTerminal.refresh();
    }
  });

  // ------------------------------------------------------- live Exness feed
  function connectFeed() {
    if (feed && (feed.readyState === WebSocket.CONNECTING || feed.readyState === WebSocket.OPEN)) return;
    if (feedReconnectTimer) {
      clearTimeout(feedReconnectTimer);
      feedReconnectTimer = null;
    }
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let socket;
    try {
      socket = new WebSocket(proto + '//' + location.host + FEED_WS_PATH);
    } catch (err) {
      feedReconnectTimer = setTimeout(connectFeed, 1500);
      return;
    }
    feed = socket;
    socket.onopen = () => {
      feedRetry = 0;
      feedWsFails = 0;
      stopPolling();
      lastFrameAt = Date.now();
      setFeedPulse(true);
      sendSubscription();
    };
    socket.onmessage = (event) => {
      lastFrameAt = Date.now();
      let msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      handleFeedFrame(msg);
    };
    socket.onerror = () => {
      try { socket.close(); } catch (e) {}
    };
    socket.onclose = () => {
      if (feed === socket) feed = null;
      feedWsFails += 1;
      // A WSGI host refuses every upgrade: two dead sockets in a row means
      // "no WebSockets here", so switch the chart to REST polling.
      if (feedWsFails >= 2) startPolling();
      if (!pollTimer) setFeedPulse(false);
      feedRetry = Math.min(feedRetry + 1, 8);
      feedReconnectTimer = setTimeout(connectFeed,
        pollTimer ? 30000 : Math.min(400 * feedRetry, 4000));
    };
  }

  function sendSubscription() {
    pollNow();   // no-op unless the chart is on the REST polling fallback
    if (!feed || feed.readyState !== WebSocket.OPEN || !currentSymbol) return;
    feed.send(JSON.stringify({ type: 'sub', symbol: currentSymbol, tf: currentInterval }));
  }

  function handleFeedFrame(msg) {
    if (!msg || !msg.t) return;
    if (msg.t === 'snap') {
      if (msg.s !== currentSymbol) return;
      if (msg.tf && msg.tf !== currentInterval) return;
      applySnapshot(msg);
    } else if (msg.t === 'tick') {
      if (msg.s !== currentSymbol) return;
      applyTick(msg);
    } else if (msg.t === 'hb') {
      // heartbeat only - onmessage already refreshed lastFrameAt
    } else if (msg.t === 'error') {
      if (msg.s && msg.s !== currentSymbol) return;
      chartReady = false;
      setStatus('Chart error', msg.message || 'The feed could not return this chart.', 'error');
      // The server retries on its own; this is the belt-and-braces path for a
      // terminal that was restarted mid-snapshot.
      if (!feedErrorRetryTimer) {
        feedErrorRetryTimer = setTimeout(() => {
          feedErrorRetryTimer = null;
          if (feed && feed.readyState === WebSocket.OPEN) sendSubscription();
        }, 3000);
      }
    }
  }

  function applySnapshot(msg) {
    const meta = msg.meta || MARKETS[currentSymbol] || {};
    if (meta && meta.symbol) {
      MARKETS[meta.symbol] = Object.assign({}, MARKETS[meta.symbol] || {}, meta, {
        decimals: Number(meta.digits) || 2
      });
    }
    serverOffsetSec = Number(msg.offset) || 0;
    klines = (msg.bars || []).map(b => ({
      time: Number(b[0]),
      open: Number(b[1]),
      high: Number(b[2]),
      low: Number(b[3]),
      close: Number(b[4]),
      volume: Number(b[5]) || 0
    })).filter(c => isFinite(c.time) && isFinite(c.close));
    if (!klines.length) {
      chartReady = false;
      setStatus('No candles from ' + feedLabel(),
        'Check that ' + currentSymbol + ' is available on ' + feedLabel() + '.', 'error');
      return;
    }
    detectWhaleActivities(klines);
    if (whaleTape) applyWhaleTape(whaleTape.prints, whaleTape.symbol);
    chartReady = true;
    lastTickAt = Date.now();
    setStatus('', '', '');
    if (msg.ticker) updateTickerDisplay(msg.ticker);
    const m = MARKETS[currentSymbol] || meta;
    const legendSym = $('b-legend-sym');
    if (legendSym) {
      legendSym.textContent = (m.label || currentSymbol) + ' / ' + (m.pair || 'USD') +
        ' · ' + currentInterval.toUpperCase() +
        (feedSource === 'public' ? ' · PUBLIC' : '');
    }
    renderChart();
    updateLegend(klines[klines.length - 1]);
    window.dispatchEvent(new CustomEvent('chart:snapshot', {
      detail: { symbol: currentSymbol, meta: m, ticker: msg.ticker || null }
    }));
  }

  function applyTick(msg) {
    if (!chartReady || !klines.length) return;
    const price = Number(msg.b) || 0;
    const tm = Number(msg.tm) || 0;
    if (!price || !tm) return;
    const step = (TF_SECONDS[currentInterval] || 300) * 1000;
    const bucket = Math.floor(tm / step) * step;
    const last = klines[klines.length - 1];
    const addVol = Number(msg.d) || 0;
    if (bucket > last.time) {
      klines.push({
        time: bucket,
        open: last.close,
        high: Math.max(last.close, price),
        low: Math.min(last.close, price),
        close: price,
        volume: addVol
      });
      if (klines.length > 1200) klines.shift();
    } else if (bucket === last.time) {
      last.high = Math.max(last.high, price);
      last.low = Math.min(last.low, price);
      last.close = price;
      last.volume = (Number(last.volume) || 0) + addVol;
    } else {
      return;
    }
    lastTickAt = Date.now();
    if (feedPulseStale) setFeedPulse(true);   // a fresh tick proves the stream is alive again
    const now = Date.now();
    if (now - lastWhaleScan > 2500) {
      lastWhaleScan = now;
      detectWhaleActivities(klines);
      if (whaleTape) applyWhaleTape(whaleTape.prints, whaleTape.symbol);
    }
    const open24 = Number(tickerData.open24) || 0;
    updateTickerDisplay({
      price: price,
      change: open24 ? ((price - open24) / open24) * 100 : Number(tickerData.change) || 0
    });
    scheduleChartRender();
    if (!crosshair) updateLegend(last);
  }

  // --- Update Ticker UI ---
  function updateTickerDisplay(data) {
    const elPrice = $('b-live-price');
    const elChange = $('b-live-change');
    const elHigh = $('b-24h-high');
    const elLow = $('b-24h-low');
    const elVol = $('b-24h-vol');

    const meta = MARKETS[currentSymbol] || { decimals: 2 };
    const decimals = meta.decimals;

    if (elPrice && data.price) {
      const oldPrice = tickerData.price;
      elPrice.textContent = formatNumber(data.price, decimals);
      if (oldPrice && oldPrice !== data.price) {
        // No forced reflow: these classes only change the colour (with a CSS
        // transition), and reading offsetWidth on every tick forced a style +
        // layout of the whole page.
        elPrice.classList.remove('tick-up', 'tick-down');
        elPrice.classList.add(data.price > oldPrice ? 'tick-up' : 'tick-down');
      }
    }

    if (elChange && data.change !== undefined) {
      const isPos = data.change >= 0;
      elChange.textContent = (isPos ? '+' : '') + data.change.toFixed(2) + '%';
      elChange.className = 'b-change ' + (isPos ? 'up' : 'down');
    }

    if (elHigh && data.high) {
      elHigh.textContent = formatNumber(data.high, decimals);
    }
    if (elLow && data.low) {
      elLow.textContent = formatNumber(data.low, decimals);
    }
    if (elVol && data.vol) {
      // Exness reports participation as tick volume, not a quote notional.
      elVol.textContent = formatCompact(data.vol);
    }

    tickerData = { ...tickerData, ...data };
  }

  // --- Update Legend (OHLCV & SMA) ---
  function updateLegend(candle) {
    if (!candle) return;
    const meta = MARKETS[currentSymbol] || { label: currentSymbol, decimals: 2 };
    const dec = meta.decimals;

    const elSym = $('b-legend-sym');
    const elO = $('b-ohlc-o');
    const elH = $('b-ohlc-h');
    const elL = $('b-ohlc-l');
    const elC = $('b-ohlc-c');
    const elV = $('b-ohlc-v');

    if (elSym) elSym.textContent = `${meta.label} / ${meta.pair || 'USD'} · ${currentInterval.toUpperCase()}` +
      (feedSource === 'public' ? ' · PUBLIC' : '');
    if (elO) elO.textContent = formatNumber(candle.open, dec);
    if (elH) elH.textContent = formatNumber(candle.high, dec);
    if (elL) elL.textContent = formatNumber(candle.low, dec);
    if (elC) {
      elC.textContent = formatNumber(candle.close, dec);
      elC.className = candle.close >= candle.open ? 'txt-up' : 'txt-down';
    }
    if (elV) elV.textContent = formatCompact(candle.volume);

    // Compute only the trailing SMA values for the legend (fast on hot paths)
    if (klines.length > 7) {
      const last7 = tailSMA(klines, 7);
      const last25 = tailSMA(klines, 25);
      const elSma7 = $('b-sma-7');
      const elSma25 = $('b-sma-25');
      if (elSma7 && last7) elSma7.textContent = formatNumber(last7, dec);
      if (elSma25 && last25) elSma25.textContent = formatNumber(last25, dec);
    }

    // Update Whale Flow Status in Legend
    const elWhale = $('b-whale-status');
    const elWhaleTxt = $('b-whale-stat-txt');
    if (elWhale && elWhaleTxt) {
      if (candle && candle.whale) {
        const w = candle.whale;
        const sideTxt = w.side === 'BUY' ? 'BUY (ACCUM)' : 'SELL (DUMP)';
        elWhaleTxt.textContent = `${sideTxt} · ${formatCompact(w.vol)} ${meta.unit || ''}`;
        elWhale.className = 'b-legend-ind whale-ind ' + (w.side === 'BUY' ? 'whale-buy' : 'whale-sell');
      } else {
        elWhaleTxt.textContent = 'SCANNING';
        elWhale.className = 'b-legend-ind whale-ind';
      }
    }
  }

  // --- Render Candlestick Chart on Canvas ---
  function renderChart() {
    if (!canvas || !ctx) return;
    // The canvas is sized by resizeCanvas() (ResizeObserver + window resize).
    // Measuring the box here ran on every tick and forced a full synchronous
    // style + layout of the dashboard (~70 ms) for no visual gain.
    if (!canvas.width || !canvas.height) {
      resizeCanvas();
      if (!canvas.width || !canvas.height) return;
    }

    const width = canvas.width / dpr;
    const height = canvas.height / dpr;
    if (width <= 0 || height <= 0 || klines.length === 0) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 1. Clear background
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, width, height);

    const priceWidth = PRICE_AXIS_W; // Right axis reservation
    const timeHeight = 22; // Bottom axis reservation
    const chartW = width - priceWidth;
    const chartH = height - timeHeight;
    const volH = Math.min(chartH * 0.22, 60);
    const candleH = chartH - volH;

    const candleSlot = chartW / visibleCandlesCount;
    const candleBarW = Math.max(2, Math.floor(candleSlot * 0.72));
    const rightMarginCandles = 3;
    // The news overlay reserves a future lane on the right (exactly where
    // the next candles will print) so upcoming events can be timed.
    const newsLaneW = (window.ChartNewsOverlay && typeof window.ChartNewsOverlay.laneWidth === 'function')
      ? window.ChartNewsOverlay.laneWidth(chartW) : 0;
    const rightEdgeX = chartW - (rightMarginCandles * candleSlot) - newsLaneW;

    // Helper: calculate X for candle index i (0 = oldest, klines.length - 1 = newest)
    function candleIndexToX(i) {
      return rightEdgeX - ((klines.length - 1 - i) * candleSlot) + panOffset;
    }

    // 2. Calculate Min / Max Price & Volume ONLY for candles currently in view
    let minPrice = Infinity;
    let maxPrice = -Infinity;
    let maxVol = 0;
    let visibleCount = 0;

    for (let i = 0; i < klines.length; i++) {
      const c = klines[i];
      const cx = candleIndexToX(i);
      if (cx >= -candleSlot * 2 && cx <= chartW + candleSlot * 2) {
        visibleCount++;
        if (c.low < minPrice) minPrice = c.low;
        if (c.high > maxPrice) maxPrice = c.high;
        if (c.volume > maxVol) maxVol = c.volume;
      }
    }

    if (visibleCount === 0 || minPrice === Infinity || minPrice === maxPrice) {
      for (let i = Math.max(0, klines.length - visibleCandlesCount); i < klines.length; i++) {
        const c = klines[i];
        if (c.low < minPrice) minPrice = c.low;
        if (c.high > maxPrice) maxPrice = c.high;
        if (c.volume > maxVol) maxVol = c.volume;
      }
    }

    if (minPrice === maxPrice || minPrice === Infinity) {
      minPrice = minPrice * 0.99;
      maxPrice = maxPrice * 1.01;
    }

    // Keep the predicted news targets on screen so their price marks and
    // dollar moves stay readable next to the axis.
    if (window.ChartNewsOverlay && typeof window.ChartNewsOverlay.priceBounds === 'function') {
      const nb = window.ChartNewsOverlay.priceBounds({ klines: klines, symbol: currentSymbol });
      if (nb) {
        // Extend only so far: a target $1,100 away must not flatten the
        // candles into a line - it then gets an off-scale axis badge instead.
        const baseSpan = Math.abs(maxPrice - minPrice) || 1;
        if (isFinite(nb.min) && nb.min < minPrice && (minPrice - nb.min) <= baseSpan * 1.5) minPrice = nb.min;
        if (isFinite(nb.max) && nb.max > maxPrice && (nb.max - maxPrice) <= baseSpan * 1.5) maxPrice = nb.max;
      }
    }

    // Add 12% price buffer top and bottom to ensure whale badges have clear margin
    const priceRange = maxPrice - minPrice;
    const bufTop = priceRange * 0.12;
    const bufBtm = priceRange * 0.12;
    const plotMin = minPrice - bufBtm;
    const plotMax = maxPrice + bufTop;
    const plotRange = plotMax - plotMin;

    function priceToY(p) {
      return candleH - ((p - plotMin) / plotRange) * candleH;
    }

    // 3. Draw Background Grid Lines
    ctx.strokeStyle = '#e6edf9';
    ctx.lineWidth = 1;

    // Horizontal Price Grids (4 lines) - tick prices must stay readable on the
    // white surface and must not fight the last-price badge below
    ctx.fillStyle = '#475569';
    ctx.font = '600 10px "JetBrains Mono", monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

    const gridSteps = 4;
    const lastTagY = klines.length ? priceToY(klines[klines.length - 1].close) : -999;
    for (let g = 0; g <= gridSteps; g++) {
      const y = candleH * (g / gridSteps);
      const priceAtY = plotMax - (g / gridSteps) * plotRange;

      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(chartW, y);
      ctx.stroke();

      // Right axis tick text - clamped inside the canvas (drawn at y=0 the top
      // label was clipped in half) and skipped where the price badge sits
      const labelY = Math.min(candleH - 8, Math.max(8, y));
      if (Math.abs(labelY - lastTagY) > 12) {
        ctx.fillText(formatNumber(priceAtY, MARKETS[currentSymbol]?.decimals || 2), chartW + 6, labelY);
      }
    }

    // Vertical Time Grids (Dynamic intervals based on zoom level)
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const timeStepInterval = Math.max(5, Math.floor(visibleCandlesCount / 6));
    for (let i = 0; i < klines.length; i += timeStepInterval) {
      const x = candleIndexToX(i);
      if (x >= 25 && x <= chartW - 25) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, chartH);
        ctx.stroke();

        ctx.fillText(formatTime(klines[i].time, currentInterval), x, chartH + 5);
      }
    }

    // 4. Draw Volume Histogram
    for (let i = 0; i < klines.length; i++) {
      const x = candleIndexToX(i);
      if (x < -candleSlot || x > chartW + candleSlot) continue;
      const c = klines[i];
      const vRatio = maxVol > 0 ? (c.volume / maxVol) : 0;
      const barH = vRatio * (volH - 6);
      const isUp = c.close >= c.open;
      ctx.fillStyle = isUp ? 'rgba(5, 150, 105, 0.30)' : 'rgba(220, 38, 38, 0.24)';
      ctx.fillRect(x - candleBarW / 2, chartH - barH, candleBarW, barH);
    }

    // 5. Draw SMAs
    const sma7 = computeSMA(klines, 7);
    const sma25 = computeSMA(klines, 25);

    function drawLine(arr, color) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < klines.length; i++) {
        const val = arr[i];
        if (val === null || val === undefined) continue;
        const x = candleIndexToX(i);
        if (x < -60 || x > chartW + 60) continue;
        const y = priceToY(val);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
    }

    drawLine(sma7, '#2563eb'); // Blue SMA7
    drawLine(sma25, '#7c3aed'); // Violet SMA25

    // 5.5. Draw Whale Volumetric Aura Glows
    for (let i = 0; i < klines.length; i++) {
      const c = klines[i];
      if (!c.whale) continue;
      const x = candleIndexToX(i);
      if (x < -50 || x > chartW + 50) continue;
      const isBuy = c.whale.side === 'BUY';
      const auraColor = isBuy ? 'rgba(5, 150, 105, ' : 'rgba(220, 38, 38, ';

      const yHigh = priceToY(c.high);
      const yLow = priceToY(c.low);
      const topY = Math.max(0, yHigh - 14);
      const auraH = Math.min(candleH, (yLow - yHigh) + 28);
      const auraW = Math.max(candleBarW * 2.5, 18);

      const auraGrad = ctx.createRadialGradient(x, (yHigh + yLow) / 2, 2, x, (yHigh + yLow) / 2, Math.max(auraW, auraH / 2));
      auraGrad.addColorStop(0, auraColor + '0.22)');
      auraGrad.addColorStop(0.65, auraColor + '0.06)');
      auraGrad.addColorStop(1, auraColor + '0)');

      ctx.fillStyle = auraGrad;
      ctx.fillRect(x - auraW, topY, auraW * 2, auraH);
    }

    // 6. Draw Candlesticks
    for (let i = 0; i < klines.length; i++) {
      const x = candleIndexToX(i);
      if (x < -candleSlot * 2 || x > chartW + candleSlot * 2) continue;
      const c = klines[i];
      const isUp = c.close >= c.open;
      const color = isUp ? '#059669' : '#dc2626';

      const yOpen = priceToY(c.open);
      const yClose = priceToY(c.close);
      const yHigh = priceToY(c.high);
      const yLow = priceToY(c.low);

      // Wick line
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(x, yHigh);
      ctx.lineTo(x, yLow);
      ctx.stroke();

      // Body rect
      const topY = Math.min(yOpen, yClose);
      const bodyH = Math.max(1.5, Math.abs(yClose - yOpen));
      ctx.fillStyle = color;
      ctx.fillRect(x - candleBarW / 2, topY, candleBarW, bodyH);
    }

    // 6.25. Show active open BUY/SELL notional on the live candle only.
    // These labels are sourced from the terminal's running positions, never
    // from generated chart volume.
    for (let i = 0; i < klines.length; i++) {
      const c = klines[i];
      // Open-flow totals belong only to the currently forming candle.
      // Historical candles must not retain or display running-position labels.
      if (i !== klines.length - 1) continue;
      const x = candleIndexToX(i);
      if (x < -candleSlot * 2 || x > chartW + candleSlot * 2) continue;
      const flow = candleOrderFlow(c);
      if (!(flow.buy > 0 || flow.sell > 0)) continue;

      const yHigh = priceToY(c.high);
      const yLow = priceToY(c.low);
      const labelYTop = Math.max(11, yHigh - 25);
      const labelYBottom = Math.min(candleH - 11, yLow + 25);
      ctx.save();
      const compact = candleSlot < 32;
      const drawFlowLabel = (text, y, color, direction) => {
        ctx.save();
        ctx.fillStyle = color;
        ctx.font = compact ? 'bold 7px "JetBrains Mono", monospace' : 'bold 8px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        if (compact) {
          // Narrow candles cannot hold a horizontal label. Rotate it around
          // this candle's x-center so it can never spill into its neighbours.
          ctx.translate(x, y);
          ctx.rotate(direction * Math.PI / 2);
          ctx.fillText(text, 0, 0);
        } else {
          const pad = 4;
          const width = ctx.measureText(text).width + pad * 2;
          ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
          ctx.fillRect(x - width / 2, y - 7, width, 14);
          ctx.fillStyle = color;
          ctx.fillText(text, x, y);
        }
        ctx.restore();
      };

      if (flow.buy > 0) {
        drawFlowLabel(`BUY ${currencyMark(currentSymbol)}${formatCompact(flow.buy)}`, labelYTop, '#059669', -1);
      }
      if (flow.sell > 0) {
        drawFlowLabel(`SELL ${currencyMark(currentSymbol)}${formatCompact(flow.sell)}`, labelYBottom, '#dc2626', 1);
      }
      ctx.restore();
    }

    // 6.5. Draw Whale Badges & Anchor Pointers on Candles
    for (let i = 0; i < klines.length; i++) {
      const c = klines[i];
      if (!c.whale) continue;
      const x = candleIndexToX(i);
      if (x < -50 || x > chartW + 50) continue;
      const isBuy = c.whale.side === 'BUY';
      const isLast = (i === klines.length - 1);

      const yHigh = priceToY(c.high);
      const yLow = priceToY(c.low);

      const badgeW = 48;
      const badgeH = 15;
      const badgeX = x - badgeW / 2;
      let badgeY;

      ctx.save();

      if (isBuy) {
        // Whale Buy: Badge positioned BELOW candle lower wick
        badgeY = yLow + 8;

        // Pointer line from low wick tip to badge top
        ctx.strokeStyle = '#059669';
        ctx.lineWidth = 1.2;
        ctx.setLineDash([2, 2]);
        ctx.beginPath();
        ctx.moveTo(x, yLow + 1);
        ctx.lineTo(x, badgeY);
        ctx.stroke();
        ctx.setLineDash([]);

        // Badge Container
        drawRoundRect(ctx, badgeX, badgeY, badgeW, badgeH, 3.5);
        ctx.fillStyle = '#dcfce7'; // Soft emerald tint
        ctx.fill();
        ctx.strokeStyle = '#059669';
        ctx.lineWidth = 1.2;
        ctx.shadowColor = 'rgba(5, 150, 105, 0.35)';
        ctx.shadowBlur = 7;
        ctx.stroke();

        // Badge Text
        ctx.shadowBlur = 0;
        ctx.fillStyle = '#059669';
        ctx.font = 'bold 8.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('🐋 BUY', x, badgeY + badgeH / 2);
      } else {
        // Whale Sell: Badge positioned ABOVE candle upper wick
        badgeY = yHigh - badgeH - 8;

        // Pointer line from high wick tip to badge bottom
        ctx.strokeStyle = '#dc2626';
        ctx.lineWidth = 1.2;
        ctx.setLineDash([2, 2]);
        ctx.beginPath();
        ctx.moveTo(x, yHigh - 1);
        ctx.lineTo(x, badgeY + badgeH);
        ctx.stroke();
        ctx.setLineDash([]);

        // Badge Container
        drawRoundRect(ctx, badgeX, badgeY, badgeW, badgeH, 3.5);
        ctx.fillStyle = '#fee2e2'; // Soft ruby tint
        ctx.fill();
        ctx.strokeStyle = '#dc2626';
        ctx.lineWidth = 1.2;
        ctx.shadowColor = 'rgba(220, 38, 38, 0.3)';
        ctx.shadowBlur = 7;
        ctx.stroke();

        // Badge Text
        ctx.shadowBlur = 0;
        ctx.fillStyle = '#dc2626';
        ctx.font = 'bold 8.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('🐋 SELL', x, badgeY + badgeH / 2);
      }

      // If active candle has live whale trade, draw glowing radar pulse ring
      if (isLast && c.whale.realtime) {
        const pulseY = priceToY(c.close);
        ctx.strokeStyle = isBuy ? '#059669' : '#dc2626';
        ctx.lineWidth = 1.5;
        ctx.shadowColor = isBuy ? '#059669' : '#dc2626';
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(x, pulseY, 7, 0, Math.PI * 2);
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      ctx.restore();
    }

    // 6.9. Economic-calendar events: released markers, the future lane on
    // the right and the next-event countdown on the running candle.
    if (window.ChartNewsOverlay && typeof window.ChartNewsOverlay.draw === 'function') {
      window.ChartNewsOverlay.draw({
        ctx: ctx,
        chartW: chartW,
        chartH: chartH,
        candleH: candleH,
        candleSlot: candleSlot,
        rightEdgeX: rightEdgeX,
        klines: klines,
        tfSec: TF_SECONDS[currentInterval],
        priceToY: priceToY,
        candleIndexToX: candleIndexToX,
        laneW: newsLaneW,
        priceWidth: priceWidth,
        symbol: currentSymbol,
      });
    }

    // 7. Draw Current Price Line & Badge
    if (klines.length > 0) {
      const last = klines[klines.length - 1];
      const lastY = priceToY(last.close);
      const isUp = last.close >= last.open;
      const tagColor = isUp ? '#059669' : '#dc2626';

      // Dashed line across chart
      ctx.strokeStyle = tagColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(0, lastY);
      ctx.lineTo(chartW, lastY);
      ctx.stroke();
      ctx.setLineDash([]); // reset dash

      // Price badge on right axis
      ctx.fillStyle = tagColor;
      ctx.fillRect(chartW + 2, lastY - 9, priceWidth - 4, 18);
      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 9.5px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(formatNumber(last.close, MARKETS[currentSymbol]?.decimals || 2), chartW + (priceWidth - 4) / 2 + 2, lastY);
    }

    // 7.5. Live / Panned Indicator Badge (Clickable)
    const badgeX = chartW - 80;
    const badgeY = 8;
    const badgeW = 74;
    const badgeH = 20;

    ctx.save();
    drawRoundRect(ctx, badgeX, badgeY, badgeW, badgeH, 4);
    if (panOffset > 10) {
      ctx.fillStyle = 'rgba(37, 99, 235, 0.12)';
      ctx.fill();
      ctx.strokeStyle = '#2563eb';
      ctx.lineWidth = 1.2;
      ctx.shadowColor = 'rgba(37, 99, 235, 0.3)';
      ctx.shadowBlur = 8;
      ctx.stroke();
      ctx.shadowBlur = 0;

      ctx.fillStyle = '#2563eb';
      ctx.font = 'bold 9px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('▶ TO LIVE', badgeX + badgeW / 2, badgeY + badgeH / 2);
    } else {
      ctx.fillStyle = 'rgba(5, 150, 105, 0.10)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(5, 150, 105, 0.30)';
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = '#059669';
      ctx.font = 'bold 9px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('● LIVE', badgeX + badgeW / 2, badgeY + badgeH / 2);
    }
    ctx.restore();

    // 8. Draw Crosshair Hover Indicator
    if (crosshair && crosshair.x >= 0 && crosshair.x <= chartW && crosshair.y >= 0 && crosshair.y <= chartH) {
      ctx.strokeStyle = 'rgba(15, 23, 42, 0.35)';
      ctx.lineWidth = 0.8;
      ctx.setLineDash([3, 3]);

      // Vertical line
      ctx.beginPath();
      ctx.moveTo(crosshair.x, 0);
      ctx.lineTo(crosshair.x, chartH);
      ctx.stroke();

      // Horizontal line
      ctx.beginPath();
      ctx.moveTo(0, crosshair.y);
      ctx.lineTo(chartW, crosshair.y);
      ctx.stroke();
      ctx.setLineDash([]);

      // Crosshair Price Tag on Right
      const hoveredPrice = plotMax - (crosshair.y / candleH) * plotRange;
      ctx.fillStyle = '#e2e8f0';
      ctx.fillRect(chartW + 2, crosshair.y - 8, priceWidth - 4, 16);
      ctx.fillStyle = '#0f172a';
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(formatNumber(hoveredPrice, MARKETS[currentSymbol]?.decimals || 2), chartW + (priceWidth - 4) / 2 + 2, crosshair.y);

      // Crosshair Time Tag on Bottom
      if (crosshair.candle) {
        const timeText = formatTime(crosshair.candle.time, currentInterval);
        const timeTagW = 60;
        ctx.fillStyle = '#e2e8f0';
        ctx.fillRect(crosshair.x - timeTagW / 2, chartH + 2, timeTagW, 16);
        ctx.fillStyle = '#0f172a';
        ctx.font = '9px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(timeText, crosshair.x, chartH + 10);
      }

      // 8.5. If hovered candle is a Whale Candle, show dedicated HUD card
      if (crosshair.candle && crosshair.candle.whale) {
        const hc = crosshair.candle;
        const hw = hc.whale;
        const isBuy = hw.side === 'BUY';
        const cardW = 175;
        const cardH = 76;
        let cardX = crosshair.x + 14;
        if (cardX + cardW > chartW - 10) cardX = crosshair.x - cardW - 14;
        let cardY = Math.max(10, Math.min(chartH - cardH - 10, crosshair.y - cardH / 2));

        ctx.save();
        drawRoundRect(ctx, cardX, cardY, cardW, cardH, 6);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.97)';
        ctx.fill();
        ctx.strokeStyle = isBuy ? '#059669' : '#dc2626';
        ctx.lineWidth = 1.2;
        ctx.shadowColor = isBuy ? 'rgba(5, 150, 105, 0.3)' : 'rgba(220, 38, 38, 0.3)';
        ctx.shadowBlur = 12;
        ctx.stroke();

        ctx.shadowBlur = 0;
        ctx.font = 'bold 9.5px "JetBrains Mono", monospace';
        ctx.fillStyle = isBuy ? '#059669' : '#dc2626';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(`🐋 WHALE ${isBuy ? 'INFLOW (BUY)' : 'OUTFLOW (SELL)'}`, cardX + 10, cardY + 10);

        ctx.font = '9px "JetBrains Mono", monospace';
        ctx.fillStyle = '#64748b';
        ctx.fillText(`Type: ${hw.realtime ? 'LIVE TAKER ORDER' : 'INSTITUTIONAL ACCUM'}`, cardX + 10, cardY + 28);

        const unit = MARKETS[currentSymbol]?.unit || '';
        ctx.fillText(`Whale Vol: `, cardX + 10, cardY + 44);
        ctx.fillStyle = '#0f172a';
        ctx.fillText(`${formatCompact(hw.vol)} ${unit}`, cardX + 78, cardY + 44);

        ctx.fillStyle = '#64748b';
        ctx.fillText(`Candle Vol: `, cardX + 10, cardY + 58);
        ctx.fillStyle = '#0f172a';
        ctx.fillText(`${formatCompact(hc.volume)} ${unit}`, cardX + 78, cardY + 58);

        ctx.restore();
      }
    }

    // 9. Floating Live Whale Alert Flash Banner (if active < 5s)
    if (lastWhaleAlert && Date.now() - lastWhaleAlert.time < 5000) {
      const age = Date.now() - lastWhaleAlert.time;
      const opacity = Math.max(0, 1 - age / 5000);
      const isBuy = lastWhaleAlert.side === 'BUY';
      const unit = MARKETS[lastWhaleAlert.symbol]?.unit || '';
      const bannerW = Math.min(340, chartW - 24);
      const bannerH = 26;
      const bannerX = (chartW - bannerW) / 2;
      const bannerY = 12;

      ctx.save();
      ctx.globalAlpha = opacity;
      drawRoundRect(ctx, bannerX, bannerY, bannerW, bannerH, 13);
      ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
      ctx.fill();
      ctx.strokeStyle = isBuy ? '#059669' : '#dc2626';
      ctx.lineWidth = 1.2;
      ctx.shadowColor = isBuy ? 'rgba(5, 150, 105, 0.7)' : 'rgba(220, 38, 38, 0.7)';
      ctx.shadowBlur = 12;
      ctx.stroke();

      ctx.shadowBlur = 0;
      ctx.font = 'bold 9.5px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = isBuy ? '#059669' : '#dc2626';
      const alertMsg = `⚡ LIVE WHALE ${lastWhaleAlert.side}: ${formatNumber(lastWhaleAlert.qty, 2)} ${unit} (${currencyMark(lastWhaleAlert.symbol)}${formatCompact(lastWhaleAlert.usd)})`;
      ctx.fillText(alertMsg, bannerX + bannerW / 2, bannerY + bannerH / 2);
      ctx.restore();
    }
  }

  // --- Switch Market Symbol (Exness instrument) ---
  function switchMarket(symbol, opts) {
    if (!marketsReady) {
      pendingSymbol = String(symbol || '');
      return false;
    }
    const key = resolveSymbol(symbol);
    if (!key) return false;
    if (key === currentSymbol && chartReady) return true;
    currentSymbol = key;
    const meta = MARKETS[key] || {};
    const kind = (meta.kind || '').toLowerCase();
    if (kind && currentCategory !== 'all' && currentCategory !== kind && !marketSearchQuery) {
      currentCategory = kind;
      renderSymbolTabs();
    }

    // Reset local data & show loading state (never seed fake candles)
    klines = [];
    crosshair = null;
    panOffset = 0;
    visibleCandlesCount = 55;
    chartReady = false;
    lastTickAt = 0;
    tickerData = { price: 0, open24: 0, change: 0, high: 0, low: 0, vol: 0 };
    updateActiveTabs();

    const legendSym = $('b-legend-sym');
    if (legendSym) {
      legendSym.textContent = (meta.label || key) + ' / ' + (meta.pair || 'USD') +
        ' · ' + currentInterval.toUpperCase();
    }
    setStatus('Loading ' + (meta.label || key) + ' from ' + feedLabel() + '…',
      (feedSource === 'public' ? 'live Binance + Yahoo candles' : 'real MetaTrader 5 candles') +
        ' · ' + currentInterval, 'loading');
    renderChart();

    const priceEl = $('b-live-price');
    if (priceEl) priceEl.textContent = '--';
    const changeEl = $('b-live-change');
    if (changeEl) {
      changeEl.textContent = '--%';
      changeEl.className = 'b-change';
    }
    ['b-24h-high', 'b-24h-low', 'b-24h-vol'].forEach(id => {
      const el = $(id);
      if (el) el.textContent = '--';
    });

    if (!feed || feed.readyState !== WebSocket.OPEN) connectFeed();
    else sendSubscription();
    pollNow();   // REST polling hosts reload immediately on a symbol/TF switch

    // Keep the existing Traders Order Flow Terminal in sync with the chart.
    if (window.TradersTerminal && typeof window.TradersTerminal.switchMarket === 'function') {
      try { window.TradersTerminal.switchMarket(meta.symbol || key); } catch (e) {}
    }
    window.dispatchEvent(new CustomEvent('chart:market', {
      detail: { symbol: meta.symbol || key, meta: meta }
    }));
    return true;
  }

  // --- Switch Timeframe Interval ---
  function switchInterval(interval) {
    if (!TF_SECONDS[interval] || interval === currentInterval) return;
    currentInterval = interval;
    updateActiveTabs();
    panOffset = 0;
    visibleCandlesCount = 55;
    setStatus('Loading ' + currentInterval + ' candles…', feedLabel(), 'loading');
    if (!feed || feed.readyState !== WebSocket.OPEN) connectFeed();
    else sendSubscription();
    pollNow();   // REST polling hosts reload immediately on a symbol/TF switch
    window.dispatchEvent(new CustomEvent('chart:interval', { detail: { tf: currentInterval } }));
  }

  // --- Resize Canvas to Container ---
  function resizeCanvas() {
    const box = $('binance-canvas-box');
    if (!box || !canvas) return;
    dpr = window.devicePixelRatio || 1;
    const bw = box.clientWidth || (box.getBoundingClientRect() && Math.floor(box.getBoundingClientRect().width)) || 0;
    const bh = box.clientHeight || (box.getBoundingClientRect() && Math.floor(box.getBoundingClientRect().height)) || 0;
      if (bw > 0 && bh > 0) {
        canvas.width = Math.floor(bw * dpr);
        canvas.height = Math.floor(bh * dpr);
        canvas.style.width = bw + 'px';
        canvas.style.height = bh + 'px';
        scheduleChartRender();
      }
  }

  // --- Interactive Drag-to-Pan, Mouse Wheel Zoom & Touch Navigation ---
  function setupChartInteractions() {
    if (!canvas) return;

    // Helper: calculate max pan limit
    function getMaxPan() {
      const chartW = (canvas.width / dpr) - PRICE_AXIS_W;
      const candleSlot = chartW / visibleCandlesCount;
      return Math.max(0, (klines.length - 8) * candleSlot);
    }

    // Helper: find candle index at X coordinate
    function getCandleIndexAtX(x) {
      if (klines.length === 0) return -1;
      const chartW = (canvas.width / dpr) - PRICE_AXIS_W;
      const candleSlot = chartW / visibleCandlesCount;
      const rightEdgeX = chartW - (3 * candleSlot);
      const diffFromRight = rightEdgeX + panOffset - x;
      const offsetIndex = Math.round(diffFromRight / candleSlot);
      const idx = klines.length - 1 - offsetIndex;
      return Math.max(0, Math.min(klines.length - 1, idx));
    }

    // --- Mouse Drag to Pan ---
    canvas.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return; // Left click only
      e.preventDefault();
      isDragging = true;
      hasMovedDuringDrag = false;
      dragStartX = e.clientX;
      dragStartPan = panOffset;
      canvas.style.cursor = 'grabbing';
    });

    window.addEventListener('mousemove', function (e) {
      if (!canvas) return;

      if (isDragging) {
        e.preventDefault();
        const dx = e.clientX - dragStartX;
        if (Math.abs(dx) > 3) hasMovedDuringDrag = true;
        panOffset = Math.max(0, Math.min(getMaxPan(), dragStartPan + dx));
        scheduleChartRender();
        return;
      }

      // Normal crosshair tracking
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const chartW = (canvas.width / dpr) - PRICE_AXIS_W;
      const chartH = (canvas.height / dpr) - 22;

      if (x >= 0 && x <= chartW && y >= 0 && y <= chartH && klines.length > 0) {
        const idx = getCandleIndexAtX(x);
        if (idx >= 0 && idx < klines.length) {
          const candle = klines[idx];
          crosshair = { x, y, candle };
          updateLegend(candle);
        } else {
          crosshair = null;
        }
      } else {
        if (crosshair) {
          crosshair = null;
          if (klines.length > 0) updateLegend(klines[klines.length - 1]);
        }
      }
      scheduleChartRender();
    });

    window.addEventListener('mouseup', function () {
      if (isDragging) {
        isDragging = false;
        if (canvas) canvas.style.cursor = 'crosshair';
      }
    });

    canvas.addEventListener('mouseleave', function () {
      if (!isDragging) {
        crosshair = null;
        if (klines.length > 0) updateLegend(klines[klines.length - 1]);
        scheduleChartRender();
      }
    });

    // --- Mouse Wheel Zoom ---
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      const zoomStep = e.deltaY < 0 ? -4 : 4;
      const prevCount = visibleCandlesCount;
      visibleCandlesCount = Math.max(16, Math.min(220, visibleCandlesCount + zoomStep));

      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const chartW = (canvas.width / dpr) - PRICE_AXIS_W;

      if (visibleCandlesCount !== prevCount && chartW > 0) {
        const prevSlot = chartW / prevCount;
        const newSlot = chartW / visibleCandlesCount;
        const ratio = Math.max(0, Math.min(1, mouseX / chartW));
        panOffset = Math.max(0, Math.min(getMaxPan(), panOffset + (chartW * (1 - ratio)) * (newSlot / prevSlot - 1)));
      }

      scheduleChartRender();
    }, { passive: false });

    // --- Click: Handle "▶ TO LIVE" Button ---
    canvas.addEventListener('click', function (e) {
      if (hasMovedDuringDrag) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const chartW = (canvas.width / dpr) - PRICE_AXIS_W;
      const badgeX = chartW - 80;
      const badgeY = 8;
      if (x >= badgeX && x <= badgeX + 74 && y >= badgeY && y <= badgeY + 20) {
        panOffset = 0;
        scheduleChartRender();
      }
    });

    // --- Double Click: Reset View ---
    canvas.addEventListener('dblclick', function () {
      panOffset = 0;
      visibleCandlesCount = 55;
      scheduleChartRender();
    });

    // --- Touch Screen Drag & Pinch Zoom ---
    let touchStartX = 0;
    let touchStartPan = 0;
    let touchInitialPinch = 0;
    let touchInitialCount = 55;

    canvas.addEventListener('touchstart', function (e) {
      e.preventDefault();
      if (e.touches.length === 1) {
        isDragging = true;
        hasMovedDuringDrag = false;
        touchStartX = e.touches[0].clientX;
        touchStartPan = panOffset;
      } else if (e.touches.length === 2) {
        isDragging = false;
        touchInitialPinch = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        touchInitialCount = visibleCandlesCount;
      }
    }, { passive: false });

    canvas.addEventListener('touchmove', function (e) {
      e.preventDefault();
      const chartW = (canvas.width / dpr) - PRICE_AXIS_W;
      const candleSlot = chartW / visibleCandlesCount;
      const maxPanOffset = Math.max(0, (klines.length - 8) * candleSlot);

      if (e.touches.length === 1 && isDragging) {
        const dx = e.touches[0].clientX - touchStartX;
        if (Math.abs(dx) > 3) hasMovedDuringDrag = true;
        panOffset = Math.max(0, Math.min(maxPanOffset, touchStartPan + dx));
        scheduleChartRender();
      } else if (e.touches.length === 2 && touchInitialPinch > 0) {
        const currentPinch = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        const scale = touchInitialPinch / currentPinch;
        visibleCandlesCount = Math.max(16, Math.min(220, Math.round(touchInitialCount * scale)));
        scheduleChartRender();
      }
    }, { passive: false });

    canvas.addEventListener('touchend', function () {
      isDragging = false;
      touchInitialPinch = 0;
    }, { passive: true });
  }

  // --- Initialize SoftradeX Terminal ---
  function initSoftradeXTerminal() {
    canvas = $('binance-candlestick-canvas');
    if (!canvas) return;
    ctx = canvas.getContext('2d');

    resizeCanvas();
    setTimeout(resizeCanvas, 60);
    setTimeout(resizeCanvas, 250);
    setTimeout(resizeCanvas, 800);
    window.addEventListener('resize', resizeCanvas);
    if (window.ResizeObserver) {
      const box = $('binance-canvas-box');
      if (box) {
        new ResizeObserver(() => resizeCanvas()).observe(box);
      }
    }
    setupChartInteractions();

    // Symbol Tabs Listener
    const symTabs = $('binance-symbol-tabs');
    if (symTabs) {
      symTabs.addEventListener('click', e => {
        const btn = e.target.closest('.b-tab-btn');
        if (!btn || !btn.dataset.symbol) return;
        switchMarket(btn.dataset.symbol);
      });
    }

    // Market Category Tabs Listener
    const catTabs = $('exness-cat-tabs');
    if (catTabs) {
      catTabs.addEventListener('click', e => {
        const btn = e.target.closest('.exness-cat-btn');
        if (!btn || !btn.dataset.cat) return;
        currentCategory = btn.dataset.cat;
        document.querySelectorAll('#exness-cat-tabs .exness-cat-btn').forEach(b => {
          b.classList.toggle('active', b.dataset.cat === currentCategory);
        });
        renderSymbolTabs();
      });
    }

    // Market Search Input Listener
    const searchInp = $('exness-market-search');
    if (searchInp) {
      searchInp.addEventListener('input', e => {
        marketSearchQuery = e.target.value;
        renderSymbolTabs();
      });
    }

    // Timeframe Tabs Listener
    const tfTabs = $('binance-tf-tabs');
    if (tfTabs) {
      tfTabs.addEventListener('click', e => {
        const btn = e.target.closest('.b-tf-btn');
        if (!btn || !btn.dataset.interval) return;
        switchInterval(btn.dataset.interval);
      });
    }

    // Terminal Collapse / Expand Toggle
    const toggleBtn = $('binance-term-toggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        const section = $('binance-terminal-section');
        if (!section) return;
        const isCollapsed = section.classList.toggle('collapsed');
        const icon = $('b-toggle-icon');
        const text = $('b-toggle-text');
        if (icon) icon.textContent = isCollapsed ? '+' : '−';
        if (text) text.textContent = isCollapsed ? 'Expand' : 'Collapse';
        if (!isCollapsed) {
          setTimeout(resizeCanvas, 50);
        }
      });
    }

    // Initial load: live Exness catalog + the tick socket
    loadMarkets();
    connectFeed();

    // Stale-feed watch: if no fresh tick arrives for 45 s (weekend market
    // close, terminal closed), the pulse goes amber instead of pretending.
    // The next real tick flips it straight back to live green.
    setInterval(() => {
      if (!chartReady || !lastTickAt) return;
      if (!feed || feed.readyState !== WebSocket.OPEN) return;
      if (Date.now() - lastTickAt > 45000) setFeedPulse('stale');
    }, 5000);

    // Stream watchdog: frames (ticks or the 1 s server heartbeat) must keep
    // arriving. A half-dead socket (Windows sleep, dropped NAT entry) never
    // fires onclose, so it is closed by force and the reconnect path runs.
    setInterval(() => {
      if (!feed || feed.readyState !== WebSocket.OPEN) return;
      if (lastFrameAt && Date.now() - lastFrameAt > 12000) {
        try { feed.close(); } catch (e) {}
      }
    }, 4000);

    // A page restored from bfcache / a slept machine gets an immediate revive.
    function reviveStream() {
      if (!feed || feed.readyState === WebSocket.CLOSED || feed.readyState === WebSocket.CLOSING) {
        connectFeed();
        return;
      }
      if (feed.readyState === WebSocket.OPEN && lastFrameAt &&
          Date.now() - lastFrameAt > 12000) {
        try { feed.close(); } catch (e) {}
      }
    }
    window.addEventListener('pageshow', reviveStream);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) reviveStream();
    });
  }

  // Global API export for dashboard cross-functional integration
  window.SoftradeXTerminal = window.BinanceTerminal = {
    switchMarket,
    switchInterval,
    connectFeed,
    resolveSymbol,
    hasMarket: (sym) => !!resolveSymbol(sym),
    getCurrentSymbol: () => currentSymbol,
    getInterval: () => currentInterval,
    getMarkets: () => Object.keys(MARKETS),
    getLastTickAt: () => lastTickAt,
    refresh: renderChart,
    // Candles currently carrying a marker from the real whale tape - used by
    // support to confirm the tape reached the chart.
    whaleMarks: () => klines.filter(c => c.whale && c.whale.tape).length,
    MARKETS
  };

  // Auto-init when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSoftradeXTerminal);
  } else {
    setTimeout(initSoftradeXTerminal, 100);
  }

})();
