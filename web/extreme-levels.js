/* Draw Bot 3's five planned extreme prices over both chart views. */
(function () {
  'use strict';

  var setups = [];
  var setupSymbol = '';
  var range = null;
  var calculationTimer = null;
  var calculationToken = 0;
  var dynamicSymbol = '';

  function normalize(value) {
    return String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  }

  function matches(a, b) {
    var left = normalize(a);
    var right = normalize(b);
    return left && right && (left === right || left.replace(/M$/, '') === right.replace(/M$/, ''));
  }

  function priceText(value) {
    var number = Number(value);
    if (!isFinite(number)) return '--';
    return number >= 1000 ? number.toFixed(2) : number < 10 ? number.toFixed(5) : number.toFixed(3);
  }

  function weekKey(timestamp) {
    var date = new Date(Number(timestamp) * 1000);
    var day = date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate() - day + 1);
    return date.getUTCFullYear() + '-' + String(date.getUTCMonth() + 1).padStart(2, '0') + '-' + String(date.getUTCDate()).padStart(2, '0');
  }

  function dayHighLow(bars) {
    var high = -Infinity;
    var low = Infinity;
    bars.forEach(function (bar) {
      high = Math.max(high, Number(bar[2]));
      low = Math.min(low, Number(bar[3]));
    });
    return { high: high, low: low };
  }

  function calculateSetups(bars, symbol) {
    if (!Array.isArray(bars) || bars.length < 2) return;
    var valid = bars.filter(function (bar) {
      return Array.isArray(bar) && isFinite(Number(bar[2])) && isFinite(Number(bar[3]));
    });
    if (valid.length < 2) return;
    var current = valid[valid.length - 1];
    var currentWeek = weekKey(current[0]);
    var weekKeys = [];
    valid.forEach(function (bar) {
      var key = weekKey(bar[0]);
      if (weekKeys.indexOf(key) < 0) weekKeys.push(key);
    });
    var previousWeek = weekKeys.length > 1 ? weekKeys[weekKeys.length - 2] : '';
    var today = dayHighLow([current]);
    var thisWeek = dayHighLow(valid.filter(function (bar) { return weekKey(bar[0]) === currentWeek; }));
    var prevBars = valid.filter(function (bar) { return weekKey(bar[0]) === previousWeek; });
    var previous = prevBars.length ? dayHighLow(prevBars) : thisWeek;
    var price = Number(current[4]);
    var previousBuy = Math.abs(price - previous.low) < Math.abs(price - previous.high);
    setups = [
      { action: 'BUY', label: 'Today Lower Low (LL)', price: today.low },
      { action: 'SELL', label: 'Today Higher High (HH)', price: today.high },
      { action: 'BUY', label: 'This Week Lower Low (LL)', price: thisWeek.low },
      { action: 'SELL', label: 'This Week Higher High (HH)', price: thisWeek.high },
      { action: previousBuy ? 'BUY' : 'SELL', label: previousBuy ? 'Prev Week Low (Floor)' : 'Prev Week High (Ceiling)', price: previousBuy ? previous.low : previous.high }
    ];
    setupSymbol = symbol;
    dynamicSymbol = symbol;
    window.dispatchEvent(new CustomEvent('bot3:planned-trades', {
      detail: { symbol: symbol, planned: setups, source: 'chart-extreme-calculation' }
    }));
    render();
  }

  function refreshCalculation(symbol) {
    if (!symbol) return;
    var terminal = window.SoftradeXTerminal;
    var token = ++calculationToken;
    fetch('/api/chart/candles?symbol=' + encodeURIComponent(symbol) + '&tf=1d&count=60', { cache: 'no-store' })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (token !== calculationToken || !data || !Array.isArray(data.bars)) return;
        calculateSetups(data.bars, symbol);
      })
      .catch(function () { /* keep the last calculated levels */ });
    if (calculationTimer) clearInterval(calculationTimer);
    calculationTimer = setInterval(function () { refreshCalculation(symbol); }, 60000);
  }

  function ensureLayer(box) {
    if (!box) return null;
    var layer = box.querySelector('.extreme-levels-layer');
    if (!layer) {
      layer = document.createElement('div');
      layer.className = 'extreme-levels-layer';
      box.appendChild(layer);
    }
    return layer;
  }

  function render() {
    var terminal = window.SoftradeXTerminal;
    var chartSymbol = terminal && typeof terminal.getCurrentSymbol === 'function'
      ? terminal.getCurrentSymbol() : '';
    var validRange = range && isFinite(range.min) && isFinite(range.max) && range.max > range.min;
    var visible = validRange && matches(setupSymbol, chartSymbol);
    // Planned extreme levels belong to the built-in MT5-style chart only.
    // TradingView keeps its own native price scale and studies uncluttered.
    var boxes = [document.getElementById('binance-canvas-box')];

    boxes.forEach(function (box) {
      var layer = ensureLayer(box);
      if (!layer) return;
      if (!visible) {
        layer.innerHTML = '';
        return;
      }
      var height = Number(range.height) || box.clientHeight || 1;
      var html = setups.slice(0, 5).map(function (setup) {
        var price = Number(setup.price);
        if (!isFinite(price) || price <= 0) return '';
        var y = (range.max - price) / (range.max - range.min) * height;
        if (y < -8 || y > height + 8) return '';
        var isBuy = String(setup.action || '').toUpperCase() === 'BUY';
        var side = isBuy ? 'BUY' : 'SELL';
        return '<div class="extreme-price-line ' + (isBuy ? 'buy' : 'sell') + '" style="top:' + y.toFixed(2) + 'px">' +
          '<span class="extreme-price-label">' + side + ' · ' + priceText(price) + '</span>' +
          '</div>';
      }).join('');
      layer.innerHTML = html;
    });
  }

  window.addEventListener('bot3:planned-trades', function (event) {
    var detail = event.detail || {};
    if (detail.source !== 'chart-extreme-calculation' && dynamicSymbol && matches(dynamicSymbol, detail.symbol)) return;
    setups = Array.isArray(detail.planned) ? detail.planned : [];
    setupSymbol = detail.symbol || '';
    render();
  });

  window.addEventListener('chart:market', function (event) {
    var detail = event.detail || {};
    var symbol = detail.symbol || (detail.meta && detail.meta.symbol) || '';
    if (symbol && !matches(dynamicSymbol, symbol)) {
      setups = [];
      setupSymbol = symbol;
      render();
    }
    refreshCalculation(symbol);
  });

  window.addEventListener('chart:price-range', function (event) {
    range = event.detail || null;
    render();
  });

  window.addEventListener('resize', render);
  document.addEventListener('DOMContentLoaded', function () {
    render();
    setTimeout(function () {
      var terminal = window.SoftradeXTerminal;
      if (terminal && typeof terminal.getCurrentSymbol === 'function') {
        refreshCalculation(terminal.getCurrentSymbol());
      }
    }, 500);
  });
})();
