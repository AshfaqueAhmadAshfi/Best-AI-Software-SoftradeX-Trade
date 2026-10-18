/* ============================================================
   Live Candle Background Engine (SoftradeX / Exness Dashboard)
   - Dynamic animated multi-phase candlestick market chart
   - Strict SINGLE-SYMBOL LOCK: Never mixes Gold with BTC or Forex
   - Asset-relative step sizing with realistic bodies and wicks
   - Continuous real-time up/down micro-fluctuations
   - Ultra-smooth Y-scale interpolation and premium visual depth
   ============================================================ */
(function () {
  'use strict';

  var canvas = document.getElementById('candle-canvas');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // --- Configuration ---
  var CANDLE_SEC = 4.0;        // candle duration (seconds) - dynamic & alive
  var MAX_CANDLES = 70;        // number of candles kept on screen
  var FRAME_MS = 33;           // ~30 fps smooth rendering
  // The price axis follows the candles that are actually on screen. Without
  // this the whole history (incl. a stale spike from long ago) dictates the
  // scale and the live candles get squashed into one thin line.
  var SCALE_CANDLES = 46;
  // A simulated market that drifts without limit stretches the chart until the
  // newest candles sit off-screen. The sim is therefore pulled back toward the
  // price it was seeded with, and a fresh history is built if it ever walks too
  // far away (only while no real quote is arriving - live prices are never
  // re-anchored like this).
  var REANCHOR_STEPS = 9;

  // --- State ---
  var candles = [];            // [{ o, h, l, c, v, bull, t }]
  var cur = null;              // active forming candle
  var candleStarted = 0;
  var lastFrame = 0; // Date.now() based - never a rAF timestamp
  var tickCount = 0;
  var W = 0, H = 0, dpr = 1;
  var hidden = false;

  // Single locked instrument (NEVER mix different symbol quotes into the same chart)
  var lockedSymbol = 'XAUUSDm';
  var displaySymbol = 'XAU/USD';
  var price = 4123.70;
  var anchorPrice = price;     // price the current history was seeded with
  var lastWsTick = 0;
  var trendPhase = 0;
  var trendBias = 0.15;
  var volatilityMultiplier = 1.0;

  // Smoothed Y-scale range (prevents jittery jumps)
  var smoothLo = 4115.0;
  var smoothHi = 4135.0;

  // Calculate asset-relative step size (so Gold, BTC, and Forex all have realistic candle heights)
  function getAssetStep(p) {
    var val = p || price || 4120;
    if (val >= 20000) return val * 0.00065;   // BTC: ~55 points
    if (val >= 1000)  return val * 0.00075;   // Gold/Indices: ~3.1 points
    if (val >= 50)    return val * 0.0008;    // Crude Oil/Stocks: ~0.06 points
    return val * 0.0006;                      // EUR/USD: ~0.00065 points
  }

  // Seed realistic historical candles at a specific price level
  function initMarketSeed(startPrice) {
    candles = [];
    var p = (startPrice && isFinite(startPrice) && startPrice > 0) ? startPrice : price;
    price = p;

    var step = getAssetStep(p);
    var totalSeed = 55;
    var now = Date.now();
    var waveCycle = 0;
    var waveDir = 1;

    for (var i = 0; i < totalSeed; i++) {
      waveCycle++;
      if (waveCycle > 5 + Math.random() * 7) {
        waveCycle = 0;
        waveDir = -waveDir; // toggle wave direction (bull / bear wave)
      }

      var openP = p;
      var trendMove = waveDir * (0.7 + Math.random() * 1.5) * step;
      var noise = (Math.random() - 0.48) * 1.6 * step;
      var delta = trendMove + noise;

      // Occasional counter-trend pullbacks
      if (Math.random() < 0.28) delta = -delta * 0.65;

      var closeP = Math.max(openP * 0.3, openP + delta);
      var highWick = Math.random() * (Math.abs(delta) * 0.75 + step * 0.7);
      var lowWick = Math.random() * (Math.abs(delta) * 0.75 + step * 0.7);

      var highP = Math.max(openP, closeP) + highWick;
      var lowP = Math.min(openP, closeP) - lowWick;
      var vol = Math.round(40 + Math.abs(delta / step) * 35 + Math.random() * 45);

      candles.push({
        o: openP,
        h: highP,
        l: lowP,
        c: closeP,
        v: vol,
        bull: closeP >= openP,
        t: now - (totalSeed - i) * CANDLE_SEC * 1000
      });

      p = closeP;
    }

    price = p;
    anchorPrice = p;
    smoothLo = p - step * 5.5;
    smoothHi = p + step * 5.5;
    startNewCandle(now);
  }

  function startNewCandle(ts) {
    var openP = cur ? cur.c : price;
    cur = {
      o: openP,
      h: openP,
      l: openP,
      c: openP,
      v: Math.round(45 + Math.random() * 55),
      bull: true,
      t: ts
    };
    candleStarted = ts;
  }

  // Realistic market step when no live feed or between ticks
  function simMicroTick() {
    tickCount++;
    trendPhase += 0.035;

    // Gradual shift in macro wave (rallies, consolidations, dips)
    if (tickCount % 50 === 0) {
      trendBias = Math.sin(trendPhase) * 0.5 + (Math.random() - 0.5) * 0.25;
      volatilityMultiplier = 0.7 + Math.random() * 0.8;
    }

    var step = getAssetStep(price);
    // Micro oscillation with momentum & realistic noise
    var microSine = Math.sin(tickCount * 0.18) * 0.45;
    var jitter = (Math.random() - 0.495) * 1.35;
    // gentle pull back to the seeded anchor so the sim never wanders away
    var revert = ((anchorPrice - price) / step) * 0.05;
    var dP = (microSine * 0.35 + trendBias * 0.65 + jitter + revert) * step * 0.16 * volatilityMultiplier;

    price = Math.max(price * 0.3, price + dP);
  }

  function tick(ts) {
    var isLiveFeed = (Date.now() - lastWsTick < 3500);

    // If MT5 watch stream is inactive (e.g. login screen or weekend), simulate active market
    if (!isLiveFeed) {
      simMicroTick();
      // the simulated market walked too far from the chart's anchor -> build a
      // fresh, realistic history at the current price instead of stretching
      if (Math.abs(price - anchorPrice) > getAssetStep(price) * REANCHOR_STEPS) {
        initMarketSeed(price);
        return;
      }
    }

    if (!cur || ts - candleStarted >= CANDLE_SEC * 1000) {
      catchUp(ts);
      startNewCandle(ts);
    }

    // Active forming candle update
    cur.c = price;
    if (price > cur.h) cur.h = price;
    if (price < cur.l) cur.l = price;

    // Small wick exploration during high volatility
    if (!isLiveFeed && Math.random() < 0.16) {
      var step = getAssetStep(price);
      if (Math.random() < 0.5) {
        cur.h += Math.random() * step * 0.14;
      } else {
        cur.l -= Math.random() * step * 0.14;
      }
    }
    cur.bull = cur.c >= cur.o;
  }

  // Handle incoming live quote from WebSocket
  function onLiveTick(np, sym) {
    if (!isFinite(np) || np <= 0) return;

    // Safety: If price jumps by > 8% (e.g. symbol switch), reseed history cleanly
    if (candles.length > 0 && Math.abs(np - price) / Math.max(1, price) > 0.08) {
      initMarketSeed(np);
      lastWsTick = Date.now();
      return;
    }

    price = np;
    lastWsTick = Date.now();
  }

  // ---- WebSocket connection for live MT5 prices ----------
  function connect() {
    try {
      var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
      var ws = new WebSocket(proto + location.host + '/ws');

      ws.onmessage = function (e) {
        var msg;
        try { msg = JSON.parse(e.data); } catch (err) { return; }
        if (msg.type !== 'watch' || !msg.data || !msg.data.watches) return;

        var rows = msg.data.watches || [];
        if (!rows.length) return;

        // SINGLE SYMBOL LOCK:
        // Always search STRICTLY for Gold (XAUUSDm / XAUUSD / XAUUSD247m).
        // Never accept BTC or Oil or Forex ticks to prevent scale distortion!
        var pick = null;
        for (var i = 0; i < rows.length; i++) {
          var sym = String(rows[i].symbol || '').toUpperCase();
          if (sym === 'XAUUSDM' || sym === 'XAUUSD' || sym === 'XAUUSD247M' || sym.indexOf('XAU') === 0) {
            if (rows[i].bid) {
              pick = rows[i];
              break;
            }
          }
        }

        // If Gold is found, update our price
        if (pick && pick.bid) {
          var np = parseFloat(pick.bid);
          lockedSymbol = pick.symbol;
          displaySymbol = 'XAU/USD';
          onLiveTick(np, pick.symbol);
        }
      };

      ws.onclose = function () { setTimeout(connect, 4000); };
      ws.onerror = function () { try { ws.close(); } catch (err) {} };
    } catch (err) {
      setTimeout(connect, 5000);
    }
  }

  // ---- Responsive Canvas Dimensions -------------------------
  // The background is pure decoration: it must never cost the dashboard a frame.
  // When the dashboard is busy (news rebuild, dials) the canvas drops to ~15 fps
  // instead of hammering the main thread every 33 ms.
  var busyUntil = 0;
  window.addEventListener('softradex:busy', function () {
    busyUntil = Date.now() + 1200;
  });
  function frameBudget() {
    return Date.now() < busyUntil ? 66 : FRAME_MS;
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function formatPriceStr(val) {
    if (!isFinite(val) || val == null) return '';
    if (val >= 1000) return val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (val >= 50) return val.toFixed(2);
    return val.toFixed(4);
  }

  // ---- Canvas Rendering -------------------------------------
  function draw(ts) {
    ctx.clearRect(0, 0, W, H);
    if (!cur) return;

    var zoneTop = H * 0.15;
    var zoneBot = H * 0.85;
    var zoneH = Math.max(10, zoneBot - zoneTop);

    // Compute range across the candles that are on screen + the active candle.
    // Only the newest SCALE_CANDLES are measured: a stale extreme deep in the
    // history must not flatten the live part of the chart.
    var rawLo = Infinity;
    var rawHi = -Infinity;
    var i, c;
    var from = Math.max(0, candles.length - SCALE_CANDLES);

    for (i = from; i < candles.length; i++) {
      c = candles[i];
      if (c.l < rawLo) rawLo = c.l;
      if (c.h > rawHi) rawHi = c.h;
    }
    if (rawLo === Infinity) {
      rawLo = cur.l;
      rawHi = cur.h;
    }
    if (cur.l < rawLo) rawLo = cur.l;
    if (cur.h > rawHi) rawHi = cur.h;

    if (!isFinite(rawLo) || !isFinite(rawHi)) return;

    // Minimum visual spread to ensure clear height
    var minSpread = getAssetStep(price) * 5.0;
    var diff = rawHi - rawLo;
    if (diff < minSpread) {
      var mid = (rawHi + rawLo) / 2;
      rawLo = mid - minSpread / 2;
      rawHi = mid + minSpread / 2;
      diff = minSpread;
    }

    var pad = diff * 0.16;
    var targetLo = rawLo - pad;
    var targetHi = rawHi + pad;

    // Smooth lerp for price scaling
    smoothLo += (targetLo - smoothLo) * 0.09;
    smoothHi += (targetHi - smoothHi) * 0.09;
    if (smoothHi <= smoothLo) smoothHi = smoothLo + 1;

    var curLo = smoothLo;
    var curHi = smoothHi;
    var range = curHi - curLo;

    function yPrice(p) {
      return zoneBot - ((p - curLo) / range) * zoneH;
    }

    // --- Subtle Horizontal Grid Lines & Price Ticks ---
    var gridSteps = 4;
    ctx.save();
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 7]);
    ctx.font = '500 10px "JetBrains Mono", monospace';
    ctx.textAlign = 'right';

    for (var g = 0; g <= gridSteps; g++) {
      var gp = curLo + (range * g) / gridSteps;
      var gy = yPrice(gp);
      if (gy < zoneTop - 10 || gy > zoneBot + 10) continue;

      ctx.strokeStyle = 'rgba(129, 140, 248, 0.08)';
      ctx.beginPath();
      ctx.moveTo(30, gy);
      ctx.lineTo(W - 95, gy);
      ctx.stroke();

      ctx.fillStyle = 'rgba(148, 163, 184, 0.35)';
      ctx.fillText(formatPriceStr(gp), W - 14, gy + 3);
    }
    ctx.restore();

    // --- Candle Layout Geometry ---
    var rightMargin = 88;                  // room for active price pill and right edge
    var leftMargin = 25;
    var totalVisible = Math.min(candles.length + 1, MAX_CANDLES);
    var slot = Math.max(14, Math.min(36, (W - rightMargin - leftMargin) / totalVisible));
    var bodyW = Math.max(7, Math.min(22, slot * 0.65));
    var activeX = W - rightMargin - slot * 0.5;

    // --- Draw Completed Historical Candles ---
    for (i = candles.length - 1; i >= 0; i--) {
      c = candles[i];
      var stepsFromRight = (candles.length - i);
      var cx = activeX - stepsFromRight * slot;
      if (cx < -slot * 2) break;

      var up = c.c >= c.o;
      // Vibrant colors: Neon Emerald vs Crisp Coral Red
      var col = up ? '16, 185, 129' : '244, 63, 94';

      var yo = yPrice(c.o);
      var yc = yPrice(c.c);
      var yh = yPrice(c.h);
      var yl = yPrice(c.l);

      var top = Math.min(yo, yc);
      var hgt = Math.max(3.5, Math.abs(yc - yo));
      var midX = cx + bodyW / 2;

      // Wick
      ctx.strokeStyle = 'rgba(' + col + ', 0.55)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(midX, yh);
      ctx.lineTo(midX, yl);
      ctx.stroke();

      // Body (smooth rounded candle)
      ctx.fillStyle = 'rgba(' + col + ', 0.28)';
      ctx.strokeStyle = 'rgba(' + col + ', 0.85)';
      ctx.lineWidth = 1.2;
      var r = Math.min(2.5, hgt / 2);
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(cx, top, bodyW, hgt, r);
      } else {
        ctx.rect(cx, top, bodyW, hgt);
      }
      ctx.fill();
      ctx.stroke();

      // Faint volume bar at bottom
      var vH = Math.min(H * 0.08, (c.v / 120) * (H * 0.08));
      ctx.fillStyle = 'rgba(' + col + ', 0.15)';
      ctx.fillRect(cx, H - 4 - vH, bodyW, vH);
    }

    // --- Draw Forming Candle (Active / Live Fluctuating) ---
    var curUp = cur.c >= cur.o;
    var curCol = curUp ? '16, 185, 129' : '244, 63, 94';
    var cyo = yPrice(cur.o);
    var cyc = yPrice(cur.c);
    var cyh = yPrice(cur.h);
    var cyl = yPrice(cur.l);

    var ctop = Math.min(cyo, cyc);
    var chgt = Math.max(4.0, Math.abs(cyc - cyo));
    var curMidX = activeX + bodyW / 2;

    // Glowing aura for forming candle
    ctx.save();
    ctx.shadowColor = 'rgba(' + curCol + ', 0.85)';
    ctx.shadowBlur = 16;

    // Active Wick
    ctx.strokeStyle = 'rgba(' + curCol + ', 0.90)';
    ctx.lineWidth = 2.0;
    ctx.beginPath();
    ctx.moveTo(curMidX, cyh);
    ctx.lineTo(curMidX, cyl);
    ctx.stroke();

    // Active Body
    ctx.fillStyle = 'rgba(' + curCol + ', 0.45)';
    ctx.strokeStyle = 'rgba(' + curCol + ', 1.0)';
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(activeX, ctop, bodyW, chgt, 2.5);
    } else {
      ctx.rect(activeX, ctop, bodyW, chgt);
    }
    ctx.fill();
    ctx.stroke();
    ctx.restore();

    // Active candle volume bar
    var curVH = Math.min(H * 0.09, (cur.v / 120) * (H * 0.09));
    ctx.fillStyle = 'rgba(' + curCol + ', 0.32)';
    ctx.fillRect(activeX, H - 4 - curVH, bodyW, curVH);

    // --- Dynamic Crosshair Price Line ---
    ctx.save();
    ctx.setLineDash([4, 6]);
    ctx.strokeStyle = 'rgba(' + curCol + ', 0.48)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(10, cyc);
    ctx.lineTo(W - 95, cyc);
    ctx.stroke();
    ctx.restore();

    // --- High-Tech Live Price Pill Badge on the Right ---
    var priceText = formatPriceStr(price);
    var arrow = curUp ? '▲' : '▼';
    var fullBadge = arrow + ' ' + priceText;

    ctx.save();
    ctx.font = '700 11px "JetBrains Mono", monospace';
    var tw = ctx.measureText(fullBadge).width + 16;
    var bx = W - tw - 12;
    var by = Math.max(zoneTop - 12, Math.min(H - 32, cyc - 11));

    // Badge glow & background
    ctx.shadowColor = 'rgba(' + curCol + ', 0.65)';
    ctx.shadowBlur = 12;
    ctx.fillStyle = 'rgba(10, 13, 22, 0.94)';
    ctx.strokeStyle = 'rgba(' + curCol + ', 0.88)';
    ctx.lineWidth = 1.3;

    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(bx, by, tw, 22, 6);
    } else {
      ctx.rect(bx, by, tw, 22);
    }
    ctx.fill();
    ctx.stroke();
    ctx.restore();

    // Badge text with vibrant arrow
    ctx.save();
    ctx.font = '700 11px "JetBrains Mono", monospace';
    ctx.fillStyle = 'rgb(' + curCol + ')';
    ctx.fillText(fullBadge, bx + 8, by + 15);
    ctx.restore();

    // --- Soft vignette edge fades so content in foreground stays crystal clear ---
    var fadeL = ctx.createLinearGradient(0, 0, Math.min(260, W * 0.22), 0);
    fadeL.addColorStop(0, 'rgba(4, 7, 17, 0.88)');
    fadeL.addColorStop(1, 'rgba(4, 7, 17, 0)');
    ctx.fillStyle = fadeL;
    ctx.fillRect(0, 0, Math.min(260, W * 0.22), H);

    var fadeB = ctx.createLinearGradient(0, H * 0.76, 0, H);
    fadeB.addColorStop(0, 'rgba(4, 7, 17, 0)');
    fadeB.addColorStop(1, 'rgba(4, 7, 17, 0.85)');
    ctx.fillStyle = fadeB;
    ctx.fillRect(0, H * 0.76, W, H * 0.24);
  }

  // --- Animation Loop ---
  // NOTE: candles are stamped with Date.now() (wall clock). requestAnimationFrame
  // hands out a timestamp measured from page load, so mixing the two made
  // `ts - candleStarted` a huge negative number: the forming candle never
  // closed and every candle melted into one endless bar. Everything below
  // therefore runs on ONE clock - Date.now().
  function loop() {
    if (hidden) {
      requestAnimationFrame(loop);
      return;
    }
    var now = Date.now();
    if (now - lastFrame >= frameBudget()) {
      lastFrame = now;
      tick(now);
      draw(now);
    }
    requestAnimationFrame(loop);
  }

  // A tab that was in the background (or a laptop that slept) can come back with
  // a big time gap - close the stale candle and re-anchor so the chart never
  // shows one enormous merged bar.
  function catchUp(now) {
    if (!cur) return;
    var age = now - candleStarted;
    if (age <= CANDLE_SEC * 1000) return;
    cur.c = price;
    cur.h = Math.max(cur.h, cur.o, cur.c);
    cur.l = Math.min(cur.l, cur.o, cur.c);
    cur.bull = cur.c >= cur.o;
    candles.push(cur);
    if (candles.length > MAX_CANDLES) candles.shift();
    startNewCandle(now);
  }

  // Small read-only probe (used by the dashboard diagnostics / tests): it shows
  // how many candles exist and how old the forming one is, which is exactly
  // what the old wall-clock bug used to break.
  window.candleBgDebug = function () {
    var lo = Infinity, hi = -Infinity;
    for (var i = Math.max(0, candles.length - SCALE_CANDLES); i < candles.length; i++) {
      if (candles[i].l < lo) lo = candles[i].l;
      if (candles[i].h > hi) hi = candles[i].h;
    }
    var allLo = Infinity, allHi = -Infinity, far = 0;
    for (var j = 0; j < candles.length; j++) {
      if (candles[j].l < allLo) allLo = candles[j].l;
      if (candles[j].h > allHi) allHi = candles[j].h;
      if (anchorPrice && Math.abs(candles[j].c - anchorPrice) / anchorPrice > 0.05) far++;
    }
    return {
      candles: candles.length,
      forming_age_ms: Math.max(0, Date.now() - candleStarted),
      candle_sec: CANDLE_SEC,
      price: price,
      anchor: anchorPrice,
      window_lo: lo,
      window_hi: hi,
      all_lo: allLo,
      all_hi: allHi,
      far_from_anchor: far,
      scale_lo: smoothLo,
      scale_hi: smoothHi,
      last: candles.slice(-4).map(function (x) {
        return [Math.round(x.o * 100) / 100, Math.round(x.h * 100) / 100,
                Math.round(x.l * 100) / 100, Math.round(x.c * 100) / 100];
      }),
      live: (Date.now() - lastWsTick) < 3500,
    };
  };

  // --- Boot Initialization ---
  resize();
  window.addEventListener('resize', resize);
  document.addEventListener('visibilitychange', function () {
    hidden = document.hidden;
  });

  if (reduced) {
    initMarketSeed(price);
    draw(Date.now());
    window.addEventListener('resize', function () { draw(Date.now()); });
    return;
  }

  initMarketSeed(price);
  connect();
  // a page restored from the bfcache / a slept machine gets a clean chart
  window.addEventListener('pageshow', function () { catchUp(Date.now()); });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) catchUp(Date.now());
  });
  requestAnimationFrame(loop);
})();
