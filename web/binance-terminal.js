/**
 * SoftradeX Pro Trading Terminal Engine
 * Pure native client connected directly to official public market REST & WebSocket stream.
 * Supports: Gold (PAXGUSDT), BTC (BTCUSDT), ETH (ETHUSDT)
 * Features:
 * - Real-time Candlestick & Volume Canvas Chart with High-DPI support
 * - SMA(7) and SMA(25) Technical Overlays
 * - Live SoftradeX WebSocket Stream (0ms tick-by-tick real-time updates)
 * - 24h Ticker Stats Ribbon (Price, Change, High, Low, Volume)
 * - Live SoftradeX Order Book (Depth with Bids & Asks)
 * - Live Recent Market Trades Stream
 * - Timeframe switching (1m, 5m, 15m, 1h, 4h, 1D)
 * - 100% Native, 0% TradingView dependency
 */
(function () {
  'use strict';

  // --- Configuration & State ---
  const MARKETS = {
    'PAXGUSDT': { label: 'GOLD / USDT', icon: '🟡', unit: 'oz', decimals: 2 },
    'BTCUSDT': { label: 'BTC / USDT', icon: '🟠', unit: 'BTC', decimals: 2 },
    'ETHUSDT': { label: 'ETH / USDT', icon: '🔷', unit: 'ETH', decimals: 2 }
  };

  let currentSymbol = 'PAXGUSDT'; // Default Gold as requested
  let currentInterval = '5m';
  let klines = []; // Array of { time, open, high, low, close, volume, whale?: { side, vol, usd, realtime, count } }
  let tickerData = { price: 0, change: 0, high: 0, low: 0, vol: 0, prevPrice: 0 };
  let orderBook = { asks: [], bids: [] };
  let recentTrades = [];
  let activeSideTab = 'depth'; // 'depth' or 'trades'
  let ws = null;
  let wsReconnectTimer = null;
  let canvas = null;
  let ctx = null;
  let crosshair = null; // { x, y, candle }
  let dpr = window.devicePixelRatio || 1;

  // Viewport & Interactive Pan / Zoom Navigation State
  let visibleCandlesCount = 55; // Zoom level: how many candles fit in viewport (15 to 220)
  let panOffset = 0;            // Pixels panned back into history (0 = live, > 0 = history)
  let isDragging = false;
  let dragStartX = 0;
  let dragStartPan = 0;
  let hasMovedDuringDrag = false;

  // Whale Order Tracking State & Thresholds
  const WHALE_THRESHOLDS = {
    'BTCUSDT': { minQty: 0.8, minUsd: 60000 },
    'ETHUSDT': { minQty: 12.0, minUsd: 30000 },
    'PAXGUSDT': { minQty: 8.0, minUsd: 25000 }
  };
  let lastWhaleAlert = null; // { symbol, side, qty, price, usd, time }

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
      // Keep real-time WebSocket verified whale data intact
      if (c.whale && c.whale.realtime) continue;

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

  function formatTime(timestamp, interval) {
    const d = new Date(timestamp);
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

  // --- Initial Fallback Seed Data (Ensures no blank chart on network latency) ---
  function getSeedKlines(symbol) {
    const base = symbol === 'BTCUSDT' ? 81500 : (symbol === 'ETHUSDT' ? 2450 : 4165);
    const count = 160;
    const now = Date.now();
    const step = 5 * 60 * 1000;
    const result = [];
    let cur = base;
    for (let i = count; i >= 0; i--) {
      const open = cur;
      const delta = (Math.random() - 0.49) * (base * 0.0035);
      const close = cur + delta;
      const high = Math.max(open, close) + Math.random() * (base * 0.002);
      const low = Math.min(open, close) - Math.random() * (base * 0.002);
      const volume = (Math.random() * 80 + 20) * (base < 5000 ? 5 : 0.8);
      result.push({
        time: now - (i * step),
        open: Number(open.toFixed(2)),
        high: Number(high.toFixed(2)),
        low: Number(low.toFixed(2)),
        close: Number(close.toFixed(2)),
        volume: Number(volume.toFixed(2))
      });
      cur = close;
    }
    return result;
  }

  // --- Fetch Historical Klines from Binance REST API ---
  async function fetchBinanceKlines(symbol, interval) {
    const url = `https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=500`;
    try {
      const resp = await fetch(url, { cache: 'no-cache' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (!Array.isArray(data) || data.length === 0) throw new Error('Empty klines');
      klines = data.map(item => ({
        time: item[0],
        open: parseFloat(item[1]),
        high: parseFloat(item[2]),
        low: parseFloat(item[3]),
        close: parseFloat(item[4]),
        volume: parseFloat(item[5])
      }));
      detectWhaleActivities(klines);
    } catch (err) {
      console.warn('[SoftradeX Terminal] Failed to fetch REST klines, using seed:', err);
      if (!klines || klines.length === 0) {
        klines = getSeedKlines(symbol);
        detectWhaleActivities(klines);
      }
    }
    renderChart();
    updateLegend(klines[klines.length - 1]);
  }

  // --- Fetch 24hr Ticker from Binance REST API ---
  async function fetchBinanceTicker(symbol) {
    const url = `https://api.binance.com/api/v3/ticker/24hr?symbol=${symbol}`;
    try {
      const resp = await fetch(url, { cache: 'no-cache' });
      if (!resp.ok) return;
      const data = await resp.json();
      updateTickerDisplay({
        price: parseFloat(data.lastPrice),
        change: parseFloat(data.priceChangePercent),
        high: parseFloat(data.highPrice),
        low: parseFloat(data.lowPrice),
        vol: parseFloat(data.quoteVolume)
      });
    } catch (e) {
      // ignore
    }
  }

  // --- Fetch Initial Depth from Binance REST API ---
  async function fetchBinanceDepth(symbol) {
    const url = `https://api.binance.com/api/v3/depth?symbol=${symbol}&limit=7`;
    try {
      const resp = await fetch(url, { cache: 'no-cache' });
      if (!resp.ok) return;
      const data = await resp.json();
      orderBook.bids = (data.bids || []).map(b => [parseFloat(b[0]), parseFloat(b[1])]);
      orderBook.asks = (data.asks || []).map(a => [parseFloat(a[0]), parseFloat(a[1])]);
      renderOrderBook();
    } catch (e) {
      // ignore
    }
  }

  // --- High-Speed Binance Stream Endpoints (Fallback resilience) ---
  const BINANCE_WS_ENDPOINTS = [
    'wss://data-stream.binance.vision/stream',
    'wss://stream.binance.com:443/stream',
    'wss://stream.binance.com/stream',
    'wss://stream.binance.com:9443/stream'
  ];
  let wsEndpointIdx = 0;
  let chartRafPending = false;
  let bookRafPending = false;
  let tradesRafPending = false;

  function scheduleChartRender() {
    if (chartRafPending) return;
    chartRafPending = true;
    requestAnimationFrame(() => {
      chartRafPending = false;
      renderChart();
    });
  }

  function scheduleOrderBookRender() {
    if (bookRafPending) return;
    bookRafPending = true;
    requestAnimationFrame(() => {
      bookRafPending = false;
      renderOrderBook();
    });
  }

  function scheduleTradesRender() {
    if (tradesRafPending) return;
    tradesRafPending = true;
    requestAnimationFrame(() => {
      tradesRafPending = false;
      if (activeSideTab === 'trades') renderRecentTrades();
    });
  }

  // --- Connect Live Binance WebSocket Stream ---
  function connectWebSocket(symbol, interval) {
    if (ws) {
      try {
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      } catch (e) {}
      ws = null;
    }
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }

    const symLower = symbol.toLowerCase();
    const endpoint = BINANCE_WS_ENDPOINTS[wsEndpointIdx % BINANCE_WS_ENDPOINTS.length];
    // Use @aggTrade instead of @trade to cut 80% message flood while preserving all price movements
    const streamUrl = `${endpoint}?streams=${symLower}@kline_${interval}/${symLower}@ticker/${symLower}@depth10@500ms/${symLower}@aggTrade`;

    try {
      ws = new WebSocket(streamUrl);

      ws.onopen = function () {
        const pulse = $('b-term-pulse');
        if (pulse) {
          pulse.classList.add('online');
          pulse.title = `Connected to SoftradeX Live Stream (${endpoint.includes('vision') ? 'High-Speed Stream' : 'Direct'})`;
        }
      };

      ws.onmessage = function (event) {
        try {
          const msg = JSON.parse(event.data);
          if (!msg || !msg.data) return;
          const data = msg.data;
          const stream = msg.stream || '';

          if (stream.includes('@kline')) {
            handleKlineUpdate(data.k);
          } else if (stream.includes('@ticker')) {
            handleTickerUpdate(data);
          } else if (stream.includes('@depth')) {
            handleDepthUpdate(data);
          } else if (stream.includes('@aggTrade') || stream.includes('@trade')) {
            handleTradeUpdate(data);
          }
        } catch (e) {
          console.error('[SoftradeX Terminal] WS msg parse err:', e);
        }
      };

      ws.onerror = function (e) {
        console.warn('[SoftradeX Terminal] WS error, trying alternate endpoint...', e);
        wsEndpointIdx = (wsEndpointIdx + 1) % BINANCE_WS_ENDPOINTS.length;
      };

      ws.onclose = function () {
        const pulse = $('b-term-pulse');
        if (pulse) pulse.classList.remove('online');
        // Fast auto-reconnect after 1s
        wsReconnectTimer = setTimeout(() => {
          connectWebSocket(currentSymbol, currentInterval);
        }, 1200);
      };
    } catch (err) {
      console.error('[SoftradeX Terminal] Failed to start WS:', err);
      wsEndpointIdx = (wsEndpointIdx + 1) % BINANCE_WS_ENDPOINTS.length;
    }
  }

  // --- Real-time Handlers (Batched via requestAnimationFrame) ---
  function handleKlineUpdate(k) {
    if (!k) return;
    const candleTime = k.t;
    const candle = {
      time: candleTime,
      open: parseFloat(k.o),
      high: parseFloat(k.h),
      low: parseFloat(k.l),
      close: parseFloat(k.c),
      volume: parseFloat(k.v)
    };

    if (klines.length > 0) {
      const last = klines[klines.length - 1];
      if (last.time === candleTime) {
        if (last.whale) candle.whale = last.whale;
        klines[klines.length - 1] = candle;
      } else if (candleTime > last.time) {
        klines.push(candle);
        if (klines.length > 1000) klines.shift();
      }
    } else {
      klines.push(candle);
    }

    detectWhaleActivities(klines);
    scheduleChartRender();
    if (!crosshair) {
      updateLegend(candle);
    }
  }

  function handleTickerUpdate(t) {
    if (!t) return;
    updateTickerDisplay({
      price: parseFloat(t.c),
      change: parseFloat(t.P),
      high: parseFloat(t.h),
      low: parseFloat(t.l),
      vol: parseFloat(t.q)
    });
  }

  function handleDepthUpdate(d) {
    if (!d) return;
    if (d.bids) {
      orderBook.bids = d.bids.slice(0, 6).map(b => [parseFloat(b[0]), parseFloat(b[1])]);
    }
    if (d.asks) {
      orderBook.asks = d.asks.slice(0, 6).map(a => [parseFloat(a[0]), parseFloat(a[1])]);
    }
    scheduleOrderBookRender();
  }

  function handleTradeUpdate(tr) {
    if (!tr) return;
    const price = parseFloat(tr.p);
    const qty = parseFloat(tr.q);
    const usd = price * qty;
    const isBuyerMaker = tr.m; // true = taker sold (market SELL), false = taker bought (market BUY)
    const side = isBuyerMaker ? 'SELL' : 'BUY';

    const trade = {
      price: price,
      qty: qty,
      time: tr.T,
      isBuyerMaker: isBuyerMaker
    };
    recentTrades.unshift(trade);
    if (recentTrades.length > 14) recentTrades.pop();
    if (activeSideTab === 'trades') {
      scheduleTradesRender();
    }

    // --- Real-Time Institutional Whale Flow Tracking ---
    const th = WHALE_THRESHOLDS[currentSymbol] || { minQty: 1.0, minUsd: 30000 };
    if (qty >= th.minQty || usd >= th.minUsd) {
      if (klines.length > 0) {
        const lastCandle = klines[klines.length - 1];
        if (!lastCandle.whale) {
          lastCandle.whale = {
            side: side,
            vol: qty,
            usd: usd,
            count: 1,
            realtime: true,
            lastSeen: Date.now()
          };
        } else {
          lastCandle.whale.vol = (lastCandle.whale.vol || 0) + qty;
          lastCandle.whale.usd = (lastCandle.whale.usd || 0) + usd;
          lastCandle.whale.count = (lastCandle.whale.count || 0) + 1;
          lastCandle.whale.side = side;
          lastCandle.whale.realtime = true;
          lastCandle.whale.lastSeen = Date.now();
        }
      }

      lastWhaleAlert = {
        symbol: currentSymbol,
        side: side,
        qty: qty,
        price: price,
        usd: usd,
        time: Date.now()
      };

      scheduleChartRender();
      // Schedule fade-out re-renders for live banner
      for (let s = 1; s <= 5; s++) {
        setTimeout(scheduleChartRender, s * 1000);
      }
    }
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
        elPrice.classList.remove('tick-up', 'tick-down');
        void elPrice.offsetWidth; // trigger reflow
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
      elVol.textContent = '$' + formatCompact(data.vol);
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

    if (elSym) elSym.textContent = `${meta.label} · ${currentInterval.toUpperCase()}`;
    if (elO) elO.textContent = formatNumber(candle.open, dec);
    if (elH) elH.textContent = formatNumber(candle.high, dec);
    if (elL) elL.textContent = formatNumber(candle.low, dec);
    if (elC) {
      elC.textContent = formatNumber(candle.close, dec);
      elC.className = candle.close >= candle.open ? 'txt-up' : 'txt-down';
    }
    if (elV) elV.textContent = formatCompact(candle.volume);

    // Compute SMAs for visible data
    if (klines.length > 7) {
      const sma7 = computeSMA(klines, 7);
      const sma25 = computeSMA(klines, 25);
      const last7 = sma7[sma7.length - 1];
      const last25 = sma25[sma25.length - 1];
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
    const box = $('binance-canvas-box');
    if (box) {
      const bw = box.clientWidth || (box.getBoundingClientRect() && Math.floor(box.getBoundingClientRect().width)) || 0;
      const bh = box.clientHeight || (box.getBoundingClientRect() && Math.floor(box.getBoundingClientRect().height)) || 0;
      if (bw > 0 && bh > 0) {
        dpr = window.devicePixelRatio || 1;
        const targetW = Math.floor(bw * dpr);
        const targetH = Math.floor(bh * dpr);
        if (canvas.width !== targetW || canvas.height !== targetH) {
          canvas.width = targetW;
          canvas.height = targetH;
          canvas.style.width = bw + 'px';
          canvas.style.height = bh + 'px';
        }
      }
    }

    const width = canvas.width / dpr;
    const height = canvas.height / dpr;
    if (width <= 0 || height <= 0 || klines.length === 0) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 1. Clear background
    ctx.fillStyle = '#0b0e14';
    ctx.fillRect(0, 0, width, height);

    const priceWidth = 64; // Right axis reservation
    const timeHeight = 22; // Bottom axis reservation
    const chartW = width - priceWidth;
    const chartH = height - timeHeight;
    const volH = Math.min(chartH * 0.22, 60);
    const candleH = chartH - volH;

    const candleSlot = chartW / visibleCandlesCount;
    const candleBarW = Math.max(2, Math.floor(candleSlot * 0.72));
    const rightMarginCandles = 3;
    const rightEdgeX = chartW - (rightMarginCandles * candleSlot);

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
    ctx.strokeStyle = '#181e2b';
    ctx.lineWidth = 1;

    // Horizontal Price Grids (4 lines)
    ctx.fillStyle = '#64748b';
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

    const gridSteps = 4;
    for (let g = 0; g <= gridSteps; g++) {
      const y = candleH * (g / gridSteps);
      const priceAtY = plotMax - (g / gridSteps) * plotRange;

      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(chartW, y);
      ctx.stroke();

      // Right axis tick text
      ctx.fillText(formatNumber(priceAtY, MARKETS[currentSymbol]?.decimals || 2), chartW + 6, y);
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
      ctx.fillStyle = isUp ? 'rgba(14, 203, 129, 0.35)' : 'rgba(246, 70, 93, 0.35)';
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

    drawLine(sma7, '#00f0ff'); // Cyan SMA7
    drawLine(sma25, '#A855F7'); // Purple SMA25

    // 5.5. Draw Whale Volumetric Aura Glows
    for (let i = 0; i < klines.length; i++) {
      const c = klines[i];
      if (!c.whale) continue;
      const x = candleIndexToX(i);
      if (x < -50 || x > chartW + 50) continue;
      const isBuy = c.whale.side === 'BUY';
      const auraColor = isBuy ? 'rgba(14, 203, 129, ' : 'rgba(246, 70, 93, ';

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
      const color = isUp ? '#0ECB81' : '#F6465D';

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
        ctx.strokeStyle = '#0ECB81';
        ctx.lineWidth = 1.2;
        ctx.setLineDash([2, 2]);
        ctx.beginPath();
        ctx.moveTo(x, yLow + 1);
        ctx.lineTo(x, badgeY);
        ctx.stroke();
        ctx.setLineDash([]);

        // Badge Container
        drawRoundRect(ctx, badgeX, badgeY, badgeW, badgeH, 3.5);
        ctx.fillStyle = '#06281b'; // Deep emerald dark
        ctx.fill();
        ctx.strokeStyle = '#0ECB81';
        ctx.lineWidth = 1.2;
        ctx.shadowColor = 'rgba(14, 203, 129, 0.7)';
        ctx.shadowBlur = 7;
        ctx.stroke();

        // Badge Text
        ctx.shadowBlur = 0;
        ctx.fillStyle = '#0ECB81';
        ctx.font = 'bold 8.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('🐋 BUY', x, badgeY + badgeH / 2);
      } else {
        // Whale Sell: Badge positioned ABOVE candle upper wick
        badgeY = yHigh - badgeH - 8;

        // Pointer line from high wick tip to badge bottom
        ctx.strokeStyle = '#F6465D';
        ctx.lineWidth = 1.2;
        ctx.setLineDash([2, 2]);
        ctx.beginPath();
        ctx.moveTo(x, yHigh - 1);
        ctx.lineTo(x, badgeY + badgeH);
        ctx.stroke();
        ctx.setLineDash([]);

        // Badge Container
        drawRoundRect(ctx, badgeX, badgeY, badgeW, badgeH, 3.5);
        ctx.fillStyle = '#2c0c12'; // Deep ruby dark
        ctx.fill();
        ctx.strokeStyle = '#F6465D';
        ctx.lineWidth = 1.2;
        ctx.shadowColor = 'rgba(246, 70, 93, 0.7)';
        ctx.shadowBlur = 7;
        ctx.stroke();

        // Badge Text
        ctx.shadowBlur = 0;
        ctx.fillStyle = '#F6465D';
        ctx.font = 'bold 8.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('🐋 SELL', x, badgeY + badgeH / 2);
      }

      // If active candle has live whale trade, draw glowing radar pulse ring
      if (isLast && c.whale.realtime) {
        const pulseY = priceToY(c.close);
        ctx.strokeStyle = isBuy ? '#0ECB81' : '#F6465D';
        ctx.lineWidth = 1.5;
        ctx.shadowColor = isBuy ? '#0ECB81' : '#F6465D';
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(x, pulseY, 7, 0, Math.PI * 2);
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      ctx.restore();
    }

    // 7. Draw Current Price Line & Badge
    if (klines.length > 0) {
      const last = klines[klines.length - 1];
      const lastY = priceToY(last.close);
      const isUp = last.close >= last.open;
      const tagColor = isUp ? '#0ECB81' : '#F6465D';

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
      ctx.fillStyle = 'rgba(0, 240, 255, 0.18)';
      ctx.fill();
      ctx.strokeStyle = '#00f0ff';
      ctx.lineWidth = 1.2;
      ctx.shadowColor = 'rgba(0, 240, 255, 0.4)';
      ctx.shadowBlur = 8;
      ctx.stroke();
      ctx.shadowBlur = 0;

      ctx.fillStyle = '#00f0ff';
      ctx.font = 'bold 9px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('▶ TO LIVE', badgeX + badgeW / 2, badgeY + badgeH / 2);
    } else {
      ctx.fillStyle = 'rgba(14, 203, 129, 0.12)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(14, 203, 129, 0.35)';
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = '#0ECB81';
      ctx.font = 'bold 9px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('● LIVE', badgeX + badgeW / 2, badgeY + badgeH / 2);
    }
    ctx.restore();

    // 8. Draw Crosshair Hover Indicator
    if (crosshair && crosshair.x >= 0 && crosshair.x <= chartW && crosshair.y >= 0 && crosshair.y <= chartH) {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
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
      ctx.fillStyle = '#334155';
      ctx.fillRect(chartW + 2, crosshair.y - 8, priceWidth - 4, 16);
      ctx.fillStyle = '#f8fafc';
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(formatNumber(hoveredPrice, MARKETS[currentSymbol]?.decimals || 2), chartW + (priceWidth - 4) / 2 + 2, crosshair.y);

      // Crosshair Time Tag on Bottom
      if (crosshair.candle) {
        const timeText = formatTime(crosshair.candle.time, currentInterval);
        const timeTagW = 60;
        ctx.fillStyle = '#334155';
        ctx.fillRect(crosshair.x - timeTagW / 2, chartH + 2, timeTagW, 16);
        ctx.fillStyle = '#f8fafc';
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
        ctx.fillStyle = 'rgba(11, 15, 23, 0.95)';
        ctx.fill();
        ctx.strokeStyle = isBuy ? '#0ECB81' : '#F6465D';
        ctx.lineWidth = 1.2;
        ctx.shadowColor = isBuy ? 'rgba(14, 203, 129, 0.5)' : 'rgba(246, 70, 93, 0.5)';
        ctx.shadowBlur = 12;
        ctx.stroke();

        ctx.shadowBlur = 0;
        ctx.font = 'bold 9.5px "JetBrains Mono", monospace';
        ctx.fillStyle = isBuy ? '#0ECB81' : '#F6465D';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(`🐋 WHALE ${isBuy ? 'INFLOW (BUY)' : 'OUTFLOW (SELL)'}`, cardX + 10, cardY + 10);

        ctx.font = '9px "JetBrains Mono", monospace';
        ctx.fillStyle = '#94a3b8';
        ctx.fillText(`Type: ${hw.realtime ? 'LIVE TAKER ORDER' : 'INSTITUTIONAL ACCUM'}`, cardX + 10, cardY + 28);

        const unit = MARKETS[currentSymbol]?.unit || '';
        ctx.fillText(`Whale Vol: `, cardX + 10, cardY + 44);
        ctx.fillStyle = '#f8fafc';
        ctx.fillText(`${formatCompact(hw.vol)} ${unit}`, cardX + 78, cardY + 44);

        ctx.fillStyle = '#94a3b8';
        ctx.fillText(`Candle Vol: `, cardX + 10, cardY + 58);
        ctx.fillStyle = '#f8fafc';
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
      ctx.fillStyle = 'rgba(15, 23, 42, 0.94)';
      ctx.fill();
      ctx.strokeStyle = isBuy ? '#0ECB81' : '#F6465D';
      ctx.lineWidth = 1.2;
      ctx.shadowColor = isBuy ? 'rgba(14, 203, 129, 0.7)' : 'rgba(246, 70, 93, 0.7)';
      ctx.shadowBlur = 12;
      ctx.stroke();

      ctx.shadowBlur = 0;
      ctx.font = 'bold 9.5px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = isBuy ? '#0ECB81' : '#F6465D';
      const alertMsg = `⚡ LIVE WHALE ${lastWhaleAlert.side}: ${formatNumber(lastWhaleAlert.qty, 2)} ${unit} ($${formatCompact(lastWhaleAlert.usd)})`;
      ctx.fillText(alertMsg, bannerX + bannerW / 2, bannerY + bannerH / 2);
      ctx.restore();
    }
  }

  // --- Render Order Book (Depth) ---
  function renderOrderBook() {
    const asksEl = $('b-depth-asks');
    const bidsEl = $('b-depth-bids');
    const midPriceEl = $('b-depth-mid-price');
    const spreadEl = $('b-depth-spread');

    const meta = MARKETS[currentSymbol] || { decimals: 2 };
    const dec = meta.decimals;

    if (!asksEl || !bidsEl) return;

    // Asks (Sells - Red) - displayed top to bottom (highest to lowest ask)
    const asks = orderBook.asks.slice(0, 6).reverse();
    let maxAskVol = Math.max(...asks.map(a => a[1] * a[0]), 1);

    asksEl.innerHTML = asks.map(a => {
      const price = a[0];
      const size = a[1];
      const total = price * size;
      const depthPct = Math.min(100, (total / maxAskVol) * 100).toFixed(1);
      return `
        <div class="b-book-row ask">
          <div class="b-book-fill" style="width: ${depthPct}%;"></div>
          <span class="b-book-p red">${formatNumber(price, dec)}</span>
          <span class="b-book-s">${formatNumber(size, 4)}</span>
          <span class="b-book-t">${formatCompact(total)}</span>
        </div>
      `;
    }).join('');

    // Bids (Buys - Green)
    const bids = orderBook.bids.slice(0, 6);
    let maxBidVol = Math.max(...bids.map(b => b[1] * b[0]), 1);

    bidsEl.innerHTML = bids.map(b => {
      const price = b[0];
      const size = b[1];
      const total = price * size;
      const depthPct = Math.min(100, (total / maxBidVol) * 100).toFixed(1);
      return `
        <div class="b-book-row bid">
          <div class="b-book-fill" style="width: ${depthPct}%;"></div>
          <span class="b-book-p green">${formatNumber(price, dec)}</span>
          <span class="b-book-s">${formatNumber(size, 4)}</span>
          <span class="b-book-t">${formatCompact(total)}</span>
        </div>
      `;
    }).join('');

    // Mid Price & Spread
    const topBid = orderBook.bids[0] ? orderBook.bids[0][0] : 0;
    const topAsk = orderBook.asks[0] ? orderBook.asks[0][0] : 0;
    if (topBid && topAsk) {
      const spread = Math.abs(topAsk - topBid);
      const mid = (topBid + topAsk) / 2;
      if (midPriceEl) {
        midPriceEl.textContent = formatNumber(mid, dec);
        midPriceEl.className = 'b-mid-price ' + (mid >= tickerData.price ? 'green' : 'red');
      }
      if (spreadEl) {
        spreadEl.textContent = `Spread: ${formatNumber(spread, dec)}`;
      }
    }
  }

  // --- Render Recent Trades ---
  function renderRecentTrades() {
    const listEl = $('b-trades-list');
    if (!listEl) return;
    const meta = MARKETS[currentSymbol] || { decimals: 2 };
    const dec = meta.decimals;

    listEl.innerHTML = recentTrades.map(tr => {
      const d = new Date(tr.time);
      const timeStr = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
      const color = tr.isBuyerMaker ? 'red' : 'green';
      return `
        <div class="b-trade-row ${color}">
          <span class="b-trade-p ${color}">${formatNumber(tr.price, dec)}</span>
          <span class="b-trade-q">${formatNumber(tr.qty, 4)}</span>
          <span class="b-trade-t">${timeStr}</span>
        </div>
      `;
    }).join('');
  }

  // --- Switch Market Symbol ---
  function switchMarket(symbol) {
    if (!MARKETS[symbol]) return;
    currentSymbol = symbol;

    // Update active tab buttons
    document.querySelectorAll('.b-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.symbol === symbol);
    });

    // Reset local data & show loading state
    klines = [];
    orderBook = { asks: [], bids: [] };
    recentTrades = [];
    crosshair = null;

    panOffset = 0;
    visibleCandlesCount = 55;
    fetchBinanceKlines(currentSymbol, currentInterval);
    fetchBinanceTicker(currentSymbol);
    fetchBinanceDepth(currentSymbol);
    connectWebSocket(currentSymbol, currentInterval);

    // Sync Traders Order Flow Terminal
    if (window.TradersTerminal && typeof window.TradersTerminal.switchMarket === 'function') {
      window.TradersTerminal.switchMarket(symbol);
    }
  }

  // --- Switch Timeframe Interval ---
  function switchInterval(interval) {
    currentInterval = interval;
    document.querySelectorAll('.b-tf-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.interval === interval);
    });
    panOffset = 0;
    visibleCandlesCount = 55;
    fetchBinanceKlines(currentSymbol, currentInterval);
    connectWebSocket(currentSymbol, currentInterval);
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
      renderChart();
    }
  }

  // --- Interactive Drag-to-Pan, Mouse Wheel Zoom & Touch Navigation ---
  function setupChartInteractions() {
    if (!canvas) return;

    // Helper: calculate max pan limit
    function getMaxPan() {
      const chartW = (canvas.width / dpr) - 64;
      const candleSlot = chartW / visibleCandlesCount;
      return Math.max(0, (klines.length - 8) * candleSlot);
    }

    // Helper: find candle index at X coordinate
    function getCandleIndexAtX(x) {
      if (klines.length === 0) return -1;
      const chartW = (canvas.width / dpr) - 64;
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
      isDragging = true;
      hasMovedDuringDrag = false;
      dragStartX = e.clientX;
      dragStartPan = panOffset;
      canvas.style.cursor = 'grabbing';
    });

    window.addEventListener('mousemove', function (e) {
      if (!canvas) return;

      if (isDragging) {
        const dx = e.clientX - dragStartX;
        if (Math.abs(dx) > 3) hasMovedDuringDrag = true;
        panOffset = Math.max(0, Math.min(getMaxPan(), dragStartPan + dx));
        renderChart();
        return;
      }

      // Normal crosshair tracking
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const chartW = (canvas.width / dpr) - 64;
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
      renderChart();
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
        renderChart();
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
      const chartW = (canvas.width / dpr) - 64;

      if (visibleCandlesCount !== prevCount && chartW > 0) {
        const prevSlot = chartW / prevCount;
        const newSlot = chartW / visibleCandlesCount;
        const ratio = Math.max(0, Math.min(1, mouseX / chartW));
        panOffset = Math.max(0, Math.min(getMaxPan(), panOffset + (chartW * (1 - ratio)) * (newSlot / prevSlot - 1)));
      }

      renderChart();
    }, { passive: false });

    // --- Click: Handle "▶ TO LIVE" Button ---
    canvas.addEventListener('click', function (e) {
      if (hasMovedDuringDrag) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const chartW = (canvas.width / dpr) - 64;
      const badgeX = chartW - 80;
      const badgeY = 8;
      if (x >= badgeX && x <= badgeX + 74 && y >= badgeY && y <= badgeY + 20) {
        panOffset = 0;
        renderChart();
      }
    });

    // --- Double Click: Reset View ---
    canvas.addEventListener('dblclick', function () {
      panOffset = 0;
      visibleCandlesCount = 55;
      renderChart();
    });

    // --- Touch Screen Drag & Pinch Zoom ---
    let touchStartX = 0;
    let touchStartPan = 0;
    let touchInitialPinch = 0;
    let touchInitialCount = 55;

    canvas.addEventListener('touchstart', function (e) {
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
    }, { passive: true });

    canvas.addEventListener('touchmove', function (e) {
      const chartW = (canvas.width / dpr) - 64;
      const candleSlot = chartW / visibleCandlesCount;
      const maxPanOffset = Math.max(0, (klines.length - 8) * candleSlot);

      if (e.touches.length === 1 && isDragging) {
        const dx = e.touches[0].clientX - touchStartX;
        if (Math.abs(dx) > 3) hasMovedDuringDrag = true;
        panOffset = Math.max(0, Math.min(maxPanOffset, touchStartPan + dx));
        renderChart();
      } else if (e.touches.length === 2 && touchInitialPinch > 0) {
        const currentPinch = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        const scale = touchInitialPinch / currentPinch;
        visibleCandlesCount = Math.max(16, Math.min(220, Math.round(touchInitialCount * scale)));
        renderChart();
      }
    }, { passive: true });

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

    // Timeframe Tabs Listener
    const tfTabs = $('binance-tf-tabs');
    if (tfTabs) {
      tfTabs.addEventListener('click', e => {
        const btn = e.target.closest('.b-tf-btn');
        if (!btn || !btn.dataset.interval) return;
        switchInterval(btn.dataset.interval);
      });
    }

    // Sidebar View Toggle (Depth vs Trades)
    const tabDepth = $('b-tab-depth');
    const tabTrades = $('b-tab-trades');
    const viewDepth = $('b-depth-container');
    const viewTrades = $('b-trades-container');

    if (tabDepth && tabTrades) {
      tabDepth.addEventListener('click', () => {
        activeSideTab = 'depth';
        tabDepth.classList.add('active');
        tabTrades.classList.remove('active');
        if (viewDepth) viewDepth.style.display = 'flex';
        if (viewTrades) viewTrades.style.display = 'none';
        renderOrderBook();
      });

      tabTrades.addEventListener('click', () => {
        activeSideTab = 'trades';
        tabTrades.classList.add('active');
        tabDepth.classList.remove('active');
        if (viewDepth) viewDepth.style.display = 'none';
        if (viewTrades) viewTrades.style.display = 'flex';
        renderRecentTrades();
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

    // Initial Load
    switchMarket(currentSymbol);
  }

  // Global API export for dashboard cross-functional integration
  window.SoftradeXTerminal = window.BinanceTerminal = {
    switchMarket,
    switchInterval,
    connectWebSocket,
    getCurrentSymbol: () => currentSymbol,
    MARKETS
  };

  // Auto-init when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSoftradeXTerminal);
  } else {
    setTimeout(initSoftradeXTerminal, 100);
  }

})();
