/**
 * Additional public exchange order-flow terminals.
 * These streams are read-only and never place orders.
 */
(function () {
  'use strict';

  const EXCHANGES = [
    { id: 'coinbase', name: 'COINBASE', pairs: { BTC: 'BTC-USD', ETH: 'ETH-USD', SOL: 'SOL-USD', XAU: 'PAXG-USD', GOLD: 'PAXG-USD' } },
    { id: 'kraken', name: 'KRAKEN', pairs: { BTC: 'BTC/USD', ETH: 'ETH/USD', SOL: 'SOL/USD', XAU: 'PAXG/USD', GOLD: 'PAXG/USD' } },
    { id: 'okx', name: 'OKX', pairs: { BTC: 'BTC-USDT', ETH: 'ETH-USDT', SOL: 'SOL-USDT', XAU: 'PAXG-USDT', GOLD: 'PAXG-USDT' } },
    { id: 'bitget', name: 'BITGET', pairs: { BTC: 'BTCUSDT', ETH: 'ETHUSDT', SOL: 'SOLUSDT', XAU: 'PAXGUSDT', GOLD: 'PAXGUSDT' } },
    { id: 'gate', name: 'GATE.IO', pairs: { BTC: 'BTC_USDT', ETH: 'ETH_USDT', SOL: 'SOL_USDT', XAU: 'PAXG_USDT', GOLD: 'PAXG_USDT' } },
    // Bitstamp serves the right headers by handshake but drops them once it
    // throttles the 3s poll, so the browser blocks it. Bybit answers CORS and
    // its spot book lists XAUT (Tether Gold) for the gold view.
    { id: 'bybit', name: 'BYBIT', pairs: { BTC: 'BTCUSDT', ETH: 'ETHUSDT', SOL: 'SOLUSDT', XAU: 'XAUTUSDT', GOLD: 'XAUTUSDT' } }
  ];
  const totals = {};
  const LIVE_WINDOW_MS = 45000;
  let selectedSymbol = '';
  let sockets = {};
  let pollTimer = null;

  const $ = id => document.getElementById(id);
  const compact = n => {
    n = Number(n) || 0;
    if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
    return '$' + n.toFixed(2);
  };
  const symbolKey = symbol => {
    const s = String(symbol || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    return ['BTC', 'ETH', 'SOL', 'XAU', 'GOLD'].find(key => s.indexOf(key) === 0) || '';
  };
  const state = id => totals[id] || (totals[id] = { buy: 0, sell: 0, trades: [], seen: new Set() });
  const set = (id, text) => { if ($(id)) $(id).textContent = text; };

  function render(id) {
    const s = state(id);
    pruneLiveTrades(s);
    recalculate(s);
    set(`mof-${id}-buy`, compact(s.buy));
    set(`mof-${id}-sell`, compact(s.sell));
    // A single exchange WebSocket can stay down while its REST poll keeps
    // answering; the badge follows the data, not the socket only.
    const live = s.connected
      || (Date.now() - (s.lastDataAt || 0) < 10000)
      || (Date.now() - (s.lastPollAt || 0) < 10000);
    set(`mof-${id}-status`, s.pair ? (live ? 'CONNECTED' : 'CONNECTING') : 'NO PAIR');
    const total = s.buy + s.sell;
    set(`mof-${id}-buy-pct`, total ? Math.round(s.buy / total * 100) + '%' : '0%');
    set(`mof-${id}-sell-pct`, total ? Math.round(s.sell / total * 100) + '%' : '0%');
    const tape = $(`mof-${id}-tape`);
    if (tape) {
      tape.innerHTML = s.trades.slice(0, 60).map(tr => `
        <div class="mof-row ${tr.side === 'BUY' ? 'buy' : 'sell'}">
          <span>${new Date(tr.ts).toLocaleTimeString()}</span>
          <b>${tr.side}</b>
          <span>${Number(tr.price).toLocaleString('en-US', { maximumFractionDigits: 5 })}</span>
          <span>${Number(tr.qty).toLocaleString('en-US', { maximumFractionDigits: 6 })}</span>
          <strong>${compact(tr.value)} <small>OPEN</small></strong>
        </div>`).join('');
    }
  }

  function pruneLiveTrades(s) {
    const cutoff = Date.now() - LIVE_WINDOW_MS;
    s.trades = s.trades.filter(tr => tr.ts >= cutoff);
  }

  function recalculate(s) {
    s.buy = s.trades.reduce((sum, tr) => sum + (tr.side === 'BUY' ? tr.value : 0), 0);
    s.sell = s.trades.reduce((sum, tr) => sum + (tr.side === 'SELL' ? tr.value : 0), 0);
  }

  function close(id) {
    if (sockets[id]) {
      sockets[id].onclose = null;
      try { sockets[id].close(); } catch (e) {}
      delete sockets[id];
    }
  }

  function addTrade(id, side, price, qty, tradeId, ts) {
    const s = state(id);
    const value = Number(price) * Number(qty);
    if (!(value > 0)) return;
    if (tradeId && s.seen.has(String(tradeId))) return;
    if (tradeId) {
      s.seen.add(String(tradeId));
      if (s.seen.size > 1200) s.seen.delete(s.seen.values().next().value);
    }
    // Kraken's REST feed encodes the taker side as 'b'/'s' while the other
    // exchanges send 'buy'/'sell' or 'Buy'/'Sell'. Classify by the first
    // letter, or every Kraken buy was booked as a sell and its BUY total
    // stayed empty while the tape showed a BUY row.
    const isBuy = String(side).toUpperCase().charAt(0) === 'B';
    s[isBuy ? 'buy' : 'sell'] += value;
    s.connected = true;
    s.lastDataAt = Date.now();
    // Use the exchange's own print time so a first poll of old history can
    // never masquerade as 45 seconds of fresh order flow.
    const when = Number(ts);
    s.trades.unshift({
      side: isBuy ? 'BUY' : 'SELL',
      price: Number(price),
      qty: Number(qty),
      value,
      ts: Number.isFinite(when) && when > 0 ? when : Date.now()
    });
    if (s.trades.length > 500) s.trades.length = 500;
    render(id);
    const bridge = window.BinanceOrderFlowBridge;
    if (bridge && typeof bridge.refresh === 'function') bridge.refresh();
  }

  async function pollTrades(ex, pair) {
    const urls = {
      coinbase: `https://api.exchange.coinbase.com/products/${encodeURIComponent(pair)}/trades`,
      kraken: `https://api.kraken.com/0/public/Trades?pair=${encodeURIComponent(pair.replace('/', ''))}`,
      okx: `https://www.okx.com/api/v5/market/trades?instId=${encodeURIComponent(pair)}&limit=100`,
      bitget: `https://api.bitget.com/api/v2/spot/market/fills?symbol=${encodeURIComponent(pair)}&limit=100`,
      gate: `https://api.gateio.ws/api/v4/spot/trades?currency_pair=${encodeURIComponent(pair)}&limit=100`,
      bybit: `https://api.bybit.com/v5/market/recent-trade?category=spot&symbol=${encodeURIComponent(pair)}&limit=60`
    };
    const s = state(ex.id);
    try {
      const response = await fetch(urls[ex.id], { cache: 'no-store' });
      if (!response.ok) return;
      // A quiet market can answer with no new prints; the poll itself still
      // proves the terminal is alive.
      s.lastPollAt = Date.now();
      const data = await response.json();
      if (ex.id === 'coinbase') {
        (Array.isArray(data) ? data : []).slice().reverse().forEach(tr =>
          addTrade(ex.id, String(tr.side).toUpperCase(), tr.price, tr.size, tr.trade_id, Date.parse(tr.time)));
      } else if (ex.id === 'kraken') {
        const result = data && data.result ? data.result : {};
        Object.keys(result).filter(key => key !== 'last').forEach(key =>
          (result[key] || []).forEach(tr =>
            addTrade(ex.id, String(tr[3]).toUpperCase(), tr[0], tr[1], tr[6], Number(tr[2]) * 1000)));
      } else if (ex.id === 'okx') {
        (data.data || []).slice().reverse().forEach(tr =>
          addTrade(ex.id, String(tr.side).toUpperCase(), tr.px, tr.sz, tr.tradeId, Number(tr.ts)));
      } else if (ex.id === 'bitget') {
        (data.data || []).slice().reverse().forEach(tr =>
          addTrade(ex.id, String(tr.side).toUpperCase(), tr.price, tr.size, tr.tradeId, Number(tr.ts)));
      } else if (ex.id === 'gate') {
        (Array.isArray(data) ? data : []).slice().reverse().forEach(tr =>
          addTrade(ex.id, String(tr.side).toUpperCase(), tr.price, tr.amount, tr.id, Number(tr.create_time_ms)));
      } else if (ex.id === 'bybit') {
        const rows = (data && data.result && Array.isArray(data.result.list)) ? data.result.list : [];
        rows.slice().reverse().forEach(tr =>
          addTrade(ex.id, String(tr.side).toUpperCase() === 'SELL' ? 'SELL' : 'BUY', tr.price, tr.size,
                   tr.execId, Number(tr.time)));
      }
    } catch (e) {}
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      EXCHANGES.forEach(ex => {
        const pair = state(ex.id).pair;
        if (pair) {
          pollTrades(ex, pair);
          render(ex.id);
        }
      });
    }, 3000);
    setInterval(() => {
      EXCHANGES.forEach(ex => render(ex.id));
      // Trades age out of the 45s window without a new print; refresh the
      // combined BUY OPEN / SELL OPEN chips so they fall with the tapes.
      const bridge = window.BinanceOrderFlowBridge;
      if (bridge && typeof bridge.refresh === 'function') bridge.refresh();
    }, 1000);
  }

  function connectCoinbase(pair, id) {
    const ws = sockets[id] = new WebSocket('wss://advanced-trade-ws.coinbase.com/ws');
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'subscribe', channel: 'market_trades', product_ids: [pair] }));
      state(id).connected = true; render(id);
    };
    ws.onmessage = event => {
      try {
        const msg = JSON.parse(event.data);
        (msg.events || []).forEach(ev => (ev.trades || []).forEach(tr => {
          addTrade(id, String(tr.side).toUpperCase() === 'SELL' ? 'SELL' : 'BUY', tr.price, tr.size,
                   tr.trade_id, Date.parse(tr.time));
        }));
      } catch (e) {}
    };
    ws.onclose = () => { state(id).connected = false; render(id); };
  }

  function connectKraken(pair, id) {
    const ws = sockets[id] = new WebSocket('wss://ws.kraken.com/v2');
    ws.onopen = () => {
      ws.send(JSON.stringify({ method: 'subscribe', params: { channel: 'trade', symbol: [pair] } }));
      state(id).connected = true; render(id);
    };
    ws.onmessage = event => {
      try {
        const msg = JSON.parse(event.data);
        // Keep the exchange trade id: the REST poll returns the same trades,
        // and without the id every one of them is counted twice.
        (msg.data || []).forEach(tr => addTrade(id, String(tr.side).toUpperCase(), tr.price, tr.qty,
          tr.trade_id, Date.parse(tr.timestamp)));
      } catch (e) {}
    };
    ws.onclose = () => { state(id).connected = false; render(id); };
  }

  function connectOkx(pair, id) {
    const ws = sockets[id] = new WebSocket('wss://ws.okx.com:8443/ws/v5/public');
    ws.onopen = () => {
      ws.send(JSON.stringify({ op: 'subscribe', args: [{ channel: 'trades', instId: pair }] }));
      state(id).connected = true; render(id);
    };
    ws.onmessage = event => {
      try {
        const msg = JSON.parse(event.data);
        (msg.data || []).forEach(tr => addTrade(id, tr.side === 'sell' ? 'SELL' : 'BUY', tr.px, tr.sz,
          tr.tradeId, Number(tr.ts)));
      } catch (e) {}
    };
    ws.onclose = () => { state(id).connected = false; render(id); };
  }

  function switchMarket(symbol) {
    const nextKey = symbolKey(symbol);
    // The chart fires chart:market for the same market more than once while
    // it boots; reconnecting every exchange each time only churns sockets.
    if (nextKey === selectedSymbol && Object.keys(sockets).length) return;
    selectedSymbol = nextKey;
    EXCHANGES.forEach(ex => {
      close(ex.id);
      const s = state(ex.id);
      s.buy = 0;
      s.sell = 0;
      s.trades = [];
      s.seen = new Set();
      s.pair = ex.pairs[selectedSymbol] || '';
      s.connected = false;
      s.lastDataAt = 0;
      s.lastPollAt = 0;
      render(ex.id);
      if (!s.pair) return;
      if (ex.id === 'coinbase') connectCoinbase(s.pair, ex.id);
      if (ex.id === 'kraken') connectKraken(s.pair, ex.id);
      if (ex.id === 'okx') connectOkx(s.pair, ex.id);
    });
  }

  function bind() {
    window.MultiExchangeOrderFlow = {
      getTotals: () => EXCHANGES.reduce((out, ex) => {
        const s = state(ex.id);
        // Recompute from the live window, not from the last render, so the
        // header chips always match what the six tapes show right now.
        recalculate(s);
        out.buy += s.buy; out.sell += s.sell;
        return out;
      }, { buy: 0, sell: 0 }),
      // Same numbers, one entry per exchange, so the BUY OPEN / SELL OPEN
      // chips can show exactly which terminal added what.
      getBreakdown: () => EXCHANGES.reduce((out, ex) => {
        const s = state(ex.id);
        recalculate(s);
        out.push({ id: ex.id, name: ex.name, buy: s.buy, sell: s.sell });
        return out;
      }, [])
    };
    window.addEventListener('chart:market', e => switchMarket(e.detail && e.detail.symbol));
    const first = $('tt-sidebar-connected-symbol') || $('tt-connected-symbol');
    switchMarket(first ? first.textContent : '');
    startPolling();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
