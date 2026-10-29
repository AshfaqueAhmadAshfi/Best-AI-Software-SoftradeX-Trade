/* ==========================================================================
   Exness Trading Dashboard - frontend
   Plain JS (no build step) + WebSocket. Only changed DOM nodes are touched,
   so updates stay smooth even at 10 messages/second.
   ========================================================================== */
'use strict';

/* ------------------------------------------------------------- utilities */
const $ = (id) => document.getElementById(id);
const el = (sel, root = document) => root.querySelector(sel);

const nfLocale = (v, d = 2) => {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
};
// Fast twin of nfLocale: toLocaleString costs ~0.25ms per call and the dials,
// ticker and curve axes call nf() several hundred times per second. Same
// output - grouped thousands, exactly d decimals - built with string maths.
const nf = (v, d = 2) => {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const neg = n < 0 || Object.is(n, -0);   // Intl renders -0 as "-0.00"
  let s = Math.abs(n).toString();
  if (s.indexOf('e') !== -1 || s.indexOf('E') !== -1) {
    // extreme magnitudes fall back to the locale formatter (same as before)
    return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  const dot = s.indexOf('.');
  let ip = dot === -1 ? s : s.slice(0, dot);
  let fp = dot === -1 ? '' : s.slice(dot + 1);
  if (fp.length > d) {
    // half-expand on the shortest round-trip decimal - exactly what
    // Intl.NumberFormat does (1.005 -> "1.01", not toFixed's "1.00")
    let digits = ip + fp.slice(0, d);
    if (fp.charCodeAt(d) - 48 >= 5) {
      const arr = digits.split('');
      let i = arr.length - 1;
      while (i >= 0) {
        if (arr[i] === '9') { arr[i] = '0'; i--; }
        else { arr[i] = String.fromCharCode(arr[i].charCodeAt(0) + 1); break; }
      }
      if (i < 0) arr.unshift('1');
      digits = arr.join('');
    }
    const intLen = digits.length - d;
    ip = intLen > 0 ? digits.slice(0, intLen) : '0';
    fp = d > 0 ? (intLen > 0 ? digits.slice(intLen) : digits.padStart(d, '0')) : '';
  } else {
    fp = fp.padEnd(d, '0');
  }
  const frac = d > 0 ? '.' + fp : '';
  let out = '';
  for (let i = 0; i < ip.length; i++) {
    if (i > 0 && (ip.length - i) % 3 === 0) out += ',';
    out += ip[i];
  }
  return (neg ? '-' : '') + out + frac;
};
const signed = (v, d = 2) => {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return (n > 0 ? '+' : '') + nf(n, d);
};
const fmtPrice = (v) => {
  if (v === null || v === undefined || v === '' || Number(v) === 0) return '—';
  const n = Math.abs(Number(v));
  if (n >= 1000) return nf(v, 2);
  if (n >= 100) return nf(v, 2);
  if (n >= 10) return nf(v, 3);
  return nf(v, 5);
};
const pad = (n) => String(n).padStart(2, '0');
// MT5 epochs are "server clock" values -> render them in UTC so they match the terminal
const dtUtc = (ts) => new Date(Number(ts) * 1000);
const timeStr = (ts) => {
  if (!ts) return '—';
  const d = dtUtc(ts);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
};
const dateStr = (ts) => {
  if (!ts) return '—';
  const d = dtUtc(ts);
  const today = new Date();
  const sameDay = d.getUTCFullYear() === today.getUTCFullYear() && d.getUTCMonth() === today.getUTCMonth() && d.getUTCDate() === today.getUTCDate();
  return `${sameDay ? '' : pad(d.getUTCDate()) + '/' + pad(d.getUTCMonth() + 1) + ' '}${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
};
const durStr = (a, b) => {
  if (!a || !b || b < a) return '—';
  let s = b - a;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
};
const valClass = (v) => (Number(v) > 0 ? 'value-win' : Number(v) < 0 ? 'value-loss' : 'flat-color');
const rowClass = (v) => (Number(v) > 0 ? 'row-win' : Number(v) < 0 ? 'row-loss' : '');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------------------------------------------------------------- i18n */
const I18N = {
  en: {
    serverTime: 'Server time', disconnected: 'Disconnected', connected: 'Connected',
    connecting: 'Connecting…', errorState: 'Error', account: 'Account', balance: 'Balance',
    equity: 'Equity', margin: 'Margin', freeMargin: 'Free margin', marginLevel: 'Margin level',
    leverage: 'Leverage', unlimited: 'Unlimited', today: 'Today', session: 'Session', totalProfitToday: 'Total profit today',
    wins: 'Profitable trades', losses: 'Losing trades', winRate: 'Win rate', floating: 'Floating P/L',
    profitFactor: 'Profit factor', bestWorst: 'Best / Worst', volume: 'Lots traded',
    openPositions: 'Open positions', symbol: 'Symbol', side: 'Side', lots: 'Lots',
    openPrice: 'Open', currentPrice: 'Current', sl: 'S/L', tp: 'T/P', swap: 'Swap',
    profit: 'P/L', opened: 'Opened', noPositions: 'No open positions right now.',
    activity: 'Activity', tradeHistory: 'Trade history', allTime: 'All time', allResults: 'All results',
    winsOnly: 'Wins only', lossesOnly: 'Losses only', exportCsv: 'Export CSV', closeTime: 'Close time',
    closePrice: 'Close', commission: 'Comm.', netPL: 'Net P/L', magic: 'Magic', comment: 'Comment',
    duration: 'Duration', noHistory: 'No closed trades in this range yet.', loadMore: 'Load more',
    settingsTitle: 'Settings', pollInterval: 'Live refresh (ms)', historyRefresh: 'History sync (ms)',
    historyDays: 'History depth (days)', save: 'Save', close: 'Close', disconnect: 'Disconnect',
    connectTitle: 'Exness Account Login', connect: 'Login',
    viewDashboard: 'View Dashboard',
    login: 'Login (account number)', password: 'Password', server: 'Server',
    serverPick: 'Please choose your Exness server…',
    serverReal: 'Exness Real accounts',
    serverTrial: 'Exness Demo / Trial accounts',
    serverOther: 'Other server (type it manually)…',
    serverHint: 'Choose your Exness server from the list.',
    serverHintCustom: 'Type the exact server name, e.g. Exness-MT5Real6',
    needLogin: 'Enter your Exness account number',
    mt5Ready: 'MT5 terminal ready:',
    mt5ReadySub: 'the dashboard starts it and logs in for you — no need to open it.',
    mt5NoExness: 'MT5 terminal found (any MT5 build can log in to Exness).',
    mt5Missing: 'No MetaTrader 5 terminal found on this PC.',
    mt5Download: 'Exness MT5 →',
    mt5DownloadPlain: 'MetaTrader 5 →',
    mt5Recheck: 'Re-check',
    marketLiveTicker: 'MARKET LIVE',
    liveRatesTitle: 'LIVE MARKET RATES',
    realtimeStreaming: '24/7 STREAM',
    priceCurvesTitle: 'Market Price Curves',
    priceCurvesSub: 'Real-time price curve & live WebSocket stream',
    watchTitle: 'Market Pressure Watch Dials',
    watchSubtitle: 'Live candle volume & buyer/seller pressure gauge (streamed - no refresh timer)',
    flowLive: 'LIVE',
    flowStepMarket: 'Market', flowStepMarketHint: 'pick one market - every stage below follows it',
    flowHeroEyebrow: 'START HERE · SELECT YOUR MARKET',
    flowSignalSub: 'the AI is still reading the flow',
    flowCurveTitle: 'Live curve', flowCurveSub: 'selected market · tick by tick',
    flowStepDial: 'Pressure dial', flowStepDialHint: 'only the market you selected',
    flowStepSetups: 'Next setups', flowStepSetupsHint: 'the next 5 extreme executions, recalculated every 60 s',
    flowStepNews: 'Next event', flowStepNewsHint: 'impact level + countdown to the release',
    flowStepSessions: 'Sessions', flowStepSessionsHint: 'live clocks, buy/sell bias & dollar forecast',
    flowStepAi: 'Full AI analysis', flowStepAiHint: '1m · 5m · 15m · 1h · 4h · 1Day candles + dials + news',
    flowStepOutlook: 'AI direction · next day', flowStepOutlookHint: 'every news source read into one verdict per market',
    flowStepHistory: 'History', flowStepHistoryHint: 'every closed trade - where the flow ended',
    flowStepPerf: 'Performance', flowStepPerfHint: 'today · 7D · 30D · 90D · this session',
    railPositions: 'Positions', railBots: 'All bots',
    flowNoDial: 'No order-flow quote for this market yet - pick another market or connect the terminal.',
    flowNoDialShort: 'Connect the terminal to fill this dial.',
    flowMarketOpen: 'Market open', flowMarketClosed: 'Market closed', flowNoQuote: 'No quote',
    flowClosedTrades: 'closed trades', flowNoSession: 'no session open',
    sessNoneRunning: 'No session is running right now - the next one opens soon.',
    botTitle: 'Auto Trading', botLot: 'Lot size', botGap: 'Gap ($)', botSL: 'SL ($)',
    bot2Title: 'Auto Trading (Candle & Structure)', bot2MaxTrades: 'Max Trades', bot2TP: 'TP ($)', bot2SL: 'SL ($)',
    bot2BlockStart: 'First block (s)', bot2BlockEnd: 'Last block (s)', bot2BlockMidFrom: 'Mid from (s)', bot2BlockMidTo: 'Mid to (s)',
    bot2BlockHint: '1-minute candle time-window: no trade in the first N seconds, the last N seconds, or the middle window - values you enter are used exactly.',
    bot2Trend: 'Watch',
    bot2Watch: 'Watch',
    bot2WatchPct: 'Watch %',
    bot2WatchHint: "Watch ON: trades only when that side's dial pressure is at or above the Watch % you enter — nothing is fixed at 60%. Watch OFF: the dial is ignored and the bot follows the candle (candle up → BUY, candle down → SELL), one trade every 2.5s. Reverse flips whichever direction it would take.",
    bot2Reverse: 'Reverse',
    bot2CheckTrend: 'Check Direction',
    bot2TrendSystemTitle: '2.5s Direction System',
    bot2TrendDetailsFooter: '⏱️ Automatic 2.5-second direction check: trades in the direction the switch allows',
    bot2StructLevels: 'Structure Levels (Day & Weekend)',
    bot3Title: 'Auto Trading (Extreme High & Low Structure)',
    bot3StructLevels: 'Extreme Structure Levels (Today, This Week & Prev Week)',
    bot3TfToday: 'Today (D1)',
    bot3TfThisWeek: 'This Week (W1)',
    bot3TfPrevWeek: 'Prev Week',
    bot3RadarTitle: 'Trade Execution Radar & Timer',
    bot3PlannedTitle: 'Next 5 Extreme Execution Setups (Upcoming Trades)',
    bot3PlannedCycle: '⚡ 60s Auto Calculation',
    bot3PlannedFooter: '🔄 Automatic 60-second recalculation: tracking live D1, W1 & Prev W1 extreme levels',
    recalcNow: 'Recalc',
    bot4Title: 'Auto Trading (Pre-Trade Ladder)',
    bot4PriceGap: 'Price Gap ($)',
    bot4PreGap: 'Pre Trade Gap ($)',
    bot4PreValue: 'Pre Trade Value',
    bot4Hint: 'Pre Trade Value = levels on each side (N buy above + N sell below). Price Gap = distance from the market price to the first pre-trade, Pre Trade Gap = distance between two pre-trades. Numbering Lot Size ON → lots auto-number 0.01, 0.02, 0.03 … on both sides.',
    bot4Numbering: 'Numbering Lot Size',
    bot4LadderTitle: 'Pre-Trade Ladder (Buy above / Sell below)',
    bot4BuyCol: 'BUY ↑ pre-trades above market',
    bot4SellCol: 'SELL ↓ pre-trades below market',
    botSymbol: 'Market', botPickSymbol: 'Pick a gold symbol…',
    botMarketSwitched: 'Market switched to', botMarketNow: 'Selected market',
    marketClosed: 'Market Closed',
    marketClosedWarn: 'This market is currently closed',
    marketClosedCannotStart: 'Cannot start bot: Market is currently closed',
    condTitle: 'Conditions (what the bot does)',
    condSymbol: 'Trades only this symbol', condMissing: 'not on this account',
    condInstead: 'not on this account — using this instead',
    condGap: 'Gap', condSL: 'Buy SL sits on the SellStop level, Sell SL on the BuyStop level —',
    condLot: 'Lot', condLock: 'SL lock: profit', condSL2: 'SL only ever moves forward - if profit falls back it stays where it was.',
    condTrail: 'Trailing: from profit',
    condReArm: 'If the market hits the stop-loss: the old buy + sell are cleared completely, then a fresh BuyStop and SellStop are placed from the current market price.',
    botStart: 'START', botStop: 'STOP',
    botClose: 'Close now', botRunning: 'RUNNING', botStopped: 'STOPPED', botOffline: 'OFFLINE',
    botGold: 'GOLD', botRules: 'Rules', botLock: 'lock', botTrail: 'trail',
    botFrom: 'from', botPendings: 'Pending', botNoPending: 'none',
    botTrade: 'Trade', botNoTrade: 'none', botRisk: 'Risk/trade', botStats: 'trades',
    botOpened: 'opened', botClosed: 'closed',
    botConfirmStart: 'Start the gold bot with this lot size?',
    botPerTrade: 'per trade if the stop-loss is hit',
    botConfirmClose: 'Close the open position now and cancel the pending orders?',
    botStarted: 'Bot STARTED — arming the BuyStop / SellStop',
    botStoppedMsg: 'Bot STOPPED — pending orders cancelled',
    botClosedMsg: 'Closed positions',
    priceMapTitle: 'Live Level Map',
    bot5Title: 'Auto Trading (News Trader)',
    bot5Mode: 'Trading style', bot5ModeAnticipate: 'Anticipate', bot5ModeStraddle: 'Straddle', bot5ModeFollow: 'Follow',
    bot5Impact: 'Min impact', bot5ImpactHigh: 'High', bot5ImpactMedium: 'Medium', bot5ImpactAll: 'All',
    bot5Pre: 'Pre minutes', bot5Post: 'Post minutes',
    bot5Hint: 'The bot reads the economic calendar, tells you BUY or SELL and when to enter, then trades the news window: it opens Pre minutes before the release and closes Post minutes after it. Anticipate = one position in the news direction, Straddle = both sides parked, Follow = trade the actual reaction.',
    bot5ManualTitle: 'Add your own event (always available offline)',
    bot5ManualAdd: '+ Add event', bot5ManualClear: 'Clear all',
    bot5NextEvent: 'Next event', bot5NoNews: 'No upcoming high-impact event',
    bot5NewsList: 'All economic news', bot5Refresh: '🔄 Refresh news',
    bot5AllMarket: 'ALL MARKET', bot5WatchOnly: 'Watch only', bot5NewsTotal: 'events',
    bot5OutlookTitle: '🤖 AI Market Direction · next day',
    bot5OutlookSub: 'date-wise · which market moves how many $ (±) · every news source read',
    sessTitle: '🌐 Trading Sessions · live clock & AI dollar forecast',
    sessSub: 'Sydney (Australian) · Tokyo (Asian) · London (European) · New York (North American)',
    sessMarket: 'Market', sessOpen: 'OPEN', sessClosed: 'CLOSED',
    sessSydney: 'Sydney', sessTokyo: 'Tokyo', sessLondon: 'London', sessNewYork: 'New York',
    sessRegAu: 'Australian', sessRegAsia: 'Asian', sessRegEu: 'European', sessRegNa: 'North American',
    sessStartsIn: 'starts in', sessEndsIn: 'ends in', sessWeekend: 'weekend break · reopens Monday',
    sessPeak: 'PEAK', sessPeakTip: 'London + New York overlap - the heaviest liquidity of the day',
    sessAi: 'AI forecast', sessMostMove: 'moves the most here', sessMixed: 'MIXED',
    sessAiUp: 'BUY Target', sessAiDown: 'SELL Target', sessRange: 'range', sessPriceUnit: 'price units',
    sessBuyTarget: 'BUY Forecast', sessSellTarget: 'SELL Forecast', sessExpRange: 'Expected Range',
    sessAllMarketsTitle: 'All Markets Session Direction & Dollar Forecast',
    sessAllMarketsLive: 'LIVE SESSION', sessAllMarketsNext: 'NEXT SESSION',
    sessUpDays: 'days closed up', sessDaysAvg: 'avg of', sessLot: 'lot',
    sessBestUp: 'best up', sessBestDown: 'best down', sessDial: 'dial',
    sessFlowTip: 'live watch-dial buyers vs sellers on this market',
    sessNoData: 'no session history yet - connect the terminal',
    sessLoading: 'Starting the session clock…', sessUpdated: 'updated',
    sessClockOnly: 'clock only (no terminal)',
    aiTitle: '🧠 Full AI Market Analysis · live BUY / SELL',
    aiSub: '1m · 5m · 15m · 1h · 4h · 1Day candles + news + live dials → where the price goes',
    aiMarket: 'Market', aiTimeframe: 'timeframe',
    aiAi: 'AI', aiBuy: 'BUY', aiSell: 'SELL', aiNoTrade: 'NO TRADE',
    aiConfidence: 'confidence', aiScore: 'score',
    aiUp: 'up', aiDown: 'down', aiRisk: 'risk',
    aiUpTarget: 'up target', aiDownTarget: 'down target',
    aiLevels: 'levels', aiRange: 'expected range',
    aiUpdated: 'updated', aiLoading: 'Reading the candles…',
    aiNoTerminal: 'connect the terminal for live candles',
    aiNoData: 'not enough closed candles yet',
    aiPriceNow: 'price now', aiClosed: 'closed candles',
    aiWhy: 'why',
    bot6Title: 'Auto Trading (Pre-Trade Entry)', bot6Short: 'Bot 6',
    bot6Distance: 'Distance ($)', bot6Pressure: 'Min Pressure (%)', bot6Side: 'Side',
    bot6SideWatch: 'Watch (auto)', bot6SideBuy: 'Buy only', bot6SideSell: 'Sell only', bot6SideBoth: 'Both sides',
    bot6WatchGate: 'Watch Check', bot6AiGate: 'AI Check', bot6WatchTitle: '👁️ Market Pressure Watch',
    bot6RulesTitle: '💰 TP / SL Rule In Force', bot6EntryTitle: '🎯 Parked Pre-Trade',
    bot6Hint: 'The pre-trade (pending order) is parked Distance ($) away from the live market price, in the direction the Market Pressure Watch allows. Watch NEUTRAL, or pressure below Min Pressure % → no trade. TP > 0 = fixed take profit, 0 = no TP. SL > 0 = fixed stop loss, SL = 0 → from $0.80 of profit the SL is set at 70% of the profit and follows it up, never back down.',
    bot6NoEntry: 'No pre-trade parked',
    bot7Title: 'Auto Trading (Reversal Candle)', bot7Short: 'Bot 7',
    bot7Reverse: 'Reversal Distance ($)', bot7CandleTitle: '🕯️ Candle Reversal Radar',
    bot2Timeframe: 'Timeframe',
    bot7LastCandle: 'Last closed candle',
    bot7Hint: "The bot reads the last closed candle and measures the reversal from the point the market turned from: the candle's close, which then follows the lowest low (bear candle) / highest high (bull candle) while the price keeps running that way - so a candle that drops, goes deeper and then suddenly snaps back still fires, from the real turning point. The moment the price travels back by the Reversal Distance ($) the trade is fired instantly in the new direction (bull candle breaks back down → SELL, bear candle breaks back up → BUY). Reversal Distance 0 = the candle's own body. Each closed candle can fire once. Turn level / trigger / left to fire are shown live in the radar. TP/SL follow the same rule as Bot 6 (SL 0 = 70% profit lock from $0.80).",
    advanced: 'Advanced', terminal: 'MT5 terminal (terminal64.exe)',
    terminalHint: 'Leave on "Auto-detect" unless you have several terminals installed.',
    remember: 'Remember login + server on this PC',
    connectSub: 'Enter your Exness login, password and server. You only reach the dashboard after a successful login.',
    badgeReal: 'LIVE · REAL', badgeDemo: 'DEMO ACCOUNT', badgeContest: 'CONTEST ACCOUNT',
    badgeEa: 'EA BRIDGE', loggedOut: 'LOGGED OUT', offline: 'OFFLINE',
    drawerBotsTitle: 'All Trading Bots & Economic News',
    flowBalanceTitle: 'LIVE TRADE FLOW',
  },
  bn: {
    serverTime: 'সার্ভার সময়', disconnected: 'সংযোগ নেই', connected: 'সংযুক্ত',
    connecting: 'সংযোগ হচ্ছে…', errorState: 'ত্রুটি', account: 'অ্যাকাউন্ট', balance: 'ব্যালেন্স',
    equity: 'ইকুইটি', margin: 'মার্জিন', freeMargin: 'ফ্রি মার্জিন', marginLevel: 'মার্জিন লেভেল',
    leverage: 'লিভারেজ', unlimited: 'আনলিমিটেড', today: 'আজ', session: 'সেশন', totalProfitToday: 'আজকের মোট প্রফিট',
    wins: 'প্রফিট ট্রেড', losses: 'লস ট্রেড', winRate: 'উইন রেট', floating: 'ফ্লোটিং লস/প্রফিট',
    profitFactor: 'প্রফিট ফ্যাক্টর', bestWorst: 'সেরা / খারাপ', volume: 'মোট লট',
    openPositions: 'ওপেন পজিশন', symbol: 'সিম্বল', side: 'টাইপ', lots: 'লট',
    openPrice: 'ওপেন', currentPrice: 'বর্তমান', sl: 'এস/এল', tp: 'টি/পি', swap: 'সোয়াপ',
    profit: 'লাভ/লস', opened: 'খোলা হয়েছে', noPositions: 'এখন কোনো ওপেন পজিশন নেই।',
    activity: 'কার্যক্রম', tradeHistory: 'ট্রেড হিস্ট্রি', allTime: 'সব সময়', allResults: 'সব ফলাফল',
    winsOnly: 'শুধু প্রফিট', lossesOnly: 'শুধু লস', exportCsv: 'CSV ডাউনলোড', closeTime: 'ক্লোজ সময়',
    closePrice: 'ক্লোজ', commission: 'কমিশন', netPL: 'নিট লাভ/লস', magic: 'ম্যাজিক', comment: 'কমেন্ট',
    duration: 'সময়কাল', noHistory: 'এই রেঞ্জে কোনো ক্লোজড ট্রেড নেই।', loadMore: 'আরও দেখুন',
    settingsTitle: 'সেটিংস', pollInterval: 'লাইভ রিফ্রেশ (ms)', historyRefresh: 'হিস্ট্রি সিঙ্ক (ms)',
    historyDays: 'হিস্ট্রি গভীরতা (দিন)', save: 'সেভ', close: 'বন্ধ', disconnect: 'ডিসকানেক্ট',
    connectTitle: 'Exness অ্যাকাউন্ট লগইন', connect: 'লগইন',
    viewDashboard: 'ড্যাশবোর্ড দেখুন',
    login: 'লগইন (অ্যাকাউন্ট নম্বর)', password: 'পাসওয়ার্ড', server: 'সার্ভার',
    serverPick: 'আপনার Exness সার্ভার বেছে নিন…',
    serverReal: 'Exness রিয়েল অ্যাকাউন্ট',
    serverTrial: 'Exness ডেমো / ট্রায়াল অ্যাকাউন্ট',
    serverOther: 'অন্য সার্ভার (নিজে লিখুন)…',
    serverHint: 'লিস্ট থেকে আপনার Exness সার্ভার বেছে নিন।',
    serverHintCustom: 'সার্ভারের নাম হুবহু লিখুন, যেমন Exness-MT5Real6',
    needLogin: 'Exness অ্যাকাউন্ট নম্বর দিন',
    mt5Ready: 'MT5 টার্মিনাল প্রস্তুত:',
    mt5ReadySub: 'ড্যাশবোর্ড নিজেই চালু করে লগইন করে দেয় — আপনাকে খুলতে হবে না।',
    mt5NoExness: 'MT5 টার্মিনাল পাওয়া গেছে (যেকোনো MT5 দিয়েই Exness-এ লগইন হয়)।',
    mt5Missing: 'এই PC-তে কোনো MetaTrader 5 টার্মিনাল পাওয়া যায়নি।',
    mt5Download: 'Exness MT5 →',
    mt5DownloadPlain: 'MetaTrader 5 →',
    mt5Recheck: 'আবার চেক করুন',
    marketLiveTicker: 'লাইভ মার্কেট',
    liveRatesTitle: 'লাইভ মার্কেট রেট',
    realtimeStreaming: '২৪/৭ স্ট্রিম',
    priceCurvesTitle: 'মার্কেট প্রাইস কার্ভ',
    priceCurvesSub: 'প্রতিটি মার্কেটের রিয়েল-টাইম প্রাইস কার্ভ · লাইভ WebSocket স্ট্রিম',
    watchTitle: 'মার্কেট প্রেশার ওয়াচ ডায়াল',
    watchSubtitle: 'রিয়েল-টাইম ক্যান্ডেল ভলিউম ও বায়ার/সেলার চাপের লাইভ ওয়াচ ডায়াল',
    flowLive: 'লাইভ',
    flowStepMarket: 'মার্কেট', flowStepMarketHint: 'একটা মার্কেট বেছে নিন — নিচের সব ধাপ এটাই ফলো করবে',
    flowHeroEyebrow: 'এখান থেকে শুরু · মার্কেট বেছে নিন',
    flowSignalSub: 'এআই এখনো ফ্লো পড়ছে',
    flowCurveTitle: 'লাইভ কার্ভ', flowCurveSub: 'নির্বাচিত মার্কেট · টিক ধরে ধরে',
    flowStepDial: 'প্রেশার ডায়াল', flowStepDialHint: 'শুধু আপনার বেছে নেওয়া মার্কেট',
    flowStepSetups: 'পরবর্তী সেটআপ', flowStepSetupsHint: 'পরের ৫টা এক্সট্রিম এন্ট্রি, প্রতি ৬০ সেকেন্ডে নতুন হিসাব',
    flowStepNews: 'পরবর্তী ইভেন্ট', flowStepNewsHint: 'ইমপ্যাক্ট লেভেল + কত সময় বাকি',
    flowStepSessions: 'সেশন', flowStepSessionsHint: 'live clocks, buy/sell bias & dollar forecast',
    flowStepAi: 'সম্পূর্ণ এআই বিশ্লেষণ', flowStepAiHint: '1m · 5m · 15m · 1h · 4h · 1Day candles + dials + news',
    flowStepOutlook: 'এআই ডিরেকশন · আগামী দিন', flowStepOutlookHint: 'সব নিউজ সোর্স পড়ে প্রতি মার্কেটে একটাই সিদ্ধান্ত',
    flowStepHistory: 'হিস্টোরি', flowStepHistoryHint: 'সব ক্লোজড ট্রেড — ফ্লো কোথায় শেষ হলো',
    flowStepPerf: 'পারফরম্যান্স', flowStepPerfHint: 'আজ · ৭দিন · ৩০দিন · ৯০দিন · এই সেশন',
    railPositions: 'পজিশন', railBots: 'সব বট',
    flowNoDial: 'এই মার্কেটের অর্ডার-ফ্লো কোট এখনো আসেনি — অন্য মার্কেট নিন বা টার্মিনাল কানেক্ট করুন।',
    flowNoDialShort: 'ডায়াল ভরতে টার্মিনাল কানেক্ট করুন।',
    flowMarketOpen: 'মার্কেট খোলা', flowMarketClosed: 'মার্কেট বন্ধ', flowNoQuote: 'কোট নেই',
    flowClosedTrades: 'ক্লোজড ট্রেড', flowNoSession: 'কোনো সেশন খোলা নেই',
    sessNoneRunning: 'এই মুহূর্তে কোনো সেশন চলছে না — পরেরটা শিগগিরই খুলবে।',
    botTitle: 'অটো ট্রেডিং', botLot: 'লট সাইজ', botGap: 'গ্যাপ ($)', botSL: 'SL ($)',
    bot2Title: 'অটো ট্রেডিং (ক্যান্ডেল ও স্ট্রাকচার)', bot2MaxTrades: 'সর্বোচ্চ রানিং ট্রেড', bot2TP: 'TP ($)', bot2SL: 'SL ($)',
    bot2BlockStart: 'শুরুতে ব্লক (সেকেন্ড)', bot2BlockEnd: 'শেষে ব্লক (সেকেন্ড)', bot2BlockMidFrom: 'মাঝের উইন্ডো শুরু (সেকেন্ড)', bot2BlockMidTo: 'মাঝের উইন্ডো শেষ (সেকেন্ড)',
    bot2BlockHint: '১-মিনিট ক্যান্ডেলের সময়-উইন্ডো: শুরুর N সেকেন্ড, শেষের N সেকেন্ড এবং মাঝের উইন্ডোতে ট্রেড নেবে না — আপনি যে মান দেবেন সেটাই ব্যবহার হবে।',
    bot2Trend: 'ওয়াচ',
    bot2Watch: 'ওয়াচ',
    bot2WatchPct: 'ওয়াচ %',
    bot2WatchHint: 'ওয়াচ ON: যে দিকের চাপ আপনি যে % লিখবেন তার চেয়ে কম হলে ট্রেড নেবে না — ৬০% কোনো ফিক্সড নিয়ম নয়। ওয়াচ OFF: ডায়াল বাদ, ক্যান্ডেল যেদিকে যাচ্ছে সেদিকেই ট্রেড (উপরে → BUY, নিচে → SELL), প্রতি ২.৫ সেকেন্ডে একবার। Reverse দিলে যে দিকেই নিত, তার ঠিক উল্টো দিকে নেবে।',
    bot2Reverse: 'রিভার্স',
    bot2CheckTrend: '🔄 ডিরেকশন চেক',
    bot2TrendSystemTitle: '২.৫-সেকেন্ড ডিরেকশন সিস্টেম',
    bot2TrendDetailsFooter: '⏱️ প্রতি ২.৫ সেকেন্ড পর পর স্বয়ংক্রিয়ভাবে ডিরেকশন চেক হচ্ছে ও সেই দিকে ট্রেড নেওয়া হচ্ছে',
    bot2StructLevels: 'মার্কেট স্ট্রাকচার লেভেল (Day & Weekend)',
    bot3Title: 'অটো ট্রেডিং (এক্সট্রিম হাই ও লো স্ট্রাকচার)',
    bot3StructLevels: 'এক্সট্রিম স্ট্রাকচার লেভেল (আজকের দিন, চলতি সপ্তাহ ও গত সপ্তাহ)',
    bot3TfToday: 'আজকের দিন (D1)',
    bot3TfThisWeek: 'চলতি সপ্তাহ (W1)',
    bot3TfPrevWeek: 'গত সপ্তাহ',
    bot3RadarTitle: 'ট্রেড এক্সিকিউশন রাডার ও টাইমার',
    bot3PlannedTitle: 'পরবর্তী ৫টি এক্সট্রিম ট্রেড সেটআপ (আসন্ন ট্রেড)',
    bot3PlannedCycle: '⚡ ৬০ সে. স্বয়ংক্রিয় হিসাব',
    bot3PlannedFooter: '🔄 প্রতি ৬০ সেকেন্ড পর পর স্বয়ংক্রিয়ভাবে হিসাব আপডেট: D1, W1 ও Prev W1 লাইভ লেভেল',
    recalcNow: 'হিসাব করুন',
    bot4Title: 'অটো ট্রেডিং (প্রি-ট্রেড ল্যাডার)',
    bot4PriceGap: 'প্রাইস গ্যাপ ($)',
    bot4PreGap: 'প্রি ট্রেড গ্যাপ ($)',
    bot4PreValue: 'প্রি ট্রেড ভ্যালু',
    bot4Hint: 'প্রি ট্রেড ভ্যালু = প্রতি পাশে কয়টা লেভেল (উপরে N টা বাই, নিচে N টা সেল)। প্রাইস গ্যাপ = মার্কেট প্রাইস থেকে প্রথম প্রি-ট্রেডের দূরত্ব, প্রি ট্রেড গ্যাপ = দুটি প্রি-ট্রেডের মাঝের দূরত্ব। Numbering Lot Size চালু করলে লট অটো 0.01, 0.02, 0.03 … ধরে দুই পাশেই সেট হবে।',
    bot4Numbering: 'Numbering Lot Size',
    bot4LadderTitle: 'প্রি-ট্রেড ল্যাডার (উপরে বাই / নিচে সেল)',
    bot4BuyCol: 'BUY ↑ মার্কেটের উপরে প্রি-ট্রেড',
    bot4SellCol: 'SELL ↓ মার্কেটের নিচে প্রি-ট্রেড',
    botSymbol: 'মার্কেট', botPickSymbol: 'গোল্ড সিম্বল বাছুন…',
    botMarketSwitched: 'মার্কেট বদলানো হলো', botMarketNow: 'নির্বাচিত মার্কেট',
    marketClosed: 'মার্কেট বন্ধ',
    marketClosedWarn: 'এই মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)',
    marketClosedCannotStart: 'মার্কেট বন্ধ থাকায় বট চালু করা যাবে না',
    condTitle: 'শর্তাবলি (বট যা করবে)',
    condSymbol: 'শুধু এই সিম্বলেই ট্রেড', condMissing: 'এই অ্যাকাউন্টে নেই',
    condInstead: 'এই অ্যাকাউন্টে নেই — এর বদলে এটা ব্যবহার হচ্ছে',
    condGap: 'গ্যাপ', condSL: 'Buy-এর SL = SellStop লেভেল, Sell-এর SL = BuyStop লেভেল —',
    condLot: 'লট', condLock: 'SL লক: প্রফিট', condSL2: 'SL শুধু লাভের দিকেই সরে — প্রফিট কমলে শেষ যেখানে ছিল সেখানেই fixed থাকে।',
    condTrail: 'ট্রেইলিং: প্রফিট',
    condReArm: 'Market SL-এ হিট করলে: পুরনো buy + sell পুরোপুরি clear হয়ে যায়, তারপর তখনকার market price থেকে নতুন করে BuyStop ও SellStop বসে।',
    botStart: 'START', botStop: 'STOP',
    botClose: 'এখনই ক্লোজ', botRunning: 'চালু আছে', botStopped: 'বন্ধ', botOffline: 'অফলাইন',
    botGold: 'গোল্ড', botRules: 'নিয়ম', botLock: 'লক', botTrail: 'ট্রেইল',
    botFrom: 'শুরু', botPendings: 'পেন্ডিং', botNoPending: 'নেই',
    botTrade: 'ট্রেড', botNoTrade: 'নেই', botRisk: 'রিস্ক/ট্রেড', botStats: 'ট্রেড',
    botOpened: 'খোলা', botClosed: 'ক্লোজড',
    botConfirmStart: 'এই লট সাইজে গোল্ড বট চালু করবেন?',
    botPerTrade: 'প্রতি ট্রেডে (SL হিট হলে)',
    botConfirmClose: 'খোলা ট্রেড এখনই ক্লোজ করে পেন্ডিং অর্ডার বাতিল করবেন?',
    botStarted: 'বট চালু — BuyStop / SellStop বসানো হচ্ছে',
    botStoppedMsg: 'বট বন্ধ — পেন্ডিং অর্ডার বাতিল করা হলো',
    botClosedMsg: 'ক্লোজ করা ট্রেড',
    priceMapTitle: 'লাইভ লেভেল ম্যাপ',
    bot5Title: 'অটো ট্রেডিং (নিউজ ট্রেডার)',
    bot5Mode: 'ট্রেডিং স্টাইল', bot5ModeAnticipate: 'অ্যান্টিসিপেট', bot5ModeStraddle: 'স্ট্র্যাডল', bot5ModeFollow: 'ফলো',
    bot5Impact: 'সর্বনিম্ন ইমপ্যাক্ট', bot5ImpactHigh: 'হাই', bot5ImpactMedium: 'মিডিয়াম', bot5ImpactAll: 'সব',
    bot5Pre: 'প্রি মিনিট', bot5Post: 'পোস্ট মিনিট',
    bot5Hint: 'বট ইকোনমিক ক্যালেন্ডার পড়ে, আপনাকে বলে BUY না SELL আর কখন ঢুকতে হবে — নিউজের Pre মিনিট আগে ট্রেড খুলে Post মিনিট পরে ক্লোজ করে। অ্যান্টিসিপেট = নিউজের দিকে একটা পজিশন, স্ট্র্যাডল = দুই পাশে অর্ডার পার্ক করা, ফলো = নিউজের পরের আসল রিঅ্যাকশন ধরা।',
    bot5ManualTitle: 'নিজের ইভেন্ট যোগ করুন (অফলাইনেও সবসময় কাজ করে)',
    bot5ManualAdd: '+ ইভেন্ট যোগ করুন', bot5ManualClear: 'সব মুছুন',
    bot5NextEvent: 'পরবর্তী ইভেন্ট', bot5NoNews: 'কোনো আসন্ন হাই-ইমপ্যাক্ট নিউজ নেই',
    bot5NewsList: 'সব ইকোনমিক নিউজ', bot5Refresh: '🔄 নিউজ রিফ্রেশ',
    bot5AllMarket: 'সব মার্কেট', bot5WatchOnly: 'শুধু দেখার জন্য', bot5NewsTotal: 'ইভেন্ট',
    bot5OutlookTitle: '🤖 এআই মার্কেট ডিরেকশন · আগামী দিন',
    bot5OutlookSub: 'তারিখ-ভিত্তিক · কোন মার্কেট কত ডলার (±) · সব নিউজ সোর্স পড়ে',
    sessTitle: '🌐 Trading Sessions · live clock & AI dollar forecast',
    sessSub: 'Sydney (Australian) · Tokyo (Asian) · London (European) · New York (North American)',
    sessMarket: 'Market', sessOpen: 'OPEN', sessClosed: 'CLOSED',
    sessSydney: 'Sydney', sessTokyo: 'Tokyo', sessLondon: 'London', sessNewYork: 'New York',
    sessRegAu: 'Australian', sessRegAsia: 'Asian', sessRegEu: 'European', sessRegNa: 'North American',
    sessStartsIn: 'starts in', sessEndsIn: 'ends in', sessWeekend: 'weekend break · reopens Monday',
    sessPeak: 'PEAK', sessPeakTip: 'London + New York overlap - the heaviest liquidity of the day',
    sessAi: 'AI forecast', sessMostMove: 'moves the most here', sessMixed: 'MIXED',
    sessAiUp: 'BUY Target', sessAiDown: 'SELL Target', sessRange: 'range', sessPriceUnit: 'price units',
    sessBuyTarget: 'BUY Forecast', sessSellTarget: 'SELL Forecast', sessExpRange: 'Expected Range',
    sessAllMarketsTitle: 'All Markets Session Direction & Dollar Forecast',
    sessAllMarketsLive: 'LIVE SESSION', sessAllMarketsNext: 'NEXT SESSION',
    sessUpDays: 'days up', sessDaysAvg: 'avg', sessLot: 'lot',
    sessBestUp: 'Max Up', sessBestDown: 'Max Down', sessDial: 'DIAL',
    sessFlowTip: 'Live watch-dial buyer vs seller for this market',
    sessNoData: 'No session history - connect terminal',
    sessLoading: 'Starting the session clock…', sessUpdated: 'Updated',
    sessClockOnly: 'Clock only (no terminal)',
    aiTitle: '🧠 সম্পূর্ণ এআই মার্কেট বিশ্লেষণ · লাইভ বাই / সেল',
    aiSub: '৫মি · ১০মি · ১৫মি · ১ঘ. ক্যান্ডেল + নিউজ + লাইভ ডায়াল → প্রাইস কোথায় যাবে',
    aiMarket: 'মার্কেট', aiTimeframe: 'টাইমফ্রেম',
    aiAi: 'এআই', aiBuy: 'বাই', aiSell: 'সেল', aiNoTrade: 'ট্রেড নেই',
    aiConfidence: 'কনফিডেন্স', aiScore: 'স্কোর',
    aiUp: 'উপরে', aiDown: 'নিচে', aiRisk: 'রিস্ক',
    aiUpTarget: 'উপরের টার্গেট', aiDownTarget: 'নিচের টার্গেট',
    aiLevels: 'লেভেল', aiRange: 'সম্ভাব্য রেঞ্জ',
    aiUpdated: 'আপডেট', aiLoading: 'ক্যান্ডেল পড়া হচ্ছে…',
    aiNoTerminal: 'লাইভ ক্যান্ডেলের জন্য টার্মিনাল কানেক্ট করুন',
    aiNoData: 'পর্যাপ্ত ক্লোজড ক্যান্ডেল নেই',
    aiPriceNow: 'এখন প্রাইস', aiClosed: 'ক্লোজড ক্যান্ডেল',
    aiWhy: 'কারণ',
    bot6Title: 'অটো ট্রেডিং (প্রি-ট্রেড এন্ট্রি)', bot6Short: 'বট ৬',
    bot6Distance: 'ডিস্ট্যান্স ($)', bot6Pressure: 'সর্বনিম্ন প্রেসার (%)', bot6Side: 'সাইড',
    bot6SideWatch: 'ওয়াচ (অটো)', bot6SideBuy: 'শুধু বাই', bot6SideSell: 'শুধু সেল', bot6SideBoth: 'দুই পাশ',
    bot6WatchGate: 'ওয়াচ চেক', bot6AiGate: 'এআই চেক', bot6WatchTitle: '👁️ মার্কেট প্রেসার ওয়াচ',
    bot6RulesTitle: '💰 চালু থাকা TP / SL রুল', bot6EntryTitle: '🎯 পার্ক করা প্রি-ট্রেড',
    bot6Hint: 'প্রি-ট্রেড (পেন্ডিং অর্ডার) লাইভ প্রাইস থেকে Distance ($) দূরে বসে, দিকটা ঠিক করে মার্কেট প্রেসার ওয়াচ। ওয়াচ নিউট্রাল, বা প্রেসার Min Pressure % এর কম → ট্রেড নেবে না। TP > ০ = ফিক্সড টেক প্রফিট, ০ = TP নেই। SL > ০ = ফিক্সড স্টপ লস, SL = ০ → $০.৮০ প্রফিট হলেই SL প্রফিটের ৭০% এ বসবে আর প্রফিট বাড়লে সাথে সাথে উপরে উঠবে, কখনো নিচে নামবে না।',
    bot6NoEntry: 'কোনো প্রি-ট্রেড পার্ক করা নেই',
    bot7Title: 'অটো ট্রেডিং (রিভার্সাল ক্যান্ডেল)', bot7Short: 'বট ৭',
    bot7Reverse: 'রিভার্সাল ডিস্ট্যান্স ($)', bot7CandleTitle: '🕯️ ক্যান্ডেল রিভার্সাল রাডার',
    bot2Timeframe: 'টাইমফ্রেম',
    bot7LastCandle: 'শেষ ক্লোজড ক্যান্ডেল',
    bot7Hint: 'বট শেষ ক্লোজড ক্যান্ডেল পড়ে, আর রিভার্সাল মাপে মার্কেট যে পয়েন্ট থেকে ঘুরেছে সেখান থেকে: ক্যান্ডেলের ক্লোজ, যা প্রাইস ওই দিকে চলতে থাকলে বেয়ার ক্যান্ডেলের সবচেয়ে নিচের লো / বুল ক্যান্ডেলের সবচেয়ে উপরের হাই পর্যন্ত ফলো করে — তাই ক্যান্ডেল নেমে আরও গভীরে গিয়ে হঠাৎ উপরে লাফালেও আসল টার্নিং পয়েন্ট থেকেই ফায়ার করে। প্রাইস Reversal Distance ($) উল্টো দিকে ফিরলেই সাথে সাথে নতুন দিকে ট্রেড নেয় (bull ক্যান্ডেল নিচে ভাঙলে → SELL, bear ক্যান্ডেল উপরে ভাঙলে → BUY)। Reversal Distance 0 = ক্যান্ডেলের নিজের বডি। প্রতি ক্লোজড ক্যান্ডেল একবারই ফায়ার করে। রাডারে Turning point / trigger / কত বাকি লাইভ দেখা যায়। TP/SL বট 6 এর মতোই (SL 0 = $0.80 থেকে 70% প্রফিট লক)।',
    advanced: 'অ্যাডভান্সড', terminal: 'MT5 টার্মিনাল (terminal64.exe)',
    terminalHint: 'একাধিক টার্মিনাল ইনস্টল না থাকলে "Auto-detect" ই রাখুন।',
    remember: 'এই পিসিতে লগইন + সার্ভার মনে রাখুন',
    connectSub: 'Exness লগইন, পাসওয়ার্ড আর সার্ভার দিন। সফল লগইন ছাড়া ড্যাশবোর্ডে ঢুকতে পারবেন না।',
    badgeReal: 'লাইভ · রিয়েল', badgeDemo: 'ডেমো অ্যাকাউন্ট', badgeContest: 'কনটেস্ট অ্যাকাউন্ট',
    badgeEa: 'EA ব্রিজ', loggedOut: 'লগআউট', offline: 'অফলাইন',
    drawerBotsTitle: 'সব ট্রেডিং বট ও ইকোনমিক নিউজ',
    flowBalanceTitle: 'লাইভ ট্রেড ফ্লো',
  },
};
let LANG = localStorage.getItem('exdash.lang') || 'en';
const t = (k) => (I18N[LANG] && I18N[LANG][k]) || I18N.en[k] || k;

function applyLang() {
  document.querySelectorAll('[data-i18n]').forEach((n) => {
    const k = n.getAttribute('data-i18n');
    const v = t(k);
    if (v && v !== k) n.textContent = v;
  });
  document.documentElement.lang = LANG;
  $('lang-btn').textContent = LANG === 'en' ? 'বাং' : 'EN';
  if (typeof updateVoiceMuteButtons === 'function') updateVoiceMuteButtons();
  if (S) renderStatLabels();
  // the sessions and AI panels carry text built in JS, so they are rebuilt from
  // the last snapshot instead of waiting for the next engine push
  if (S) {
    sessionsSig = '';
    aiSig = '';
    renderSessions(S);
    renderAiMarket(S);
    renderFlowStage(S);
  }
}

/* --------------------------------------------------------------- state */
let S = null;                 // latest snapshot from the backend
let ws = null;
let wsRetry = 0;
let wsAlive = false;
let lastServerMsgAt = 0;
let wasConnected = false;
let dashboardPreview = false;
let dashboardPreviewUnlocked = false;
let dashboardPasswordResolve = null;
let connectViewPinned = false;   // user explicitly opened the login screen
let botsEnabled = false;
let period = 'today';
let histPeriod = '7';
let histResult = 'all';
let histSymbol = '';
let histSearch = '';
let histSortKey = 'close_time';   // history table sort column (set by header click)
let histSortDir = 'desc';         // 'asc' | 'desc'
let extraRows = [];           // pages fetched through /api/history
let posRows = new Map();      // ticket -> <tr>
let histSig = '';
let lastClosedId = null;
let lastEventTs = 0;
let voiceMuted = localStorage.getItem('exdash.voice_muted') === 'true';
let speechQueue = [];
let isSpeaking = false;
let currentSpeakingKey = null;
let seenEventKeys = new Set();
let _bot5LiveRefreshAt = 0;
let feedInitialized = false;

/* -------------------------------------------------------------- websocket */
let wsReconnectTimer = null;

/* Hosted hosting (cPanel / Passenger) closes every WebSocket upgrade, so a
   socket that never opens twice in a row means "this host has no WebSockets" -
   the dashboard then polls the very same snapshot over HTTP. The numbers are
   identical, only the transport differs. */
let httpPollTimer = null;
let wsFailStreak = 0;
// Signature of the news calendar this tab holds. The cPanel-safe state feed
// leaves the 1 MB list out of a poll while this matches (?have=), so the state
// request stays a few KB instead of a megabyte every 2.5 s.
let newsHaveSig = '';

function startHttpPoll() {
  if (httpPollTimer) return;
  const tick = async () => {
    httpPollTimer = setTimeout(tick, 2500);
    // bounded: a slowed-down shared host must not queue up poll after poll
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const abort = ctrl ? setTimeout(() => { try { ctrl.abort(); } catch (e) {} }, 10000) : null;
    try {
      const resp = await fetch('/api/state' + (newsHaveSig ? '?have=' + encodeURIComponent(newsHaveSig) : ''), {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: ctrl ? ctrl.signal : undefined
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (!data) return;
      lastServerMsgAt = Date.now();
      queueStateFrame(data);
      const foot = $('foot-ws');
      if (foot) { foot.textContent = 'http: live (polling)'; foot.style.color = '#93c5fd'; }
    } catch (e) {
      const foot = $('foot-ws');
      if (foot) { foot.textContent = 'http: retrying…'; foot.style.color = '#fca5a5'; }
    } finally {
      if (abort) clearTimeout(abort);
    }
  };
  tick();
}

function stopHttpPoll() {
  if (httpPollTimer) { clearTimeout(httpPollTimer); httpPollTimer = null; }
}

function connectWS() {
  // exactly one live socket: a superseded one must never keep feeding the UI,
  // otherwise every reconnect doubles the live frame rate (dial traffic is ~20/s)
  let sock;
  try { sock = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`); }
  catch (e) { scheduleReconnect(); return; }
  ws = sock;

  sock.onopen = () => {
    if (ws !== sock) return;
    wsAlive = true; wsRetry = 0;
    wsFailStreak = 0;
    stopHttpPoll();
    lastServerMsgAt = Date.now();
    $('foot-ws').textContent = 'socket: live';
    $('foot-ws').style.color = '#86efac';
    sock.send(JSON.stringify({ type: 'refresh' }));
  };
  sock.onmessage = (ev) => {
    if (ws !== sock) return;
    lastServerMsgAt = Date.now();
    let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === 'state') { queueStateFrame(msg.data); }
    else if (msg.type === 'watch') { queueWatchFrame(msg.data); }
    else if (msg.type === 'notify') { toast(msg.data.kind, msg.data.message); }
  };
  sock.onclose = () => {
    if (ws !== sock) return;   // stale socket - a newer one already took over
    wsAlive = false;
    wsFailStreak += 1;
    // Two sockets in a row that never opened = a host without WebSocket
    // support (Passenger on cPanel), not an outage.
    if (wsFailStreak >= 2) startHttpPoll();
    $('foot-ws').textContent = httpPollTimer ? 'http: live (polling)' : 'socket: reconnecting…';
    $('foot-ws').style.color = httpPollTimer ? '#93c5fd' : '';
    scheduleReconnect();
  };
  sock.onerror = () => { try { sock.close(); } catch (e) {} };
}

/* The engine pushes a full state snapshot whenever anything moves - with live
   prices in its change gate that is 6-8 frames per second, and one snapshot
   repaints the whole dashboard (~35 ms). Keep only the newest snapshot and
   paint at most ~4 per second: the dials and the chart already stream their
   own 20 fps tick frames, so nothing visible waits for this. */
let _statePending = null;
let _stateLastAt = 0;
let _stateTimer = null;
function applyPendingState() {
  _stateTimer = null;
  _stateLastAt = Date.now();
  const next = _statePending;
  _statePending = null;
  if (next) applyState(next);
}
function queueStateFrame(data) {
  if (!data) return;
  _statePending = data;
  const wait = 250 - (Date.now() - _stateLastAt);
  if (wait <= 0) {
    if (_stateTimer) { clearTimeout(_stateTimer); _stateTimer = null; }
    applyPendingState();
    return;
  }
  if (_stateTimer) return;
  _stateTimer = setTimeout(applyPendingState, wait);
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;   // one pending reconnect, never a chain of them
  wsRetry = Math.min(wsRetry + 1, 12);
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
    connectWS();
  }, httpPollTimer ? 20000 : Math.min(400 * wsRetry, 4000));
}

/* ------------------------------------------------------------ state apply */
/* ------------------------------------------------- live trade flow balance (.flow-balance) */
// Sits under the live curve. OUR side = every open trade of the logged-in
// account (manual + every bot) on the selected market. WORLD side = the
// whole market's 5-minute taker buy vs sell volume and trader long/short
// accounts, pulled live from Binance public data (crypto pairs; gold uses
// PAXGUSDT as the world-wide proxy). Nothing is faked: instruments Binance
// does not trade honestly report "no world data for this market".
const FLOW_BAL_SYMBOLS = {
  BTCUSDTM: 'BTCUSDT', BTCUSDM: 'BTCUSDT', BTCUSDT: 'BTCUSDT', BTCUSD: 'BTCUSDT',
  ETHUSDTM: 'ETHUSDT', ETHUSDM: 'ETHUSDT', ETHUSDT: 'ETHUSDT',
  SOLUSDTM: 'SOLUSDT', SOLUSDT: 'SOLUSDT',
  XAUUSDM: 'PAXGUSDT', XAUUSD: 'PAXGUSDT', XAUUSD247M: 'PAXGUSDT',
};

function _fbFetchJson(url) {
  return fetch(url, { headers: { 'Accept': 'application/json' } })
    .then(r => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))));
}
const _fbMoney = (v) => {
  const n = Number(v) || 0;
  if (n >= 1e9) return `$${nf(n / 1e9, 1)}B`;
  if (n >= 1e6) return `$${nf(n / 1e6, 1)}M`;
  if (n >= 1e3) return `$${nf(n / 1e3, 1)}K`;
  return `$${nf(n, 0)}`;
};

/* Our side: manual + all bots, counted by lots and live dollars */
function updateFlowBalanceOurs(d) {
  const cell = $('fb-ours');
  if (!cell) return;
  const isBn = LANG === 'bn';
  const sym = (flowSymbol() || '').toUpperCase();
  const norm = (s) => String(s || '').toUpperCase().replace(/M$/, '');
  const src = d || S || {};
  const pos = Array.isArray(src.positions) ? src.positions : [];
  let buyLots = 0, sellLots = 0, buyNet = 0, sellNet = 0, buyN = 0, sellN = 0;
  pos.forEach((p) => {
    if (sym && norm(p.symbol) !== norm(sym)) return;
    const lots = Number(p.volume || 0);
    const net = Number(p.net !== undefined ? p.net : (p.profit || 0));
    if (String(p.side || '').toLowerCase() === 'buy') { buyLots += lots; buyNet += net; buyN++; }
    else { sellLots += lots; sellNet += net; sellN++; }
  });
  if (buyN + sellN === 0) {
    cell.innerHTML = `<b>${isBn ? '💻 আমাদের অ্যাকাউন্ট (বট + ম্যানুয়াল)' : '💻 Our account (bots + manual)'}</b>` +
      `<span class="fb-flat">${isBn ? `${esc(sym || '—')} মার্কেটে এখন কোনো ওপেন ট্রেড নেই` : `no open trades on ${esc(sym || '—')} right now`}</span>`;
    return;
  }
  cell.innerHTML = `<b>${isBn ? '💻 আমাদের অ্যাকাউন্ট (বট + ম্যানুয়াল)' : '💻 Our account (bots + manual)'}</b>` +
    `<span class="fb-buy">▲ BUY ${buyN} · ${nf(buyLots, 2)} lots · ${signed(buyNet)}</span>` +
    `<span class="fb-sell">▼ SELL ${sellN} · ${nf(sellLots, 2)} lots · ${signed(sellNet)}</span>` +
    `<span class="fb-flat">${isBn ? 'ফ্লোটিং' : 'floating'} ${signed(buyNet + sellNet)}</span>`;
}

/* World side: live 5m taker volume split + trader account long/short */
async function pollFlowBalanceWorld() {
  const cell = $('fb-world');
  if (!cell) return;
  const isBn = LANG === 'bn';
  const sym = (flowSymbol() || '').toUpperCase();
  const binSym = FLOW_BAL_SYMBOLS[sym] || null;
  const label = `🌍 ${isBn ? 'সারা পৃথিবী' : 'Whole world'}${binSym ? ' · ' + binSym : ''}`;
  if (!binSym) {
    cell.innerHTML = `<b>${esc(label)}</b>` +
      `<span class="fb-flat">${isBn ? esc(sym) + ' মার্কেটের বিশ্বব্যাপী ডেটা ফিড এখনো নেই' : 'no world-wide data feed for ' + esc(sym) + ' yet'}</span>`;
    return;
  }
  try {
    const [taker, glob, tick] = await Promise.all([
      _fbFetchJson(`https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=${binSym}&period=5m&limit=1`),
      _fbFetchJson(`https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=${binSym}&period=5m&limit=1`),
      _fbFetchJson(`https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=${binSym}`),
    ]);
    const t = Array.isArray(taker) ? (taker[0] || {}) : {};
    const g = Array.isArray(glob) ? (glob[0] || {}) : {};
    const quoteVol = Number((tick && tick.quoteVolume) || 0);
    const buyVol = Number(t.buyVol || 0);
    const sellVol = Number(t.sellVol || 0);
    const share = (buyVol + sellVol) > 0 ? buyVol / (buyVol + sellVol) : 0.5;
    const buyUsd = quoteVol * share;
    const sellUsd = quoteVol * (1 - share);
    const longAcc = Math.round(Number(g.longAccount || 0.5) * 100);
    const shortAcc = 100 - longAcc;
    cell.innerHTML = `<b>${esc(label)}</b>` +
      `<span class="fb-buy">▲ BUY ${_fbMoney(buyUsd)} (${Math.round(share * 100)}%)</span>` +
      `<span class="fb-sell">▼ SELL ${_fbMoney(sellUsd)} (${Math.round((1 - share) * 100)}%)</span>` +
      `<span class="fb-acc">${isBn ? 'ট্রেডার অ্যাকাউন্ট' : 'trader accounts'} ${longAcc}% / ${shortAcc}%</span>`;
  } catch (e) {
    cell.innerHTML = `<b>${esc(label)}</b><span class="fb-flat">${isBn ? 'বিশ্ব-ডেটা ফিড এই মুহূর্তে পৌঁছাচ্ছে না' : 'world data feed unreachable right now'}</span>`;
  }
}

function applyState(data) {
  if (!data) return;
  const prev = S;
  // The engine leaves the (1 MB) calendar out of frames where it did not
  // change; keep showing the last list it did send.
  if (data.bot5 && !Array.isArray(data.bot5.news)
      && prev && prev.bot5 && Array.isArray(prev.bot5.news)) {
    data.bot5.news = prev.bot5.news;
  }
  // Remember which list we hold, so the next poll can ask the server to leave
  // it out of the response.
  if (data.bot5 && Array.isArray(data.bot5.news) && data.bot5.news_sig) {
    newsHaveSig = data.bot5.news_sig;
  }
  S = data;
  if (window.TradersTerminal && typeof window.TradersTerminal.onSnapshot === 'function') {
    window.TradersTerminal.onSnapshot(data);
  }
  const connected = !!data.connected || data.status === 'connected' || (data.mode === 'ea');
  if (!connected && wasConnected) fullResetUI();
  wasConnected = connected;
  renderConnection(data);
  syncBotStartButtons(connected);
  if (!connectViewPinned && (connected || dashboardPreview)) {
    $('connect-view').classList.add('hidden');
    $('dash').classList.remove('hidden');
    scheduleFlowWires();   // the connectors measure as soon as the dashboard is on screen
  } else {
    $('connect-view').classList.remove('hidden');
    $('dash').classList.add('hidden');
  }
  if (data.error && data.status === 'error') showConnectError(data.error);
  renderAccount(data);
  renderStats(data);
  if (data.market_statuses) {
    marketStatuses = data.market_statuses;
  }
  if (data.market_watch && Array.isArray(data.market_watch) && data.market_watch.length) {
    // live stream push (engine tick): patch the watch dials in place - no timer involved
    marketWatches = data.market_watch;
    applyMarketWatchState();   // (also refreshes the ticker tape)
  }
  // Bots 1-4, 6 and 7 live inside the side drawer only. Repainting them at
  // every engine frame while that drawer is shut was the single most expensive
  // thing on the page (15k hidden nodes + innerHTML rebuilds), so they are
  // painted once when the drawer opens instead. Bot 5 also feeds the news
  // panel on the main page, so it always renders.
  if (botsDrawerOpen()) {
    renderBotsPanels(data);
  }
  try { renderBot5(data); } catch (e) { console.error('renderBot5 error:', e); }
  try { renderSessions(data); } catch (e) { console.error('renderSessions error:', e); }
  try { renderAiMarket(data); } catch (e) { console.error('renderAiMarket error:', e); }
  try { renderFlowStage(data); } catch (e) { console.error('renderFlowStage error:', e); }
  try { renderPositions(data); } catch (e) { console.error('renderPositions error:', e); }
  try { updateFlowBalanceOurs(data); } catch (e) { console.error('updateFlowBalanceOurs error:', e); }
  try { renderHistory(data); } catch (e) { console.error('renderHistory error:', e); }
  try { renderFeed(data); } catch (e) { console.error('renderFeed error:', e); }
  try { renderFooter(data); } catch (e) { console.error('renderFooter error:', e); }
  try { detectNewClosed(prev, data); } catch (e) { console.error('detectNewClosed error:', e); }
}

/* True while the bots drawer is on screen. Its contents are skipped by the
   browser when closed (content-visibility), so repainting them is wasted work. */
function botsDrawerOpen() {
  const dash = $('dash');
  return !!dash && dash.classList.contains('drawer-right-open');
}

function renderBotsPanels(d) {
  if (!d) return;
  try { renderBot(d); } catch (e) { console.error('renderBot error:', e); }
  try { renderBot2(d); } catch (e) { console.error('renderBot2 error:', e); }
  try { renderBot3(d); } catch (e) { console.error('renderBot3 error:', e); }
  try { renderBot4(d); } catch (e) { console.error('renderBot4 error:', e); }
  try { renderBot6(d); } catch (e) { console.error('renderBot6 error:', e); }
  try { renderBot7(d); } catch (e) { console.error('renderBot7 error:', e); }
}

/* Wipe every trace of the previous session (used on disconnect / logout) */
function fullResetUI() {
  posRows.forEach((tr) => tr.remove());
  posRows.clear();
  $('positions-body').innerHTML = '';
  $('history-body').innerHTML = '';
  const feedElem = $('feed');
  if (feedElem) feedElem.innerHTML = '';
  $('pos-count').textContent = '0';
  $('hist-count').textContent = '0';
  const rhEl = $('rail-hist-count');
  if (rhEl) rhEl.textContent = '0';
  const thEl = $('tab-badge-hist');
  if (thEl) thEl.textContent = '0';
  const rfEl = $('rail-feed-count');
  if (rfEl) rfEl.textContent = '0';
  const tfEl = $('tab-badge-feed');
  if (tfEl) tfEl.textContent = '0';
  const fbEl = $('feed-badge');
  if (fbEl) fbEl.textContent = '0';
  $('pos-total').textContent = '0.00';
  histSig = '';
  lastClosedId = null;
  lastEventTs = 0;
  extraRows = [];
  seenEventKeys.clear();
  feedInitialized = false;
  speechQueue = [];
  if ('speechSynthesis' in window) {
    try { window.speechSynthesis.cancel(); } catch (e) {}
  }
  if (typeof clearSpeakingHighlight === 'function') clearSpeakingHighlight();
  isSpeaking = false;
  currentSpeakingKey = null;
  ['acc-login', 'acc-server', 'acc-balance', 'acc-equity', 'acc-margin',
    'acc-marginfree', 'acc-marginlevel', 'acc-leverage'].forEach((id) => { $(id).textContent = '—'; });
  $('password').value = '';
  if (typeof closeFlowDrawers === 'function') closeFlowDrawers();
}

/* ------------------------------------------------------------- connection */
function renderConnection(d) {
  const w = $('conn-widget'), txt = $('conn-text'), badge = $('source-badge');
  let widget = 'offline', text = t('disconnected'), cls = 'badge badge-off', label = t('loggedOut');

  if (d.status === 'connecting') {
    text = t('connecting'); label = '…';
  } else if (d.status === 'error') {
    widget = 'error'; text = t('errorState'); label = 'ERROR';
  } else if (d.source === 'mt5') {
    widget = 'online'; text = t('connected');
    if (d.account_kind === 'demo') { cls = 'badge badge-demo'; label = t('badgeDemo'); }
    else if (d.account_kind === 'contest') { cls = 'badge badge-demo'; label = t('badgeContest'); }
    else if (d.account_kind === 'real') { cls = 'badge badge-live'; label = t('badgeReal'); }
    else { cls = 'badge badge-live'; label = 'LIVE MT5'; }
  } else if (d.source === 'ea') {
    widget = 'online'; text = t('connected'); cls = 'badge badge-ea'; label = t('badgeEa');
  }

  w.classList.remove('online', 'offline', 'error');
  w.classList.add(widget);
  if (txt.textContent !== text) txt.textContent = text;
  if (badge.textContent !== label) badge.textContent = label;
  if (badge.className !== cls) badge.className = cls;
  $('topbar-disconnect').classList.toggle('hidden', !(d.connected || d.status === 'connected'));
}

/* ---------------------------------------------------------------- account */
function renderAccount(d) {
  const a = d.account || {};
  const has = !!d.account;
  const cur = has ? (a.currency || 'USD') : '';
  const set = (id, v) => { const n = $(id); if (n && n.textContent !== v) n.textContent = v; };
  set('acc-login', a.login ? String(a.login) : '—');
  set('acc-server', a.server || '—');
  set('acc-balance', has ? `${nf(a.balance)} ${cur}` : '—');
  set('acc-equity', has ? `${nf(a.equity)} ${cur}` : '—');
  set('acc-margin', has ? nf(a.margin) : '—');
  set('acc-marginfree', has ? nf(a.margin_free) : '—');
  set('acc-marginlevel', has && a.margin_level ? `${nf(a.margin_level, 1)}%` : '—');
  // Exness reports "Unlimited" leverage as 1:2000000000
  const lev = Number(a.leverage || 0);
  set('acc-leverage', has && lev ? (lev >= 1e9 ? t('unlimited') : `1:${lev}`) : '—');
}

/* ------------------------------------------------------------------ stats */
function renderStatLabels() {
  const lbl = { today: t('totalProfitToday'), '7d': 'Last 7 days', '30d': 'Last 30 days', '90d': 'Last 90 days', session: 'This session' };
  if (LANG === 'bn') {
    lbl['7d'] = 'শেষ ৭ দিন'; lbl['30d'] = 'শেষ ৩০ দিন'; lbl['90d'] = 'শেষ ৯০ দিন'; lbl.session = 'এই সেশন';
  }
  $('stat-net-label').textContent = lbl[period] || t('totalProfitToday');
}

function renderStats(d) {
  const s = (d.stats && d.stats[period]) || {};
  const cur = (d.account && d.account.currency) || 'USD';
  const net = Number(s.net_profit || 0);

  const netEl = $('stat-net');
  netEl.textContent = signed(net) + (s.trades ? '' : '');
  netEl.className = 'stat-value big ' + (net > 0 ? 'pos-color' : net < 0 ? 'neg-color' : 'flat-color');
  $('stat-net-sub').textContent = `${s.trades || 0} closed · ${cur} · pf ${s.profit_factor ?? '—'}`;

  $('stat-wins').textContent = nf(s.wins || 0, 0);
  $('stat-wins-sub').textContent = `gross +${nf(s.gross_profit || 0)}`;
  $('stat-losses').textContent = nf(s.losses || 0, 0);
  $('stat-losses-sub').textContent = `gross -${nf(s.gross_loss || 0)}`;
  $('stat-winrate').textContent = `${nf(s.win_rate || 0, 1)}%`;
  $('stat-winrate-sub').textContent = `${s.wins || 0}W / ${s.losses || 0}L${s.breakeven ? ` / ${s.breakeven}BE` : ''}`;

  const fl = Number(s.floating || 0);
  const flEl = $('stat-floating');
  flEl.textContent = signed(fl);
  flEl.className = 'stat-value ' + (fl > 0 ? 'pos-color' : fl < 0 ? 'neg-color' : 'flat-color');
  $('stat-floating-sub').textContent = `${s.open_count || 0} open · ${nf(s.open_volume || 0)} lots`;

  $('stat-pf').textContent = s.profit_factor === null || s.profit_factor === undefined ? (s.gross_profit > 0 ? '∞' : '—') : nf(s.profit_factor, 2);
  $('stat-pf-sub').textContent = `expectancy ${signed(s.expectancy || 0)}`;

  const bw = $('stat-bw');
  bw.innerHTML = `<span class="${Number(s.best) > 0 ? 'value-win' : ''}">${signed(s.best)}</span> / <span class="${Number(s.worst) < 0 ? 'value-loss' : ''}">${signed(s.worst)}</span>`;
  $('stat-bw-sub').textContent = `${s.best_symbol || '—'} / ${s.worst_symbol || '—'}`;

  $('stat-volume').textContent = nf(s.volume || 0);
  $('stat-volume-sub').textContent = `${s.max_win_streak || 0}W streak · ${s.max_loss_streak || 0}L streak`;

  document.querySelectorAll('#period-seg .seg-btn').forEach((b) => b.classList.toggle('active', b.dataset.period === period));
}

/* -------------------------------------------------------------- positions */
function renderPositions(d) {
  const body = $('positions-body');
  const list = d.positions || [];
  $('pos-count').textContent = list.length;
  const total = list.reduce((a, p) => a + Number(p.profit || 0) + Number(p.swap || 0), 0);
  const tot = $('pos-total');
  tot.textContent = signed(total);
  tot.className = 'panel-total ' + valClass(total);
  $('pos-empty').classList.toggle('hidden', list.length > 0);

  const seen = new Set();
  list.forEach((p) => {
    const key = String(p.ticket);
    seen.add(key);
    let tr = posRows.get(key);
    if (!tr) {
      tr = document.createElement('tr');
      tr.innerHTML = '<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td class="num"></td><td></td>';
      tr.classList.add('flash');
      posRows.set(key, tr);
      body.appendChild(tr);
    }
    const c = tr.children;
    const net = Number(p.profit || 0) + Number(p.swap || 0);
    setCell(c[0], p.symbol || '—');
    c[1].innerHTML = `<span class="${p.side === 'buy' ? 'side-buy' : 'side-sell'}">${esc((p.side || '').toUpperCase())}</span>`;
    setCell(c[2], nf(p.volume, 2));
    setCell(c[3], fmtPrice(p.price_open));
    setCell(c[4], fmtPrice(p.price_current));
    setCell(c[5], fmtPrice(p.sl));
    setCell(c[6], fmtPrice(p.tp));
    setCell(c[7], nf(p.swap, 2));
    setCell(c[8], signed(net));
    c[8].className = 'num ' + valClass(net);
    setCell(c[9], dateStr(p.time));
    const cls = rowClass(net);
    if (tr.dataset.rc !== cls) { tr.className = cls; tr.dataset.rc = cls; }
  });
  posRows.forEach((tr, key) => {
    if (!seen.has(key)) { tr.remove(); posRows.delete(key); }
  });
  // keep DOM order in sync with the data order
  list.forEach((p) => {
    const tr = posRows.get(String(p.ticket));
    if (tr && tr.nextSibling !== null) body.appendChild(tr);
  });
}
function setCell(node, v) { if (node && node.textContent !== String(v)) node.textContent = v; }

/* ---------------------------------------------------------------- history */
function histSince() {
  const nowS = S && S.server_epoch ? S.server_epoch : Math.floor(Date.now() / 1000);
  if (histPeriod === 'today') return (S && S.day_start) || (nowS - 86400);
  if (histPeriod === 'all') return 0;
  return nowS - Number(histPeriod) * 86400;
}
function historyRows() {
  if (!S) return [];
  const map = new Map();
  // a null/0 position_id must not collapse every such row onto one map key
  const keyOf = (r, i) => String(r.position_id || r.ticket || `row-${i}`);
  (extraRows || []).forEach((r, i) => map.set(keyOf(r, i), r));
  (S.history || []).forEach((r, i) => map.set(keyOf(r, i), r));
  let rows = Array.from(map.values());
  const since = histSince();
  rows = rows.filter((r) => Number(r.close_time || 0) >= since);
  if (histResult === 'win') rows = rows.filter((r) => Number(r.net_profit) > 0);
  if (histResult === 'loss') rows = rows.filter((r) => Number(r.net_profit) < 0);
  if (histSymbol) rows = rows.filter((r) => (r.symbol || '').toUpperCase() === histSymbol.toUpperCase());
  if (histSearch) {
    const q = histSearch.toLowerCase();
    rows = rows.filter((r) => String(r.comment || '').toLowerCase().includes(q) || String(r.ticket || '').includes(q) || String(r.position_id).includes(q));
  }
  rows.sort((a, b) => Number(b.close_time) - Number(a.close_time));
  return rows;
}
function renderHistory(d) {
  const rows = historyRows();
  // header sort: the server sends newest-first; any header click reorders this
  // local copy (no refetch needed). Ties fall back to close_time desc so the
  // list never jumps randomly between renders.
  const NUMERIC_KEYS = new Set(['close_time', 'open_time', 'volume', 'open_price', 'close_price', 'commission', 'swap', 'net_profit', 'magic']);
  const durOf = (r) => (Number(r.close_time || 0) - Number(r.open_time || 0));
  const valOf = (r) => {
    if (histSortKey === 'duration') return durOf(r);
    if (NUMERIC_KEYS.has(histSortKey)) return Number(r[histSortKey] || 0);
    return String(r[histSortKey] || '');
  };
  rows.sort((a, b) => {
    const av = valOf(a);
    const bv = valOf(b);
    let cmp = (typeof av === 'number' && typeof bv === 'number') ? (av - bv) : String(av).localeCompare(String(bv));
    if (!cmp) cmp = Number(b.close_time || 0) - Number(a.close_time || 0);
    return histSortDir === 'asc' ? cmp : -cmp;
  });
  document.querySelectorAll('#history-head-row th[data-key]').forEach((th) => {
    th.classList.toggle('sorted', th.dataset.key === histSortKey);
    th.classList.toggle('sorted-asc', th.dataset.key === histSortKey && histSortDir === 'asc');
    th.classList.toggle('sorted-desc', th.dataset.key === histSortKey && histSortDir === 'desc');
  });
  $('hist-count').textContent = rows.length;
  const rhEl = $('rail-hist-count');
  if (rhEl) rhEl.textContent = rows.length;
  const thEl = $('tab-badge-hist');
  if (thEl) thEl.textContent = rows.length;
  $('hist-empty').classList.toggle('hidden', rows.length > 0);

  const sig = histPeriod + '|' + histResult + '|' + histSymbol + '|' + histSearch + '|' + histSortKey + '|' + histSortDir + '|' + rows.length + '|' +
    (rows[0] ? rows[0].position_id : '') + '|' + (rows[rows.length - 1] ? rows[rows.length - 1].position_id : '') + '|' +
    rows.reduce((a, r) => a + Number(r.net_profit || 0), 0).toFixed(2);
  if (sig === histSig) return;
  histSig = sig;

  const body = $('history-body');
  const html = rows.slice(0, 600).map((r) => {
    const net = Number(r.net_profit || 0);
    return `<tr class="${rowClass(net)}">
      <td class="ts">${dateStr(r.close_time)}</td>
      <td>${esc(r.symbol || '')}</td>
      <td><span class="${r.side === 'buy' ? 'side-buy' : 'side-sell'}">${esc((r.side || '').toUpperCase())}</span></td>
      <td>${nf(r.volume, 2)}</td>
      <td>${fmtPrice(r.open_price)}</td>
      <td>${fmtPrice(r.close_price)}</td>
      <td>${nf(r.commission, 2)}</td>
      <td>${nf(r.swap, 2)}</td>
      <td class="num ${valClass(net)}">${signed(net)}</td>
      <td>${r.magic ? esc(r.magic) : '—'}</td>
      <td>${esc(r.comment || '')}${r.source && r.source !== 'mt5' ? ` <span class="muted">[${esc(r.source)}]</span>` : ''}</td>
      <td class="ts">${durStr(r.open_time, r.close_time)}</td>
    </tr>`;
  }).join('');
  body.innerHTML = html;

  const net = rows.reduce((a, r) => a + Number(r.net_profit || 0), 0);
  const wins = rows.filter((r) => Number(r.net_profit) > 0).length;
  const losses = rows.filter((r) => Number(r.net_profit) < 0).length;
  $('hist-foot').innerHTML = `showing ${Math.min(rows.length, 600)} of ${rows.length} · <span class="${valClass(net)}">${signed(net)}</span> · ${wins}W / ${losses}L · total ever: ${d.history_total || 0}`;

  const syms = Array.from(new Set(rows.map((r) => r.symbol).filter(Boolean))).sort();
  const dl = $('symbol-list');
  if (dl.dataset.n !== syms.join(',')) { dl.dataset.n = syms.join(','); dl.innerHTML = syms.map((s) => `<option value="${esc(s)}">`).join(''); }
}

/* ------------------------------------------------------------ market pressure watch dials */
let marketWatches = [];
let marketStatuses = {};

function findWatchForSymbol(sym) {
  if (!sym || !marketWatches || !marketWatches.length) return null;
  const sUpper = String(sym).toUpperCase().trim();
  let w = marketWatches.find(x => String(x.symbol || '').toUpperCase() === sUpper);
  if (!w) {
    w = marketWatches.find(x => String(x.symbol || '').toUpperCase().replace(/M$/, '').replace(/\.$/, '') === sUpper.replace(/M$/, '').replace(/\.$/, ''));
  }
  if (!w) {
    w = marketWatches.find(x => String(x.displayName || '').toUpperCase() === sUpper);
  }
  return w || marketWatches[0] || null;
}

function getMarketInfo(sym) {
  if (!sym) return { open: false, status: 'CLOSED', reason: 'EMPTY' };
  const sUpper = String(sym).toUpperCase().trim();
  // 1. Check live marketWatches array
  if (marketWatches && marketWatches.length) {
    let w = marketWatches.find(x => String(x.symbol || '').toUpperCase() === sUpper);
    if (!w) {
      w = marketWatches.find(x => String(x.symbol || '').toUpperCase().replace(/M$/, '').replace(/\.$/, '') === sUpper.replace(/M$/, '').replace(/\.$/, ''));
    }
    if (!w) {
      w = marketWatches.find(x => String(x.displayName || '').toUpperCase() === sUpper);
    }
    if (w && w.market_open !== undefined) {
      return {
        open: !!w.market_open,
        status: w.market_status || (w.market_open ? 'OPEN' : 'CLOSED'),
        reason: w.market_reason || '',
        message: w.market_message || ''
      };
    }
  }
  // 2. Check S.market_statuses or cached marketStatuses map
  const stMap = (S && S.market_statuses) || marketStatuses;
  if (stMap) {
    const direct = stMap[sym] || stMap[sUpper] || stMap[sUpper.replace(/M$/, '').replace(/\.$/, '')];
    if (direct && direct.open !== undefined) {
      return direct;
    }
  }
  // 3. Fallback schedule check
  const is247 = sUpper.includes('BTC') || sUpper.includes('ETH') || sUpper.includes('CRYPTO') || sUpper.includes('247');
  if (is247) return { open: true, status: 'OPEN', reason: 'CRYPTO_247', message: 'Crypto 24/7' };
  if (sUpper === 'DXY' || sUpper.includes('INDEX')) return { open: false, status: 'UNAVAILABLE', reason: 'NOT_FOUND', message: 'Not available' };

  const now = new Date();
  const utcDay = now.getUTCDay(); // 0=Sun, 1=Mon, ..., 5=Fri, 6=Sat
  const utcHour = now.getUTCHours();
  const utcMin = now.getUTCMinutes();

  // Weekend: Friday 21:55 UTC to Sunday 21:00 UTC
  if (utcDay === 6) return { open: false, status: 'CLOSED', reason: 'WEEKEND_CLOSED', message: 'Weekend closure' };
  if (utcDay === 0 && utcHour < 21) return { open: false, status: 'CLOSED', reason: 'WEEKEND_CLOSED', message: 'Weekend closure' };
  if (utcDay === 5 && (utcHour > 21 || (utcHour === 21 && utcMin >= 55))) return { open: false, status: 'CLOSED', reason: 'WEEKEND_CLOSED', message: 'Weekend closure' };

  // Daily break: Mon-Thu 21:00 UTC to 22:00 UTC for gold/metals, crude oil, and US indices
  const isBreakMarket = sUpper.includes('XAU') || sUpper.includes('GOLD') || sUpper.includes('OIL') || sUpper.includes('TEC') || sUpper.includes('US30') || sUpper.includes('100');
  if (isBreakMarket && utcHour === 21) {
    return { open: false, status: 'CLOSED', reason: 'SESSION_CLOSED', message: 'Daily break (21:00-22:00 UTC)' };
  }

  return { open: true, status: 'OPEN', reason: 'DEFAULT' };
}

function isMarketOpen(sym) {
  return getMarketInfo(sym).open !== false;
}

function updateAllMarketChips() {
  document.querySelectorAll('.chip[data-sym]').forEach(btn => {
    const sym = btn.dataset.sym;
    if (!sym) return;
    const isOpen = isMarketOpen(sym);
    btn.classList.toggle('market-off', !isOpen);
    btn.classList.toggle('disabled', !isOpen);
    if (!isOpen) {
      btn.setAttribute('title', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ (Market Closed)` : `${sym} market is currently closed`);
    } else if (btn.getAttribute('title')?.includes('closed') || btn.getAttribute('title')?.includes('বন্ধ')) {
      btn.removeAttribute('title');
    }
    let offTag = btn.querySelector('.chip-off-tag');
    if (!isOpen) {
      if (!offTag) {
        offTag = document.createElement('span');
        offTag.className = 'chip-off-tag';
        offTag.textContent = 'OFF';
        btn.appendChild(offTag);
      }
    } else {
      if (offTag) offTag.remove();
    }
  });

  // Also update ticker tape items
  document.querySelectorAll('.ticker-item[data-sym], .login-ticker-item[data-sym]').forEach(item => {
    const sym = item.dataset.sym;
    if (!sym) return;
    const isOpen = isMarketOpen(sym);
    item.classList.toggle('ticker-off', !isOpen);
    let offPill = item.querySelector('.ticker-off-pill');
    if (!isOpen) {
      if (!offPill) {
        offPill = document.createElement('span');
        offPill.className = 'ticker-off-pill';
        offPill.textContent = 'OFF';
        const symEl = item.querySelector('.ticker-sym, .login-ticker-sym');
        if (symEl) symEl.appendChild(offPill);
        else item.appendChild(offPill);
      }
    } else {
      if (offPill) offPill.remove();
    }
  });
}


function renderVuSegments(pct, type) {
  const activeCount = Math.min(8, Math.max(1, Math.round((Number(pct || 50) / 100) * 8)));
  let html = '';
  for (let i = 0; i < 8; i++) {
    const isActive = type === 'buy' ? (i >= 8 - activeCount) : (i < activeCount);
    html += `<span class="vu-seg${isActive ? ' active' : ''}"></span>`;
  }
  return html;
}

function generateGaugeSvg(sym, dominant, buyerPct, sellerPct) {
  let ticksSvg = '';
  for (let th = -65; th <= 65; th += 5) {
    const rad = (th * Math.PI) / 180;
    const sin = Math.sin(rad);
    const cos = Math.cos(rad);
    const isMajor = Math.abs(th) % 15 === 0 || Math.abs(th) === 65 || th === 0;
    const rOut = 84;
    const rIn = isMajor ? 73 : 78;
    const x1 = (110 + rIn * sin).toFixed(1);
    const y1 = (106 - rIn * cos).toFixed(1);
    const x2 = (110 + rOut * sin).toFixed(1);
    const y2 = (106 - rOut * cos).toFixed(1);
    const strokeColor = th < -5 ? '#22c55e' : (th > 5 ? '#ef4444' : '#38bdf8');
    const strokeW = isMajor ? '2.4' : '1.2';
    const opacity = isMajor ? '1' : '0.6';
    ticksSvg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${strokeColor}" stroke-width="${strokeW}" opacity="${opacity}" />`;
  }

  return `
    <svg class="watch-gauge-svg" viewBox="0 0 220 160">
      <defs>
        <linearGradient id="mGradBuy" x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#059669" />
          <stop offset="100%" stop-color="#22c55e" />
        </linearGradient>
        <linearGradient id="mGradSell" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#ef4444" />
          <stop offset="100%" stop-color="#b91c1c" />
        </linearGradient>
        <filter id="mGlowGreen" x="-25%" y="-25%" width="150%" height="150%">
          <feDropShadow dx="0" dy="0" stdDeviation="3" flood-color="#22c55e" flood-opacity="0.75"/>
        </filter>
        <filter id="mGlowRed" x="-25%" y="-25%" width="150%" height="150%">
          <feDropShadow dx="0" dy="0" stdDeviation="3" flood-color="#ef4444" flood-opacity="0.75"/>
        </filter>
        <filter id="mGlowBlue" x="-25%" y="-25%" width="150%" height="150%">
          <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#38bdf8" flood-opacity="0.8"/>
        </filter>
      </defs>

      <!-- Recessed Machine Guide Channel -->
      <path d="M 35.7 71.3 A 82 82 0 0 1 184.3 71.3" fill="none" stroke="rgba(15, 23, 42, 0.95)" stroke-width="13" stroke-linecap="round" />
      <path d="M 35.7 71.3 A 82 82 0 0 1 184.3 71.3" fill="none" stroke="rgba(255, 255, 255, 0.12)" stroke-width="1.2" stroke-linecap="round" />

      <!-- Glowing High-Voltage Pressure Arcs -->
      <path d="M 35.7 71.3 A 82 82 0 0 1 110 24" fill="none" stroke="url(#mGradBuy)" stroke-width="8" stroke-linecap="round" filter="url(#mGlowGreen)" opacity="1" />
      <path d="M 110 24 A 82 82 0 0 1 184.3 71.3" fill="none" stroke="url(#mGradSell)" stroke-width="8" stroke-linecap="round" filter="url(#mGlowRed)" opacity="1" />

      <!-- Concentric Micro-Track -->
      <path d="M 42 75 A 72 72 0 0 1 178 75" fill="none" stroke="rgba(255, 255, 255, 0.12)" stroke-width="1" stroke-dasharray="3 3" />

      <!-- Precision Radiated Ticks -->
      ${ticksSvg}

      <!-- Center Zero-Index (12 o'clock) -->
      <polygon points="110,17 105,25 115,25" fill="#38bdf8" filter="url(#mGlowBlue)" />
      <circle cx="110" cy="30" r="2.2" fill="#38bdf8" filter="url(#mGlowBlue)" />
      <text x="110" y="13" class="gauge-center-zero">0Δ</text>

      <!-- Cockpit Etchings -->
      <text x="21" y="89" class="gauge-label-buy">BUY</text>
      <text x="21" y="100" class="gauge-sub-buy">100%</text>

      <text x="199" y="89" class="gauge-label-sell">SELL</text>
      <text x="199" y="100" class="gauge-sub-sell">100%</text>

      <text x="68" y="48" class="gauge-tech-hash">« TORQUE</text>
      <text x="152" y="48" class="gauge-tech-hash">PRESSURE »</text>

      <!-- Crosshairs -->
      <line x1="102" y1="106" x2="118" y2="106" stroke="rgba(56, 189, 248, 0.35)" stroke-width="1.2" />
      <line x1="110" y1="98" x2="110" y2="114" stroke="rgba(56, 189, 248, 0.35)" stroke-width="1.2" />
      <circle cx="110" cy="106" r="12" fill="none" stroke="rgba(56, 189, 248, 0.25)" stroke-width="1.2" stroke-dasharray="2 2" />
    </svg>
  `;
}


function renderMarketWatches() {
  const grid = $('market-watch-grid');
  if (!grid) return;
  if (!marketWatches || marketWatches.length === 0) {
    grid.innerHTML = '<div style="color:var(--txt-dim);padding:14px;font-size:13px;grid-column:1/-1;text-align:center;">Analyzing market symbols and order flow...</div>';
    return;
  }

  // The pipeline shows exactly ONE dial: the market picked in the top box.
  const activeSym = flowSymbol();
  const row = activeSym ? findWatchForSymbol(activeSym) : null;

  // Overall market pressure summary (patched live on every stream push)
  updateWatchSummaryPill();

  if (!activeSym || !row) {
    if (grid.dataset.one !== String(activeSym)) {
      grid.dataset.one = String(activeSym);
      grid.innerHTML = `<div class="watch-dial-empty">${esc(activeSym ? t('flowNoDial') : t('flowNoDialShort'))}</div>`;
    }
    return;
  }
  grid.dataset.one = String(activeSym);

  grid.innerHTML = [row].map((w, idx) => {
    const sym = w.symbol || '';
    const isActive = sym.toUpperCase() === activeSym.toUpperCase();
    const dominant = String(w.dominant || 'NEUTRAL').toUpperCase();
    const domCls = watchDominantClass(dominant);
    const needleDeg = Number(w.needle_deg || 0);
    const buyerPct = Number(w.buyer_pct || 50);
    const sellerPct = Number(w.seller_pct || 50);
    const digits = Number(w.digits || 2);
    const priceStr = w.ask ? nf(w.ask, digits) : '—';
    const spreadStr = esc(w.spread !== undefined ? String(w.spread) : '—');
    const { flow, arrow: flowArrow } = watchFlowMeta(w.candle_flow);
    const isLive = w.live !== false;
    const isOpen = w.market_open !== false;
    const domBadgeText = !isOpen ? 'MARKET OFF' : watchAnnunciatorText(dominant, buyerPct, sellerPct);
    const cardTitle = !isOpen
      ? (LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ (Market Closed)` : `${sym} market is currently closed`)
      : (LANG === 'bn' ? 'ক্লিক করে এই মার্কেট সিলেক্ট করুন' : 'Click to select this market');
    const stateBadgeHtml = !isOpen
      ? `<span class="machine-off-badge${isActive ? ' active-off' : ''}">MARKET OFF</span>`
      : (isActive ? `<span class="machine-engaged-badge">ENGAGED</span>` : `<span class="machine-standby-badge">STANDBY</span>`);

    return `
      <div class="watch-card${isActive ? ' active-target' : ''}${!isOpen ? ' market-off' : ''}" data-sym="${esc(sym)}" title="${esc(cardTitle)}">
        <!-- 4 Machine Hex Socket Screws -->
        <div class="machine-bolt bolt-tl"></div>
        <div class="machine-bolt bolt-tr"></div>
        <div class="machine-bolt bolt-bl"></div>
        <div class="machine-bolt bolt-br"></div>

        <!-- Machine Card Top Strip -->
        <div class="machine-card-header">
          <div class="machine-chip-id">
            <span class="machine-led-pulse ${!isOpen ? 'off' : domCls}"></span>
            <span class="machine-label-text">CHRONO-${String(idx + 1).padStart(2, '0')}</span>
          </div>
          <span class="machine-clock${isLive ? '' : ' sim'}" title="${isLive ? 'Last live reading' : (w.price_source === 'public' ? 'Public feed price — Yahoo / Binance (MT5 not connected)' : 'Simulated data — MT5 not connected')}">${watchClockText(w.updated_at)}</span>
          ${stateBadgeHtml}
        </div>

        <!-- Machine Watch Dial Case -->
        <div class="watch-case">
          <div class="watch-bezel">
            <div class="watch-bezel-rim"></div>
            
            ${generateGaugeSvg(sym, dominant, buyerPct, sellerPct)}

            <!-- Rotating Machine Needle -->
            <div class="watch-hand-wrap" style="transform: rotate(${needleDeg}deg);">
              <div class="watch-hand-counterweight"></div>
              <div class="watch-hand-lance ${domCls}">
                <div class="lance-core"></div>
              </div>
              <div class="watch-hand-boss">
                <div class="boss-center-screw"></div>
              </div>
            </div>

            <!-- Center Hub: Market Name in the Middle -->
            <div class="watch-center-hub">
              <div class="watch-market-plate">
                <span class="watch-market-symbol">${esc(w.displayName || sym)}</span>
              </div>
              <div class="watch-price-display">${priceStr}</div>
              <div class="watch-annunciator ${!isOpen ? 'off' : domCls}">
                <span class="annunciator-lamp ${!isOpen ? 'off' : ''}"></span>
                <span class="annunciator-text">${domBadgeText}</span>
              </div>
            </div>

            <div class="watch-glass-sheen"></div>
          </div>
        </div>

        <!-- Machine Lower Telemetry Deck -->
        <div class="watch-telemetry-deck">
          <div class="watch-pressure-split">
            <span class="pressure-label buyer">
              <span class="tele-dot green"></span>
              BUY: <b>${buyerPct}%</b>
            </span>
            <span class="tele-divider">//</span>
            <span class="pressure-label seller">
              SELL: <b>${sellerPct}%</b>
              <span class="tele-dot red"></span>
            </span>
          </div>

          <!-- 16-Segment Dual LED VU-Meter -->
          <div class="vu-meter-rack" title="Buyer: ${buyerPct}% · Seller: ${sellerPct}%">
            <div class="vu-meter-bar vu-buy">
              ${renderVuSegments(buyerPct, 'buy')}
            </div>
            <div class="vu-meter-divider"></div>
            <div class="vu-meter-bar vu-sell">
              ${renderVuSegments(sellerPct, 'sell')}
            </div>
          </div>

          <!-- Big Buyer / Seller pressure readout (the space under the dial) -->
          <div class="watch-pressure-bigrack" title="Buyer: ${buyerPct}% · Seller: ${sellerPct}%">
            <div class="wpb wpb-buyer">
              <span class="wpb-name">BUYER</span>
              <b class="wpb-val">${buyerPct}%</b>
              <span class="wpb-sub">${LANG === 'bn' ? 'বায়ার চাপ' : 'buyer pressure'}</span>
            </div>
            <div class="wpb-vs">⇄</div>
            <div class="wpb wpb-seller">
              <span class="wpb-name">SELLER</span>
              <b class="wpb-val">${sellerPct}%</b>
              <span class="wpb-sub">${LANG === 'bn' ? 'সেলার চাপ' : 'seller pressure'}</span>
            </div>
          </div>

          <!-- Machine Specs Row: price / watch direction / flow / spread / target -->
          <div class="machine-specs-row">
            <div class="spec-item">
              <span class="spec-k">PRICE</span>
              <span class="spec-v spec-bid">${w.bid ? nf(w.bid, digits) : '—'}</span>
            </div>
            <div class="spec-item">
              <span class="spec-k">DIR</span>
              <span class="spec-v spec-direction ${String(w.direction || 'FLAT').toLowerCase()}">${String(w.direction || 'FLAT').toUpperCase() === 'UP' ? '▲ UP' : String(w.direction || 'FLAT').toUpperCase() === 'DOWN' ? '▼ DOWN' : '► FLAT'}</span>
            </div>
            <div class="spec-item">
              <span class="spec-k">FLOW</span>
              <span class="spec-v flow-val spec-flow ${flow}">${flowArrow} ${flow.toUpperCase()}</span>
            </div>
            <div class="spec-item">
              <span class="spec-k">SPREAD</span>
              <span class="spec-v spec-spread">${spreadStr}</span>
            </div>
            <div class="spec-item">
              <span class="spec-k">TARGET</span>
              <span class="spec-v target-btn spec-target${!isOpen ? ' disabled' : (isActive ? ' active' : '')}" title="${!isOpen ? (LANG === 'bn' ? 'মার্কেট বন্ধ' : 'Market closed') : ''}">${!isOpen ? 'OFF' : (isActive ? 'ACTIVE' : 'SELECT')}</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join('');


  // The dial mirrors the market picker at the top of the pipeline - the box owns
  // the choice, so the grid stays read-only.
}


/* ------------------------------------------------------------ auto trading */
function renderBot(d) {
  const b = d.bot || {};
  const cfg = b.config || {};
  const running = !!b.running;
  const live = !!d.connected;
  const chip = $('bot-chip');
  chip.className = 'bot-chip ' + (!live ? 'offline' : running ? 'running' : 'stopped');
  chip.textContent = !live ? t('botOffline') : running ? t('botRunning') : t('botStopped');

  $('bot-start').classList.toggle('active', running);
  $('bot-stop').classList.toggle('active', !running && live);

  // ---- symbol: pick gold or BTC; whatever is picked is what gets traded
  const wanted = b.symbol_requested || cfg.symbol || 'XAUUSDm';
  const sym = b.symbol || '';
  const symOk = !!b.symbol_ok && sym;
  const shown = symOk ? sym : wanted;
  const subFrom = b.symbol_substituted || '';
  $('bot-symbol').textContent = shown;

  let priceStr = '';
  if (b.ask && b.bid) {
    const askStr = fmtPrice(b.ask);
    const bidStr = fmtPrice(b.bid);
    const spStr = b.spread !== undefined ? esc(String(b.spread)) : '';
    priceStr = ` · Ask <b>${askStr}</b> / Bid <b>${bidStr}</b> <span style="font-size:11px;color:var(--txt-faint)">(Spread ${spStr})</span>`;
  }
  $('bot-symbol-name').innerHTML = `${esc(shown)}${priceStr}`;
  $('bot-symbol-name').style.color = symOk ? 'var(--accent)' : '#fca5a5';

  // ---- market buttons: click a button, that market is selected and traded
  const pick = $('bot-market-chips');
  const options = (b.symbol_options && b.symbol_options.length ? b.symbol_options
                  : b.gold_symbols && b.gold_symbols.length ? b.gold_symbols : []);
  const list = options.length ? options.slice() : [shown];
  if (!list.includes(shown)) list.unshift(shown);
  if (live) {
    const sig = list.join(',') + '|' + shown;
    if (pick.dataset.n !== sig) {
      pick.dataset.n = sig;
      pick.innerHTML = list.map((s) =>
        `<button type="button" class="chip${s === shown ? ' active' : ''}" data-sym="${esc(s)}">${esc(s)}</button>`).join('');
    }
    pick.classList.remove('hidden');
  } else {
    pick.classList.remove('hidden');
    pick.querySelectorAll('.chip').forEach(c => {
      c.classList.toggle('active', c.dataset.sym === shown);
    });
  }
  setBotCurveSymbol('bot1', shown);

  const lotEl = $('bot-lot');
  if (document.activeElement !== lotEl && cfg.lot !== undefined && Number(lotEl.value) !== Number(cfg.lot)) {
    lotEl.value = cfg.lot;
  }
  if (b.volume_min) { lotEl.min = b.volume_min; lotEl.step = b.volume_step || 0.01; }

  const gapEl = $('bot-gap');
  const gapCfg = Number(cfg.distance || 2);
  if (document.activeElement !== gapEl && gapCfg && Number(gapEl.value) !== gapCfg) gapEl.value = gapCfg;

  const slEl = $('bot-sl');
  const slCfg = Number(b.sl_distance || (gapCfg * 2));
  if (document.activeElement !== slEl && slCfg && Number(slEl.value) !== slCfg) slEl.value = slCfg.toFixed(2);

  const usd = (v) => nf(v, 2);
  const gap = Number(gapEl.value) || gapCfg;
  const slDist = Number(slEl.value) || slCfg;
  const lot = Number(lotEl.value) || Number(cfg.lot || 0.01);
  const mpp = Number(b.money_per_price || 0) * (Number(cfg.lot || 0) > 0 ? lot / Number(cfg.lot) : 1);
  const risk = b.risk_per_trade && Number(cfg.lot) ? (Number(b.risk_per_trade) * lot / Number(cfg.lot)) : (slDist * (mpp || 1));

  const conds = [
    `${esc(t('condGap'))}: <b>$${usd(gap)}</b> — <b>BuyStop = ask + $${usd(gap)}</b> · <b>SellStop = bid − $${usd(gap)}</b>`,
    `${esc(t('condSL'))} <b>±$${usd(slDist)}</b> (Buy ও Sell দুটোর SL একই দূরত্বে)`,
    `${esc(t('condLot'))}: <b>${lot}</b> · ${esc(t('botRisk'))} <b>$${usd(risk)}</b>`,
    `${esc(t('condLock'))} <b>$${usd(cfg.lock_trigger)}</b> → <b>SL $${usd(cfg.lock_amount)}</b>`,
    `${esc(t('condTrail'))} <b>$${usd(cfg.trail_start)}</b> → <b>SL = ${nf(cfg.trail_pct || 75, 0)}% profit</b>`,
    `${esc(t('condSL2'))}`,
    esc(t('condReArm')),
  ];
  $('bot-cond').innerHTML = conds.map((c) => `<li>${c}</li>`).join('');

  const orders = b.orders || [];
  $('bot-orders').innerHTML = orders.length
    ? `<b>${esc(t('botPendings'))}:</b> ` + orders.map((o) =>
        `${esc(o.kind || '')} ${nf(o.volume, 2)} @ <b>${fmtPrice(o.price_open)}</b> (SL ${fmtPrice(o.sl)})`).join(' · ')
    : `<b>${esc(t('botPendings'))}:</b> ${esc(t('botNoPending'))}`;

  const pos = b.positions || [];
  $('bot-pos').innerHTML = pos.length
    ? `<b>${esc(t('botTrade'))}:</b> ` + pos.map((p) => {
        const net = Number(p.net || 0);
        return `${esc(String(p.side || '').toUpperCase())} ${nf(p.volume, 2)} @ ${fmtPrice(p.price_open)} · ` +
          `SL <b>${fmtPrice(p.sl)}</b> · <span class="${net >= 0 ? 'up' : 'down'}">${signed(net)}</span>`;
      }).join(' · ')
    : `<b>${esc(t('botTrade'))}:</b> ${esc(t('botNoTrade'))}`;

  const stats = b.stats || {};
  const last = b.last_error
    ? `<span style="color:#fca5a5">${esc(b.last_error)}</span>`
    : esc(b.last_action || '—');
  $('bot-last').innerHTML = `${esc(t('botMarketNow'))}: <b>${esc(shown)}</b> · ` +
    `${stats.opened || 0} ${esc(t('botOpened'))} / ${stats.closed || 0} ${esc(t('botClosed'))} · ${last}`;
  $('bot-last').className = 'bot-line' + (b.last_error ? ' warn' : '');
  renderPriceMap('bot', b);
}

/* ------------------------------------------------------- live level map */
/* Shared digital panel: WHERE on the market price each bot has parked its
   levels - pending orders above/below + open positions, newest first.      */
function renderPriceMap(prefix, b) {
  const wrap = $(`${prefix}-pricemap`);
  if (!wrap) return;
  const priceEl = $(`${prefix}-map-price`);
  const rowsEl = $(`${prefix}-map-rows`);
  if (!priceEl || !rowsEl) return;

  const price = Number(b.map_price || 0);
  priceEl.textContent = price ? fmtPrice(price) : '—';

  const rows = Array.isArray(b.map) ? b.map : [];
  if (!rows.length) {
    rowsEl.innerHTML = `<div class="pm-empty">${LANG === 'bn' ? 'এই বট এখনো মার্কেটে কোনো লেভেল সেট করেনি' : 'No levels set on the market yet'}</div>`;
    return;
  }
  const isBn = LANG === 'bn';
  rowsEl.innerHTML = rows.map((r) => {
    const kind = String(r.kind || '').toLowerCase();
    const isPos = r.type === 'position';
    const dist = Number(r.dist || 0);
    const dir = dist > 0 ? '↑' : dist < 0 ? '↓' : '·';
    const distTxt = price ? `${dir} ${nf(Math.abs(dist), 2)}` : '';
    const net = Number(r.net || 0);
    const netTxt = isPos ? `<span class="pm-net ${net >= 0 ? 'up' : 'down'}">${signed(net)}</span>` : '';
    const state = isPos ? (isBn ? 'ওপেন' : 'OPEN') : (isBn ? 'পেন্ডিং' : 'PENDING');
    return `<div class="pm-row ${kind} ${isPos ? 'pos' : 'pend'}">
      <span class="pm-kind">${esc(r.label || kind.toUpperCase())}</span>
      <span class="pm-price">${fmtPrice(r.price)}</span>
      <span class="pm-lot">${nf(r.lot, 2)}</span>
      <span class="pm-dist">${distTxt}</span>
      ${netTxt}
      <span class="pm-state">${state}</span>
    </div>`;
  }).join('');
}


async function botPost(path, body) {
  try {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) {
      const msg = (j.error && j.error.message) || 'Request failed';
      toast('error', msg);
      showConnectError(j.error || null);
      return j;
    }
    if (j.bot) { if (S) { S.bot = j.bot; renderBot(S); } }
    return j;
  } catch (e) { toast('error', String(e)); return {}; }
}

function bindBotUI() {
  $('bot-start').addEventListener('click', async () => {
    const b = (S && S.bot) || {};
    const sym = b.symbol || 'XAUUSDm';
    if (!isMarketOpen(sym)) {
      toast('error', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ থাকায় অটো ট্রেডিং চালু করা যাবে না` : `Cannot start bot: ${sym} market is currently closed`);
      return;
    }
    const lot = Number($('bot-lot').value || 0.01);
    const risk = Number(b.money_per_price || 1) * 2 * Number((b.config || {}).distance || 2);
    const ok = window.confirm(
      `${t('botConfirmStart')}\n\nLot: ${lot}\nRisk ~$${risk.toFixed(2)} ${t('botPerTrade')}`);
    if (!ok) return;
    const res = await botPost('/api/bot/start', { lot });
    if (res && res.ok) toast('ok', t('botStarted'));
  });
  $('bot-stop').addEventListener('click', async () => {
    const res = await botPost('/api/bot/stop', {});
    if (res && res.ok) toast('ok', t('botStoppedMsg'));
  });
  $('bot-close').addEventListener('click', async () => {
    if (!window.confirm(t('botConfirmClose'))) return;
    const res = await botPost('/api/bot/close', {});
    if (res && res.ok) toast('ok', `${t('botClosedMsg')} (${res.closed || 0})`);
  });
  $('bot-lot').addEventListener('change', () => {
    void botPost('/api/bot/config', { lot: Number($('bot-lot').value || 0.01) });
  });
  $('bot-gap').addEventListener('change', () => {
    const gap = Number($('bot-gap').value || 2);
    const body = { distance: gap };
    // keep the SL in step with the gap until the user edits the SL box themselves
    if (!$('bot-sl').dataset.touched) {
      $('bot-sl').value = (gap * 2).toFixed(2);
      body.sl_distance = 0;
    }
    void botPost('/api/bot/config', body);
  });
  $('bot-sl').addEventListener('change', () => {
    $('bot-sl').dataset.touched = '1';
    void botPost('/api/bot/config', { sl_distance: Number($('bot-sl').value || 0) });
  });
  $('bot-market-chips').addEventListener('click', async (e) => {
    const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
    if (!chip || !chip.dataset.sym) return;
    const sym = chip.dataset.sym;
    if (chip.classList.contains('market-off') || !isMarketOpen(sym)) {
      toast('warn', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)` : `${sym} market is currently closed`);
      return;
    }

    // Immediate visual response
    document.querySelectorAll('#bot-market-chips .chip')
      .forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
    $('bot-symbol').textContent = sym;
    $('bot-symbol-name').textContent = sym;
    $('bot-market-chips').dataset.n = ''; // reset signature cache
    setBotCurveSymbol('bot1', sym);

    const res = await botPost('/api/bot/config', { symbol: sym });
    if (res && res.ok) {
      if (res.bot) {
        if (S) S.bot = res.bot;
        renderBot(S);
      }
      toast('ok', `${t('botMarketSwitched')}: ${sym}`);
      if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
    }
  });
}

/* ------------------------------------------------------------ auto trading bot 2 timer */
let bot2TrendSecRemaining = 5;

function getBot2ActiveSeconds() {
  const b = (S && S.bot2) || {};
  const interval = Number(b.trend_interval || 2.5);
  const checkedTs = Number(b.trend_checked_ts || 0);
  const now = Date.now() / 1000;
  if (checkedTs > 0) {
    const elapsed = Math.max(0, now - checkedTs);
    const remain = interval - (elapsed % interval);
    return Math.max(0.1, remain);
  }
  const secOfCycle = (now % interval);
  return Math.max(0.1, interval - secOfCycle);
}

function updateBot2TimerElements(sec) {
  const b = (S && S.bot2) || {};
  const cfg = b.config || {};
  const interval = Number(b.trend_interval || 2.5);
  const mTrend = String(b.market_trend || 'NEUTRAL').toUpperCase();
  const trendMode = !!(b.watch_enabled !== undefined ? b.watch_enabled : (b.trend_mode !== undefined ? b.trend_mode : cfg.watch_enabled));
  const reverseOn = !!(b.reverse !== undefined ? b.reverse : cfg.reverse);
  const arrow = mTrend === 'UP' ? ' ▲' : (mTrend === 'DOWN' ? ' ▼' : ' ▬');

  const numSec = Number(sec);
  const cleanSec = Math.max(0, Math.ceil(numSec * 2) / 2);
  const secFormatted = (cleanSec % 1 === 0 ? cleanSec.toFixed(0) : cleanSec.toFixed(1)) + 's';

  // 1. Top Trend Pill: Watch: BUYER ▲ / SELLER ▼ / NEUTRAL ▬ (⏱️ 2.5s) (ACTIVE)
  const trendPill = $('bot2-trend-pill');
  if (trendPill) {
    trendPill.className = 'pill trend-pill ' + (mTrend === 'UP' ? 'up' : (mTrend === 'DOWN' ? 'down' : 'neutral')) + (trendMode ? ' active' : '');
    const modeLabel = trendMode ? (LANG === 'bn' ? 'ওয়াচ' : 'Watch') : (LANG === 'bn' ? 'ক্যান্ডেল' : 'Candle');
    const dirLabel = trendMode
      ? (mTrend === 'UP' ? 'BUYER ▲' : (mTrend === 'DOWN' ? 'SELLER ▼' : 'NEUTRAL ▬'))
      : (mTrend === 'UP' ? 'UP ▲' : (mTrend === 'DOWN' ? 'DOWN ▼' : 'FLAT ▬'));
    trendPill.textContent = `${modeLabel}: ${dirLabel} (⏱️ ${secFormatted})`
      + (trendMode ? '' : (LANG === 'bn' ? ' (ওয়াচ OFF)' : ' (WATCH OFF)'))
      + (reverseOn ? (LANG === 'bn' ? ' ⇄ রিভার্স' : ' ⇄ REVERSE') : '');
    trendPill.title = LANG === 'bn' ? `পরবর্তী ২.৫ সেকেন্ড চেক: ${secFormatted} পর (ক্লিক করে এখনই চেক করুন)` : `Next 2.5s check in ${secFormatted} (Click to check now)`;
  }

  // 2. Card Countdown Pill: ⏱️ 2.5s
  const countdownEl = $('bot2-trend-countdown');
  if (countdownEl) {
    if (cleanSec <= 0 || cleanSec === interval) {
      countdownEl.textContent = LANG === 'bn' ? '⏱️ চেক হচ্ছে...' : '⏱️ Checking...';
      countdownEl.className = 'trend-countdown-pill urgent';
    } else {
      countdownEl.textContent = `⏱️ ${secFormatted}`;
      countdownEl.className = 'trend-countdown-pill' + (cleanSec <= 1 ? ' urgent' : '');
    }
  }

  // 3. Progress Bar Fill: 100% down to 0%
  const bar = $('bot2-trend-bar');
  if (bar) {
    const pct = Math.max(0, Math.min(100, (cleanSec / interval) * 100));
    bar.style.width = pct + '%';
  }
}

function tickBot2TrendCountdown() {
  const sec = getBot2ActiveSeconds();
  bot2TrendSecRemaining = sec;
  updateBot2TimerElements(sec);
}

/* ------------------------------------------------------------ auto trading bot 2 */
function renderBot2(d) {
  const b = d.bot2 || {};
  const cfg = b.config || {};
  const running = !!b.running;
  const live = !!d.connected;
  const chip = $('bot2-chip');
  if (chip) {
    chip.className = 'bot-chip ' + (!live ? 'offline' : running ? 'running' : 'stopped');
    chip.textContent = !live ? t('botOffline') : running ? t('botRunning') : t('botStopped');
  }

  $('bot2-start').classList.toggle('active', running);
  $('bot2-stop').classList.toggle('active', !running && live);
  const trendMode = !!(b.watch_enabled !== undefined ? b.watch_enabled : (b.trend_mode !== undefined ? b.trend_mode : cfg.watch_enabled));
  const reverseOn = !!(b.reverse !== undefined ? b.reverse : cfg.reverse);
  const watchMin = Number(b.watch_min_pct !== undefined ? b.watch_min_pct : (cfg.watch_min_pct || 60));
  const trendBtn = $('bot2-trend');
  if (trendBtn) trendBtn.classList.toggle('active', trendMode);

  const trendStatus = $('bot2-trend-status');
  if (trendStatus) {
    trendStatus.textContent = trendMode ? 'ON' : 'OFF';
    trendStatus.className = 'trend-badge ' + (trendMode ? 'active' : 'deactive');
  }

  const revBtn = $('bot2-reverse');
  if (revBtn) revBtn.classList.toggle('active', reverseOn);
  const revStatus = $('bot2-reverse-status');
  if (revStatus) {
    revStatus.textContent = reverseOn ? 'ON' : 'OFF';
    revStatus.className = 'trend-badge ' + (reverseOn ? 'active' : 'deactive');
  }

  const mTrend = String(b.market_trend || 'NEUTRAL').toUpperCase();
  const wBuyer = b.watch_buyer_pct !== undefined ? Number(b.watch_buyer_pct) : null;
  const wSeller = b.watch_seller_pct !== undefined ? Number(b.watch_seller_pct) : null;
  const pressureTxt = (wBuyer !== null && wSeller !== null)
    ? (LANG === 'bn' ? `ওয়াচ: বায়ার ${wBuyer.toFixed(1)}% · সেলার ${wSeller.toFixed(1)}%` : `Watch: BUYER ${wBuyer.toFixed(1)}% · SELLER ${wSeller.toFixed(1)}%`)
    : '';
  const trendDetails = b.trend_details || (mTrend === 'UP' ? 'Watch: BUYER pressure' : (mTrend === 'DOWN' ? 'Watch: SELLER pressure' : 'Watch: NEUTRAL - no trade'));
  const trendScore = b.trend_score !== undefined ? Number(b.trend_score) : 0;
  const scoreSign = trendScore > 0 ? `+${trendScore}` : `${trendScore}`;

  // Watch Pressure Trend Check System Box (NEUTRAL = neutral grey, no trading)
  const trendBox = $('bot2-trend-box');
  if (trendBox) {
    trendBox.className = 'bot2-trend-box ' + (mTrend === 'UP' ? 'trend-up' : (mTrend === 'DOWN' ? 'trend-down' : 'trend-neutral'));
  }
  const radarDot = $('bot2-radar-dot');
  if (radarDot) {
    radarDot.className = 'trend-radar-dot' + (mTrend === 'DOWN' ? ' down' : (mTrend === 'NEUTRAL' ? ' neutral' : ''));
  }
  const trendVal = $('bot2-trend-value');
  if (trendVal) {
    trendVal.className = 'trend-tag ' + (mTrend === 'UP' ? 'up' : (mTrend === 'DOWN' ? 'down' : 'neutral'));
    const dirWord = mTrend === 'UP' ? 'BUY ▲' : (mTrend === 'DOWN' ? 'SELL ▼' : '');
    const flippedWord = (reverseOn && dirWord) ? (mTrend === 'UP' ? 'SELL ▼' : 'BUY ▲') : dirWord;
    const tagLabel = trendMode
      ? (mTrend === 'UP' ? (LANG === 'bn' ? 'বায়ার প্রেশার' : 'BUYER PRESSURE')
        : mTrend === 'DOWN' ? (LANG === 'bn' ? 'সেলার প্রেশার' : 'SELLER PRESSURE')
        : (LANG === 'bn' ? 'নিরপেক্ষ - ট্রেড নেবে না' : 'NEUTRAL - NO TRADE'))
      : (mTrend === 'UP' ? (LANG === 'bn' ? 'ক্যান্ডেল উপরে (ওয়াচ OFF)' : 'CANDLE UP (WATCH OFF)')
        : mTrend === 'DOWN' ? (LANG === 'bn' ? 'ক্যান্ডেল নিচে (ওয়াচ OFF)' : 'CANDLE DOWN (WATCH OFF)')
        : (LANG === 'bn' ? 'ক্যান্ডেল ফ্ল্যাট - ট্রেড নেবে না' : 'CANDLE FLAT - NO TRADE'));
    trendVal.textContent = flippedWord
      ? `${tagLabel} - ${flippedWord}` + ((reverseOn && dirWord) ? (LANG === 'bn' ? ' ⇄ রিভার্স' : ' ⇄ REVERSE') : '')
      : tagLabel;
  }
  const detailsEl = $('bot2-trend-details');
  if (detailsEl) {
    detailsEl.innerHTML = `${pressureTxt ? `<b>${esc(pressureTxt)}</b> · ` : ''}<b>Score: ${scoreSign}</b> · ${esc(trendDetails)}`;
  }
  const footerEl = $('bot2-trend-footer');
  if (footerEl) {
    footerEl.textContent = trendMode
      ? (LANG === 'bn'
        ? `⏱️ প্রতি ২.৫ সেকেন্ডে ওয়াচ ডায়াল দেখা হচ্ছে — যে দিকের চাপ ${watchMin}% বা তার বেশি হলেই সেই দিকে ট্রেড`
        : `⏱️ Reading the watch dials every 2.5s — trades only at/above ${watchMin}% pressure`)
      : (LANG === 'bn'
        ? '⏱️ ওয়াচ OFF — প্রতি ২.৫ সেকেন্ডে ক্যান্ডেল যেদিকে যাচ্ছে সেদিকেই ট্রেড নেওয়া হচ্ছে'
        : '⏱️ WATCH OFF — following the candle direction, checked every 2.5s');
  }

  // Update live ticking timer elements
  updateBot2TimerElements(getBot2ActiveSeconds());

  // ---- symbol
  const wanted = b.symbol_requested || cfg.symbol || 'BTCUSDTm';
  const sym = b.symbol || '';
  const symOk = !!b.symbol_ok && sym;
  const shown = symOk ? sym : wanted;
  $('bot2-symbol').textContent = shown;

  let priceStr = '';
  if (b.ask && b.bid) {
    const askStr = fmtPrice(b.ask);
    const bidStr = fmtPrice(b.bid);
    const spStr = b.spread !== undefined ? esc(String(b.spread)) : '';
    priceStr = ` · Ask <b>${askStr}</b> / Bid <b>${bidStr}</b> <span style="font-size:11px;color:var(--txt-faint)">(Spread ${spStr})</span>`;
  }
  $('bot2-symbol-name').innerHTML = `${esc(shown)}${priceStr}`;
  $('bot2-symbol-name').style.color = symOk ? 'var(--accent)' : '#fca5a5';

  // ---- market chips
  const pick = $('bot2-market-chips');
  const options = (b.symbol_options && b.symbol_options.length ? b.symbol_options : []);
  const list = options.length ? options.slice() : [shown];
  if (!list.includes(shown)) list.unshift(shown);
  if (live) {
    const sig = list.join(',') + '|' + shown;
    if (pick.dataset.n !== sig) {
      pick.dataset.n = sig;
      pick.innerHTML = list.map((s) =>
        `<button type="button" class="chip${s === shown ? ' active' : ''}" data-sym="${esc(s)}">${esc(s)}</button>`).join('');
    }
    pick.classList.remove('hidden');
  } else {
    pick.classList.remove('hidden');
    pick.querySelectorAll('.chip').forEach(c => {
      c.classList.toggle('active', c.dataset.sym === shown);
    });
  }
  setBotCurveSymbol('bot2', shown);

  // Inputs: lot, max_trades, tp, sl
  const lotEl = $('bot2-lot');
  if (document.activeElement !== lotEl && cfg.lot !== undefined && Number(lotEl.value) !== Number(cfg.lot)) {
    lotEl.value = cfg.lot;
  }
  if (b.volume_min) { lotEl.min = b.volume_min; lotEl.step = b.volume_step || 0.01; }

  const maxTradesEl = $('bot2-max-trades');
  if (document.activeElement !== maxTradesEl && cfg.max_trades !== undefined && Number(maxTradesEl.value) !== Number(cfg.max_trades)) {
    maxTradesEl.value = cfg.max_trades;
  }

  const tpEl = $('bot2-tp');
  if (document.activeElement !== tpEl && cfg.tp !== undefined && Number(tpEl.value) !== Number(cfg.tp)) {
    tpEl.value = Number(cfg.tp).toFixed(2);
  }

  const slEl = $('bot2-sl');
  if (document.activeElement !== slEl && cfg.sl !== undefined && Number(slEl.value) !== Number(cfg.sl)) {
    slEl.value = Number(cfg.sl).toFixed(2);
  }

  // Candle time-window block fields (first / last / middle seconds)
  const blockStartEl = $('bot2-block-start');
  if (blockStartEl && document.activeElement !== blockStartEl && cfg.block_start_sec !== undefined && Number(blockStartEl.value) !== Number(cfg.block_start_sec)) {
    blockStartEl.value = cfg.block_start_sec;
  }
  const blockEndEl = $('bot2-block-end');
  if (blockEndEl && document.activeElement !== blockEndEl && cfg.block_end_sec !== undefined && Number(blockEndEl.value) !== Number(cfg.block_end_sec)) {
    blockEndEl.value = cfg.block_end_sec;
  }
  const blockMidFromEl = $('bot2-block-mid-from');
  if (blockMidFromEl && document.activeElement !== blockMidFromEl && cfg.block_mid_from !== undefined && Number(blockMidFromEl.value) !== Number(cfg.block_mid_from)) {
    blockMidFromEl.value = cfg.block_mid_from;
  }
  const blockMidToEl = $('bot2-block-mid-to');
  if (blockMidToEl && document.activeElement !== blockMidToEl && cfg.block_mid_to !== undefined && Number(blockMidToEl.value) !== Number(cfg.block_mid_to)) {
    blockMidToEl.value = cfg.block_mid_to;
  }

  // Watch % field: the exact value the bot checks (nothing is fixed at 60%)
  const watchMinEl = $('bot2-watch-min');
  if (watchMinEl && document.activeElement !== watchMinEl && Number(watchMinEl.value) !== watchMin) {
    watchMinEl.value = watchMin;
  }

  // Structure Levels
  const struct = b.structure || {};
  const tf = (cfg.timeframe || 'D1').toUpperCase();
  document.querySelectorAll('#bot2-tf-toggle .chip').forEach(btn => {
    btn.classList.toggle('active', (btn.dataset.tf || '').toUpperCase() === tf);
  });

  const levels = (tf === 'W1' ? struct.w1 : struct.d1) || {};
  $('bot2-hh').textContent = levels.hh ? fmtPrice(levels.hh) : '—';
  $('bot2-lh').textContent = levels.lh ? fmtPrice(levels.lh) : '—';
  $('bot2-hl').textContent = levels.hl ? fmtPrice(levels.hl) : '—';
  $('bot2-ll').textContent = levels.ll ? fmtPrice(levels.ll) : '—';

  // Highlight if price is touching structure levels
  const curMid = (Number(b.ask || 0) + Number(b.bid || 0)) / 2;
  const tol = Math.max(Number(b.spread || 0) * 3, curMid * 0.0005);
  const cards = document.querySelectorAll('#bot2-struct-grid .struct-card');
  if (cards.length === 4 && curMid > 0) {
    cards[0].classList.toggle('active-touch', !!levels.hh && Math.abs(curMid - levels.hh) <= tol);
    cards[1].classList.toggle('active-touch', !!levels.lh && Math.abs(curMid - levels.lh) <= tol);
    cards[2].classList.toggle('active-touch', !!levels.hl && Math.abs(curMid - levels.hl) <= tol);
    cards[3].classList.toggle('active-touch', !!levels.ll && Math.abs(curMid - levels.ll) <= tol);
  }

  const lot = Number(lotEl.value) || Number(cfg.lot || 0.01);
  const maxTrades = Number(maxTradesEl.value) || Number(cfg.max_trades || 1);
  const tpVal = Number(tpEl.value) || Number(cfg.tp || 0.10);
  const slVal = Number(slEl.value) || Number(cfg.sl || 2.00);

  // Conditions list (in Bengali & English)
  const isBn = LANG === 'bn';
  const conds = isBn ? [
    `<b>১. লট সাইজ:</b> <b>${lot}</b> · <b>২. রানিং ট্রেড সীমা:</b> একসাথে সর্বোচ্চ <b>${maxTrades}</b> টি ট্রেড রানিং থাকবে, এর বেশি হবে না।`,
    `<b>৩. মার্কেট ও লাইভ প্রাইস:</b> বোতাম চেপে মার্কেট নির্বাচন এবং লাইভ আস্ক/বিড প্রাইস ট্র্যাক হয়।`,
    trendMode
      ? `<b>৪. ওয়াচ ON — ২.৫ সেকেন্ড ট্রেডিং:</b> প্রতি ২.৫ সেকেন্ডে ওয়াচ ডায়াল দেখা হয়; যে দিকের চাপ <b>${watchMin}%</b> বা তার বেশি, সেই দিকেই ট্রেড নেয় (বর্তমান: <b>${mTrend}</b> -> <b>${mTrend === 'UP' ? 'BUY' : 'SELL'}</b>), সর্বোচ্চ <b>${maxTrades}</b> টি একসাথে (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>)।`
      : `<b>৪. ওয়াচ OFF — ক্যান্ডেল ফলো:</b> প্রতি ২.৫ সেকেন্ডে ক্যান্ডেল যেদিকে যাচ্ছে সেদিকেই ট্রেড নেয় — উপরে উঠলে <b>BUY</b>, নিচে নামলে <b>SELL</b> (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>)।`,
    `<b>৫. মার্কেট স্ট্রাকচার লেভেল (${tf === 'W1' ? 'Weekend/Week' : 'Day'}):</b>
      <br>&nbsp;&nbsp;• <b>Lower Low (LL)</b> প্রাইসে আসলে <b>BUY</b> করবে${trendMode && mTrend !== 'UP' ? ' (ট্রেন্ড DOWN থাকায় অফ)' : ' (৪নং ক্যান্ডেল রুল তখন অফ থাকবে)'}
      <br>&nbsp;&nbsp;• <b>Higher High (HH)</b> প্রাইসে আসলে <b>SELL</b> করবে${trendMode && mTrend !== 'DOWN' ? ' (ট্রেন্ড UP থাকায় অফ)' : ' (৪নং রুল অফ থাকবে)'}
      <br>&nbsp;&nbsp;• <b>Lower High (LH)</b> প্রাইসে আসলে <b>SELL</b> করবে${trendMode && mTrend !== 'DOWN' ? ' (ট্রেন্ড UP থাকায় অফ)' : ' (৪নং রুল অফ থাকবে)'}
      <br>&nbsp;&nbsp;• <b>Higher Low (HL)</b> প্রাইসে আসলে <b>BUY</b> করবে${trendMode && mTrend !== 'UP' ? ' (ট্রেন্ড DOWN থাকায় অফ)' : ' (৪নং রুল অফ থাকবে)'}`,
    `<b>৬. ২.৫ সে. ব্যবধানে ১টি করে এন্ট্রি:</b> একসাথে সবগুলো না নিয়ে প্রতি ২.৫ সেকেন্ডে ১টি করে ট্রেন্ড অনুযায়ী ট্রেড নিবে যতক্ষণ না সর্বোচ্চ সীমা (<b>${maxTrades}</b> টি) পূর্ণ হয়।`,
    trendMode
      ? `<b>৭. ওয়াচ প্রেশার ফিল্টার (ওয়াচ ON, <b>${watchMin}%</b>):</b> ট্রেড নেওয়ার আগে ওয়াচের চাপ চেক হবে — যে দিকের চাপ <b>${watchMin}%</b> বা তার বেশি হলেই সেই দিকে ট্রেড, কম হলে কোনো ট্রেড নয়। এই % ফিল্ডে আপনি যা দেবেন, ঠিক সেটাই ব্যবহার হবে (৬০% ফিক্সড নয়)।`
      : `<b>৭. ওয়াচ প্রেশার ফিল্টার (ওয়াচ OFF):</b> ডায়াল একেবারেই দেখা হয় না — শুধু ক্যান্ডেলের দিক দেখে ট্রেড হয়।`,
    `<b>৮. রিভার্স (${reverseOn ? 'ON' : 'OFF'}):</b> ${reverseOn ? 'বট যে দিকেই ট্রেড নেওয়ার সিদ্ধান্ত নেবে, বাস্তবে তার ঠিক উল্টো দিকে অর্ডার যাবে (BUY ⇄ SELL)।' : 'বন্ধ — বট যে দিকে সিদ্ধান্ত নেয়, অর্ডারও ঠিক সেই দিকেই যায়।'}`,
  ] : [
    `<b>1. Lot Size:</b> <b>${lot}</b> · <b>2. Max Running:</b> Maximum <b>${maxTrades}</b> concurrent trade(s) running at once.`,
    `<b>3. Market Selection:</b> Choose instruments with real-time Ask / Bid / Spread tracking.`,
    trendMode
      ? `<b>4. WATCH ON — 2.5s Dial Trading:</b> reads the watch dials every 2.5s and opens 1 trade in the direction whose pressure is at/above <b>${watchMin}%</b> (current: <b>${mTrend}</b> -> <b>${mTrend === 'UP' ? 'BUY' : 'SELL'}</b>), up to <b>${maxTrades}</b> simultaneous trades (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>).`
      : `<b>4. WATCH OFF — Candle Follow:</b> trades the side the candle is actually moving to, checked every 2.5s — candle UP -> <b>BUY</b>, candle DOWN -> <b>SELL</b> (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>).`,
    `<b>5. Structure Levels (${tf === 'W1' ? 'Weekend/Week' : 'Day'}):</b>
      <br>&nbsp;&nbsp;• At <b>Lower Low (LL)</b> -> Take <b>BUY</b>${trendMode && mTrend !== 'UP' ? ' (Blocked: Trend is DOWN)' : ' (Step 4 paused)'}
      <br>&nbsp;&nbsp;• At <b>Higher High (HH)</b> -> Take <b>SELL</b>${trendMode && mTrend !== 'DOWN' ? ' (Blocked: Trend is UP)' : ' (Step 4 paused)'}
      <br>&nbsp;&nbsp;• At <b>Lower High (LH)</b> -> Take <b>SELL</b>${trendMode && mTrend !== 'DOWN' ? ' (Blocked: Trend is UP)' : ' (Step 4 paused)'}
      <br>&nbsp;&nbsp;• At <b>Higher Low (HL)</b> -> Take <b>BUY</b>${trendMode && mTrend !== 'UP' ? ' (Blocked: Trend is DOWN)' : ' (Step 4 paused)'}`,
    `<b>6. 2.5-Second Interval Execution:</b> Opens 1 trade every 2.5s sequentially (never all at once) until active count reaches <b>${maxTrades}</b>.`,
    trendMode
      ? `<b>7. Watch Pressure Filter (WATCH ON, <b>${watchMin}%</b>):</b> before entering it checks the dials — only the side whose pressure is at/above <b>${watchMin}%</b> is traded, anything below is strictly blocked. The % is exactly what you typed in the field (60% is not fixed).`
      : `<b>7. Watch Pressure Filter (WATCH OFF):</b> the dials are not read at all — the trade follows the candle direction only.`,
    `<b>8. Reverse (${reverseOn ? 'ON' : 'OFF'}):</b> ${reverseOn ? 'whatever direction the bot decides, the order that reaches the broker is the exact opposite (BUY ⇄ SELL).' : 'off — the bot trades exactly the direction it decides.'}`,
  ];
  $('bot2-cond').innerHTML = conds.map((c) => `<li>${c}</li>`).join('');

  const pos = b.positions || [];
  const runningCount = b.running_count !== undefined ? b.running_count : pos.length;
  $('bot2-running-line').innerHTML = `<b>${isBn ? 'রানিং ট্রেড' : 'Running Trades'}:</b> <b>${runningCount} / ${maxTrades}</b> (Slot: ${maxTrades - runningCount > 0 ? (maxTrades - runningCount) + ' available' : 'FULL'})`;

  const trendLine = $('bot2-trend-line');
  if (trendLine) {
    const rawSec = getBot2ActiveSeconds();
    const cleanSec = Math.max(0, Math.ceil(Number(rawSec) * 2) / 2);
    const secFormatted = (cleanSec % 1 === 0 ? cleanSec.toFixed(0) : cleanSec.toFixed(1)) + 's';
    const revTxt = reverseOn ? (isBn ? ' · ⇄ রিভার্স চালু (সব ট্রেড উল্টো)' : ' · ⇄ REVERSE ON (every trade flipped)') : '';
    if (trendMode) {
      trendLine.innerHTML = `<b>${isBn ? 'ওয়াচ মোড' : 'Watch Mode'}:</b> <span class="trend-active-text">ON [${mTrend} ${mTrend === 'UP' ? '▲' : '▼'}]</span> — ${isBn ? `প্রতি ২.৫ সে. পর পর ১টি করে ${mTrend === 'UP' ? 'BUY' : 'SELL'} ট্রেড ওপেন হচ্ছে (ওয়াচ %: ${watchMin} · পরবর্তী চেক: ${secFormatted})` : `Opening 1 new trade every 2.5s in the dial direction ${mTrend} (Watch %: ${watchMin} · Next: ${secFormatted})`}${revTxt}`;
    } else {
      trendLine.innerHTML = `<b>${isBn ? 'ওয়াচ মোড' : 'Watch Mode'}:</b> <span class="trend-deactive-text">OFF</span> — ${isBn ? `ক্যান্ডেল ফলো ট্রেডিং: ক্যান্ডেল যেদিকে যাচ্ছে সেদিকেই ট্রেড (২.৫ সে. দিক: ${mTrend} ${mTrend === 'UP' ? '▲' : '▼'} · Next: ${secFormatted})` : `Candle-follow trading: trading the side the candle is moving to (2.5s direction: ${mTrend} · Next: ${secFormatted})`}${revTxt}`;
    }
  }

  $('bot2-pos').innerHTML = pos.length
    ? `<b>${esc(t('botTrade'))}:</b> ` + pos.map((p) => {
        const net = Number(p.net || 0);
        return `${esc(String(p.side || '').toUpperCase())} ${nf(p.volume, 2)} @ ${fmtPrice(p.price_open)} · ` +
          `P/L <span class="${net >= 0 ? 'up' : 'down'}">${signed(net)}</span>`;
      }).join(' · ')
    : `<b>${esc(t('botTrade'))}:</b> ${esc(t('botNoTrade'))}`;

  const stats = b.stats || {};
  const last = b.last_error
    ? `<span style="color:#fca5a5">${esc(b.last_error)}</span>`
    : esc(b.last_action || '—');
  $('bot2-last').innerHTML = `${esc(t('botMarketNow'))}: <b>${esc(shown)}</b> · ` +
    `${stats.opened || 0} ${esc(t('botOpened'))} / ${stats.closed || 0} ${esc(t('botClosed'))} · ${last}`;
  $('bot2-last').className = 'bot-line' + (b.last_error ? ' warn' : '');
  renderPriceMap('bot2', b);
}

async function bot2Post(path, body) {
  try {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) {
      const msg = (j.error && j.error.message) || 'Request failed';
      toast('error', msg);
      return j;
    }
    if (j.bot2) { if (S) { S.bot2 = j.bot2; renderBot2(S); } }
    return j;
  } catch (e) { toast('error', String(e)); return {}; }
}

function bindBot2UI() {
  $('bot2-start').addEventListener('click', async () => {
    const sym = (S && S.bot2 && S.bot2.symbol) || 'BTCUSDTm';
    if (!isMarketOpen(sym)) {
      toast('error', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ থাকায় বট চালু করা যাবে না` : `Cannot start bot: ${sym} market is currently closed`);
      return;
    }
    const lot = Number($('bot2-lot').value || 0.01);
    const maxTrades = Number($('bot2-max-trades').value || 1);
    const tp = Number($('bot2-tp').value || 0.10);
    const sl = Number($('bot2-sl').value || 2.00);
    const b2cfg = (S && S.bot2 && S.bot2.config) || {};
    const b2state = (S && S.bot2) || {};
    const wOn = !!(b2state.watch_enabled !== undefined ? b2state.watch_enabled
      : (b2state.trend_mode !== undefined ? b2state.trend_mode : b2cfg.watch_enabled));
    const rOn = !!b2state.reverse;
    const wPct = Number(b2state.watch_min_pct !== undefined ? b2state.watch_min_pct : (b2cfg.watch_min_pct || ($('bot2-watch-min') ? $('bot2-watch-min').value : 60)));
    const ok = window.confirm(
      LANG === 'bn'
        ? `ক্যান্ডেল ও স্ট্রাকচার বট চালু করবেন?\n\nলট: ${lot}\nসর্বোচ্চ রানিং ট্রেড: ${maxTrades}\nTP: $${tp} | SL: $${sl}\nওয়াচ: ${wOn ? `ON (${wPct}%)` : 'OFF — ক্যান্ডেল ফলো'}\nরিভার্স: ${rOn ? 'ON (সব ট্রেড উল্টো)' : 'OFF'}`
        : `Start Candle & Structure Bot?\n\nLot: ${lot}\nMax Trades: ${maxTrades}\nTP: $${tp} | SL: $${sl}\nWatch: ${wOn ? `ON (${wPct}%)` : 'OFF — candle follow'}\nReverse: ${rOn ? 'ON (every trade flipped)' : 'OFF'}`
    );
    if (!ok) return;
    const res = await bot2Post('/api/bot2/start', { lot, max_trades: maxTrades, tp, sl });
    if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ২ চালু হয়েছে' : 'Bot 2 Started');
  });

  $('bot2-stop').addEventListener('click', async () => {
    const res = await bot2Post('/api/bot2/stop', {});
    if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ২ বন্ধ করা হলো' : 'Bot 2 Stopped');
  });

  $('bot2-trend').addEventListener('click', async () => {
    const cur = S && S.bot2
      ? !!(S.bot2.watch_enabled !== undefined ? S.bot2.watch_enabled
        : (S.bot2.trend_mode !== undefined ? S.bot2.trend_mode : (S.bot2.config || {}).watch_enabled))
      : false;
    const nextState = !cur;
    $('bot2-trend').classList.toggle('active', nextState);
    const trendStatus = $('bot2-trend-status');
    if (trendStatus) {
      trendStatus.textContent = nextState ? 'ON' : 'OFF';
      trendStatus.className = 'trend-badge ' + (nextState ? 'active' : 'deactive');
    }
    const res = await bot2Post('/api/bot2/config', { watch_enabled: nextState });
    if (res && res.ok) {
      if (res.bot2) {
        if (S) S.bot2 = res.bot2;
        renderBot2(S);
      }
      toast('ok', nextState
        ? (LANG === 'bn' ? 'ওয়াচ ON — আপনার দেওয়া % অনুযায়ী ডায়ালের চাপ চেক হবে' : 'Watch ON — the dial pressure decides the direction')
        : (LANG === 'bn' ? 'ওয়াচ OFF — এখন ক্যান্ডেলের দিকেই ট্রেড নেবে' : 'Watch OFF — following the candle flow'));
    }
  });

  $('bot2-reverse').addEventListener('click', async () => {
    const cur = S && S.bot2 ? !!S.bot2.reverse : false;
    const nextState = !cur;
    $('bot2-reverse').classList.toggle('active', nextState);
    const revStatus = $('bot2-reverse-status');
    if (revStatus) {
      revStatus.textContent = nextState ? 'ON' : 'OFF';
      revStatus.className = 'trend-badge ' + (nextState ? 'active' : 'deactive');
    }
    const res = await bot2Post('/api/bot2/config', { reverse: nextState });
    if (res && res.ok) {
      if (res.bot2) {
        if (S) S.bot2 = res.bot2;
        renderBot2(S);
      }
      toast('ok', nextState
        ? (LANG === 'bn' ? 'রিভার্স ON — এখন প্রতিটি ট্রেড উল্টো দিকে যাবে' : 'Reverse ON — every trade is flipped')
        : (LANG === 'bn' ? 'রিভার্স OFF' : 'Reverse OFF'));
    }
  });

  $('bot2-close').addEventListener('click', async () => {
    if (!window.confirm(LANG === 'bn' ? 'বট ২ এর সব খোলা ট্রেড এখনই ক্লোজ করবেন?' : 'Close all Bot 2 open positions right now?')) return;
    const res = await bot2Post('/api/bot2/close', {});
    if (res && res.ok) toast('ok', `${LANG === 'bn' ? 'ক্লোজ করা ট্রেড' : 'Closed positions'} (${res.closed || 0})`);
  });

  const triggerTrendCheck = async () => {
    // the trend system runs every `trend_check_interval` seconds (2.5s by
    // default) - the old "60s" labels described a timer that no longer exists
    const iv = (S && S.bot2 && S.bot2.config && Number(S.bot2.config.trend_check_interval)) || 2.5;
    toast('info', LANG === 'bn' ? 'ট্রেন্ড চেক হচ্ছে…' : 'Checking trend…');
    const refreshBtn = $('bot2-trend-refresh');
    if (refreshBtn) refreshBtn.style.transform = 'rotate(360deg)';
    updateBot2TimerElements(iv);
    const res = await bot2Post('/api/bot2/check-trend', {});
    setTimeout(() => { if (refreshBtn) refreshBtn.style.transform = ''; }, 300);
    if (res && res.ok) {
      if (res.bot2 && S) {
        S.bot2 = res.bot2;
      }
      const curTrend = res.trend || (res.bot2 && res.bot2.market_trend) || 'UP';
      toast('ok', LANG === 'bn' ? `ট্রেন্ড চেক সম্পন্ন: ${curTrend} (${curTrend === 'UP' ? 'বুলিশ ▲' : 'বেয়ারিশ ▼'})` : `Trend check complete: ${curTrend}`);
      updateBot2TimerElements(iv);
    }
  };

  const checkTrendBtn = $('bot2-check-trend-btn');
  if (checkTrendBtn) checkTrendBtn.addEventListener('click', triggerTrendCheck);

  const refreshTrendIcon = $('bot2-trend-refresh');
  if (refreshTrendIcon) refreshTrendIcon.addEventListener('click', triggerTrendCheck);

  const trendPillEl = $('bot2-trend-pill');
  if (trendPillEl) {
    trendPillEl.style.cursor = 'pointer';
    trendPillEl.addEventListener('click', triggerTrendCheck);
  }

  $('bot2-lot').addEventListener('change', () => {
    void bot2Post('/api/bot2/config', { lot: Number($('bot2-lot').value || 0.01) });
  });
  $('bot2-max-trades').addEventListener('change', () => {
    void bot2Post('/api/bot2/config', { max_trades: Math.max(1, parseInt($('bot2-max-trades').value || 1, 10)) });
  });
  $('bot2-tp').addEventListener('change', () => {
    void bot2Post('/api/bot2/config', { tp: Number($('bot2-tp').value || 0.10) });
  });
  $('bot2-sl').addEventListener('change', () => {
    void bot2Post('/api/bot2/config', { sl: Number($('bot2-sl').value || 2.00) });
  });
  $('bot2-block-start').addEventListener('change', () => {
    void bot2Post('/api/bot2/config', { block_start_sec: Math.max(0, Number($('bot2-block-start').value || 0)) });
  });
  $('bot2-block-end').addEventListener('change', () => {
    void bot2Post('/api/bot2/config', { block_end_sec: Math.max(0, Number($('bot2-block-end').value || 0)) });
  });
  $('bot2-block-mid-from').addEventListener('change', () => {
    void bot2Post('/api/bot2/config', { block_mid_from: Math.max(0, Number($('bot2-block-mid-from').value || 0)) });
  });
  $('bot2-block-mid-to').addEventListener('change', () => {
    void bot2Post('/api/bot2/config', { block_mid_to: Math.max(0, Number($('bot2-block-mid-to').value || 0)) });
  });
  $('bot2-watch-min').addEventListener('change', () => {
    const v = Math.min(99, Math.max(1, Number($('bot2-watch-min').value || 60)));
    void bot2Post('/api/bot2/config', { watch_min_pct: v });
  });

  $('bot2-market-chips').addEventListener('click', async (e) => {
    const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
    if (!chip || !chip.dataset.sym) return;
    const sym = chip.dataset.sym;
    if (chip.classList.contains('market-off') || !isMarketOpen(sym)) {
      toast('warn', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)` : `${sym} market is currently closed`);
      return;
    }

    document.querySelectorAll('#bot2-market-chips .chip')
      .forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
    $('bot2-symbol').textContent = sym;
    $('bot2-symbol-name').textContent = sym;
    $('bot2-market-chips').dataset.n = '';
    setBotCurveSymbol('bot2', sym);

    const res = await bot2Post('/api/bot2/config', { symbol: sym });
    if (res && res.ok) {
      if (res.bot2) {
        if (S) S.bot2 = res.bot2;
        renderBot2(S);
      }
      toast('ok', `${t('botMarketSwitched')}: ${sym}`);
      if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
    }
  });

  $('bot2-tf-toggle').addEventListener('click', async (e) => {
    const btn = e.target && e.target.closest ? e.target.closest('button') : null;
    if (!btn || !btn.dataset.tf) return;
    const tf = btn.dataset.tf;
    document.querySelectorAll('#bot2-tf-toggle .chip').forEach(b => b.classList.toggle('active', b === btn));
    const res = await bot2Post('/api/bot2/config', { timeframe: tf });
    if (res && res.ok && res.bot2) {
      if (S) S.bot2 = res.bot2;
      renderBot2(S);
    }
  });
}

/* ------------------------------------------------------------ auto trading bot 3 (extreme high & low) */
function renderBot3(d) {
  const b = d.bot3 || {};
  const cfg = b.config || {};
  const running = !!b.running;
  const live = !!d.connected;
  const wanted = b.symbol_requested || cfg.symbol || 'XAUUSDm';
  const sym = b.symbol || '';
  const symOk = !!b.symbol_ok && sym;
  const shown = symOk ? sym : wanted;
  const b3Panel = $('bot3-panel');
  if (b3Panel) {
    const chip = $('bot3-chip');
    if (chip) {
      chip.className = 'bot-chip ' + (!live ? 'offline' : running ? 'running' : 'stopped');
      chip.textContent = !live ? t('botOffline') : running ? t('botRunning') : t('botStopped');
    }

    if ($('bot3-start')) $('bot3-start').classList.toggle('active', running);
    if ($('bot3-stop')) $('bot3-stop').classList.toggle('active', !running && live);

    if ($('bot3-symbol')) $('bot3-symbol').textContent = shown;

    let priceStr = '';
    if (b.ask && b.bid) {
      const askStr = fmtPrice(b.ask);
      const bidStr = fmtPrice(b.bid);
      const spStr = b.spread !== undefined ? esc(String(b.spread)) : '';
      priceStr = ` · Ask <b>${askStr}</b> / Bid <b>${bidStr}</b> <span style="font-size:11px;color:var(--txt-faint)">(Spread ${spStr})</span>`;
    }
    if ($('bot3-symbol-name')) {
      $('bot3-symbol-name').innerHTML = `${esc(shown)}${priceStr}`;
      $('bot3-symbol-name').style.color = symOk ? 'var(--accent)' : '#fca5a5';
    }

    // ---- market chips
    const pick = $('bot3-market-chips');
    if (pick) {
      const options = (b.symbol_options && b.symbol_options.length ? b.symbol_options : []);
      const list = options.length ? options.slice() : [shown];
      if (!list.includes(shown)) list.unshift(shown);
      if (live) {
        const sig = list.join(',') + '|' + shown;
        if (pick.dataset.n !== sig) {
          pick.dataset.n = sig;
          pick.innerHTML = list.map((s) =>
            `<button type="button" class="chip${s === shown ? ' active' : ''}" data-sym="${esc(s)}">${esc(s)}</button>`).join('');
        }
        pick.classList.remove('hidden');
      } else {
        pick.classList.remove('hidden');
        pick.querySelectorAll('.chip').forEach(c => {
          c.classList.toggle('active', c.dataset.sym === shown);
        });
      }
    }
    setBotCurveSymbol('bot3', shown);

    // inputs update if not focused
    const lotEl = $('bot3-lot');
    if (lotEl && document.activeElement !== lotEl && cfg.lot !== undefined) lotEl.value = cfg.lot;
    const maxTradesEl = $('bot3-max-trades');
    if (maxTradesEl && document.activeElement !== maxTradesEl && cfg.max_trades !== undefined) maxTradesEl.value = cfg.max_trades;
    const tpEl = $('bot3-tp');
    if (tpEl && document.activeElement !== tpEl && cfg.tp !== undefined) tpEl.value = Number(cfg.tp).toFixed(2);
    const slEl = $('bot3-sl');
    if (slEl && document.activeElement !== slEl && cfg.sl !== undefined) slEl.value = Number(cfg.sl).toFixed(2);

    // Timeframe Toggle (D1, W1, PREV)
    const levelsData = b.levels || {};
    const tf = (cfg.timeframe || 'D1').toUpperCase();
    document.querySelectorAll('#bot3-tf-toggle .chip').forEach(btn => {
      btn.classList.toggle('active', (btn.dataset.tf || '').toUpperCase() === tf);
    });

    // Extract levels for selected timeframe
    let curLevels = {};
    let hhSub = 'Higher High';
    let lhSub = 'Lower High';
    let hlSub = 'Higher Low';
    let llSub = 'Lower Low';

    if (tf === 'W1') {
      curLevels = levelsData.w1 || {};
      hhSub = 'This Week High (HH)';
      lhSub = 'This Week LH';
      hlSub = 'This Week HL';
      llSub = 'This Week Low (LL)';
    } else if (tf === 'PREV') {
      curLevels = levelsData.prev || {};
      hhSub = 'Prev Week High (HH)';
      lhSub = 'Prev Week LH';
      hlSub = 'Prev Week HL';
      llSub = 'Prev Week Low (LL)';
    } else {
      curLevels = levelsData.d1 || {};
      hhSub = 'Today High (HH)';
      lhSub = 'Today LH';
      hlSub = 'Today HL';
      llSub = 'Today Low (LL)';
    }

    if ($('bot3-hh')) $('bot3-hh').textContent = curLevels.hh ? fmtPrice(curLevels.hh) : '—';
    if ($('bot3-lh')) $('bot3-lh').textContent = curLevels.lh ? fmtPrice(curLevels.lh) : '—';
    if ($('bot3-hl')) $('bot3-hl').textContent = curLevels.hl ? fmtPrice(curLevels.hl) : '—';
    if ($('bot3-ll')) $('bot3-ll').textContent = curLevels.ll ? fmtPrice(curLevels.ll) : '—';

    if ($('bot3-hh-sub')) $('bot3-hh-sub').textContent = hhSub;
    if ($('bot3-lh-sub')) $('bot3-lh-sub').textContent = lhSub;
    if ($('bot3-hl-sub')) $('bot3-hl-sub').textContent = hlSub;
    if ($('bot3-ll-sub')) $('bot3-ll-sub').textContent = llSub;

    // Active touch highlight
    const curMid = (Number(b.ask || 0) + Number(b.bid || 0)) / 2;
    const tol = Math.max(Number(b.spread || 0) * 3, curMid * 0.0006);
    const cards = document.querySelectorAll('#bot3-struct-grid .struct-card');
    if (cards.length === 4 && curMid > 0) {
      cards[0].classList.toggle('active-touch', !!curLevels.hh && Math.abs(curMid - curLevels.hh) <= tol);
      cards[1].classList.toggle('active-touch', !!curLevels.lh && Math.abs(curMid - curLevels.lh) <= tol);
      cards[2].classList.toggle('active-touch', !!curLevels.hl && Math.abs(curMid - curLevels.hl) <= tol);
      cards[3].classList.toggle('active-touch', !!curLevels.ll && Math.abs(curMid - curLevels.ll) <= tol);
    }
  }

  // ---- Radar Timer & Zone Pill
  const zonePill = $('bot3-zone-pill');
  if (zonePill) {
    const zs = b.zone_status || 'MIDDLE (WAITING)';
    zonePill.textContent = zs;
    zonePill.className = 'radar-zone-pill ' + (zs.includes('BUY') ? 'in-buy' : zs.includes('SELL') ? 'in-sell' : '');
  }

  // Sync 60s auto-recalculation timer with backend countdown
  if (b.recalc_countdown !== undefined && b.recalc_countdown !== null && !_bot3RecalcPending) {
    if (Math.abs(bot3RecalcRemaining - b.recalc_countdown) > 2) {
      bot3RecalcRemaining = Number(b.recalc_countdown);
    }
  }
  updateBot3TimerPills(bot3RecalcRemaining);
  if (b.last_recalc_ts) {
    updateBot3LastUpdated(b.last_recalc_ts);
  }

  // ---- Next 5 Planned Trades List
  const plannedEl = $('bot3-planned-trades');
  const planned = b.planned_trades || [];
  if (plannedEl) {
    if (!planned.length) {
      plannedEl.innerHTML = `
        <div class="pt-empty-state">
          <span class="pt-empty-icon">⏳</span>
          <span class="pt-empty-text">${LANG === 'bn' ? 'এক্সট্রিম সেটআপ হিসাব হচ্ছে... অপেক্ষা করুন' : 'Calculating extreme setups... Please wait'}</span>
        </div>
      `;
    } else {
      const isBn = LANG === 'bn';
      plannedEl.innerHTML = planned.map((p) => {
        const rank = p.priority_rank || p.id || 1;
        const rankCls = rank === 1 ? 'rank-1' : (rank === 2 ? 'rank-2' : '');
        const isBuy = (p.action || '').toUpperCase() === 'BUY';
        const touchCls = p.is_touch ? ' touching' : '';
        const statusCls = p.status_cls || (p.is_touch ? 'trigger' : 'pending');
        const statusTxt = isBn ? (p.status_text_bn || p.status_text || 'অপেক্ষমাণ') : (p.status_text || 'PENDING');

        const distStr = (p.distance !== undefined && p.distance !== null && p.distance !== '') ? `$${fmtPrice(p.distance)} away` : (isBn ? 'লেভেলে আছে' : 'at level');
        const distPctStr = (p.dist_pct !== undefined && p.dist_pct !== null) ? ` (${p.dist_pct}%)` : '';
        const distDisplay = distStr + distPctStr;
        const nearCls = (p.is_touch || (p.dist_pct !== undefined && p.dist_pct <= 0.35)) ? ' near' : '';

        const desc = isBn ? (p.reason_bn || p.reason) : p.reason;
        const tpValEst = p.est_tp_price ? fmtPrice(p.est_tp_price) : '--';
        const slValEst = p.est_sl_price ? fmtPrice(p.est_sl_price) : '--';
        const tpDollar = p.tp ? `$${p.tp}` : `$${nf(b.config ? b.config.tp || 0.5 : 0.5, 2)}`;
        const slDollar = p.sl ? `$${p.sl}` : `$${nf(b.config ? b.config.sl || 5.0 : 5.0, 2)}`;

        return `
          <div class="pt-card ${isBuy ? 'buy' : 'sell'}${touchCls}">
            <div class="pt-card-header">
              <div class="pt-card-tags">
                <span class="pt-rank-badge ${rankCls}">#${rank}</span>
                <span class="pt-badge">${esc(p.action)}</span>
                <span class="pt-scope-tag">${esc(p.scope || '')}</span>
              </div>
              <span class="pt-status-pill status-${statusCls}">${esc(statusTxt)}</span>
            </div>
            <div class="pt-card-body">
              <div class="pt-info">
                <span class="pt-name">${esc(p.label)}</span>
                <span class="pt-desc">${esc(desc)}</span>
              </div>
              <div class="pt-metrics">
                <div class="pt-metric-row">
                  <span class="pt-metric-lbl">${isBn ? 'লেভেল' : 'Level'}:</span>
                  <span class="pt-price">${fmtPrice(p.price)}</span>
                </div>
                <div class="pt-metric-row">
                  <span class="pt-metric-lbl">${isBn ? 'দূরত্ব' : 'Dist'}:</span>
                  <span class="pt-dist${nearCls}">${esc(distDisplay)}</span>
                </div>
              </div>
            </div>
            <div class="pt-card-targets">
              <span class="pt-target-tag tp">🎯 TP: ${tpValEst} (+${tpDollar})</span>
              <span class="pt-target-tag sl">🛡️ SL: ${slValEst} (-${slDollar})</span>
            </div>
          </div>
        `;
      }).join('');
    }
  }

  if (b3Panel) {
    const lotEl = $('bot3-lot');
    const maxTradesEl = $('bot3-max-trades');
    const tpEl = $('bot3-tp');
    const slEl = $('bot3-sl');
    const lot = Number(lotEl ? lotEl.value : (cfg.lot || 0.01));
    const maxTrades = Number(maxTradesEl ? maxTradesEl.value : (cfg.max_trades || 1));
    const tpVal = Number(tpEl ? tpEl.value : (cfg.tp || 0.50));
    const slVal = Number(slEl ? slEl.value : (cfg.sl || 5.00));

    // Conditions list
    const isBn = LANG === 'bn';
    const conds = isBn ? [
      `<b>১. চরম সীমা লেভেল চেক:</b> আজকের দিন (D1), চলতি সপ্তাহ ওপেনিং (W1) এবং গত সপ্তাহের (Prev W1) সব Higher High (HH) এবং Lower Low (LL) নিয়মিত স্ক্যান করে।`,
      `<b>২. এক্সট্রিম রেঞ্জ এন্ট্রি:</b> মার্কেট একদম <b>নিচে (LL)</b> নামলে <b>BUY</b> ট্রেড নিবে; মার্কেট একদম <b>উপরে (HH)</b> উঠলে <b>SELL</b> ট্রেড নিবে (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>)।`,
      `<b>৩. মিডল জোন ফিল্টার:</b> রেঞ্জের মাঝে কোনো ট্রেড নিবে না — শুধুই একদম উপর বা নিচ থেকে হাই-প্রোবাবিলিটি ট্রেড নিবে।`,
      `<b>৪. রানিং লিমিট:</b> লট <b>${lot}</b> · একসাথে সর্বোচ্চ <b>${maxTrades}</b> টি ট্রেড রানিং থাকবে।`,
    ] : [
      `<b>1. Extreme Structure Monitoring:</b> Continuously tracks Today (D1), This Week (W1), and Previous Week (Prev W1) Higher High (HH) and Lower Low (LL) extremes.`,
      `<b>2. Extreme Sniper Execution:</b> When market is at the absolute <b>bottom (LL)</b> -> Take <b>BUY</b>; when at the absolute <b>top (HH)</b> -> Take <b>SELL</b> (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>).`,
      `<b>3. Middle Range Filter:</b> Strictly NO TRADES in the middle zone — exclusively snipes extremes.`,
      `<b>4. Trade Limits:</b> Lot <b>${lot}</b> · Maximum <b>${maxTrades}</b> concurrent trade(s) running at once.`,
    ];
    const condEl = $('bot3-cond');
    if (condEl) condEl.innerHTML = conds.map((c) => `<li>${c}</li>`).join('');

    // Running line & pos
    const pos = b.positions || [];
    const runningCount = b.running_count !== undefined ? b.running_count : pos.length;
    const runEl = $('bot3-running-line');
    if (runEl) runEl.innerHTML = `<b>${isBn ? 'রানিং ট্রেড' : 'Running Trades'}:</b> <b>${runningCount} / ${maxTrades}</b> (Slot: ${maxTrades - runningCount > 0 ? (maxTrades - runningCount) + ' available' : 'FULL'})`;

    const posEl = $('bot3-pos');
    if (posEl) {
      posEl.innerHTML = pos.length
        ? pos.map((p) => {
            const side = (p.side || 'buy').toUpperCase();
            const sideColor = side === 'BUY' ? 'var(--win)' : 'var(--loss)';
            const net = Number(p.net || 0);
            const netStr = `${net >= 0 ? '+' : ''}$${net.toFixed(2)}`;
            const netColor = net >= 0 ? 'var(--win)' : 'var(--loss)';
            return `<span style="font-family:var(--mono)">#${p.ticket} <b style="color:${sideColor}">${side}</b> ${p.volume} @ ${fmtPrice(p.price_open)} &rarr; <b style="color:${netColor}">${netStr}</b></span>`;
          }).join(' · ')
        : (isBn ? 'নেই' : 'none');
    }

    const stats = b.stats || {};
    const opened = stats.opened || 0;
    const closed = stats.closed || 0;
    const realized = stats.realized !== undefined ? Number(stats.realized).toFixed(2) : '0.00';
    const actionText = b.last_action ? ` · <i>${esc(b.last_action)}</i>` : '';
    const lastEl = $('bot3-last');
    if (lastEl) {
      lastEl.innerHTML = `${esc(t('botMarketNow'))}: <b>${esc(shown)}</b> · ` +
        `${opened} ${t('botOpened')} / ${closed} ${t('botClosed')} (P/L: $${realized})${actionText}`;
      lastEl.className = 'bot-line' + (b.last_error ? ' warn' : '');
    }
    renderPriceMap('bot3', b);
  }
}

async function bot3Post(path, body) {
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await res.json();
    if (!res.ok) {
      const msg = (j.error && j.error.message) || j.detail || 'Request failed';
      toast('error', msg);
      return j;
    }
    if (j.bot3) { if (S) { S.bot3 = j.bot3; renderBot3(S); } }
    return j;
  } catch (e) { toast('error', String(e)); return {}; }
}

function bindBot3UI() {
  const b3Start = $('bot3-start');
  if (b3Start) {
    b3Start.addEventListener('click', async () => {
      const sym = (S && S.bot3 && S.bot3.symbol) || 'XAUUSDm';
      if (!isMarketOpen(sym)) {
        toast('error', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ থাকায় বট চালু করা যাবে না` : `Cannot start bot: ${sym} market is currently closed`);
        return;
      }
      const lot = Number($('bot3-lot')?.value || 0.01);
      const maxTrades = Number($('bot3-max-trades')?.value || 1);
      const tp = Number($('bot3-tp')?.value || 0.50);
      const sl = Number($('bot3-sl')?.value || 5.00);
      const ok = window.confirm(
        LANG === 'bn'
          ? `এক্সট্রিম স্ট্রাকচার বট চালু করবেন?\n\nলট: ${lot}\nসর্বোচ্চ রানিং ট্রেড: ${maxTrades}\nTP: $${tp} | SL: $${sl}`
          : `Start Extreme Structure Bot?\n\nLot: ${lot}\nMax Trades: ${maxTrades}\nTP: $${tp} | SL: $${sl}`
      );
      if (!ok) return;
      const res = await bot3Post('/api/bot3/start', { lot, max_trades: maxTrades, tp, sl });
      if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ৩ চালু হয়েছে' : 'Bot 3 Started');
    });
  }

  const b3Stop = $('bot3-stop');
  if (b3Stop) {
    b3Stop.addEventListener('click', async () => {
      const res = await bot3Post('/api/bot3/stop', {});
      if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ৩ বন্ধ করা হলো' : 'Bot 3 Stopped');
    });
  }

  const b3Close = $('bot3-close');
  if (b3Close) {
    b3Close.addEventListener('click', async () => {
      if (!window.confirm(LANG === 'bn' ? 'বট ৩ এর সব খোলা ট্রেড এখনই ক্লোজ করবেন?' : 'Close all Bot 3 open positions right now?')) return;
      const res = await bot3Post('/api/bot3/close', {});
      if (res && res.ok) toast('ok', `${LANG === 'bn' ? 'ক্লোজ করা ট্রেড' : 'Closed positions'} (${res.closed || 0})`);
    });
  }

  const b3Lot = $('bot3-lot');
  if (b3Lot) {
    b3Lot.addEventListener('change', () => {
      void bot3Post('/api/bot3/config', { lot: Number(b3Lot.value || 0.01) });
    });
  }
  const b3MaxTrades = $('bot3-max-trades');
  if (b3MaxTrades) {
    b3MaxTrades.addEventListener('change', () => {
      void bot3Post('/api/bot3/config', { max_trades: Math.max(1, parseInt(b3MaxTrades.value || 1, 10)) });
    });
  }
  const b3Tp = $('bot3-tp');
  if (b3Tp) {
    b3Tp.addEventListener('change', () => {
      void bot3Post('/api/bot3/config', { tp: Number(b3Tp.value || 0.50) });
    });
  }
  const b3Sl = $('bot3-sl');
  if (b3Sl) {
    b3Sl.addEventListener('change', () => {
      void bot3Post('/api/bot3/config', { sl: Number(b3Sl.value || 5.00) });
    });
  }

  const b3Chips = $('bot3-market-chips');
  if (b3Chips) {
    b3Chips.addEventListener('click', async (e) => {
      const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
      if (!chip || !chip.dataset.sym) return;
      const sym = chip.dataset.sym;
      if (chip.classList.contains('market-off') || !isMarketOpen(sym)) {
        toast('warn', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)` : `${sym} market is currently closed`);
        return;
      }

      document.querySelectorAll('#bot3-market-chips .chip')
        .forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
      if ($('bot3-symbol')) $('bot3-symbol').textContent = sym;
      if ($('bot3-symbol-name')) $('bot3-symbol-name').textContent = sym;
      b3Chips.dataset.n = '';
      setBotCurveSymbol('bot3', sym);

      const res = await bot3Post('/api/bot3/config', { symbol: sym });
      if (res && res.ok) {
        if (res.bot3) {
          if (S) S.bot3 = res.bot3;
          renderBot3(S);
        }
        toast('ok', `${t('botMarketSwitched')}: ${sym}`);
        if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
      }
    });
  }

  const b3Tf = $('bot3-tf-toggle');
  if (b3Tf) {
    b3Tf.addEventListener('click', async (e) => {
      const btn = e.target && e.target.closest ? e.target.closest('button') : null;
      if (!btn || !btn.dataset.tf) return;
      const tf = btn.dataset.tf;
      document.querySelectorAll('#bot3-tf-toggle .chip').forEach(b => b.classList.toggle('active', b === btn));
      const res = await bot3Post('/api/bot3/config', { timeframe: tf });
      if (res && res.ok && res.bot3) {
        if (S) S.bot3 = res.bot3;
        renderBot3(S);
      }
    });
  }

  const recalcBtn = $('bot3-recalc-btn');
  if (recalcBtn) {
    recalcBtn.addEventListener('click', () => {
      void triggerBot3Recalculate(true);
    });
  }
}

/* ------------------------------------------------------ bot 4 (pre-trade ladder) */
function renderBot4(d) {
  const b = d.bot4 || {};
  const cfg = b.config || {};
  const running = !!b.running;
  const live = !!d.connected;
  const chip = $('bot4-chip');
  if (chip) {
    chip.className = 'bot-chip ' + (!live ? 'offline' : running ? 'running' : 'stopped');
    chip.textContent = !live ? t('botOffline') : running ? t('botRunning') : t('botStopped');
  }

  $('bot4-start').classList.toggle('active', running);
  $('bot4-stop').classList.toggle('active', !running && live);

  // ---- symbol + live price
  const wanted = b.symbol_requested || cfg.symbol || 'XAUUSDm';
  const sym = b.symbol || '';
  const symOk = !!b.symbol_ok && sym;
  const shown = symOk ? sym : wanted;
  $('bot4-symbol').textContent = shown;

  let priceStr = '';
  if (b.ask && b.bid) {
    const askStr = fmtPrice(b.ask);
    const bidStr = fmtPrice(b.bid);
    const spStr = b.spread !== undefined ? esc(String(b.spread)) : '';
    priceStr = ` · Ask <b>${askStr}</b> / Bid <b>${bidStr}</b> <span style="font-size:11px;color:var(--txt-faint)">(Spread ${spStr})</span>`;
  }
  $('bot4-symbol-name').innerHTML = `${esc(shown)}${priceStr}`;
  $('bot4-symbol-name').style.color = symOk ? 'var(--accent)' : '#fca5a5';

  // ---- market chips
  const pick = $('bot4-market-chips');
  const options = (b.symbol_options && b.symbol_options.length ? b.symbol_options : []);
  const list = options.length ? options.slice() : [shown];
  if (!list.includes(shown)) list.unshift(shown);
  if (live) {
    const sig = list.join(',') + '|' + shown;
    if (pick.dataset.n !== sig) {
      pick.dataset.n = sig;
      pick.innerHTML = list.map((s) =>
        `<button type="button" class="chip${s === shown ? ' active' : ''}" data-sym="${esc(s)}">${esc(s)}</button>`).join('');
    }
    pick.classList.remove('hidden');
  } else {
    pick.classList.remove('hidden');
    pick.querySelectorAll('.chip').forEach(c => {
      c.classList.toggle('active', c.dataset.sym === shown);
    });
  }
  setBotCurveSymbol('bot4', shown);

  // ---- inputs update if not focused
  const lotEl = $('bot4-lot');
  if (document.activeElement !== lotEl && cfg.lot !== undefined) lotEl.value = cfg.lot;
  const gapEl = $('bot4-price-gap');
  if (document.activeElement !== gapEl && cfg.price_gap !== undefined) gapEl.value = cfg.price_gap;
  const pgtEl = $('bot4-pre-gap');
  if (document.activeElement !== pgtEl && cfg.pre_trade_gap !== undefined) pgtEl.value = cfg.pre_trade_gap;
  const pvEl = $('bot4-pre-value');
  if (document.activeElement !== pvEl && cfg.pre_trade_value !== undefined) pvEl.value = cfg.pre_trade_value;

  // ---- Numbering Lot Size toggle
  const numOn = !!cfg.numbering_lot;
  const numBtn = $('bot4-numbering');
  if (numBtn) numBtn.classList.toggle('active', numOn);
  const numSt = $('bot4-numbering-status');
  if (numSt) {
    numSt.textContent = numOn ? 'ON' : 'OFF';
    numSt.className = 'trend-badge ' + (numOn ? 'active' : 'deactive');
  }

  // ---- ladder board + pills
  renderBot4Ladder(b);
  renderPriceMap('bot4', b);

  // ---- status lines
  const n = (b.ladder && b.ladder.value) || cfg.pre_trade_value || 5;
  const pend = b.pending_count || 0;
  const open = b.running_count || 0;
  const rl = $('bot4-running-line');
  if (rl) {
    rl.innerHTML = `<b>${LANG === 'bn' ? 'প্রি-ট্রেড:' : 'Pre-trades:'}</b> ` +
      `${pend} ${LANG === 'bn' ? 'পেন্ডিং' : 'pending'} · ` +
      `${b.filled_count || 0} ${LANG === 'bn' ? 'হিট' : 'hit'} · ` +
      `${open} ${LANG === 'bn' ? 'ওপেন' : 'open'} / ${n * 2} ${LANG === 'bn' ? 'ল্যাডার' : 'ladder'}`;
  }
  const posEl = $('bot4-pos');
  if (posEl) {
    const ps = b.positions || [];
    const parts = ps.map((p) => {
      const side = String(p.side || '').toUpperCase();
      const lv = p.level ? `#${p.level}` : '';
      const lock = p.armed ? ' 🔒' : '';
      const net = Number(p.net || 0);
      return `${side} ${lv} ${p.volume || ''} (${net >= 0 ? '+' : ''}${net.toFixed(2)})${lock}`;
    });
    posEl.innerHTML = `<b>${LANG === 'bn' ? 'ট্রেড:' : 'Trades:'}</b> ${parts.length ? parts.join(' · ') : (LANG === 'bn' ? 'নেই' : 'none')}`;
  }
  const stats = b.stats || {};
  const actionText = b.last_action ? ` · <i>${esc(b.last_action)}</i>` : '';
  const lastEl = $('bot4-last');
  lastEl.innerHTML = `${esc(t('botMarketNow'))}: <b>${esc(shown)}</b> · ` +
    `${stats.opened || 0} ${t('botOpened')} / ${stats.closed || 0} ${t('botClosed')} (P/L: $${Number(stats.realized || 0).toFixed(2)})${actionText}`;
  lastEl.className = 'bot-line' + (b.last_error ? ' warn' : '');
}

function renderBot4Ladder(b) {
  const lad = b.ladder || {};
  const isBn = LANG === 'bn';
  for (const side of ['buy', 'sell']) {
    const el = side === 'buy' ? $('bot4-buy-ladder') : $('bot4-sell-ladder');
    if (!el) continue;
    const rows = lad[side] || [];
    if (!rows.length) {
      el.innerHTML = `<div class="lr-empty">${isBn ? 'এখনো কোনো প্রি-ট্রেড নেই' : 'No pre-trades yet'}</div>`;
      continue;
    }
    el.innerHTML = rows.map((r) => {
      const st = r.state || 'pending';
      const lbl = {
        pending: isBn ? 'পেন্ডিং' : 'pending',
        filled: isBn ? 'হিট' : 'filled',
        closed: isBn ? 'ক্লোজড' : 'closed',
        cancelled: isBn ? 'বাতিল' : 'cancelled',
        failed: isBn ? 'ব্যর্থ' : 'failed',
      }[st] || st;
      const lock = r.armed ? ' 🔒' : '';
      return `<div class="ladder-row ${st}">
        <span class="lr-no">#${r.level}</span>
        <span class="lr-price">${r.price ? fmtPrice(r.price) : '—'}</span>
        <span class="lr-lot">${Number(r.lot || 0).toFixed(2)}</span>
        <span class="lr-state st-${st}">${lbl}${lock}</span>
      </div>`;
    }).join('');
  }
  const pendEl = $('bot4-pending-pill');
  const all = (lad.buy || []).concat(lad.sell || []);
  const pend = all.filter((r) => r.state === 'pending').length;
  if (pendEl) pendEl.textContent = `${pend} PENDING`;
  const anchEl = $('bot4-anchor-pill');
  if (anchEl) anchEl.textContent = b.price ? `🎯 ${fmtPrice(b.price)}` : '—';
}

async function bot4Post(path, body) {
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await res.json();
    if (!res.ok) {
      const msg = (j.error && j.error.message) || j.detail || 'Request failed';
      toast('error', msg);
      return j;
    }
    if (j.bot4) { if (S) { S.bot4 = j.bot4; renderBot4(S); } }
    return j;
  } catch (e) { toast('error', String(e)); return {}; }
}

function bindBot4UI() {
  $('bot4-start').addEventListener('click', async () => {
    const sym = (S && S.bot4 && S.bot4.symbol) || 'XAUUSDm';
    if (!isMarketOpen(sym)) {
      toast('error', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ থাকায় বট চালু করা যাবে না` : `Cannot start bot: ${sym} market is currently closed`);
      return;
    }
    const lot = Number($('bot4-lot').value || 0.01);
    const priceGap = Number($('bot4-price-gap').value || 1.0);
    const preGap = Number($('bot4-pre-gap').value || 0.5);
    const preValue = Math.max(1, parseInt($('bot4-pre-value').value || 5, 10));
    const ok = window.confirm(
      LANG === 'bn'
        ? `প্রি-ট্রেড ল্যাডার বট চালু করবেন?\n\nলট: ${lot}\nপ্রাইস গ্যাপ: ${priceGap}\nপ্রি ট্রেড গ্যাপ: ${preGap}\nপ্রি ট্রেড ভ্যালু: ${preValue} (উপরে ${preValue} + নিচে ${preValue})`
        : `Start Pre-Trade Ladder Bot?\n\nLot: ${lot}\nPrice Gap: ${priceGap}\nPre Trade Gap: ${preGap}\nPre Trade Value: ${preValue} (${preValue} buy above + ${preValue} sell below)`
    );
    if (!ok) return;
    const res = await bot4Post('/api/bot4/start', {
      lot, price_gap: priceGap, pre_trade_gap: preGap, pre_trade_value: preValue,
    });
    if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ৪ চালু হয়েছে' : 'Bot 4 Started');
  });

  $('bot4-stop').addEventListener('click', async () => {
    const res = await bot4Post('/api/bot4/stop', {});
    if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ৪ বন্ধ করা হলো' : 'Bot 4 Stopped');
  });

  $('bot4-close').addEventListener('click', async () => {
    if (!window.confirm(LANG === 'bn' ? 'বট ৪ এর সব খোলা ট্রেড এখনই ক্লোজ করবেন?' : 'Close all Bot 4 open positions right now?')) return;
    const res = await bot4Post('/api/bot4/close', {});
    if (res && res.ok) toast('ok', `${LANG === 'bn' ? 'ক্লোজ করা ট্রেড' : 'Closed positions'} (${res.closed || 0})`);
  });

  $('bot4-numbering').addEventListener('click', async () => {
    const cur = !!(S && S.bot4 && S.bot4.config && S.bot4.config.numbering_lot);
    const next = !cur;
    const res = await bot4Post('/api/bot4/config', { numbering_lot: next });
    if (res && res.ok) toast('ok', `Numbering Lot Size · ${next ? (LANG === 'bn' ? 'চালু' : 'ON') : (LANG === 'bn' ? 'বন্ধ' : 'OFF')}`);
  });

  $('bot4-lot').addEventListener('change', () => {
    void bot4Post('/api/bot4/config', { lot: Number($('bot4-lot').value || 0.01) });
  });
  $('bot4-price-gap').addEventListener('change', () => {
    void bot4Post('/api/bot4/config', { price_gap: Math.max(0, Number($('bot4-price-gap').value || 1.0)) });
  });
  $('bot4-pre-gap').addEventListener('change', () => {
    void bot4Post('/api/bot4/config', { pre_trade_gap: Math.max(0, Number($('bot4-pre-gap').value || 0.5)) });
  });
  $('bot4-pre-value').addEventListener('change', () => {
    void bot4Post('/api/bot4/config', { pre_trade_value: Math.max(1, parseInt($('bot4-pre-value').value || 5, 10)) });
  });

  $('bot4-market-chips').addEventListener('click', async (e) => {
    const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
    if (!chip || !chip.dataset.sym) return;
    const sym = chip.dataset.sym;
    if (chip.classList.contains('market-off') || !isMarketOpen(sym)) {
      toast('warn', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)` : `${sym} market is currently closed`);
      return;
    }

    document.querySelectorAll('#bot4-market-chips .chip')
      .forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
    $('bot4-symbol').textContent = sym;
    $('bot4-symbol-name').textContent = sym;
    $('bot4-market-chips').dataset.n = '';
    setBotCurveSymbol('bot4', sym);

    const res = await bot4Post('/api/bot4/config', { symbol: sym });
    if (res && res.ok) {
      if (res.bot4) {
        if (S) S.bot4 = res.bot4;
        renderBot4(S);
      }
      toast('ok', `${t('botMarketSwitched')}: ${sym}`);
      if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
    }
  });
}

/* ------------------------------------------------------- bot 5: news trader */
const BOT5_MODES = ['anticipate', 'straddle', 'follow'];
const BOT5_IMPACTS = ['high', 'medium', 'all'];

/* Short labels for every news site that feeds the calendar */
const NEWS_SRC_LABEL = {
  'tradingview': 'TV',
  'forex-factory': 'FF',
  'investing.com': 'INV',
  'yahoo-finance': 'YH',
  'google-news': 'G',
};

// ---- fast stand-in for toLocaleString(weekday, hour, minute) --------------
// Locale formatting costs ~0.5ms per call and the news page formats 460 rows
// on every rebuild - this keeps the exact same text at ~0ms.
const _WD_SHORT_EN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const _WD_SHORT_BN = ['রবি', 'সোম', 'মঙ্গল', 'বুধ', 'বৃহস্পতি', 'শুক্র', 'শনি'];
const _BN_DIGITS = ['০', '১', '২', '৩', '৪', '৫', '৬', '৭', '৮', '৯'];
const _bnDigits = (s) => String(s).replace(/[0-9]/g, (c) => _BN_DIGITS[c.charCodeAt(0) - 48]);

function fmtNewsWhen(ts, bn) {
  const d = new Date(Number(ts) * 1000);
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (!bn) return `${_WD_SHORT_EN[d.getDay()]} ${String(d.getHours()).padStart(2, '0')}:${mm}`;
  const h12 = String(d.getHours() % 12 || 12).padStart(2, '0');
  return `${_WD_SHORT_BN[d.getDay()]} ${_bnDigits(h12)}:${_bnDigits(mm)} ${d.getHours() < 12 ? 'AM' : 'PM'}`;
}

function renderBot5(d) {
  const b = d.bot5 || {};
  const cfg = b.config || {};
  const running = !!b.running;
  const live = !!d.connected;
  const isBn = LANG === 'bn';

  const chip = $('bot5-chip');
  if (chip) {
    chip.className = 'bot-chip ' + (!live ? 'offline' : running ? 'running' : 'stopped');
    chip.textContent = !live ? t('botOffline') : running ? t('botRunning') : t('botStopped');
  }
  if ($('bot5-start')) $('bot5-start').classList.toggle('active', running);
  if ($('bot5-stop')) $('bot5-stop').classList.toggle('active', !running && live);

  // ---- symbol + live price
  const wanted = b.symbol_requested || cfg.symbol || 'XAUUSDm';
  const sym = b.symbol || '';
  const symOk = !!b.symbol_ok && sym;
  const shown = symOk ? sym : wanted;
  if ($('bot5-symbol')) $('bot5-symbol').textContent = shown;

  let priceStr = '';
  if (b.ask && b.bid) {
    priceStr = ` · Ask <b>${fmtPrice(b.ask)}</b> / Bid <b>${fmtPrice(b.bid)}</b>`;
  }
  if ($('bot5-symbol-name')) {
    $('bot5-symbol-name').innerHTML = `${esc(shown)}${priceStr}`;
    $('bot5-symbol-name').style.color = symOk ? 'var(--accent)' : '#fca5a5';
  }

  // ---- market chips: ALL MARKETS the news engine covers -----------------
  const pick = $('bot5-market-chips');
  if (pick) {
    const options = (b.symbol_options && b.symbol_options.length ? b.symbol_options : []);
    const list = options.length ? options.slice() : [shown];
    if (!list.includes(shown)) list.unshift(shown);
    if (live) {
      const sig = list.join(',') + '|' + shown;
      if (pick.dataset.n !== sig) {
        pick.dataset.n = sig;
        pick.innerHTML = list.map((s) =>
          `<button type="button" class="chip${s === shown ? ' active' : ''}" data-sym="${esc(s)}">${esc(s)}</button>`).join('');
      }
      pick.classList.remove('hidden');
    } else {
      pick.classList.remove('hidden');
      pick.querySelectorAll('.chip').forEach(c => {
        c.classList.toggle('active', c.dataset.sym === shown);
      });
    }
  }
  setBotCurveSymbol('bot5', shown);

  // ---- feed pill + state --------------------------------------------
  const fp = $('bot5-feed-pill');
  if (fp) {
    const err = b.feed_error;
    fp.className = 'pill ' + (err ? 'down' : b.feed_count ? 'up' : '');
    fp.textContent = err ? 'NEWS ⚠' : 'NEWS';
    fp.title = err || '';
  }
  const fs = $('bot5-feed-state');
  if (fs) {
    let src = String(b.feed_source || (b.feed_count ? 'cache' : '—'));
    const when = b.feed_updated
      ? new Date(Number(b.feed_updated) * 1000).toLocaleTimeString(LANG === 'bn' ? 'bn-BD' : 'en-GB', { hour: '2-digit', minute: '2-digit' })
      : '';
    fs.textContent = `${src} · ${b.feed_count || 0} ${isBn ? 'ইভেন্ট' : 'events'}${when ? ` · ${when}` : ''}`;
    fs.className = 'radar-timer-pill' + (b.feed_error ? ' warn' : '');
    fs.title = b.feed_error || '';
  }

  // ---- inputs update if not focused ---------------------------------
  const setNum = (id, val, min) => {
    const el = $(id);
    if (!el || val === undefined || val === null) return;
    if (document.activeElement !== el && Number(el.value) !== Number(val)) el.value = val;
    if (min !== undefined) el.min = min;
  };
  setNum('bot5-lot', cfg.lot);
  setNum('bot5-max-trades', cfg.max_trades, 1);
  setNum('bot5-pre', cfg.pre_minutes, 0);
  setNum('bot5-post', cfg.post_minutes, 1);
  setNum('bot5-tp', cfg.tp);
  setNum('bot5-sl', cfg.sl);
  const modeEl = $('bot5-mode');
  if (modeEl && document.activeElement !== modeEl && BOT5_MODES.includes(cfg.mode)) modeEl.value = cfg.mode;
  const impEl = $('bot5-impact');
  if (impEl && document.activeElement !== impEl && BOT5_IMPACTS.includes(cfg.min_impact)) impEl.value = cfg.min_impact;

  window.__lastPollData = d;

  // ---- next-event card + bias + impacted markets --------------------
  const curImpactFilter = (window.__activeNewsImpactFilter || 'high').toLowerCase();
  const allNewsList = Array.isArray(b.news) ? b.news : [];
  const matchingNews = allNewsList.filter(n => String(n.impact || 'low').toLowerCase() === curImpactFilter);

  const filterBar = $('news-impact-filter-bar');
  if (filterBar) {
    filterBar.querySelectorAll('.news-impact-btn').forEach(btn => {
      btn.classList.toggle('active', (btn.dataset.impact || '').toLowerCase() === curImpactFilter);
    });
  }

  // Prefer an upcoming event that matches the impact filter; never fall back
  // to a past headline while a live upcoming event exists (the card must
  // always lead with what comes NEXT).
  const upcomingInMatch = matchingNews.find(n => !n.past && (Number(n.ts || 0) >= (Date.now() / 1000 - 60)));
  const ev = upcomingInMatch
    || (b.active_event || null)
    || matchingNews[0]
    || (allNewsList.find(n => !n.past && (Number(n.ts || 0) >= (Date.now() / 1000 - 60))) || allNewsList[0]);
  window.__currentNewsCardEvent = ev;
  const card = $('bot5-next-card');
  const wCard = $('watch-next-card');

  // Derive explicit bias & direction from the event, its impacted platform markets, or live dials
  const evImpacts = (ev && Array.isArray(ev.impacted_markets)) ? ev.impacted_markets : [];
  const evFirstSide = (ev && ev.side) || (evImpacts[0] && evImpacts[0].side) || (b.bias && b.bias.side) || '';
  const evFirstReason = (ev && ev.reason) || (evImpacts[0] && evImpacts[0].reason) || (b.bias && b.bias.reason) || '';
  const bias = {
    side: evFirstSide,
    reason: evFirstReason,
    confident: (ev && ev.confident !== undefined) ? ev.confident : (b.bias ? b.bias.confident : true),
  };
  const impChips = $('bot5-next-impacted-chips');
  const wImpChips = $('watch-next-impacted-chips');
  if (ev) {
    if (card) card.classList.remove('flat');
    if (wCard) wCard.classList.remove('flat');
    const titleTxt = ev.title || '—';
    const evTitle = $('bot5-event-title');
    if (evTitle) evTitle.textContent = titleTxt;
    const wTitle = $('watch-event-title');
    if (wTitle) wTitle.textContent = titleTxt;

    // backend now sends minutes_to for every row; fall back to the event's
    // own ts so the NEXT EVENT line can never read "in 0 min"
    const mins = (ev.minutes_to !== undefined && ev.minutes_to !== null)
      ? Number(ev.minutes_to)
      : Math.round((Number(ev.ts || 0) - Date.now() / 1000) / 60);
    const whenTxt = mins >= 0
      ? (isBn ? `আর ${fmtCountdown(mins)}` : `in ${fmtCountdown(mins)}`)
      : (isBn ? `${fmtCountdown(-mins)} আগে` : `${fmtCountdown(-mins)} ago`);
    const metaHtml =
      `<span class="news-impact imp-${esc(ev.impact || 'low')}">${esc(String(ev.impact || 'low').toUpperCase())}</span>` +
      `<span class="news-cur">${esc(ev.currency || '')}</span>` +
      `<span class="news-when">${whenTxt}</span>` +
      (ev.forecast ? `<span class="news-fp">${isBn ? 'ফোরকাস্ট' : 'F'}: ${esc(ev.forecast)}</span>` : '') +
      (ev.previous ? `<span class="news-fp">${isBn ? 'প্রিভিয়াস' : 'P'}: ${esc(ev.previous)}</span>` : '');
    const evMeta = $('bot5-event-meta');
    if (evMeta) evMeta.innerHTML = metaHtml;
    const wMeta = $('watch-event-meta');
    if (wMeta) wMeta.innerHTML = metaHtml;

    let side = String(bias.side || ev.side || '').toUpperCase();
    if (!side && evImpacts.length && evImpacts[0].side) {
      side = String(evImpacts[0].side).toUpperCase();
    }
    const confident = !!bias.confident;
    const pillClass = 'news-bias-pill ' + (side === 'BUY' ? 'buy' : side === 'SELL' ? 'sell' : 'wait') + (confident ? ' confident' : '');
    const pillText = side === 'BUY' ? '▲ BUY' : side === 'SELL' ? '▼ SELL' : (isBn ? 'ওয়েট' : 'WAIT');

    const pill = $('bot5-bias-pill');
    if (pill) {
      pill.className = pillClass;
      pill.textContent = pillText;
    }
    const wPill = $('watch-bias-pill');
    if (wPill) {
      wPill.className = pillClass;
      wPill.textContent = pillText;
    }

    const reasonTxt = bias.reason || (isBn ? 'বিয়াস হিসাব হচ্ছে…' : 'Calculating bias…');
    const evReason = $('bot5-bias-reason');
    if (evReason) evReason.textContent = reasonTxt;
    const wReason = $('watch-bias-reason');
    if (wReason) wReason.textContent = reasonTxt;

    // AI Expected Move (e.g. +$18.50 UP / -$22.00 DOWN)
    const pred = ev.predicted_move || (bias && bias.predicted_move);
    let predHtml = '';
    const moveSide = side || (pred && pred.direction === 'UP' ? 'BUY' : (pred && pred.direction === 'DOWN' ? 'SELL' : ''));
    if (pred && (pred.text_en || pred.val_str) && moveSide) {
      const isBuy = moveSide.toLowerCase() === 'buy';
      const moveTxt = isBn ? (pred.text_bn || pred.text_en || pred.val_str) : (pred.text_en || pred.val_str);
      const tip = `${isBn ? 'AI প্রত্যাশিত মুভ' : 'AI Expected Move'}: ${moveTxt} (${pred.range_str || ''}) · ${esc(pred.market || '')}`;
      predHtml = `<span class="news-pred-pill ${isBuy ? 'up' : 'down'}" title="${esc(tip)}">
        <span class="pred-ai-tag">⚡ AI</span>
        <span>${esc(moveTxt)}</span>
      </span>`;
    }
    const predEl = $('bot5-predicted-move');
    if (predEl) predEl.innerHTML = predHtml;
    const wPredEl = $('watch-predicted-move');
    if (wPredEl) wPredEl.innerHTML = predHtml;

    // Impacted markets and directions (BUY / SELL)
    const impacts = evImpacts.length ? evImpacts : (side ? [
      { market: 'XAUUSDm', side: side === 'BUY' ? 'BUY' : 'SELL', reason: `${ev.currency || ''} sentiment` },
      { market: 'USOILm', side: side === 'BUY' ? 'BUY' : 'SELL', reason: 'Commodity demand' },
      { market: 'DXYm', side: side === 'BUY' ? 'SELL' : 'BUY', reason: 'USD safe haven' },
      { market: 'BTCUSDm', side: side === 'BUY' ? 'BUY' : 'SELL', reason: 'Risk flow' },
    ] : []);
    let chipsHtml = '';
    if (impacts.length) {
      chipsHtml = impacts.map(m => {
        const isBuy = String(m.side || '').toUpperCase() === 'BUY';
        const mTxt = isBn ? (m.move_text_bn || m.move_text_en || '') : (m.move_text_en || '');
        const mBadge = mTxt ? `<span class="imp-move">(${esc(mTxt)})</span>` : '';
        return `<div class="imp-market-pill ${isBuy ? 'buy' : 'sell'}" title="${esc(m.reason || '')}${m.range_str ? ` · ${esc(m.range_str)}` : ''}">
          <span class="imp-sym">${esc(m.market)}</span>
          <span class="imp-dir">${isBuy ? '▲ BUY' : '▼ SELL'}</span>
          ${mBadge}
        </div>`;
      }).join('');
    } else {
      chipsHtml = `<span class="imp-none">${isBn ? 'মার্কেট বিশ্লেষণ হচ্ছে…' : 'Analyzing markets…'}</span>`;
    }
    if (impChips) impChips.innerHTML = chipsHtml;
    if (wImpChips) wImpChips.innerHTML = chipsHtml;
  } else {
    const predEl = $('bot5-predicted-move');
    if (predEl) predEl.innerHTML = '';
    const wPredEl = $('watch-predicted-move');
    if (wPredEl) wPredEl.innerHTML = '';
    if (card) card.classList.add('flat');
    if (wCard) wCard.classList.add('flat');
    const noNewsTxt = t('bot5NoNews');
    const evTitle = $('bot5-event-title');
    if (evTitle) evTitle.textContent = noNewsTxt;
    const wTitle = $('watch-event-title');
    if (wTitle) wTitle.textContent = noNewsTxt;

    const evMeta = $('bot5-event-meta');
    if (evMeta) evMeta.innerHTML = '—';
    const wMeta = $('watch-event-meta');
    if (wMeta) wMeta.innerHTML = '—';

    const pillWait = isBn ? 'ওয়েট' : 'WAIT';
    const pill = $('bot5-bias-pill');
    if (pill) {
      pill.className = 'news-bias-pill wait';
      pill.textContent = pillWait;
    }
    const wPill = $('watch-bias-pill');
    if (wPill) {
      wPill.className = 'news-bias-pill wait';
      wPill.textContent = pillWait;
    }

    const noEvReason = b.feed_error || (isBn ? 'কোনো ইভেন্ট নেই' : 'No event queued');
    const evReason = $('bot5-bias-reason');
    if (evReason) evReason.textContent = noEvReason;
    const wReason = $('watch-bias-reason');
    if (wReason) wReason.textContent = noEvReason;

    if (impChips) impChips.innerHTML = '—';
    if (wImpChips) wImpChips.innerHTML = '—';
  }
  const planTxt = b.plan || '—';
  const planEl = $('bot5-plan');
  if (planEl) planEl.textContent = planTxt;
  const wPlanEl = $('watch-plan');
  if (wPlanEl) wPlanEl.textContent = planTxt;

  // Render filtered news items in #watch-filtered-news-list
  const wFilteredList = $('watch-filtered-news-list');
  if (wFilteredList) {
    if (!matchingNews.length) {
      wFilteredList.innerHTML = `<div style="text-align:center; padding: 6px; color:#94a3b8; font-size:10px;">No ${curImpactFilter.toUpperCase()} impact events scheduled</div>`;
    } else {
      wFilteredList.innerHTML = matchingNews.slice(0, 5).map(n => {
        const side = String(n.side || '').toUpperCase();
        const sideClass = side === 'BUY' ? 'buy' : side === 'SELL' ? 'sell' : '';
        const mins = Number(n.minutes_to || 0);
        const timeStr = mins >= 0 ? `in ${fmtCountdown(mins)}` : `${fmtCountdown(-mins)} ago`;
        return `<div class="news-filtered-item">
          <div class="nfi-left">
            <span class="nfi-cur">${esc(n.currency || '')}</span>
            <span class="nfi-title">${esc(n.title || '')}</span>
          </div>
          <div class="nfi-right" style="display:flex; align-items:center; gap:8px;">
            ${side ? `<span class="nfi-side ${sideClass}">${side === 'BUY' ? '▲ BUY' : '▼ SELL'}</span>` : ''}
            <span class="nfi-time">${timeStr}</span>
          </div>
        </div>`;
      }).join('');
    }
  }

  // ---- AI direction board: NEXT-DAY window --------------------------------
  // The board always reads the NEXT day's market, never today's: on the 18th
  // it shows the 19th's verdicts, on the 19th the 20th's (18 -> 19 -> 20 ...),
  // computed from tomorrow 00:00 -> tomorrow 24:00 (local).
  const outEl = $('bot5-outlook-chips');
  const wOutEl = $('watch-outlook-chips');
  if (outEl || wOutEl) {
    const outlook = Array.isArray(b.outlook) ? b.outlook : [];
    let outHtml = '';
    if (!outlook.length) {
      outHtml = `<span class="outlook-empty">${isBn ? 'নিউজ বিশ্লেষণ হচ্ছে…' : 'Reading the news…'}</span>`;
    } else {
      const day0 = new Date(); day0.setHours(0, 0, 0, 0);
      const winStart = day0.getTime() / 1000 + 86400;     // tomorrow 00:00
      // only the board's own day (tomorrow) shapes the verdicts; stale rows
      // from before the backend update never drag the board back to today
      const rows = outlook.filter(o0 => !Number(o0.top_ts || 0) || Number(o0.top_ts || 0) >= winStart);
      const list = rows.length ? rows : outlook;
      const pills = list.map(o => {
        const v = String(o.verdict || '').toUpperCase();
        const cls = v === 'BUY' ? 'buy' : v === 'SELL' ? 'sell' : 'mixed';
        const arrow = v === 'BUY' ? '▲' : v === 'SELL' ? '▼' : '◆';
        const conf = Number(o.confidence || 0);
        const fTxt = String(o.forecast_text || '');
        const fCls = String(o.forecast_dollar || 0) > 0 ? 'up' : String(o.forecast_dollar || 0) < 0 ? 'down' : '';
        const when = Number(o.top_ts || 0)
          ? new Date(Number(o.top_ts) * 1000).toLocaleString(LANG === 'bn' ? 'bn-BD' : 'en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
          : '';
        const flowTxt = (o.buyer_pct !== undefined && o.seller_pct !== undefined)
          ? `${isBn ? 'ডায়াল' : 'dial'} BUY ${Number(o.buyer_pct).toFixed(0)}% / SELL ${Number(o.seller_pct).toFixed(0)}%`
          : '';
        const srcTxt = o.source === 'live flow'
          ? (isBn ? 'লাইভ ফ্লো (নিউজ নেই বা সমান)' : 'live flow (no news / tie)')
          : (o.source === 'news' ? (isBn ? 'নিউজ' : 'news') : (isBn ? 'নিরপেক্ষ' : 'flat'));
        const tip = [
          o.top_event || '',
          when,
          `${isBn ? 'সোর্স' : 'source'}: ${srcTxt}`,
          flowTxt,
          `${isBn ? 'প্রত্যাশিত' : 'expected'}: ${fTxt}`,
          `${Number(o.buy || 0)}B/${Number(o.sell || 0)}S · ${isBn ? 'কনফিডেন্স' : 'confidence'} ${conf}%`,
        ].filter(Boolean).join(' · ');
        const mark = o.source === 'live flow' ? ' ⚡' : '';
        return `<span class="outlook-pill ${cls}" title="${esc(tip)}">` +
          `<b>${esc(o.market)}</b>` +
          `<span class="outlook-verdict">${arrow} ${v}${mark}</span>` +
          `<span class="outlook-forecast ${fCls}">${esc(fTxt)}</span>` +
          `<span class="outlook-count">${Number(o.buy || 0)}B/${Number(o.sell || 0)}S · ${conf}%</span>` +
        `</span>`;
      }).join('');
      outHtml = `<div class="outlook-date-row">${pills}</div>`;
    }
    if (outEl) outEl.innerHTML = outHtml;
    if (wOutEl) wOutEl.innerHTML = outHtml;
  }

  // ---- news list -----------------------------------------------------
  // Shows ALL news (every currency + impact). Expired rows are skipped here and
  // removed live by tickBot5Countdown the second their time passes.
  const nowSec = Date.now() / 1000;
  const rawNews = Array.isArray(b.news) ? b.news : [];
  // upcoming events expire the second their time passes; published headlines
  // (Google / Yahoo / Investing) stay for their 12h reading window
  // ALL ECONOMIC NEWS panel impact filter: 'all' | 'high' | 'medium' | 'low'
  const allFilt = String((typeof window !== 'undefined' && window.__allNewsImpactFilter) || 'all').toLowerCase();
  const news = rawNews
    .filter(n => Number(n.expire_ts || n.ts || 0) > nowSec)
    .filter(n => allFilt === 'all' || String(n.impact || 'low').toLowerCase() === allFilt);
  document.querySelectorAll('#news-impact-all-bar .news-impact-btn').forEach(btn => {
    btn.classList.toggle('active', (btn.dataset.imp || '').toLowerCase() === allFilt);
  });
  const totalTxt = b.news_total ? ` <span class="news-total-pill">${b.news_total}</span>` : '';
  const headEl = $('bot5-news-count');
  if (headEl && headEl.dataset.sig !== totalTxt) {
    headEl.dataset.sig = totalTxt;
    headEl.innerHTML = totalTxt;
  }
  // which news sites are feeding this list, and how many events each brought
  const srcEl = $('bot5-news-sources');
  if (srcEl) {
    const srcs = Array.isArray(b.feed_sources) ? b.feed_sources : [];
    const srcHtml = srcs.length
      ? srcs.map(s => {
          const name = String(s.name || '');
          const cls = name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
          const label = NEWS_SRC_LABEL[name] || name.slice(0, 3).toUpperCase();
          return `<span class="news-src-badge src-${esc(cls)}" title="${esc(name)}">${esc(label)} · ${Number(s.count || 0)}</span>`;
        }).join('')
      : '';
    if (srcEl.dataset.sig !== srcHtml) {
      srcEl.dataset.sig = srcHtml;
      srcEl.innerHTML = srcHtml;
    }
  }
  const newsEl = $('bot5-news');
  // The list holds 400+ rows; rebuilding it on every engine push (~3/sec) was
  // the single most expensive thing this page did - it churned the DOM and
  // forced a full page layout that every canvas read then paid for. Rebuild
  // only when the payload really changes, plus once a minute so the relative
  // "in 3h 6m" labels stay fresh. Expired rows are still removed instantly by
  // tickBot5Countdown, and the next rebuild picks the filtered list up anyway.
  let newsSig = `${Math.floor(nowSec / 60)}|${news.length}|${Number(b.news_total || 0)}|${Number(b.feed_updated || 0)}|${allFilt}`;
  for (let ni = 0; ni < news.length; ni++) {
    const n0 = news[ni];
    newsSig += `|${Number(n0.ts || 0)}:${n0.traded ? 1 : 0}:${String(n0.side || '')}`;
  }
  const newsDirty = !!newsEl && newsEl.dataset.sig !== newsSig;
  if (newsEl && newsDirty) {
    newsEl.dataset.sig = newsSig;
    // tell the decorative candle background a heavy (400+ row) rebuild is
    // coming: it drops to ~15 fps for a moment so the main thread stays free
    try { window.dispatchEvent(new Event('softradex:busy')); } catch (e) {}
  }
  if (newsEl && newsDirty && !news.length) {
    newsEl.innerHTML = `<div class="news-empty">${isBn ? 'ক্যালেন্ডারে কোনো আসন্ন ইভেন্ট নেই' : 'No upcoming events on the calendar'}</div>`;
  } else if (newsEl && newsDirty) {
    newsEl.innerHTML = news.map((n) => {
      const ts = Number(n.ts || 0);
      const diffSec = ts - nowSec;
      const mins = Math.round(diffSec / 60);
      const isPast = n.past === true;
      const side = String(n.side || '').toUpperCase();
      let whenTxt = '';
      if (isPast) {
        const agoMin = Math.max(1, Math.round(-diffSec / 60));
        const agoTxt = agoMin < 60 ? (isBn ? `${agoMin}মি. আগে` : `${agoMin}m ago`)
          : agoMin < 1440 ? (isBn ? `${Math.floor(agoMin / 60)}ঘ. আগে` : `${Math.floor(agoMin / 60)}h ago`)
          : (isBn ? `${Math.floor(agoMin / 1440)}দিন আগে` : `${Math.floor(agoMin / 1440)}d ago`);
        whenTxt = '📰 ' + agoTxt;
      } else if (diffSec <= 0) {
        whenTxt = isBn ? '⏱ লাইভ' : '⏱ LIVE';
      } else if (mins < 60) {
        whenTxt = isBn ? `আর ${mins}মি.` : `in ${mins}m`;
      } else if (mins < 1440) {
        const h = Math.floor(mins / 60), rm = mins % 60;
        whenTxt = isBn ? `আর ${h}ঘ.${rm ? ` ${rm}মি.` : ''}` : `in ${h}h${rm ? ` ${rm}m` : ''}`;
      } else {
        const d = Math.floor(mins / 1440), rh = Math.floor((mins % 1440) / 60);
        whenTxt = isBn ? `আর ${d}দিন ${rh}ঘ.` : `in ${d}d ${rh}h`;
      }
      const when = ts ? fmtNewsWhen(ts, LANG === 'bn') : '';
      const fStr = n.forecast != null ? `<span class="news-forecast-tag" title="Forecast: ${esc(n.forecast)}">F: ${esc(n.forecast)}</span>` : '';

      const pred = n.predicted_move;
      let moveBadge = '';
      if (pred && pred.text_en && side) {
        const isBuy = side === 'BUY';
        const txt = isBn ? (pred.text_bn || pred.text_en) : pred.text_en;
        const tip = `${isBn ? 'AI প্রত্যাশিত মার্কেট মুভ' : 'AI Expected Market Move'}: ${txt} (${pred.range_str || ''}) · ${esc(pred.market || '')}`;
        moveBadge = `<span class="news-row-move ${isBuy ? 'up' : 'down'}" title="${esc(tip)}">
          <span class="move-icon">⚡ AI</span>
          <span>${esc(txt)}</span>
        </span>`;
      }

      const impacts = Array.isArray(n.impacted_markets) ? n.impacted_markets : [];
      // every affected market is listed with its direction - the backend sends
      // the full list, nothing is collapsed into "+N more"
      const impactPills = impacts.map(m => {
        const isBuy = String(m.side || '').toUpperCase() === 'BUY';
        const mTxt = isBn ? (m.move_text_bn || m.move_text_en || '') : (m.move_text_en || '');
        const mTag = mTxt ? `<span class="market-dir-move">${esc(mTxt)}</span>` : '';
        return `<span class="market-dir-pill ${isBuy ? 'buy' : 'sell'}" title="${esc(m.market)} · ${esc(m.reason || '')}${m.range_str ? ` (${esc(m.range_str)})` : ''}">` +
          `<b>${esc(m.market)}</b>: ${isBuy ? '▲ BUY' : '▼ SELL'}${mTag ? ' · ' + mTag : ''}` +
        `</span>`;
      }).join('');
      const watchOnly = (n.qualified === false && !isPast)
        ? `<span class="news-watch-tag" title="${esc(t('bot5WatchOnly'))}">👁</span>` : '';
      const srcLabel = NEWS_SRC_LABEL[String(n.source || '')] || '';
      const srcTag = srcLabel
        ? `<span class="news-src-badge" title="${esc((n.publisher ? n.publisher + ' · ' : '') + (n.source || ''))}">${esc(srcLabel)}</span>` : '';

      return `<div class="news-row${n.traded ? ' traded' : ''}${(n.qualified === false && !isPast) ? ' watch-only' : ''}${isPast ? ' published' : ''}" data-ts="${isPast ? Number(n.expire_ts || ts) : ts}">
        <div class="news-row-top">
          <span class="news-impact imp-${esc(n.impact || 'low')}">${esc(String(n.impact || 'low').toUpperCase())}</span>
          <span class="news-cur">${esc(n.currency || '')}</span>
          <span class="news-title" title="${esc(n.title || '')}">${esc(n.title || '')}</span>
          ${fStr}
          ${srcTag}
          <span class="news-time">${esc(when)}</span>
          <span class="news-min">${whenTxt}</span>
          ${side ? `<span class="news-side ${side === 'BUY' ? 'buy' : 'sell'}">${side === 'BUY' ? '▲ BUY' : '▼ SELL'}</span>` : ''}
          ${moveBadge}
          ${watchOnly}
          ${n.traded ? `<span class="news-traded">✓</span>` : ''}
        </div>
        ${impacts.length ? `<div class="news-row-impacts"><span class="impacts-label">${isBn ? 'ইমপ্যাক্ট:' : 'Markets:'}</span>${impactPills}</div>` : ''}
      </div>`;
    }).join('');
  }

  // ---- status lines --------------------------------------------------
  const rl = $('bot5-running-line');
  if (rl) {
    const modeTxt = { anticipate: isBn ? 'অ্যান্টিসিপেট' : 'Anticipate', straddle: isBn ? 'স্ট্র্যাডল' : 'Straddle', follow: isBn ? 'ফলো' : 'Follow' }[cfg.mode] || cfg.mode;
    rl.innerHTML = `<b>${isBn ? 'স্টাইল' : 'Style'}:</b> ${esc(modeTxt)} · ` +
      `${b.open || 0} ${isBn ? 'ওপেন' : 'open'} · ${b.pending || 0} ${isBn ? 'পেন্ডিং' : 'pending'} · ` +
      `${isBn ? 'ফ্লোটিং' : 'Floating'}: <span class="${Number(b.floating || 0) >= 0 ? 'up' : 'down'}">${signed(b.floating || 0)}</span>`;
  }
  const posEl = $('bot5-pos');
  if (posEl) {
    const ps = b.positions || [];
    const parts = ps.map((p) => {
      const side = String(p.side || '').toUpperCase();
      const net = Number(p.net || 0);
      return `${side} ${nf(p.lot, 2)} @ ${fmtPrice(p.entry)} (${net >= 0 ? '+' : ''}${net.toFixed(2)})`;
    });
    posEl.innerHTML = `<b>${isBn ? 'ট্রেড' : 'Trades'}:</b> ${parts.length ? parts.join(' · ') : (isBn ? 'নেই' : 'none')}`;
  }
  const stats = b.stats || {};
  const last = b.last_error
    ? `<span style="color:#fca5a5">${esc(b.last_error)}</span>`
    : esc(b.last_action || '—');
  const lastEl = $('bot5-last');
  if (lastEl) {
    lastEl.innerHTML = `${esc(t('botMarketNow'))}: <b>${esc(shown)}</b> · ` +
      `${stats.opened || 0} ${esc(t('botOpened'))} / ${stats.closed || 0} ${esc(t('botClosed'))} · ` +
      `${isBn ? 'রিয়েলাইজড' : 'Realized'}: $${Number(stats.realized || 0).toFixed(2)} · ${last}`;
    lastEl.className = 'bot-line' + (b.last_error ? ' warn' : '');
  }

  // ---- price map ------------------------------------------------------
  if ($('bot5-pricemap')) renderPriceMap('bot5', b);
}

function fmtCountdown(mins) {
  const m = Math.max(0, Math.round(Number(mins) || 0));
  if (m >= 1440) return `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`;
  if (m >= 60) return `${Math.floor(m / 60)}h ${m % 60}m`;
  return `${m}m`;
}

/* 1-second live countdown to the next news event and immediate auto-removal of expired news */
/* ==========================================================================
   Trading sessions board — Sydney (Australian), Tokyo (Asian), London
   (European), New York (North American).

   The clock is computed by the backend (daylight-saving aware, absolute open /
   close epochs) and then ticked locally at 1 Hz between pushes, so the
   countdown never waits for the next WebSocket frame. The AI reading next to
   each session is measured - the last ~20 days of M15 bars clipped to that
   session window give the average dollars up, dollars down and the net drift
   that decides BUY / SELL.
   ========================================================================== */
let sessionsBoard = null;
let sessionsSig = '';

const SESSION_LABEL = { sydney: 'sessSydney', tokyo: 'sessTokyo', london: 'sessLondon', newyork: 'sessNewYork' };
const SESSION_REGION = { sydney: 'sessRegAu', tokyo: 'sessRegAsia', london: 'sessRegEu', newyork: 'sessRegNa' };

function sessHms(secs) {
  const s = Math.max(0, Math.round(Number(secs) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

function sessMoney(v) { return '$' + Math.abs(Number(v) || 0).toFixed(2); }

function sessWhen(row, secs) {
  if (row.state === 'open') return `LIVE · Ends in ${sessHms(secs)}`;
  if (row.weekend) return 'Weekend break · reopens Monday';
  return `Starts in ${sessHms(secs)}`;
}

function sessRowHtml(row, board) {
  const key = String(row.key || '');
  const isOpen = row.state === 'open';
  const st = row.stats || null;
  const flow = row.flow || {};
  const flowSide = String(flow.dominant || '').toUpperCase();
  const region = row.region_en || (SESSION_REGION[key] ? t(SESSION_REGION[key]) : '') || '';
  const peak = row.peak ? `<span class="sess-peak" title="London + New York overlap - heaviest liquidity">PEAK</span>` : '';
  const statePill = `<span class="sess-state ${isOpen ? 'open' : 'closed'}">${isOpen ? 'OPEN' : 'CLOSED'}</span>`;

  let aiHtml = `<div class="sess-nodata">No session history - connect terminal</div>`;
  if (st) {
    const v = String(st.verdict || 'MIXED').toUpperCase();
    const cls = v === 'BUY' ? 'buy' : v === 'SELL' ? 'sell' : 'mixed';
    const arrow = v === 'BUY' ? '▲' : v === 'SELL' ? '▼' : '◆';

    const buyAmt = sessMoney(st.buy_money !== undefined ? st.buy_money : st.up_money);
    const sellAmt = sessMoney(st.sell_money !== undefined ? st.sell_money : st.down_money);
    const rangeAmt = sessMoney(st.range_money);

    const allM = Array.isArray(row.all_markets) ? row.all_markets : [];
    const mSum = row.market_summary || {};

    let marketsHtml = '';
    if (allM.length) {
      marketsHtml = `<div class="sess-markets-block">` +
        `<div class="sess-markets-title">` +
          `<span>🌐 All Markets in Session:</span>` +
          `<span class="sess-markets-count-badge">${mSum.buy_count || 0} BUY · ${mSum.sell_count || 0} SELL</span>` +
        `</div>` +
        `<div class="sess-markets-list">` +
          allM.map(m => {
            const mv = String(m.verdict || 'MIXED').toUpperCase();
            const mcls = mv === 'BUY' ? 'buy' : mv === 'SELL' ? 'sell' : 'mixed';
            const marr = mv === 'BUY' ? '▲' : mv === 'SELL' ? '▼' : '◆';
            const cleanSym = String(m.symbol || '').replace(/m$/, '');
            const mVal = mv === 'BUY' ? `+$${Number(m.buy_money || 0).toFixed(1)}` : mv === 'SELL' ? `-$${Number(m.sell_money || 0).toFixed(1)}` : `~$${Number(m.range_money || 0).toFixed(1)}`;
            const isCur = String(board.symbol || '') === m.symbol;
            return `<button type="button" class="sess-mini-chip ${mcls}${isCur ? ' active' : ''}" data-sym="${esc(m.symbol)}" title="${esc(m.symbol)}: ${mv} (${mVal})">` +
              `<b>${esc(cleanSym)}</b> ${marr} ${mv} <small>${mVal}</small>` +
            `</button>`;
          }).join('') +
        `</div>` +
      `</div>`;
    }

    aiHtml =
      `<div class="sess-action-line">` +
        `<span class="sess-action-label">ACTION:</span>` +
        `<span class="sess-action-pill ${cls}">${arrow} ${v}</span>` +
        `${st.confidence ? `<span class="sess-win-stat">${st.confidence}% conf</span>` : ''}` +
      `</div>` +
      `<div class="sess-fc-grid">` +
        `<div class="sess-fc-card buy" title="Estimated upward potential if BUY">` +
          `<span class="sess-fc-tag">▲ BUY TARGET</span>` +
          `<span class="sess-fc-amt">+${buyAmt} Up</span>` +
        `</div>` +
        `<div class="sess-fc-card sell" title="Estimated downward potential if SELL">` +
          `<span class="sess-fc-tag">▼ SELL TARGET</span>` +
          `<span class="sess-fc-amt">-${sellAmt} Down</span>` +
        `</div>` +
        `<div class="sess-fc-card range" title="Expected total session range">` +
          `<span class="sess-fc-tag">↕ EXP RANGE</span>` +
          `<span class="sess-fc-amt">↕ ${rangeAmt}</span>` +
        `</div>` +
      `</div>` +
      `<div class="sess-ai-verdict-line">` +
        `${flowSide ? `<span class="sess-flow ${flowSide === 'BUY' ? 'buy' : flowSide === 'SELL' ? 'sell' : 'flat'}" title="Live watch-dial buyer vs seller">DIAL ${flowSide === 'BUY' ? '▲' : flowSide === 'SELL' ? '▼' : '◆'} ${esc(flowSide)} ${Number(flow.buyer_pct || 0).toFixed(0)}%</span>` : ''}` +
        `<span class="sess-win-stat">${st.up_days || 0}/${st.days || 20} days up</span>` +
      `</div>` +
      marketsHtml;
  }

  const secs = isOpen ? Number(row.closes_in_sec || 0) : Number(row.opens_in_sec || 0);
  const pct = isOpen ? Math.max(0, Math.min(100, Number(row.progress || 0) * 100)) : 0;
  return `<div class="sess-row ${isOpen ? 'live' : 'closed'}${row.peak ? ' peak' : ''}" data-key="${esc(key)}">` +
      `<div class="sess-top">` +
        `<span class="sess-name">${esc(t(SESSION_LABEL[key] || '') || row.name_en || key)}</span>` +
        `<span class="sess-region">${esc(region)}</span>` +
        `${peak}${statePill}` +
      `</div>` +
      `<div class="sess-count" data-key="${esc(key)}">${esc(sessWhen(row, secs))}</div>` +
      `<div class="sess-bar"><span class="sess-bar-fill" style="width:${pct.toFixed(1)}%"></span></div>` +
      `<div class="sess-time-block">` +
        `<div class="sess-time-row"><span><b>Starts:</b> ${esc(row.open_utc || '--:--')} UTC</span><span><b>Ends:</b> ${esc(row.close_utc || '--:--')} UTC</span></div>` +
        `<div class="sess-time-row sub"><span>Local: ${esc(row.open_local || '--:--')} – ${esc(row.close_local || '--:--')}</span><span>(${esc(row.tz_offset || 'Local')})</span></div>` +
      `</div>` +
      aiHtml +
    `</div>`;
}

function renderSessions(data) {
  const grid = $('sessions-grid');
  if (!grid) return;
  const board = (data && data.bot5 && data.bot5.sessions) || null;
  const sel = $('sessions-market');
  const sumEl = $('sessions-summary');
  const updEl = $('sessions-updated');
  if (!board || !Array.isArray(board.sessions) || !board.sessions.length) {
    if (sessionsSig !== 'empty') {
      sessionsSig = 'empty';
      grid.innerHTML = `<div class="sessions-loading">${esc(t('sessLoading'))}</div>`;
    }
    return;
  }
  bindSessionsUI();
  sessionsBoard = board;

  if (sel) {
    const syms = Array.isArray(board.symbols) && board.symbols.length
      ? board.symbols.slice()
      : (board.symbol ? [board.symbol] : []);
    const cur = String(board.symbol || '');
    const msig = syms.join('|') + '#' + cur;
    if (sel.dataset.sig !== msig) {
      sel.dataset.sig = msig;
      sel.innerHTML = syms.map(s => `<option value="${esc(s)}"${s === cur ? ' selected' : ''}>${esc(s)}</option>`).join('');
    }
  }

  const radarBadge = $('sess-active-session-badge');
  const radarCounts = $('sess-radar-counts');
  const radarChips = $('sess-radar-chips');
  if (radarChips) {
    const openSess = (board.sessions || []).find(r => r.state === 'open') || (board.sessions || [])[0];
    if (openSess) {
      const sKey = openSess.key;
      const sName = t(SESSION_LABEL[sKey] || '') || openSess.name_en || sKey;
      if (radarBadge) {
        radarBadge.textContent = `${openSess.state === 'open' ? '🔴 LIVE: ' : 'NEXT: '}${sName.toUpperCase()}`;
        radarBadge.style.color = openSess.state === 'open' ? 'var(--win)' : 'var(--accent)';
      }
      const sMarkets = Array.isArray(openSess.all_markets) ? openSess.all_markets : [];
      const sSum = openSess.market_summary || {};
      if (radarCounts) {
        radarCounts.innerHTML = `<span style="color:var(--win)">${sSum.buy_count || 0} BUY ▲</span> · ` +
          `<span style="color:var(--loss)">${sSum.sell_count || 0} SELL ▼</span>` +
          (sSum.mixed_count ? ` · <span style="color:var(--warn)">${sSum.mixed_count} MIXED</span>` : '');
      }
      if (sMarkets.length) {
        radarChips.innerHTML = sMarkets.map(m => {
          const mv = String(m.verdict || 'MIXED').toUpperCase();
          const mcls = mv === 'BUY' ? 'buy' : mv === 'SELL' ? 'sell' : 'mixed';
          const marr = mv === 'BUY' ? '▲' : mv === 'SELL' ? '▼' : '◆';
          const cleanSym = String(m.symbol || '').replace(/m$/, '');
          const isCur = String(board.symbol || '') === m.symbol;
          const amtStr = mv === 'BUY' ? `+$${Number(m.buy_money || 0).toFixed(1)}` : mv === 'SELL' ? `-$${Number(m.sell_money || 0).toFixed(1)}` : `~$${Number(m.range_money || 0).toFixed(1)}`;
          return `<button type="button" class="sess-radar-pill ${mcls}${isCur ? ' active' : ''}" data-sym="${esc(m.symbol)}" title="${esc(m.symbol)}: ${mv} (${amtStr})">` +
            `<span class="pill-sym">${esc(cleanSym)}</span>` +
            `<span class="pill-dir">${marr} ${mv}</span>` +
            `<span class="pill-amt">${amtStr}</span>` +
          `</button>`;
        }).join('');
      } else {
        radarChips.innerHTML = `<span style="font-size:11px;color:var(--txt-faint)">Loading all markets forecast for this session…</span>`;
      }
    }
  }

  // Render ALL 4 sessions (Sydney, Tokyo, London, New York)
  const allSessions = Array.isArray(board.sessions) && board.sessions.length ? board.sessions : [];
  const sig = [board.symbol, board.updated, board.source, board.tz_data ? 1 : 0, allSessions.length,
    allSessions.map(r => `${r.key}:${r.state}:${r.open_ts}:${r.peak ? 1 : 0}:${(r.flow || {}).dominant || ''}:${r.stats ? `${r.stats.verdict}${r.stats.up_money}${r.stats.down_money}${r.stats.days}` : '-'}:${(r.all_markets || []).length}`).join(',')].join('#');
  if (sig !== sessionsSig) {
    sessionsSig = sig;
    grid.innerHTML = allSessions.length
      ? allSessions.map(r => sessRowHtml(r, board)).join('')
      : `<div class="sessions-loading">Starting the session clock…</div>`;
  }

  const sum = board.summary || null;
  if (sumEl) {
    if (!sum) {
      sumEl.textContent = `${board.symbol || ''} — No session data`;
    } else {
      const v = String(sum.verdict || 'MIXED').toUpperCase();
      const arrow = v === 'BUY' ? '▲' : v === 'SELL' ? '▼' : '◆';
      const name = t(SESSION_LABEL[sum.key] || '') || sum.name_en || '';
      const buyM = sessMoney(sum.buy_money !== undefined ? sum.buy_money : sum.up_money);
      const sellM = sessMoney(sum.sell_money !== undefined ? sum.sell_money : sum.down_money);
      const rangeM = sessMoney(sum.range_money);
      sumEl.innerHTML = `<span class="sess-sum-market">${esc(board.symbol || '')}</span>` +
        `<span class="sess-sum-txt">🤖 AI Forecast: <b>${esc(name)}</b> moves the most here — ` +
        `<b class="${v === 'BUY' ? 'buy' : v === 'SELL' ? 'sell' : 'mixed'}">${arrow} ${esc(v)}</b> · ` +
        `BUY Target: <b style="color:var(--win)">+${buyM} Up</b> · ` +
        `SELL Target: <b style="color:var(--loss)">-${sellM} Down</b> · ` +
        `Range: <b style="color:#38bdf8">${rangeM}</b></span>`;
    }
  }
  if (updEl) {
    const ago = Math.max(0, Math.round(Date.now() / 1000 - Number(board.updated || 0)));
    updEl.textContent = `${t('sessUpdated')} ${ago}s · ${board.source === 'mt5' ? 'MT5 M15' : t('sessClockOnly')}`;
  }
  tickSessionsClock();
}

/* 1 Hz local tick: countdowns + the progress bar of the open session */
function tickSessionsClock() {
  const rows = document.querySelectorAll('.sess-row');
  if (!rows.length || !sessionsBoard) return;
  const skew = Number(sessionsBoard.now_utc || 0) - Date.now() / 1000;
  const nowUtc = Date.now() / 1000 + skew;
  const byKey = {};
  (sessionsBoard.sessions || []).forEach(r => { byKey[r.key] = r; });
  rows.forEach(el => {
    const row = byKey[el.dataset.key];
    if (!row) return;
    const isOpen = row.state === 'open';
    const secs = isOpen ? Number(row.close_ts || 0) - nowUtc : Number(row.open_ts || 0) - nowUtc;
    const countEl = el.querySelector('.sess-count');
    if (countEl) countEl.textContent = sessWhen(row, secs);
    const bar = el.querySelector('.sess-bar-fill');
    if (bar && isOpen) {
      const span = Math.max(1, Number(row.close_ts || 0) - Number(row.open_ts || 0));
      const done = Math.max(0, Math.min(1, (nowUtc - Number(row.open_ts || 0)) / span));
      bar.style.width = (done * 100).toFixed(1) + '%';
    }
    // the ticker also flips the card the moment a session opens / closes, so the
    // panel is never stale for the few hundred ms before the next push
    if (isOpen && secs <= 0) { el.classList.remove('live'); el.classList.add('closed'); }
    else if (!isOpen && secs <= 0) { el.classList.remove('closed'); el.classList.add('live'); }
  });
}

async function sessPost(path, body) {
  try {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) {
      const msg = (j.error && j.error.message) || 'Request failed';
      toast('error', msg);
      return {};
    }
    return j;
  } catch (e) { toast('error', String(e)); return {}; }
}

function bindSessionsUI() {
  const sel = $('sessions-market');
  if (sel && !sel.dataset.bound) {
    sel.dataset.bound = '1';
    sel.addEventListener('change', async () => {
      const sym = String(sel.value || '');
      if (!sym) return;
      sel.disabled = true;
      const j = await sessPost('/api/bot5/config', { session_market: sym });
      sel.disabled = false;
      if (j && j.bot5) {
        if (S) S.bot5 = j.bot5;
        sessionsSig = '';                 // force the AI numbers to be redrawn
        renderSessions({ bot5: j.bot5 });
        renderBot5(S || { bot5: j.bot5 });
      }
    });
  }

  const boardEl = $('sessions-board');
  if (boardEl && !boardEl.dataset.chipsBound) {
    boardEl.dataset.chipsBound = '1';
    boardEl.addEventListener('click', async (e) => {
      const btn = e.target.closest('.sess-mini-chip, .sess-radar-pill');
      if (!btn || !btn.dataset.sym) return;
      const sym = btn.dataset.sym;
      if (sel) sel.value = sym;
      const j = await sessPost('/api/bot5/config', { session_market: sym });
      if (j && j.bot5) {
        if (S) S.bot5 = j.bot5;
        sessionsSig = '';
        renderSessions({ bot5: j.bot5 });
        renderBot5(S || { bot5: j.bot5 });
      }
    });
  }
}

/* ==========================================================================
   Full AI market analysis — 5m / 10m / 15m / 1h.

   The backend reads the last 140 *closed* candles of every timeframe and turns
   four independent readings (EMA trend, momentum vs ATR, swing break, RSI) into
   BUY / SELL / NEUTRAL per timeframe; the timeframe verdicts are then voted
   together with the news bias and the live watch dials into one verdict, and
   the expected travel (ATR x 2) gives the price targets: how far up and how far
   down this market is expected to go, in price and in account money.

   Live update: every engine-tick WebSocket frame carries the fresh payload, so
   the whole panel follows the price without a timer of its own.
   ========================================================================== */
let aiBoard = null;
let aiSig = '';

const AI_TF_LABEL = { M1: '1m', M5: '5m', M15: '15m', H1: '1h', H4: '4h', D1: '1Day', M10: '10m' };

function aiSide(v) {
  const s = String(v || '').toUpperCase();
  return s === 'BUY' ? 'buy' : s === 'SELL' ? 'sell' : 'mixed';
}

function aiSideTxt(v) {
  const s = String(v || '').toUpperCase();
  if (s === 'BUY') return t('aiBuy');
  if (s === 'SELL') return t('aiSell');
  return t('aiNoTrade');
}

function aiArrow(v) {
  const s = String(v || '').toUpperCase();
  return s === 'BUY' ? '▲' : s === 'SELL' ? '▼' : '◆';
}

function aiMoney(v) {
  const n = Number(v) || 0;
  return `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(2)}`;
}

function aiPx(v, digits) { return (Number(v) || 0).toFixed(Number(digits) === 0 ? 2 : Number(digits || 2)); }

function aiRowHtml(row, board) {
  const v = String(row.verdict || '').toUpperCase();
  const cls = aiSide(v);
  const sel = String(board.timeframe || '') === String(row.key || '') ? ' sel' : '';
  const digits = Number(board.digits || 2);
  const name = esc(AI_TF_LABEL[row.key] || row.key);
  const reasons = (row.reasons || []).join(' · ');
  if (!row.ready) {
    return `<div class="ai-row${sel}" data-key="${esc(row.key)}">` +
      `<div class="ai-row-top"><span class="ai-tf-name">${name}</span>` +
      `<span class="ai-conf">${esc(t('aiNoData'))}</span></div>` +
    `</div>`;
  }
  const mpp = Number(board.money_per_price || 0);
  const upMoney = Number(row.up_distance || 0) * mpp;
  const downMoney = Number(row.down_distance || 0) * mpp;
  const score = Number(row.score || 0);
  return `<div class="ai-row ${cls}${sel}" data-key="${esc(row.key)}" title="${esc(reasons)}">` +
    `<div class="ai-row-top">` +
      `<span class="ai-tf-name">${name}</span>` +
      `<span class="sess-verdict ${cls}">${aiArrow(v)} ${esc(aiSideTxt(v))}</span>` +
      `<span class="ai-conf">${Number(row.confidence || 0)}%</span>` +
    `</div>` +
    `<div class="ai-row-mid"><span>RSI ${Number(row.rsi || 0).toFixed(0)}</span>` +
      `<span>ATR ${aiPx(row.atr, digits)}</span>` +
      `<span>${esc(t('aiScore'))} ${score >= 0 ? '+' : ''}${score}</span></div>` +
    `<div class="ai-row-up">▲ ${esc(t('aiUp'))} ${aiPx(row.up_price, digits)} <small>${aiMoney(Math.abs(upMoney))}</small></div>` +
    `<div class="ai-row-down">▼ ${esc(t('aiDown'))} ${aiPx(row.down_price, digits)} <small>${aiMoney(-Math.abs(downMoney))}</small></div>` +
    `<div class="ai-row-note">${esc((row.reasons || []).slice(0, 3).join(' · '))}</div>` +
  `</div>`;
}

function renderAiMarket(data) {
  const board = (data && data.bot5 && data.bot5.ai) || null;
  if (!$('ai-market-board')) return;
  const grid = $('ai-grid');
  const sel = $('ai-market');
  const tabs = $('ai-tf-tabs');
  const verEl = $('ai-verdict');
  const tgtEl = $('ai-targets');
  const updEl = $('ai-updated');
  if (!board || !Array.isArray(board.rows) || !board.rows.length) {
    if (aiSig !== 'empty') {
      aiSig = 'empty';
      if (grid) grid.innerHTML = `<div class="ai-empty">${esc(t('aiNoData'))}</div>`;
    }
    return;
  }
  bindAiMarketUI();
  aiBoard = board;

  // market selector — only what this account can actually trade
  if (sel) {
    const syms = Array.isArray(board.symbols) && board.symbols.length
      ? board.symbols.slice()
      : (board.symbol ? [board.symbol] : []);
    const cur = String(board.symbol || '');
    const msig = syms.join('|') + '#' + cur;
    if (sel.dataset.sig !== msig) {
      sel.dataset.sig = msig;
      sel.innerHTML = syms.map(s => `<option value="${esc(s)}"${s === cur ? ' selected' : ''}>${esc(s)}</option>`).join('');
    }
  }

  // Ensure all 6 timeframes (1m, 5m, 15m, 1h, 4h, 1Day) are always represented
  const EXPECTED_TFS = ['M1', 'M5', 'M15', 'H1', 'H4', 'D1'];
  const existingMap = {};
  (board.rows || []).forEach(r => { if (r && r.key) existingMap[r.key] = r; });
  const allRows = EXPECTED_TFS.map(tf => {
    if (existingMap[tf]) return existingMap[tf];
    return {
      key: tf,
      name: AI_TF_LABEL[tf] || tf,
      ready: false,
      verdict: 'MIXED',
      score: 0,
      confidence: 0,
      reasons: ['Awaiting MT5 candles for ' + (AI_TF_LABEL[tf] || tf)],
    };
  });

  // timeframe tabs — click one to make it the timeframe of the targets
  if (tabs) {
    const tsig = `${board.timeframe}|${LANG}|` + allRows.map(r => `${r.key}:${r.verdict}`).join(',');
    if (tabs.dataset.sig !== tsig) {
      tabs.dataset.sig = tsig;
      tabs.innerHTML = allRows.map(r => {
        const on = String(board.timeframe || '') === String(r.key || '');
        const label = esc(AI_TF_LABEL[r.key] || r.key);
        const tip = `${t('aiTimeframe')}: ${label} · ${aiArrow(r.verdict)} ${aiSideTxt(r.verdict)} ${Number(r.confidence || 0)}%`;
        return `<span class="ai-tab ${aiSide(r.verdict)}${on ? ' sel' : ''}" data-tf="${esc(r.key)}" title="${esc(tip)}">${label} ${aiArrow(r.verdict)}</span>`;
      }).join('');
    }
  }

  const digits = Number(board.digits || 2);
  const comb = board.combined || {};
  const tgt = board.target || {};

  // the one-line AI answer: which side, how confident, on which timeframe
  if (verEl) {
    const v = String(comb.verdict || '').toUpperCase();
    const why = (comb.reasons || []).join(' · ');
    verEl.innerHTML = `<span class="ai-side ${aiSide(v)}">${aiArrow(v)} ${esc(aiSideTxt(v))}</span> ` +
      `<b>${Number(comb.confidence || 0)}%</b> ${esc(t('aiConfidence'))} · ` +
      `${esc(t('aiTimeframe'))} <b>${esc(AI_TF_LABEL[board.timeframe] || board.timeframe || '')}</b> · ` +
      `${esc(t('aiPriceNow'))} <b>${Number(board.price) > 0 ? aiPx(board.price, digits) : '—'}</b>` +
      `${why ? `<span class="ai-why" title="${esc(why)}">${esc(t('aiWhy'))}: ${esc(why)}</span>` : ''}`;
  }

  // the price targets: how far this market travels up and down from here
  if (tgtEl) {
    if (tgt.ready) {
      const upRole = String(tgt.primary_side || '').toUpperCase() === 'BUY' ? t('aiUpTarget') : t('aiRisk');
      const downRole = String(tgt.primary_side || '').toUpperCase() === 'SELL' ? t('aiDownTarget') : t('aiRisk');
      tgtEl.innerHTML =
        `<span class="ai-target up" title="${esc(t('aiUpTarget'))}"><b>▲ ${esc(t('aiUp'))} ${aiPx(tgt.up_price, digits)}</b> ` +
          `<small>${aiMoney(tgt.up_money)} · ${esc(upRole)}</small></span>` +
        `<span class="ai-target down" title="${esc(t('aiDownTarget'))}"><b>▼ ${esc(t('aiDown'))} ${aiPx(tgt.down_price, digits)}</b> ` +
          `<small>${aiMoney(-Math.abs(tgt.down_money))} · ${esc(downRole)}</small></span>` +
        `<span class="ai-target level"><b>${esc(t('aiLevels'))}</b> ` +
          `<small>${aiPx(tgt.level_up, digits)} / ${aiPx(tgt.level_down, digits)}</small></span>`;
    } else {
      tgtEl.innerHTML = `<span class="ai-target">${esc(board.source === 'mt5' ? t('aiNoData') : t('aiNoTerminal'))}</span>`;
    }
  }

  const sig = [board.symbol, board.timeframe, board.source, board.digits, LANG,
    (comb.verdict || '') + Number(comb.confidence || 0),
    tgt.ready ? `${tgt.up_price}:${tgt.down_price}` : '-',
    allRows.map(r => `${r.key}:${r.verdict}:${r.score}:${r.confidence}:${r.up_price}:${r.down_price}:${r.rsi}:${r.atr}:${r.ready ? 1 : 0}`).join(',')].join('#');
  if (sig !== aiSig) {
    aiSig = sig;
    if (grid) grid.innerHTML = allRows.map(r => aiRowHtml(r, board)).join('');
  }
  tickAiAge();
}

/* 1 Hz: the payload is only re-pushed when the numbers change, so the age line
   keeps ticking locally in between */
function tickAiAge() {
  const el = $('ai-updated');
  if (!el || !aiBoard) return;
  const ago = Math.max(0, Math.round(Date.now() / 1000 - Number(aiBoard.updated || 0)));
  el.textContent = `${t('aiUpdated')} ${ago}s · ${aiBoard.source === 'mt5' ? t('aiClosed') : t('aiNoTerminal')}`;
}

function bindAiMarketUI() {
  const sel = $('ai-market');
  if (sel && !sel.dataset.bound) {
    sel.dataset.bound = '1';
    sel.addEventListener('change', async () => {
      const sym = String(sel.value || '');
      if (!sym) return;
      sel.disabled = true;
      const j = await sessPost('/api/bot5/config', { ai_market: sym });
      sel.disabled = false;
      if (j && j.bot5) {
        if (S) S.bot5 = j.bot5;
        aiSig = '';                         // force every card and target to redraw
        renderAiMarket({ bot5: j.bot5 });
      }
    });
  }
  const tabs = $('ai-tf-tabs');
  if (tabs && !tabs.dataset.bound) {
    tabs.dataset.bound = '1';
    tabs.addEventListener('click', async (ev) => {
      const el = ev.target && ev.target.closest ? ev.target.closest('.ai-tab') : null;
      if (!el) return;
      const key = String(el.dataset.tf || '');
      if (!key || (aiBoard && aiBoard.timeframe === key)) return;
      const j = await sessPost('/api/bot5/config', { ai_timeframe: key });
      if (j && j.bot5) {
        if (S) S.bot5 = j.bot5;
        aiSig = '';
        renderAiMarket({ bot5: j.bot5 });
      }
    });
  }
}

/* ==========================================================================
   SIGNAL PIPELINE — one market drives the whole vertical flow.

   The market box at the top owns the flow: picking a market writes it to the
   news engine (ai_market + session_market, which feed the live dial, the AI
   board and the session board) and to the extreme planner (bot 3). Every stage
   below then reports on that same market, and the wires between the stages
   carry the live numbers of the stage they leave.
   ========================================================================== */
let flowPick = '';          // optimistic selection while the backend round-trips

function flowBoard() { return (S && S.bot5 && S.bot5.ai) || null; }

/* The market the whole pipeline follows: the user's pick first, then whatever
   the AI board reports back, then the session board, then the traded market. */
function flowSymbol() {
  const board = flowBoard();
  const backend = board && board.symbol ? String(board.symbol) : '';
  if (flowPick && backend && backend.toUpperCase() === flowPick.toUpperCase()) flowPick = '';
  const sess = (S && S.bot5 && S.bot5.sessions) || null;
  return String(flowPick || backend || (sess && sess.symbol) || '').trim();
}

/* Buyer / seller pressure of the selected market: the dial row first (it moves
   ~20x per second), the AI reading second, a neutral 50/50 last. */
function flowPressure(sym) {
  const w = findWatchForSymbol(sym) || {};
  const board = flowBoard() || {};
  const flow = board.flow || {};
  const rawBuyer = w.buyer_pct !== undefined ? w.buyer_pct : flow.buyer_pct;
  const rawSeller = w.seller_pct !== undefined ? w.seller_pct : flow.seller_pct;
  const buyer = Number(rawBuyer);
  const seller = Number(rawSeller);
  return {
    buyer: Number.isFinite(buyer) ? buyer : 50,
    seller: Number.isFinite(seller) ? seller : (Number.isFinite(buyer) ? 100 - buyer : 50),
    dominant: String(w.dominant || flow.dominant || 'NEUTRAL').toUpperCase(),
  };
}

function flowTag(id, text) {
  const el = $(id);
  if (!el) return;
  const b = el.querySelector('b');
  if (b && b.textContent !== text) b.textContent = text;
}

/* The one user action of the pipeline: pick the market everything follows. */
async function selectFlowMarket(sym) {
  sym = String(sym || '').trim();
  if (!sym) return;
  flowPick = sym;

  // 1. Synchronize all active bots' selected symbol state
  if (typeof _botSelectedSymbols !== 'undefined') {
    _botSelectedSymbols.bot1 = sym;
    _botSelectedSymbols.bot2 = sym;
    _botSelectedSymbols.bot3 = sym;
    _botSelectedSymbols.bot4 = sym;
    _botSelectedSymbols.bot5 = sym;
    _botSelectedSymbols.bot6 = sym;
    _botSelectedSymbols.bot7 = sym;
  }

  // 2. Synchronize Live Curves across stages
  if (typeof setBotCurveSymbol === 'function') {
    setBotCurveSymbol('flow', sym);
    setBotCurveSymbol('bot1', sym);
    setBotCurveSymbol('bot2', sym);
    setBotCurveSymbol('bot3', sym);
    setBotCurveSymbol('bot4', sym);
    setBotCurveSymbol('bot5', sym);
  }

  // 3. Synchronize the Exness MT5 chart with the selected market.
  // The chart resolves broker suffixes itself (XAUUSD247m -> XAUUSDm, ...).
  const activeTerm = window.SoftradeXTerminal || window.BinanceTerminal;
  if (activeTerm && typeof activeTerm.switchMarket === 'function') {
    const target = typeof activeTerm.resolveSymbol === 'function'
      ? activeTerm.resolveSymbol(sym)
      : '';
    if (target) activeTerm.switchMarket(target);
  }

  // 3b. Synchronize Traders Order Flow Terminal directly with Market 01.
  // force=true: hard reset — the tape is wiped and rebuilt for THIS market
  // only (nothing from any previously selected market can leak through).
  if (window.TradersTerminal && typeof window.TradersTerminal.switchMarket === 'function') {
    window.TradersTerminal.switchMarket(sym, true);
  }

  // 4. Synchronize market select dropdowns in Sessions and AI board
  const sessSel = $('sessions-market');
  if (sessSel) {
    if (!Array.from(sessSel.options).some(o => o.value === sym)) {
      sessSel.innerHTML += `<option value="${esc(sym)}">${esc(sym)}</option>`;
    }
    sessSel.value = sym;
  }
  const aiSel = $('ai-market');
  if (aiSel) {
    if (!Array.from(aiSel.options).some(o => o.value === sym)) {
      aiSel.innerHTML += `<option value="${esc(sym)}">${esc(sym)}</option>`;
    }
    aiSel.value = sym;
  }

  // 5. Instantly refresh every stage that follows Market 01
  renderFlowStage(S || {});
  renderMarketWatches();          // 02 · Market Pressure Watch Dials
  updateAllMarketChips();
  void pollFlowBalanceWorld();    // the flow strip follows the selected market
  if (typeof renderAiMarket === 'function') renderAiMarket(S || {});   // 06 · Full AI analysis
  if (typeof renderSessions === 'function') renderSessions(S || {});   // 08 · Sessions
  if (typeof renderBot5 === 'function') renderBot5(S || {});           // 05 · Next event + 03 · AI direction

  // 6. Update backend configurations
  try {
    const rets = await Promise.all([
      sessPost('/api/bot5/config', { ai_market: sym, session_market: sym }),
      sessPost('/api/bot3/config', { symbol: sym }),
    ]);
    if (S) {
      if (rets[0] && rets[0].bot5) S.bot5 = rets[0].bot5;
      if (rets[1] && rets[1].bot3) S.bot3 = rets[1].bot3;
    }
  } catch (e) {}
  // the backend recalculated for the selected market — repaint every stage again
  if (S) {
    renderFlowStage(S);
    renderMarketWatches();
    if (typeof renderAiMarket === 'function') renderAiMarket(S);
    if (typeof renderSessions === 'function') renderSessions(S);
    if (typeof renderBot5 === 'function') renderBot5(S);
    if (typeof renderBot3 === 'function') renderBot3(S);    // 07 · Next setups
  }
  if (wsAlive) { try { ws.send(JSON.stringify({ type: 'refresh' })); } catch (e) {} }
}

function renderFlowStage(d) {
  if (!$('flow-pipeline')) return;
  d = d || {};
  const board = flowBoard() || {};
  const sym = flowSymbol();
  const w = findWatchForSymbol(sym) || {};
  const digits = Number(w.digits !== undefined ? w.digits : (board.digits || 2));
  const f = flowPressure(sym);

  // ---- market chips: comprehensive list of all markets
  const DEFAULT_MARKET_LIST = [
    'XAUUSDm', 'BTCUSDTm', 'ETHUSDTm', 'EURUSDm', 'GBPUSDm',
    'USDJPYm', 'AUDUSDm', 'USDCADm', 'USDCHFm', 'USOILm', 'USTECm', 'US30m'
  ];
  let syms = [];
  if (Array.isArray(board.symbols) && board.symbols.length) {
    syms = board.symbols.slice();
  }
  if (marketWatches && marketWatches.length) {
    marketWatches.forEach(wItem => {
      if (wItem.symbol && !syms.includes(wItem.symbol)) syms.push(wItem.symbol);
    });
  }
  DEFAULT_MARKET_LIST.forEach(dm => {
    if (!syms.includes(dm) && !syms.includes(dm.replace(/m$/, ''))) syms.push(dm);
  });
  if (sym && !syms.includes(sym)) syms.unshift(sym);

  // the market picker is the chip row below (there is no <select> element)
  const chips = $('flow-market-chips');
  if (chips) {
    const top = syms.slice(0, 12);
    const csig = top.join('|') + '#' + sym;
    if (chips.dataset.sig !== csig) {
      chips.dataset.sig = csig;
      chips.innerHTML = top.map(s => `<button type="button" class="mh-chip${s === sym ? ' on' : ''}" data-sym="${esc(s)}">${esc(String(s).replace(/m$/, ''))}</button>`).join('');
    }
  }

  // ---- the live price block
  const price = Number(w.ask || w.bid || board.price || 0);
  setCell($('flow-price'), price > 0 ? nf(price, digits) : '—');
  setCell($('flow-symbol-label'), String(w.displayName || sym || '—').toUpperCase());
  const chgEl = $('flow-change');
  if (chgEl) {
    const dir = String(w.direction || '').toUpperCase();
    const chg = Number(w.change_pct || 0);
    const arrow = dir === 'UP' ? '▲' : (dir === 'DOWN' ? '▼' : '►');
    const txt = price > 0
      ? `${arrow} ${dir || 'FLAT'}${chg ? ` · ${chg > 0 ? '+' : ''}${chg.toFixed(2)}%` : ''}`
      : '—';
    if (chgEl.textContent !== txt) chgEl.textContent = txt;
    chgEl.className = 'mh-change' + (chg > 0 ? ' pos' : (chg < 0 ? ' neg' : ''));
  }

  // ---- the AI answer of the selected market + its live pressure
  const comb = board.combined || {};
  const verdict = String(comb.verdict || '').toUpperCase();
  const sigEl = $('flow-signal');
  if (sigEl) {
    const txt = `${aiArrow(verdict)} ${aiSideTxt(verdict)}${comb.confidence ? ` ${Number(comb.confidence)}%` : ''}`;
    if (sigEl.textContent !== txt) sigEl.textContent = txt;
    const wantCls = `mh-signal ${aiSide(verdict)}`;
    if (sigEl.className !== wantCls) sigEl.className = wantCls;
  }
  setCell($('flow-signal-sub'), String((comb.reasons || [])[0] || t('flowSignalSub')));
  const barEl = $('flow-bar-buy');
  if (barEl) barEl.style.width = `${Math.max(2, Math.min(98, f.buyer))}%`;
  setCell($('flow-buy-pct'), `${f.buyer.toFixed(0)}%`);
  setCell($('flow-sell-pct'), `${f.seller.toFixed(0)}%`);

  // ---- quotes
  const d2 = digits > 4 ? 5 : digits;
  setCell($('flow-bid'), w.bid ? nf(w.bid, d2) : '—');
  setCell($('flow-ask'), w.ask ? nf(w.ask, d2) : '—');
  setCell($('flow-spread'), (w.spread !== undefined && w.spread !== null) ? String(w.spread) : '—');

  // ---- market state pill
  const st = $('flow-market-status');
  if (st) {
    let cls = 'mh-status';
    let txt;
    if (!sym) { cls = 'mh-status muted'; txt = '—'; }
    else if (price <= 0) { cls = 'mh-status muted'; txt = t('flowNoQuote'); }
    else if (w.market_open === false) { cls = 'mh-status off'; txt = t('flowMarketClosed'); }
    else { txt = t('flowMarketOpen'); }
    if (st.textContent !== txt) st.textContent = txt;
    if (st.className !== cls) st.className = cls;
  }

  // ---- the wires between the stages carry the live numbers
  const hist = _priceHistory[getHistoryKey(sym)] || [];
  flowTag('flow-tag-dial', `${sym || '—'} → NEXT EVENT · LIVE`);
  flowTag('flow-tag-setups', `BUYER ${f.buyer.toFixed(0)}% / SELLER ${f.seller.toFixed(0)}%`);
  const planned = ((d.bot3 || {}).planned_trades || []).length;
  const nowSec0 = Date.now() / 1000;
  const news24 = ((d.bot5 || {}).news || []).filter(n => { const ts = Number(n.ts || 0); return ts > nowSec0 && ts <= nowSec0 + 86400; }).length;
  flowTag('flow-tag-news', news24 ? `NEXT EVENT → DIAL · ${news24} IN 24H` : 'NEXT EVENT → DIAL · WATCHING');
  flowTag('flow-tag-curve', `${sym || '—'} · ${hist.length} TICKS · ${String(w.direction || '').toUpperCase() || 'FLAT'}`);
  const cdEl = $('watch-countdown');
  const evEl = $('watch-event-title');
  const cdTxt = cdEl ? String(cdEl.textContent || '').trim() : '';
  const evTxt = evEl ? String(evEl.textContent || '').trim() : '';
  flowTag('flow-tag-session', (cdTxt && evTxt && cdTxt !== '—') ? `${cdTxt} · ${evTxt.slice(0, 32)}` : t('bot5NoNews'));
  const sessBoard = (d.bot5 || {}).sessions || {};
  const running = (sessBoard.sessions || []).filter(r => r.state === 'open');
  flowTag('flow-tag-ai', running.length
    ? `${running.map(r => String(t(SESSION_LABEL[r.key] || '') || r.name_en || r.key).toUpperCase()).join(' + ')} OPEN`
    : t('flowNoSession'));
  const outlook = (d.bot5 || {}).outlook || [];
  const oRow = outlook.find(o => String(o.market || '').toUpperCase() === sym.toUpperCase()) || null;
  flowTag('flow-tag-outlook', (verdict || oRow)
    ? `AI ${verdict || '—'} ${Number(comb.confidence || 0)}% → 24H ${oRow ? `${String(oRow.verdict || '').toUpperCase()} ${Number(oRow.confidence || 0)}%` : '—'}`
    : 'AI → 24H');
  const hcEl = $('hist-count');
  flowTag('flow-tag-history', `${hcEl ? String(hcEl.textContent || '0') : '0'} ${t('flowClosedTrades')}`);

  // ---- the curve headline
  setCell($('flow-curve-sub'), `${sym || '—'} · ${hist.length} ticks`);

  // ---- rail badges
  const posEl = $('rail-pos-count');
  if (posEl) {
    const n = (d.positions || []).length;
    setCell(posEl, String(n));
    posEl.classList.toggle('hot', n > 0);
    const tpEl = $('tab-badge-pos');
    if (tpEl) setCell(tpEl, String(n));
  }
  const botEl = $('rail-bot-count');
  if (botEl) {
    const n = ['bot', 'bot2', 'bot3', 'bot4', 'bot5', 'bot6', 'bot7']
      .filter(k => d[k] && d[k].running).length;
    setCell(botEl, `${n}/7`);
    botEl.classList.toggle('hot', n > 0);
    const tbEl = $('tab-badge-bots');
    if (tbEl) setCell(tbEl, `${n}/7`);
  }
}

/* Ultra-lightweight in-place price patch for high frequency 30-100ms watch frames (no reflow). */
function updateFlowStageLivePrices() {
  const sym = flowSymbol();
  if (!sym) return;
  const w = findWatchForSymbol(sym);
  if (!w) return;
  const digits = Number(w.digits !== undefined ? w.digits : 2);
  const price = Number(w.ask || w.bid || 0);
  if (price > 0) setCell($('flow-price'), nf(price, digits));
  if (window.TradersTerminal && typeof window.TradersTerminal.onPriceTick === 'function') {
    window.TradersTerminal.onPriceTick(sym, price, w.bid, w.ask, S);
  }
  const chgEl = $('flow-change');
  if (chgEl) {
    const dir = String(w.direction || '').toUpperCase();
    const chg = Number(w.change_pct || 0);
    const arrow = dir === 'UP' ? '▲' : (dir === 'DOWN' ? '▼' : '►');
    const txt = price > 0
      ? `${arrow} ${dir || 'FLAT'}${chg ? ` · ${chg > 0 ? '+' : ''}${chg.toFixed(2)}%` : ''}`
      : '—';
    if (chgEl.textContent !== txt) chgEl.textContent = txt;
    chgEl.className = 'mh-change' + (chg > 0 ? ' pos' : (chg < 0 ? ' neg' : ''));
  }
  const f = flowPressure(sym);
  const barEl = $('flow-bar-buy');
  if (barEl) barEl.style.width = `${Math.max(2, Math.min(98, f.buyer))}%`;
  setCell($('flow-buy-pct'), `${f.buyer.toFixed(0)}%`);
  setCell($('flow-sell-pct'), `${f.seller.toFixed(0)}%`);
  const d2 = digits > 4 ? 5 : digits;
  if (w.bid) setCell($('flow-bid'), nf(w.bid, d2));
  if (w.ask) setCell($('flow-ask'), nf(w.ask, d2));
  if (w.spread !== undefined && w.spread !== null) setCell($('flow-spread'), String(w.spread));
  flowTag('flow-tag-setups', `BUYER ${f.buyer.toFixed(0)}% / SELLER ${f.seller.toFixed(0)}%`);
}

/* ----------------------------------------------------- the side drawers */
function closeFlowDrawers() {
  const dash = $('dash');
  if (!dash) return;
  dash.classList.remove('drawer-left-open', 'drawer-right-open');
  const scrim = $('drawer-scrim');
  if (scrim) scrim.hidden = true;
  document.body.classList.remove('no-scroll');
}

function initFlowUI() {
  const dash = $('dash');
  const scrim = $('drawer-scrim');
  const switchLeftDrawerTab = (tabName) => {
    const isPos = tabName === 'positions';
    const posTab = $('tab-btn-positions');
    const histTab = $('tab-btn-history');
    const posView = $('view-positions');
    const histView = $('view-history');
    if (posTab) posTab.classList.toggle('active', isPos);
    if (histTab) histTab.classList.toggle('active', !isPos);
    if (posView) posView.classList.toggle('active', isPos);
    if (histView) histView.classList.toggle('active', !isPos);
  };
  window.__switchLeftDrawerTab = switchLeftDrawerTab;

  const switchRightDrawerTab = (tabName) => {
    const isBots = tabName === 'bots';
    const botsTab = $('tab-btn-bots');
    const actTab = $('tab-btn-activity');
    const botsView = $('view-bots');
    const actView = $('view-activity');
    if (botsTab) botsTab.classList.toggle('active', isBots);
    if (actTab) actTab.classList.toggle('active', !isBots);
    if (botsView) botsView.classList.toggle('active', isBots);
    if (actView) actView.classList.toggle('active', !isBots);
  };
  window.__switchRightDrawerTab = switchRightDrawerTab;

  const setDrawer = (side, tab) => {
    if (!dash) return;
    dash.classList.toggle('drawer-left-open', side === 'left');
    dash.classList.toggle('drawer-right-open', side === 'right');
    const open = !!side;
    if (scrim) scrim.hidden = !open;
    document.body.classList.toggle('no-scroll', open);
    const lp = $('rail-positions');
    if (lp) lp.setAttribute('aria-expanded', side === 'left' ? 'true' : 'false');
    const lh = $('rail-history');
    if (lh) lh.setAttribute('aria-expanded', side === 'left' ? 'true' : 'false');
    const rb = $('rail-bots');
    if (rb) rb.setAttribute('aria-expanded', side === 'right' ? 'true' : 'false');
    const ra = $('rail-activity');
    if (ra) ra.setAttribute('aria-expanded', side === 'right' ? 'true' : 'false');
    const pd = $('positions-drawer');
    if (pd) pd.setAttribute('aria-hidden', side === 'left' ? 'false' : 'true');
    const bd = $('bots-drawer');
    if (bd) bd.setAttribute('aria-hidden', side === 'right' ? 'false' : 'true');
    // the drawer was paused while shut - paint it with the newest engine state
    if (side === 'right' && S) renderBotsPanels(S);

    if (side === 'left' && tab) {
      switchLeftDrawerTab(tab);
    }
    if (side === 'right' && tab) {
      switchRightDrawerTab(tab);
    }
  };
  window.__setFlowDrawer = setDrawer;

  const toggle = (side, tab) => {
    const isOpen = !!dash && dash.classList.contains(side === 'left' ? 'drawer-left-open' : 'drawer-right-open');
    playTactileClick('click');
    if (isOpen) {
      if (side === 'left' && tab) {
        const isCurrentlyPos = $('tab-btn-positions')?.classList.contains('active');
        const currentTab = isCurrentlyPos ? 'positions' : 'history';
        if (tab !== currentTab) {
          switchLeftDrawerTab(tab);
          return;
        }
      }
      if (side === 'right' && tab) {
        const isCurrentlyBots = $('tab-btn-bots')?.classList.contains('active');
        const currentTab = isCurrentlyBots ? 'bots' : 'activity';
        if (tab !== currentTab) {
          switchRightDrawerTab(tab);
          return;
        }
      }
      setDrawer(null);
    } else {
      setDrawer(side, tab || (side === 'left' ? 'positions' : 'bots'));
    }
  };

  const posBtn = $('rail-positions');
  if (posBtn && !posBtn.dataset.bound) {
    posBtn.dataset.bound = '1';
    posBtn.addEventListener('click', () => toggle('left', 'positions'));
  }
  const histBtn = $('rail-history');
  if (histBtn && !histBtn.dataset.bound) {
    histBtn.dataset.bound = '1';
    histBtn.addEventListener('click', () => toggle('left', 'history'));
  }
  const tabPos = $('tab-btn-positions');
  if (tabPos && !tabPos.dataset.bound) {
    tabPos.dataset.bound = '1';
    tabPos.addEventListener('click', () => switchLeftDrawerTab('positions'));
  }
  const tabHist = $('tab-btn-history');
  if (tabHist && !tabHist.dataset.bound) {
    tabHist.dataset.bound = '1';
    tabHist.addEventListener('click', () => switchLeftDrawerTab('history'));
  }
  const botBtn = $('rail-bots');
  if (botBtn && !botBtn.dataset.bound) {
    botBtn.dataset.bound = '1';
    botBtn.addEventListener('click', () => toggle('right', 'bots'));
  }
  const actBtn = $('rail-activity');
  if (actBtn && !actBtn.dataset.bound) {
    actBtn.dataset.bound = '1';
    actBtn.addEventListener('click', () => toggle('right', 'activity'));
  }
  const tabBots = $('tab-btn-bots');
  if (tabBots && !tabBots.dataset.bound) {
    tabBots.dataset.bound = '1';
    tabBots.addEventListener('click', () => switchRightDrawerTab('bots'));
  }
  const tabAct = $('tab-btn-activity');
  if (tabAct && !tabAct.dataset.bound) {
    tabAct.dataset.bound = '1';
    tabAct.addEventListener('click', () => switchRightDrawerTab('activity'));
  }
  if (scrim && !scrim.dataset.bound) {
    scrim.dataset.bound = '1';
    scrim.addEventListener('click', () => setDrawer(null));
  }
  document.querySelectorAll('[data-drawer-close]').forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => setDrawer(null));
  });
  if (!window.__flowEscapeBound) {
    window.__flowEscapeBound = true;
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeFlowDrawers(); });
  }

  // Fullscreen toggle for Bots Drawer
  const fsToggle = $('bots-fs-toggle');
  if (fsToggle && !fsToggle.dataset.bound) {
    fsToggle.dataset.bound = '1';
    fsToggle.addEventListener('click', () => {
      const drawer = $('bots-drawer');
      if (!drawer) return;
      const isFs = drawer.classList.toggle('fullscreen');
      const icon = $('bots-fs-icon');
      const lbl = $('bots-fs-label');
      if (icon) icon.textContent = isFs ? '🗗' : '⛶';
      if (lbl) lbl.textContent = isFs ? 'Side View' : 'Fullscreen';
    });
  }

  // Quick Nav Tabs for Bots Drawer
  const quickNav = $('bots-quick-nav');
  if (quickNav && !quickNav.dataset.bound) {
    quickNav.dataset.bound = '1';
    quickNav.addEventListener('click', (e) => {
      const btn = e.target && e.target.closest ? e.target.closest('.b-nav-pill') : null;
      if (!btn || !btn.dataset.bot) return;
      quickNav.querySelectorAll('.b-nav-pill').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const targetId = btn.dataset.bot;
      const allPanels = document.querySelectorAll('#bots-drawer .bot-panel');
      if (targetId === 'all') {
        allPanels.forEach(p => {
          p.classList.remove('bot-highlight');
        });
        const firstPanel = document.getElementById('all-news-panel') || document.getElementById('bot1-panel');
        if (firstPanel) firstPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else {
        allPanels.forEach(p => {
          if (p.id === targetId) {
            p.classList.add('bot-highlight');
          } else {
            p.classList.remove('bot-highlight');
          }
        });
        const targetEl = document.getElementById(targetId);
        if (targetEl) {
          targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
    });
  }

  const chips = $('flow-market-chips');
  if (chips && !chips.dataset.bound) {
    chips.dataset.bound = '1';
    chips.addEventListener('click', (ev) => {
      const btn = ev.target && ev.target.closest ? ev.target.closest('.mh-chip') : null;
      if (!btn || !btn.dataset.sym) return;
      void selectFlowMarket(btn.dataset.sym);
    });
  }

  const newsFilterBar = $('news-impact-filter-bar');
  if (newsFilterBar && !newsFilterBar.dataset.bound) {
    newsFilterBar.dataset.bound = '1';
    newsFilterBar.addEventListener('click', (e) => {
      const btn = e.target && e.target.closest ? e.target.closest('.news-impact-btn') : null;
      if (!btn || !btn.dataset.impact) return;
      playTactileClick('click');
      window.__activeNewsImpactFilter = btn.dataset.impact.toLowerCase();
      newsFilterBar.querySelectorAll('.news-impact-btn').forEach(b => b.classList.toggle('active', b === btn));
      const payload = window.__lastPollData || (typeof S !== 'undefined' ? S : null);
      if (payload) {
        renderBot5(payload);
      }
    });
  }

  // ALL ECONOMIC NEWS panel: ALL / HIGH / MEDIUM / LOW filter
  const allNewsBar = $('news-impact-all-bar');
  if (allNewsBar && !allNewsBar.dataset.bound) {
    allNewsBar.dataset.bound = '1';
    allNewsBar.addEventListener('click', (e) => {
      const btn = e.target && e.target.closest ? e.target.closest('.news-impact-btn') : null;
      if (!btn || !btn.dataset.imp) return;
      playTactileClick('click');
      window.__allNewsImpactFilter = btn.dataset.imp.toLowerCase();
      allNewsBar.querySelectorAll('.news-impact-btn').forEach(b => b.classList.toggle('active', b === btn));
      const payload = window.__lastPollData || (typeof S !== 'undefined' ? S : null);
      if (payload) renderBot5(payload);   // re-filters and re-renders the list
    });
  }



  initSoftradeXTerminalUI();
  setupFlowWires();
}

function initSoftradeXTerminalUI() {
  // Handled natively by softradex-terminal.js (Pure WebSocket & REST, 0% TradingView)
}
function initBinanceTerminalUI() {
  initSoftradeXTerminalUI();
}

/* ================= LIVE DATA WIRES BETWEEN THE STAGES =================
   The pipeline stages (01 → 05 → 02 → 03 → 04 → 08 → 06 → 07 → 09) are
   joined with glowing circuit wires that CARRY the live numbers: every
   wire tag (BUYER %, TICKS, countdowns, verdicts) is updated by the same
   feeds that drive the cards. Orthogonal, rounded, PCB-style routing,
   measured from the real layout, so the wires survive every breakpoint. */
const FLOW_LINK_ROUTES = [
  ['flow-tag-dial',    'stage-market',   'stage-news'],
  ['flow-tag-news',    'stage-news',     'stage-dial'],
  ['flow-tag-outlook', 'stage-dial',     'stage-outlook'],
  ['flow-tag-curve',   'stage-outlook',  'stage-curve'],
  ['flow-tag-session', 'stage-curve',    'stage-sessions'],
  ['flow-tag-ai',      'stage-sessions', 'stage-ai'],
  ['flow-tag-setups',  'stage-ai',       'stage-setups'],
];
let _flowWiresRAF = 0;

function scheduleFlowWires() {
  return;
}

function updateFlowWires() {
  return;
}

function setupFlowWires() {
  document.body.classList.remove('wires-on');
}

function tickBot5Countdown() {
  const now = Date.now() / 1000;
  const el = $('bot5-countdown');
  const wEl = $('watch-countdown');
  const b5 = S && S.bot5;
  const ev = window.__currentNewsCardEvent || (b5 && (b5.active_event || (Array.isArray(b5.news) ? (b5.news.find(n => !n.past && (Number(n.ts || 0) >= (now - 60))) || b5.news[0]) : null)));
  if (el || wEl) {
    if (!ev || !ev.ts) {
      if (el) el.textContent = '—';
      if (wEl) wEl.textContent = '—';
    } else {
      const secs = Math.round(Number(ev.ts) - now);
      if (secs <= 0) {
        const liveTxt = '⏱ ' + (LANG === 'bn' ? 'লাইভ' : 'LIVE');
        if (el) el.textContent = liveTxt;
        if (wEl) {
          wEl.textContent = liveTxt;
          wEl.className = 'news-countdown soon';
        }
        const whenTxt = LANG === 'bn' ? 'লাইভ চলছে' : 'LIVE NOW';
        const nw1 = $('bot5-event-meta');
        if (nw1) {
          const wSpan = nw1.querySelector('.news-when');
          if (wSpan && wSpan.textContent !== whenTxt) wSpan.textContent = whenTxt;
        }
        const nw2 = $('watch-event-meta');
        if (nw2) {
          const wSpan = nw2.querySelector('.news-when');
          if (wSpan && wSpan.textContent !== whenTxt) wSpan.textContent = whenTxt;
        }
        // Once event starts/passes, auto-refresh to advance to the next event
        // (throttled: a 1 Hz refresh for the whole post window spams the socket)
        if (wsAlive && secs <= -2) {
          const tnow = Date.now();
          if (tnow - (_bot5LiveRefreshAt || 0) > 5000) {
            _bot5LiveRefreshAt = tnow;
            try { ws.send(JSON.stringify({ type: 'refresh' })); } catch (e) {}
          }
        }
      } else {
        const m = Math.floor(secs / 60), s = secs % 60;
        const countTxt = m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
        const countCls = 'news-countdown' + (secs <= 300 ? ' soon' : '');
        if (el) {
          el.textContent = countTxt;
          el.className = countCls;
        }
        if (wEl) {
          wEl.textContent = countTxt;
          wEl.className = countCls;
        }
        const mins = Math.round(secs / 60);
        const whenTxt = mins >= 0
          ? (LANG === 'bn' ? `আর ${fmtCountdown(mins)}` : `in ${fmtCountdown(mins)}`)
          : (LANG === 'bn' ? `${fmtCountdown(-mins)} আগে` : `${fmtCountdown(-mins)} ago`);
        const nw1 = $('bot5-event-meta');
        if (nw1) {
          const wSpan = nw1.querySelector('.news-when');
          if (wSpan && wSpan.textContent !== whenTxt) wSpan.textContent = whenTxt;
        }
        const nw2 = $('watch-event-meta');
        if (nw2) {
          const wSpan = nw2.querySelector('.news-when');
          if (wSpan && wSpan.textContent !== whenTxt) wSpan.textContent = whenTxt;
        }
      }
    }
  }

  // "previous news show korbe na. jeta sesh ota chole jeba" -> 
  // Live auto-removal of finished news from DOM immediately every second
  const rows = document.querySelectorAll('#bot5-news .news-row');
  if (rows && rows.length) {
    let removed = false;
    rows.forEach((row) => {
      const ts = Number(row.dataset.ts || 0);
      if (ts && ts <= now) {
        row.remove();
        removed = true;
      }
    });
    if (removed && !document.querySelector('#bot5-news .news-row')) {
      const newsEl = $('bot5-news');
      if (newsEl) {
        newsEl.innerHTML = `<div class="news-empty">${LANG === 'bn' ? 'ক্যালেন্ডারে কোনো আসন্ন ইভেন্ট নেই' : 'No upcoming events on the calendar'}</div>`;
      }
    }
    // the live list is a rolling window: ask the engine for fresh rows when the
    // next event is close, so newly expired rows are replaced by newer ones
    if (removed && wsAlive && !_bot5NewsRefreshPending) {
      _bot5NewsRefreshPending = true;
      setTimeout(() => {
        _bot5NewsRefreshPending = false;
        try { ws.send(JSON.stringify({ type: 'refresh' })); } catch (e) {}
      }, 1500);
    }
  }
}
let _bot5NewsRefreshPending = false;

function renderBot5Manual() {
  // Manual event creation removed per user request
}

async function bot5Post(path, body) {
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await res.json();
    if (!res.ok) {
      const msg = (j.error && j.error.message) || j.detail || 'Request failed';
      toast('error', msg);
      return j;
    }
    if (j.bot5) { if (S) { S.bot5 = j.bot5; renderBot5(S); } }
    return j;
  } catch (e) { toast('error', String(e)); return {}; }
}

function bindBot5UI() {
  const b5Start = $('bot5-start');
  if (b5Start) {
    b5Start.addEventListener('click', async () => {
      const sym = (S && S.bot5 && S.bot5.symbol) || 'XAUUSDm';
      if (!isMarketOpen(sym)) {
        toast('error', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ থাকায় বট চালু করা যাবে না` : `Cannot start bot: ${sym} market is currently closed`);
        return;
      }
      const lot = Number($('bot5-lot').value || 0.01);
      const mode = $('bot5-mode').value || 'anticipate';
      const impact = $('bot5-impact').value || 'high';
      const pre = Math.max(0, parseInt($('bot5-pre').value || 5, 10));
      const post = Math.max(1, parseInt($('bot5-post').value || 15, 10));
      const ok = window.confirm(
        LANG === 'bn'
          ? `নিউজ ট্রেডার বট চালু করবেন?\n\nলট: ${lot}\nস্টাইল: ${mode}\nইমপ্যাক্ট: ${impact}\nপ্রি: ${pre} মিনিট · পোস্ট: ${post} মিনিট`
          : `Start News Trader Bot?\n\nLot: ${lot}\nStyle: ${mode}\nImpact: ${impact}\nPre: ${pre} min · Post: ${post} min`
      );
      if (!ok) return;
      const res = await bot5Post('/api/bot5/start', { lot, mode, min_impact: impact, pre_minutes: pre, post_minutes: post });
      if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ৫ (নিউজ ট্রেডার) চালু হয়েছে' : 'Bot 5 (News Trader) Started');
    });
  }

  const b5Stop = $('bot5-stop');
  if (b5Stop) {
    b5Stop.addEventListener('click', async () => {
      const res = await bot5Post('/api/bot5/stop', {});
      if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ৫ বন্ধ করা হলো' : 'Bot 5 Stopped');
    });
  }

  const b5Close = $('bot5-close');
  if (b5Close) {
    b5Close.addEventListener('click', async () => {
      if (!window.confirm(LANG === 'bn' ? 'বট ৫ এর সব খোলা ট্রেড এখনই ক্লোজ করবেন?' : 'Close all Bot 5 open positions right now?')) return;
      const res = await bot5Post('/api/bot5/close', {});
      if (res && res.ok) toast('ok', `${LANG === 'bn' ? 'ক্লোজ করা ট্রেড' : 'Closed positions'} (${res.closed || 0})`);
    });
  }

  const b5Refresh = $('bot5-refresh');
  if (b5Refresh) {
    b5Refresh.addEventListener('click', async () => {
      toast('ok', LANG === 'bn' ? 'ক্যালেন্ডার আনা হচ্ছে…' : 'Fetching calendar…');
      const res = await bot5Post('/api/bot5/refresh', {});
      if (res && res.ok) {
        toast('ok', LANG === 'bn' ? `ক্যালেন্ডার: ${res.count || 0} ইভেন্ট` : `Calendar: ${res.count || 0} events`);
        if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
      }
    });
  }

  const b5Mode = $('bot5-mode');
  if (b5Mode) b5Mode.addEventListener('change', () => { void bot5Post('/api/bot5/config', { mode: b5Mode.value }); });
  const b5Impact = $('bot5-impact');
  if (b5Impact) b5Impact.addEventListener('change', () => { void bot5Post('/api/bot5/config', { min_impact: b5Impact.value }); });
  const b5Lot = $('bot5-lot');
  if (b5Lot) b5Lot.addEventListener('change', () => { void bot5Post('/api/bot5/config', { lot: Number(b5Lot.value || 0.01) }); });
  const b5MaxTrades = $('bot5-max-trades');
  if (b5MaxTrades) b5MaxTrades.addEventListener('change', () => { void bot5Post('/api/bot5/config', { max_trades: Math.max(1, parseInt(b5MaxTrades.value || 1, 10)) }); });
  const b5Pre = $('bot5-pre');
  if (b5Pre) b5Pre.addEventListener('change', () => { void bot5Post('/api/bot5/config', { pre_minutes: Math.max(0, parseInt(b5Pre.value || 5, 10)) }); });
  const b5PostEl = $('bot5-post');
  if (b5PostEl) b5PostEl.addEventListener('change', () => { void bot5Post('/api/bot5/config', { post_minutes: Math.max(1, parseInt(b5PostEl.value || 15, 10)) }); });
  const b5Tp = $('bot5-tp');
  if (b5Tp) b5Tp.addEventListener('change', () => { void bot5Post('/api/bot5/config', { tp: Math.max(0, Number(b5Tp.value || 3.0)) }); });
  const b5Sl = $('bot5-sl');
  if (b5Sl) b5Sl.addEventListener('change', () => { void bot5Post('/api/bot5/config', { sl: Math.max(0, Number(b5Sl.value || 3.0)) }); });

  const b5Chips = $('bot5-market-chips');
  if (b5Chips) {
    b5Chips.addEventListener('click', async (e) => {
      const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
      if (!chip || !chip.dataset.sym) return;
      const sym = chip.dataset.sym;
      if (chip.classList.contains('market-off') || !isMarketOpen(sym)) {
        toast('warn', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)` : `${sym} market is currently closed`);
        return;
      }
      document.querySelectorAll('#bot5-market-chips .chip')
        .forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
      if ($('bot5-symbol')) $('bot5-symbol').textContent = sym;
      if ($('bot5-symbol-name')) $('bot5-symbol-name').textContent = sym;
      b5Chips.dataset.n = '';
      setBotCurveSymbol('bot5', sym);
      const res = await bot5Post('/api/bot5/config', { symbol: sym });
      if (res && res.ok) {
        if (res.bot5) {
          if (S) S.bot5 = res.bot5;
          renderBot5(S);
        }
        toast('ok', `${t('botMarketSwitched')}: ${sym}`);
        if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
      }
    });
  }
}

/* ----------------------------------------------------------- voice alerts & feed */
function hashStr(str) {
  let hash = 0;
  const s = String(str || '');
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash) + s.charCodeAt(i);
    hash |= 0;
  }
  return hash;
}

/* ------------------------------------------ bot 6 (pre-trade entry) */
function renderWatchBox(elId, w, minPct, isBn) {
  const el = $(elId);
  if (!el) return;
  const buyer = Number(w.buyer_pct || 0);
  const seller = Number(w.seller_pct || 0);
  const dom = String(w.dominant || 'NEUTRAL').toUpperCase();
  if (!buyer && !seller) {
    el.innerHTML = `<div class="lr-empty">${isBn ? 'ওয়াচ ডায়ালে এখনো ডাটা আসেনি — নিরাপদে ট্রেড বন্ধ' : 'No dial data yet - trading is safely blocked'}</div>`;
    return;
  }
  const okBuy = buyer >= minPct;
  const okSell = seller >= minPct;
  const bar = (lbl, val, ok, cls) => `
    <div class="ladder-row ${ok ? 'filled' : ''}">
      <span class="lr-no">${lbl}</span>
      <span class="lr-price">${nf(val, 1)}%</span>
      <span class="lr-lot">${ok ? (isBn ? 'পাস' : 'PASS') : '< ' + nf(minPct, 0) + '%'}</span>
      <span class="lr-state ${ok ? 'st-filled' : 'st-cancelled'}">${ok ? '✔' : '✘'}</span>
    </div>`;
  const reason = String(w.reason || '');
  el.innerHTML = bar('BUY', buyer, okBuy) + bar('SELL', seller, okSell)
    + `<div class="lr-empty">${isBn ? 'থ্রেশহোল্ড' : 'threshold'} ${nf(minPct, 0)}% · ${esc(dom)}`
    + (reason ? ` — ${esc(reason)}` : '') + `</div>`;
}

function renderBot6(d) {
  const b = d.bot6 || {};
  const cfg = b.config || {};
  const isBn = LANG === 'bn';
  const running = !!b.running;
  const live = !!d.connected;
  const chip = $('bot6-chip');
  if (chip) {
    chip.className = 'bot-chip ' + (!live ? 'offline' : running ? 'running' : 'stopped');
    chip.textContent = !live ? t('botOffline') : running ? t('botRunning') : t('botStopped');
  }
  $('bot6-start').classList.toggle('active', running);
  $('bot6-stop').classList.toggle('active', !running && live);

  const wanted = b.symbol_requested || cfg.symbol || 'XAUUSDm';
  const sym = b.symbol || '';
  const symOk = !!b.symbol_ok && sym;
  const shown = symOk ? sym : wanted;
  $('bot6-symbol').textContent = shown;
  const wpill = $('bot6-watch-pill');
  const w = b.watch || {};
  if (wpill) {
    const dom = String(w.dominant || 'NEUTRAL').toUpperCase();
    wpill.textContent = w.allowed
      ? `${dom} ${nf(w.buyer_pct || 0, 0)}% / ${nf(w.seller_pct || 0, 0)}%`
      : `WATCH ${dom}`;
    wpill.style.color = w.allowed ? 'var(--green)' : '';
  }

  let priceStr = '';
  if (b.ask && b.bid) {
    priceStr = ` · Ask <b>${fmtPrice(b.ask)}</b> / Bid <b>${fmtPrice(b.bid)}</b>`;
  }
  const sn = $('bot6-symbol-name');
  if (sn) {
    sn.innerHTML = `${esc(shown)}${priceStr}`;
    sn.style.color = symOk ? 'var(--accent)' : '#fca5a5';
  }

  const pick = $('bot6-market-chips');
  if (pick) {
    const options = (b.symbol_options && b.symbol_options.length ? b.symbol_options : []);
    const list = options.length ? options.slice() : [shown];
    if (!list.includes(shown)) list.unshift(shown);
    if (live) {
      const sig = list.join(',') + '|' + shown;
      if (pick.dataset.n !== sig) {
        pick.dataset.n = sig;
        pick.innerHTML = list.map((s) =>
          `<button type="button" class="chip${s === shown ? ' active' : ''}" data-sym="${esc(s)}">${esc(s)}</button>`).join('');
      }
    } else {
      pick.querySelectorAll('.chip').forEach(c => c.classList.toggle('active', c.dataset.sym === shown));
    }
  }
  setBotCurveSymbol('bot6', shown);

  const setVal = (id, v) => {
    const el = $(id);
    if (el && document.activeElement !== el && v !== undefined && v !== null) el.value = v;
  };
  setVal('bot6-lot', cfg.lot);
  setVal('bot6-distance', cfg.distance);
  setVal('bot6-tp', cfg.tp);
  setVal('bot6-sl', cfg.sl);
  setVal('bot6-pressure', cfg.watch_min_pct);
  const sideEl = $('bot6-side');
  if (sideEl && document.activeElement !== sideEl && cfg.side) sideEl.value = cfg.side;

  const gateOn = !!cfg.watch_gate;
  const gb = $('bot6-watch-gate');
  if (gb) gb.classList.toggle('active', gateOn);
  const gs = $('bot6-watch-gate-status');
  if (gs) {
    gs.textContent = gateOn ? 'ON' : 'OFF';
    gs.className = 'trend-badge ' + (gateOn ? 'active' : 'deactive');
  }
  const aiOn = cfg.ai_gate !== false;
  const ab = $('bot6-ai-gate');
  if (ab) ab.classList.toggle('active', aiOn);
  const as = $('bot6-ai-gate-status');
  if (as) {
    as.textContent = aiOn ? 'ON' : 'OFF';
    as.className = 'trend-badge ' + (aiOn ? 'active' : 'deactive');
  }

  renderPriceMap('bot6', b);

  const entries = b.entries || (b.entry && b.entry.price ? [b.entry] : []);
  const ep = $('bot6-entry-pill');
  if (ep) {
    const pend = entries.filter((e) => e.state === 'pending').length;
    ep.textContent = pend ? `${pend} PRE-TRADE` : (isBn ? 'প্রি-ট্রেড নেই' : 'NO PRE-TRADE');
  }
  const ap = $('bot6-anchor-pill');
  if (ap) {
    const first = entries[0];
    ap.textContent = first && first.price ? `🎯 ${fmtPrice(first.price)} (${fmtPrice(first.dist || 0)})` : (b.price ? `🎯 ${fmtPrice(b.price)}` : '—');
  }
  const wbox = $('bot6-watch-box');
  if (wbox && !wbox.innerHTML) {
    renderWatchBox('bot6-watch-box', w, Number(b.watch_min_pct || cfg.watch_min_pct || 60), isBn);
  } else {
    const sig = 'b6w|' + [w.buyer_pct, w.seller_pct, w.dominant, w.allowed, b.watch_min_pct].join('|');
    if (wbox && wbox.dataset.n !== sig) {
      wbox.dataset.n = sig;
      renderWatchBox('bot6-watch-box', w, Number(b.watch_min_pct || cfg.watch_min_pct || 60), isBn);
    }
  }

  const rules = b.rules || {};
  const rbox = $('bot6-rules-box');
  if (rbox) {
    const rsig = 'b6r|' + [rules.tp, rules.sl, rules.tp_money, rules.sl_money].join('|');
    if (rbox.dataset.n !== rsig) {
      rbox.dataset.n = rsig;
      const row = (lbl, val) => `<div class="ladder-row b6-row"><span class="lr-no b6-lbl">${lbl}</span><span class="lr-lot b6-val">${val}</span></div>`;
      rbox.innerHTML =
        row('TP', rules.tp === 'fixed' ? `${isBn ? 'ফিক্সড' : 'FIXED'} $${nf(rules.tp_money, 2)}` : (isBn ? 'নেই (0)' : 'none (0)')) +
        row('SL', rules.sl === 'fixed' ? `${isBn ? 'ফিক্সড' : 'FIXED'} $${nf(rules.sl_money, 2)}` : `🔒 ${nf(rules.lock_pct || 70, 0)}% profit-lock`) +
        row(isBn ? 'লক শুরু' : 'lock starts', `$${nf(rules.lock_min_profit || 0.8, 2)}`);
    }
  }

  const rl = $('bot6-running-line');
  if (rl) {
    const ai = b.ai || {};
    const aiTxt = ai.verdict
      ? ` · <b>AI:</b> <span style="color:${ai.allowed ? 'var(--green)' : 'var(--red)'}">${esc(ai.verdict)}${ai.allowed ? '' : ' ⛔'}</span>`
      : '';
    rl.innerHTML = `<b>${isBn ? 'সাইড:' : 'Side:'}</b> ${esc(String(cfg.side || 'watch'))} · ` +
      `<b>${isBn ? 'ডিস্ট্যান্স:' : 'Distance:'}</b> ${nf(cfg.distance, 2)} · ` +
      `${b.running_count || 0} ${isBn ? 'ওপেন' : 'open'} / ${b.pending_count || 0} ${isBn ? 'পেন্ডিং' : 'pending'}` + aiTxt;
  }
  const posEl = $('bot6-pos');
  if (posEl) {
    const ps = b.positions || [];
    const parts = ps.map((p) => {
      const side = String(p.side || '').toUpperCase();
      const net = Number(p.net || 0);
      const lock = p.locked ? ' 🔒' : '';
      return `${side} ${p.volume || ''} (${net >= 0 ? '+' : ''}${net.toFixed(2)})${lock}`;
    });
    posEl.innerHTML = `<b>${isBn ? 'ট্রেড:' : 'Trades:'}</b> ${parts.length ? parts.join(' · ') : (isBn ? 'নেই' : 'none')}`;
  }
  const stats = b.stats || {};
  const lastEl = $('bot6-last');
  if (lastEl) {
    const actionText = b.last_action ? ` · <i>${esc(b.last_action)}</i>` : '';
    lastEl.innerHTML = `${esc(t('botMarketNow'))}: <b>${esc(shown)}</b> · ` +
      `${stats.opened || 0} ${t('botOpened')} / ${stats.closed || 0} ${t('botClosed')} (P/L: $${Number(stats.realized || 0).toFixed(2)})${actionText}`;
    lastEl.className = 'bot-line' + (b.last_error ? ' warn' : '');
  }
}

async function bot6Post(path, body) {
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await res.json();
    if (!res.ok) {
      const msg = (j.error && j.error.message) || j.detail || 'Request failed';
      toast('error', msg);
      return j;
    }
    if (j.bot6) { if (S) { S.bot6 = j.bot6; renderBot6(S); } }
    return j;
  } catch (e) { toast('error', String(e)); return {}; }
}

function bindBot6UI() {
  const isBn = () => LANG === 'bn';
  $('bot6-start').addEventListener('click', async () => {
    const sym = (S && S.bot6 && S.bot6.symbol) || 'XAUUSDm';
    if (!isMarketOpen(sym)) {
      toast('error', isBn() ? `${sym} মার্কেটটি বর্তমানে বন্ধ থাকায় বট চালু করা যাবে না` : `Cannot start bot: ${sym} market is currently closed`);
      return;
    }
    const lot = Number($('bot6-lot').value || 0.01);
    const distance = Number($('bot6-distance').value || 1.0);
    const tp = Number($('bot6-tp').value || 0);
    const sl = Number($('bot6-sl').value || 0);
    const pressure = Number($('bot6-pressure').value || 60);
    const side = $('bot6-side').value || 'watch';
    const ok = window.confirm(
      isBn()
        ? `প্রি-ট্রেড এন্ট্রি বট চালু করবেন?\n\nলট: ${lot}\nডিস্ট্যান্স: ${distance}\nসাইড: ${side}\nTP: ${tp > 0 ? '$' + tp : 'নেই'}\nSL: ${sl > 0 ? '$' + sl : '৭০% প্রফিট লক ($০.৮০ থেকে)'}\nওয়াচ থ্রেশহোল্ড: ${pressure}%`
        : `Start Pre-Trade Entry Bot?\n\nLot: ${lot}\nDistance: ${distance}\nSide: ${side}\nTP: ${tp > 0 ? '$' + tp : 'none'}\nSL: ${sl > 0 ? '$' + sl : '70% profit lock (from $0.80)'}\nWatch threshold: ${pressure}%`
    );
    if (!ok) return;
    const res = await bot6Post('/api/bot6/start', {
      lot, distance, tp, sl, watch_min_pct: pressure, side,
    });
    if (res && res.ok) toast('ok', isBn() ? 'বট ৬ চালু হয়েছে' : 'Bot 6 Started');
  });

  $('bot6-stop').addEventListener('click', async () => {
    const res = await bot6Post('/api/bot6/stop', {});
    if (res && res.ok) toast('ok', isBn() ? 'বট ৬ বন্ধ করা হলো' : 'Bot 6 Stopped');
  });

  $('bot6-close').addEventListener('click', async () => {
    if (!window.confirm(isBn() ? 'বট ৬ এর সব খোলা ট্রেড এখনই ক্লোজ করবেন?' : 'Close all Bot 6 trades right now?')) return;
    const res = await bot6Post('/api/bot6/close', {});
    if (res && res.ok) toast('ok', `${isBn() ? 'ক্লোজ করা ট্রেড' : 'Closed positions'} (${res.closed || 0})`);
  });

  $('bot6-watch-gate').addEventListener('click', async () => {
    const cur = !!(S && S.bot6 && S.bot6.config && S.bot6.config.watch_gate);
    const next = !cur;
    const res = await bot6Post('/api/bot6/config', { watch_gate: next });
    if (res && res.ok) toast('ok', `${isBn() ? 'ওয়াচ চেক' : 'Watch Check'} · ${next ? 'ON' : 'OFF'}`);
  });

  $('bot6-ai-gate').addEventListener('click', async () => {
    const cur = !(S && S.bot6 && S.bot6.config && S.bot6.config.ai_gate === false);
    const next = !cur;
    const res = await bot6Post('/api/bot6/config', { ai_gate: next });
    if (res && res.ok) toast('ok', `${isBn() ? 'এআই চেক' : 'AI Check'} · ${next ? 'ON' : 'OFF'}`);
  });

  $('bot6-lot').addEventListener('change', () => { void bot6Post('/api/bot6/config', { lot: Number($('bot6-lot').value || 0.01) }); });
  $('bot6-distance').addEventListener('change', () => { void bot6Post('/api/bot6/config', { distance: Math.max(0, Number($('bot6-distance').value || 1.0)) }); });
  $('bot6-tp').addEventListener('change', () => { void bot6Post('/api/bot6/config', { tp: Math.max(0, Number($('bot6-tp').value || 0)) }); });
  $('bot6-sl').addEventListener('change', () => { void bot6Post('/api/bot6/config', { sl: Math.max(0, Number($('bot6-sl').value || 0)) }); });
  $('bot6-pressure').addEventListener('change', () => { void bot6Post('/api/bot6/config', { watch_min_pct: Math.min(99, Math.max(0, Number($('bot6-pressure').value || 60))) }); });
  $('bot6-side').addEventListener('change', () => { void bot6Post('/api/bot6/config', { side: $('bot6-side').value }); });
  $('bot6-market-chips').addEventListener('click', async (e) => {
    const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
    if (!chip || !chip.dataset.sym) return;
    const sym = chip.dataset.sym;
    if (chip.classList.contains('market-off') || !isMarketOpen(sym)) {
      toast('warn', isBn() ? `${sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)` : `${sym} market is currently closed`);
      return;
    }
    document.querySelectorAll('#bot6-market-chips .chip').forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
    const res = await bot6Post('/api/bot6/config', { symbol: sym });
    if (res && res.ok) {
      if (res.bot6) { if (S) S.bot6 = res.bot6; renderBot6(S); }
      toast('ok', `${t('botMarketSwitched')}: ${sym}`);
      if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
    }
  });
}

/* ------------------------------------------ bot 7 (reversal candle) */
function _hhmm(ts) {
  if (!ts) return '—';
  const d2 = new Date(Number(ts) * 1000);
  return String(d2.getHours()).padStart(2, '0') + ':' + String(d2.getMinutes()).padStart(2, '0');
}

function renderBot7(d) {
  const b = d.bot7 || {};
  const cfg = b.config || {};
  const isBn = LANG === 'bn';
  const running = !!b.running;
  const live = !!d.connected;
  const chip = $('bot7-chip');
  if (chip) {
    chip.className = 'bot-chip ' + (!live ? 'offline' : running ? 'running' : 'stopped');
    chip.textContent = !live ? t('botOffline') : running ? t('botRunning') : t('botStopped');
  }
  $('bot7-start').classList.toggle('active', running);
  $('bot7-stop').classList.toggle('active', !running && live);

  const wanted = b.symbol_requested || cfg.symbol || 'XAUUSDm';
  const sym = b.symbol || '';
  const symOk = !!b.symbol_ok && sym;
  const shown = symOk ? sym : wanted;
  $('bot7-symbol').textContent = shown;
  let priceStr = '';
  if (b.ask && b.bid) priceStr = ` · Ask <b>${fmtPrice(b.ask)}</b> / Bid <b>${fmtPrice(b.bid)}</b>`;
  const sn = $('bot7-symbol-name');
  if (sn) {
    sn.innerHTML = `${esc(shown)}${priceStr}`;
    sn.style.color = symOk ? 'var(--accent)' : '#fca5a5';
  }

  const pick = $('bot7-market-chips');
  if (pick) {
    const options = (b.symbol_options && b.symbol_options.length ? b.symbol_options : []);
    const list = options.length ? options.slice() : [shown];
    if (!list.includes(shown)) list.unshift(shown);
    if (live) {
      const sig = list.join(',') + '|' + shown;
      if (pick.dataset.n !== sig) {
        pick.dataset.n = sig;
        pick.innerHTML = list.map((s) =>
          `<button type="button" class="chip${s === shown ? ' active' : ''}" data-sym="${esc(s)}">${esc(s)}</button>`).join('');
      }
    } else {
      pick.querySelectorAll('.chip').forEach(c => c.classList.toggle('active', c.dataset.sym === shown));
    }
  }
  setBotCurveSymbol('bot7', shown);

  const setVal = (id, v) => {
    const el = $(id);
    if (el && document.activeElement !== el && v !== undefined && v !== null) el.value = v;
  };
  setVal('bot7-lot', cfg.lot);
  setVal('bot7-reverse', cfg.reverse_dist);
  setVal('bot7-tp', cfg.tp);
  setVal('bot7-sl', cfg.sl);
  setVal('bot7-pressure', cfg.watch_min_pct);
  const tfEl = $('bot7-tf');
  if (tfEl && document.activeElement !== tfEl && cfg.timeframe) tfEl.value = cfg.timeframe;

  const gateOn = !!cfg.watch_gate;
  const gb = $('bot7-watch-gate');
  if (gb) gb.classList.toggle('active', gateOn);
  const gs = $('bot7-watch-gate-status');
  if (gs) {
    gs.textContent = gateOn ? 'ON' : 'OFF';
    gs.className = 'trend-badge ' + (gateOn ? 'active' : 'deactive');
  }
  const aiOn7 = cfg.ai_gate !== false;
  const ab7 = $('bot7-ai-gate');
  if (ab7) ab7.classList.toggle('active', aiOn7);
  const as7 = $('bot7-ai-gate-status');
  if (as7) {
    as7.textContent = aiOn7 ? 'ON' : 'OFF';
    as7.className = 'trend-badge ' + (aiOn7 ? 'active' : 'deactive');
  }
  renderPriceMap('bot7', b);

  const candle = b.candle || {};
  const trig = b.trigger || {};
  const cp = $('bot7-candle-pill');
  if (cp) {
    cp.textContent = candle.ready
      ? `${cfg.timeframe || 'M1'} ${candle.bull ? 'BULL ▲' : 'BEAR ▼'}`
      : (isBn ? 'ক্যান্ডেল লোড হচ্ছে…' : 'loading candle…');
    cp.className = 'pill trend-pill ' + (candle.ready ? (candle.bull ? 'pill-bull' : 'pill-bear') : '');
    cp.style.color = '';
  }
  const tp = $('bot7-trigger-pill');
  if (tp) {
    let tpText = 'WAITING';
    let tpClass = 'radar-zone-pill';
    if (!candle.ready) {
      tpText = 'WAITING';
      tpClass = 'radar-zone-pill';
    } else if (trig.fired) {
      tpText = isBn ? 'এই ক্যান্ডেলে ফায়ার হয়েছে' : 'FIRED this candle';
      tpClass = 'radar-zone-pill in-buy';
    } else if (trig.blocked) {
      tpText = isBn ? 'রিভার্সাল হয়েছে · গেট ব্লক' : 'REVERSAL · GATE BLOCKED';
      tpClass = 'radar-zone-pill in-sell';
    } else if (Number(trig.left) <= 0 && Number(trig.dist) > 0) {
      tpText = running ? (isBn ? 'রিভার্সাল রেডি' : 'REVERSAL READY')
        : (isBn ? 'রিভার্সাল রেডি · START চাপুন' : 'REVERSAL READY · press START');
      tpClass = 'radar-zone-pill in-ready';
    } else {
      tpText = isBn ? 'রিভার্সাল খুঁজছে' : 'HUNTING REVERSAL';
      tpClass = 'radar-zone-pill in-hunting';
    }
    tp.textContent = tpText;
    tp.className = tpClass;
  }
  const ctime = $('bot7-candle-time-pill');
  if (ctime) ctime.textContent = candle.ready ? `🕯️ ${_hhmm(trig.candle_time)}` : '—';

  const cbox = $('bot7-candle-box');
  if (cbox) {
    if (!candle.ready) {
      const msg = trig.rates_error ? esc(String(trig.rates_error)) : (isBn ? 'টার্মিনাল থেকে ক্যান্ডেল আসছে…' : 'waiting for candles from the terminal…');
      cbox.innerHTML = `<div class="lr-empty">${msg}</div>`;
    } else {
      const sig = 'b7c|' + [candle.time, candle.close, candle.bull, candle.body, b.price,
        trig.ref, trig.need, trig.left, trig.blocked].join('|');
      if (cbox.dataset.n !== sig) {
        cbox.dataset.n = sig;
        const price = Number(b.price || 0);
        const dist = Number(trig.dist || 0) || Number(cfg.reverse_dist || 0) || Number(candle.body || 0);
        const close = Number(candle.close || 0);
        // the server reports the live turning point: it starts at the candle's
        // close and follows the lowest low / highest high while the move keeps
        // running, so a drop that goes deeper then snaps back fires from there
        const ref = Number(trig.ref || 0) || close;
        const need = Number(trig.need || 0) || (candle.bull ? (ref - dist) : (ref + dist));
        const left = price > 0 ? (candle.bull ? (price - need) : (need - price)) : 0;
        const row = (lbl, val, cls) => `<div class="ladder-row b7-row ${cls || ''}"><span class="lr-no b7-lbl">${lbl}</span><span class="lr-price b7-val">${val}</span></div>`;
        const dirBadge = candle.bull
          ? `<span class="b7-badge bull">BULL ▲</span>`
          : `<span class="b7-badge bear">BEAR ▼</span>`;
        const leftBadge = left <= 0
          ? `<span class="b7-badge ready">✔ ${isBn ? 'হয়ে গেছে' : 'REACHED'}</span>`
          : `<span class="b7-badge waiting">${nf(left, 2)} ${isBn ? 'বাকি' : 'left'}</span>`;
        cbox.innerHTML =
          row(isBn ? 'দিক' : 'direction', dirBadge, candle.bull ? 'bull-row' : 'bear-row') +
          row(isBn ? 'বডি' : 'body', `<span class="b7-body-val">${nf(candle.body || 0, 2)}</span>`) +
          row(isBn ? 'ক্লোজ' : 'close', `<span class="b7-close-val">${fmtPrice(close)}</span>`) +
          row(isBn ? 'টার্নিং পয়েন্ট' : 'turning point', `<span class="b7-ref">${fmtPrice(ref)}</span>`) +
          row(isBn ? 'উল্টো ট্রিগার' : 'reversal trigger', `<span class="b7-need">${fmtPrice(need)}</span>`) +
          row(isBn ? 'বাকি' : 'left to fire', leftBadge, left <= 0 ? 'ready-row' : '') +
          (trig.blocked ? row(isBn ? 'ব্লক' : 'blocked', `<span class="b7-blocked">${esc(String(trig.blocked))}</span>`, 'failed') : '');
      }
    }
  }

  const wbox = $('bot7-watch-box');
  const w = b.watch || {};
  if (wbox) {
    const sig = 'b7w|' + [w.buyer_pct, w.seller_pct, w.dominant, w.allowed, b.watch_min_pct].join('|');
    if (wbox.dataset.n !== sig) {
      wbox.dataset.n = sig;
      renderWatchBox('bot7-watch-box', w, Number(b.watch_min_pct || cfg.watch_min_pct || 60), isBn);
    }
  }

  const rl = $('bot7-running-line');
  if (rl) {
    const ai = b.ai || {};
    const aiTxt = ai.verdict
      ? ` · <b>AI:</b> <span style="color:${ai.allowed ? 'var(--green)' : 'var(--red)'}">${esc(ai.verdict)}${ai.allowed ? '' : ' ⛔'}</span>`
      : '';
    rl.innerHTML = `<b>${isBn ? 'টাইমফ্রেম:' : 'Timeframe:'}</b> ${esc(String(cfg.timeframe || 'M1'))} · ` +
      `<b>${isBn ? 'রিভার্স:' : 'Reverse:'}</b> ${nf(cfg.reverse_dist, 2)} · ` +
      `${b.running_count || 0} ${isBn ? 'ওপেন' : 'open'}` + aiTxt;
  }
  const posEl = $('bot7-pos');
  if (posEl) {
    const parts = (b.positions || []).map((p) => {
      const side = String(p.side || '').toUpperCase();
      const net = Number(p.net || 0);
      const lock = p.locked ? ' 🔒' : '';
      return `${side} ${p.volume || ''} (${net >= 0 ? '+' : ''}${net.toFixed(2)})${lock}`;
    });
    posEl.innerHTML = `<b>${isBn ? 'ট্রেড:' : 'Trades:'}</b> ${parts.length ? parts.join(' · ') : (isBn ? 'নেই' : 'none')}`;
  }
  const stats = b.stats || {};
  const lastEl = $('bot7-last');
  if (lastEl) {
    const actionText = b.last_action ? ` · <i>${esc(b.last_action)}</i>` : '';
    lastEl.innerHTML = `${esc(t('botMarketNow'))}: <b>${esc(shown)}</b> · ` +
      `${stats.opened || 0} ${t('botOpened')} / ${stats.closed || 0} ${t('botClosed')} (P/L: $${Number(stats.realized || 0).toFixed(2)})${actionText}`;
    lastEl.className = 'bot-line' + (b.last_error ? ' warn' : '');
  }
}

async function bot7Post(path, body) {
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await res.json();
    if (!res.ok) {
      const msg = (j.error && j.error.message) || j.detail || 'Request failed';
      toast('error', msg);
      return j;
    }
    if (j.bot7) { if (S) { S.bot7 = j.bot7; renderBot7(S); } }
    return j;
  } catch (e) { toast('error', String(e)); return {}; }
}

function bindBot7UI() {
  const isBn = () => LANG === 'bn';
  $('bot7-start').addEventListener('click', async () => {
    const sym = (S && S.bot7 && S.bot7.symbol) || 'XAUUSDm';
    if (!isMarketOpen(sym)) {
      toast('error', isBn() ? `${sym} মার্কেটটি বর্তমানে বন্ধ থাকায় বট চালু করা যাবে না` : `Cannot start bot: ${sym} market is currently closed`);
      return;
    }
    const lot = Number($('bot7-lot').value || 0.01);
    const timeframe = $('bot7-tf').value || 'M1';
    const reverse_dist = Number($('bot7-reverse').value || 0);
    const tp = Number($('bot7-tp').value || 0);
    const sl = Number($('bot7-sl').value || 0);
    const pressure = Number($('bot7-pressure').value || 60);
    const ok = window.confirm(
      isBn()
        ? `রিভার্সাল ক্যান্ডেল বট চালু করবেন?\n\nলট: ${lot}\nটাইমফ্রেম: ${timeframe}\nরিভার্সাল ডিস্ট্যান্স: ${reverse_dist > 0 ? reverse_dist : 'অটো (ক্যান্ডেলের বডি)'}\nTP: ${tp > 0 ? '$' + tp : 'নেই'}\nSL: ${sl > 0 ? '$' + sl : '৭০% প্রফিট লক ($০.৮০ থেকে)'}\nওয়াচ থ্রেশহোল্ড: ${pressure}%`
        : `Start Reversal Candle Bot?\n\nLot: ${lot}\nTimeframe: ${timeframe}\nReversal distance: ${reverse_dist > 0 ? reverse_dist : 'auto (candle body)'}\nTP: ${tp > 0 ? '$' + tp : 'none'}\nSL: ${sl > 0 ? '$' + sl : '70% profit lock (from $0.80)'}\nWatch threshold: ${pressure}%`
    );
    if (!ok) return;
    const res = await bot7Post('/api/bot7/start', {
      lot, timeframe, reverse_dist, tp, sl, watch_min_pct: pressure,
    });
    if (res && res.ok) toast('ok', isBn() ? 'বট ৭ চালু হয়েছে' : 'Bot 7 Started');
  });

  $('bot7-stop').addEventListener('click', async () => {
    const res = await bot7Post('/api/bot7/stop', {});
    if (res && res.ok) toast('ok', isBn() ? 'বট ৭ বন্ধ করা হলো' : 'Bot 7 Stopped');
  });

  $('bot7-close').addEventListener('click', async () => {
    if (!window.confirm(isBn() ? 'বট ৭ এর সব খোলা ট্রেড এখনই ক্লোজ করবেন?' : 'Close all Bot 7 trades right now?')) return;
    const res = await bot7Post('/api/bot7/close', {});
    if (res && res.ok) toast('ok', `${isBn() ? 'ক্লোজ করা ট্রেড' : 'Closed positions'} (${res.closed || 0})`);
  });

  $('bot7-watch-gate').addEventListener('click', async () => {
    const cur = !!(S && S.bot7 && S.bot7.config && S.bot7.config.watch_gate);
    const next = !cur;
    const res = await bot7Post('/api/bot7/config', { watch_gate: next });
    if (res && res.ok) toast('ok', `${isBn() ? 'ওয়াচ চেক' : 'Watch Check'} · ${next ? 'ON' : 'OFF'}`);
  });

  $('bot7-ai-gate').addEventListener('click', async () => {
    const cur = !(S && S.bot7 && S.bot7.config && S.bot7.config.ai_gate === false);
    const next = !cur;
    const res = await bot7Post('/api/bot7/config', { ai_gate: next });
    if (res && res.ok) toast('ok', `${isBn() ? 'এআই চেক' : 'AI Check'} · ${next ? 'ON' : 'OFF'}`);
  });

  $('bot7-lot').addEventListener('change', () => { void bot7Post('/api/bot7/config', { lot: Number($('bot7-lot').value || 0.01) }); });
  $('bot7-tf').addEventListener('change', () => { void bot7Post('/api/bot7/config', { timeframe: $('bot7-tf').value }); });
  $('bot7-reverse').addEventListener('change', () => { void bot7Post('/api/bot7/config', { reverse_dist: Math.max(0, Number($('bot7-reverse').value || 0)) }); });
  $('bot7-tp').addEventListener('change', () => { void bot7Post('/api/bot7/config', { tp: Math.max(0, Number($('bot7-tp').value || 0)) }); });
  $('bot7-sl').addEventListener('change', () => { void bot7Post('/api/bot7/config', { sl: Math.max(0, Number($('bot7-sl').value || 0)) }); });
  $('bot7-pressure').addEventListener('change', () => { void bot7Post('/api/bot7/config', { watch_min_pct: Math.min(99, Math.max(0, Number($('bot7-pressure').value || 60))) }); });
  $('bot7-market-chips').addEventListener('click', async (e) => {
    const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
    if (!chip || !chip.dataset.sym) return;
    const sym = chip.dataset.sym;
    if (chip.classList.contains('market-off') || !isMarketOpen(sym)) {
      toast('warn', isBn() ? `${sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)` : `${sym} market is currently closed`);
      return;
    }
    document.querySelectorAll('#bot7-market-chips .chip').forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
    const res = await bot7Post('/api/bot7/config', { symbol: sym });
    if (res && res.ok) {
      if (res.bot7) { if (S) S.bot7 = res.bot7; renderBot7(S); }
      toast('ok', `${t('botMarketSwitched')}: ${sym}`);
      if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
    }
  });
}

function clearSpeakingHighlight() {
  if (currentSpeakingKey) {
    const el = document.getElementById(currentSpeakingKey);
    if (el) el.classList.remove('voice-light-pulse');
  }
}

function updateVoiceMuteButtons() {
  const topbarBtn = $('topbar-mute-btn');
  const panelBtn = $('voice-mute-btn');
  const panelIcon = $('voice-icon');
  const panelLbl = $('voice-lbl');

  if (topbarBtn) {
    topbarBtn.textContent = voiceMuted ? '🔇' : '🔊';
    topbarBtn.title = voiceMuted
      ? (LANG === 'bn' ? 'ভয়েস আনমিউট করুন (সাউন্ড অন)' : 'Unmute Voice Announcements')
      : (LANG === 'bn' ? 'ভয়েস মিউট করুন (সাউন্ড অফ)' : 'Mute Voice Announcements');
    topbarBtn.classList.toggle('muted', voiceMuted);
  }

  if (panelBtn) {
    panelBtn.classList.toggle('muted', voiceMuted);
    panelBtn.title = voiceMuted
      ? (LANG === 'bn' ? 'ভয়েস আনমিউট করুন' : 'Click to Unmute Voice')
      : (LANG === 'bn' ? 'ভয়েস মিউট করুন' : 'Click to Mute Voice');
  }
  if (panelIcon) {
    panelIcon.textContent = voiceMuted ? '🔇' : '🔊';
  }
  if (panelLbl) {
    panelLbl.textContent = voiceMuted ? 'Voice: OFF' : 'Voice: ON';
  }
}

function setVoiceMute(muted, showToast = true) {
  voiceMuted = !!muted;
  localStorage.setItem('exdash.voice_muted', voiceMuted ? 'true' : 'false');
  if (voiceMuted) {
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch (e) {}
    }
    clearSpeakingHighlight();
    speechQueue = [];
    isSpeaking = false;
    currentSpeakingKey = null;
  }
  updateVoiceMuteButtons();
  if (showToast) {
    toast('ok', voiceMuted
      ? (LANG === 'bn' ? 'ভয়েস অ্যালার্ট বন্ধ করা হয়েছে 🔇' : 'Voice Announcements Muted 🔇')
      : (LANG === 'bn' ? 'ভয়েস অ্যালার্ট চালু করা হয়েছে 🔊' : 'Voice Announcements Active 🔊'));
  }
}

function formatEventForSpeech(e) {
  if (!e) return '';
  const msg = String(e.message || '').trim();
  if (!msg) return '';

  const isBn = (LANG === 'bn');

  // 1. Profit trade closed: e.g. "closed #123 EURUSD -> +0.15" or "Take profit reached -> +0.20"
  const winMatch = msg.match(/(?:closed|tp|profit).*?->\s*\+?([\d.]+)/i);
  if (winMatch) {
    const amt = winMatch[1];
    return isBn ? `ট্রেড প্রফিটে ক্লোজ হয়েছে, লাভ ${amt} ডলার` : `Trade closed in profit, plus ${amt} dollars`;
  }

  // 2. Loss trade closed: e.g. "closed #123 EURUSD -> -0.15" or "Stop loss reached -> -0.20"
  const lossMatch = msg.match(/(?:closed|sl|loss).*?->\s*-([\d.]+)/i);
  if (lossMatch) {
    const amt = lossMatch[1];
    return isBn ? `ট্রেড লসে ক্লোজ হয়েছে, লস ${amt} ডলার` : `Trade closed with loss, minus ${amt} dollars`;
  }

  // 3. Any other closed trade with -> value
  const anyClosedMatch = msg.match(/closed.*?->\s*([+-]?[\d.]+)/i);
  if (anyClosedMatch) {
    const val = Number(anyClosedMatch[1]);
    if (val >= 0) {
      return isBn ? `ট্রেড ক্লোজ হয়েছে, লাভ ${val} ডলার` : `Trade closed in profit, plus ${val} dollars`;
    } else {
      return isBn ? `ট্রেড ক্লোজ হয়েছে, লস ${Math.abs(val)} ডলার` : `Trade closed with loss, minus ${Math.abs(val)} dollars`;
    }
  }

  // 4. Trend updates: e.g. "Bot 2 60s Trend: UP (BULLISH)"
  if (/trend/i.test(msg) || /bullish|bearish/i.test(msg)) {
    if (/bullish|up/i.test(msg)) {
      return isBn ? 'ট্রেন্ড আপডেট: মার্কেট বুলিশ আপ ট্রেন্ড' : 'Trend update: Market Bullish UP';
    }
    if (/bearish|down/i.test(msg)) {
      return isBn ? 'ট্রেন্ড আপডেট: মার্কেট বেয়ারিশ ডাউন ট্রেন্ড' : 'Trend update: Market Bearish DOWN';
    }
  }

  // 5. Bot orders: BUY / SELL
  const buyMatch = msg.match(/\b(buy)\b/i);
  const sellMatch = msg.match(/\b(sell)\b/i);
  const symMatch = msg.match(/\b([A-Z]{6}|[A-Z]{3}\/[A-Z]{3})\b/);
  const symbol = symMatch ? symMatch[1].replace('/', '') : '';

  if (buyMatch) {
    return isBn ? `নতুন বাই অর্ডার এক্সিকিউট হয়েছে ${symbol}` : `New Buy order executed ${symbol}`;
  }
  if (sellMatch) {
    return isBn ? `নতুন সেল অর্ডার এক্সিকিউট হয়েছে ${symbol}` : `New Sell order executed ${symbol}`;
  }

  // 6. Connect / Disconnect
  if (/login successful|connected/i.test(msg)) {
    return isBn ? 'অ্যাকাউন্ট সফলভাবে কানেক্ট হয়েছে' : 'Account connected successfully';
  }
  if (/disconnected|logged out/i.test(msg)) {
    return isBn ? 'অ্যাকাউন্ট ডিসকানেক্ট হয়েছে' : 'Account disconnected';
  }

  // 7. Generic message cleanup: strip symbols like ->, #, *, [], emojis
  let clean = msg
    .replace(/->/g, ' to ')
    .replace(/[#@*▲▼★⚡✓✔✕✖•|]/g, ' ')
    .replace(/\[.*?\]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  if (clean.length > 90) clean = clean.slice(0, 85);
  return clean;
}

function processSpeechQueue() {
  if (voiceMuted) return;
  if (!('speechSynthesis' in window)) return;
  if (isSpeaking) return;
  if (speechQueue.length === 0) return;

  const item = speechQueue.shift();
  if (!item || !item.speechText) {
    processSpeechQueue();
    return;
  }

  isSpeaking = true;
  currentSpeakingKey = item.elId;

  // Clear any existing highlight and light up the new line like an illuminated lamp
  clearSpeakingHighlight();
  const el = document.getElementById(item.elId);
  if (el) {
    el.classList.add('voice-light-pulse');
    try {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (e) {}
  }

  try {
    const utterance = new SpeechSynthesisUtterance(item.speechText);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;

    // Pick best available voice
    const voices = window.speechSynthesis.getVoices();
    if (voices && voices.length) {
      if (LANG === 'bn') {
        const bnVoice = voices.find(v => v.lang && v.lang.startsWith('bn'));
        if (bnVoice) utterance.voice = bnVoice;
      }
      if (!utterance.voice) {
        const enVoice = voices.find(v => /natural|google|zira|david/i.test(v.name) && v.lang.startsWith('en'))
                     || voices.find(v => v.lang && v.lang.startsWith('en'));
        if (enVoice) utterance.voice = enVoice;
      }
    }

    const onFinish = () => {
      if (el) el.classList.remove('voice-light-pulse');
      currentSpeakingKey = null;
      isSpeaking = false;
      // Slight pause between events so speech is crisp, natural, and never overlaps
      setTimeout(() => {
        processSpeechQueue();
      }, 350);
    };

    utterance.onend = onFinish;
    utterance.onerror = (err) => {
      console.warn('SpeechSynthesis error:', err);
      onFinish();
    };

    window.speechSynthesis.speak(utterance);
  } catch (err) {
    console.error('Speech synthesis failure:', err);
    if (el) el.classList.remove('voice-light-pulse');
    currentSpeakingKey = null;
    isSpeaking = false;
    setTimeout(() => { processSpeechQueue(); }, 350);
  }
}

/* ------------------------------------------------------------------- feed */
function renderFeed(d) {
  const evs = d.events || [];
  const feedEl = $('feed');
  const badgeEl = $('feed-badge');
  const tabBadgeEl = $('tab-badge-feed');
  const railFeedEl = $('rail-feed-count');

  if (!evs.length) {
    if (feedEl) feedEl.innerHTML = '<li class="k-info"><span class="t">--:--</span><span>waiting for activity…</span></li>';
    if (badgeEl) badgeEl.textContent = '0';
    if (tabBadgeEl) tabBadgeEl.textContent = '0';
    if (railFeedEl) railFeedEl.textContent = '0';
    return;
  }

  // 1. Initial load snapshot handling:
  // "2. Update na asha porjonto kisu bolbe na." -> On first load, record existing events so they are not spoken.
  if (!feedInitialized) {
    evs.forEach((e) => {
      const key = `${e.ts}_${e.kind || ''}_${e.message || ''}`;
      seenEventKeys.add(key);
    });
    if (seenEventKeys.size > 1500) seenEventKeys = new Set(Array.from(seenEventKeys).slice(-800));
    feedInitialized = true;
  } else {
    // 2. Identify newly arrived events and queue for speech sequentially:
    const newItems = [];
    evs.forEach((e) => {
      const key = `${e.ts}_${e.kind || ''}_${e.message || ''}`;
      if (!seenEventKeys.has(key)) {
        seenEventKeys.add(key);
        if (seenEventKeys.size > 1500) seenEventKeys = new Set(Array.from(seenEventKeys).slice(-800));
        const speechText = formatEventForSpeech(e);
        if (speechText) {
          const elId = `feed-evt-${e.ts}-${Math.abs(hashStr(key))}`;
          newItems.push({
            key,
            ts: e.ts || 0,
            kind: e.kind,
            rawText: e.message,
            speechText: speechText,
            elId: elId
          });
        }
      }
    });

    if (newItems.length > 0 && !voiceMuted) {
      // Sort oldest to newest so they speak in chronological sequence
      newItems.sort((a, b) => (a.ts || 0) - (b.ts || 0));
      newItems.forEach((it) => speechQueue.push(it));
      processSpeechQueue();
    }
  }

  const cntStr = String(evs.length);
  if (badgeEl) badgeEl.textContent = cntStr;
  if (tabBadgeEl) tabBadgeEl.textContent = cntStr;
  if (railFeedEl) railFeedEl.textContent = cntStr;

  // Re-render feed list with unique DOM IDs and keep active lamp glow if currently speaking
  if (feedEl) {
    feedEl.innerHTML = evs.slice(0, 40).map((e) => {
      let kind = String(e.kind || 'info');
      if (kind === 'trade') kind = /closed/i.test(e.message) ? (/->\s*-/.test(e.message) ? 'trade loss' : 'trade') : 'trade';
      // kind and message come from the server (and the EA bridge) - escape both
      // before they touch innerHTML, or a crafted EA event is script injection
      const cls = 'k-' + kind.toLowerCase().replace(/[^a-z-]/g, '');
      const txt = esc(String(e.message || ''));
      const colored = txt.replace(/-&gt;\s*([+-]?[\d.]+)/, (m, v) => `-&gt; <span class="${Number(v) > 0 ? 'value-win' : 'value-loss'}">${v}</span>`);

      const key = `${e.ts}_${e.kind || ''}_${e.message || ''}`;
      const elId = `feed-evt-${e.ts}-${Math.abs(hashStr(key))}`;
      const isLit = (currentSpeakingKey === elId);

      return `<li id="${elId}" class="${cls} ${isLit ? 'voice-light-pulse' : ''}"><span class="t">${esc(e.server_time || '')}</span><span>${colored}</span></li>`;
    }).join('');
  }
}

/* ----------------------------------------------------------------- footer */
function renderFooter(d) {
  const src = { mt5: 'source: MT5 terminal', ea: 'source: EA bridge', none: 'source: logged out' }[d.source] || 'source: idle';
  $('foot-source').textContent = src;
  $('foot-latency').textContent = `engine ${d.engine_latency_ms ?? '—'} ms`;
  $('foot-version').textContent = `MT5 ${d.env && d.env.mt5_version ? 'v' + d.env.mt5_version : 'n/a'}`;
  const now = new Date();
  $('server-clock').textContent = d.server_time ? d.server_time.split(' ')[1] : '--:--:--';
  $('last-update').textContent = `updated ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())} (local)`;
}

/* ---------------------------------------------------------------- toasts */
function toast(kind, message) {
  const box = $('toasts');
  const div = document.createElement('div');
  let cls = 'ok';
  if (kind === 'error' || /failed/i.test(message)) cls = 'err';
  else if (kind === 'warn') cls = 'ok';
  else if (/closed/i.test(message)) cls = /-\d/.test(message.replace('->', '')) ? 'loss' : 'win';
  div.className = 'toast ' + cls;
  div.innerHTML = `<div class="tt">${esc(kind === 'error' ? 'Error' : 'Update')}</div><div class="tm">${esc(message)}</div>`;
  box.appendChild(div);
  setTimeout(() => { div.style.opacity = '0'; div.style.transform = 'translateY(6px)'; div.style.transition = 'all .3s'; }, 5200);
  setTimeout(() => div.remove(), 5600);
  while (box.children.length > 5) box.firstChild.remove();
}

function detectNewClosed(prev, d) {
  const top = (d.history || [])[0];
  if (!top) return;
  const id = String(top.position_id);
  if (lastClosedId === null) { lastClosedId = id; return; }
  if (id !== lastClosedId) {
    lastClosedId = id;
    const net = Number(top.net_profit || 0);
    toast('trade', `Closed ${top.symbol} ${top.side} ${top.volume} -> ${net > 0 ? '+' : ''}${nf(net)}`);
  }
}

/* --------------------------------------------------------------- handlers */
function showConnectError(err) {
  const box = $('connect-error');
  if (!err) { box.classList.add('hidden'); return; }
  const code = err.code ? ` <span class="muted">(#${err.code})</span>` : '';
  box.innerHTML = `<b>${esc(err.message || 'Connection failed')}</b>${code}${err.hint ? `<br><span class="muted">${esc(err.hint)}</span>` : ''}`;
  box.classList.remove('hidden');
}

async function doConnect() {
  showConnectError(null);
  const login = $('login').value.trim();
  const server = currentServer();
  if (!login) { showConnectError({ message: t('needLogin') }); $('login').focus(); return; }
  if (!server) { showConnectError({ message: t('serverPick') }); ($('server-select').value === SERVER_CUSTOM ? $('server') : $('server-select')).focus(); return; }

  const btn = $('connect-btn');
  btn.disabled = true;
  $('connect-progress').classList.remove('hidden');
  try {
    const body = {
      login,
      password: $('password').value,
      server,
      path: $('terminal-path').value || '',
      remember: $('remember').checked,
    };
    const r = await fetch('/api/connect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) {
      showConnectError(j.error || { message: 'Login failed' });
    } else {
      // only a working login is remembered
      if ($('remember').checked) {
        localStorage.setItem('exdash.login', login);
        localStorage.setItem('exdash.server', server);
      }
      connectViewPinned = false;
      toast('ok', `Logged in: ${j.account ? j.account.login : login} · ${(j.account && j.account.server) || server}`);
    }
  } catch (e) {
    showConnectError({ message: String(e) });
  } finally {
    btn.disabled = false;
    $('connect-progress').classList.add('hidden');
  }
}

/* Full logout: server drops the MT5 session, the UI goes back to the login page */
async function doDisconnect() {
  try { await fetch('/api/disconnect', { method: 'POST' }); } catch (e) {}
  S = null;
  wasConnected = false;
  dashboardPreview = false;
  dashboardPreviewUnlocked = false;
  connectViewPinned = false;
  fullResetUI();
  histSig = '';
  lastClosedId = null;
  $('dash').classList.add('hidden');
  $('connect-view').classList.remove('hidden');
  $('settings-modal').classList.add('hidden');
  showConnectError(null);
  toast('ok', 'Disconnected — Exness account login din');
}

function bindUI() {
  el('#connect-form').addEventListener('submit', (e) => { e.preventDefault(); doConnect(); });
  function closeDashboardPassword(accepted) {
    const modal = $('dashboard-password-modal');
    if (modal) modal.classList.add('hidden');
    const input = $('dashboard-password-input');
    if (input) input.value = '';
    const resolve = dashboardPasswordResolve;
    dashboardPasswordResolve = null;
    if (resolve) resolve(accepted);
  }
  function askDashboardPassword() {
    const modal = $('dashboard-password-modal');
    const input = $('dashboard-password-input');
    const error = $('dashboard-password-error');
    if (!modal || !input) return Promise.resolve(false);
    modal.classList.remove('hidden');
    if (error) error.textContent = '';
    setTimeout(() => input.focus(), 30);
    return new Promise((resolve) => { dashboardPasswordResolve = resolve; });
  }
  function unlockDashboardPreview() {
    if (dashboardPreviewUnlocked) return true;
    return askDashboardPassword().then((entered) => {
      if (!entered) return false;
      dashboardPreviewUnlocked = true;
      showConnectError(null);
      return true;
    });
  }
  const passwordForm = $('dashboard-password-form');
  if (passwordForm) {
    passwordForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const input = $('dashboard-password-input');
      const error = $('dashboard-password-error');
      if (input && input.value === '0186') {
        closeDashboardPassword(true);
      } else if (error) {
        error.textContent = LANG === 'bn' ? 'ভুল password — access denied' : 'Incorrect password — access denied';
        if (input) { input.select(); input.focus(); }
      }
    });
  }
  const passwordCancel = $('dashboard-password-cancel');
  const passwordCancelText = $('dashboard-password-cancel-text');
  if (passwordCancel) passwordCancel.addEventListener('click', () => closeDashboardPassword(false));
  if (passwordCancelText) passwordCancelText.addEventListener('click', () => closeDashboardPassword(false));
  const passwordModal = $('dashboard-password-modal');
  if (passwordModal) passwordModal.addEventListener('click', (event) => {
    if (event.target === passwordModal) closeDashboardPassword(false);
  });
  $('view-dashboard-btn').addEventListener('click', async () => {
    if (!(await unlockDashboardPreview())) return;
    dashboardPreview = true;
    connectViewPinned = false;
    $('connect-view').classList.add('hidden');
    $('dash').classList.remove('hidden');
    setTimeout(() => {
      window.dispatchEvent(new Event('resize'));
      if (window.SoftradeXTerminal && window.SoftradeXTerminal.refresh) {
        window.SoftradeXTerminal.refresh();
      }
    }, 60);
    toast('ok', LANG === 'bn' ? 'Exness ড্যাশবোর্ড ও চার্ট দেখানো হচ্ছে' : 'Exness dashboard & chart opened');
  });
  const connWidget = $('conn-widget');
  if (connWidget) {
    connWidget.style.cursor = 'pointer';
    connWidget.addEventListener('click', async () => {
      if ($('dash').classList.contains('hidden')) {
        if (!(await unlockDashboardPreview())) return;
        dashboardPreview = true;
        connectViewPinned = false;
        $('dash').classList.remove('hidden');
        $('connect-view').classList.add('hidden');
        setTimeout(() => {
          window.dispatchEvent(new Event('resize'));
          if (window.SoftradeXTerminal && window.SoftradeXTerminal.refresh) {
            window.SoftradeXTerminal.refresh();
          }
        }, 60);
      } else {
        dashboardPreview = false;
        connectViewPinned = true;
        $('connect-view').classList.remove('hidden');
        $('dash').classList.add('hidden');
      }
    });
  }
  bindBotUI();
  bindBot2UI();
  bindBot3UI();
  bindBot4UI();
  bindBot5UI();
  bindBot6UI();
  bindBot7UI();
  const watchRefreshBtn = $('watch-refresh-btn');
  if (watchRefreshBtn) {
    // manual only - the dials themselves run off the live WebSocket stream
    watchRefreshBtn.addEventListener('click', () => {
      void fetchMarketWatch(true);
      toast('ok', LANG === 'bn' ? 'মার্কেট ওয়াচ প্রেশার রিফ্রেশ হয়েছে' : 'Market Watch Pressure refreshed');
    });
  }
  $('server-select').addEventListener('change', onServerChange);
  $('server').addEventListener('input', () => { $('server-hint').textContent = t('serverHintCustom'); });
  $('mt5-status').addEventListener('click', (e) => {
    if (e.target && e.target.id === 'mt5-recheck') { e.preventDefault(); void loadConfig(true); }
  });
  $('topbar-disconnect').addEventListener('click', doDisconnect);
  $('pw-toggle').addEventListener('click', () => {
    const p = $('password');
    p.type = p.type === 'password' ? 'text' : 'password';
  });
  $('lang-btn').addEventListener('click', () => {
    LANG = LANG === 'en' ? 'bn' : 'en';
    localStorage.setItem('exdash.lang', LANG);
    applyLang();
  });
  const topbarMuteBtn = $('topbar-mute-btn');
  if (topbarMuteBtn) {
    topbarMuteBtn.addEventListener('click', () => {
      setVoiceMute(!voiceMuted, true);
    });
  }
  const feedVoiceMuteBtn = $('voice-mute-btn');
  if (feedVoiceMuteBtn) {
    feedVoiceMuteBtn.addEventListener('click', () => {
      setVoiceMute(!voiceMuted, true);
    });
  }
  const primeSpeech = () => {
    if ('speechSynthesis' in window && !window.__speechPrimed) {
      window.__speechPrimed = true;
      try {
        const u = new SpeechSynthesisUtterance('');
        window.speechSynthesis.speak(u);
      } catch (e) {}
    }
  };
  window.addEventListener('click', primeSpeech, { once: true });
  window.addEventListener('touchstart', primeSpeech, { once: true });
  document.querySelectorAll('#period-seg .seg-btn').forEach((b) => b.addEventListener('click', () => { period = b.dataset.period; renderStatLabels(); if (S) { renderStats(S); } }));
  $('refresh-btn').addEventListener('click', () => { if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' })); else loadInitial(); });
  $('hist-period').addEventListener('change', (e) => { histPeriod = e.target.value; histSig = ''; extraRows = []; if (S) renderHistory(S); void loadMore(true); });
  // sortable history table headers (th-close + every other column)
  const headRow = $('history-head-row');
  if (headRow && !headRow.dataset.bound) {
    headRow.dataset.bound = '1';
    headRow.addEventListener('click', (e) => {
      const th = e.target && e.target.closest ? e.target.closest('th[data-key]') : null;
      if (!th) return;
      playTactileClick('click');
      const key = th.dataset.key;
      if (histSortKey === key) {
        histSortDir = histSortDir === 'asc' ? 'desc' : 'asc';
      } else {
        histSortKey = key;
        // numbers & time read best biggest/newest first, text reads best A→Z
        histSortDir = (['symbol', 'side', 'comment'].includes(key)) ? 'asc' : 'desc';
      }
      histSig = '';
      if (S) renderHistory(S);
    });
  }
  $('hist-result').addEventListener('change', (e) => { histResult = e.target.value; histSig = ''; if (S) renderHistory(S); });
  $('hist-symbol').addEventListener('input', (e) => { histSymbol = e.target.value.trim(); histSig = ''; if (S) renderHistory(S); });
  $('hist-search').addEventListener('input', (e) => { histSearch = e.target.value.trim(); histSig = ''; if (S) renderHistory(S); });
  $('hist-csv').addEventListener('click', () => {
    const q = histPeriod === 'all' ? 3650 : (histPeriod === 'today' ? 1 : histPeriod);
    window.open(`/api/export.csv?days=${q}${histSymbol ? '&symbol=' + encodeURIComponent(histSymbol) : ''}`, '_blank');
  });
  $('hist-more').addEventListener('click', () => loadMore(false));
  // settings
  const modal = $('settings-modal');
  $('settings-btn').addEventListener('click', () => {
    $('set-poll').value = CFG.poll_interval_ms || 400;
    $('set-hist').value = 2000;
    $('set-days').value = CFG.history_days || 60;
    modal.classList.remove('hidden');
  });
  $('set-close').addEventListener('click', () => modal.classList.add('hidden'));
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.add('hidden'); });
  $('set-save').addEventListener('click', async () => {
    const res = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ poll_interval_ms: Number($('set-poll').value), history_interval_ms: Number($('set-hist').value), history_days: Number($('set-days').value) }) });
    modal.classList.add('hidden');
    toast(res && res.ok ? 'ok' : 'error', res && res.ok ? 'Settings saved' : 'Settings could not be saved');
  });
  $('set-disconnect').addEventListener('click', () => { modal.classList.add('hidden'); void doDisconnect(); });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') modal.classList.add('hidden'); });
}

let CFG = {};

async function loadMore(reset) {
  if (reset) extraRows = [];
  const btn = $('hist-more');
  btn.disabled = true;
  try {
    const days = histPeriod === 'today' ? 1 : (histPeriod === 'all' ? 3650 : Number(histPeriod));
    const offset = extraRows.length;
    const url = `/api/history?days=${days}&limit=500&offset=${offset}` + (histSymbol ? `&symbol=${encodeURIComponent(histSymbol)}` : '');
    const r = await fetch(url);
    const j = await r.json();
    if (j.trades && j.trades.length) {
      extraRows = extraRows.concat(j.trades);
      histSig = '';
      if (S) renderHistory(S);
    }
    btn.classList.toggle('hidden', !(j.count === 500));
  } catch (e) {} finally { btn.disabled = false; }
}

async function loadInitial() {
  try {
    const r = await fetch('/api/state');
    const j = await r.json();
    if (j && Object.keys(j).length) applyState(j);
  } catch (e) {}
}

const SERVER_CUSTOM = '__custom__';

/* Build the Exness server dropdown from GET /api/config */
function buildServerSelect() {
  const sel = $('server-select');
  const groups = CFG.servers || {};
  const real = groups.real || [];
  const trial = groups.trial || [];
  let html = `<option value="" disabled selected>${esc(t('serverPick'))}</option>`;
  if (real.length) {
    html += `<optgroup label="${esc(t('serverReal'))}">` +
      real.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('') + '</optgroup>';
  }
  if (trial.length) {
    html += `<optgroup label="${esc(t('serverTrial'))}">` +
      trial.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('') + '</optgroup>';
  }
  html += `<option value="${SERVER_CUSTOM}">${esc(t('serverOther'))}</option>`;
  sel.innerHTML = html;

  const all = real.concat(trial);
  const suggestions = CFG.known_servers || all;
  $('servers').innerHTML = suggestions.map((s) => `<option value="${esc(s)}">`).join('');

  // restore the last choice (server -> login, saved in the browser)
  const lastServer = CFG.saved_server || localStorage.getItem('exdash.server') || '';
  const lastLogin = CFG.saved_login || localStorage.getItem('exdash.login') || '';
  if (lastLogin) $('login').value = lastLogin;
  if (lastServer) {
    if (all.includes(lastServer)) sel.value = lastServer;
    else { sel.value = SERVER_CUSTOM; $('server').value = lastServer; }
  }
  onServerChange();
}

function onServerChange() {
  const custom = $('server-select').value === SERVER_CUSTOM;
  $('server').classList.toggle('hidden', !custom);
  $('server-hint').textContent = custom ? t('serverHintCustom') : t('serverHint');
  if (custom) $('server').focus();
}

function currentServer() {
  const v = $('server-select').value;
  return (v === SERVER_CUSTOM ? $('server').value : v).trim();
}

/* MT5 terminal availability - shown before the user even tries to log in */
function renderMt5Status() {
  const box = $('mt5-status');
  const terms = CFG.terminals || [];
  const exness = CFG.exness_terminals || [];
  const url = CFG.exness_download || 'https://www.exness.com/metatrader-5/';
  const plain = CFG.mt5_download || 'https://www.metatrader5.com/en/download';
  const links = `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(t('mt5Download'))}</a>` +
    `<a href="${esc(plain)}" target="_blank" rel="noopener noreferrer">${esc(t('mt5DownloadPlain'))}</a>`;
  const recheck = `<a href="#" id="mt5-recheck">${esc(t('mt5Recheck'))}</a>`;
  // any MT5 build can log into an Exness account (the server name is resolved
  // through MetaQuotes' global directory), so one terminal is all we need
  if (terms.length) {
    box.className = 'mt5-status ok';
    box.innerHTML = `<b>✅ ${esc(t('mt5Ready'))}</b><span class="path">${esc(exness[0] || terms[0])}</span>` +
      `<span class="path">${esc(t('mt5ReadySub'))}</span>${recheck}`;
  } else {
    box.className = 'mt5-status warn';
    box.innerHTML = `<b>⚠️ ${esc(t('mt5Missing'))}</b>${links}${recheck}`;
  }
}

/* The Start buttons ship disabled in the HTML so the dashboard can never
   arm a bot before an account is connected and automated trading is
   enabled on the server. */
const BOT_START_BUTTONS = ['bot-start', 'bot2-start', 'bot3-start', 'bot4-start',
  'bot5-start', 'bot6-start', 'bot7-start'];
function syncBotStartButtons(connected) {
  const ready = botsEnabled && (connected !== undefined ? connected : wasConnected);
  BOT_START_BUTTONS.forEach((id) => {
    const btn = $(id);
    if (!btn) return;
    btn.disabled = !ready;
    btn.title = ready ? '' : (botsEnabled
      ? 'Log in to an Exness account to start a bot'
      : 'Automated trading is disabled in config/config.json (bots_enabled: false)');
  });
}

async function loadConfig(silent) {
  let fresh;
  try {
    const r = await fetch('/api/config');
    fresh = await r.json();
  } catch (e) { if (!silent) CFG = {}; return; }
  const prevTerminals = (CFG.terminals || []).join('|');
  CFG = fresh;
  botsEnabled = !!fresh.bots_enabled;
  syncBotStartButtons();

  const sel = $('terminal-path');
  const list = CFG.terminals || [];
  if (!silent || prevTerminals !== list.join('|')) {
    const keep = sel.value;
    sel.innerHTML = '<option value="">Auto-detect (recommended)</option>' +
      list.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
    if (CFG.terminal_path) sel.value = CFG.terminal_path;
    else if ((CFG.exness_terminals || []).length) sel.value = CFG.exness_terminals[0];
    else if (keep) sel.value = keep;
  }
  renderMt5Status();
  if (silent) return;
  buildServerSelect();
  $('remember').checked = CFG.remember_login !== false;
  $('foot-version').textContent = `${CFG.app} v${CFG.version}`;
}

/* ------------------------------------------------------------------- market watch dials
   LIVE STREAM MODE - no timer, no countdown:
   every WebSocket state snapshot carries `market_watch` (recomputed on each engine
   tick), so applyState() patches the machine cards in place: needle sweep, price,
   pressure, VU meter and the per-dial measurement clock all move with the live feed.
   fetchMarketWatch() is only a one-shot seed at boot plus the manual refresh button;
   it is never scheduled. */

async function fetchMarketWatch(force = false) {
  try {
    const url = '/api/market-watch' + (force ? '?force=true' : '');
    const r = await fetch(url);
    if (!r.ok) return;
    const data = await r.json();
    if (!data || !Array.isArray(data.watches)) return;
    marketWatches = data.watches;
    applyMarketWatchState();
    updateMarketTickerTape(marketWatches);
  } catch (e) {
    // silent fail - the live WebSocket stream keeps the dials updated
  }
}

/* Live measurement clock for a dial: local time of the last reading, to 0.01 s.
   The engine streams ticks about 20x per second, so the hundredths really move. */
function watchClockText(tsSec) {
  const d = new Date(Number(tsSec) * 1000);
  if (!Number.isFinite(d.getTime())) return '--:--:--.--';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(Math.floor(d.getMilliseconds() / 10))}`;
}

function watchDominantClass(dominant) {
  return dominant === 'BUYER' ? 'buyer' : (dominant === 'SELLER' ? 'seller' : 'neutral');
}

function watchAnnunciatorText(dominant, buyerPct, sellerPct) {
  if (dominant === 'BUYER') return LANG === 'bn' ? `বায়ার চাপ ${buyerPct}%` : `BUYER ${buyerPct}%`;
  if (dominant === 'SELLER') return LANG === 'bn' ? `সেলার চাপ ${sellerPct}%` : `SELLER ${sellerPct}%`;
  return LANG === 'bn' ? 'নিরপেক্ষ ৫০%' : 'NEUTRAL 50%';
}

function watchFlowMeta(flowRaw) {
  const flow = String(flowRaw || 'SIDEWAYS').toLowerCase();
  const arrow = flow === 'bullish' ? '▲' : (flow === 'bearish' ? '▼' : '►');
  return { flow, arrow };
}

/* Panel header badge - shows the clock of the freshest pushed reading. */
function updateWatchStreamBadge() {
  const badge = $('watch-timer-badge');
  const txt = $('watch-timer-text');
  if (!badge || !txt || !marketWatches.length) return;
  const anyLive = marketWatches.some((w) => w && w.live !== false);
  // offline + public prices = real Yahoo / Binance quotes, not the simulator
  const anyPublic = marketWatches.some((w) => w && w.live === false && w.price_source === 'public');
  const newest = marketWatches.reduce((m, w) => Math.max(m, Number(w.updated_at) || 0), 0);
  badge.classList.add('live');
  badge.classList.toggle('sim', !anyLive && !anyPublic);
  txt.textContent = `${anyLive ? 'LIVE' : (anyPublic ? 'PUBLIC' : 'SIM')} · ${watchClockText(newest)}`;
}

/* Light live frame pushed on every engine tick: dials, ticker and lightweight price displays only. */
function applyMarketWatchFrame(frame) {
  if (!frame || !Array.isArray(frame.watches) || !frame.watches.length) return;
  marketWatches = frame.watches;
  const grid = $('market-watch-grid');
  if (grid && (!grid.querySelector('.watch-card') && !grid.querySelector('.watch-dial-empty'))) {
    renderMarketWatches();
  }
  updateMarketWatchDials();
  updateWatchSummaryPill();
  updateWatchStreamBadge();
  updatePriceCurves();
  updateMarketTickerTape(marketWatches);
  updateFlowStageLivePrices();
}

// The quote stream pushes a frame every 30ms (24+/sec). Patching the dials,
// the ticker and the curve recorder on all of them made this the most
// expensive path on the page. Coalesce: keep only the newest frame and
// process at most one every 100ms - the needle still sweeps smoothly via its
// CSS transition, and recordPriceTick throttles itself to 350ms anyway.
let _watchFramePending = null;
let _watchFrameLast = 0;
let _watchFrameTimer = null;
function queueWatchFrame(frame) {
  if (!frame || !Array.isArray(frame.watches) || !frame.watches.length) return;
  _watchFramePending = frame;
  const elapsed = Date.now() - _watchFrameLast;
  if (elapsed >= 100) {
    _watchFrameLast = Date.now();
    const f = _watchFramePending;
    _watchFramePending = null;
    applyMarketWatchFrame(f);
    return;
  }
  if (_watchFrameTimer) return;
  _watchFrameTimer = setTimeout(() => {
    _watchFrameTimer = null;
    _watchFrameLast = Date.now();
    const f = _watchFramePending;
    _watchFramePending = null;
    if (f) applyMarketWatchFrame(f);
  }, 100 - elapsed);
}

/* Structure changed (boot / different symbol set) -> full machine rebuild. */
function applyMarketWatchState() {
  const grid = $('market-watch-grid');
  if (!grid || !marketWatches.length) return;
  const sig = marketWatches.map((w) => String(w.symbol || '')).join(',') + '#' + flowSymbol();
  if (grid.dataset.sig !== sig || (!grid.querySelector('.watch-card') && !grid.querySelector('.watch-dial-empty'))) {
    grid.dataset.sig = sig;
    renderMarketWatches();
  }
  updateMarketWatchDials();
  updateWatchSummaryPill();
  updateWatchStreamBadge();
  updatePriceCurves();
  updateMarketTickerTape(marketWatches);
  updateAllMarketChips();
  // 01 MARKET hero (price / bid / ask / spread / wires) must react on every live
  // frame, not just the slow poll - otherwise the selected market's price crawls.
  try { renderFlowStage(S || {}); } catch (e) {}
}

/* In-place live patch - keeps the CSS transitions (needle sweep, VU fade) intact. */
function updateMarketWatchDials() {
  const grid = $('market-watch-grid');
  if (!grid) return;
  const activeSym = flowSymbol().toUpperCase();
  const bySym = new Map(marketWatches.map((w) => [String(w.symbol || '').toUpperCase(), w]));
  // one formatted clock per distinct reading - a frame carries the same stamp for every dial
  const clockCache = new Map();
  const clockFor = (ts) => {
    const key = Math.floor((Number(ts) || 0) * 100);
    let text = clockCache.get(key);
    if (text === undefined) { text = watchClockText(ts); clockCache.set(key, text); }
    return text;
  };

  grid.querySelectorAll('.watch-card').forEach((card) => {
    const w = bySym.get(String(card.dataset.sym || '').toUpperCase());
    if (!w) return;

    const buyerPct = Number(w.buyer_pct || 50);
    const sellerPct = Number(w.seller_pct || 50);
    const needleDeg = Number(w.needle_deg || 0);
    const dominant = String(w.dominant || 'NEUTRAL').toUpperCase();
    const domCls = watchDominantClass(dominant);
    const digits = Number(w.digits || 2);
    const { flow, arrow } = watchFlowMeta(w.candle_flow);
    const isLive = w.live !== false;
    const isActive = !!activeSym && String(w.symbol || '').toUpperCase() === activeSym;
    const isOpen = w.market_open !== false;
    // fast frames arrive ~20x/second: only redo the pressure machinery when it moved
    const pressureKey = `${dominant}|${buyerPct}|${sellerPct}|${flow}|${isOpen ? 1 : 0}`;
    const pressureChanged = !card._mwState || card._mwState.pressureKey !== pressureKey;

    // needle sweep (animated by the CSS transition)
    const wrap = card.querySelector('.watch-hand-wrap');
    if (wrap) wrap.style.transform = `rotate(${needleDeg}deg)`;
    const lance = card.querySelector('.watch-hand-lance');
    if (lance) lance.className = `watch-hand-lance ${domCls}`;

    // center hub: symbol, live price, annunciator
    const symEl = card.querySelector('.watch-market-symbol');
    if (symEl) symEl.textContent = w.displayName || w.symbol || '';
    const priceEl = card.querySelector('.watch-price-display');
    if (priceEl) priceEl.textContent = w.ask ? nf(w.ask, digits) : '—';
    if (pressureChanged) {
      const ann = card.querySelector('.watch-annunciator');
      const annTxt = card.querySelector('.annunciator-text');
      if (ann) ann.className = `watch-annunciator ${!isOpen ? 'off' : domCls}`;
      if (annTxt) annTxt.textContent = !isOpen ? 'MARKET OFF' : watchAnnunciatorText(dominant, buyerPct, sellerPct);

      // pressure split + dual LED VU meter
      const buyLabel = card.querySelector('.pressure-label.buyer b');
      const sellLabel = card.querySelector('.pressure-label.seller b');
      if (buyLabel) buyLabel.textContent = `${buyerPct}%`;
      if (sellLabel) sellLabel.textContent = `${sellerPct}%`;
      const rack = card.querySelector('.vu-meter-rack');
      if (rack) {
        rack.title = `Buyer: ${buyerPct}% · Seller: ${sellerPct}%`;
        patchVuSegments(rack.querySelector('.vu-buy'), buyerPct, 'buy');
        patchVuSegments(rack.querySelector('.vu-sell'), sellerPct, 'sell');
      }
      // big buyer / seller readout under the dial
      const bigRack = card.querySelector('.watch-pressure-bigrack');
      if (bigRack) {
        bigRack.title = `Buyer: ${buyerPct}% · Seller: ${sellerPct}%`;
        const bBig = card.querySelector('.wpb-buyer .wpb-val');
        const sBig = card.querySelector('.wpb-seller .wpb-val');
        if (bBig) bBig.textContent = `${buyerPct}%`;
        if (sBig) sBig.textContent = `${sellerPct}%`;
        bigRack.classList.toggle('buyer-lead', dominant === 'BUYER');
        bigRack.classList.toggle('seller-lead', dominant === 'SELLER');
      }
      const flowEl = card.querySelector('.spec-flow');
      if (flowEl) {
        flowEl.className = `spec-v flow-val spec-flow ${flow}`;
        flowEl.textContent = `${arrow} ${flow.toUpperCase()}`;
      }
      // Watch Direction: net move of the pressure window (UP / DOWN / FLAT)
      const dirEl = card.querySelector('.spec-direction');
      if (dirEl) {
        const dir = String(w.direction || 'FLAT').toUpperCase();
        const chg = Number(w.change_pct || 0);
        dirEl.className = `spec-v spec-direction ${dir.toLowerCase()}`;
        dirEl.textContent = `${dir === 'UP' ? '▲' : dir === 'DOWN' ? '▼' : '►'} ${dir}${chg ? ` ${chg > 0 ? '+' : ''}${chg.toFixed(2)}%` : ''}`;
      }
      card._mwState = { pressureKey };
    }

    // telemetry deck: price + spread move with every tick
    const spreadEl = card.querySelector('.spec-spread');
    if (spreadEl && w.spread != null) spreadEl.textContent = w.spread;
    const bidEl = card.querySelector('.spec-bid');
    if (bidEl && w.bid != null) bidEl.textContent = nf(w.bid, Number(w.digits || 2));

    // live measurement clock on the machine face
    const clockEl = card.querySelector('.machine-clock');
    if (clockEl) {
      clockEl.textContent = clockFor(w.updated_at);
      clockEl.title = isLive ? 'Last live reading'
        : (w.price_source === 'public' ? 'Public feed price - Yahoo / Binance (MT5 not connected)' : 'Simulated data - MT5 not connected');
      clockEl.classList.toggle('sim', !isLive);
    }

    // header LED + engaged state + disabled state
    const led = card.querySelector('.machine-led-pulse');
    if (led) led.className = `machine-led-pulse ${!isOpen ? 'off' : domCls}`;
    card.classList.toggle('active-target', isActive);
    card.classList.toggle('market-off', !isOpen);

    const stateBadge = card.querySelector('.machine-engaged-badge, .machine-standby-badge, .machine-off-badge');
    if (stateBadge) {
      if (!isOpen) {
        stateBadge.className = `machine-off-badge${isActive ? ' active-off' : ''}`;
        stateBadge.textContent = 'MARKET OFF';
      } else {
        const want = isActive ? 'machine-engaged-badge' : 'machine-standby-badge';
        if (stateBadge.className !== want) {
          stateBadge.className = want;
          stateBadge.textContent = isActive ? 'ENGAGED' : 'STANDBY';
        }
      }
    }
    const targetBtn = card.querySelector('.spec-target');
    if (targetBtn) {
      if (!isOpen) {
        targetBtn.className = 'spec-v target-btn spec-target disabled';
        targetBtn.textContent = 'OFF';
        targetBtn.title = LANG === 'bn' ? 'মার্কেট বন্ধ' : 'Market closed';
      } else {
        targetBtn.className = `spec-v target-btn spec-target${isActive ? ' active' : ''}`;
        targetBtn.textContent = isActive ? 'ACTIVE' : 'SELECT';
        targetBtn.removeAttribute('title');
      }
    }
  });
}

function patchVuSegments(bar, pct, type) {
  if (!bar) return;
  const segs = bar.querySelectorAll('.vu-seg');
  if (!segs.length) return;
  const active = Math.min(8, Math.max(1, Math.round((Number(pct || 50) / 100) * 8)));
  segs.forEach((seg, i) => {
    seg.classList.toggle('active', type === 'buy' ? i >= 8 - active : i < active);
  });
}

/* Overall buyer/seller pressure summary pill (live). */
function updateWatchSummaryPill() {
  const sumPill = $('watch-summary-pill');
  if (!sumPill || !marketWatches.length) return;
  // the single dial reads its own market, not the average of every market
  const own = findWatchForSymbol(flowSymbol());
  const rows = own ? [own] : marketWatches;
  let totalBuy = 0, totalSell = 0;
  rows.forEach((w) => {
    totalBuy += Number(w.buyer_pct || 50);
    totalSell += Number(w.seller_pct || 50);
  });
  const avgBuy = Math.round(totalBuy / rows.length);
  const avgSell = Math.round(totalSell / rows.length);
  if (avgBuy >= avgSell + 5) {
    sumPill.textContent = `BUYER DOMINANT (${avgBuy}%)`;
    sumPill.style.background = 'rgba(34, 197, 94, 0.2)';
    sumPill.style.color = '#4ade80';
    sumPill.style.borderColor = '#22c55e';
  } else if (avgSell >= avgBuy + 5) {
    sumPill.textContent = `SELLER DOMINANT (${avgSell}%)`;
    sumPill.style.background = 'rgba(239, 68, 68, 0.2)';
    sumPill.style.color = '#f87171';
    sumPill.style.borderColor = '#ef4444';
  } else {
    sumPill.textContent = 'BALANCED (50/50)';
    sumPill.style.background = 'rgba(148, 163, 184, 0.15)';
    sumPill.style.color = '#cbd5e1';
    sumPill.style.borderColor = 'rgba(148, 163, 184, 0.3)';
  }
}

/* ================= MARKET PRICE CURVES ENGINE ================= */
/*
   Selected Market Live Price Curve for Bot 1, Bot 2, Bot 3, Bot 4, Bot 5.
   Rolling price history per symbol (max 300 points), rendered as smooth
   canvas line charts with glowing gradient fill beside each bot's market chips.
*/
const MAX_CURVE_POINTS = 300;
const _priceHistory = {};   // symbol -> [{t, bid, ask}]
let _curvesLastRenderTs = 0;

const _botSelectedSymbols = {
  bot1: 'XAUUSDm',
  bot2: 'BTCUSDTm',
  bot3: 'XAUUSDm',
  bot4: 'XAUUSDm',
  bot5: 'XAUUSDm',
};

function getHistoryKey(sym) {
  // Exness copies carry a broker suffix ("XAUUSDm") while the market-watch
  // feed publishes the clean pair ("XAUUSD"). Both must share ONE rolling
  // history, otherwise the selected market's curve never receives a live
  // tick and keeps drawing its synthetic seed at a stale price forever.
  return String(sym || '').toUpperCase().replace(/M$/, '');
}

function getBasePriceForSym(sym) {
  const s = String(sym || '').toUpperCase();
  if (s.includes('BTC')) return 83750.00;
  if (s.includes('XAU') || s.includes('GOLD')) return 2652.40;
  if (s.includes('EURUSD')) return 1.0854;
  if (s.includes('GBPUSD')) return 1.2914;
  if (s.includes('USDJPY')) return 152.42;
  if (s.includes('OIL')) return 71.35;
  if (s.includes('TEC') || s.includes('100')) return 19885.50;
  if (s.includes('30')) return 42160.00;
  if (s.includes('ETH')) return 2648.50;
  if (s.includes('DXY')) return 104.55;
  return 100.0;
}

function findWatchForSymbol(sym) {
  if (!sym) return null;
  const sUpper = String(sym).toUpperCase();
  const sClean = sUpper.replace(/M$/, '').replace(/\.$/, '');
  
  if (marketWatches && marketWatches.length) {
    let w = marketWatches.find(x => String(x.symbol || '').toUpperCase() === sUpper);
    if (w) return w;
    w = marketWatches.find(x => String(x.symbol || '').toUpperCase().replace(/M$/, '').replace(/\.$/, '') === sClean);
    if (w) return w;
  }

  if (typeof _tickerData !== 'undefined') {
    if (_tickerData.has(sUpper)) return _tickerData.get(sUpper);
    if (_tickerData.has(sClean)) return _tickerData.get(sClean);
  }

  return null;
}

/* Collect a new price tick from live market watches or bot state. */
function recordPriceTick(sym, bid, ask) {
  if (!sym || (!bid && !ask)) return;
  const key = getHistoryKey(sym);
  const cleanKey = key.replace(/M$/, '').replace(/\.$/, '');
  const now = Date.now();
  const b = Number(bid || ask);
  const a = Number(ask || bid);

  [key, cleanKey].forEach(k => {
    if (!_priceHistory[k]) _priceHistory[k] = [];
    const arr = _priceHistory[k];
    if (arr.length && now - arr[arr.length - 1].t < 350) return;
    // A jump this big between two consecutive ticks is never a live move: it is
    // a stale fallback price (the disconnected demo feed seeds gold at 2652)
    // or an overnight session gap. Drop the old points instead of stretching
    // the whole curve - and its HIGH/LOW stats - across both levels.
    const lastP = arr[arr.length - 1];
    if (lastP && lastP.bid > 0 && Math.abs(b - lastP.bid) > lastP.bid * 0.02) arr.length = 0;
    arr.push({ t: now, bid: b, ask: a });
    if (arr.length > MAX_CURVE_POINTS) arr.shift();
  });
}

function ensurePriceHistorySeeded(sym, currentPrice, digits) {
  if (!sym) return;
  const historyKey = getHistoryKey(sym);
  if (!_priceHistory[historyKey] || _priceHistory[historyKey].length < 2) {
    _priceHistory[historyKey] = [];
    // seed from the LIVE quote when we have one - the hardcoded fallback price
    // is only a last resort, otherwise the chart opens stretched to an old level
    const live = findWatchForSymbol(sym) || {};
    const livePrice = Number(live.bid || live.ask || 0);
    const basePrice = currentPrice > 0 ? currentPrice
      : (livePrice > 0 ? livePrice : getBasePriceForSym(sym));
    const d = digits || symbolDefaultDigits(sym);
    const now = Date.now();
    const count = 15;
    for (let i = count; i >= 0; i--) {
      const wobble = (Math.sin(i * 0.8) * 0.0008 + (Math.random() - 0.48) * 0.0006) * basePrice;
      const p = Number((basePrice + wobble).toFixed(d));
      const spread = d > 4 ? 0.00015 : 0.25;
      _priceHistory[historyKey].push({
        t: now - i * 1000,
        bid: p,
        ask: Number((p + spread).toFixed(d)),
      });
    }
  }
}

function getBotSelectedSymbol(botKey) {
  if (botKey === 'flow') {
    // the pipeline curve is the selected market's live curve
    return flowSymbol() || 'XAUUSDm';
  }
  if (botKey === 'bot1') {
    const el = $('bot-symbol-name');
    const txt = el ? el.textContent.split('·')[0].trim() : '';
    if (txt) return txt;
    return (S && S.bot && S.bot.symbol) || _botSelectedSymbols.bot1 || 'XAUUSDm';
  }
  if (botKey === 'bot2') {
    const el = $('bot2-symbol-name');
    const txt = el ? el.textContent.split('·')[0].trim() : '';
    if (txt) return txt;
    return (S && S.bot2 && S.bot2.symbol) || _botSelectedSymbols.bot2 || 'BTCUSDTm';
  }
  if (botKey === 'bot3') {
    const el = $('bot3-symbol-name');
    const txt = el ? el.textContent.split('·')[0].trim() : '';
    if (txt) return txt;
    return (S && S.bot3 && S.bot3.symbol) || _botSelectedSymbols.bot3 || 'XAUUSDm';
  }
  if (botKey === 'bot4') {
    const el = $('bot4-symbol-name');
    const txt = el ? el.textContent.split('·')[0].trim() : '';
    if (txt) return txt;
    return (S && S.bot4 && S.bot4.symbol) || _botSelectedSymbols.bot4 || 'XAUUSDm';
  }
  if (botKey === 'bot5') {
    const el = $('bot5-symbol-name');
    const txt = el ? el.textContent.split('·')[0].trim() : '';
    if (txt) return txt;
    return (S && S.bot5 && S.bot5.symbol) || _botSelectedSymbols.bot5 || 'XAUUSDm';
  }
  return 'XAUUSDm';
}

function setBotCurveSymbol(botKey, sym) {
  if (!botKey || !sym) return;
  _botSelectedSymbols[botKey] = sym;
  const box = $(`${botKey}-curve-box`);
  if (!box) return;

  const card = $(`${botKey}-curve-card`);
  if (!card || card.dataset.sym !== sym) {
    const w = findWatchForSymbol(sym) || { symbol: sym };
    const digits = Number(w.digits !== undefined ? w.digits : symbolDefaultDigits(sym));
    const priceVal = Number(w.ask || w.bid || 0);
    const priceStr = priceVal ? nf(priceVal, digits) : '—';
    const dir = String(w.direction || 'FLAT').toUpperCase();
    const dirCls = dir === 'UP' ? 'up' : dir === 'DOWN' ? 'down' : 'flat';
    const dirIcon = dir === 'UP' ? '▲' : dir === 'DOWN' ? '▼' : '►';
    const chg = Number(w.change_pct || 0);
    const chgStr = chg ? `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%` : '+0.00%';
    const chgCls = chg > 0 ? 'pos' : chg < 0 ? 'neg' : 'flat';

    box.innerHTML = `
      <div class="price-curve-card bot-curve-card" data-sym="${esc(sym)}" id="${botKey}-curve-card">
        <div class="curve-card-header">
          <div class="curve-card-left">
            <span class="curve-symbol-badge" id="${botKey}-curve-badge">${esc(w.displayName || sym)}</span>
            <span class="curve-serial">LIVE CURVE</span>
          </div>
          <div class="curve-card-right">
            <span class="curve-change ${chgCls}" id="${botKey}-curve-chg">${chgStr}</span>
            <span class="curve-direction-pill ${dirCls}" id="${botKey}-curve-dir">${dirIcon} ${dir}</span>
            <span class="curve-live-price" id="${botKey}-curve-price">${priceStr}</span>
          </div>
        </div>
        <div class="curve-chart-wrap bot-curve-chart-wrap" id="${botKey}-curve-wrap">
          <canvas id="${botKey}-curve-canvas"></canvas>
        </div>
        <div class="curve-stats-bar" id="${botKey}-curve-stats">
          <span class="curve-stat"><span class="curve-stat-label">O</span> <span class="curve-stat-value" id="${botKey}-curve-open">—</span></span>
          <span class="curve-stat"><span class="curve-stat-label">H</span> <span class="curve-stat-value high" id="${botKey}-curve-high">—</span></span>
          <span class="curve-stat"><span class="curve-stat-label">L</span> <span class="curve-stat-value low" id="${botKey}-curve-low">—</span></span>
          <span class="curve-stat"><span class="curve-stat-label">C</span> <span class="curve-stat-value" id="${botKey}-curve-close">—</span></span>
          <span class="curve-stat"><span class="curve-stat-label">SPREAD</span> <span class="curve-stat-value" id="${botKey}-curve-spread">—</span></span>
          <span class="curve-stat"><span class="curve-stat-label">TICKS</span> <span class="curve-stat-value" id="${botKey}-curve-ticks">0</span></span>
        </div>
      </div>
    `;

    ensurePriceHistorySeeded(sym, priceVal, digits);
  }

  drawBotCurve(botKey, sym);
}

/* Canvas sizes are cached here. Reading clientWidth/clientHeight from inside
   the live draw path forced a full synchronous style + layout of this very
   large dashboard (~70 ms per curve, eight curves per frame) - that is what
   made the page stutter. The box is measured once when the card is built and
   afterwards only by a ResizeObserver. */
const _curveBoxes = new Map();   // botKey -> { wrap, w, h, dpr }

function curveBoxFor(botKey, wrap) {
  let box = _curveBoxes.get(botKey);
  if (box && box.wrap === wrap) return box;
  if (box && box.ro) { try { box.ro.disconnect(); } catch (e) {} }
  box = { wrap, w: 0, h: 0, dpr: window.devicePixelRatio || 1, ro: null };
  _curveBoxes.set(botKey, box);
  if (window.ResizeObserver) {
    try {
      box.ro = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const rect = entry.contentRect || {};
          box.w = Math.round(rect.width || 0);
          box.h = Math.round(rect.height || 0);
        }
        box.dpr = window.devicePixelRatio || 1;
        const card = $(`${botKey}-curve-card`);
        if (card && card.dataset.sym) drawBotCurve(botKey, card.dataset.sym);
      });
      box.ro.observe(wrap);
    } catch (e) { /* without ResizeObserver the one-time measure below still works */ }
  }
  return box;
}

function drawBotCurve(botKey, sym) {
  const canvas = $(`${botKey}-curve-canvas`);
  if (!canvas) return;
  // the bot cards live in the side drawer: painting them while it is shut is
  // invisible work (the browser also warns about rendering hidden subtrees)
  if (!botsDrawerOpen() && canvas.closest && canvas.closest('#bots-drawer')) return;
  const wrap = canvas.parentElement;
  if (!wrap) return;
  const box = curveBoxFor(botKey, wrap);
  if (!box.w || !box.h) {
    // first draw after the card was built - measured once, then cached
    box.w = wrap.clientWidth;
    box.h = wrap.clientHeight;
    box.dpr = window.devicePixelRatio || 1;
  }
  const W = box.w;
  const H = box.h;
  if (!W || !H) return;

  const dpr = box.dpr || 1;
  const targetW = Math.floor(W * dpr);
  const targetH = Math.floor(H * dpr);
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW;
    canvas.height = targetH;
  }

  const ctx = canvas.getContext('2d');
  ctx.save();
  ctx.scale(dpr, dpr);

  const historyKey = getHistoryKey(sym);
  const points = _priceHistory[historyKey] || [];
  const w = findWatchForSymbol(sym) || {};
  const digits = Number(w.digits !== undefined ? w.digits : symbolDefaultDigits(sym));
  const d = digits > 4 ? 5 : digits;

  const pad = { top: 12, right: 12, bottom: 8, left: 12 };
  const cw = W - pad.left - pad.right;
  const ch = H - pad.top - pad.bottom;

  // Background
  ctx.fillStyle = 'rgba(255, 255, 255, 0.75)';
  ctx.fillRect(0, 0, W, H);

  if (points.length < 2) {
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Syncing candle flow…', W / 2, H / 2);
    ctx.restore();
    return;
  }

  // --- Aggregate ticks into Candlesticks (OHLC) ---
  const numCandles = Math.min(22, Math.max(10, Math.floor(cw / 18)));
  const bucket = Math.max(1, Math.floor(points.length / numCandles));
  const candles = [];
  let minP = Infinity, maxP = -Infinity, maxVol = 1;

  for (let ci = 0; ci < points.length; ci += bucket) {
    const chunk = points.slice(ci, ci + bucket);
    if (!chunk.length) continue;
    const cOpen = chunk[0].bid;
    const cClose = chunk[chunk.length - 1].bid;
    let cHigh = -Infinity, cLow = Infinity;
    for (let pi = 0; pi < chunk.length; pi++) {
      const pBid = chunk[pi].bid;
      if (pBid > cHigh) cHigh = pBid;
      if (pBid < cLow) cLow = pBid;
    }
    const cVol = chunk.length;
    if (cHigh > maxP) maxP = cHigh;
    if (cLow < minP) minP = cLow;
    if (cVol > maxVol) maxVol = cVol;
    candles.push({ open: cOpen, high: cHigh, low: cLow, close: cClose, volume: cVol });
  }

  if (minP === Infinity || maxP === -Infinity || minP === maxP) {
    const fallbackP = points[points.length - 1].bid || 100;
    minP = fallbackP * 0.998;
    maxP = fallbackP * 1.002;
  }

  const range = maxP - minP || 1;
  const margin = range * 0.08;
  const plotMin = minP - margin;
  const plotMax = maxP + margin;
  const priceRange = plotMax - plotMin;

  const candleW = Math.max(4, (cw / candles.length) * 0.62);
  const getX = (i) => pad.left + (i + 0.5) * (cw / candles.length);
  const getY = (p) => pad.top + (1 - (p - plotMin) / priceRange) * ch;

  // Grid lines
  ctx.strokeStyle = 'rgba(37, 99, 235, 0.10)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const gy = pad.top + (i / 3) * ch;
    ctx.beginPath();
    ctx.moveTo(pad.left, gy);
    ctx.lineTo(W - pad.right, gy);
    ctx.stroke();
  }

  // Draw Candlesticks (Wicks + Bodies)
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i];
    const cx = getX(i);
    const isBull = c.close >= c.open;
    const candleColor = isBull ? '#059669' : '#dc2626';
    const bodyTop = getY(Math.max(c.open, c.close));
    const bodyBot = getY(Math.min(c.open, c.close));
    const bodyH = Math.max(2, bodyBot - bodyTop);

    // Wick
    ctx.strokeStyle = candleColor;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(cx, getY(c.high));
    ctx.lineTo(cx, getY(c.low));
    ctx.stroke();

    // Body
    ctx.fillStyle = isBull ? 'rgba(5, 150, 105, 0.80)' : 'rgba(220, 38, 38, 0.80)';
    ctx.fillRect(cx - candleW / 2, bodyTop, candleW, bodyH);

    // Volume bar
    const vH = Math.min(ch * 0.22, (c.volume / maxVol) * (ch * 0.22));
    ctx.fillStyle = isBull ? 'rgba(5, 150, 105, 0.18)' : 'rgba(220, 38, 38, 0.16)';
    ctx.fillRect(cx - candleW / 2, H - pad.bottom - vH, candleW, vH);
  }

  // Draw Spline Glow Curve connecting candle closes
  if (candles.length > 1) {
    const isOverallUp = candles[candles.length - 1].close >= candles[0].open;
    const curveColor = isOverallUp ? '#2563eb' : '#d97706';

    const grad = ctx.createLinearGradient(0, pad.top, 0, H - pad.bottom);
    grad.addColorStop(0, isOverallUp ? 'rgba(37, 99, 235, 0.16)' : 'rgba(217, 119, 6, 0.14)');
    grad.addColorStop(1, 'rgba(0, 0, 0, 0)');

    ctx.beginPath();
    ctx.moveTo(getX(0), getY(candles[0].close));
    for (let i = 1; i < candles.length; i++) {
      const prevX = getX(i - 1);
      const prevY = getY(candles[i - 1].close);
      const curX = getX(i);
      const curY = getY(candles[i].close);
      const midX = (prevX + curX) / 2;
      ctx.bezierCurveTo(midX, prevY, midX, curY, curX, curY);
    }
    ctx.lineTo(getX(candles.length - 1), H - pad.bottom);
    ctx.lineTo(getX(0), H - pad.bottom);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Glowing curve line
    ctx.beginPath();
    ctx.moveTo(getX(0), getY(candles[0].close));
    for (let i = 1; i < candles.length; i++) {
      const prevX = getX(i - 1);
      const prevY = getY(candles[i - 1].close);
      const curX = getX(i);
      const curY = getY(candles[i].close);
      const midX = (prevX + curX) / 2;
      ctx.bezierCurveTo(midX, prevY, midX, curY, curX, curY);
    }
    ctx.strokeStyle = curveColor;
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.strokeStyle = isOverallUp ? 'rgba(37, 99, 235, 0.32)' : 'rgba(217, 119, 6, 0.30)';
    ctx.lineWidth = 4;
    ctx.stroke();
  }

  // Live price pulse & tag
  const lastPoint = points[points.length - 1];
  const lastBid = lastPoint.bid;
  const lastX = getX(candles.length - 1);
  const lastY = getY(lastBid);

  ctx.beginPath();
  ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
  ctx.fillStyle = '#2563eb';
  ctx.fill();

  ctx.beginPath();
  ctx.arc(lastX, lastY, 7, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(37, 99, 235, 0.25)';
  ctx.fill();

  // Price dashed line
  ctx.setLineDash([3, 3]);
  ctx.strokeStyle = 'rgba(37, 99, 235, 0.32)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, lastY);
  ctx.lineTo(W - pad.right, lastY);
  ctx.stroke();
  ctx.setLineDash([]);

  // Price tag badge
  const priceLabel = nf(lastBid, d);
  ctx.font = '8.5px "JetBrains Mono", monospace';
  const lblW = ctx.measureText(priceLabel).width + 8;
  const lblH = 13;
  const lblX = Math.max(pad.left, W - pad.right - lblW - 2);
  const lblY = Math.max(pad.top, Math.min(H - pad.bottom - lblH, lastY - lblH / 2));
  ctx.fillStyle = 'rgba(255, 255, 255, 0.94)';
  ctx.strokeStyle = 'rgba(37, 99, 235, 0.45)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(lblX, lblY, lblW, lblH, 3);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#2563eb';
  ctx.textAlign = 'center';
  ctx.fillText(priceLabel, lblX + lblW / 2, lblY + 9.5);

  ctx.restore();

  // Update header and stats (OHLC + Ticks)
  const lastCandle = candles[candles.length - 1] || {};
  const openEl = $(`${botKey}-curve-open`);
  const highEl = $(`${botKey}-curve-high`);
  const lowEl = $(`${botKey}-curve-low`);
  const closeEl = $(`${botKey}-curve-close`);
  const spreadEl = $(`${botKey}-curve-spread`);
  const ticksEl = $(`${botKey}-curve-ticks`);
  const priceEl = $(`${botKey}-curve-price`);

  if (priceEl) priceEl.textContent = nf(lastBid, d);
  if (openEl) openEl.textContent = lastCandle.open ? nf(lastCandle.open, d) : '—';
  if (highEl) highEl.textContent = maxP !== -Infinity ? nf(maxP, d) : '—';
  if (lowEl) lowEl.textContent = minP !== Infinity ? nf(minP, d) : '—';
  if (closeEl) closeEl.textContent = lastCandle.close ? nf(lastCandle.close, d) : '—';
  if (spreadEl && lastPoint && lastPoint.ask) {
    spreadEl.textContent = nf(Math.abs(lastPoint.ask - lastPoint.bid), d);
  }
  if (ticksEl) ticksEl.textContent = String(points.length);

  const firstBid = points[0].bid;
  const isUp = lastBid >= firstBid;
  const dirPill = $(`${botKey}-curve-dir`);
  if (dirPill) {
    dirPill.className = `curve-direction-pill ${isUp ? 'up' : 'down'}`;
    dirPill.textContent = `${isUp ? '▲' : '▼'} ${isUp ? 'UP' : 'DOWN'}`;
  }

  const chgEl = $(`${botKey}-curve-chg`);
  if (chgEl) {
    const chg = firstBid > 0 ? ((lastBid - firstBid) / firstBid) * 100 : (w.change_pct || 0);
    const chgStr = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
    chgEl.className = `curve-change ${chg > 0 ? 'pos' : chg < 0 ? 'neg' : 'flat'}`;
    chgEl.textContent = chgStr;
  }
}

/* Update all selected bot curves from the latest market telemetry. */
function updatePriceCurves() {
  if (marketWatches && marketWatches.length) {
    marketWatches.forEach((w) => {
      if (w.symbol && (w.bid || w.ask)) {
        recordPriceTick(w.symbol, w.bid || w.ask, w.ask || w.bid);
      }
    });
  }

  const now = Date.now();
  if (now - _curvesLastRenderTs < 200) return;
  _curvesLastRenderTs = now;

  ['bot1', 'bot2', 'bot3', 'bot4', 'bot5', 'flow'].forEach((botKey) => {
    const sym = getBotSelectedSymbol(botKey);
    if (!sym) return;
    const card = $(`${botKey}-curve-card`);
    if (!card || card.dataset.sym !== sym) {
      setBotCurveSymbol(botKey, sym);
    } else {
      drawBotCurve(botKey, sym);
    }
  });
}

/* ================= LIVE ROTATING MARKET TICKER TAPE ENGINE (Market Name + Price Gurbe) ================= */
/*
   Continuous rotating ticker tape running across both Login page and Dashboard.
   Populates Set A and Set B (identical clones) into #market-ticker-track and
   #login-ticker-track for seamless, gapless 360-degree CSS translation loop.
   In-place DOM updates on incoming ticks with green/red flash animations.
*/
const DEFAULT_TICKER_MARKETS = [
  { symbol: 'XAUUSD', displayName: 'XAU/USD (Gold)', ask: 2652.40, bid: 2652.15, digits: 2, change_pct: 0.45, direction: 'UP', spread: 0.25 },
  { symbol: 'EURUSD', displayName: 'EUR/USD', ask: 1.08542, bid: 1.08528, digits: 5, change_pct: 0.16, direction: 'UP', spread: 0.00014 },
  { symbol: 'BTCUSD', displayName: 'BTC/USD (Bitcoin)', ask: 83750.00, bid: 83740.00, digits: 2, change_pct: 1.85, direction: 'UP', spread: 10.0 },
  { symbol: 'GBPUSD', displayName: 'GBP/USD', ask: 1.29145, bid: 1.29130, digits: 5, change_pct: -0.09, direction: 'DOWN', spread: 0.00015 },
  { symbol: 'USDJPY', displayName: 'USD/JPY', ask: 152.420, bid: 152.405, digits: 3, change_pct: 0.26, direction: 'UP', spread: 0.015 },
  { symbol: 'USOIL', displayName: 'US OIL (Crude)', ask: 71.35, bid: 71.30, digits: 2, change_pct: -0.38, direction: 'DOWN', spread: 0.05 },
  { symbol: 'USTEC', displayName: 'NAS100 (Tech)', ask: 19885.50, bid: 19884.00, digits: 2, change_pct: 0.72, direction: 'UP', spread: 1.5 },
  { symbol: 'US30', displayName: 'US30 (Dow Jones)', ask: 42160.00, bid: 42155.00, digits: 2, change_pct: 0.31, direction: 'UP', spread: 5.0 },
  { symbol: 'ETHUSD', displayName: 'ETH/USD (Ethereum)', ask: 2648.50, bid: 2647.20, digits: 2, change_pct: -0.45, direction: 'DOWN', spread: 1.3 },
  { symbol: 'AUDUSD', displayName: 'AUD/USD', ask: 0.65485, bid: 0.65470, digits: 5, change_pct: -0.15, direction: 'DOWN', spread: 0.00015 },
  { symbol: 'GBPJPY', displayName: 'GBP/JPY', ask: 196.820, bid: 196.800, digits: 3, change_pct: 0.18, direction: 'UP', spread: 0.020 },
  { symbol: 'DXY', displayName: 'USD INDEX', ask: 104.55, bid: 104.50, digits: 2, change_pct: 0.12, direction: 'UP', spread: 0.05 },
];

const _tickerData = new Map();
let _tickerInitialized = false;
let _microTicksTimer = null;

function symbolDefaultDigits(sym) {
  const s = String(sym || '').toUpperCase();
  if (s.includes('JPY')) return 3;
  if (s.includes('BTC') || s.includes('XAU') || s.includes('GOLD') || s.includes('OIL') || s.includes('US30') || s.includes('100') || s.includes('TEC') || s.includes('DXY')) return 2;
  return 5;
}

function buildTickerItemHtml(w, copyId, isCompact = false) {
  const sym = String(w.symbol || '').toUpperCase();
  const name = w.displayName || sym;
  const digits = Number(w.digits !== undefined ? w.digits : symbolDefaultDigits(sym));
  const priceVal = Number(w.ask || w.bid || 0);
  const priceStr = priceVal ? nf(priceVal, digits) : '—';
  const dir = String(w.direction || 'FLAT').toUpperCase();
  const dirCls = dir === 'UP' ? 'up' : (dir === 'DOWN' ? 'down' : 'flat');
  const dirIcon = dir === 'UP' ? '▲' : (dir === 'DOWN' ? '▼' : '►');
  const chg = Number(w.change_pct || 0);
  const chgStr = `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`;
  const spread = Number(w.spread || 0);
  const spreadStr = spread > 0 ? (digits > 4 ? (spread * 100000).toFixed(1) : spread.toFixed(1)) : '';
  const isOpen = (typeof isMarketOpen === 'function') ? isMarketOpen(sym) : true;
  const offCls = !isOpen ? ' ticker-off' : '';
  const offPill = !isOpen ? '<span class="ticker-off-pill">OFF</span>' : '';

  if (isCompact) {
    return `
      <div class="login-ticker-item${offCls}" data-sym="${esc(sym)}">
        <span class="login-ticker-sym">${esc(sym)}${offPill}</span>
        <span class="login-ticker-price" id="login-tk-price-${esc(sym)}-${copyId}">${priceStr}</span>
        <span class="login-ticker-chg ${dirCls}" id="login-tk-chg-${esc(sym)}-${copyId}">${dirIcon} ${chgStr}</span>
      </div>
    `;
  }

  return `
    <div class="ticker-item${offCls}" data-sym="${esc(sym)}" onclick="onTickerItemClick('${esc(sym)}')">
      <span class="ticker-sym">${esc(name)}${offPill}</span>
      <span class="ticker-price" id="tk-price-${esc(sym)}-${copyId}">${priceStr}</span>
      <span class="ticker-chg ${dirCls}" id="tk-chg-${esc(sym)}-${copyId}">${dirIcon} ${chgStr}</span>
      ${spreadStr ? `<span class="ticker-spread" id="tk-sp-${esc(sym)}-${copyId}">SP ${spreadStr}</span>` : ''}
    </div>
  `;
}

function initMarketTickerTape() {
  if (_tickerInitialized) return;
  _tickerInitialized = true;

  DEFAULT_TICKER_MARKETS.forEach(m => {
    _tickerData.set(m.symbol.toUpperCase(), { ...m });
  });

  renderMarketTickerDom(true);
  startDisconnectedMicroTicks();

  // Initialize bot curves for Bot 1..5
  ['bot1', 'bot2', 'bot3', 'bot4', 'bot5'].forEach(botKey => {
    const sym = getBotSelectedSymbol(botKey);
    setBotCurveSymbol(botKey, sym);
  });
}

function renderMarketTickerDom(force = false) {
  const topTrack = $('market-ticker-track');
  const loginTrack = $('login-ticker-track');
  const items = Array.from(_tickerData.values());
  if (!items.length) return;

  if (topTrack && (force || !topTrack.children.length)) {
    const setA = items.map(w => buildTickerItemHtml(w, 'a', false)).join('');
    const setB = items.map(w => buildTickerItemHtml(w, 'b', false)).join('');
    topTrack.innerHTML = setA + setB;
  }

  if (loginTrack && (force || !loginTrack.children.length)) {
    const setA = items.map(w => buildTickerItemHtml(w, 'a', true)).join('');
    const setB = items.map(w => buildTickerItemHtml(w, 'b', true)).join('');
    loginTrack.innerHTML = setA + setB;
  }
}

function updateMarketTickerTape(watches) {
  if (!_tickerInitialized) {
    initMarketTickerTape();
  }
  if (!watches || !Array.isArray(watches) || !watches.length) return;

  let hasNew = false;
  watches.forEach(w => {
    if (!w || !w.symbol) return;
    const sym = String(w.symbol).toUpperCase();
    if (!_tickerData.has(sym)) {
      hasNew = true;
    }
  });

  watches.forEach(w => {
    if (!w || !w.symbol) return;
    const sym = String(w.symbol).toUpperCase();
    const existing = _tickerData.get(sym);
    const newPrice = Number(w.ask || w.bid || 0);
    const digits = Number(w.digits !== undefined ? w.digits : (existing ? existing.digits : symbolDefaultDigits(sym)));

    let priceDir = null;
    if (existing && newPrice > 0 && existing.ask > 0) {
      if (newPrice > existing.ask) priceDir = 'up';
      else if (newPrice < existing.ask) priceDir = 'down';
    }

    _tickerData.set(sym, {
      ...(existing || {}),
      ...w,
      symbol: sym,
      displayName: w.displayName || (existing ? existing.displayName : sym),
      ask: newPrice || (existing ? existing.ask : 0),
      bid: Number(w.bid) || (existing ? existing.bid : 0),
      digits,
      direction: w.direction || (priceDir === 'up' ? 'UP' : (priceDir === 'down' ? 'DOWN' : (existing ? existing.direction : 'FLAT'))),
      change_pct: w.change_pct !== undefined ? w.change_pct : (existing ? existing.change_pct : 0),
    });

    if (newPrice > 0) {
      const priceStr = nf(newPrice, digits);
      const chg = Number(w.change_pct !== undefined ? w.change_pct : (existing ? existing.change_pct : 0));
      const chgStr = `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`;
      const dir = String(w.direction || (priceDir === 'up' ? 'UP' : (priceDir === 'down' ? 'DOWN' : (existing ? existing.direction : 'FLAT')))).toUpperCase();
      const dirCls = dir === 'UP' ? 'up' : (dir === 'DOWN' ? 'down' : 'flat');
      const dirIcon = dir === 'UP' ? '▲' : (dir === 'DOWN' ? '▼' : '►');

      ['a', 'b'].forEach(copyId => {
        const pEl = $(`tk-price-${sym}-${copyId}`);
        if (pEl) {
          pEl.textContent = priceStr;
          if (priceDir) {
            pEl.classList.remove('tick-up', 'tick-down');
            pEl.classList.add(priceDir === 'up' ? 'tick-up' : 'tick-down');
            setTimeout(() => { pEl.classList.remove('tick-up', 'tick-down'); }, 700);
          }
        }
        const cEl = $(`tk-chg-${sym}-${copyId}`);
        if (cEl) {
          cEl.className = `ticker-chg ${dirCls}`;
          cEl.textContent = `${dirIcon} ${chgStr}`;
        }

        const lpEl = $(`login-tk-price-${sym}-${copyId}`);
        if (lpEl) {
          lpEl.textContent = priceStr;
          if (priceDir) {
            lpEl.style.color = priceDir === 'up' ? '#4ade80' : '#f87171';
            setTimeout(() => { lpEl.style.color = '#38bdf8'; }, 700);
          }
        }
        const lcEl = $(`login-tk-chg-${sym}-${copyId}`);
        if (lcEl) {
          lcEl.className = `login-ticker-chg ${dirCls}`;
          lcEl.textContent = `${dirIcon} ${chgStr}`;
        }
      });
    }
  });

  const topTrack = $('market-ticker-track');
  if (hasNew && (!topTrack || !topTrack.children.length)) {
    renderMarketTickerDom(true);
  }
}

function startDisconnectedMicroTicks() {
  if (_microTicksTimer) return;
  _microTicksTimer = setInterval(() => {
    // live data from either transport (socket or HTTP polling) beats fake ticks
    if ((wsAlive || httpPollTimer) && wasConnected) return;

    const items = Array.from(_tickerData.values());
    if (!items.length) return;
    const count = 1 + Math.floor(Math.random() * 2);
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(Math.random() * items.length);
      const item = items[idx];
      if (!item || !item.ask) continue;
      const digits = item.digits || 2;
      const step = Math.pow(10, -digits) * (1 + Math.floor(Math.random() * 3));
      const up = Math.random() > 0.48;
      const delta = up ? step : -step;
      const newPrice = Number((item.ask + delta).toFixed(digits));
      const newChg = Number(((item.change_pct || 0) + (up ? 0.01 : -0.01)).toFixed(2));
      recordPriceTick(item.symbol, newPrice, newPrice);
      updateMarketTickerTape([{
        symbol: item.symbol,
        displayName: item.displayName,
        ask: newPrice,
        bid: Number((item.bid + delta).toFixed(digits)),
        digits,
        direction: up ? 'UP' : 'DOWN',
        change_pct: newChg,
      }]);
    }
    updatePriceCurves();
  }, 1600);
}

function onTickerItemClick(sym) {
  if (!sym) return;
  if (!isMarketOpen(sym)) {
    toast('warn', LANG === 'bn' ? `${sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে (Market Closed)` : `${sym} market is currently closed`);
  }
  playTactileClick('click');
  const upSym = sym.toUpperCase();
  document.querySelectorAll(`.ticker-item[data-sym="${upSym}"]`).forEach(el => {
    el.classList.add('active-sym');
    setTimeout(() => el.classList.remove('active-sym'), 1500);
  });

  // clicking a ticker market drives the whole pipeline to that market
  const list = ((S && S.bot5 && S.bot5.ai && S.bot5.ai.symbols) || []).map(s => String(s).toUpperCase());
  if (list.includes(upSym)) void selectFlowMarket(upSym);

  const watchCard = $(`watch-card-${upSym}`);
  const curveCard = $(`curve-card-${upSym}`);
  const target = curveCard || watchCard;
  if (target && !$('dash').classList.contains('hidden')) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.add('active-target');
    setTimeout(() => target.classList.remove('active-target'), 2000);
  }
}

/* ------------------------------------------------------------------- bot 3 recalculate timer */
let bot3RecalcRemaining = 60;
let _bot3RecalcPending = false;
let _bot3LastRecalcDoneTs = 0;

function updateBot3TimerPills(sec) {
  const isBn = LANG === 'bn';
  const timerPill = $('bot3-planned-timer');
  if (timerPill) {
    if (sec <= 0 || _bot3RecalcPending) {
      timerPill.textContent = isBn ? '🔄 হিসাব হচ্ছে...' : '🔄 Recalculating...';
      timerPill.className = 'planned-timer-pill recalculating';
    } else {
      timerPill.textContent = `⏱️ ${sec}s`;
      timerPill.className = 'planned-timer-pill' + (sec <= 10 ? ' urgent' : '');
    }
  }
  const radarTimer = $('bot3-timer');
  if (radarTimer) {
    radarTimer.textContent = `⏱️ ${sec}s`;
    radarTimer.className = 'radar-timer-pill' + (sec <= 10 ? ' urgent' : '');
  }
}

function updateBot3LastUpdated(ts) {
  const el = $('bot3-last-updated');
  if (!el) return;
  const isBn = LANG === 'bn';
  let d = ts ? new Date(ts * 1000) : new Date();
  if (isNaN(d.getTime())) d = new Date();
  const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  el.textContent = (isBn ? 'আপডেট: ' : 'Updated: ') + timeStr;
}

function flashBot3RecalcPulse() {
  const wrap = document.querySelector('.planned-trades-card-wrap');
  if (wrap) {
    wrap.classList.add('recalc-flash');
    setTimeout(() => wrap.classList.remove('recalc-flash'), 900);
  }
}

async function triggerBot3Recalculate(manual = false) {
  if (_bot3RecalcPending) return;
  _bot3RecalcPending = true;
  updateBot3TimerPills(0);
  const btn = $('bot3-recalc-btn');
  if (btn) btn.classList.add('loading');

  try {
    const res = await bot3Post('/api/bot3/recalculate', {});
    if (res && res.ok) {
      if (res.bot3 && S) {
        S.bot3 = res.bot3;
        renderBot3(S);
      }
      bot3RecalcRemaining = 60;
      _bot3LastRecalcDoneTs = Date.now();
      updateBot3LastUpdated(Date.now() / 1000);
      flashBot3RecalcPulse();
      if (manual) {
        toast('ok', LANG === 'bn' ? 'এক্সট্রিম সেটআপ হিসাব সম্পন্ন হয়েছে' : 'Extreme Setups Recalculated');
      }
    }
  } catch (err) {
    console.error('Bot 3 recalculate failed', err);
  } finally {
    _bot3RecalcPending = false;
    if (btn) btn.classList.remove('loading');
    updateBot3TimerPills(bot3RecalcRemaining);
  }
}

function tickBot3RecalcTimer() {
  if (_bot3RecalcPending) return;
  bot3RecalcRemaining--;
  if (bot3RecalcRemaining <= 0) {
    bot3RecalcRemaining = 60;
    void triggerBot3Recalculate(false);
  } else {
    updateBot3TimerPills(bot3RecalcRemaining);
  }
}

/* ------------------------------------------------------------------- 3D Tilt & Cyber Audio Engine */
let currentTiltEl = null;

function resetTilt(el) {
  if (!el) return;
  el.style.setProperty('--rx', '0deg');
  el.style.setProperty('--ry', '0deg');
  el.style.setProperty('--mx', '50%');
  el.style.setProperty('--my', '0%');
  el.classList.remove('is-hovered-3d');
}

function playTactileClick(type = 'click') {
  if (voiceMuted) return;
  try {
    const ctx = window._audioCtx || (window._audioCtx = new (window.AudioContext || window.webkitAudioContext)());
    if (ctx.state === 'suspended') void ctx.resume();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const now = ctx.currentTime;
    if (type === 'start') {
      osc.type = 'sine';
      osc.frequency.setValueAtTime(320, now);
      osc.frequency.exponentialRampToValueAtTime(780, now + 0.08);
      gain.gain.setValueAtTime(0.06, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.08);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(now); osc.stop(now + 0.08);
    } else if (type === 'stop') {
      osc.type = 'sine';
      osc.frequency.setValueAtTime(600, now);
      osc.frequency.exponentialRampToValueAtTime(200, now + 0.09);
      gain.gain.setValueAtTime(0.07, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.09);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(now); osc.stop(now + 0.09);
    } else {
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(800, now);
      osc.frequency.exponentialRampToValueAtTime(300, now + 0.03);
      gain.gain.setValueAtTime(0.025, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.03);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(now); osc.stop(now + 0.03);
    }
  } catch (e) {
    // AudioContext blocked until gesture or unsupported
  }
}

function init3DTiltEngine() {
  const tiltSelector = '.watch-card, .stat-card, .acc-item, .connect-card, .struct-card, .bot2-trend-box';

  document.addEventListener('pointerover', (e) => {
    const el = e.target.closest(tiltSelector);
    if (el) {
      if (currentTiltEl && currentTiltEl !== el) resetTilt(currentTiltEl);
      currentTiltEl = el;
      el.classList.add('is-hovered-3d');
    }
  }, { passive: true });

  document.addEventListener('pointerout', (e) => {
    if (currentTiltEl && (!e.relatedTarget || !currentTiltEl.contains(e.relatedTarget))) {
      resetTilt(currentTiltEl);
      currentTiltEl = null;
    }
  }, { passive: true });

  document.addEventListener('pointermove', (e) => {
    if (!currentTiltEl) return;
    const rect = currentTiltEl.getBoundingClientRect();
    const relX = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const relY = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
    const tiltX = (relY - 0.5) * -12;
    const tiltY = (relX - 0.5) * 12;
    const mx = Math.round(relX * 100);
    const my = Math.round(relY * 100);

    currentTiltEl.style.setProperty('--rx', `${tiltX.toFixed(2)}deg`);
    currentTiltEl.style.setProperty('--ry', `${tiltY.toFixed(2)}deg`);
    currentTiltEl.style.setProperty('--mx', `${mx}%`);
    currentTiltEl.style.setProperty('--my', `${my}%`);
  }, { passive: true });

  // Tactile audio feedback on interactions
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('button, .btn, .chip, .seg-btn, .icon-btn');
    if (!btn) return;
    if (btn.classList.contains('bot-start')) {
      playTactileClick('start');
    } else if (btn.classList.contains('bot-stop') || btn.classList.contains('danger-btn')) {
      playTactileClick('stop');
    } else {
      playTactileClick('click');
    }
  });
}

/* ------------------------------------------------------------------- init */
(function init() {
  const q = new URLSearchParams(location.search);
  if (q.get('lang') === 'bn' || q.get('lang') === 'en') { LANG = q.get('lang'); localStorage.setItem('exdash.lang', LANG); }
  bindUI();
  updateVoiceMuteButtons();
  applyLang();
  renderStatLabels();
  init3DTiltEngine();
  void loadConfig();
  connectWS();
  void loadInitial();
  setInterval(() => { if (!wsAlive && !httpPollTimer) void loadInitial(); }, 3000);
  // while logged out, pick up a freshly installed MT5 terminal automatically
  setInterval(() => { if (!wasConnected) void loadConfig(true); }, 15000);
  setInterval(() => { if (wsAlive) { try { ws.send(JSON.stringify({ type: 'ping' })); } catch (e) {} } }, 20000);
  // Half-dead socket watchdog: a sleeping machine / dropped NAT entry does not
  // fire onclose, so a socket silent for several ping cycles is recycled.
  setInterval(() => {
    if (!wsAlive || !lastServerMsgAt) return;
    if (Date.now() - lastServerMsgAt > 60000) {
      try { ws.close(); } catch (e) {}
    }
  }, 15000);
  // live countdown ticker for Bot 2 2.5-second trend check
  setInterval(tickBot2TrendCountdown, 250);
  // live 1-second countdown ticker for Bot 3 60-second extreme setups recalculation
  setInterval(tickBot3RecalcTimer, 1000);
  // live 1-second countdown ticker for Bot 5 next news event
  setInterval(tickBot5Countdown, 1000);
  // the session clock ticks locally so no countdown ever waits for a push
  setInterval(tickSessionsClock, 1000);
  // the AI panel's "updated Ns ago" line ticks locally between pushes
  setInterval(tickAiAge, 1000);
  // Market Pressure Watch Dials: NO ticker at all. One seed fetch here, then every
  // engine-tick WebSocket snapshot patches the dials in place (live time included).
  initFlowUI();
  initMarketTickerTape();
  void fetchMarketWatch();
  // live trade flow balance: our side rides the engine ticks, the whole world
  // (Binance public data) refreshes every 10 s
  void pollFlowBalanceWorld();
  setInterval(pollFlowBalanceWorld, 10000);
  // Market Pressure Watch Dials: NO timer. The live WebSocket state snapshot
  // (every engine tick) patches the dials in place. No setInterval needed.
  /* ==========================================================================
     ORDER FLOW BRIDGE: 02 MARKET PRESSURE WATCH DIALS
     Streams live order tape buyer vs seller pressure directly to Stage 05 Dials.
     ========================================================================== */
  window.applyOrderFlowToWatchDials = function(stats) {
    if (!stats) return;
    const sym = String(stats.symbol || flowSymbol() || 'GOLD').trim();
    const buyPct = Math.max(1, Math.min(99, Math.round(Number(stats.buyPct) || 50)));
    const sellPct = 100 - buyPct;
    // Needle degrees: -65deg is BUY 100%, 0deg is 50/50, +65deg is SELL 100%
    const needleDeg = Number((((50 - buyPct) / 50) * 65).toFixed(1));
    const dominant = buyPct >= 55 ? 'BUYER' : (sellPct >= 55 ? 'SELLER' : 'NEUTRAL');
    const domCls = dominant.toLowerCase();
    const flow = buyPct > 52 ? 'bullish' : (sellPct > 52 ? 'bearish' : 'sideways');

    // Update in-memory marketWatches item
    let w = typeof findWatchForSymbol === 'function' ? findWatchForSymbol(sym) : null;
    if (!w && marketWatches && marketWatches.length > 0) {
      w = marketWatches[0];
    }
    if (w) {
      w.buyer_pct = buyPct;
      w.seller_pct = sellPct;
      w.needle_deg = needleDeg;
      w.dominant = dominant;
      w.candle_flow = flow;
      w.direction = buyPct >= 50 ? 'UP' : 'DOWN';
      if (stats.lastPrice > 0) {
        w.ask = stats.lastPrice;
        w.bid = stats.lastPrice - (w.spread || 0.1);
      }
      w.updated_at = Date.now() / 1000;
      w.live = true;
    }

    // Direct DOM Patch for instantaneous 60fps needle, VU-meter, and pressure readouts
    const grid = $('market-watch-grid');
    if (grid) {
      const card = grid.querySelector('.watch-card');
      if (card) {
        // 1. Rotate Needle
        const wrap = card.querySelector('.watch-hand-wrap');
        if (wrap) wrap.style.transform = `rotate(${needleDeg}deg)`;
        const lance = card.querySelector('.watch-hand-lance');
        if (lance) lance.className = `watch-hand-lance ${domCls}`;

        // 2. Annunciator Lamp & Text
        const ann = card.querySelector('.watch-annunciator');
        const annTxt = card.querySelector('.annunciator-text');
        if (ann) ann.className = `watch-annunciator ${domCls}`;
        if (annTxt) {
          annTxt.textContent = buyPct >= 72
            ? (LANG === 'bn' ? 'তীব্র বায়ার চাপ (BUY FLOW)' : 'STRONG BUYER FLOW')
            : (buyPct >= 55
              ? (LANG === 'bn' ? `বায়ার চাপ ${buyPct}%` : `BUYER PRESSURE ${buyPct}%`)
              : (sellPct >= 72
                ? (LANG === 'bn' ? 'তীব্র সেলার চাপ (SELL FLOW)' : 'STRONG SELLER FLOW')
                : (sellPct >= 55
                  ? (LANG === 'bn' ? `সেলার চাপ ${sellPct}%` : `SELLER PRESSURE ${sellPct}%`)
                  : (LANG === 'bn' ? 'অর্ডার ফ্লো ব্যালান্সড ৫০%' : 'ORDER FLOW BALANCED 50%'))));
        }

        // 3. Live Price Display
        if (stats.lastPrice > 0) {
          const priceEl = card.querySelector('.watch-price-display');
          if (priceEl) priceEl.textContent = Number(stats.lastPrice).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          const bidEl = card.querySelector('.spec-bid');
          if (bidEl) bidEl.textContent = Number(stats.lastPrice).toFixed(2);
        }

        // 4. Pressure Split Header Labels
        const buyLabel = card.querySelector('.pressure-label.buyer b');
        const sellLabel = card.querySelector('.pressure-label.seller b');
        if (buyLabel) buyLabel.textContent = `${buyPct}%`;
        if (sellLabel) sellLabel.textContent = `${sellPct}%`;

        // 5. Dual 16-Segment LED VU-Meter
        const rack = card.querySelector('.vu-meter-rack');
        if (rack) {
          rack.title = `Buyer: ${buyPct}% · Seller: ${sellPct}%`;
          if (typeof patchVuSegments === 'function') {
            patchVuSegments(rack.querySelector('.vu-buy'), buyPct, 'buy');
            patchVuSegments(rack.querySelector('.vu-sell'), sellPct, 'sell');
          }
        }

        // 6. Big Buyer vs Seller Readout Box
        const bBig = card.querySelector('.wpb-buyer .wpb-val');
        const sBig = card.querySelector('.wpb-seller .wpb-val');
        if (bBig) bBig.textContent = `${buyPct}%`;
        if (sBig) sBig.textContent = `${sellPct}%`;

        const bigRack = card.querySelector('.watch-pressure-bigrack');
        if (bigRack) {
          bigRack.title = `Buyer: ${buyPct}% · Seller: ${sellPct}%`;
          bigRack.classList.toggle('buyer-lead', dominant === 'BUYER');
          bigRack.classList.toggle('seller-lead', dominant === 'SELLER');
        }

        // 7. Direction and Flow Specs
        const flowEl = card.querySelector('.spec-flow');
        if (flowEl) {
          const arrow = buyPct >= 50 ? '▲' : '▼';
          flowEl.className = `spec-v flow-val spec-flow ${flow}`;
          flowEl.textContent = `${arrow} ${flow.toUpperCase()}`;
        }
        const dirEl = card.querySelector('.spec-direction');
        if (dirEl) {
          const dir = buyPct >= 50 ? 'UP' : 'DOWN';
          dirEl.className = `spec-v spec-direction ${dir.toLowerCase()}`;
          dirEl.textContent = `${dir === 'UP' ? '▲' : '▼'} ${dir}`;
        }

        // 8. Live Clock on Machine Face
        const clockEl = card.querySelector('.machine-clock');
        if (clockEl) {
          clockEl.textContent = watchClockText(Date.now() / 1000);
          clockEl.classList.remove('sim');
        }
      }
    }

    // 9. Update Summary Pill in Stage 05 Header
    if (typeof updateWatchSummaryPill === 'function') {
      try { updateWatchSummaryPill(); } catch (e) {}
    }
  };

  /* ==========================================================================
     ORDER FLOW BRIDGE: 03 FULL AI MARKET ANALYSIS (live BUY / SELL)
     Feeds real-time execution order flow into AI Market Analysis & computes
     live Direction (BUY / SELL / BALANCED), Confidence %, Targets, and Timeframes.
     ========================================================================== */
  window.applyOrderFlowToAiMarket = function(stats) {
    if (!stats) return;
    const sym = String(stats.symbol || flowSymbol() || 'GOLD').trim();
    const buyPct = Math.max(1, Math.min(99, Math.round(Number(stats.buyPct) || 50)));
    const sellPct = 100 - buyPct;
    const price = Number(stats.lastPrice) > 0 ? Number(stats.lastPrice) : 4168.20;
    const digits = sym.includes('EUR') || sym.includes('GBP') ? 5 : (sym.includes('JPY') ? 3 : 2);

    // Compute live Direction & Confidence from order flow tape
    let verdict = 'BALANCED';
    let sideClass = 'mixed';
    let arrow = '◆';
    let sideText = LANG === 'bn' ? 'ব্যালান্সড (অপেক্ষা করুন)' : 'BALANCED (WAIT)';
    let confidence = 50;

    if (buyPct >= 65) {
      verdict = buyPct >= 75 ? 'STRONG_BUY' : 'BUY';
      sideClass = 'buy';
      arrow = '▲';
      sideText = buyPct >= 75
        ? (LANG === 'bn' ? 'তীব্র বাই (STRONG BUY)' : 'STRONG BUY')
        : (LANG === 'bn' ? 'বাই (BUY)' : 'BUY');
      confidence = Math.min(98, Math.max(55, Math.round(buyPct + (Math.abs(stats.netDelta || 0) > 50000 ? 5 : 0))));
    } else if (sellPct >= 65) {
      verdict = sellPct >= 75 ? 'STRONG_SELL' : 'SELL';
      sideClass = 'sell';
      arrow = '▼';
      sideText = sellPct >= 75
        ? (LANG === 'bn' ? 'তীব্র সেল (STRONG SELL)' : 'STRONG SELL')
        : (LANG === 'bn' ? 'সেল (SELL)' : 'SELL');
      confidence = Math.min(98, Math.max(55, Math.round(sellPct + (Math.abs(stats.netDelta || 0) > 50000 ? 5 : 0))));
    } else if (buyPct > 52) {
      verdict = 'BUY';
      sideClass = 'buy';
      arrow = '▲';
      sideText = LANG === 'bn' ? 'বাই (BUY)' : 'BUY';
      confidence = Math.round(52 + (buyPct - 50) * 1.6);
    } else if (sellPct > 52) {
      verdict = 'SELL';
      sideClass = 'sell';
      arrow = '▼';
      sideText = LANG === 'bn' ? 'সেল (SELL)' : 'SELL';
      confidence = Math.round(52 + (sellPct - 50) * 1.6);
    }

    // Dynamic targets & levels
    const atrEst = price * (sym.includes('BTC') ? 0.008 : 0.0035);
    const upDist = atrEst * (buyPct / 50);
    const downDist = atrEst * (sellPct / 50);
    const upPrice = price + upDist;
    const downPrice = price - downDist;
    const upMoney = upDist * 10;
    const downMoney = -downDist * 10;
    const levelUp = price + (atrEst * 1.65);
    const levelDown = price - (atrEst * 1.65);

    // Update in-memory aiBoard
    if (!aiBoard) {
      aiBoard = {
        symbol: sym,
        timeframe: 'M5',
        price: price,
        digits: digits,
        source: 'order_flow',
        updated: Date.now() / 1000,
        combined: {},
        target: {},
        rows: []
      };
    }
    aiBoard.symbol = sym;
    aiBoard.price = price;
    aiBoard.digits = digits;
    aiBoard.updated = Date.now() / 1000;
    aiBoard.combined = {
      verdict: sideClass === 'buy' ? 'BUY' : (sideClass === 'sell' ? 'SELL' : 'MIXED'),
      confidence: confidence,
      reasons: [
        `Order Flow: ${buyPct}% Buyers / ${sellPct}% Sellers`,
        `Net Delta: ${stats.netDelta >= 0 ? '+' : ''}$${Math.abs(stats.netDelta || 0).toLocaleString()}`,
        `Tape Speed: ${stats.speed || '0.0'} tx/s`
      ]
    };
    aiBoard.target = {
      ready: true,
      primary_side: sideClass === 'sell' ? 'SELL' : 'BUY',
      up_price: upPrice,
      down_price: downPrice,
      up_money: upMoney,
      down_money: downMoney,
      level_up: levelUp,
      level_down: levelDown
    };

    // 1. Update #ai-verdict (The prominent one-line AI answer)
    const verEl = $('ai-verdict');
    if (verEl) {
      const deltaSign = (stats.netDelta || 0) >= 0 ? '+' : '';
      const deltaStr = `${deltaSign}$${Math.abs(stats.netDelta || 0).toLocaleString()}`;
      verEl.innerHTML = `
        <span class="ai-side ${sideClass}">${arrow} ${sideText}</span>
        <b>${confidence}%</b> ${LANG === 'bn' ? 'কনফিডেন্স' : 'Confidence'} · 
        ${LANG === 'bn' ? 'অর্ডার ফ্লো' : 'Order Flow'} <b>${buyPct}% BUY / ${sellPct}% SELL</b> · 
        ${LANG === 'bn' ? 'প্রাইস' : 'Price Now'} <b>${price.toFixed(digits)}</b>
        <span class="ai-why" title="Traders Order Flow Terminal Auto-Analysis">
          ${LANG === 'bn' ? 'কারণ' : 'Why'}: ${stats.speed} tx/s গতি · ডেল্টা ${deltaStr}
        </span>
      `;
    }

    // 2. Update #ai-targets (Price targets and travel corridors)
    const tgtEl = $('ai-targets');
    if (tgtEl) {
      const upRole = sideClass === 'buy' ? (LANG === 'bn' ? 'টার্গেট' : 'Target') : (LANG === 'bn' ? 'রিস্ক' : 'Risk');
      const downRole = sideClass === 'sell' ? (LANG === 'bn' ? 'টার্গেট' : 'Target') : (LANG === 'bn' ? 'রিস্ক' : 'Risk');
      tgtEl.innerHTML = `
        <span class="ai-target up" title="${LANG === 'bn' ? 'আপ টার্গেট' : 'Up Target'}">
          <b>▲ ${LANG === 'bn' ? 'আপ' : 'UP'} ${upPrice.toFixed(digits)}</b>
          <small>+$${Math.abs(upMoney).toFixed(1)} · ${upRole}</small>
        </span>
        <span class="ai-target down" title="${LANG === 'bn' ? 'ডাউন টার্গেট' : 'Down Target'}">
          <b>▼ ${LANG === 'bn' ? 'ডাউন' : 'DOWN'} ${downPrice.toFixed(digits)}</b>
          <small>-$${Math.abs(downMoney).toFixed(1)} · ${downRole}</small>
        </span>
        <span class="ai-target level">
          <b>${LANG === 'bn' ? 'লেভেলস' : 'Levels'}</b>
          <small>${levelUp.toFixed(digits)} / ${levelDown.toFixed(digits)}</small>
        </span>
      `;
    }

    // 3. Update #ai-tf-tabs (Timeframe buttons: 1m, 5m, 15m, 1h, 4h, 1Day)
    const tabs = $('ai-tf-tabs');
    if (tabs) {
      const TFS = [
        { k: 'M1', lbl: '1m', v: sideClass === 'buy' ? 'BUY' : (sideClass === 'sell' ? 'SELL' : 'MIXED') },
        { k: 'M5', lbl: '5m', v: sideClass === 'buy' ? 'BUY' : (sideClass === 'sell' ? 'SELL' : 'MIXED') },
        { k: 'M15', lbl: '15m', v: sideClass === 'buy' ? 'BUY' : (sideClass === 'sell' ? 'SELL' : 'MIXED') },
        { k: 'H1', lbl: '1h', v: buyPct >= 58 ? 'BUY' : (sellPct >= 58 ? 'SELL' : 'MIXED') },
        { k: 'H4', lbl: '4h', v: buyPct >= 62 ? 'BUY' : (sellPct >= 62 ? 'SELL' : 'MIXED') },
        { k: 'D1', lbl: '1Day', v: buyPct >= 65 ? 'BUY' : (sellPct >= 65 ? 'SELL' : 'MIXED') }
      ];
      tabs.innerHTML = TFS.map(item => {
        const itemArrow = item.v === 'BUY' ? '▲' : (item.v === 'SELL' ? '▼' : '◆');
        const itemSide = item.v === 'BUY' ? 'buy' : (item.v === 'SELL' ? 'sell' : 'mixed');
        const on = (aiBoard.timeframe || 'M5') === item.k ? ' sel' : '';
        return `<span class="ai-tab ${itemSide}${on}" data-tf="${item.k}">${item.lbl} ${itemArrow}</span>`;
      }).join('');
    }

    // 4. Update #ai-updated status age
    const updEl = $('ai-updated');
    if (updEl) {
      updEl.textContent = LANG === 'bn'
        ? `আপডেট: ০ সেকেন্ড আগে · TRADERS ORDER FLOW টার্মিনাল লাইভ অটো-আপডেট`
        : `Updated: 0s ago · TRADERS ORDER FLOW TERMINAL Live Auto-Update`;
    }

    // 5. Update Timeframe Detail Grid (#ai-grid)
    const grid = $('ai-grid');
    if (grid && (!grid.children.length || grid.querySelector('.ai-empty'))) {
      const TFS = [
        { k: 'M1', name: '1m', tfConf: confidence },
        { k: 'M5', name: '5m', tfConf: confidence },
        { k: 'M15', name: '15m', tfConf: Math.max(50, confidence - 3) },
        { k: 'H1', name: '1h', tfConf: Math.max(50, confidence - 6) },
        { k: 'H4', name: '4h', tfConf: Math.max(50, confidence - 9) },
        { k: 'D1', name: '1Day', tfConf: Math.max(50, confidence - 12) }
      ];
      grid.innerHTML = TFS.map(tfItem => {
        const tfVerdict = sideClass === 'buy' ? 'BUY' : (sideClass === 'sell' ? 'SELL' : 'MIXED');
        const tfArrow = sideClass === 'buy' ? '▲' : (sideClass === 'sell' ? '▼' : '◆');
        const selClass = (aiBoard.timeframe || 'M5') === tfItem.k ? ' sel' : '';
        return `
          <div class="ai-row ${sideClass}${selClass}" data-key="${tfItem.k}">
            <div class="ai-row-top">
              <span class="ai-tf-name">${tfItem.name}</span>
              <span class="sess-verdict ${sideClass}">${tfArrow} ${tfVerdict}</span>
              <span class="ai-conf">${tfItem.tfConf}%</span>
            </div>
            <div class="ai-row-mid">
              <span>Order Flow</span>
              <span>Speed ${stats.speed || '0.0'} tx/s</span>
              <span>Delta ${stats.netDelta >= 0 ? '+' : ''}$${Math.abs(stats.netDelta || 0).toFixed(0)}</span>
            </div>
            <div class="ai-row-up">▲ UP ${upPrice.toFixed(digits)} <small>+$${Math.abs(upMoney).toFixed(1)}</small></div>
            <div class="ai-row-down">▼ DOWN ${downPrice.toFixed(digits)} <small>-$${Math.abs(downMoney).toFixed(1)}</small></div>
            <div class="ai-row-note">Traders Order Flow Terminal · ${buyPct}% Buyers / ${sellPct}% Sellers</div>
          </div>
        `;
      }).join('');
    } else if (grid) {
      grid.querySelectorAll('.ai-row').forEach(rowEl => {
        const tfVerdict = sideClass === 'buy' ? 'BUY' : (sideClass === 'sell' ? 'SELL' : 'MIXED');
        const tfArrow = sideClass === 'buy' ? '▲' : (sideClass === 'sell' ? '▼' : '◆');
        rowEl.className = `ai-row ${sideClass}${rowEl.classList.contains('sel') ? ' sel' : ''}`;
        const verdEl = rowEl.querySelector('.sess-verdict');
        if (verdEl) {
          verdEl.className = `sess-verdict ${sideClass}`;
          verdEl.textContent = `${tfArrow} ${tfVerdict}`;
        }
        const confEl = rowEl.querySelector('.ai-conf');
        if (confEl) confEl.textContent = `${confidence}%`;
        const upEl = rowEl.querySelector('.ai-row-up');
        if (upEl) upEl.innerHTML = `▲ UP ${upPrice.toFixed(digits)} <small>+$${Math.abs(upMoney).toFixed(1)}</small>`;
        const downEl = rowEl.querySelector('.ai-row-down');
        if (downEl) downEl.innerHTML = `▼ DOWN ${downPrice.toFixed(digits)} <small>-$${Math.abs(downMoney).toFixed(1)}</small>`;
        const noteEl = rowEl.querySelector('.ai-row-note');
        if (noteEl) noteEl.textContent = `Traders Order Flow: ${buyPct}% Buyers / ${sellPct}% Sellers · Live`;
      });
    }
  };

  window.addEventListener('resize', () => {
    ['bot1', 'bot2', 'bot3', 'bot4', 'bot5', 'flow'].forEach(botKey => {
      const sym = getBotSelectedSymbol(botKey);
      drawBotCurve(botKey, sym);
    });
  });
})();
