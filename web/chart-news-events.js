/**
 * Chart News Events
 * Draws the live economic-calendar / Google-News events (the same feed the
 * "05 · NEXT EVENT" stage renders) straight onto the candlestick chart.
 *
 *  - released events inside the visible history get a thin marker line
 *  - upcoming events are placed at the exact time they will print, inside a
 *    reserved "future" lane on the right of the chart (where the next candles
 *    will appear), with a numbered list of the next headlines
 *  - the running candle carries a live countdown tag of the next event
 *
 * Direction colour: green = UP / BUY, red = DOWN / SELL, amber = WAIT.
 * The overlay never writes to the DOM and never touches the news section.
 */
(function () {
  'use strict';

  const REFRESH_MS = 30000;      // calendar refresh
  const TICK_MS = 15000;         // countdown repaint
  const HORIZON_SEC = 12 * 3600; // furthest upcoming event drawn
  const PAST_WINDOW_SEC = 2 * 3600;
  const MAX_FUTURE_ROWS = 4;

  const COLORS = { UP: '#0ECB81', DOWN: '#F6465D', WAIT: '#F0B90B' };
  const IMPACT_ALPHA = { high: 1, medium: 0.72, low: 0.42 };

  let events = [];       // normalised rows, sorted by time
  let upcomingCount = 0; // events inside the drawn horizon (drives the lane)
  let fetching = false;

  function marketKey(v) {
    return String(v || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  }

  function marketMatch(a, b) {
    const x = marketKey(a);
    const y = marketKey(b);
    if (!x || !y) return false;
    if (x === y) return true;
    const short = x.length <= y.length ? x : y;
    const long = x.length <= y.length ? y : x;
    return short.length >= 5 && long.indexOf(short) === 0 && long.length - short.length <= 4;
  }

  function normSide(v) {
    const s = String(v || '').toLowerCase();
    if (s === 'buy' || s === 'up' || s === 'bullish') return 'UP';
    if (s === 'sell' || s === 'down' || s === 'bearish') return 'DOWN';
    return '';
  }

  // Predicted impact of the event on the market currently on the chart.
  function impactFor(ev, symbol) {
    const impacts = Array.isArray(ev.impacted) ? ev.impacted : [];
    if (!symbol) return null;
    for (let i = 0; i < impacts.length; i++) {
      if (marketMatch(impacts[i].market, symbol)) return impacts[i];
    }
    return null;
  }

  // Direction of the event for the market currently on the chart, so gold and
  // the same calendar row can disagree without contradicting each other.
  function directionFor(ev, symbol) {
    const imp = impactFor(ev, symbol);
    if (imp) {
      const d = normSide(imp.side) || normSide(imp.direction);
      if (d) return d;
    }
    return normSide(ev.side) || 'WAIT';
  }

  // Dollar / point amount the event is expected to move this market.
  function moveAbs(imp) {
    if (!imp) return 0;
    const raw = imp.move_abs !== undefined ? imp.move_abs : imp.move_dollar;
    const v = Math.abs(Number(raw));
    return isFinite(v) && v > 0 ? v : 0;
  }

  function moveText(imp) {
    if (!imp) return '';
    const first = String(imp.move_text_en || '').trim().split(/\s+/)[0];
    if (/[0-9]/.test(first)) return first;
    const signed = Number(imp.signed_dollar !== undefined ? imp.signed_dollar : imp.move_dollar);
    if (!isFinite(signed) || signed === 0) return '';
    const abs = Math.abs(signed);
    return (signed > 0 ? '+' : '-') + '$' + abs.toFixed(abs >= 100 ? 1 : 2);
  }

  function fmtPrice(p) {
    const abs = Math.abs(p);
    const dec = abs >= 100 ? 2 : abs >= 1 ? 4 : 5;
    return p.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }

  function visibleFuture(nowSec) {
    const out = [];
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      if (e.ts < nowSec - 300 || e.ts > nowSec + HORIZON_SEC) continue;
      out.push(e);
    }
    if (!out.length) return out;
    // High/medium events keep the chart readable; low-impact rows only show
    // when the calendar has nothing stronger queued.
    const strong = out.filter(e => e.impact !== 'low');
    return (strong.length ? strong : out).slice(0, MAX_FUTURE_ROWS);
  }

  function visiblePast(nowSec, firstCandleTs) {
    const out = [];
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      if (e.ts > nowSec || e.ts < nowSec - PAST_WINDOW_SEC) continue;
      if (firstCandleTs && e.ts < firstCandleTs) continue;
      if (e.impact === 'low') continue;
      out.push(e);
    }
    return out;
  }

  function refreshUpcoming() {
    const nowSec = Date.now() / 1000;
    upcomingCount = 0;
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      if (e.ts < nowSec - 300 || e.ts > nowSec + HORIZON_SEC) continue;
      if (e.impact === 'low') continue;
      upcomingCount++;
    }
  }

  // Width the chart reserves on the right for the upcoming-event lane.
  function laneWidth(chartW) {
    if (!upcomingCount) return 0;
    if (!chartW || chartW < 420) return 0;
    return Math.min(196, Math.max(150, Math.round(chartW * 0.2)));
  }

  async function refreshNews() {
    if (fetching) return;
    fetching = true;
    try {
      const res = await fetch('/api/state', { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const st = await res.json();
      const rows = (st && st.bot5 && Array.isArray(st.bot5.news)) ? st.bot5.news : [];
      const next = [];
      for (let i = 0; i < rows.length; i++) {
        const r = rows[i];
        const ts = Number(r && r.ts);
        if (!(ts > 0)) continue;
        next.push({
          ts: ts,
          title: String(r.title || r.event || '—'),
          currency: String(r.currency || ''),
          impact: String(r.impact || 'low').toLowerCase(),
          side: r.side || '',
          impacted: Array.isArray(r.impacted_markets) ? r.impacted_markets : [],
          forecast: r.forecast || '',
          previous: r.previous || '',
        });
      }
      next.sort((a, b) => a.ts - b.ts);
      events = next;
      refreshUpcoming();
      repaint();
    } catch (e) {
      // Keep the last good calendar; the chart simply redraws it.
    } finally {
      fetching = false;
    }
  }

  function repaint() {
    if (window.SoftradeXTerminal && typeof window.SoftradeXTerminal.refresh === 'function') {
      window.SoftradeXTerminal.refresh();
    }
  }

  function fmtClock(ts) {
    const d = new Date(ts * 1000);
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    return h + ':' + m;
  }

  function fmtCountdown(minutes) {
    const mins = Math.max(0, Math.round(minutes));
    if (mins < 60) return mins + 'm';
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    if (h < 24) return m ? h + 'h ' + m + 'm' : h + 'h';
    const d = Math.floor(h / 24);
    return d + 'd ' + (h % 24) + 'h';
  }

  function trimToWidth(ctx, text, maxW) {
    if (maxW <= 8) return '';
    if (ctx.measureText(text).width <= maxW) return text;
    let cut = text;
    while (cut.length > 1 && ctx.measureText(cut + '…').width > maxW) {
      cut = cut.slice(0, -1);
    }
    return cut + '…';
  }

  function roundRect(ctx, x, y, w, h, r) {
    const rr = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + w, y, x + w, y + h, rr);
    ctx.arcTo(x + w, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + w, y, rr);
    ctx.closePath();
  }

  // Price levels the chart must keep on screen: the projected targets of the
  // next releases that carry a move for the current market.
  function priceBounds(args) {
    const klines = args && args.klines;
    if (!klines || !klines.length) return null;
    const nowSec = Date.now() / 1000;
    const close = Number(klines[klines.length - 1].close);
    if (!(close > 0)) return null;
    let lo = Infinity;
    let hi = -Infinity;
    let n = 0;
    for (let i = 0; i < events.length && n < 2; i++) {
      const e = events[i];
      if (e.ts < nowSec - 60 || e.ts > nowSec + HORIZON_SEC || e.impact === 'low') continue;
      const imp = impactFor(e, args.symbol);
      const dist = moveAbs(imp);
      if (!dist) continue;
      const dir = directionFor(e, args.symbol);
      if (dir === 'WAIT') continue;
      const target = close + (dir === 'UP' ? dist : -dist);
      lo = Math.min(lo, target);
      hi = Math.max(hi, target);
      n++;
    }
    return n ? { min: lo, max: hi } : null;
  }

  function draw(v) {
    const ctx = v.ctx;
    if (!ctx || !v.klines || !v.klines.length || !(v.candleSlot > 0)) return;
    if (!events.length) return;

    const chartW = v.chartW;
    const chartH = v.chartH;
    const lastIdx = v.klines.length - 1;
    const lastCandle = v.klines[lastIdx];
    const tfSec = v.tfSec || 300;
    const nowSec = Date.now() / 1000;
    const future = visibleFuture(nowSec);
    const past = visiblePast(nowSec, v.klines[0].time);
    if (!future.length && !past.length) return;

    const xNow = v.candleIndexToX(lastIdx);
    const midTs = lastCandle.time + tfSec / 2;
    const xOfTs = ts => xNow + ((ts - midTs) * v.candleSlot) / tfSec;
    const laneW = v.laneW || 0;

    ctx.save();
    ctx.lineWidth = 1;

    // ---- released events inside the visible history -------------------
    for (let i = 0; i < past.length; i++) {
      const e = past[i];
      const x = xOfTs(e.ts);
      if (x < 6 || x > chartW - 6) continue;
      const dir = directionFor(e, v.symbol);
      const color = COLORS[dir] || COLORS.WAIT;
      const alpha = IMPACT_ALPHA[e.impact] || 0.5;

      ctx.globalAlpha = 0.16 * alpha;
      ctx.strokeStyle = color;
      ctx.setLineDash([2, 5]);
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, chartH);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.globalAlpha = 0.5 * alpha;
      ctx.fillStyle = color;
      ctx.font = '7px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(dir === 'UP' ? '▲' : dir === 'DOWN' ? '▼' : '•', x, 2);
    }

    ctx.globalAlpha = 1;

    // ---- upcoming events: the reserved future lane --------------------
    if (laneW > 0 && future.length) {
      const rows = future;
      const rowH = 26;
      const listH = rows.length * rowH + 20;
      const listTop = chartH - listH;
      const x0 = chartW - laneW + 5;

      ctx.fillStyle = 'rgba(8, 12, 18, 0.80)';
      roundRect(ctx, chartW - laneW - 1, listTop, laneW + 1, listH, 4);
      ctx.fill();
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.22)';
      ctx.stroke();

      ctx.fillStyle = '#94a3b8';
      ctx.font = 'bold 8px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText('FUTURE NEWS · UP / DOWN', x0, listTop + 9);

      // Rails spread across the lane in real time order: the nearest release
      // sits next to the running candle, the furthest at the right edge, so a
      // quiet 5m chart can still show events that are hours away.
      const maxX = chartW - 8;
      const laneStart = chartW - laneW + 8;
      const span = Math.max(20, maxX - laneStart);
      const horizonMin = Math.max(60, Math.min(720, (rows[rows.length - 1].ts - nowSec) / 60));
      const placed = rows.map((e) => {
        const dir = directionFor(e, v.symbol);
        const mins = Math.max(0, (e.ts - nowSec) / 60);
        return {
          x: laneStart + span * Math.min(1, mins / horizonMin),
          e: e,
          dir: dir,
          color: COLORS[dir] || COLORS.WAIT,
          alpha: IMPACT_ALPHA[e.impact] || 0.5,
        };
      });
      for (let i = 1; i < placed.length; i++) {
        if (placed[i].x < placed[i - 1].x + 26) placed[i].x = placed[i - 1].x + 26;
      }
      const over = placed.length ? placed[placed.length - 1].x - maxX : 0;
      if (over > 0) {
        for (let i = 0; i < placed.length; i++) {
          placed[i].x = Math.max(laneStart, placed[i].x - over);
        }
      }

      for (let i = 0; i < rows.length; i++) {
        const e = rows[i];
        const dir = directionFor(e, v.symbol);
        const color = COLORS[dir] || COLORS.WAIT;
        const rowTop = listTop + 18 + i * rowH;
        const line1 = rowTop + 7;
        const line2 = rowTop + 17;

        // ---- list row: time · direction · countdown, then the headline
        ctx.globalAlpha = 1;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(x0 + 4, line1, e.impact === 'high' ? 4.5 : 3.5, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = '#cbd5e1';
        ctx.font = '8px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(fmtClock(e.ts), x0 + 11, line1);

        ctx.fillStyle = color;
        ctx.font = 'bold 8px "JetBrains Mono", monospace';
        const dirTxt = dir === 'UP' ? '▲ UP' : dir === 'DOWN' ? '▼ DOWN' : '• WAIT';
        ctx.fillText(dirTxt, x0 + 42, line1);

        const mins = (e.ts - nowSec) / 60;
        ctx.fillStyle = 'rgba(148, 163, 184, 0.9)';
        ctx.font = '7.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.fillText('in ' + fmtCountdown(mins), chartW - 5, line1);

        // expected dollar / point move, then the headline
        const mv = moveText(impactFor(e, v.symbol));
        let titleX = x0;
        if (mv) {
          ctx.fillStyle = color;
          ctx.font = 'bold 7.5px "JetBrains Mono", monospace';
          ctx.textAlign = 'left';
          ctx.fillText(mv, x0, line2);
          titleX = x0 + ctx.measureText(mv).width + 5;
        }
        ctx.fillStyle = 'rgba(203, 213, 225, 0.78)';
        ctx.font = '7.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText(trimToWidth(ctx, e.title, chartW - 5 - titleX), titleX, line2);
      }

      // ---- vertical rails at the exact future time
      for (let i = 0; i < placed.length; i++) {
        const p = placed[i];
        ctx.globalAlpha = 0.85;
        ctx.strokeStyle = p.color;
        ctx.lineWidth = p.alpha > 0.8 ? 1.6 : 1.2;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(p.x, 34);
        ctx.lineTo(p.x, listTop - 2);
        ctx.stroke();
        ctx.setLineDash([]);

        // arrow head at both ends of the rail so the column is readable
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.moveTo(p.x - 3.5, 34);
        ctx.lineTo(p.x + 3.5, 34);
        ctx.lineTo(p.x, 39);
        ctx.closePath();
        ctx.fill();
        ctx.beginPath();
        ctx.moveTo(p.x - 3, listTop - 6);
        ctx.lineTo(p.x + 3, listTop - 6);
        ctx.lineTo(p.x, listTop - 1);
        ctx.closePath();
        ctx.fill();
      }
    }

    ctx.globalAlpha = 1;

    // ---- projected target price marks ---------------------------------
    // The axis badge carries the exact level the event is expected to reach
    // plus the dollar / point move, for the two nearest releases.
    const marks = [];
    const liveY = v.priceToY(lastCandle.close);
    // Price marks are taken from the nearest releases that actually carry a
    // predicted move for THIS market, so gold can skip an AUD/JPY print that
    // has no gold forecast and pick the next USD one instead.
    const markSources = [];
    for (let i = 0; i < events.length && markSources.length < 6; i++) {
      const e = events[i];
      if (e.ts < nowSec - 60 || e.ts > nowSec + HORIZON_SEC || e.impact === 'low') continue;
      if (!impactFor(e, v.symbol)) continue;
      markSources.push(e);
    }
    for (let i = 0; i < markSources.length && marks.length < 2; i++) {
      const e = markSources[i];
      const imp = impactFor(e, v.symbol);
      const dist = moveAbs(imp);
      if (!dist) continue;
      const dir = directionFor(e, v.symbol);
      if (dir === 'WAIT') continue;
      const target = lastCandle.close + (dir === 'UP' ? dist : -dist);
      const rawY = v.priceToY(target);
      const offScale = rawY < 12 || rawY > v.candleH - 16;
      const y = Math.max(12, Math.min(v.candleH - 16, rawY));
      if (Math.abs(y - liveY) < 20) continue;
      if (marks.some(m => Math.abs(m.y - y) < 24)) continue;
      marks.push({
        y: y, e: e, dir: dir, color: COLORS[dir], mv: moveText(imp),
        target: target, offScale: offScale,
      });
    }
    for (let i = 0; i < marks.length; i++) {
      const m = marks[i];
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = m.color;
      ctx.lineWidth = 1;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      // A target outside the visible price range is not a level on this screen,
      // so it only gets a stub next to the axis instead of a full-width line.
      ctx.moveTo(m.offScale ? chartW - 34 : 0, m.y);
      ctx.lineTo(chartW, m.y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      if (!m.offScale) {
        ctx.fillStyle = 'rgba(148, 163, 184, 0.92)';
        ctx.font = '7.5px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        const label = fmtClock(m.e.ts) + '  ' + m.mv + '  ' + (m.e.currency || '') + ' ' + (m.e.title || '');
        ctx.fillText(trimToWidth(ctx, label, chartW * 0.6), 4, m.y - 2);
      }

      const axisW = (v.priceWidth || 76) - 4;
      ctx.fillStyle = m.color;
      roundRect(ctx, chartW + 2, m.y - 11, axisW, 22, 3);
      ctx.fill();
      ctx.fillStyle = '#071018';
      ctx.font = 'bold 8px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const priceTxt = (m.offScale ? (m.dir === 'UP' ? '▲ ' : '▼ ') : '') + fmtPrice(m.target);
      ctx.fillText(priceTxt, chartW + 2 + axisW / 2, m.y - 4);
      ctx.font = 'bold 7px "JetBrains Mono", monospace';
      ctx.fillText(m.mv, chartW + 2 + axisW / 2, m.y + 6);
    }

    // ---- next event on the running candle -----------------------------
    if (future.length) {
      const next = future[0];
      const dir = directionFor(next, v.symbol);
      const color = COLORS[dir] || COLORS.WAIT;
      const mins = (next.ts - nowSec) / 60;
      const nextMove = moveText(impactFor(next, v.symbol));
      const label = (dir === 'UP' ? '▲ UP ' : dir === 'DOWN' ? '▼ DOWN ' : '• WAIT ') +
        (mins >= 0 ? fmtCountdown(mins) : 'NOW') + (nextMove ? ' · ' + nextMove : '');

      ctx.font = 'bold 8px "JetBrains Mono", monospace';
      const tw = ctx.measureText(label).width + 10;
      // Keep the tag on the candle side of the lane so it never hides the
      // future-news list that lives on the right edge.
      const laneStart = laneW > 0 ? chartW - laneW : chartW;
      let tx = xNow + 8;
      if (tx + tw > laneStart - 6) tx = xNow - tw - 8;
      if (tx < 4) tx = Math.max(4, laneStart - tw - 6);
      const ty = Math.max(8, Math.min(chartH - 16, v.priceToY(lastCandle.close) + 14));

      ctx.fillStyle = 'rgba(7, 12, 20, 0.9)';
      roundRect(ctx, tx, ty, tw, 13, 3);
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.85;
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = color;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, tx + tw / 2, ty + 7);
    }

    ctx.restore();
  }

  window.ChartNewsOverlay = {
    laneWidth, draw, priceBounds, refresh: refreshNews, getEvents: () => events.slice(),
  };

  refreshNews();
  setInterval(refreshNews, REFRESH_MS);
  setInterval(() => {
    refreshUpcoming();
    if (!document.hidden) repaint();
  }, TICK_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { refreshNews(); repaint(); }
  });
})();
