/**
 * Traders Order Flow Terminal
 * Real-time order execution tape & buyer/seller flow terminal.
 * Directly synchronized with Stage 01 Market (flow-market chips).
 * Features:
 * - Real-time Trade Entry (OPEN) and Trade Exit / Close (CLOSE, TP, SL) order flow
 * - Live Binance WebSocket Stream (AggTrade for Crypto & Gold)
 * - Micro-Liquidity Simulator with realistic Open & Close order flow
 * - Strict one-market isolation: the tape, stats & open-volume pools only ever
 *   contain the market selected in Stage 01 — nothing from any other market
 * - Own account trades (bot positions / history) are NEVER shown here
 * - Filter by ALL, OPEN, CLOSED, BUYS, SELLS, WHALES
 * - Velocity & delta analytics
 */
(function () {
  'use strict';

  // --- Configuration & Market Parameters ---
  const MARKET_PROFILES = {
    'BTC': { baseAsset: 'BTC', quote: 'USDT', decimals: 2, minQty: 0.005, maxQty: 3.5, unit: 'BTC', binancePair: 'btcusdt' },
    'ETH': { baseAsset: 'ETH', quote: 'USDT', decimals: 2, minQty: 0.1, maxQty: 25.0, unit: 'ETH', binancePair: 'ethusdt' },
    'SOL': { baseAsset: 'SOL', quote: 'USDT', decimals: 2, minQty: 1.0, maxQty: 180.0, unit: 'SOL', binancePair: 'solusdt' },
    'XAU': { baseAsset: 'GOLD', quote: 'USD', decimals: 2, minQty: 0.5, maxQty: 45.0, unit: 'oz', binancePair: 'paxgusdt' },
    'GOLD': { baseAsset: 'GOLD', quote: 'USD', decimals: 2, minQty: 0.5, maxQty: 45.0, unit: 'oz', binancePair: 'paxgusdt' },
    'EUR': { baseAsset: 'EUR', quote: 'USD', decimals: 5, minQty: 0.1, maxQty: 15.0, unit: 'Lots', binancePair: null },
    'GBP': { baseAsset: 'GBP', quote: 'USD', decimals: 5, minQty: 0.1, maxQty: 12.0, unit: 'Lots', binancePair: null },
    'JPY': { baseAsset: 'USD', quote: 'JPY', decimals: 3, minQty: 0.1, maxQty: 15.0, unit: 'Lots', binancePair: null },
    'OIL': { baseAsset: 'OIL', quote: 'USD', decimals: 2, minQty: 10.0, maxQty: 600.0, unit: 'bbl', binancePair: null },
    'TEC': { baseAsset: 'USTEC', quote: 'USD', decimals: 2, minQty: 0.2, maxQty: 10.0, unit: 'Lots', binancePair: null },
    'US30': { baseAsset: 'US30', quote: 'USD', decimals: 1, minQty: 0.2, maxQty: 8.0, unit: 'Lots', binancePair: null },
    'DXY': { baseAsset: 'DXY', quote: 'USD', decimals: 3, minQty: 0.5, maxQty: 20.0, unit: 'Lots', binancePair: null },
    'DSEX': { baseAsset: 'DSEX', quote: 'BDT', decimals: 2, minQty: 1.0, maxQty: 10000.0, unit: 'pts', binancePair: null },
    'DSE30': { baseAsset: 'DSE30', quote: 'BDT', decimals: 2, minQty: 1.0, maxQty: 10000.0, unit: 'pts', binancePair: null },
    'GP': { baseAsset: 'GP', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'BATBC': { baseAsset: 'BATBC', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'SQURPHARMA': { baseAsset: 'SQURPHARMA', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'BRACBANK': { baseAsset: 'BRACBANK', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'WALTONHPL': { baseAsset: 'WALTONHPL', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'RENATA': { baseAsset: 'RENATA', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'LHBL': { baseAsset: 'LHBL', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'UPGDCL': { baseAsset: 'UPGDCL', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'BEXIMCO': { baseAsset: 'BEXIMCO', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'CITYBANK': { baseAsset: 'CITYBANK', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'DUTCHBANGL': { baseAsset: 'DUTCHBANGL', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'ALARABANK': { baseAsset: 'ALARABANK', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'MTB': { baseAsset: 'MTB', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'DBH': { baseAsset: 'DBH', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'OLYMPIC': { baseAsset: 'OLYMPIC', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'ICB': { baseAsset: 'ICB', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'AMCLPRAN': { baseAsset: 'AMCLPRAN', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'NCCBANK': { baseAsset: 'NCCBANK', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'SOUTHEASTB': { baseAsset: 'SOUTHEASTB', quote: 'BDT', decimals: 2, minQty: 0.01, maxQty: 1000.0, unit: 'share', binancePair: null },
    'USDBDT': { baseAsset: 'USD', quote: 'BDT', decimals: 3, minQty: 1.0, maxQty: 100000.0, unit: 'rate', binancePair: null },
  };

  const TRADER_POOLS = [
    'Institutional LP', 'Citadel-Desk', 'Jump-Algo #2', 'Alpha-Quant', 'Whale Fund #7',
    'Exness LP', 'Retail Swift', 'Momentum Bot', 'Grid Master', 'HFT Flow #9',
    'Prime Brokerage', 'Arbitrage-X', 'Apex Trader', 'Liquidity Desk', 'Wintermute Flow'
  ];

  /* Fallback DSE company/bank roster used until bd-app.js publishes the full
   * catalogue on window.BD_COMPANIES. Keeps foreign brokers off the BD tape
   * even on the very first seed frame. */
  const BD_TRADER_FALLBACK = [
    'Grameenphone Ltd', 'BRAC Bank', 'Square Pharmaceuticals', 'BAT Bangladesh',
    'Walton Hi-Tech Industries', 'Renata Ltd', 'LafargeHolcim Bangladesh',
    'United Power Generation', 'Beximco Ltd', 'The City Bank',
    'Dutch-Bangla Bank', 'Al-Arafah Islami Bank', 'Mutual Trust Bank',
    'DBH Finance', 'Olympic Industries', 'Investment Corporation of Bangladesh',
    'AMCL (Pran)', 'NCC Bank', 'Southeast Bank'
  ];

  /* Global banks, brokers, prop desks, funds, central banks, exchanges,
   * retail brokers and corporates requested for the SoftradeX dashboard.
   * These are ADDED to the existing flow: the original desk names and the
   * BD company/bank roster above stay in the rotation. */
  const SOFTX_GLOBAL_TRADERS = `
Deutsche Bank
UBS
JPMorgan Chase
Citigroup
Goldman Sachs
Barclays
HSBC
Morgan Stanley
Bank of America
BNP Paribas
Société Générale
State Street
Wells Fargo
Standard Chartered
Royal Bank of Canada
Bank of Montreal
MUFG
Mizuho
Nomura
Sumitomo Mitsui Banking Corporation (SMBC)
ING
Commerzbank
Santander
BBVA
NatWest Markets
Lloyds Banking Group
Crédit Agricole
Crédit Mutuel
Rabobank
KBC Group
Danske Bank
Nordea
SEB
Swedbank
DNB
Skandinaviska Enskilda Banken
ABN AMRO
UniCredit
Intesa Sanpaolo
Erste Group
Raiffeisen Bank International
Bank of China
Industrial and Commercial Bank of China (ICBC)
China Construction Bank
Agricultural Bank of China
China Merchants Bank
Bank of Communications
China CITIC Bank
ANZ
Westpac
National Australia Bank
Commonwealth Bank of Australia
Macquarie Group
DBS Bank
OCBC Bank
United Overseas Bank (UOB)
Bank of Taiwan
Taiwan Cooperative Bank
Kookmin Bank
Shinhan Bank
Woori Bank
Hana Bank
State Bank of India
ICICI Bank
HDFC Bank
Axis Bank
Kotak Mahindra Bank
Qatar National Bank
Emirates NBD
First Abu Dhabi Bank
Saudi National Bank
Riyad Bank
Türkiye İş Bankası
Garanti BBVA
Bank of America Merrill Lynch
XTX Markets
Citadel Securities
Jane Street
Virtu Financial
Jump Trading
Hudson River Trading
Tower Research Capital
DRW
Optiver
Susquehanna International Group (SIG)
Flow Traders
GTS
Maven Securities
IMC Trading
Akuna Capital
Quantlab
Headlands Technologies
Global Trading Systems
Renaissance Technologies
TransMarket Group
Five Rings
Old Mission Capital
Wolverine Trading
Belvedere Trading
Geneva Trading
Da Vinci Trading
Squarepoint Capital
Qube Research & Technologies
Radix Trading
Valkyrie Trading
HRT Financial
Schonfeld Strategic Advisors
Mako Trading
WH Trading
CTC Trading
Peak6
Kershner Trading Group
Teza Technologies
Hudson Bay Capital Management
Bridgewater Associates
Brevan Howard
Millennium Management
Citadel
Point72 Asset Management
Man Group
AQR Capital Management
Two Sigma
D. E. Shaw
Winton Group
Tudor Investment Corporation
Caxton Associates
Rokos Capital Management
ExodusPoint Capital Management
Marshall Wace
Balyasny Asset Management
Elliott Investment Management
Davidson Kempner Capital Management
PIMCO
BlackRock
Vanguard
Fidelity Investments
Wellington Management
Invesco
Capital Group
Lone Pine Capital
Soros Fund Management
Pershing Square Capital Management
Verition Fund Management
BlueCrest Capital Management
Graham Capital Management
Aspect Capital
Man AHL
Systematica Investments
Transtrend
Campbell & Company
Chesapeake Capital
QMA
Arrowstreet Capital
Acadian Asset Management
Lazard Asset Management
Amundi
Schroders
Allianz Global Investors
BNY Investments
State Street Global Advisors
Federal Reserve
European Central Bank
Bank of Japan
Bank of England
Swiss National Bank
People's Bank of China
Reserve Bank of India
Bank of Canada
Reserve Bank of Australia
Reserve Bank of New Zealand
Monetary Authority of Singapore
Hong Kong Monetary Authority
Bank Indonesia
Bank Negara Malaysia
Bank of Korea
Bangladesh Bank
Bank of Thailand
Central Bank of Brazil
Banco de México
South African Reserve Bank
Central Bank of Türkiye
Saudi Central Bank
Central Bank of the UAE
Bank of Israel
Central Bank of Norway
Sveriges Riksbank
Danmarks Nationalbank
Bank of Russia
Central Bank of Nigeria
Central Bank of the Philippines
Central Bank of Sri Lanka
State Bank of Pakistan
Central Bank of Egypt
Central Bank of Chile
Central Bank of Colombia
Bank of Ghana
Bank of Uganda
Bank of Jamaica
Central Bank of Argentina
Bank of Algeria
TP ICAP
BGC Group
Tradition
StoneX
Finalto
Cboe FX
360T
FXall
LSEG FX Matching
EBS Market
CME Group
Bloomberg FXGO
BidFX
FX Connect
Currenex
LMAX Exchange
LMAX Global
Integral
smartTrade Technologies
FlexTrade Systems
Trading Technologies
FastMatch
ParFX
FXSpotStream
Thomson Reuters Dealing
NEX Markets
Traiana
CLS Group
LCH
ICE
Eurex
Nasdaq
Cboe Global Markets
Refinitiv
MarketAxess
Tradeweb
IG Group
OANDA
FOREX.com
CMC Markets
Saxo Bank
Interactive Brokers
Pepperstone
IC Markets
Exness
XM
FxPro
AvaTrade
Eightcap
Tickmill
Admirals
FXTM
Plus500
XTB
Swissquote
Dukascopy
eToro
City Index
Capital.com
Vantage Markets
FP Markets
BlackBull Markets
Axi
ThinkMarkets
Fusion Markets
FBS
RoboForex
HFM
PU Prime
VT Markets
Traders Trust
Libertex
Markets.com
ActivTrades
HYCM
Amarkets
Moneta Markets
TMGM
ATFX
Deriv
Saxo
CMC Spreadbetting
FOREX.com UK
Alpari
NordFX
InstaForex
Octa
Apple
Microsoft
Amazon
Alphabet
Toyota
Samsung
Volkswagen
BMW
Mercedes-Benz Group
Shell
BP
ExxonMobil
TotalEnergies
Coca-Cola
PepsiCo
Nestlé
Siemens
Airbus
Boeing
Alibaba
Tencent
Sony
Honda
Hyundai Motor
Ford Motor Company
General Motors
Nissan
Stellantis
TSMC
Intel
NVIDIA
Meta Platforms
Walmart
Costco
Procter & Gamble
Unilever
L'Oréal
Nike
Adidas
IKEA (Ingka Group)
Maersk
FedEx
UPS
Caterpillar
Schneider Electric
Rio Tinto
BHP
Glencore
Petrobras
Saudi Aramco
Equinor
Eni
Chevron
ConocoPhillips
Deere & Company
Lockheed Martin
RTX Corporation
Pfizer
Roche
Novartis
AstraZeneca
Bayer
Sanofi
Air Liquide
LVMH
Hermès
Inditex
H&M Group
BYD
Xiaomi
Huawei
Lenovo
Panasonic
LG Electronics
Reliance Industries
Tata Motors
Infosys
Wipro
Jardine Matheson
Grab Holdings
`.trim().split('\n').map((name) => name.trim()).filter(Boolean);

  /* ------------------------------------------------------------------ *
   * BD (DSE) company roster.
   *
   * On the BD Stock Market dashboard the tape must name the BD companies
   * that trade, so the TRADER / ROUTE column carries their real listed
   * names (Grameenphone Ltd, BAT Bangladesh, ...). bd-app.js publishes the
   * roster on ``window.BD_COMPANIES`` once the catalogue is loaded; the
   * Exness dashboard never sets it, so it keeps the desk names above.
   * ------------------------------------------------------------------ */
  function bdRoster() {
    const list = (typeof window !== 'undefined' && Array.isArray(window.BD_COMPANIES))
      ? window.BD_COMPANIES : [];
    return list
      .map((c) => (c && (c.name || c.label)) ? String(c.name || c.label) : '')
      .filter(Boolean);
  }

  function isBdMarket(sym) {
    const s = String(sym || '').toUpperCase().trim();
    if (!s) return false;
    const list = (typeof window !== 'undefined' && Array.isArray(window.BD_COMPANIES))
      ? window.BD_COMPANIES : [];
    if (list.some((c) => c && String(c.symbol || '').toUpperCase() === s)) return true;
    // Indices (DSEX / DSE30) and USDBDT are not scrips, but they are still BD
    // markets and must only ever show DSE companies / banks on the tape.
    const prof = getProfile(s);
    return String(prof.quote || '').toUpperCase() === 'BDT';
  }

  // The name pool that feeds the TRADER / ROUTE column for the active market.
  function traderPool() {
    if (!activeSymbol) return TRADER_POOLS;
    if (isBdMarket(activeSymbol)) {
      const bd = bdRoster();
      return bd.length ? bd : BD_TRADER_FALLBACK;
    }
    return TRADER_POOLS;
  }

  function pickTrader() {
    const pool = traderPool();
    const poolSize = pool.length;
    if (!poolSize) return 'Market Flow';
    if (isBdMarket(activeSymbol)) {
      /* BD (DSE) page: the tape must surface the whole listed company / bank
       * universe, not a random walk that can leave scrips invisible.  A
       * round-robin pointer keeps circulating through every BD name, so each
       * new OPEN ticket is guaranteed to eventually show all companies +
       * banks in trading order. */
      const key = String(activeSymbol || 'BD').toUpperCase();
      const ptr = bdTraderPointers.get(key) || 0;
      bdTraderPointers.set(key, (ptr + 1) % poolSize);
      return pool[ptr % poolSize];
    }
    /* SoftradeX main dashboard: keep the original trading desks flowing,
     * keep the BD companies/banks, and add the global bank / broker / fund /
     * central-bank / corporate roster. In every group of four OPEN tickets:
     *   slot 0 -> BD name, slot 2 -> global name, slots 1/3 -> original desk.
     * Both added rosters are round-robined, so every supplied name eventually
     * prints instead of being lost in a random draw. */
    softxPickPointer++;
    const slot = softxPickPointer % 4;
    if (slot === 0) {
      const bdPool = bdRoster().length ? bdRoster() : BD_TRADER_FALLBACK;
      return bdPool[softxBdPointer++ % bdPool.length];
    }
    if (slot === 2) {
      return SOFTX_GLOBAL_TRADERS[softxGlobalPointer++ % SOFTX_GLOBAL_TRADERS.length];
    }
    return pool[Math.floor(Math.random() * poolSize)];
  }

  let activeSymbol = '';
  let ws = null;
  let wsReconnectTimer = null;
  let wsRetries = 0;
  let activeFilter = 'all'; // 'all' | 'open' | 'close' | 'buy' | 'sell' | 'whale'
  let isPaused = false;
  let trades = [];
  let tradeTimestamps = []; // for speed calculation (last 3 sec)
  let buyVol = 0;
  let sellVol = 0;
  let buyCount = 0;
  let sellCount = 0;
  let openCount = 0;
  let closeCount = 0;
  let closeVol = 0;
  let realizedPnl = 0;
  let winCount = 0;
  let lossCount = 0;
  let netDelta = 0;
  let lastPrice = 0;
  let simTimer = null;
  let analysisTimer = null;
  let cycleTimer = null;
  let runningOffset = 0;
  let runningRotateAt = 0;
  let softxPickPointer = 0;
  let softxBdPointer = 0;
  let softxGlobalPointer = 0;
  let tapeScanTimer = null;
  let lastTapeUserScroll = 0;
  const bdTraderPointers = new Map();

  // Live Open Orders Pool (Only Active Open Trades: Buyer $ & Seller $)
  let openBuyVol = 0;
  let openSellVol = 0;
  let openBuyCount = 0;
  let openSellCount = 0;
  const activeOpenBuyTrades = [];  // pool of active open BUY trades
  const activeOpenSellTrades = []; // pool of active open SELL trades

  function $(id) {
    return document.getElementById(id);
  }

  function getProfile(sym) {
    const s = String(sym || '').toUpperCase();
    for (const key of Object.keys(MARKET_PROFILES)) {
      if (s.includes(key)) return { ...MARKET_PROFILES[key], symbol: sym };
    }
    return {
      baseAsset: sym.replace(/m$/i, '').toUpperCase(),
      quote: 'USD',
      decimals: 2,
      minQty: 0.1,
      maxQty: 10.0,
      unit: 'Lots',
      binancePair: null,
      symbol: sym
    };
  }

  function formatNum(num, dec = 2) {
    if (num === null || num === undefined || isNaN(num)) return '--';
    return Number(num).toLocaleString('en-US', {
      minimumFractionDigits: dec,
      maximumFractionDigits: dec
    });
  }

  function currencyMark(sym) {
    const s = String(sym || activeSymbol || '').toUpperCase();
    if (!s) return '$';
    return String(getProfile(s).quote || '').toUpperCase() === 'BDT' ? '৳' : '$';
  }

  function formatMoney(num) {
    const n = Math.abs(Number(num) || 0);
    const sign = Number(num) < 0 ? '-' : '';
    const mark = currencyMark(activeSymbol);
    if (n >= 1e9) return `${sign}${mark}${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `${sign}${mark}${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `${sign}${mark}${(n / 1e3).toFixed(1)}K`;
    return `${sign}${mark}${n.toFixed(0)}`;
  }

  function signedMoney(num) {
    const n = Number(num) || 0;
    if (n === 0) return formatMoney(0);
    return `${n > 0 ? '+' : ''}${formatMoney(n)}`;
  }

  function paintSigned(el, num) {
    if (!el) return;
    const n = Number(num) || 0;
    el.textContent = signedMoney(n);
    el.classList.toggle('pos', n > 0);
    el.classList.toggle('neg', n < 0);
  }

  function escHtml(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /* Mark-to-market P&L of one still-open position.  The live mark is the
   * latest tick for the selected market, so every running row grows or
   * shrinks with the tape instead of staying frozen at its entry price. */
  function openPnlFor(trade) {
    if (!trade) return 0;
    const entry = Number(trade.price) || 0;
    const amount = Number(trade.amount) || 0;
    if (!entry || !amount) return 0;
    /* On the BD page running positions are allowed to survive a market
     * switch, so each ticket is marked against its own scrip when possible;
     * otherwise it falls back to the active market's live tick. */
    let mark = Number(lastPrice) || entry;
    if (trade.market && trade.market !== activeSymbol) {
      const own = livePriceForSymbol(trade.market);
      if (own > 0) mark = own;
    }
    return (String(trade.side).toUpperCase() === 'BUY' ? (mark - entry) : (entry - mark)) * amount;
  }

  function openPnlTotal() {
    return activeOpenBuyTrades.concat(activeOpenSellTrades).reduce((sum, t) => sum + openPnlFor(t), 0);
  }

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  function formatTime(d = new Date()) {
    if (!(d instanceof Date) || isNaN(d.getTime())) d = new Date();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
  }

  // --- Get current Market 01 Symbol ---
  function getMarket01Symbol() {
    if (typeof window.flowSymbol === 'function') {
      const s = window.flowSymbol();
      if (s) return s;
    }
    const sel = $('flow-market');
    if (sel && sel.value) return sel.value;
    const lbl = $('flow-symbol-label');
    if (lbl && lbl.textContent && lbl.textContent !== '—') return lbl.textContent.trim();
    return 'BTCUSDTm';
  }

  // --- Live Open Orders Volume Sync & Tracking ---
  function syncOpenVolumes() {
    openBuyVol = activeOpenBuyTrades.reduce((sum, t) => sum + (Number(t.value) || 0), 0);
    openBuyCount = activeOpenBuyTrades.length;
    openSellVol = activeOpenSellTrades.reduce((sum, t) => sum + (Number(t.value) || 0), 0);
    openSellCount = activeOpenSellTrades.length;
  }

  function recordOpenTrade(trade) {
    if (trade.side === 'BUY') {
      activeOpenBuyTrades.push(trade);
    } else {
      activeOpenSellTrades.push(trade);
    }
    syncOpenVolumes();
  }

  function recordCloseTrade(trade) {
    const isBuy = trade.side === 'BUY';
    const pool = isBuy ? activeOpenBuyTrades : activeOpenSellTrades;
    let idx = -1;

    // 1. Try to find by explicit relatedOpenId
    if (trade.relatedOpenId) {
      idx = pool.findIndex(t => t.id === trade.relatedOpenId);
    }
    // 2. Try to find by position ticket or id pattern
    if (idx === -1 && trade.id) {
      const clean = String(trade.id).replace(/^(pos-close-|closed-toast-|hist-)/, '');
      idx = pool.findIndex(t => String(t.id).includes(clean));
    }
    // 3. FIFO fallback: pick from older open trades in the pool
    if (idx === -1 && pool.length > 0) {
      const maxPick = Math.max(1, Math.min(5, Math.floor(pool.length / 2)));
      idx = Math.floor(Math.random() * maxPick);
    }

    if (idx !== -1) {
      pool.splice(idx, 1);
    }
    syncOpenVolumes();
  }

  // --- Add a Live Trade to Tape ---
  function addTrade(trade) {
    if (!trade) return;

    // Normalize trade fields
    trade.action = String(trade.action || 'OPEN').toUpperCase();
    trade.closeType = String(trade.closeType || '').toUpperCase();
    trade.profit = (trade.profit !== undefined && trade.profit !== null && !isNaN(Number(trade.profit)))
      ? Number(trade.profit)
      : null;
    trade.time = trade.time || formatTime();
    trade.decimals = trade.decimals !== undefined ? trade.decimals : 2;
    trade.unit = trade.unit || 'Lots';
    trade.price = Number(trade.price) || 0;
    trade.amount = Number(trade.amount) || 0;
    trade.value = Number(trade.value) || (trade.price * trade.amount) || 0;
    trade.ts = Number(trade.ts) || Date.now();
    trade.side = String(trade.side || 'BUY').toUpperCase();
    trade.market = activeSymbol || trade.market || '';

    const now = Date.now();
    tradeTimestamps.push(now);
    // Keep timestamps within last 3 seconds for velocity
    const cutoff = now - 3000;
    while (tradeTimestamps.length && tradeTimestamps[0] < cutoff) {
      tradeTimestamps.shift();
    }

    // Accumulate Stats
    if (trade.side === 'BUY') {
      buyVol += trade.value;
      buyCount++;
      netDelta += trade.value;
    } else {
      sellVol += trade.value;
      sellCount++;
      netDelta -= trade.value;
    }

    if (trade.action === 'CLOSE') {
      closeCount++;
      closeVol += (trade.profit !== null ? Math.abs(trade.profit) : trade.value);
      if (trade.profit !== null) {
        realizedPnl += trade.profit;
        if (trade.profit >= 0) winCount++;
        else lossCount++;
      }
      recordCloseTrade(trade);
    } else {
      openCount++;
      recordOpenTrade(trade);
    }

    trades.unshift(trade);
    if (trades.length > 250) trades.pop();

    updateStatsBar();

    if (isPaused) return;

    // Check filter match
    if (!matchesFilter(trade)) return;

    renderRow(trade, true);
  }

  function matchesFilter(trade) {
    if (activeFilter === 'all') return true;
    if (activeFilter === 'open') return trade.action === 'OPEN';
    if (activeFilter === 'close') return trade.action === 'CLOSE';
    if (activeFilter === 'buy') return trade.side === 'BUY';
    if (activeFilter === 'sell') return trade.side === 'SELL';
    if (activeFilter === 'whale') return !!trade.isWhale;
    return false;
  }

  function renderRow(trade, prepend = false) {
    const tapeBodies = document.querySelectorAll('.tt-tape-body');
    if (!tapeBodies || !tapeBodies.length) return;

    const isBuy = trade.side === 'BUY';
    const isClose = trade.action === 'CLOSE';
    const sideClass = isBuy ? 'buy' : 'sell';
    const closeRowClass = isClose ? 'close-trade' : 'open-trade';
    const whaleClass = trade.isWhale ? 'whale-trade' : '';
    const ourClass = trade.isOurAccount ? 'our-trade' : '';

    const pnlClass = trade.profit !== null
      ? (trade.profit >= 0 ? 'win' : 'loss')
      : '';

    const actionType = isClose
      ? (trade.closeType ? trade.closeType.toLowerCase() : 'close')
      : 'open';

    const actionBadgeClass = isClose
      ? `close ${actionType} ${pnlClass}`.trim()
      : 'open';

    const actionText = isClose
      ? (trade.closeType ? `✕ ${trade.closeType}` : '✕ CLOSE')
      : '● OPEN';

    // The richer BD tape labels are scoped to bd.html only: the Exness
    // dashboard keeps its original compact VALUE cell untouched.
    const isBdDashboard = document.body && document.body.classList.contains('bd-dashboard');

    // BD shares / index points need two decimals, not the four-decimal crypto
    // quantity format: 8,237.1863 pts was overflowing the SIZE column.
    const compactAmountUnit = isBdDashboard && ['pts', 'share'].includes(String(trade.unit || ''));
    const amountDecimals = compactAmountUnit ? 2 : (trade.decimals <= 2 ? 4 : 2);

    let totalHtml = '';
    if (isClose && trade.profit !== null) {
      const sign = trade.profit >= 0 ? '+' : '';
      totalHtml = isBdDashboard
        ? `<b class="${pnlClass}">${sign}${formatMoney(trade.profit)}</b><small class="tt-total-kind">P&amp;L</small>`
        : `<b class="${pnlClass}">${sign}${formatMoney(trade.profit)}</b>`;
    } else {
      totalHtml = isBdDashboard
        ? `<b>${formatMoney(trade.value)}</b><small class="tt-total-kind">ORDER</small>`
        : formatMoney(trade.value);
    }

    const htmlContent = `
      <span class="tt-cell-time">${trade.time}</span>
      <span class="tt-cell-side">
        <span class="tt-side-badge ${sideClass}">${isBuy ? '▲ BUY' : '▼ SELL'}</span>
      </span>
      <span class="tt-cell-action">
        <span class="tt-action-badge ${actionBadgeClass}" title="${isClose ? `Position Closed: ${trade.closeType || 'EXIT'}${trade.profit !== null ? ` (${trade.profit >= 0 ? '+' : ''}${formatMoney(trade.profit)})` : ''}` : 'Position Opened / Trade Entry'}">${actionText}</span>
      </span>
      <span class="tt-cell-price ${sideClass}">${formatNum(trade.price, trade.decimals)}</span>
      <span class="tt-cell-amount">${trade.isWhale ? '🐋 ' : ''}${formatNum(trade.amount, amountDecimals)} <i class="tt-unit">${trade.unit}</i></span>
      <span class="tt-cell-total">${totalHtml}</span>
      <span class="tt-cell-trader" title="${trade.trader}">${trade.trader}</span>
    `;

    tapeBodies.forEach(tapeBody => {
      const row = document.createElement('div');
      row.className = `tt-row ${sideClass} ${closeRowClass} ${whaleClass} ${ourClass}`;
      row.innerHTML = htmlContent;

      if (prepend && tapeBody.firstChild) {
        tapeBody.insertBefore(row, tapeBody.firstChild);
      } else {
        tapeBody.appendChild(row);
      }

      // Cap DOM children to 40 for ultra-fluid rendering
      while (tapeBody.children.length > 40) {
        tapeBody.removeChild(tapeBody.lastChild);
      }
    });
  }

  function runningPositions() {
    return activeOpenBuyTrades.concat(activeOpenSellTrades);
  }

  function renderRunningPositions(list) {
    const bodies = document.querySelectorAll('#tt-running-body, #tt-sidebar-running-body');
    if (!bodies.length) return;

    const all = Array.isArray(list) ? list : runningPositions();
    if (!all.length) {
      const empty = '<div class="tt-run-empty">No running positions yet. A new OPEN order will appear here and stay visible until it is closed.</div>';
      bodies.forEach(body => { body.innerHTML = empty; });
      return;
    }

    const sorted = all.slice().sort((a, b) => (Number(b.ts) || 0) - (Number(a.ts) || 0));
    const limit = 12;
    const poolSize = sorted.length;
    const rotating = poolSize > limit;
    const base = rotating ? (runningOffset % poolSize) : 0;
    const windowed = rotating
      ? sorted.slice(base).concat(sorted.slice(0, base)).slice(0, limit)
      : sorted;

    const rowsHtml = windowed.map(trade => {
      const isBuy = String(trade.side).toUpperCase() === 'BUY';
      const sideClass = isBuy ? 'buy' : 'sell';
      const pnl = openPnlFor(trade);
      const base = Number(trade.value) || (Number(trade.price) * Number(trade.amount)) || 0;
      const pct = base > 0 ? (pnl / base) * 100 : 0;
      const ownMark = (trade.market && trade.market !== activeSymbol) ? livePriceForSymbol(trade.market) : 0;
      const mark = ownMark > 0 ? ownMark : (Number(lastPrice) || Number(trade.price) || 0);
      const dec = Number(trade.decimals) || 2;
      const amount = Number(trade.amount) || 0;
      const entry = Number(trade.price) || 0;
      const pnlClass = pnl >= 0 ? 'pos' : 'neg';
      const pctClass = pct >= 0 ? 'pos' : 'neg';
      const sign = pct >= 0 ? '+' : '';

      return `
        <div class="tt-run-row ${sideClass}" title="Order value ${formatMoney(trade.value)} · Running P&amp;L ${signedMoney(pnl)}">
          <span class="tt-run-side ${sideClass}">${isBuy ? 'BUY' : 'SELL'}</span>
          <span class="tt-run-main">
            <b>${escHtml(trade.trader || 'Market Flow')}</b>
            <small>${escHtml(trade.time)} · ${formatNum(amount, 2)} ${escHtml(trade.unit)} @ ${formatNum(entry, dec)} · ${formatMoney(trade.value)}</small>
          </span>
          <span class="tt-run-mark">
            <b>${formatNum(mark, dec)}</b>
            <small>mark</small>
          </span>
          <span class="tt-run-pnl ${pnlClass}">
            <b>${signedMoney(pnl)}</b>
            <small class="${pctClass}">${sign}${Math.abs(pct).toFixed(2)}%</small>
          </span>
        </div>
      `;
    }).join('') + (rotating
      ? `<div class="tt-run-more">+${poolSize - limit} more running position${poolSize - limit > 1 ? 's' : ''}…</div>`
      : '');

    bodies.forEach(body => { body.innerHTML = rowsHtml; });
  }

  function updateAnalysisStats() {
    /* The running panel is a rotating window: when there are more open
     * tickets than fit, the view slowly walks through both the previously
     * opened and the freshly opened running positions. */
    if (Date.now() >= runningRotateAt) {
      runningRotateAt = Date.now() + 6500;
      const openTotal = runningPositions().length;
      if (openTotal > 12) {
        runningOffset = (runningOffset + 1) % openTotal;
      }
    }

    const openAll = runningPositions();
    const openOnlyValue = openBuyVol + openSellVol;
    const openValue = openAll.reduce((sum, t) => sum + (Number(t.value) || 0), 0);
    const unrealized = openAll.reduce((sum, t) => sum + openPnlFor(t), 0);
    const turnover = buyVol + sellVol;
    const closed = winCount + lossCount;
    const winRate = closed ? Math.round((winCount / closed) * 100) : null;

    document.querySelectorAll('.tt-open-count-val').forEach(el => {
      el.textContent = String(openAll.length);
    });
    document.querySelectorAll('.tt-realized-val').forEach(el => paintSigned(el, realizedPnl));
    document.querySelectorAll('.tt-unrealized-val').forEach(el => paintSigned(el, unrealized));
    document.querySelectorAll('.tt-winrate-val').forEach(el => {
      el.textContent = winRate === null ? '—' : `${winRate}%`;
    });

    setText('tt-kpi-order', formatMoney(openOnlyValue));
    setText('tt-kpi-order-sub', `BUY ${formatMoney(openBuyVol)} · SELL ${formatMoney(openSellVol)}`);
    paintSigned($('tt-kpi-realized'), realizedPnl);
    setText('tt-kpi-realized-sub', `${winCount}W / ${lossCount}L closed`);
    paintSigned($('tt-kpi-unrealized'), unrealized);
    setText('tt-kpi-winrate', winRate === null ? '—' : `${winRate}%`);
    setText('tt-kpi-winloss', `${winCount}W / ${lossCount}L`);
    setText('tt-running-count', String(openAll.length));
    setText('tt-running-total', `${formatMoney(openValue)} open`);
    setText('tt-sidebar-running-count', String(openAll.length));
    setText('tt-sidebar-running-total', `${formatMoney(openValue)} open`);

    renderRunningPositions(openAll);
  }

  function updateStatsBar() {
    const totalVol = (buyVol + sellVol) || 1;
    const buyPct = Math.round((buyVol / totalVol) * 100);
    const sellPct = 100 - buyPct;
    /* Running totals shown inside the terminals: only positions that are still
     * OPEN count. Close / TP / SL tickets never add to these amounts. */
    const openBookVol = openBuyVol + openSellVol;
    const openBuyPct = openBookVol > 0 ? Math.round((openBuyVol / openBookVol) * 100) : 50;
    const openSellPct = 100 - openBuyPct;
    const txPerSec = (tradeTimestamps.length / 3).toFixed(1);
    const isPos = netDelta >= 0;

    // Format Open Buyer and Open Seller Volumes
    const buyOpenFormatted = formatMoney(openBuyVol);
    const sellOpenFormatted = formatMoney(openSellVol);

    // Update Topbar Open Volume Chips
    const elBuyOpen = $('b-open-buy-val');
    if (elBuyOpen) {
      if (elBuyOpen.textContent !== buyOpenFormatted) {
        const isUp = openBuyVol >= (elBuyOpen._prevVal || 0);
        elBuyOpen.textContent = buyOpenFormatted;
        elBuyOpen._prevVal = openBuyVol;
        elBuyOpen.classList.remove('flash-up', 'flash-down');
        void elBuyOpen.offsetWidth;
        elBuyOpen.classList.add(isUp ? 'flash-up' : 'flash-down');
        setTimeout(() => elBuyOpen.classList.remove('flash-up', 'flash-down'), 250);
      }
    }
    const elSellOpen = $('b-open-sell-val');
    if (elSellOpen) {
      if (elSellOpen.textContent !== sellOpenFormatted) {
        const isUp = openSellVol >= (elSellOpen._prevVal || 0);
        elSellOpen.textContent = sellOpenFormatted;
        elSellOpen._prevVal = openSellVol;
        elSellOpen.classList.remove('flash-up', 'flash-down');
        void elSellOpen.offsetWidth;
        elSellOpen.classList.add(isUp ? 'flash-up' : 'flash-down');
        setTimeout(() => elSellOpen.classList.remove('flash-up', 'flash-down'), 250);
      }
    }

    document.querySelectorAll('.b-open-buy-val').forEach(el => {
      if (el !== elBuyOpen) el.textContent = buyOpenFormatted;
    });
    document.querySelectorAll('.b-open-sell-val').forEach(el => {
      if (el !== elSellOpen) el.textContent = sellOpenFormatted;
    });

    const buyChip = $('b-open-buy-chip');
    if (buyChip) {
      buyChip.title = `Active Open Buyers: ${buyOpenFormatted} (${openBuyCount} open positions)\nDecreases on Close / TP / SL`;
    }
    const sellChip = $('b-open-sell-chip');
    if (sellChip) {
      sellChip.title = `Active Open Sellers: ${sellOpenFormatted} (${openSellCount} open positions)\nDecreases on Close / TP / SL`;
    }

    // Update all matching elements across all terminal instances
    document.querySelectorAll('.tt-buy-vol-val').forEach(el => {
      el.textContent = `${openBuyPct}% (${formatMoney(openBuyVol)})`;
    });
    document.querySelectorAll('.tt-sell-vol-val').forEach(el => {
      el.textContent = `${openSellPct}% (${formatMoney(openSellVol)})`;
    });
    document.querySelectorAll('.tt-close-stat-val').forEach(el => {
      el.textContent = `${closeCount} tx (${formatMoney(closeVol)})`;
    });
    document.querySelectorAll('.tt-speed-val').forEach(el => {
      el.textContent = `${txPerSec} tx/s`;
    });
    document.querySelectorAll('.tt-meter-buy').forEach(el => {
      el.style.width = `${openBuyPct}%`;
    });
    document.querySelectorAll('.tt-meter-sell').forEach(el => {
      el.style.width = `${openSellPct}%`;
    });
    document.querySelectorAll('.tt-foot-net-delta').forEach(el => {
      el.innerHTML = `DELTA: <b class="${isPos ? 'pos' : 'neg'}">${isPos ? '+' : ''}${formatMoney(netDelta)}</b>`;
    });

    const statsPayload = {
      symbol: activeSymbol,
      buyVol,
      sellVol,
      openBuyVol,
      openSellVol,
      openBuyCount,
      openSellCount,
      totalVol,
      buyPct,
      sellPct,
      buyCount,
      sellCount,
      closeCount,
      closeVol,
      openCount,
      netDelta,
      speed: txPerSec,
      lastPrice
    };

    // Bridge to Market Pressure Watch Dials
    if (typeof window.applyOrderFlowToWatchDials === 'function') {
      try { window.applyOrderFlowToWatchDials(statsPayload); } catch (e) {}
    }

    // Bridge to Full AI Market Analysis
    if (typeof window.applyOrderFlowToAiMarket === 'function') {
      try { window.applyOrderFlowToAiMarket(statsPayload); } catch (e) {}
    }

    updateAnalysisStats();
  }

  function reRenderAllRows() {
    const tapeBodies = document.querySelectorAll('.tt-tape-body');
    tapeBodies.forEach(tb => { tb.innerHTML = ''; });
    const filtered = trades.filter(matchesFilter).slice(0, 40);
    filtered.slice().reverse().forEach(tr => renderRow(tr, true));
  }

  // --- SoftradeX Live WebSocket Stream (When Crypto/Gold) ---
  function connectBinanceWs(binancePair) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      wsRetries = 0;
    }
    if (ws) {
      try { ws.close(); } catch (e) {}
      ws = null;
    }
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    if (!binancePair) return;

    const pair = binancePair.toLowerCase();
    const url = `wss://stream.binance.com:9443/ws/${pair}@aggTrade`;

    try {
      ws = new WebSocket(url);
      ws.onopen = () => {
        wsRetries = 0;
        document.querySelectorAll('.tt-status-text').forEach(statText => {
          statText.textContent = `Connected to SoftradeX Global Tape · ${pair.toUpperCase()} · Live WebSocket`;
        });
        document.querySelectorAll('.tt-pulse-dot').forEach(pulse => {
          pulse.className = 'tt-pulse-dot live';
        });
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          const price = parseFloat(msg.p);
          const qty = parseFloat(msg.q);
          const isSell = msg.m; // buyer is maker means taker was seller
          const side = isSell ? 'SELL' : 'BUY';
          const prof = getProfile(activeSymbol);
          const notional = price * qty;
          const isWhale = notional >= (prof.unit === 'BTC' ? 40000 : 25000);

          lastPrice = price;

          const pool = side === 'BUY' ? activeOpenBuyTrades : activeOpenSellTrades;
          const totalPool = activeOpenBuyTrades.length + activeOpenSellTrades.length;
          let closeProb = 0.32;
          if (totalPool < 8) closeProb = 0.10;
          else if (totalPool > 35) closeProb = 0.60;

          const isClose = (pool.length > 0) && (Math.random() < closeProb);
          let relatedOpenId = null;
          let closeType = '';
          let traderName = '';

          if (isClose) {
            const pickIdx = Math.floor(Math.random() * Math.min(pool.length, 5));
            const openT = pool[pickIdx];
            if (openT) {
              relatedOpenId = openT.id;
              const pDiff = side === 'BUY' ? (price - openT.price) : (openT.price - price);
              closeType = pDiff >= 0 ? 'TP' : 'EXIT';
              traderName = `${(openT.trader || 'Binance Flow').replace(/\s*\[.*\]$/, '')} [${closeType}]`;
            } else {
              closeType = Math.random() > 0.45 ? 'TP' : 'EXIT';
              const randomTrader = pickTrader();
              traderName = `${randomTrader} [${closeType}]`;
            }
          } else {
            const randomTrader = pickTrader();
            traderName = randomTrader;
          }

          const action = isClose ? 'CLOSE' : 'OPEN';

          addTrade({
            id: msg.a || (Date.now() + Math.random()),
            relatedOpenId,
            ts: Number(msg.T) || Date.now(),
            time: formatTime(new Date(msg.T || Date.now())),
            side,
            action,
            closeType,
            profit: null,
            price,
            amount: qty,
            value: notional,
            unit: prof.unit,
            decimals: prof.decimals,
            trader: traderName,
            isWhale,
            isOurAccount: false
          });
        } catch (e) {}
      };

      ws.onerror = () => {
        if (ws) { try { ws.close(); } catch (e) {} }
      };

      ws.onclose = () => {
        // The exchange feed is a best-effort extra (some networks block it).
        // Back off instead of retrying every 2 s forever: the local simulator
        // keeps the terminal alive meanwhile, so nothing looks frozen.
        wsRetries = Math.min(wsRetries + 1, 6);
        const wait = Math.min(2000 * Math.pow(1.7, wsRetries - 1), 30000);
        wsReconnectTimer = setTimeout(() => {
          if (activeSymbol && getProfile(activeSymbol).binancePair) {
            connectBinanceWs(getProfile(activeSymbol).binancePair);
          }
        }, wait);
      };
    } catch (e) {}
  }

  // --- Real-time Micro-Liquidity Simulator (For Forex/Oil/CFDs or MT5 ticks) ---
  function startSimulatedLiquidity() {
    if (simTimer) clearInterval(simTimer);

    simTimer = setInterval(() => {
      // If Binance WS is streaming for this pair, skip simulated ticks
      const prof = getProfile(activeSymbol);
      if (prof.binancePair && ws && ws.readyState === WebSocket.OPEN) return;

      // Only the selected market's price feeds this tape (never another market)
      let price = lastPrice;

      if (!price || price <= 0) {
        price = defaultPriceFor(activeSymbol);
      }

      // Small jitter around current price
      const tickSpread = (price * 0.00012);

      // Dynamic realistic mix between OPEN and CLOSE
      const totalOpen = activeOpenBuyTrades.length + activeOpenSellTrades.length;
      let closeProb = 0.35;
      if (totalOpen < 10) closeProb = 0.12;
      else if (totalOpen > 35) closeProb = 0.65;

      const canClose = (activeOpenBuyTrades.length > 0 || activeOpenSellTrades.length > 0);
      const isClose = canClose && (Math.random() < closeProb);
      const action = isClose ? 'CLOSE' : 'OPEN';

      let isBuy = true;
      let openTradeToClose = null;

      if (isClose) {
        if (activeOpenBuyTrades.length > 0 && activeOpenSellTrades.length > 0) {
          isBuy = Math.random() < (activeOpenBuyTrades.length / totalOpen);
        } else if (activeOpenBuyTrades.length > 0) {
          isBuy = true;
        } else {
          isBuy = false;
        }
        const pool = isBuy ? activeOpenBuyTrades : activeOpenSellTrades;
        const pickIdx = Math.floor(Math.random() * Math.min(pool.length, 6));
        openTradeToClose = pool[pickIdx];
      } else {
        isBuy = Math.random() > 0.48;
      }

      const executedPrice = isBuy
        ? price + (Math.random() * tickSpread * 0.5)
        : price - (Math.random() * tickSpread * 0.5);

      let amount = 0;
      let value = 0;
      let isWhale = false;
      let closeType = '';
      let simProfit = null;
      let trader = '';
      let relatedOpenId = null;

      if (isClose && openTradeToClose) {
        amount = openTradeToClose.amount;
        value = openTradeToClose.value || (executedPrice * amount);
        isWhale = !!openTradeToClose.isWhale;
        const priceDiff = isBuy
          ? (executedPrice - openTradeToClose.price)
          : (openTradeToClose.price - executedPrice);
        simProfit = priceDiff * amount;
        closeType = simProfit >= 0 ? 'TP' : (Math.random() > 0.5 ? 'SL' : 'EXIT');
        const baseName = (openTradeToClose.trader || 'Algo-Flow').replace(/\s*\[.*\]$/, '');
        trader = `${baseName} [${closeType}]`;
        relatedOpenId = openTradeToClose.id;
      } else {
        const qtyRange = prof.maxQty - prof.minQty;
        const u = Math.random();
        amount = Number((prof.minQty + (qtyRange * Math.pow(u, 3))).toFixed(prof.decimals <= 2 ? 4 : 2));
        value = executedPrice * amount;
        isWhale = u > 0.92;
        const baseTrader = isWhale
          ? (isBdMarket(activeSymbol) ? pickTrader() : 'Whale Liquidity Desk')
          : pickTrader();
        trader = baseTrader;
      }

      lastPrice = executedPrice;

      addTrade({
        id: Date.now() + Math.random(),
        relatedOpenId,
        ts: Date.now(),
        time: formatTime(),
        side: isBuy ? 'BUY' : 'SELL',
        action,
        closeType,
        profit: simProfit,
        price: executedPrice,
        amount,
        value,
        unit: prof.unit,
        decimals: prof.decimals,
        trader,
        isWhale,
        isOurAccount: false
      });
    }, Math.floor(Math.random() * 350) + 250);
  }

  // --- Reasonable default price per market (used until a live tick arrives) ---
  function defaultPriceFor(sym) {
    if (!sym) return lastPrice;
    if (sym.includes('XAU')) return 4165.20;
    if (sym.includes('BTC')) return 81450.0;
    if (sym.includes('ETH')) return 2648.50;
    if (sym.includes('EUR')) return 1.08420;
    if (sym.includes('GBP')) return 1.29140;
    if (sym.includes('JPY')) return 152.420;
    if (sym.includes('OIL')) return 74.50;
    if (sym.includes('DSEX')) return 5820.0;
    if (sym.includes('DSE30')) return 2210.0;
    if (sym.includes('GP')) return 316.5;
    if (sym.includes('BATBC')) return 382.0;
    if (sym.includes('SQURPHARMA')) return 205.4;
    if (sym.includes('BRACBANK')) return 58.3;
    if (sym.includes('WALTONHPL')) return 1180.0;
    if (sym.includes('RENATA')) return 878.0;
    if (sym.includes('LHBL')) return 74.6;
    if (sym.includes('UPGDCL')) return 152.0;
    if (sym.includes('BEXIMCO')) return 91.5;
    if (sym.includes('CITYBANK')) return 24.3;
    if (sym.includes('DUTCHBANGL')) return 48.7;
    if (sym.includes('ALARABANK')) return 21.6;
    if (sym.includes('MTB')) return 19.4;
    if (sym.includes('DBH')) return 75.4;
    if (sym.includes('OLYMPIC')) return 105.0;
    if (sym.includes('ICB')) return 162.5;
    if (sym.includes('AMCLPRAN')) return 158.0;
    if (sym.includes('NCCBANK')) return 15.2;
    if (sym.includes('SOUTHEASTB')) return 14.9;
    if (sym.includes('USDBDT')) return 122.6;
    return 100.0;
  }

  // Live price of a market taken straight from the shared dashboard state
  // (S.market_watch) — guarantees a right-symbol price on every market switch.
  function livePriceForSymbol(sym) {
    try {
      const S = window.S;
      const list = (S && Array.isArray(S.market_watch)) ? S.market_watch : [];
      const up = String(sym || '').toUpperCase().replace(/M$/, '').trim();
      for (const w of list) {
        const wsym = String(w.symbol || '').toUpperCase().replace(/M$/, '').trim();
        if (wsym === up) {
          const p = Number(w.ask || w.bid || 0);
          if (p > 0) return p;
        }
      }
    } catch (e) {}
    return 0;
  }

  // --- External hook when real MT5 ticks of THE SELECTED MARKET arrive ---
  // The tape is market flow only: own-account trades are never shown here.
  function onPriceTick(sym, price, bid, ask) {
    if (!sym || !activeSymbol) return;
    if (sym.toUpperCase() !== activeSymbol.toUpperCase()) return;
    if (price && price > 0) lastPrice = price;
  }

  // --- External hook when detectNewClosed catches a closed trade ---
  // Own account trades are intentionally not part of the Traders Order Flow
  // Terminal (they live in the positions drawer / history drawer instead).
  function onTradeClosed(closedDeal) {
    return; // no-op by design
  }

  // --- Switch Connected Market (hard reset: old market never leaks through) ---
  function switchMarket(newSym, force) {
    if (!newSym) return;
    const cleanSym = String(newSym).trim();
    if (!force && cleanSym === activeSymbol && trades.length > 0) return;
    const wasSameSym = cleanSym === activeSymbol;

    activeSymbol = cleanSym;
    const prof = getProfile(cleanSym);

    // Fresh price for THIS market — never reuse the previous market's price
    const freshPrice = livePriceForSymbol(cleanSym) || (wasSameSym ? lastPrice : 0);

    // Update UI Badge across all panels (main panel + sidebar panel)
    document.querySelectorAll('#tt-connected-symbol, .tt-connected-symbol').forEach(badgeSym => {
      badgeSym.textContent = cleanSym.toUpperCase();
    });

    document.querySelectorAll('.tt-status-text').forEach(statText => {
      statText.textContent = `Syncing to Market: ${cleanSym.toUpperCase()}...`;
    });

    // Seed Initial Realistic Open Trades on Market Connect
    function seedInitialTrades(sym, basePrice) {
      const pProfile = getProfile(sym);
      const pPrice = Number(basePrice) || defaultPriceFor(sym);

      const seedDefs = [
        { side: 'BUY', trader: 'Momentum Bot', factor: 0.35, secAgo: 72 },
        { side: 'BUY', trader: 'Exness LP', factor: 0.08, secAgo: 65 },
        { side: 'BUY', trader: 'Grid Master', factor: 0.22, secAgo: 58 },
        { side: 'SELL', trader: 'HFT Flow #9', factor: 0.15, secAgo: 52 },
        { side: 'BUY', trader: 'Exness LP', factor: 0.40, secAgo: 45 },
        { side: 'BUY', trader: 'Arbitrage-X', factor: 0.55, secAgo: 38 },
        { side: 'BUY', trader: 'Whale Fund #7', factor: 1.85, secAgo: 30, isWhale: true },
        { side: 'SELL', trader: 'Wintermute Flow', factor: 0.25, secAgo: 24 },
        { side: 'SELL', trader: 'Citadel-Desk', factor: 0.38, secAgo: 18 },
        { side: 'SELL', trader: 'Apex Trader', factor: 0.12, secAgo: 12 },
        { side: 'BUY', trader: 'Retail Swift', factor: 0.60, secAgo: 6 }
      ];
      const useBdRoster = isBdMarket(sym);
      /* Previous running positions seeded on entry: on the SoftradeX dashboard
       * they carry the existing desk names plus BD companies / banks and a
       * sample of the newly supplied global names, so all three populations
       * are visible from the first frame. The global pointer is shared with
       * live flow, so the tape continues through the list without immediately
       * repeating the seeded names. */
      const bdSeedPool = bdRoster().length ? bdRoster() : BD_TRADER_FALLBACK;
      const seedTraderKinds = [
        'global', 'bd', 'global', 'desk', 'bd', 'global',
        'desk', 'bd', 'global', 'desk', 'global'
      ];
      let bdSeedPointer = 0;
      let seedGlobalPointer = 0;
      let seedIndex = 0;

      const now = Date.now();
      for (const def of seedDefs) {
        const tSpread = (pPrice * 0.0001);
        const isB = def.side === 'BUY';
        const p = isB ? (pPrice - Math.random() * tSpread) : (pPrice + Math.random() * tSpread);
        const qty = Number((pProfile.minQty + (pProfile.maxQty - pProfile.minQty) * (def.factor * 0.12)).toFixed(pProfile.decimals <= 2 ? 4 : 2)) || pProfile.minQty;
        const val = p * qty;
        const tradeTime = formatTime(new Date(now - def.secAgo * 1000));

        addTrade({
          id: 'seed-' + Math.random().toString(36).substring(2, 9),
          ts: now - def.secAgo * 1000,
          time: tradeTime,
          side: def.side,
          action: 'OPEN',
          closeType: '',
          profit: null,
          price: p,
          amount: qty,
          value: val,
          unit: pProfile.unit,
          decimals: pProfile.decimals,
          trader: useBdRoster
            ? pickTrader()
            : (seedTraderKinds[seedIndex] === 'global'
                ? SOFTX_GLOBAL_TRADERS[seedGlobalPointer++ % SOFTX_GLOBAL_TRADERS.length]
                : (seedTraderKinds[seedIndex] === 'bd'
                    ? bdSeedPool[bdSeedPointer++ % bdSeedPool.length]
                    : def.trader)),
          isWhale: !!def.isWhale,
          isOurAccount: false
        });
        seedIndex++;
      }

      /* Live flow continues right after the names sampled into the previous
       * running positions, so the seeded global names are not repeated first. */
      if (!useBdRoster) softxGlobalPointer = seedGlobalPointer;
    }

    // Reset EVERYTHING for the new market so no other-market trace remains,
    // except on the BD page, where a combined DSE order book is expected:
    // the previous running positions must stay visible next to the new ones.
    const bdPage = document.body && document.body.classList.contains('bd-dashboard');
    trades = [];
    buyVol = 0;
    sellVol = 0;
    buyCount = 0;
    sellCount = 0;
    closeCount = 0;
    closeVol = 0;
    realizedPnl = 0;
    winCount = 0;
    lossCount = 0;
    openCount = 0;
    netDelta = 0;
    tradeTimestamps = [];
    if (bdPage) {
      /* Running positions survive a scrip switch on this page so the flowing
       * DSE book keeps showing older + freshly opened tickets side by side. */
    } else {
      activeOpenBuyTrades.length = 0;
      activeOpenSellTrades.length = 0;
      openBuyVol = 0;
      openSellVol = 0;
      openBuyCount = 0;
      openSellCount = 0;
    }
    runningOffset = 0;
    runningRotateAt = Date.now() + 6500;
    lastPrice = freshPrice;

    document.querySelectorAll('.tt-tape-body').forEach(tapeBody => {
      tapeBody.innerHTML = `
        <div class="tt-divider-row">
          <span>⚡ CONNECTED TO MARKET: <b>${cleanSym.toUpperCase()}</b> · STREAMING ORDER FLOW (OPEN & CLOSE)</span>
        </div>
      `;
    });

    seedInitialTrades(cleanSym, lastPrice);
    updateStatsBar();

    // Check for Binance pair counterpart
    if (prof.binancePair) {
      connectBinanceWs(prof.binancePair);
    } else {
      if (ws) {
        try { ws.close(); } catch (e) {}
        ws = null;
      }
      if (wsReconnectTimer) {
        clearTimeout(wsReconnectTimer);
        wsReconnectTimer = null;
      }
      document.querySelectorAll('.tt-status-text').forEach(statText => {
        statText.textContent = `Connected to Market: ${cleanSym.toUpperCase()} · Live Liquidity Stream`;
      });
    }

    startSimulatedLiquidity();
  }

  // --- Initialize Terminal UI & Event Listeners ---
  // --- Auto-scan the live tape on the SoftradeX dashboard ---
  // The terminal should reveal both the previously running tickets that were
  // already open when the dashboard was entered and the positions opened live
  // afterwards. The SoftradeX page has no separate running-position panel, so
  // the tape itself gently walks through its rows and loops back to the top.
  function startTapeAutoScan() {
    if (tapeScanTimer) return;
    const bodies = Array.from(document.querySelectorAll('.tt-tape-body'));
    if (!bodies.length) return;

    bodies.forEach(body => {
      body.addEventListener('wheel', () => { lastTapeUserScroll = Date.now(); }, { passive: true });
      body.addEventListener('pointerdown', () => { lastTapeUserScroll = Date.now(); });
      body.addEventListener('touchstart', () => { lastTapeUserScroll = Date.now(); }, { passive: true });
    });

    tapeScanTimer = setInterval(() => {
      if (document.hidden) return;
      // Do not fight a user who is reading / scrolling the tape.
      if (Date.now() - lastTapeUserScroll < 12000) return;
      document.querySelectorAll('.tt-tape-body').forEach(body => {
        if (body.scrollHeight <= body.clientHeight + 24) return;
        const maxTop = body.scrollHeight - body.clientHeight;
        if (body.scrollTop >= maxTop - 8) {
          body.scrollTo({ top: 0, behavior: 'smooth' });
        } else {
          body.scrollTo({
            top: Math.min(maxTop, body.scrollTop + Math.max(110, body.clientHeight * 0.66)),
            behavior: 'smooth'
          });
        }
      });
    }, 8000);
  }

  function init() {
    const panels = document.querySelectorAll('.traders-terminal-panel');
    if (!panels || !panels.length) return;

    panels.forEach(panel => {
      // Filter Buttons
      const filterBtns = panel.querySelectorAll('.tt-filter-btn');
      filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          activeFilter = btn.dataset.filter || 'all';
          document.querySelectorAll('.tt-filter-btn').forEach(b => {
            b.classList.toggle('active', (b.dataset.filter || 'all') === activeFilter);
          });
          reRenderAllRows();
        });
      });

      // Pause / Resume Button
      const pauseBtn = panel.querySelector('.tt-pause-btn');
      if (pauseBtn) {
        pauseBtn.addEventListener('click', () => {
          isPaused = !isPaused;
          document.querySelectorAll('.tt-pause-btn').forEach(pBtn => {
            pBtn.textContent = isPaused ? '▶' : '⏸';
            pBtn.title = isPaused ? 'Resume Live Stream' : 'Pause Live Stream';
            pBtn.classList.toggle('paused', isPaused);
          });
          if (!isPaused) reRenderAllRows();
        });
      }

      // Clear Button
      const clearBtn = panel.querySelector('.tt-clear-btn');
      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          trades = [];
          buyVol = 0;
          sellVol = 0;
          buyCount = 0;
          sellCount = 0;
          closeCount = 0;
          closeVol = 0;
          realizedPnl = 0;
          winCount = 0;
          lossCount = 0;
          openCount = 0;
          netDelta = 0;
          tradeTimestamps = [];
          activeOpenBuyTrades.length = 0;
          activeOpenSellTrades.length = 0;
          openBuyVol = 0;
          openSellVol = 0;
          openBuyCount = 0;
          openSellCount = 0;
          document.querySelectorAll('.tt-tape-body').forEach(tb => { tb.innerHTML = ''; });
          updateStatsBar();
        });
      }

      // Sidebar terminal: switch between the live tape and the live running
      // positions. The main Stage 04 terminal shows both side by side.
      const viewTabs = panel.querySelectorAll('.tt-view-tab');
      if (viewTabs.length) {
        viewTabs.forEach(tab => {
          tab.addEventListener('click', () => {
            const view = tab.dataset.ttView || 'tape';
            viewTabs.forEach(t => {
              const on = t === tab;
              t.classList.toggle('active', on);
              t.setAttribute('aria-selected', on ? 'true' : 'false');
            });
            panel.querySelectorAll('.tt-sidebar-pane').forEach(pane => {
              pane.hidden = (pane.dataset.ttPane || '') !== view;
            });
          });
        });
      }
    });

    // Listen to Market 01 change event on #flow-market dropdown
    const flowSelect = $('flow-market');
    if (flowSelect) {
      flowSelect.addEventListener('change', (e) => {
        switchMarket(e.target.value);
      });
    }

    // Interactive Topbar Chips: Click to filter BUYS / SELLS
    const buyChip = $('b-open-buy-chip');
    if (buyChip) {
      buyChip.addEventListener('click', () => {
        activeFilter = (activeFilter === 'buy') ? 'all' : 'buy';
        document.querySelectorAll('.tt-filter-btn').forEach(b => {
          b.classList.toggle('active', (b.dataset.filter || 'all') === activeFilter);
        });
        reRenderAllRows();
      });
    }

    const sellChip = $('b-open-sell-chip');
    if (sellChip) {
      sellChip.addEventListener('click', () => {
        activeFilter = (activeFilter === 'sell') ? 'all' : 'sell';
        document.querySelectorAll('.tt-filter-btn').forEach(b => {
          b.classList.toggle('active', (b.dataset.filter || 'all') === activeFilter);
        });
        reRenderAllRows();
      });
    }

    // Initial Market Sync
    const initialSym = getMarket01Symbol();
    switchMarket(initialSym);

    // SoftradeX dashboard: let the tape cycle through the already-running
    // tickets and the ones opened live. The BD page keeps its dedicated
    // running-positions panel, so it is intentionally not auto-scrolled here.
    if (!document.body.classList.contains('bd-dashboard')) {
      startTapeAutoScan();
    }

    // Keep mark-to-market P&L alive between ticks: the running-position panel
    // and the realized/running KPI cards update even when no new tape row lands.
    if (analysisTimer) clearInterval(analysisTimer);
    analysisTimer = setInterval(updateAnalysisStats, 700);

    // Continuous Watchdog: check every 800ms if Market 01 changed
    setInterval(() => {
      const curSym = getMarket01Symbol();
      if (curSym && curSym.toUpperCase() !== activeSymbol.toUpperCase()) {
        switchMarket(curSym);
      }
    }, 800);
  }

  // Export API globally
  window.TradersTerminal = {
    switchMarket,
    addTrade,
    onPriceTick,
    onSnapshot: (snap) => {},          // legacy hook — account data is never shown
    onTradeClosed,                      // no-op by design (own trades stay out)
    getActiveSymbol: () => activeSymbol,
    getOpenVolume: () => ({ openBuyVol, openSellVol, openBuyCount, openSellCount }),
    getAnalysisStats: () => ({
      realizedPnl,
      unrealizedPnl: openPnlTotal(),
      winCount,
      lossCount,
      openCount: activeOpenBuyTrades.length + activeOpenSellTrades.length,
      turnover: buyVol + sellVol,
      lastPrice
    }),
    getRunningPositions: () => runningPositions().map(t => ({
      id: t.id,
      side: t.side,
      trader: t.trader,
      price: t.price,
      amount: t.amount,
      value: t.value,
      mark: Number(lastPrice) || t.price,
      pnl: openPnlFor(t),
      ts: t.ts
    }))
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    setTimeout(init, 150);
  }
})();
