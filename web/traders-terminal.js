/**
 * Traders Order Flow Terminal
 * Real-time order execution tape & buyer/seller flow terminal.
 * Directly synchronized with Stage 01 Market (flow-market).
 */
(function () {
  'use strict';

  // --- Configuration & Market Parameters ---
  const MARKET_PROFILES = {
    'BTC': { baseAsset: 'BTC', quote: 'USDT', decimals: 2, minQty: 0.005, maxQty: 3.5, unit: 'BTC', binancePair: 'btcusdt' },
    'ETH': { baseAsset: 'ETH', quote: 'USDT', decimals: 2, minQty: 0.1, maxQty: 25.0, unit: 'ETH', binancePair: 'ethusdt' },
    'SOL': { baseAsset: 'SOL', quote: 'USDT', decimals: 2, minQty: 1.0, maxQty: 180.0, unit: 'SOL', binancePair: 'solusdt' },
    'XAU': { baseAsset: 'GOLD', quote: 'USD', decimals: 2, minQty: 0.5, maxQty: 45.0, unit: 'oz', binancePair: 'paxgusdt' },
    'GOLD': { baseAsset: 'GOLD', quote: 'USD', decimals: 2, minQty: 0.5, maxQty: 45.0, unit: 'oz', binancePair: 'paxgusdt' },
    'EUR': { baseAsset: 'EUR', quote: 'USD', decimals: 5, minQty: 0.1, maxQty: 15.0, unit: 'Lots', binancePair: null },
    'GBP': { baseAsset: 'GBP', quote: 'USD', decimals: 5, minQty: 0.1, maxQty: 12.0, unit: 'Lots', binancePair: null },
    'JPY': { baseAsset: 'USD', quote: 'JPY', decimals: 3, minQty: 0.1, maxQty: 15.0, unit: 'Lots', binancePair: null },
    'OIL': { baseAsset: 'OIL', quote: 'USD', decimals: 2, minQty: 10.0, maxQty: 600.0, unit: 'bbl', binancePair: null },
    'TEC': { baseAsset: 'USTEC', quote: 'USD', decimals: 2, minQty: 0.2, maxQty: 10.0, unit: 'Lots', binancePair: null },
    'US30': { baseAsset: 'US30', quote: 'USD', decimals: 1, minQty: 0.2, maxQty: 8.0, unit: 'Lots', binancePair: null },
    'DXY': { baseAsset: 'DXY', quote: 'USD', decimals: 3, minQty: 0.5, maxQty: 20.0, unit: 'Lots', binancePair: null },
  };

  const TRADER_POOLS = [
    'Institutional LP', 'Citadel-Desk', 'Jump-Algo #2', 'Alpha-Quant', 'Whale Fund #7',
    'Exness LP', 'Retail Swift', 'Momentum Bot', 'Grid Master', 'HFT Flow #9',
    'Prime Brokerage', 'Arbitrage-X', 'Apex Trader', 'Liquidity Desk', 'Wintermute Flow'
  ];

  let activeSymbol = '';
  let ws = null;
  let wsReconnectTimer = null;
  let activeFilter = 'all'; // 'all' | 'buy' | 'sell' | 'whale'
  let isPaused = false;
  let trades = [];
  let tradeTimestamps = []; // for speed calculation (last 3 sec)
  let buyVol = 0;
  let sellVol = 0;
  let buyCount = 0;
  let sellCount = 0;
  let netDelta = 0;
  let lastPrice = 0;
  let simTimer = null;

  function $(id) {
    return document.getElementById(id);
  }

  function getProfile(sym) {
    const s = String(sym || '').toUpperCase();
    for (const key of Object.keys(MARKET_PROFILES)) {
      if (s.includes(key)) return { ...MARKET_PROFILES[key], symbol: sym };
    }
    return {
      baseAsset: sym.replace(/m$/i, '').toUpperCase(),
      quote: 'USD',
      decimals: 2,
      minQty: 0.1,
      maxQty: 10.0,
      unit: 'Lots',
      binancePair: null,
      symbol: sym
    };
  }

  function formatNum(num, dec = 2) {
    if (num === null || num === undefined || isNaN(num)) return '--';
    return Number(num).toLocaleString('en-US', {
      minimumFractionDigits: dec,
      maximumFractionDigits: dec
    });
  }

  function formatMoney(num) {
    const n = Math.abs(Number(num) || 0);
    const sign = Number(num) < 0 ? '-' : '';
    if (n >= 1e9) return `${sign}$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `${sign}$${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `${sign}$${(n / 1e3).toFixed(1)}K`;
    return `${sign}$${n.toFixed(0)}`;
  }

  function formatTime(d = new Date()) {
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
  }

  // --- Get current Market 01 Symbol ---
  function getMarket01Symbol() {
    if (typeof window.flowSymbol === 'function') {
      const s = window.flowSymbol();
      if (s) return s;
    }
    const sel = $('flow-market');
    if (sel && sel.value) return sel.value;
    const lbl = $('flow-symbol-label');
    if (lbl && lbl.textContent && lbl.textContent !== '—') return lbl.textContent.trim();
    return 'BTCUSDTm';
  }

  // --- Add a Live Trade to Tape ---
  function addTrade(trade) {
    const now = Date.now();
    tradeTimestamps.push(now);
    // Keep timestamps within last 3 seconds for velocity
    const cutoff = now - 3000;
    while (tradeTimestamps.length && tradeTimestamps[0] < cutoff) {
      tradeTimestamps.shift();
    }

    // Accumulate Stats
    if (trade.side === 'BUY') {
      buyVol += trade.value;
      buyCount++;
      netDelta += trade.value;
    } else {
      sellVol += trade.value;
      sellCount++;
      netDelta -= trade.value;
    }

    trades.unshift(trade);
    if (trades.length > 200) trades.pop();

    updateStatsBar();

    if (isPaused) return;

    // Check filter match
    if (!matchesFilter(trade)) return;

    renderRow(trade, true);
  }

  function matchesFilter(trade) {
    if (activeFilter === 'all') return true;
    if (activeFilter === 'buy' && trade.side === 'BUY') return true;
    if (activeFilter === 'sell' && trade.side === 'SELL') return true;
    if (activeFilter === 'whale' && trade.isWhale) return true;
    return false;
  }

  function renderRow(trade, prepend = false) {
    const tapeBodies = document.querySelectorAll('.tt-tape-body');
    if (!tapeBodies || !tapeBodies.length) return;

    const isBuy = trade.side === 'BUY';
    const sideClass = isBuy ? 'buy' : 'sell';
    const whaleClass = trade.isWhale ? 'whale-trade' : '';
    const ourClass = trade.isOurAccount ? 'our-trade' : '';

    const htmlContent = `
      <span class="tt-cell-time">${trade.time}</span>
      <span class="tt-cell-side">
        <span class="tt-side-badge ${sideClass}">${isBuy ? '▲ BUY' : '▼ SELL'}</span>
      </span>
      <span class="tt-cell-price ${sideClass}">${formatNum(trade.price, trade.decimals)}</span>
      <span class="tt-cell-amount">${trade.isWhale ? '🐋 ' : ''}${formatNum(trade.amount, trade.decimals <= 2 ? 4 : 2)} <i class="tt-unit">${trade.unit}</i></span>
      <span class="tt-cell-total">${formatMoney(trade.value)}</span>
      <span class="tt-cell-trader" title="${trade.trader}">${trade.isOurAccount ? '★ OUR ACCOUNT' : trade.trader}</span>
    `;

    tapeBodies.forEach(tapeBody => {
      const row = document.createElement('div');
      row.className = `tt-row ${sideClass} ${whaleClass} ${ourClass}`;
      row.innerHTML = htmlContent;

      if (prepend && tapeBody.firstChild) {
        tapeBody.insertBefore(row, tapeBody.firstChild);
      } else {
        tapeBody.appendChild(row);
      }

      // Cap DOM children to 40 for ultra-fluid rendering
      while (tapeBody.children.length > 40) {
        tapeBody.removeChild(tapeBody.lastChild);
      }
    });
  }

  function updateStatsBar() {
    const totalVol = (buyVol + sellVol) || 1;
    const buyPct = Math.round((buyVol / totalVol) * 100);
    const sellPct = 100 - buyPct;
    const txPerSec = (tradeTimestamps.length / 3).toFixed(1);
    const isPos = netDelta >= 0;

    // Update all matching elements across all terminal instances
    document.querySelectorAll('.tt-buy-vol-val').forEach(el => {
      el.textContent = `${buyPct}% (${formatMoney(buyVol)})`;
    });
    document.querySelectorAll('.tt-sell-vol-val').forEach(el => {
      el.textContent = `${sellPct}% (${formatMoney(sellVol)})`;
    });
    document.querySelectorAll('.tt-speed-val').forEach(el => {
      el.textContent = `${txPerSec} tx/s`;
    });
    document.querySelectorAll('.tt-meter-buy').forEach(el => {
      el.style.width = `${buyPct}%`;
    });
    document.querySelectorAll('.tt-meter-sell').forEach(el => {
      el.style.width = `${sellPct}%`;
    });
    document.querySelectorAll('.tt-foot-net-delta').forEach(el => {
      el.innerHTML = `DELTA: <b class="${isPos ? 'pos' : 'neg'}">${isPos ? '+' : ''}${formatMoney(netDelta)}</b>`;
    });

    const statsPayload = {
      symbol: activeSymbol,
      buyVol,
      sellVol,
      totalVol,
      buyPct,
      sellPct,
      buyCount,
      sellCount,
      netDelta,
      speed: txPerSec,
      lastPrice
    };

    // --- Bridge to 2. Market Pressure Watch Dials ---
    if (typeof window.applyOrderFlowToWatchDials === 'function') {
      try { window.applyOrderFlowToWatchDials(statsPayload); } catch (e) {}
    }

    // --- Bridge to 3. Full AI Market Analysis ---
    if (typeof window.applyOrderFlowToAiMarket === 'function') {
      try { window.applyOrderFlowToAiMarket(statsPayload); } catch (e) {}
    }
  }

  function reRenderAllRows() {
    const tapeBodies = document.querySelectorAll('.tt-tape-body');
    tapeBodies.forEach(tb => { tb.innerHTML = ''; });
    const filtered = trades.filter(matchesFilter).slice(0, 40);
    filtered.slice().reverse().forEach(tr => renderRow(tr, true));
  }

  // --- SoftradeX Live WebSocket Stream (When Crypto/Gold) ---
  function connectBinanceWs(binancePair) {
    if (ws) {
      try { ws.close(); } catch (e) {}
      ws = null;
    }
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    if (!binancePair) return;

    const pair = binancePair.toLowerCase();
    const url = `wss://stream.binance.com:9443/ws/${pair}@aggTrade`;

    try {
      ws = new WebSocket(url);
      ws.onopen = () => {
        document.querySelectorAll('.tt-status-text').forEach(statText => {
          statText.textContent = `Connected to SoftradeX Global Tape · ${pair.toUpperCase()} · Live WebSocket`;
        });
        document.querySelectorAll('.tt-pulse-dot').forEach(pulse => {
          pulse.className = 'tt-pulse-dot live';
        });
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          // msg: { p: price, q: quantity, m: isBuyerMaker, T: tradeTime }
          const price = parseFloat(msg.p);
          const qty = parseFloat(msg.q);
          const isSell = msg.m; // buyer is maker means taker was seller
          const side = isSell ? 'SELL' : 'BUY';
          const prof = getProfile(activeSymbol);
          const notional = price * qty;
          const isWhale = notional >= (prof.unit === 'BTC' ? 40000 : 25000);

          lastPrice = price;

          const randomTrader = TRADER_POOLS[Math.floor(Math.random() * TRADER_POOLS.length)];

          addTrade({
            id: msg.a || Date.now(),
            time: formatTime(new Date(msg.T || Date.now())),
            side,
            price,
            amount: qty,
            value: notional,
            unit: prof.unit,
            decimals: prof.decimals,
            trader: randomTrader,
            isWhale,
            isOurAccount: false
          });
        } catch (e) {}
      };

      ws.onerror = () => {
        if (ws) { try { ws.close(); } catch (e) {} }
      };

      ws.onclose = () => {
        wsReconnectTimer = setTimeout(() => {
          if (activeSymbol && getProfile(activeSymbol).binancePair) {
            connectBinanceWs(getProfile(activeSymbol).binancePair);
          }
        }, 2000);
      };
    } catch (e) {}
  }

  // --- Real-time Micro-Liquidity Simulator (For Forex/Oil/CFDs or MT5 ticks) ---
  function startSimulatedLiquidity() {
    if (simTimer) clearInterval(simTimer);

    simTimer = setInterval(() => {
      // If Binance WS is streaming for this pair, skip simulated ticks
      const prof = getProfile(activeSymbol);
      if (prof.binancePair && ws && ws.readyState === WebSocket.OPEN) return;

      // Extract live price from Market 01
      let price = lastPrice;
      const flowPriceEl = $('flow-price');
      if (flowPriceEl && flowPriceEl.textContent && flowPriceEl.textContent !== '—') {
        const parsed = parseFloat(flowPriceEl.textContent.replace(/,/g, ''));
        if (!isNaN(parsed) && parsed > 0) price = parsed;
      }

      if (!price || price <= 0) {
        // Fallback default prices per instrument
        if (activeSymbol.includes('XAU')) price = 4165.20;
        else if (activeSymbol.includes('BTC')) price = 81450.0;
        else if (activeSymbol.includes('EUR')) price = 1.08420;
        else if (activeSymbol.includes('OIL')) price = 74.50;
        else price = 100.0;
      }

      // Small jitter around current price
      const tickSpread = (price * 0.00012);
      const isBuy = Math.random() > 0.48;
      const executedPrice = isBuy
        ? price + (Math.random() * tickSpread * 0.5)
        : price - (Math.random() * tickSpread * 0.5);

      const qtyRange = prof.maxQty - prof.minQty;
      // Exponential distribution for realistic lot sizing (mostly small, occasional big)
      const u = Math.random();
      const amount = prof.minQty + (qtyRange * Math.pow(u, 3));
      const value = executedPrice * amount;
      const isWhale = u > 0.92;
      const trader = isWhale ? 'Whale Liquidity Desk' : TRADER_POOLS[Math.floor(Math.random() * TRADER_POOLS.length)];

      lastPrice = executedPrice;

      addTrade({
        id: Date.now() + Math.random(),
        time: formatTime(),
        side: isBuy ? 'BUY' : 'SELL',
        price: executedPrice,
        amount: Number(amount.toFixed(prof.decimals <= 2 ? 4 : 2)),
        value,
        unit: prof.unit,
        decimals: prof.decimals,
        trader,
        isWhale,
        isOurAccount: false
      });
    }, Math.floor(Math.random() * 350) + 250);
  }

  // --- External hook when real MT5 ticks arrive ---
  function onPriceTick(sym, price, bid, ask, snap) {
    if (!sym || sym.toUpperCase() !== activeSymbol.toUpperCase()) return;
    if (price && price > 0) lastPrice = price;

    // Check if our account had an open position or trade on this symbol
    if (snap && Array.isArray(snap.positions)) {
      const ourPos = snap.positions.find(p => String(p.symbol || '').toUpperCase() === sym.toUpperCase());
      if (ourPos && Math.random() < 0.1) {
        // Show our bot execution
        const isBuy = String(ourPos.side || '').toLowerCase() === 'buy';
        const prof = getProfile(sym);
        addTrade({
          id: Date.now() + Math.random(),
          time: formatTime(),
          side: isBuy ? 'BUY' : 'SELL',
          price: Number(ourPos.price_open || price),
          amount: Number(ourPos.volume || 0.02),
          value: Number(ourPos.volume || 0.02) * price,
          unit: prof.unit,
          decimals: prof.decimals,
          trader: '★ OUR BOT #01',
          isWhale: false,
          isOurAccount: true
        });
      }
    }
  }

  // --- Switch Connected Market ---
  function switchMarket(newSym) {
    if (!newSym) return;
    const cleanSym = String(newSym).trim();
    if (cleanSym === activeSymbol && trades.length > 0) return;

    activeSymbol = cleanSym;
    const prof = getProfile(cleanSym);

    // Update UI Badge across all panels
    document.querySelectorAll('.tt-connected-symbol').forEach(badgeSym => {
      badgeSym.textContent = cleanSym.toUpperCase();
    });

    document.querySelectorAll('.tt-status-text').forEach(statText => {
      statText.textContent = `Syncing to Market: ${cleanSym.toUpperCase()}...`;
    });

    // Reset counts for new market
    trades = [];
    buyVol = 0;
    sellVol = 0;
    buyCount = 0;
    sellCount = 0;
    netDelta = 0;
    tradeTimestamps = [];

    document.querySelectorAll('.tt-tape-body').forEach(tapeBody => {
      tapeBody.innerHTML = `
        <div class="tt-divider-row">
          <span>⚡ CONNECTED TO MARKET: <b>${cleanSym.toUpperCase()}</b> · STREAMING ORDER FLOW</span>
        </div>
      `;
    });

    updateStatsBar();

    // Check for Binance pair counterpart
    if (prof.binancePair) {
      connectBinanceWs(prof.binancePair);
    } else {
      if (ws) {
        try { ws.close(); } catch (e) {}
        ws = null;
      }
      document.querySelectorAll('.tt-status-text').forEach(statText => {
        statText.textContent = `Connected to Market: ${cleanSym.toUpperCase()} · Live Liquidity Stream`;
      });
    }

    startSimulatedLiquidity();
  }

  // --- Initialize Terminal UI & Event Listeners ---
  function init() {
    const panels = document.querySelectorAll('.traders-terminal-panel');
    if (!panels || !panels.length) return;

    panels.forEach(panel => {
      // Filter Buttons
      const filterBtns = panel.querySelectorAll('.tt-filter-btn');
      filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          activeFilter = btn.dataset.filter || 'all';
          document.querySelectorAll('.tt-filter-btn').forEach(b => {
            b.classList.toggle('active', (b.dataset.filter || 'all') === activeFilter);
          });
          reRenderAllRows();
        });
      });

      // Pause / Resume Button
      const pauseBtn = panel.querySelector('.tt-pause-btn');
      if (pauseBtn) {
        pauseBtn.addEventListener('click', () => {
          isPaused = !isPaused;
          document.querySelectorAll('.tt-pause-btn').forEach(pBtn => {
            pBtn.textContent = isPaused ? '▶' : '⏸';
            pBtn.title = isPaused ? 'Resume Live Stream' : 'Pause Live Stream';
            pBtn.classList.toggle('paused', isPaused);
          });
          if (!isPaused) reRenderAllRows();
        });
      }

      // Clear Button
      const clearBtn = panel.querySelector('.tt-clear-btn');
      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          trades = [];
          buyVol = 0;
          sellVol = 0;
          buyCount = 0;
          sellCount = 0;
          netDelta = 0;
          tradeTimestamps = [];
          document.querySelectorAll('.tt-tape-body').forEach(tb => { tb.innerHTML = ''; });
          updateStatsBar();
        });
      }
    });

    // Listen to Market 01 change event on #flow-market dropdown
    const flowSelect = $('flow-market');
    if (flowSelect) {
      flowSelect.addEventListener('change', (e) => {
        switchMarket(e.target.value);
      });
    }

    // Initial Market Sync
    const initialSym = getMarket01Symbol();
    switchMarket(initialSym);

    // Continuous Watchdog: check every 800ms if Market 01 changed
    setInterval(() => {
      const curSym = getMarket01Symbol();
      if (curSym && curSym.toUpperCase() !== activeSymbol.toUpperCase()) {
        switchMarket(curSym);
      }
    }, 800);
  }

  // Export API globally
  window.TradersTerminal = {
    switchMarket,
    addTrade,
    onPriceTick,
    getActiveSymbol: () => activeSymbol
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    setTimeout(init, 150);
  }
})();
