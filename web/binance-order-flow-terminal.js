/**
 * Binance Order Flow Terminal
 * Captures real Binance aggregate trades for the market selected in Stage 01.
 * Binance aggTrade messages identify the taker side:
 *   m=true  -> the taker sold
 *   m=false -> the taker bought
 */
(function () {
  'use strict';

  const PAIRS = {
    BTC: 'btcusdt',
    ETH: 'ethusdt',
    SOL: 'solusdt',
    XAU: 'paxgusdt',
    GOLD: 'paxgusdt'
  };
  const states = new Map();
  let selectedSymbol = '';
  let selectedPair = '';
  let socket = null;
  let reconnectTimer = null;
  let reconnectAttempt = 0;
  let paused = false;
  let filter = 'ALL';

  function $(id) { return document.getElementById(id); }

  function stateFor(pair) {
    if (!states.has(pair)) {
      states.set(pair, { buy: 0, sell: 0, buyCount: 0, sellCount: 0, trades: [] });
    }
    return states.get(pair);
  }

  function compact(value) {
    const n = Number(value) || 0;
    if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
    return '$' + n.toFixed(2);
  }

  function number(value, decimals) {
    return (Number(value) || 0).toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    });
  }

  function pairForSymbol(symbol) {
    const raw = String(symbol || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    for (const key of Object.keys(PAIRS)) {
      if (raw.indexOf(key) === 0) return PAIRS[key];
    }
    if (/^[A-Z0-9]+USDT$/.test(raw)) return raw.toLowerCase();
    return '';
  }

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  function combinedTotals() {
    const state = stateFor(selectedPair);
    let tradersBuy = 0;
    let tradersSell = 0;
    const terminal = window.TradersTerminal;
    if (terminal && typeof terminal.getOpenVolume === 'function') {
      try {
        const totals = terminal.getOpenVolume() || {};
        tradersBuy = Number(totals.openBuyVol) || 0;
        tradersSell = Number(totals.openSellVol) || 0;
      } catch (e) {}
    }
    const otherExchanges = window.MultiExchangeOrderFlow &&
      typeof window.MultiExchangeOrderFlow.getTotals === 'function'
      ? window.MultiExchangeOrderFlow.getTotals() : { buy: 0, sell: 0 };
    const combined = window.MultiExchangeOrderFlow &&
      typeof window.MultiExchangeOrderFlow.getOpenTotals === 'function'
      ? window.MultiExchangeOrderFlow.getOpenTotals() : null;
    const breakdown = combined && combined.breakdown
      ? combined.breakdown
      : (window.MultiExchangeOrderFlow &&
         typeof window.MultiExchangeOrderFlow.getBreakdown === 'function'
        ? window.MultiExchangeOrderFlow.getBreakdown() : []);
    const venueBuy = combined ? Number(combined.venueBuy) || 0
      : (Number(otherExchanges.buy) || 0);
    const venueSell = combined ? Number(combined.venueSell) || 0
      : (Number(otherExchanges.sell) || 0);
    const finalTradersBuy = combined ? Number(combined.tradersBuy) || 0 : tradersBuy;
    const finalTradersSell = combined ? Number(combined.tradersSell) || 0 : tradersSell;
    const binanceBuy = Number(state.buy) || 0;
    const binanceSell = Number(state.sell) || 0;
    return {
      // The top chips represent every source once: 12 public venue tapes,
      // Binance's own tape, and the Traders terminal's running open positions.
      buy: combined ? Number(combined.buy) || 0 : venueBuy + binanceBuy + finalTradersBuy,
      sell: combined ? Number(combined.sell) || 0 : venueSell + binanceSell + finalTradersSell,
      venueBuy,
      venueSell,
      binanceBuy: combined ? Number(combined.binanceBuy) || 0 : binanceBuy,
      binanceSell: combined ? Number(combined.binanceSell) || 0 : binanceSell,
      tradersBuy: finalTradersBuy,
      tradersSell: finalTradersSell,
      breakdown,
      buyCount: state.buyCount,
      sellCount: state.sellCount
    };
  }

  // One line per source so the chip total can be read back to every terminal
  // on the page: the 12 venues, Binance, then the Traders open positions.
  function sourceLines(totals, side) {
    const lines = [
      `12 VENUES ${side.toUpperCase()}: ${compact(totals['venue' + side])}`,
      `BINANCE ${side.toUpperCase()}: ${compact(totals['binance' + side])}`,
      `TRADERS POSITIONS ${side.toUpperCase()}: ${compact(totals['traders' + side])}`
    ];
    const key = side === 'Buy' ? 'buy' : 'sell';
    const venues = (totals.breakdown || []).filter(entry => !entry.extra);
    for (let i = 0; i < venues.length; i += 3) {
      lines.push(venues.slice(i, i + 3)
        .map(entry => `${entry.name} ${compact(entry[key])}`)
        .join(' + '));
    }
    lines.push(`TOTAL ${side.toUpperCase()} OPEN: ${compact(totals[side === 'Buy' ? 'buy' : 'sell'])}`);
    return lines.join('\n');
  }

  function updateTopChips() {
    const totals = combinedTotals();
    const buy = compact(totals.buy);
    const sell = compact(totals.sell);
    document.querySelectorAll('.b-open-buy-val').forEach(el => { el.textContent = buy; });
    document.querySelectorAll('.b-open-sell-val').forEach(el => { el.textContent = sell; });
    const buyChip = $('b-open-buy-chip');
    const sellChip = $('b-open-sell-chip');
    if (sellChip) {
      sellChip.title = `COMBINED ${selectedPair.toUpperCase()} SELL OPEN (12 venues + Binance + positions): ${sell}\n` +
        sourceLines(totals, 'Sell');
    }
    if (buyChip) {
      buyChip.title = `COMBINED ${selectedPair.toUpperCase()} BUY OPEN (12 venues + Binance + positions): ${buy}\n` +
        sourceLines(totals, 'Buy');
    }
  }

  function render() {
    const state = stateFor(selectedPair);
    const total = state.buy + state.sell;
    const buyPct = total ? Math.round((state.buy / total) * 100) : 0;
    const sellPct = total ? 100 - buyPct : 0;
    setText('bof-market', selectedPair ? selectedPair.toUpperCase() : '—');
    setText('bof-status', selectedPair ? (socket && socket.readyState === WebSocket.OPEN ? 'CONNECTED' : 'CONNECTING') : 'NO BINANCE PAIR');
    setText('bof-buy-total', compact(state.buy));
    setText('bof-sell-total', compact(state.sell));
    setText('bof-buy-pct', `${buyPct}%`);
    setText('bof-sell-pct', `${sellPct}%`);
    const body = $('bof-tape');
    if (!body) return;
    const rows = state.trades.filter(tr => filter === 'ALL' || tr.side === filter).slice(0, 80);
    body.innerHTML = rows.map(tr => `
      <div class="bof-row ${tr.side === 'BUY' ? 'buy' : 'sell'}">
        <span>${new Date(tr.ts).toLocaleTimeString()}</span>
        <b>${tr.side}</b>
        <span>${number(tr.price, tr.price >= 100 ? 2 : 5)}</span>
        <span>${number(tr.qty, 6)}</span>
        <strong>${compact(tr.notional)}</strong>
      </div>`).join('');
  }

  function setConnection(kind, text) {
    const dot = $('bof-pulse');
    if (dot) dot.className = 'bof-pulse ' + kind;
    setText('bof-status', text);
  }

  function closeSocket() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket) {
      socket.onclose = null;
      try { socket.close(); } catch (e) {}
      socket = null;
    }
  }

  function scheduleReconnect() {
    if (!selectedPair || reconnectTimer) return;
    const wait = Math.min(30000, 1000 * Math.pow(1.7, reconnectAttempt++));
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, wait);
  }

  function connect() {
    closeSocket();
    if (!selectedPair) {
      setConnection('offline', 'NO BINANCE PAIR');
      render();
      return;
    }
    setConnection('connecting', 'CONNECTING');
    try {
      socket = new WebSocket(`wss://stream.binance.com:9443/ws/${selectedPair}@aggTrade`);
      socket.onopen = () => {
        reconnectAttempt = 0;
        setConnection('live', 'CONNECTED');
        render();
      };
      socket.onmessage = event => {
        if (paused) return;
        try {
          const msg = JSON.parse(event.data);
          // A frame from a socket replaced by a market switch must never
          // be booked into the newly selected market.
          if (msg.s && String(msg.s).toLowerCase() !== selectedPair) return;
          const price = Number(msg.p);
          const qty = Number(msg.q);
          if (!(price > 0) || !(qty > 0)) return;
          const side = msg.m ? 'SELL' : 'BUY';
          const state = stateFor(selectedPair);
          const notional = price * qty;
          state[side === 'BUY' ? 'buy' : 'sell'] += notional;
          state[side === 'BUY' ? 'buyCount' : 'sellCount'] += 1;
          state.trades.unshift({ ts: Number(msg.T) || Date.now(), side, price, qty, notional });
          if (state.trades.length > 500) state.trades.length = 500;
          updateTopChips();
          render();
        } catch (e) {
          setConnection('error', 'INVALID BINANCE DATA');
        }
      };
      socket.onerror = () => setConnection('error', 'BINANCE STREAM ERROR');
      socket.onclose = () => {
        socket = null;
        if (selectedPair) {
          setConnection('connecting', 'RECONNECTING');
          scheduleReconnect();
        }
      };
    } catch (e) {
      setConnection('error', 'BINANCE UNAVAILABLE');
      scheduleReconnect();
    }
  }

  function switchMarket(symbol) {
    const nextSymbol = String(symbol || '');
    const nextPair = pairForSymbol(nextSymbol);
    // XAUUSD, XAUUSDm and PAXGUSDT all map to one stream: never tear down a
    // healthy connection just because the label around it changed.
    if (nextPair && nextPair === selectedPair && socket) {
      selectedSymbol = nextSymbol;
      updateTopChips();
      return;
    }
    selectedSymbol = nextSymbol;
    selectedPair = nextPair;
    closeSocket();
    reconnectAttempt = 0;
    updateTopChips();
    render();
    connect();
  }

  function bind() {
    document.querySelectorAll('[data-bof-filter]').forEach(button => {
      button.addEventListener('click', () => {
        filter = button.dataset.bofFilter;
        document.querySelectorAll('[data-bof-filter]').forEach(el => el.classList.toggle('active', el === button));
        render();
      });
    });
    const pause = $('bof-pause');
    if (pause) pause.addEventListener('click', () => {
      paused = !paused;
      pause.textContent = paused ? '▶' : 'Ⅱ';
      pause.classList.toggle('active', paused);
      setText('bof-status', paused ? 'PAUSED' : 'CONNECTED');
    });
    window.addEventListener('chart:market', event => {
      switchMarket(event.detail && event.detail.symbol);
    });
    window.BinanceOrderFlowBridge = {
      active: true,
      getTotals: () => {
        const state = stateFor(selectedPair);
        return { buy: state.buy, sell: state.sell, symbol: selectedSymbol };
      },
      refresh: updateTopChips
    };
    updateTopChips();
    window.setInterval(updateTopChips, 500);
    const initial = $('tt-sidebar-connected-symbol') || $('tt-connected-symbol');
    switchMarket(initial ? initial.textContent : '');
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
