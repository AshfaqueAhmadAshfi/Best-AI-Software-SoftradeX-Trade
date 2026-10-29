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
    { id: 'bybit', name: 'BYBIT', pairs: { BTC: 'BTCUSDT', ETH: 'ETHUSDT', SOL: 'SOLUSDT', XAU: 'XAUTUSDT', GOLD: 'XAUTUSDT' } },
    // These six publish a keyless trade API without CORS headers, so their
    // public tapes are normalised by /api/orderflow/trades on our backend.
    { id: 'kucoin', name: 'KUCOIN', proxy: true, pairs: { BTC: 'BTC-USDT', ETH: 'ETH-USDT', SOL: 'SOL-USDT', XAU: 'PAXG-USDT', GOLD: 'PAXG-USDT' } },
    { id: 'bitfinex', name: 'BITFINEX', proxy: true, pairs: { BTC: 'BTCUSD', ETH: 'ETHUSD', SOL: 'SOLUSD', XAU: 'XAUT:USD', GOLD: 'XAUT:USD' } },
    { id: 'mexc', name: 'MEXC', proxy: true, pairs: { BTC: 'BTCUSDT', ETH: 'ETHUSDT', SOL: 'SOLUSDT' } },
    { id: 'gemini', name: 'GEMINI', proxy: true, pairs: { BTC: 'btcusd', ETH: 'ethusd', SOL: 'solusd', XAU: 'paxgusd', GOLD: 'paxgusd' } },
    { id: 'deribit', name: 'DERIBIT', proxy: true, pairs: { BTC: 'BTC-PERPETUAL', ETH: 'ETH-PERPETUAL', SOL: 'SOL_USDC-PERPETUAL' } },
    { id: 'htx', name: 'HTX', proxy: true, pairs: { BTC: 'btcusdt', ETH: 'ethusdt', SOL: 'solusdt', XAU: 'xautusdt', GOLD: 'xautusdt' } }
  ];
  // Binance and the Traders Order Flow terminal live on the same board as the
  // 12 public tapes. They are not exchange entries (no pair / proxy), so they
  // are appended as explicit sources instead of being folded into EXCHANGES.
  const BOARD_SOURCES = EXCHANGES.concat([
    { id: 'binance', name: 'BINANCE', extra: true },
    { id: 'traders', name: 'TRADERS ORDER FLOW', extra: true }
  ]);
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

  // ------------------------------------------------------------------
  // Combined board: every venue keeps its own BUY / SELL totals, then the
  // Traders terminal's running open-position amounts are added to the
  // grand total that feeds the BUY OPEN / SELL OPEN chips.
  // ------------------------------------------------------------------
  let boardRefs = null;

  function venueBreakdown() {
    return EXCHANGES.reduce((out, ex) => {
      const s = state(ex.id);
      recalculate(s);
      out.push({ id: ex.id, name: ex.name, buy: s.buy, sell: s.sell,
                 pair: s.pair || '' });
      return out;
    }, []);
  }

  function venueTotals() {
    return venueBreakdown().reduce((out, venue) => {
      out.buy += venue.buy;
      out.sell += venue.sell;
      return out;
    }, { buy: 0, sell: 0 });
  }

  function binanceVolumes() {
    const bridge = window.BinanceOrderFlowBridge;
    if (!bridge || typeof bridge.getTotals !== 'function') {
      return { buy: 0, sell: 0, pair: '' };
    }
    try {
      const volume = bridge.getTotals() || {};
      return {
        buy: Number(volume.buy) || 0,
        sell: Number(volume.sell) || 0,
        pair: String(volume.symbol || '')
      };
    } catch (e) {
      return { buy: 0, sell: 0, pair: '' };
    }
  }

  function traderVolumes() {
    const terminal = window.TradersTerminal;
    if (!terminal || typeof terminal.getOpenVolume !== 'function') {
      return { buy: 0, sell: 0, count: 0 };
    }
    try {
      const volume = terminal.getOpenVolume() || {};
      return {
        buy: Number(volume.openBuyVol) || 0,
        sell: Number(volume.openSellVol) || 0,
        count: (Number(volume.openBuyCount) || 0) + (Number(volume.openSellCount) || 0)
      };
    } catch (e) {
      return { buy: 0, sell: 0, count: 0 };
    }
  }

  // Every source that contributes to the board: the 12 public tapes, Binance's
  // own aggregate-trade flow, then the Traders terminal's running positions.
  function sourceBreakdown() {
    const rows = venueBreakdown();
    const binance = binanceVolumes();
    rows.push({
      id: 'binance', name: 'BINANCE', buy: binance.buy, sell: binance.sell,
      pair: binance.pair, extra: true
    });
    const traders = traderVolumes();
    rows.push({
      id: 'traders', name: 'TRADERS ORDER FLOW', buy: traders.buy, sell: traders.sell,
      pair: 'POSITIONS', extra: true, count: traders.count
    });
    return rows;
  }

  function openTotals() {
    const breakdown = sourceBreakdown();
    const totals = breakdown.reduce((out, source) => {
      out.buy += source.buy;
      out.sell += source.sell;
      return out;
    }, { buy: 0, sell: 0 });
    const venues = breakdown.filter(source => !source.extra).reduce((out, venue) => {
      out.buy += venue.buy;
      out.sell += venue.sell;
      return out;
    }, { buy: 0, sell: 0 });
    const binance = breakdown.find(source => source.id === 'binance') || { buy: 0, sell: 0 };
    const traders = breakdown.find(source => source.id === 'traders') ||
      { buy: 0, sell: 0, count: 0 };
    return {
      buy: totals.buy,
      sell: totals.sell,
      venueBuy: venues.buy,
      venueSell: venues.sell,
      binanceBuy: binance.buy || 0,
      binanceSell: binance.sell || 0,
      tradersBuy: traders.buy || 0,
      tradersSell: traders.sell || 0,
      tradersCount: traders.count || 0,
      breakdown
    };
  }

  function boardUi() {
    if (boardRefs) return boardRefs;
    const root = $('venue-flow-board');
    const list = $('vfb-list');
    if (!root || !list) return null;
    boardRefs = {
      root, list,
      market: $('vfb-market'),
      buy: $('vfb-buy'), sell: $('vfb-sell'),
      buyPct: $('vfb-buy-pct'), sellPct: $('vfb-sell-pct'),
      barBuy: $('vfb-bar-buy'), barSell: $('vfb-bar-sell'),
      venueBuy: $('vfb-venue-buy'), venueSell: $('vfb-venue-sell'),
      binanceBuy: $('vfb-binance-buy'), binanceSell: $('vfb-binance-sell'),
      posBuy: $('vfb-pos-buy'), posSell: $('vfb-pos-sell'),
      updated: $('vfb-updated'),
      rows: Object.create(null)
    };
    if (list.dataset.ready !== '1') {
      list.innerHTML = BOARD_SOURCES.map(source => `
        <div class="vfb-venue${source.extra ? ' extra' : ''}" data-vfb="${source.id}">
          <div class="vfb-venue-top"><b>${source.name}</b><em class="vfb-venue-pct">0%</em></div>
          <div class="vfb-venue-vals"><span class="buy">B <b>$0.00</b></span><span class="sell">S <b>$0.00</b></span></div>
          <div class="vfb-venue-bar"><i class="buy"></i><i class="sell"></i></div>
        </div>`).join('');
      list.dataset.ready = '1';
      BOARD_SOURCES.forEach(source => {
        const row = list.querySelector(`[data-vfb="${source.id}"]`);
        if (!row) return;
        boardRefs.rows[source.id] = {
          root: row,
          pct: row.querySelector('.vfb-venue-pct'),
          buy: row.querySelector('.vfb-venue-vals .buy b'),
          sell: row.querySelector('.vfb-venue-vals .sell b'),
          barBuy: row.querySelector('.vfb-venue-bar .buy'),
          barSell: row.querySelector('.vfb-venue-bar .sell')
        };
      });
    }
    return boardRefs;
  }

  function updateBoard() {
    const ui = boardUi();
    if (!ui) return;
    const totals = openTotals();
    const grand = totals.buy + totals.sell;
    const buyPct = grand ? Math.round(totals.buy / grand * 100) : 0;
    const sellPct = grand ? 100 - buyPct : 0;

    set('vfb-market', selectedSymbol || '—');
    set('vfb-buy', compact(totals.buy));
    set('vfb-sell', compact(totals.sell));
    set('vfb-buy-pct', buyPct + '%');
    set('vfb-sell-pct', sellPct + '%');
    set('vfb-venue-buy', compact(totals.venueBuy));
    set('vfb-venue-sell', compact(totals.venueSell));
    set('vfb-binance-buy', compact(totals.binanceBuy));
    set('vfb-binance-sell', compact(totals.binanceSell));
    set('vfb-pos-buy', compact(totals.tradersBuy));
    set('vfb-pos-sell', compact(totals.tradersSell));
    if (ui.barBuy) ui.barBuy.style.width = buyPct + '%';
    if (ui.barSell) ui.barSell.style.width = sellPct + '%';

    totals.breakdown.forEach(entry => {
      const row = ui.rows[entry.id];
      if (!row) return;
      const total = entry.buy + entry.sell;
      const pct = total ? Math.round(entry.buy / total * 100) : 0;
      const live = entry.id === 'traders' ? true : !!entry.pair;
      row.root.classList.toggle('off', !live);
      if (row.pct) {
        if (entry.id === 'traders') row.pct.textContent = entry.count ? entry.count + ' POS' : 'NO POS';
        else if (!live) row.pct.textContent = entry.id === 'binance' ? 'WAITING' : 'NO PAIR';
        else row.pct.textContent = pct + '% BUY';
      }
      if (row.buy) row.buy.textContent = compact(entry.buy);
      if (row.sell) row.sell.textContent = compact(entry.sell);
      if (row.barBuy) row.barBuy.style.width = pct + '%';
      if (row.barSell) row.barSell.style.width = (total ? 100 - pct : 0) + '%';
    });
    if (ui.updated) ui.updated.textContent = 'UPDATED ' + new Date().toLocaleTimeString();
  }

  function render(id, withTape) {
    if (withTape === undefined) withTape = true;
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
    // A venue can be perfectly healthy and still have no print in the last
    // 45 seconds (Gemini on a quiet Saturday, for example). Say QUIET instead
    // of pretending the socket is still connecting.
    set(`mof-${id}-status`, !s.pair ? 'NO PAIR'
      : (live ? (s.trades.length ? 'CONNECTED' : 'QUIET') : 'CONNECTING'));
    const total = s.buy + s.sell;
    set(`mof-${id}-buy-pct`, total ? Math.round(s.buy / total * 100) + '%' : '0%');
    set(`mof-${id}-sell-pct`, total ? Math.round(s.sell / total * 100) + '%' : '0%');
    const tape = $(`mof-${id}-tape`);
    if (tape && withTape) {
      // Rebuilding twelve 60-row tapes once a second was enough to block the
      // main thread. Summary chips still tick every second; the DOM only
      // changes when a new print actually arrives or an old one ages out.
      const first = s.trades[0];
      const last = s.trades[s.trades.length - 1];
      const key = s.trades.length + '|' + (first ? first.id || first.ts : '') +
        '|' + (last ? last.ts : '');
      if (key !== s.tapeKey) {
        s.tapeKey = key;
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
  }

  // A REST poll can deliver dozens of prints at once. Rendering the whole
  // tape for every print made twelve terminals fight the main thread, so a
  // burst is coalesced into one paint.
  const renderTimers = Object.create(null);
  function scheduleRender(id) {
    if (renderTimers[id]) return;
    renderTimers[id] = setTimeout(() => {
      delete renderTimers[id];
      render(id);
      const bridge = window.BinanceOrderFlowBridge;
      if (bridge && typeof bridge.refresh === 'function') bridge.refresh();
    }, 40);
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
    scheduleRender(id);
  }

  async function pollTrades(ex, pair) {
    const s = state(ex.id);
    if (ex.proxy) {
      try {
        const market = selectedSymbol || 'BTC';
        const url = `/api/orderflow/trades?venue=${encodeURIComponent(ex.id)}` +
          `&market=${encodeURIComponent(market)}&limit=80`;
        const response = await fetch(url, { cache: 'no-store' });
        if (!response.ok) return;
        const data = await response.json();
        s.lastPollAt = Date.now();
        (Array.isArray(data.trades) ? data.trades : []).forEach(tr =>
          addTrade(ex.id, tr.side, tr.price, tr.qty, tr.id, tr.ts));
        render(ex.id);
      } catch (e) {}
      return;
    }
    const urls = {
      coinbase: `https://api.exchange.coinbase.com/products/${encodeURIComponent(pair)}/trades`,
      kraken: `https://api.kraken.com/0/public/Trades?pair=${encodeURIComponent(pair.replace('/', ''))}`,
      okx: `https://www.okx.com/api/v5/market/trades?instId=${encodeURIComponent(pair)}&limit=100`,
      bitget: `https://api.bitget.com/api/v2/spot/market/fills?symbol=${encodeURIComponent(pair)}&limit=100`,
      gate: `https://api.gateio.ws/api/v4/spot/trades?currency_pair=${encodeURIComponent(pair)}&limit=100`,
      bybit: `https://api.bybit.com/v5/market/recent-trade?category=spot&symbol=${encodeURIComponent(pair)}&limit=60`
    };
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
          // Once per poll is enough to drop prints that aged out of the live
          // window; the key guard inside render() keeps an unchanged tape
          // from being rebuilt.
          render(ex.id, true);
        }
      });
    }, 3000);
    setInterval(() => {
      EXCHANGES.forEach(ex => render(ex.id, false));
      // Trades age out of the 45s window without a new print; refresh the
      // combined board and BUY OPEN / SELL OPEN chips so they fall with the
      // tapes and with the Traders terminal's running positions.
      updateBoard();
      const bridge = window.BinanceOrderFlowBridge;
      if (bridge && typeof bridge.refresh === 'function') bridge.refresh();
    }, 1000);
    // The chips refresh every 500ms; keep the combined board on the same
    // cadence so the venue + position totals never look out of step.
    setInterval(updateBoard, 500);
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
      s.tapeKey = '';
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
    updateBoard();
  }

  function bind() {
    window.MultiExchangeOrderFlow = {
      // Venue-only totals (12 public tapes).
      getTotals: venueTotals,
      // One entry per contributing source (12 venues + Binance + Traders), so
      // the board and the chip tooltip can show exactly where each amount came
      // from.
      getBreakdown: sourceBreakdown,
      // Every source above. This is the number that feeds BUY OPEN / SELL
      // OPEN, and it includes Binance and the Traders running positions once.
      getOpenTotals: openTotals
    };
    window.addEventListener('chart:market', e => switchMarket(e.detail && e.detail.symbol));
    const first = $('tt-sidebar-connected-symbol') || $('tt-connected-symbol');
    switchMarket(first ? first.textContent : '');
    updateBoard();
    startPolling();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
