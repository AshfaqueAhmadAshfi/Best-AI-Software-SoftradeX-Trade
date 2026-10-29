/**
 * Big Player Order Flow Terminal
 * ------------------------------------------------------------------
 * Sits at the very bottom of the dashboard and reads the SAME live Exness
 * MetaTrader 5 tick feed that powers the chart. It highlights unusually large
 * price impulses ("big prints"), the side they hit, and whether the market
 * actually followed in the next 6 seconds.
 *
 * Nothing here is simulated: rows come from real MT5 ticks / real 1m bars.
 * Burst size is real tick participation, and follow-through is measured on
 * later ticks, not invented.
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var section = null;
  var symbol = '';
  var meta = null;
  var digits = 2;
  var offsetSec = 0;
  var lastPrice = 0;
  var priceWindow = [];          // { t, p } over the last 5 minutes
  var ws = null;
  var wsTimer = null;
  var wsRetry = 0;
  var wsFails = 0;
  var lastFrameAt = 0;
  var errRetryTimer = null;
  var paused = false;
  var buffered = [];
  var pendingRows = {};
  var buyCount = 0;
  var sellCount = 0;
  var followed = 0;
  var measured = 0;
  var rowCount = 0;
  var MAX_ROWS = 60;
  var lastLiveAt = 0;

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function fmt(n, dec) {
    var v = Number(n);
    if (!isFinite(v)) return '--';
    var d = typeof dec === 'number' ? dec : digits;
    return v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  function fmtSigned(n, dec) {
    var v = Number(n) || 0;
    return (v > 0 ? '+' : v < 0 ? '−' : '±') + fmt(Math.abs(v), dec);
  }

  function fmtTime(ms) {
    var d = new Date(Number(ms) - offsetSec * 1000);
    var hh = String(d.getHours()).padStart(2, '0');
    var mm = String(d.getMinutes()).padStart(2, '0');
    var ss = String(d.getSeconds()).padStart(2, '0');
    return hh + ':' + mm + ':' + ss;
  }

  function pulse(state) {
    var el = $('wf-pulse');
    if (!el) return;
    el.className = 'wf-pulse' + (state ? ' ' + state : '');
    el.title = state === 'online'
      ? 'Live Exness MT5 tick stream'
      : state === 'error'
        ? 'Exness feed error'
        : 'Waiting for the Exness tick stream…';
  }

  function status(text, kind) {
    var el = $('wf-status');
    if (el) el.textContent = text;
    if (kind === 'online') pulse('online');
    else if (kind === 'error') pulse('error');
    else pulse('');
  }

  function setMarketHeader() {
    var m = meta || {};
    var nameEl = $('wf-market');
    if (nameEl) {
      nameEl.textContent = (m.label || symbol || '—') + (m.pair ? ' / ' + m.pair : '');
    }
    var subEl = $('wf-market-sub');
    if (subEl) {
      subEl.textContent = symbol
        ? symbol + (m.description ? ' · ' + m.description : '') + ' · 6s follow'
        : 'waiting for the chart…';
    }
  }

  function metric(id, text, subId, subText) {
    var el = $(id);
    if (el) el.textContent = text;
    if (subId) {
      var s = $(subId);
      if (s) s.textContent = subText;
    }
  }

  function updateStats() {
    var total = buyCount + sellCount;
    var buyPct = total ? Math.round((buyCount / total) * 100) : 0;
    var sellPct = total ? 100 - buyPct : 0;
    metric('wf-buy', String(buyCount), 'wf-buy-pct', buyPct + '%');
    metric('wf-sell', String(sellCount), 'wf-sell-pct', sellPct + '%');

    var delta = buyCount - sellCount;
    var bias = 'BALANCED';
    if (total >= 3) {
      if (delta >= Math.max(2, total * 0.18)) bias = 'BUYERS IN CONTROL';
      else if (delta <= -Math.max(2, total * 0.18)) bias = 'SELLERS IN CONTROL';
    }
    metric('wf-delta', (delta > 0 ? '+' : '') + delta, 'wf-bias', bias);

    if (measured > 0) {
      var rate = Math.round((followed / measured) * 100);
      metric('wf-follow', rate + '%', 'wf-follow-sub', followed + ' of ' + measured + ' followed');
    } else {
      metric('wf-follow', '—', 'wf-follow-sub', 'measuring…');
    }

    var buyBar = $('wf-meter-buy');
    var sellBar = $('wf-meter-sell');
    if (buyBar) buyBar.style.width = buyPct + '%';
    if (sellBar) sellBar.style.width = sellPct + '%';
  }

  function updateMarketMove() {
    var now = Date.now();
    while (priceWindow.length && now - priceWindow[0].t > 300000) priceWindow.shift();
    if (!priceWindow.length || !lastPrice) {
      metric('wf-move', '—', 'wf-move-sub', 'no live ticks yet');
      return;
    }
    var first = priceWindow[0].p;
    var delta = lastPrice - first;
    var pct = first ? (delta / first) * 100 : 0;
    metric('wf-move', fmtSigned(delta), 'wf-move-sub',
      (delta >= 0 ? '▲ ' : '▼ ') + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '% · 5 min');
    var el = $('wf-move');
    if (el) el.style.color = delta > 0 ? '#0ECB81' : delta < 0 ? '#F6465D' : '#e2e8f0';
  }

  function followCell(row) {
    var fv = Number(row.fv);
    if (!isFinite(fv)) return '<span class="wf-follow pending">measuring…</span>';
    var cls = fv > 0 ? 'up' : fv < 0 ? 'down' : 'flat';
    var word = cls === 'up' ? '▲ FOLLOWED ' : cls === 'down' ? '▼ FADED ' : '• HELD ';
    return '<span class="wf-follow ' + cls + '">' + word + fmtSigned(fv) + '</span>';
  }

  function buildRow(row) {
    var side = String(row.side || '').toUpperCase() === 'SELL' ? 'SELL' : 'BUY';
    var isBuy = side === 'BUY';
    var price = Number(row.px) || 0;
    var ticks = Number(row.ticks) || 0;
    var mv = Number(row.mv) || 0;
    var live = String(row.src || '') === 'live';
    var el = document.createElement('div');
    el.className = 'wf-row fresh' + (live ? ' whale' : '');
    el.dataset.id = String(row.id || '');
    el.innerHTML =
      '<span class="wf-time">' + esc(fmtTime(row.tm)) + '</span>' +
      '<span><span class="wf-side ' + (isBuy ? 'buy' : 'sell') + '">' +
        (isBuy ? '▲ BUY' : '▼ SELL') + '</span></span>' +
      '<span class="wf-price ' + (isBuy ? 'buy' : 'sell') + '">' + fmt(price) + '</span>' +
      '<span class="wf-burst">' + (ticks ? '<b>' + ticks + '</b> tick' + (ticks === 1 ? '' : 's') : '—') + '</span>' +
      '<span class="wf-move ' + (isBuy ? 'up' : 'down') + '">' +
        (isBuy ? '▲ ' : '▼ ') + fmt(mv) + '</span>' +
      '<span class="wf-follow-slot">' +
        (live ? '<span class="wf-follow pending">measuring 6s…</span>' : followCell(row)) +
      '</span>' +
      '<span class="wf-src' + (live ? ' live' : '') + '">' + (live ? 'TICK' : '1M') + '</span>';
    return el;
  }

  function removeEmpty() {
    var empty = $('wf-empty');
    if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
  }

  function emptyTape(message) {
    var tape = $('wf-tape');
    if (!tape) return;
    tape.innerHTML = '<div class="wf-empty" id="wf-empty">' + esc(message) + '</div>';
    pendingRows = {};
    rowCount = 0;
  }

  function addRow(row, prepend) {
    if (!row) return;
    var live = String(row.src || '') === 'live';
    var side = String(row.side || '').toUpperCase() === 'SELL' ? 'SELL' : 'BUY';
    if (side === 'BUY') buyCount++; else sellCount++;
    updateStats();

    if (paused) {
      buffered.push(row);
      if (buffered.length > MAX_ROWS) buffered.shift();
      return;
    }
    renderRow(row, prepend !== false);
  }

  function renderRow(row, prepend) {
    var tape = $('wf-tape');
    if (!tape) return;
    removeEmpty();
    var el = buildRow(row);
    if (prepend) tape.insertBefore(el, tape.firstChild);
    else tape.appendChild(el);
    rowCount++;
    // lastElementChild guarantees the loop always makes progress, even if a
    // text node sits at the end of the tape.
    while (tape.children.length > MAX_ROWS && tape.lastElementChild) {
      tape.removeChild(tape.lastElementChild);
    }
    if (String(row.src || '') === 'live' && row.id) pendingRows[String(row.id)] = el;
  }

  function applyFollow(msg) {
    var id = String(msg.id || '');
    var el = pendingRows[id];
    var st = String(msg.st || 'FLAT');
    var mv = Number(msg.mv) || 0;
    measured++;
    if (st === 'UP') followed++;
    updateStats();
    if (!el) return;
    var slot = el.querySelector('.wf-follow-slot');
    if (!slot) return;
    var word = st === 'UP' ? '▲ FOLLOWED ' : st === 'DOWN' ? '▼ FADED ' : '• HELD ';
    slot.innerHTML = '<span class="wf-follow ' + (st === 'UP' ? 'up' : st === 'DOWN' ? 'down' : 'flat') +
      '">' + word + fmtSigned(mv) + '</span>';
    delete pendingRows[id];
  }

  // ------------------------------------------------------------- transport
  function connect() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
    if (wsTimer) { clearTimeout(wsTimer); wsTimer = null; }
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var socket;
    try {
      socket = new WebSocket(proto + '//' + location.host + '/ws/chart');
    } catch (e) {
      wsTimer = setTimeout(connect, 1500);
      return;
    }
    ws = socket;
    socket.onopen = function () {
      wsRetry = 0;
      wsFails = 0;
      lastFrameAt = Date.now();
      pulse('online');
      if (symbol) socket.send(JSON.stringify({ type: 'sub', symbol: symbol, tf: '1m' }));
    };
    socket.onmessage = function (event) {
      lastFrameAt = Date.now();
      var msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (!msg || !msg.t) return;
      if (msg.t === 'snap') {
        if (msg.s !== symbol) return;
        applySnapshot(msg);
      } else if (msg.t === 'tick') {
        if (msg.s !== symbol) return;
        applyTick(msg);
      } else if (msg.t === 'print') {
        if (msg.s !== symbol) return;
        applyTick({ b: msg.px, tm: msg.tm });
        addRow(msg, true);
        lastLiveAt = Date.now();
        status('LIVE · Exness MT5 · big prints streaming', 'online');
      } else if (msg.t === 'follow') {
        if (msg.s !== symbol) return;
        applyFollow(msg);
      } else if (msg.t === 'hb') {
        // heartbeat only - onmessage already refreshed lastFrameAt
      } else if (msg.t === 'error') {
        if (msg.s && msg.s !== symbol) return;
        status('Exness feed error: ' + (msg.message || 'unknown'), 'error');
        // retry the subscription so a briefly-offline terminal recovers
        if (errRetryTimer) clearTimeout(errRetryTimer);
        errRetryTimer = setTimeout(function () {
          errRetryTimer = null;
          if (ws && ws.readyState === WebSocket.OPEN && symbol) {
            ws.send(JSON.stringify({ type: 'sub', symbol: symbol, tf: '1m' }));
          }
        }, 3000);
      }
    };
    socket.onerror = function () {
      try { socket.close(); } catch (e) {}
    };
    socket.onclose = function () {
      if (ws === socket) ws = null;
      pulse('');
      if (!symbol) return;
      wsFails += 1;
      // Hosted hosting (cPanel / Passenger) has no WebSocket at all: stop
      // hammering a 404 every few seconds and say so instead of pretending.
      if (wsFails >= 2) {
        status('HTTP hosting · live tape needs the WebSocket stream', '');
        return;
      }
      wsRetry = Math.min(wsRetry + 1, 8);
      wsTimer = setTimeout(connect, Math.min(500 * wsRetry, 5000));
    };
  }

  function applySnapshot(msg) {
    meta = msg.meta || meta;
    if (meta && meta.digits !== undefined) digits = Number(meta.digits) || digits;
    offsetSec = Number(msg.offset) || 0;
    setMarketHeader();
    status('LIVE · Exness MT5 · tick-burst detector running', 'online');
    var ticker = msg.ticker || {};
    if (ticker.price) {
      lastPrice = Number(ticker.price) || lastPrice;
      priceWindow.push({ t: Date.now(), p: lastPrice });
    }
  }

  function applyTick(msg) {
    var p = Number(msg.b) || 0;
    if (!p) return;
    lastPrice = p;
    var now = Date.now();
    priceWindow.push({ t: now, p: p });
    if (priceWindow.length > 1200) priceWindow.shift();
    lastLiveAt = now;
    status('LIVE · Exness MT5 · big-player tape streaming', 'online');
  }

  // --------------------------------------------------------------- market
  function resolve(sym) {
    var term = window.SoftradeXTerminal;
    if (term && typeof term.resolveSymbol === 'function') {
      var key = term.resolveSymbol(sym);
      if (key) return key;
    }
    return String(sym || '').trim();
  }

  function follow(sym) {
    var key = resolve(sym);
    if (!key) return;
    if (key === symbol && ws && ws.readyState === WebSocket.OPEN) return;
    symbol = key;
    meta = (window.SoftradeXTerminal && window.SoftradeXTerminal.MARKETS)
      ? window.SoftradeXTerminal.MARKETS[key] || null
      : null;
    digits = meta && meta.digits !== undefined ? Number(meta.digits) : 2;
    buyCount = sellCount = followed = measured = 0;
    priceWindow = [];
    buffered = [];
    pendingRows = {};
    updateStats();
    updateMarketMove();
    setMarketHeader();
    emptyTape('Connecting to ' + symbol + ' · waiting for the first big prints…');
    status('Subscribing to ' + symbol + ' on Exness…', '');

    fetch('/api/chart/tape?symbol=' + encodeURIComponent(key) + '&limit=40', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || symbol !== key) return;
        if (data.meta) {
          meta = data.meta;
          digits = Number(meta.digits) || digits;
          setMarketHeader();
        }
        var rows = data.prints || [];
        rows.forEach(function (row) { addRow(row, false); });
        if (!rows.length) {
          emptyTape('Listening to ' + symbol + ' · the first unusual burst will appear here.');
        } else {
          status('LIVE · Exness MT5 · ' + rows.length + ' recent unusual 1m prints loaded', 'online');
        }
      })
      .catch(function () {
        emptyTape('Tape history unavailable · live prints will still stream in.');
      });

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'sub', symbol: symbol, tf: '1m' }));
    } else {
      connect();
    }
  }

  function resume() {
    paused = false;
    var btn = $('wf-pause');
    if (btn) {
      btn.textContent = '⏸ Pause';
      btn.classList.remove('paused');
      btn.title = 'Pause the live tape';
    }
    var rows = buffered.slice().reverse();
    buffered = [];
    rows.forEach(function (row) { renderRow(row, true); });
  }

  function init() {
    section = $('whale-flow-section');
    if (!section) return;

    var pauseBtn = $('wf-pause');
    if (pauseBtn) {
      pauseBtn.addEventListener('click', function () {
        if (paused) {
          resume();
        } else {
          paused = true;
          pauseBtn.textContent = '▶ Resume';
          pauseBtn.classList.add('paused');
          pauseBtn.title = 'Resume the live tape';
        }
      });
    }

    var clearBtn = $('wf-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        buyCount = sellCount = followed = measured = 0;
        buffered = [];
        pendingRows = {};
        updateStats();
        emptyTape('Tape cleared · listening for the next big print…');
      });
    }

    window.addEventListener('chart:market', function (ev) {
      var detail = (ev && ev.detail) || {};
      if (detail.symbol) follow(detail.symbol);
    });
    window.addEventListener('chart:snapshot', function (ev) {
      var detail = (ev && ev.detail) || {};
      if (detail.symbol && detail.symbol !== symbol) follow(detail.symbol);
    });

    var term = window.SoftradeXTerminal;
    var current = term && typeof term.getCurrentSymbol === 'function' ? term.getCurrentSymbol() : '';
    if (current) {
      follow(current);
    } else {
      fetch('/api/chart/symbols', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || !data.available || !data.symbols || !data.symbols.length) {
            status((data && data.message) || 'Exness terminal is not connected', 'error');
            return;
          }
          var pick = data.symbols[0].symbol;
          data.symbols.forEach(function (m) {
            if (m.symbol === 'XAUUSDm') pick = m.symbol;
          });
          follow(pick);
        })
        .catch(function (err) {
          status('Exness feed unavailable: ' + (err && err.message ? err.message : err), 'error');
        });
    }

    setInterval(updateMarketMove, 2000);
    setInterval(function () {
      if (paused) return;
      if (!lastLiveAt) return;
      if (Date.now() - lastLiveAt > 10000 && ws && ws.readyState === WebSocket.OPEN) {
        status('LIVE · waiting for the next Exness tick…', 'online');
      }
    }, 5000);
    // Stream watchdog: the server heartbeat arrives even on a quiet market, so
    // a socket with no frames at all is half-dead and must be reconnected.
    setInterval(function () {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (lastFrameAt && Date.now() - lastFrameAt > 15000) {
        try { ws.close(); } catch (e) {}
      }
    }, 5000);

    updateStats();
    updateMarketMove();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    setTimeout(init, 260);
  }
})();
