/* ==========================================================================
   TradingView chart host (tv-chart.js)
   --------------------------------------------------------------------------
   The built-in candle chart is drawn by the browser from candles this server
   has to fetch first - on a busy shared host that first fetch is slow. This
   module puts TradingView's streaming chart in the same slot instead:

   - the embed is a plain iframe that TradingView's own CDN renders, so the
     page needs no third-party script and nothing can block it at load time,
   - our symbol tabs / timeframe tabs rewrite the frame's embed config, so the
     chart follows the market without touching the rest of the dashboard,
   - every market is mapped to a real TradingView symbol (exchange:SYMBOL),
   - a market TradingView has no symbol for keeps the built-in chart,
   - while TradingView is on screen the candle feed stops being asked for
     history (window.SOFT_TV_ACTIVE), so the host only serves the small
     ticker request the price ribbon needs.

   Nothing else changes: the ticker strip, pressure dials, whale tape and
   order-flow boards keep their own feeds and know nothing about this file.
   ========================================================================== */
(function () {
  'use strict';

  var STORAGE_KEY = 'softradex.chart.tv';   // '1' TradingView, '0' built-in
  var FRAME_BASE = 'https://www.tradingview-widget.com/embed-widget/advanced-chart/?locale=en#';

  function $(id) { return document.getElementById(id); }

  /* ---------------------------------------------------- symbol mapping --- */
  // Markets whose TradingView symbol cannot be derived from the broker name.
  var EXPLICIT = {
    // metals (USD quotes)
    XAUUSDm: 'OANDA:XAUUSD',
    XAGUSDm: 'OANDA:XAGUSD',
    XPTUSDm: 'OANDA:XPTUSD',
    XPDUSDm: 'OANDA:XPDUSD',
    // energy - continuous futures contracts
    USOILm: 'NYMEX:CL1!',
    UKOILm: 'ICEEUR:BRN1!',
    XNGUSDm: 'NYMEX:NG1!',
    // indices
    USTECm: 'NASDAQ:NDX',
    US500m: 'SP:SPX',
    US30m: 'DJ:DJI',
    DE30m: 'XETR:DAX',
    FR40m: 'EURONEXT:CAC',
    AUS200m: 'ASX:AXJO',
    // US stocks - qualified with the exchange they actually trade on
    AAPLm: 'NASDAQ:AAPL',
    MSFTm: 'NASDAQ:MSFT',
    AMZNm: 'NASDAQ:AMZN',
    GOOGLm: 'NASDAQ:GOOGL',
    TSLAm: 'NASDAQ:TSLA',
    NVDAm: 'NASDAQ:NVDA',
    METAm: 'NASDAQ:META',
    INTCm: 'NASDAQ:INTC',
    AMDm: 'NASDAQ:AMD',
    NEFLm: 'NASDAQ:NFLX',
    JPMm: 'NYSE:JPM',
    BAm: 'NYSE:BA',
    DISm: 'NYSE:DIS',
    NKEm: 'NYSE:NKE',
    COINm: 'NYSE:COIN',
    PFEm: 'NYSE:PFE',
    Vm: 'NYSE:V',
    MAm: 'NYSE:MA',
    KOm: 'NYSE:KO',
    WMTm: 'NYSE:WMT',
    XOMm: 'NYSE:XOM'
  };

  // Exotic crosses live on the IDC feed rather than on OANDA.
  var FOREX_EXOTIC = {
    USDSGD: 1, USDHKD: 1, USDTRY: 1, USDZAR: 1, USDMXN: 1, USDCNH: 1,
    USDSEK: 1, USDNOK: 1, USDDKK: 1, USDPLN: 1, USDTHB: 1, EURTRY: 1,
    EURSGD: 1
  };

  // Our timeframe buttons -> TradingView interval tokens.
  var TF_MAP = { '1m': '1', '5m': '5', '15m': '15', '1h': '60', '4h': '240', '1d': 'D' };

  // 9 MA + 21 MA + the MT5 ZigZag, drawn on every TradingView frame. The
  // widget takes studies from the same embed config as the symbol, so all
  // three follow the market/timeframe automatically.
  //
  // `inputs.length` is what sets each MA period: without it both studies load
  // with the default length (9) and look like a single line.
  //
  // ZigZag maps the MT5 parameters: period -> depth 12 and deviation 5 are
  // real inputs of TradingView's ZigZag (the widget legend shows "Zigzag 5
  // 12", so both are accepted). MT5's third parameter, backstep 3, has no
  // counterpart in TradingView's built-in study - it is sent for
  // completeness and ignored there rather than silently dropped here.
  var STUDIES = [
    { id: 'MASimple@tv-basicstudies', inputs: { length: 9 } },
    { id: 'MASimple@tv-basicstudies', inputs: { length: 21 } },
    { id: 'ZigZag@tv-basicstudies', inputs: { depth: 12, deviation: 5, backstep: 3 } }
  ];

  // Returns 'EXCHANGE:SYMBOL', or '' when TradingView has nothing for it -
  // the caller then leaves the built-in chart in place instead of showing a
  // TradingView "no data" error.
  function tvSymbolFor(key, meta) {
    var k = String(key || '');
    if (EXPLICIT[k]) return EXPLICIT[k];
    var kind = String((meta && meta.kind) || '').toLowerCase();
    // XAUUSDm -> XAUUSD, BTCUSDTm -> BTCUSDT, Vm -> V, ChinaHm -> ChinaH
    var base = k.replace(/m$/, '').toUpperCase();

    if (kind === 'crypto') {
      // BTCUSDT -> BTC, ETHUSD -> ETH, DOGEUSD -> DOGE
      var coin = base.replace(/USDT$/, '').replace(/USD$/, '');
      if (/^[A-Z0-9]{2,10}$/.test(coin)) return 'BINANCE:' + coin + 'USDT';
      return '';
    }
    if (kind === 'forex') {
      if (!/^[A-Z]{6}$/.test(base)) return '';
      return (FOREX_EXOTIC[base] ? 'FX_IDC:' : 'OANDA:') + base;
    }
    // metals quoted against EUR/GBP/AUD, HK50 / UK100 / JP225 and friends
    // have no entry we can trust -> '' = built-in chart stays.
    return '';
  }

  // The embed reads its whole configuration from the URL fragment, so a new
  // symbol/timeframe is just a new fragment: the frame keeps its slot and
  // only its content reloads.
  function frameSrc(symbol, interval) {
    return FRAME_BASE + encodeURIComponent(JSON.stringify({
      symbol: symbol,
      interval: interval,
      timezone: 'Etc/UTC',
      style: '1',                 // candles
      theme: 'light',
      studies: STUDIES,           // 9 MA and 21 MA overlay
      hide_side_toolbar: true,    // the dashboard already has the tabs
      allow_symbol_change: false,
      withdateranges: false,
      save_image: false,
      support_host: 'https://www.tradingview.com'
    }));
  }

  /* ------------------------------------------------------------ state ---- */
  var wrapper = null;        // .binance-chart-wrapper (flex column)
  var box = null;            // #tv-chart-box
  var frame = null;          // the embed <iframe>
  var btn = null;            // toolbar toggle

  var tvSymbol = 'OANDA:XAUUSD';
  var tvInterval = '5';
  var hasMapping = true;     // current market has a TradingView symbol
  var wantActive = true;     // user preference, persisted
  try { wantActive = localStorage.getItem(STORAGE_KEY) !== '0'; } catch (e) {}

  function mountWidget() {
    if (frame || !box) return;
    frame = document.createElement('iframe');
    frame.className = 'tv-chart-frame';
    frame.setAttribute('title', 'TradingView chart');
    frame.setAttribute('frameborder', '0');
    frame.setAttribute('allowtransparency', 'true');
    // TradingView refuses to draw when the embed arrives with a referrer it
    // does not like (an IP host such as http://192.168.x.x/ or
    // http://127.0.0.1:8787 shows "Something went wrong" inside the frame).
    // The chart is read-only, so nothing here needs a referrer.
    frame.setAttribute('referrerpolicy', 'no-referrer');
    applyFrame();
    var placeholder = box.querySelector('.tv-chart-placeholder');
    if (placeholder && placeholder.parentNode) placeholder.parentNode.removeChild(placeholder);
    box.appendChild(frame);
  }

  // Point the frame at the current symbol/timeframe - a no-op when it already
  // shows them, so a redraw of the dashboard does not reload the chart.
  function applyFrame() {
    if (!frame) return;
    var src = frameSrc(tvSymbol, tvInterval);
    if (frame.dataset.tv === src) return;
    frame.dataset.tv = src;
    frame.src = src;
  }

  function updateButton() {
    if (!btn) return;
    var show = wantActive && hasMapping;
    btn.classList.toggle('active', show);
    btn.classList.toggle('tv-na', wantActive && !hasMapping);
    var label = $('tv-toggle-text');
    if (label) {
      label.textContent = show ? 'TradingView · ON'
        : (wantActive ? 'TradingView · n/a' : 'TradingView · OFF');
    }
    if (btn.title) {
      btn.title = !hasMapping
        ? 'TradingView has no symbol for this market - the built-in chart stays'
        : 'Switch the candle chart between TradingView and the built-in chart';
    }
  }

  function applyVisibility() {
    var show = wantActive && hasMapping;
    if (wrapper) wrapper.classList.toggle('tv-mode', show);

    var wasActive = window.SOFT_TV_ACTIVE === true;
    window.SOFT_TV_ACTIVE = show;
    updateButton();

    if (show) {
      mountWidget();
      applyFrame();
    } else if (wasActive) {
      // TradingView just stopped drawing the candles - the built-in chart
      // needs one fresh snapshot (the feed skipped it while TV was on).
      window.dispatchEvent(new CustomEvent('chart:refresh'));
    }
  }

  /* ------------------------------------------------------- cross chip --- */
  // The hosted TradingView widget ignores style overrides (`studies_overrides`
  // changes nothing - checked pixel by pixel), so its two moving averages
  // cannot be recoloured from here. The crossover, though, is ours to compute:
  // MA9 vs MA21 from the same candle feed the built-in chart uses, shown as a
  // chip beside the chart toggle so the golden/death cross is readable at a
  // glance while the TradingView frame is on screen.
  var badge = null;
  var crossTimer = null;
  var crossSeq = 0;
  var ourSymbol = '';
  var ourTf = '';

  function smaAt(closes, i, n) {
    if (i < n - 1) return null;
    var sum = 0;
    for (var k = i - n + 1; k <= i; k++) sum += closes[k];
    return sum / n;
  }

  function ensureBadge() {
    if (badge && badge.isConnected) return badge;
    var anchor = $('tv-chart-toggle');
    if (!anchor || !anchor.parentNode) return null;
    badge = document.createElement('div');
    badge.className = 'tv-cross-badge pending';
    badge.id = 'tv-cross-badge';
    badge.title = 'MA 9 vs MA 21 crossover on the chart you are looking at';
    badge.innerHTML = '<span class="tv-cross-label">MA 9 / 21</span>' +
      '<span class="tv-cross-sub">reading candles&hellip;</span>';
    anchor.parentNode.insertBefore(badge, anchor.nextSibling);
    return badge;
  }

  // bars arrive as [time, open, high, low, close, volume]. Returns the current
  // side of the crossover plus the bar where it last flipped, or null when the
  // feed gave us too little history to average over.
  function analyseCross(bars) {
    if (!Array.isArray(bars)) return null;
    var closes = [], stamps = [];
    for (var i = 0; i < bars.length; i++) {
      var bar = bars[i];
      var close = Array.isArray(bar) ? bar[4] : Number(bar && bar.c);
      var time = Array.isArray(bar) ? bar[0] : Number(bar && bar.t);
      if (!isFinite(close)) return null;
      closes.push(close);
      stamps.push(time);
    }
    var last = closes.length - 1;
    if (last < 21) return null;

    function diff(i) {
      var fast = smaAt(closes, i, 9);
      var slow = smaAt(closes, i, 21);
      if (fast === null || slow === null) return 0;
      return fast - slow;
    }

    var now = diff(last);
    var crossAt = 0;
    for (var j = 21; j <= last; j++) {
      var d = diff(j), prev = diff(j - 1);
      if (prev !== 0 && d !== 0 && ((prev < 0 && d > 0) || (prev > 0 && d < 0))) {
        crossAt = stamps[j];   // the most recent flip inside the window
      }
    }
    return {
      golden: now > 0,
      crossAt: crossAt,
      ma9: smaAt(closes, last, 9),
      ma21: smaAt(closes, last, 21)
    };
  }

  function fmtPrice(value) {
    if (!isFinite(value)) return '--';
    return value >= 1000 ? value.toFixed(2) : value.toFixed(value < 10 ? 5 : 3);
  }

  function paintBadge(result) {
    var box = ensureBadge();
    if (!box || !result) return;
    box.classList.toggle('golden', !!result.golden);
    box.classList.toggle('death', !result.golden);
    box.classList.remove('pending');
    var label = box.querySelector('.tv-cross-label');
    if (label) label.textContent = result.golden ? 'GOLDEN CROSS' : 'DEATH CROSS';
    var sub = box.querySelector('.tv-cross-sub');
    if (sub) {
      var when = result.crossAt ? new Date(result.crossAt) : null;
      var hh = when ? ('0' + when.getUTCHours()).slice(-2) + ':' +
        ('0' + when.getUTCMinutes()).slice(-2) : '';
      sub.textContent = (hh ? 'crossed ' + hh + ' \u00b7 ' : '') +
        'MA9 ' + fmtPrice(result.ma9) + ' \u00b7 MA21 ' + fmtPrice(result.ma21);
    }
  }

  async function refreshCross() {
    var box = ensureBadge();
    if (!box) return;
    var term = window.SoftradeXTerminal;
    var sym = (term && typeof term.getCurrentSymbol === 'function')
      ? term.getCurrentSymbol() : ourSymbol;
    var tf = (term && typeof term.getInterval === 'function')
      ? term.getInterval() : ourTf;
    if (!sym || !tf) return;
    var token = ++crossSeq;
    try {
      var res = await fetch('/api/chart/candles?symbol=' + encodeURIComponent(sym) +
        '&tf=' + encodeURIComponent(tf) + '&count=60', { cache: 'no-store' });
      if (!res.ok || token !== crossSeq) return;
      var data = await res.json();
      if (token !== crossSeq) return;
      paintBadge(analyseCross(data && data.bars));
    } catch (e) {
      // keep the last label - a dropped request must not blank the chip
    }
  }

  function startCrossFeed() {
    ensureBadge();
    void refreshCross();
    if (crossTimer) clearInterval(crossTimer);
    // 60 bars every 20s is a few KB - the cross only changes on a new bar,
    // so anything faster would be traffic without information.
    crossTimer = setInterval(function () { void refreshCross(); }, 20000);
  }

  /* ------------------------------------------------------------- wiring -- */
  function init() {
    wrapper = document.querySelector('#binance-terminal-section .binance-chart-wrapper');
    box = $('tv-chart-box');
    btn = $('tv-chart-toggle');
    if (!wrapper || !box) return;

    if (btn) {
      btn.addEventListener('click', function () {
        wantActive = !wantActive;
        try { localStorage.setItem(STORAGE_KEY, wantActive ? '1' : '0'); } catch (e) {}
        applyVisibility();
      });
    }

    // The terminal announces every market change - map it and follow along.
    window.addEventListener('chart:market', function (ev) {
      var detail = ev.detail || {};
      var meta = detail.meta || {};
      var key = detail.symbol || meta.symbol || '';
      ourSymbol = key;
      void refreshCross();       // new market = new candles = new crossover
      var mapped = tvSymbolFor(key, meta);
      if (mapped) {
        tvSymbol = mapped;
        hasMapping = true;
      } else {
        hasMapping = false;
      }
      applyVisibility();
    });

    window.addEventListener('chart:interval', function (ev) {
      var tf = (ev.detail && ev.detail.tf) || '';
      ourTf = tf;
      void refreshCross();
      if (TF_MAP[tf]) {
        tvInterval = TF_MAP[tf];
        applyVisibility();
      }
    });

    applyVisibility();
    startCrossFeed();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Small surface for debugging from the browser console.
  window.TvChartHost = {
    symbolFor: tvSymbolFor,
    current: function () { return { symbol: tvSymbol, interval: tvInterval, active: window.SOFT_TV_ACTIVE === true }; }
  };
})();
