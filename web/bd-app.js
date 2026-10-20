/* =========================================================================
   BD Stock Market Dashboard controller (data-only board, DSE simulated feed)

   Shares the SAME chart terminal + Traders Order Flow Terminal as the Exness
   dashboard (softradex-terminal.js + traders-terminal.js) but drives them
   from the BD DSE feed:
     /api/chart/symbols?market=bd   -> BD catalogue for the shared terminal
     /api/bd/watch                  -> 1 Hz buyer/seller frame (light)
     /api/bd/state                  -> hero / AI / outlook / sessions / setups
   The Exness dashboard (index.html + app.js) is never touched.
   ========================================================================= */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const S = window.S = {
    selected: 'DSEX',     // selected BD market (every stage follows it)
    watch: {},            // live watch rows by symbol (1 Hz frame)
    market_watch: [],     // trimmed rows -> TradersTerminal livePriceForSymbol
    bd: {},               // last /api/bd/state payload
    MARKETS: {},          // BD catalogue by symbol
    ORDER: []             // catalogue display order
  };

  const esc = (v) => String(v == null ? '' : v)
    .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (v, d) => Number(v == null || isNaN(v) ? 0 : v).toFixed(d == null ? 2 : d);
  const pct = (v) => (v >= 0 ? '+' : '') + Number(v || 0).toFixed(2) + '%';
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const sideColor = (v) => (v === 'BUY' ? '#0ECB81' : (v === 'SELL' ? '#F6465D' : '#94a3b8'));
  const hhmmss = (t) => new Date((t || 0) * 1000).toLocaleTimeString('en-GB', { hour12: false });
  const nowHMS = () => new Date().toLocaleTimeString('en-GB', { hour12: false });

  function dhakaClock(d) {
    try {
      return new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Dhaka', hour12: false,
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      }).format(d || new Date());
    } catch (e) { return nowHMS(); }
  }

  function fmtCountdown(sec) {
    sec = clamp(Math.floor(sec || 0), 0, 9 * 3600);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  async function getJson(url, ms) {
    const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const t = ctl ? setTimeout(() => ctl.abort(), ms || 9000) : null;
    try {
      const r = await fetch(url, { cache: 'no-store', signal: ctl ? ctl.signal : undefined });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } finally { if (t) clearTimeout(t); }
  }

  /* ------------------------------------------------------------------ *
   * MARKET 01 — catalog, chips, market switching                        *
   * ------------------------------------------------------------------ */

  async function loadCatalog() {
    try {
      const data = await getJson('/api/chart/symbols?market=bd');
      const rows = (data && data.symbols) || [];
      if (!rows.length) throw new Error('no BD symbols');
      rows.forEach((r) => {
        if (!r || !r.symbol) return;
        S.MARKETS[r.symbol] = {
          symbol: r.symbol, label: r.label || r.symbol, pair: r.pair || 'BDT',
          icon: r.icon || '•', digits: r.digits == null ? 2 : r.digits,
          kind: r.kind || 'stock', description: r.description || ''
        };
        if (S.ORDER.indexOf(r.symbol) < 0) S.ORDER.push(r.symbol);
      });
      if (!S.MARKETS[S.selected]) S.selected = S.ORDER[0] || 'DSEX';
      // Traders Order Flow Terminal (BD page): publish the DSE company/bank
      // roster so the tape's TRADER / ROUTE column only ever names BD-listed
      // companies and banks - never foreign LPs / brokers.
      window.BD_COMPANIES = S.ORDER
        .map((sym) => S.MARKETS[sym])
        .filter((m) => m && m.kind === 'stock')
        .map((m) => ({ symbol: m.symbol, name: m.description || m.label }));
      renderChips();
      ['sessions-market', 'ai-market'].forEach((id) => {
        const el = $(id);
        if (!el) return;
        el.innerHTML = S.ORDER.map((sym) =>
          '<option value="' + esc(sym) + '">' + esc(((S.MARKETS[sym] || {}).icon || '') + ' ' + (S.MARKETS[sym] || {}).symbol) + '</option>').join('');
        if (el.contains(el.querySelector('option[value="' + S.selected + '"]'))) el.value = S.selected;
      });
      forceSyncTerminals();
      pollState();
    } catch (err) {
      const chips = $('flow-market-chips');
      if (chips) chips.innerHTML = '<span style="color:#F6465D;font-size:12px;">BD catalogue unavailable: ' + esc(err.message || err) + '</span>';
      setTimeout(loadCatalog, 5000);
    }
  }

  function forceSyncTerminals() {
    const term = window.SoftradeXTerminal || window.BinanceTerminal;
    if (term && typeof term.switchMarket === 'function') {
      try { term.switchMarket(S.selected); } catch (e) { /* markets not ready yet */ }
    }
    if (window.TradersTerminal && typeof window.TradersTerminal.switchMarket === 'function') {
      try { window.TradersTerminal.switchMarket(S.selected, true); } catch (e) {}
    }
  }

  function selectMarket(sym) {
    const mk = S.MARKETS[sym];
    if (!mk) return;
    S.selected = mk.symbol;
    renderChips();
    forceSyncTerminals();
    ['sessions-market', 'ai-market'].forEach((id) => {
      const el = $(id);
      if (el && el.value !== S.selected) el.value = S.selected;
    });
    const w = S.watch[S.selected];
    if (w && window.TradersTerminal && typeof window.TradersTerminal.onPriceTick === 'function') {
      window.TradersTerminal.onPriceTick(w.symbol, ((Number(w.bid) + Number(w.ask)) / 2), Number(w.bid), Number(w.ask));
    }
    pollState();
  }

  function renderChips() {
    const box = $('flow-market-chips');
    if (!box) return;
    const sig = S.selected + '|' + S.ORDER.map((sym) => {
      const w = S.watch[sym];
      return sym + ':' + (w ? w.change_pct : '-');
    }).join(',');
    if (sig === S._chipSig) return;
    S._chipSig = sig;
    box.innerHTML = S.ORDER.map((sym) => {
      const m = S.MARKETS[sym];
      const w = S.watch[sym] || {};
      const up = Number(w.change_pct || 0);
      const cls = up > 0.02 ? 'up' : (up < -0.02 ? 'down' : 'flat');
      return '<button type="button" class="bd-mchip ' + cls + (sym === S.selected ? ' sel' : '') + '" data-sym="' + esc(sym) + '" title="' + esc(m.description) + '">' +
        '<span class="mc-icon">' + esc(m.icon) + '</span><span class="mc-sym">' + esc(m.label) + '</span>' +
        '<span class="mc-chg">' + pct(w.change_pct) + '</span></button>';
    }).join('');
    box.querySelectorAll('.bd-mchip').forEach((b) => {
      b.addEventListener('click', () => selectMarket(b.getAttribute('data-sym')));
    });
  }

  window.flowSymbol = function flowSymbol() {
    return S.selected;
  };

  /* ------------------------------------------------------------------ *
   * 1 Hz watch frame — dials, hero prices, strip, tape feed            *
   * ------------------------------------------------------------------ */

  async function pollWatch() {
    try {
      const data = await getJson('/api/bd/watch', 6000);
      if (!data || !data.watches || !data.watches.length) return;
      const seen = {};
      const trimmed = [];
      data.watches.forEach((w) => {
        if (!w || !w.symbol) return;
        seen[w.symbol] = true;
        S.watch[w.symbol] = w;
        trimmed.push({
          symbol: w.symbol, displayName: w.displayName || w.symbol, ask: w.ask, bid: w.bid,
          spread: w.spread, digits: w.digits, direction: w.direction, change_pct: w.change_pct,
          market_open: w.market_open, market_status: w.market_status
        });
      });
      S.market_watch = trimmed;

      const sel = S.watch[S.selected];
      if (sel && window.TradersTerminal && typeof window.TradersTerminal.onPriceTick === 'function') {
        window.TradersTerminal.onPriceTick(sel.symbol, (Number(sel.bid) + Number(sel.ask)) / 2, Number(sel.bid), Number(sel.ask));
      }

      renderDials();
      if (S._heroSym !== S.selected) updateHeroFromWatch();
      renderChips();       // keeps the mini badges on the chips in sync (1 s)
      renderStrip(data);
      const badge = $('tt-connected-symbol');
      const badge2 = document.querySelector('.tt-connected-symbol');
      if (S.selected) {
        if (badge && badge.textContent !== S.selected.toUpperCase()) badge.textContent = S.selected.toUpperCase();
        if (badge2 && badge2.textContent !== S.selected.toUpperCase()) badge2.textContent = S.selected.toUpperCase();
      }
      if (typeof S.market_watch.length === 'number' && $('fb-world')) {
        $('fb-world').textContent = 'World flow sensors: ' + S.market_watch.length + ' BD markets live · ' + nowHMS();
      }
    } catch (err) { /* keep last frame */ }
  }

  // --- Dial grid ------------------------------------------------------
  const dialNodes = {};
  function dialCard(sym) {
    const m = S.MARKETS[sym] || { symbol: sym, label: sym, icon: '•' };
    const card = document.createElement('div');
    card.className = 'bd-watch-card';
    card.setAttribute('data-sym', sym);
    card.innerHTML =
      '<div class="bdw-top">' +
      '<span class="bdw-sym">' + esc(m.icon) + ' ' + esc(m.label) + ' <small>' + esc(m.symbol) + '</small></span>' +
      '<span class="bdw-chg">0.00%</span></div>' +
      '<div class="bdw-price">—</div>' +
      '<div class="bdw-bar"><span style="width:50%"></span></div>' +
      '<div class="bdw-foot"><span class="bdw-direction">—</span><span class="bdw-status">—</span></div>';
    card.addEventListener('click', () => selectMarket(sym));
    return card;
  }

  function renderDials() {
    const grid = $('market-watch-grid');
    if (!grid) return;
    if (!dialed_order) dialed_order = S.ORDER.slice();
    // grow / shrink to match catalog
    if (grid.dataset.hasDials !== '1') {
      grid.innerHTML = '';
      dialed_order.forEach((sym) => {
        const el = dialCard(sym);
        dialNodes[sym] = el;
        grid.appendChild(el);
      });
      grid.dataset.hasDials = '1';
    }
    dialed_order.forEach((sym) => {
      const el = dialNodes[sym];
      const row = S.watch[sym];
      if (!el || !row) return;
      const w = S.watch[sym];
      const buy = Number(w.buyer_pct || 0), sell = Number(w.seller_pct || 100 - Number(w.buyer_pct || 0));
      const chg = Number(w.change_pct || 0);
      const mid = (Number(w.bid) + Number(w.ask)) / 2;
      const pxEl = el.querySelector('.bdw-price');
      const chgEl = el.querySelector('.bdw-chg');
      const barEl = el.querySelector('.bdw-bar span');
      const dirEl = el.querySelector('.bdw-direction');
      const stEl = el.querySelector('.bdw-status');
      pxEl.textContent = fmt(mid, Number(w.digits == null ? 2 : w.digits));
      pxEl.classList.remove('up', 'down');
      pxEl.classList.add(chg >= 0 ? 'up' : 'down');
      chgEl.textContent = pct(chg);
      chgEl.classList.remove('up', 'down');
      chgEl.classList.add(chg >= 0 ? 'up' : 'down');
      barEl.style.width = clamp(buy, 1, 99).toFixed(1) + '%';
      dirEl.textContent = (w.direction || '—') + ' ' + Math.round(buy) + '/' + Math.round(sell);
      stEl.textContent = w.market_status || '';
      if (w.market_open) stEl.classList.add('open'); else stEl.classList.remove('open');
      el.classList.toggle('sel', sym === S.selected);
    });
  }
  let dialed_order = null;

  // --- Instant hero from the 1 Hz frame (AI frame lands every ~4 s) -----
  function updateHeroFromWatch() {
    const w = S.watch[S.selected];
    if (!w) return;
    const m = S.MARKETS[S.selected] || { label: S.selected, pair: 'BDT', icon: '•' };
    $('flow-symbol-label').textContent = m.icon + ' ' + m.label + ' · ' + m.pair;
    const mid = (Number(w.bid) + Number(w.ask)) / 2;
    $('flow-price').textContent = fmt(mid, Number(w.digits == null ? 2 : w.digits));
    const chgEl = $('flow-change');
    const chg = Number(w.change_pct || 0);
    chgEl.textContent = pct(chg);
    chgEl.style.color = chg >= 0 ? '#0ECB81' : '#F6465D';
    $('flow-bid').textContent = fmt(w.bid, Number(w.digits == null ? 2 : w.digits));
    $('flow-ask').textContent = fmt(w.ask, Number(w.digits == null ? 2 : w.digits));
    $('flow-spread').textContent = fmt(w.spread, Number(w.digits == null ? 2 : w.digits));
    const buy = Number(w.buyer_pct || 0);
    $('flow-bar-buy').style.width = clamp(buy, 1, 99).toFixed(1) + '%';
    $('flow-buy-pct').textContent = Math.round(buy) + '%';
    $('flow-sell-pct').textContent = Math.round(100 - buy) + '%';
    const st = $('flow-market-status');
    if (st) st.textContent = (w.market_open ? '🟢 ' : '🔴 ') + (w.market_status || '—') + ' · ' + (w.market_message || '');
  }

  // --- BD strip --------------------------------------------------------
  function renderStrip(data) {
    mapStrip('bd-strip-dsex', 'DSEX');
    mapStrip('bd-strip-dse30', 'DSE30');
    mapStrip('bd-strip-gp', 'GP');
    mapStrip('bd-strip-batbc', 'BATBC');
    if (data && data.updated_at) $('bd-strip-updated').textContent = hhmmss(data.updated_at);
    if (S.bd && S.bd.sessions) {
      $('bd-strip-session').textContent = S.bd.sessions.status + ' · ' + S.bd.sessions.phase;
      const chip = $('bd-dse-status');
      if (chip) {
        chip.textContent = 'DSE ' + S.bd.sessions.status;
        chip.style.borderColor = S.bd.sessions.status === 'OPEN' ? 'rgba(14,203,129,.5)' : 'rgba(255,209,102,.4)';
        chip.style.color = S.bd.sessions.status === 'OPEN' ? '#0ECB81' : '#ffd166';
      }
    }
  }

  function mapStrip(id, sym) {
    const el = $(id);
    const w = S.watch[sym];
    if (!el || !w) return;
    const mid = (Number(w.bid) + Number(w.ask)) / 2;
    const chg = Number(w.change_pct || 0);
    el.textContent = fmt(mid, Number(w.digits == null ? 2 : w.digits)) + ' (' + pct(chg) + ')';
    el.style.color = chg >= 0 ? '#0ECB81' : '#F6465D';
  }

  /* ------------------------------------------------------------------ *
   * 4 s state frame — hero AI, outlook, sessions, setups, event, curve *
   * ------------------------------------------------------------------ */

  async function pollState() {
    try {
      const data = await getJson('/api/bd/state?symbol=' + encodeURIComponent(S.selected), 12000);
      if (!data || !data.ok) return;
      S.bd = data;
      renderHero(data);
      if ($('watch-outlook-chips')) renderOutlook(data.outlook || []);
      renderSessions(data.sessions || {});
      renderAi(data.ai || {});
      renderSetups(data.setups || []);
      renderEvent(data.event || {});
      renderCurve(data.curve || []);
      $('bd-strip-session').textContent = (data.sessions ? data.sessions.status + ' · ' + data.sessions.phase : '—');
      $('bd-strip-updated').textContent = hhmmss(data.updated_at);
    } catch (err) { /* keep last frame */ }
  }

  function renderHero(data) {
    const hero = data.hero || {};
    S._heroSym = data.symbol;
    const m = S.MARKETS[data.symbol] || {};
    const label = $('flow-symbol-label');
    if (label && hero.label) {
      label.textContent = (hero.icon || m.icon || '•') + ' ' + hero.label + ' · ' + (hero.pair || '');
    }
    const priceEl = $('flow-price');
    priceEl.textContent = fmt(hero.price, hero.digits);
    priceEl.dataset.ai = '1';
    const chgEl = $('flow-change');
    const chg = Number(hero.change_pct || 0);
    chgEl.textContent = pct(chg) + ' (' + (chg >= 0 ? '+' : '') + fmt(hero.change_abs, hero.digits) + ')';
    chgEl.style.color = chg >= 0 ? '#0ECB81' : '#F6465D';
    $('flow-bid').textContent = fmt(hero.bid, hero.digits);
    $('flow-ask').textContent = fmt(hero.ask, hero.digits);
    $('flow-spread').textContent = fmt(hero.spread, hero.digits);
    const buy = clamp(Number(hero.buy_pct || 50), 1, 99);
    $('flow-bar-buy').style.width = buy.toFixed(1) + '%';
    $('flow-buy-pct').textContent = Math.round(buy) + '%';
    $('flow-sell-pct').textContent = Math.round(100 - buy) + '%';
    const st = $('flow-market-status');
    if (st) {
      st.textContent = (hero.market_status === 'OPEN' ? '🟢 ' : '🔴 ') +
        String(hero.market_status || '—').toUpperCase() + ' · ' + (hero.market_message || '');
    }

    const ai = data.ai || {};
    const verdict = ai.verdict || 'NEUTRAL';
    const sig = $('flow-signal');
    if (sig) {
      sig.textContent = (verdict === 'BUY' ? '▲ BUY' : (verdict === 'SELL' ? '▼ SELL' : '◆ WAIT')) + ' · ' + (ai.confidence || 0) + '%';
      sig.style.color = sideColor(verdict);
      sig.style.borderColor = sideColor(verdict);
    }
    const sigSub = $('flow-signal-sub');
    if (sigSub) {
      sigSub.textContent = verdict === 'NEUTRAL'
        ? 'the AI is still reading the flow · score ' + fmt(ai.score, 1)
        : 'the AI says ' + verdict.toLowerCase() + ' · score ' + fmt(ai.score, 1) + ' · ' + (hero.label || data.symbol);
    }
  }

  function renderOutlook(outlook) {
    const box = $('watch-outlook-chips');
    if (!box) return;
    if (!outlook.length) { box.innerHTML = '<div class="sessions-loading">Reading the flow…</div>'; return; }
    box.innerHTML = outlook.map((o) => {
      const cls = o.direction === 'BUY' ? 'up' : (o.direction === 'SELL' ? 'down' : 'flat');
      return '<span class="bd-outlook-chip ' + cls + '">' +
        '<span>' + esc(o.icon || '•') + '</span>' +
        '<span class="o-sym">' + esc(o.label) + '</span>' +
        '<span class="o-chg">' + pct(Number(o.change_pct || 0)) + '</span>' +
        '<span class="o-dir">' + esc(o.direction) + ' · ' + esc(o.grade) + '</span>' +
        '</span>';
    }).join('');
  }

  function renderSessions(sess) {
    if (!$('sessions-grid')) return;
    $('sessions-summary').textContent = sess.summary || sess.message || '—';
    const cards = sess.cards || [];
    if (!cards.length) { $('sessions-grid').innerHTML = '<div class="sessions-loading">Clock starting…</div>'; return; }
    const now = Date.now() / 1000;
    $('sessions-grid').innerHTML = cards.map((c) => {
      let endLabel = c.end_ts ? 'ENDS IN ' + fmtCountdown(c.end_ts - now) : 'STARTS IN ' + fmtCountdown(c.start_ts - now);
      return '<div class="bd-sess-card' + (c.active ? ' active' : '') + '">' +
        '<div class="bd-sess-name">' + esc(c.name) + '</div>' +
        '<div class="bd-sess-range">' + esc(c.range) + '</div>' +
        '<div class="bd-sess-end">' + endLabel + '</div>' +
        '<div class="bd-sess-tag' + (c.active ? '' : ' off') + '">' + (c.active ? '● LIVE NOW' : '· idle') + '</div>' +
        '</div>';
    }).join('');
    $('sessions-updated').textContent = 'Dhaka ' + dhakaClock() + ' · updated ' + nowHMS();
  }

  function renderAi(ai) {
    if (!$('ai-grid')) return;
    const verdict = ai.verdict || 'NEUTRAL';
    const vc = sideColor(verdict);
    $('ai-verdict').innerHTML = '<span style="color:' + vc + ';font-weight:800;">' +
      (verdict === 'BUY' ? '▲ BUY' : (verdict === 'SELL' ? '▼ SELL' : '◆ NEUTRAL')) + '</span>' +
      ' <span style="color:#8b98ab;">· score ' + fmt(ai.score, 1) + ' · confidence ' + (ai.confidence || 0) + '%</span>';
    const tg = ai.targets || {};
    const tRows = [
      ['T1', tg.t1], ['T2', tg.t2], ['STOP', tg.stop]
    ].filter((l) => l[1] != null);
    $('ai-targets').innerHTML = tRows.map((r) =>
      '<span class="bd-target-chip"><small>' + r[0] + '</small>' + fmt(r[1], 2) + '</span>').join('');
    $('ai-grid').innerHTML = (ai.tf_grades || []).map((g) => {
      const cls = g.grade.indexOf('BUY') >= 0 ? 'buy' : (g.grade.indexOf('SELL') >= 0 ? 'sell' : 'neutral');
      return '<div class="bd-ai-tf">' +
        '<div style="display:flex;justify-content:space-between;align-items:baseline;">' +
        '<span style="font:700 11px JetBrains Mono;color:#64748b;letter-spacing:.08em;">' + esc(String(g.tf).toUpperCase()) + '</span>' +
        '<span class="bd-ai-grade ' + cls + '">' + esc(g.grade) + '</span></div>' +
        '<div class="bd-ai-note">' + esc(g.note || '') + ' · score ' + fmt(g.score, 1) + '</div>' +
        '</div>';
    }).join('');
    $('ai-updated').textContent = 'updated ' + nowHMS();
  }

  function renderSetups(setups) {
    const box = $('bot3-planned-trades');
    if (!box) return;
    if (!setups.length) { box.innerHTML = '<div class="sessions-loading">Scanning the levels…</div>'; return; }
    box.innerHTML = setups.map((s) => {
      const sideCls = String(s.side).toUpperCase() === 'SELL' ? 'sell' : 'buy';
      return '<div class="bd-setup-row ' + sideCls + '">' +
        '<span class="s-side"></span>' +
        '<div><div class="bd-setup-title">' + esc(s.title) + ' <small style="color:#64748b;">(' + esc(s.tf) + ')</small></div>' +
        '<div class="bd-setup-note">' + esc(s.note || '') + '</div></div>' +
        '<div class="bd-setup-px">entry ' + fmt(s.entry, 2) + '</div>' +
        '<div class="bd-setup-px" style="color:#F6465D;">stop ' + fmt(s.sl, 2) + '</div>' +
        '<div class="bd-setup-px" style="color:#0ECB81;">tp ' + fmt(s.tp, 2) + '</div>' +
        '<div class="bd-setup-px" style="color:#ffd166;">RR ' + fmt(s.rr, 1) + '</div>' +
        '<div class="bd-setup-note">' + fmt(s.distance_pt, 2) + ' pts from current</div>' +
        '</div>';
    }).join('');
    $('bot3-last-updated').textContent = 'Updated: ' + nowHMS();
  }

  function renderEvent(ev) {
    if (!$('watch-event-title')) return;
    $('watch-event-title').textContent = ev.title || 'Reading the DSE calendar…';
    $('watch-event-meta').textContent = 'Dhaka ' + dhakaClock(new Date((ev.ts || 0) * 1000)) +
      ' · impact ' + (ev.impact || '—') + ' · status ' + (ev.status || '—');
    const pill = $('watch-bias-pill');
    if (pill) {
      const ai = (S.bd.ai || {});
      const wc = sideColor(ai.verdict);
      pill.textContent = 'BIAS: ' + (ai.verdict || 'WAIT') + ' (' + (ai.confidence || 0) + '%)';
      pill.style.color = wc; pill.style.borderColor = wc;
    }
    const pm = $('watch-predicted-move');
    if (pm) {
      pm.textContent = 'Next event: ' + (ev.title || '—');
    }
    const reason = $('watch-bias-reason');
    if (reason) {
      reason.textContent = (ev.status === 'LIVE') ? 'Event window is active — watch the tape inline above.' :
        'Countdown running · bias recalculated live from 15m grade score.';
    }
    $('watch-next-impacted-chips').innerHTML = (ev.impacted || []).map((r) => {
      const cls = r.direction === 'BUY' ? 'buy' : (r.direction === 'SELL' ? 'sell' : 'wait');
      return '<span class="bd-impacted-chip ' + cls + '">' + esc(r.label) + ' → ' + esc(r.direction) + '</span>';
    }).join('');
    $('watch-plan').textContent = ev.plan || '—';
  }

  function renderCurve(rows) {
    const box = $('flow-curve-box');
    if (!box) return;
    if (!rows || rows.length < 2) {
      box.innerHTML = '<div class="sessions-loading">No curve data yet…</div>';
      return;
    }
    const W = 600, H = 150, P = 10;
    let mn = Infinity, mx = -Infinity;
    rows.forEach((r) => { const v = Number(r[1]); if (v < mn) mn = v; if (v > mx) mx = v; });
    const span = (mx - mn) || (mx * 0.002) || 1;
    mn -= span * 0.08; mx += span * 0.08;
    const pts = rows.map((r, i) =>
      (P + i * (W - 2 * P) / (rows.length - 1)).toFixed(1) + ',' +
      (H - P - (Number(r[1]) - mn) / (mx - mn) * (H - 2 * P)).toFixed(1)).join(' ');
    const up = Number(rows[rows.length - 1][1]) >= Number(rows[0][1]);
    const col = up ? '#0ECB81' : '#F6465D';
    box.innerHTML =
      '<svg viewBox="0 0 600 150" preserveAspectRatio="none">' +
      '<line x1="0" y1="' + (H / 2) + '" x2="600" y2="' + (H / 2) + '" stroke="rgba(148,163,184,.15)" stroke-dasharray="4 6"/>' +
      '<polyline fill="none" stroke="' + col + '" stroke-width="2" points="' + pts + '"/>' +
      '</svg>';
    const hero = (S.bd && S.bd.hero) || {};
    $('flow-curve-sub').textContent = (hero.label || S.selected) + ' · ' + rows.length + ' × 1m ticks';
  }

  /* ------------------------------------------------------------------ *
   * 1 s heartbeat — clocks, countdowns                                  *
   * ------------------------------------------------------------------ */

  function heartbeat() {
    $('bd-dhaka-clock').textContent = dhakaClock();
    const ev = (S.bd && S.bd.event) || null;
    if (ev && ev.ts) {
      const sec = ev.ts - Date.now() / 1000;
      $('watch-countdown').textContent = (ev.status === 'LIVE' ? 'LIVE NOW' : 'T-' + fmtCountdown(sec) + ' · ' + (ev.impact || ''));
    }
    if ($('watch-timer-text')) $('watch-timer-text').textContent = 'LIVE · ' + nowHMS();
  }

  /* ------------------------------------------------------------------ *
   * Live open-volume chips (world simulated tape, not own account)       *
   * ------------------------------------------------------------------ */

  function money(v) {
    let n = Number(v || 0);
    if (Math.abs(n) >= 1e9) return '৳' + (n / 1e9).toFixed(2) + 'B';
    if (Math.abs(n) >= 1e6) return '৳' + (n / 1e6).toFixed(2) + 'M';
    if (Math.abs(n) >= 1e3) return '৳' + (n / 1e3).toFixed(1) + 'K';
    return '৳' + n.toFixed(2);
  }

  function refreshOpenVolume() {
    if (!window.TradersTerminal || typeof window.TradersTerminal.getOpenVolume !== 'function') return;
    let vol;
    try { vol = window.TradersTerminal.getOpenVolume(); } catch (e) { return; }
    if (!vol) return;
    const bE = $('b-open-buy-val'), sE = $('b-open-sell-val');
    if (bE) bE.textContent = money(vol.openBuyVol);
    if (sE) sE.textContent = money(vol.openSellVol);
  }

  /* ------------------------------------------------------------------ *
   * Boot                                                                *
   * ------------------------------------------------------------------ */

  function boot() {
    loadCatalog();
    pollWatch();
    setInterval(pollWatch, 1100);
    setInterval(pollState, 4000);
    setInterval(heartbeat, 1000);
    setInterval(refreshOpenVolume, 900);
    heartbeat();
    pollState();

    // The shared terminal announces market switches (chips/select/sync) —
    // follow along so every stage keeps matching the chart.
    window.addEventListener('chart:market', (e) => {
      const sym = e && e.detail && e.detail.symbol;
      if (sym && S.MARKETS[sym] && sym !== S.selected) {
        S.selected = sym;
        renderChips();
        ['sessions-market', 'ai-market'].forEach((id) => {
          const el = $(id);
          if (el && el.value !== sym) el.value = sym;
        });
        pollState();
      }
    });

    const sessSel = $('sessions-market');
    if (sessSel) sessSel.addEventListener('change', () => selectMarket(sessSel.value));
    const aiSel = $('ai-market');
    if (aiSel) aiSel.addEventListener('change', () => selectMarket(aiSel.value));
    const recalBtn = $('bot3-recalc-btn');
    if (recalBtn) recalBtn.addEventListener('click', () => {
      pollState();
      const b = $('bot3-recalc-badge');
      if (b) { b.textContent = '⚡ Recalculated ' + nowHMS(); }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    setTimeout(boot, 50);
  }
})();
