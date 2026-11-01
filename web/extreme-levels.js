/* Draw Bot 3's five planned extreme prices over both chart views. */
(function () {
  'use strict';

  var setups = [];
  var setupSymbol = '';
  var range = null;

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
    var boxes = [document.getElementById('binance-canvas-box'), document.getElementById('tv-chart-box')];

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
    setups = Array.isArray(detail.planned) ? detail.planned : [];
    setupSymbol = detail.symbol || '';
    render();
  });

  window.addEventListener('chart:price-range', function (event) {
    range = event.detail || null;
    render();
  });

  window.addEventListener('resize', render);
  document.addEventListener('DOMContentLoaded', render);
})();
