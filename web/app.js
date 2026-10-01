/* ==========================================================================
   Exness Trading Dashboard - frontend
   Plain JS (no build step) + WebSocket. Only changed DOM nodes are touched,
   so updates stay smooth even at 10 messages/second.
   ========================================================================== */
'use strict';

/* ------------------------------------------------------------- utilities */
const $ = (id) => document.getElementById(id);
const el = (sel, root = document) => root.querySelector(sel);

const nf = (v, d = 2) => {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
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
    watchTitle: 'Market Pressure Watch Dials',
    watchSubtitle: 'Live candle volume & buyer/seller pressure gauge (streamed - no refresh timer)',
    botTitle: 'Auto Trading', botLot: 'Lot size', botGap: 'Gap ($)', botSL: 'SL ($)',
    bot2Title: 'Auto Trading (Candle & Structure)', bot2MaxTrades: 'Max Trades', bot2TP: 'TP ($)', bot2SL: 'SL ($)',
    bot2BlockStart: 'First block (s)', bot2BlockEnd: 'Last block (s)', bot2BlockMidFrom: 'Mid from (s)', bot2BlockMidTo: 'Mid to (s)',
    bot2BlockHint: '1-minute candle time-window: no trade in the first N seconds, the last N seconds, or the middle window - values you enter are used exactly.',
    bot2Trend: 'Trend',
    bot2CheckTrend: 'Check Trend',
    bot2TrendSystemTitle: '2.5s Trend System',
    bot2TrendDetailsFooter: '⏱️ Automatic 2.5-second trend analysis: executes trades in verified trend direction',
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
    botSymbol: 'Market', botPickSymbol: 'Pick a gold symbol…',
    botMarketSwitched: 'Market switched to', botMarketNow: 'Selected market',
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
    advanced: 'Advanced', terminal: 'MT5 terminal (terminal64.exe)',
    terminalHint: 'Leave on "Auto-detect" unless you have several terminals installed.',
    remember: 'Remember login + server on this PC',
    connectSub: 'Enter your Exness login, password and server. You only reach the dashboard after a successful login.',
    badgeReal: 'LIVE · REAL', badgeDemo: 'DEMO ACCOUNT', badgeContest: 'CONTEST ACCOUNT',
    badgeEa: 'EA BRIDGE', loggedOut: 'LOGGED OUT', offline: 'OFFLINE',
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
    watchTitle: 'মার্কেট প্রেশার ওয়াচ ডায়াল',
    watchSubtitle: 'রিয়েল-টাইম ক্যান্ডেল ভলিউম ও বায়ার/সেলার চাপের লাইভ ওয়াচ ডায়াল',
    botTitle: 'অটো ট্রেডিং', botLot: 'লট সাইজ', botGap: 'গ্যাপ ($)', botSL: 'SL ($)',
    bot2Title: 'অটো ট্রেডিং (ক্যান্ডেল ও স্ট্রাকচার)', bot2MaxTrades: 'সর্বোচ্চ রানিং ট্রেড', bot2TP: 'TP ($)', bot2SL: 'SL ($)',
    bot2BlockStart: 'শুরুতে ব্লক (সেকেন্ড)', bot2BlockEnd: 'শেষে ব্লক (সেকেন্ড)', bot2BlockMidFrom: 'মাঝের উইন্ডো শুরু (সেকেন্ড)', bot2BlockMidTo: 'মাঝের উইন্ডো শেষ (সেকেন্ড)',
    bot2BlockHint: '১-মিনিট ক্যান্ডেলের সময়-উইন্ডো: শুরুর N সেকেন্ড, শেষের N সেকেন্ড এবং মাঝের উইন্ডোতে ট্রেড নেবে না — আপনি যে মান দেবেন সেটাই ব্যবহার হবে।',
    bot2Trend: 'Trend',
    bot2CheckTrend: 'ট্রেন্ড চেক',
    bot2TrendSystemTitle: '২.৫-সেকেন্ড ট্রেন্ড সিস্টেম',
    bot2TrendDetailsFooter: '⏱️ প্রতি ২.৫ সেকেন্ড পর পর স্বয়ংক্রিয়ভাবে ট্রেন্ড চেক হচ্ছে ও সেই ট্রেন্ডে ট্রেড নেওয়া হচ্ছে',
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
    botSymbol: 'মার্কেট', botPickSymbol: 'গোল্ড সিম্বল বাছুন…',
    botMarketSwitched: 'মার্কেট বদলানো হলো', botMarketNow: 'নির্বাচিত মার্কেট',
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
    advanced: 'অ্যাডভান্সড', terminal: 'MT5 টার্মিনাল (terminal64.exe)',
    terminalHint: 'একাধিক টার্মিনাল ইনস্টল না থাকলে "Auto-detect" ই রাখুন।',
    remember: 'এই পিসিতে লগইন + সার্ভার মনে রাখুন',
    connectSub: 'Exness লগইন, পাসওয়ার্ড আর সার্ভার দিন। সফল লগইন ছাড়া ড্যাশবোর্ডে ঢুকতে পারবেন না।',
    badgeReal: 'লাইভ · রিয়েল', badgeDemo: 'ডেমো অ্যাকাউন্ট', badgeContest: 'কনটেস্ট অ্যাকাউন্ট',
    badgeEa: 'EA ব্রিজ', loggedOut: 'লগআউট', offline: 'অফলাইন',
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
}

/* --------------------------------------------------------------- state */
let S = null;                 // latest snapshot from the backend
let ws = null;
let wsRetry = 0;
let wsAlive = false;
let wasConnected = false;
let period = 'today';
let histPeriod = '7';
let histResult = 'all';
let histSymbol = '';
let histSearch = '';
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
let feedInitialized = false;

/* -------------------------------------------------------------- websocket */
let wsReconnectTimer = null;

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
    $('foot-ws').textContent = 'socket: live';
    $('foot-ws').style.color = '#86efac';
    sock.send(JSON.stringify({ type: 'refresh' }));
  };
  sock.onmessage = (ev) => {
    if (ws !== sock) return;
    let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === 'state') { applyState(msg.data); }
    else if (msg.type === 'watch') { applyMarketWatchFrame(msg.data); }
    else if (msg.type === 'notify') { toast(msg.data.kind, msg.data.message); }
  };
  sock.onclose = () => {
    if (ws !== sock) return;   // stale socket - a newer one already took over
    wsAlive = false;
    $('foot-ws').textContent = 'socket: reconnecting…';
    $('foot-ws').style.color = '';
    scheduleReconnect();
  };
  sock.onerror = () => { try { sock.close(); } catch (e) {} };
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;   // one pending reconnect, never a chain of them
  wsRetry = Math.min(wsRetry + 1, 12);
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
    connectWS();
  }, Math.min(400 * wsRetry, 4000));
}

/* ------------------------------------------------------------ state apply */
function applyState(data) {
  if (!data) return;
  const prev = S;
  S = data;
  const connected = !!data.connected || data.status === 'connected' || (data.mode === 'ea');
  if (!connected && wasConnected) fullResetUI();
  wasConnected = connected;
  renderConnection(data);
  if (connected) {
    $('connect-view').classList.add('hidden');
    $('dash').classList.remove('hidden');
  } else {
    $('connect-view').classList.remove('hidden');
    $('dash').classList.add('hidden');
  }
  if (data.error && data.status === 'error') showConnectError(data.error);
  renderAccount(data);
  renderStats(data);
  if (data.market_watch && Array.isArray(data.market_watch) && data.market_watch.length) {
    // live stream push (engine tick): patch the watch dials in place - no timer involved
    marketWatches = data.market_watch;
    applyMarketWatchState();
  }
  renderBot(data);
  renderBot2(data);
  renderBot3(data);
  renderPositions(data);
  renderHistory(data);
  renderFeed(data);
  renderFooter(data);
  detectNewClosed(prev, data);
}

/* Wipe every trace of the previous session (used on disconnect / logout) */
function fullResetUI() {
  posRows.forEach((tr) => tr.remove());
  posRows.clear();
  $('positions-body').innerHTML = '';
  $('history-body').innerHTML = '';
  $('feed').innerHTML = '';
  $('pos-count').textContent = '0';
  $('hist-count').textContent = '0';
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
function setCell(node, v) { if (node.textContent !== String(v)) node.textContent = v; }

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
  (extraRows || []).forEach((r) => map.set(String(r.position_id), r));
  (S.history || []).forEach((r) => map.set(String(r.position_id), r));
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
  $('hist-count').textContent = rows.length;
  $('hist-empty').classList.toggle('hidden', rows.length > 0);

  const sig = histPeriod + '|' + histResult + '|' + histSymbol + '|' + histSearch + '|' + rows.length + '|' +
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
    const rIn = isMajor ? 74 : 79;
    const x1 = (110 + rIn * sin).toFixed(1);
    const y1 = (106 - rIn * cos).toFixed(1);
    const x2 = (110 + rOut * sin).toFixed(1);
    const y2 = (106 - rOut * cos).toFixed(1);
    const strokeColor = th < -5 ? '#22c55e' : (th > 5 ? '#ef4444' : '#38bdf8');
    const strokeW = isMajor ? '2' : '1';
    const opacity = isMajor ? '0.95' : '0.45';
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
        <filter id="mGlowGreen" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#22c55e" flood-opacity="0.6"/>
        </filter>
        <filter id="mGlowRed" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#ef4444" flood-opacity="0.6"/>
        </filter>
      </defs>

      <!-- Recessed Machine Guide Channel -->
      <path d="M 35.7 71.3 A 82 82 0 0 1 184.3 71.3" fill="none" stroke="rgba(15, 23, 42, 0.95)" stroke-width="12" stroke-linecap="round" />
      <path d="M 35.7 71.3 A 82 82 0 0 1 184.3 71.3" fill="none" stroke="rgba(255, 255, 255, 0.08)" stroke-width="1" stroke-linecap="round" />

      <!-- Glowing High-Voltage Pressure Arcs -->
      <path d="M 35.7 71.3 A 82 82 0 0 1 110 24" fill="none" stroke="url(#mGradBuy)" stroke-width="7" stroke-linecap="round" filter="url(#mGlowGreen)" opacity="0.95" />
      <path d="M 110 24 A 82 82 0 0 1 184.3 71.3" fill="none" stroke="url(#mGradSell)" stroke-width="7" stroke-linecap="round" filter="url(#mGlowRed)" opacity="0.95" />

      <!-- Concentric Micro-Track -->
      <path d="M 42 75 A 72 72 0 0 1 178 75" fill="none" stroke="rgba(255, 255, 255, 0.07)" stroke-width="1" stroke-dasharray="3 3" />

      <!-- Precision Radiated Ticks -->
      ${ticksSvg}

      <!-- Center Zero-Index (12 o'clock) -->
      <polygon points="110,18 106,25 114,25" fill="#38bdf8" />
      <circle cx="110" cy="30" r="2" fill="#38bdf8" />
      <text x="110" y="14" class="gauge-center-zero">0Δ</text>

      <!-- Cockpit Etchings -->
      <text x="24" y="90" class="gauge-label-buy">BUY</text>
      <text x="24" y="100" class="gauge-sub-buy">100%</text>

      <text x="196" y="90" class="gauge-label-sell">SELL</text>
      <text x="196" y="100" class="gauge-sub-sell">100%</text>

      <text x="68" y="50" class="gauge-tech-hash">« TORQUE</text>
      <text x="152" y="50" class="gauge-tech-hash">PRESSURE »</text>

      <!-- Crosshairs -->
      <line x1="102" y1="106" x2="118" y2="106" stroke="rgba(56, 189, 248, 0.2)" stroke-width="1" />
      <line x1="110" y1="98" x2="110" y2="114" stroke="rgba(56, 189, 248, 0.2)" stroke-width="1" />
      <circle cx="110" cy="106" r="12" fill="none" stroke="rgba(56, 189, 248, 0.15)" stroke-width="1" stroke-dasharray="2 2" />
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

  // Determine current active symbol from bot or bot2
  const activeSym = (S && S.bot2 && S.bot2.symbol) || (S && S.bot && S.bot.symbol) || 'BTCUSDTm';

  // Overall market pressure summary (patched live on every stream push)
  updateWatchSummaryPill();

  grid.innerHTML = marketWatches.map((w, idx) => {
    const sym = w.symbol || '';
    const isActive = sym.toUpperCase() === activeSym.toUpperCase();
    const dominant = String(w.dominant || 'NEUTRAL').toUpperCase();
    const domCls = watchDominantClass(dominant);
    const needleDeg = Number(w.needle_deg || 0);
    const buyerPct = Number(w.buyer_pct || 50);
    const sellerPct = Number(w.seller_pct || 50);
    const digits = Number(w.digits || 2);
    const priceStr = w.ask ? nf(w.ask, digits) : '—';
    const spreadStr = w.spread !== undefined ? w.spread : '—';
    const { flow, arrow: flowArrow } = watchFlowMeta(w.candle_flow);
    const isLive = w.live !== false;
    const domBadgeText = watchAnnunciatorText(dominant, buyerPct, sellerPct);

    return `
      <div class="watch-card${isActive ? ' active-target' : ''}" data-sym="${esc(sym)}" title="${LANG === 'bn' ? 'ক্লিক করে এই মার্কেট সিলেক্ট করুন' : 'Click to select this market'}">
        <!-- 4 Machine Hex Socket Screws -->
        <div class="machine-bolt bolt-tl"></div>
        <div class="machine-bolt bolt-tr"></div>
        <div class="machine-bolt bolt-bl"></div>
        <div class="machine-bolt bolt-br"></div>

        <!-- Machine Card Top Strip -->
        <div class="machine-card-header">
          <div class="machine-chip-id">
            <span class="machine-led-pulse ${domCls}"></span>
            <span class="machine-label-text">CHRONO-${String(idx + 1).padStart(2, '0')}</span>
          </div>
          <span class="machine-clock${isLive ? '' : ' sim'}" title="${isLive ? 'Last live reading' : 'Simulated data — MT5 not connected'}">${watchClockText(w.updated_at)}</span>
          ${isActive ? `<span class="machine-engaged-badge">ENGAGED</span>` : `<span class="machine-standby-badge">STANDBY</span>`}
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
              <div class="watch-annunciator ${domCls}">
                <span class="annunciator-lamp"></span>
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

          <!-- Machine Specs Row -->
          <div class="machine-specs-row">
            <div class="spec-item">
              <span class="spec-k">SPREAD</span>
              <span class="spec-v spec-spread">${spreadStr}</span>
            </div>
            <div class="spec-item">
              <span class="spec-k">FLOW</span>
              <span class="spec-v flow-val spec-flow ${flow}">${flowArrow} ${flow.toUpperCase()}</span>
            </div>
            <div class="spec-item">
              <span class="spec-k">TARGET</span>
              <span class="spec-v target-btn spec-target${isActive ? ' active' : ''}">${isActive ? 'ACTIVE' : 'SELECT'}</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join('');


  // Add click to select symbol
  grid.querySelectorAll('.watch-card').forEach(card => {
    card.addEventListener('click', async () => {
      const sym = card.dataset.sym;
      if (!sym) return;
      toast('ok', `${t('botMarketSwitched')}: ${sym}`);
      try {
        await Promise.all([
          botPost('/api/bot2/config', { symbol: sym }),
          botPost('/api/bot/config', { symbol: sym }),
          botPost('/api/bot3/config', { symbol: sym })
        ]);
        if (wsAlive) ws.send(JSON.stringify({ type: 'refresh' }));
      } catch (e) {}
    });
  });
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
    const spStr = b.spread !== undefined ? b.spread : '';
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
    pick.innerHTML = '';
    pick.classList.add('hidden');
  }

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
  if (document.activeElement !== slEl && slCfg && Number(slEl.value) !== slCfg) slEl.value = nf(slCfg, 2);

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
    const lot = Number($('bot-lot').value || 0.01);
    const b = (S && S.bot) || {};
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
      $('bot-sl').value = nf(gap * 2, 2);
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

    // Immediate visual response
    document.querySelectorAll('#bot-market-chips .chip')
      .forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
    $('bot-symbol').textContent = sym;
    $('bot-symbol-name').textContent = sym;
    $('bot-market-chips').dataset.n = ''; // reset signature cache

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
  const trendMode = !!(b.trend_mode !== undefined ? b.trend_mode : cfg.trend_mode);
  const arrow = mTrend === 'UP' ? ' ▲' : (mTrend === 'DOWN' ? ' ▼' : ' ▬');

  const numSec = Number(sec);
  const cleanSec = Math.max(0, Math.ceil(numSec * 2) / 2);
  const secFormatted = (cleanSec % 1 === 0 ? cleanSec.toFixed(0) : cleanSec.toFixed(1)) + 's';

  // 1. Top Trend Pill: Watch: BUYER ▲ / SELLER ▼ / NEUTRAL ▬ (⏱️ 2.5s) (ACTIVE)
  const trendPill = $('bot2-trend-pill');
  if (trendPill) {
    trendPill.className = 'pill trend-pill ' + (mTrend === 'UP' ? 'up' : (mTrend === 'DOWN' ? 'down' : 'neutral')) + (trendMode ? ' active' : '');
    trendPill.textContent = (LANG === 'bn' ? 'ওয়াচ: ' : 'Watch: ')
      + (mTrend === 'UP' ? 'BUYER ▲' : (mTrend === 'DOWN' ? 'SELLER ▼' : 'NEUTRAL ▬'))
      + ` (⏱️ ${secFormatted})` + (trendMode ? ' (ACTIVE)' : ' (OFF)');
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
  const trendMode = !!(b.trend_mode !== undefined ? b.trend_mode : cfg.trend_mode);
  const trendBtn = $('bot2-trend');
  if (trendBtn) trendBtn.classList.toggle('active', trendMode);

  const trendStatus = $('bot2-trend-status');
  if (trendStatus) {
    trendStatus.textContent = trendMode ? 'ACTIVE' : 'OFF';
    trendStatus.className = 'trend-badge ' + (trendMode ? 'active' : 'deactive');
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
    trendVal.textContent = mTrend === 'UP' ? (LANG === 'bn' ? 'বায়ার প্রেশার - BUY ▲' : 'BUYER PRESSURE - BUY ▲')
      : (mTrend === 'DOWN' ? (LANG === 'bn' ? 'সেলার প্রেশার - SELL ▼' : 'SELLER PRESSURE - SELL ▼')
      : (LANG === 'bn' ? 'নিরপেক্ষ - ট্রেড নেবে না' : 'NEUTRAL - NO TRADE'));
  }
  const detailsEl = $('bot2-trend-details');
  if (detailsEl) {
    detailsEl.innerHTML = `${pressureTxt ? `<b>${esc(pressureTxt)}</b> · ` : ''}<b>Score: ${scoreSign}</b> · ${esc(trendDetails)}`;
  }
  const footerEl = $('bot2-trend-footer');
  if (footerEl) {
    footerEl.textContent = LANG === 'bn'
      ? '⏱️ প্রতি ২.৫ সেকেন্ড পর পর স্বয়ংক্রিয়ভাবে ট্রেন্ড (UP/DOWN) চেক হচ্ছে ও সেই ট্রেন্ডে ট্রেড নেওয়া হচ্ছে'
      : '⏱️ Checking & updating market trend (UP/DOWN) automatically every 2.5 seconds';
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
    const spStr = b.spread !== undefined ? b.spread : '';
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
    pick.innerHTML = '';
    pick.classList.add('hidden');
  }

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
    tpEl.value = nf(cfg.tp, 2);
  }

  const slEl = $('bot2-sl');
  if (document.activeElement !== slEl && cfg.sl !== undefined && Number(slEl.value) !== Number(cfg.sl)) {
    slEl.value = nf(cfg.sl, 2);
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
      ? `<b>৪. ২.৫ সেকেন্ড ট্রেন্ড ট্রেডিং (চালু):</b> প্রতি ২.৫ সেকেন্ড টাইমার চেক করে ১টি করে নতুন ট্রেড নিবে (বর্তমান ট্রেন্ড: <b>${mTrend}</b> -> <b>${mTrend === 'UP' ? 'BUY' : 'SELL'}</b>), সর্বোচ্চ <b>${maxTrades}</b> টি ট্রেড একসাথে রানিং থাকতে পারবে (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>)।`
      : `<b>৪. ক্যান্ডেল ফ্লো রিভার্সাল:</b> ক্যান্ডেল নিচে নামলে <b>BUY</b> ট্রেড (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>); ক্যান্ডেল উপরে উঠলে <b>SELL</b> ট্রেড (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>) — ফ্লো এর বিপরীত দিকে ট্রেড।`,
    `<b>৫. মার্কেট স্ট্রাকচার লেভেল (${tf === 'W1' ? 'Weekend/Week' : 'Day'}):</b>
      <br>&nbsp;&nbsp;• <b>Lower Low (LL)</b> প্রাইসে আসলে <b>BUY</b> করবে${trendMode && mTrend !== 'UP' ? ' (ট্রেন্ড DOWN থাকায় অফ)' : ' (৪নং ক্যান্ডেল রুল তখন অফ থাকবে)'}
      <br>&nbsp;&nbsp;• <b>Higher High (HH)</b> প্রাইসে আসলে <b>SELL</b> করবে${trendMode && mTrend !== 'DOWN' ? ' (ট্রেন্ড UP থাকায় অফ)' : ' (৪নং রুল অফ থাকবে)'}
      <br>&nbsp;&nbsp;• <b>Lower High (LH)</b> প্রাইসে আসলে <b>SELL</b> করবে${trendMode && mTrend !== 'DOWN' ? ' (ট্রেন্ড UP থাকায় অফ)' : ' (৪নং রুল অফ থাকবে)'}
      <br>&nbsp;&nbsp;• <b>Higher Low (HL)</b> প্রাইসে আসলে <b>BUY</b> করবে${trendMode && mTrend !== 'UP' ? ' (ট্রেন্ড DOWN থাকায় অফ)' : ' (৪নং রুল অফ থাকবে)'}`,
    `<b>৬. ২.৫ সে. ব্যবধানে ১টি করে এন্ট্রি:</b> একসাথে সবগুলো না নিয়ে প্রতি ২.৫ সেকেন্ডে ১টি করে ট্রেন্ড অনুযায়ী ট্রেড নিবে যতক্ষণ না সর্বোচ্চ সীমা (<b>${maxTrades}</b> টি) পূর্ণ হয়।`,
    `<b>৭. ওয়াচ মার্কেট প্রেশার ফিল্টার (>৫২%):</b> ট্রেড নেওয়ার আগে ওয়াচ মার্কেটে বায়ার বা সেলার চাপ চেক করবে — চাপ ৫২% এর বেশি হলে BUY বা SELL নিবে, ৫২% এর কম হলে কোনো ট্রেড নিবে না।`,
  ] : [
    `<b>1. Lot Size:</b> <b>${lot}</b> · <b>2. Max Running:</b> Maximum <b>${maxTrades}</b> concurrent trade(s) running at once.`,
    `<b>3. Market Selection:</b> Choose instruments with real-time Ask / Bid / Spread tracking.`,
    trendMode
      ? `<b>4. 2.5s Trend Trading Mode (ACTIVE):</b> Checks 2.5s trend timer and opens 1 new trade every 2.5s in verified trend direction (Current: <b>${mTrend}</b> -> <b>${mTrend === 'UP' ? 'BUY' : 'SELL'}</b>), up to <b>${maxTrades}</b> simultaneous trades (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>).`
      : `<b>4. Candle Flow Reversal:</b> When candle moves DOWN -> Take <b>BUY</b> (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>); when candle moves UP -> Take <b>SELL</b> (TP <b>$${nf(tpVal, 2)}</b>, SL <b>$${nf(slVal, 2)}</b>) — counter-flow trade.`,
    `<b>5. Structure Levels (${tf === 'W1' ? 'Weekend/Week' : 'Day'}):</b>
      <br>&nbsp;&nbsp;• At <b>Lower Low (LL)</b> -> Take <b>BUY</b>${trendMode && mTrend !== 'UP' ? ' (Blocked: Trend is DOWN)' : ' (Step 4 paused)'}
      <br>&nbsp;&nbsp;• At <b>Higher High (HH)</b> -> Take <b>SELL</b>${trendMode && mTrend !== 'DOWN' ? ' (Blocked: Trend is UP)' : ' (Step 4 paused)'}
      <br>&nbsp;&nbsp;• At <b>Lower High (LH)</b> -> Take <b>SELL</b>${trendMode && mTrend !== 'DOWN' ? ' (Blocked: Trend is UP)' : ' (Step 4 paused)'}
      <br>&nbsp;&nbsp;• At <b>Higher Low (HL)</b> -> Take <b>BUY</b>${trendMode && mTrend !== 'UP' ? ' (Blocked: Trend is DOWN)' : ' (Step 4 paused)'}`,
    `<b>6. 2.5-Second Interval Execution:</b> Opens 1 trade every 2.5s sequentially (never all at once) until active count reaches <b>${maxTrades}</b>.`,
    `<b>7. Watch Market Pressure Filter (>52%):</b> Before taking trade, checks Watch market: only takes BUY or SELL if pressure is >52%; strictly blocks trade if below 52%.`,
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
    if (trendMode) {
      trendLine.innerHTML = `<b>${isBn ? 'ট্রেন্ড মোড' : 'Trend Mode'}:</b> <span class="trend-active-text">ACTIVE [${mTrend} ${mTrend === 'UP' ? '▲' : '▼'}]</span> — ${isBn ? `প্রতি ২.৫ সে. পর পর ১টি করে নতুন ${mTrend === 'UP' ? 'BUY' : 'SELL'} ট্রেড ওপেন হচ্ছে (পরবর্তী চেক: ${secFormatted})` : `Opening 1 new trade every 2.5s in verified ${mTrend} trend (Next check: ${secFormatted})`}`;
    } else {
      trendLine.innerHTML = `<b>${isBn ? 'ট্রেন্ড মোড' : 'Trend Mode'}:</b> <span class="trend-deactive-text">DEACTIVE (OFF)</span> — ${isBn ? `নরমাল রিভার্সাল ফ্লো ট্রেডিং চালু (২.৫ সে. ট্রেন্ড: ${mTrend} ${mTrend === 'UP' ? '▲' : '▼'}, Next: ${secFormatted})` : `Standard reversal flow trading (2.5s trend: ${mTrend}, Next: ${secFormatted})`}`;
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
    const lot = Number($('bot2-lot').value || 0.01);
    const maxTrades = Number($('bot2-max-trades').value || 1);
    const tp = Number($('bot2-tp').value || 0.10);
    const sl = Number($('bot2-sl').value || 2.00);
    const ok = window.confirm(
      LANG === 'bn'
        ? `ক্যান্ডেল ও স্ট্রাকচার বট চালু করবেন?\n\nলট: ${lot}\nসর্বোচ্চ রানিং ট্রেড: ${maxTrades}\nTP: $${tp} | SL: $${sl}`
        : `Start Candle & Structure Bot?\n\nLot: ${lot}\nMax Trades: ${maxTrades}\nTP: $${tp} | SL: $${sl}`
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
    const cur = S && S.bot2 ? (S.bot2.trend_mode !== undefined ? S.bot2.trend_mode : (S.bot2.config || {}).trend_mode) : false;
    const nextState = !cur;
    $('bot2-trend').classList.toggle('active', nextState);
    const trendStatus = $('bot2-trend-status');
    if (trendStatus) {
      trendStatus.textContent = nextState ? 'ACTIVE' : 'OFF';
      trendStatus.className = 'trend-badge ' + (nextState ? 'active' : 'deactive');
    }
    const res = await bot2Post('/api/bot2/config', { trend_mode: nextState });
    if (res && res.ok) {
      if (res.bot2) {
        if (S) S.bot2 = res.bot2;
        renderBot2(S);
      }
      toast('ok', nextState
        ? (LANG === 'bn' ? 'ট্রেন্ড মোড ACTIVE করা হলো' : 'Trend Mode ACTIVE')
        : (LANG === 'bn' ? 'ট্রেন্ড মোড DEACTIVE করা হলো' : 'Trend Mode DEACTIVATED'));
    }
  });

  $('bot2-close').addEventListener('click', async () => {
    if (!window.confirm(LANG === 'bn' ? 'বট ২ এর সব খোলা ট্রেড এখনই ক্লোজ করবেন?' : 'Close all Bot 2 open positions right now?')) return;
    const res = await bot2Post('/api/bot2/close', {});
    if (res && res.ok) toast('ok', `${LANG === 'bn' ? 'ক্লোজ করা ট্রেড' : 'Closed positions'} (${res.closed || 0})`);
  });

  const triggerTrendCheck = async () => {
    toast('info', LANG === 'bn' ? '৬০-সেকেন্ড ট্রেন্ড চেক হচ্ছে…' : 'Checking 60s trend…');
    const refreshBtn = $('bot2-trend-refresh');
    if (refreshBtn) refreshBtn.style.transform = 'rotate(360deg)';
    updateBot2TimerElements(60);
    const res = await bot2Post('/api/bot2/check-trend', {});
    setTimeout(() => { if (refreshBtn) refreshBtn.style.transform = ''; }, 300);
    if (res && res.ok) {
      if (res.bot2 && S) {
        S.bot2 = res.bot2;
      }
      const curTrend = res.trend || (res.bot2 && res.bot2.market_trend) || 'UP';
      toast('ok', LANG === 'bn' ? `ট্রেন্ড চেক সম্পন্ন: ${curTrend} (${curTrend === 'UP' ? 'বুলিশ ▲' : 'বেয়ারিশ ▼'})` : `Trend check complete: ${curTrend}`);
      updateBot2TimerElements(60);
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

  $('bot2-market-chips').addEventListener('click', async (e) => {
    const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
    if (!chip || !chip.dataset.sym) return;
    const sym = chip.dataset.sym;

    document.querySelectorAll('#bot2-market-chips .chip')
      .forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
    $('bot2-symbol').textContent = sym;
    $('bot2-symbol-name').textContent = sym;
    $('bot2-market-chips').dataset.n = '';

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
  const chip = $('bot3-chip');
  if (chip) {
    chip.className = 'bot-chip ' + (!live ? 'offline' : running ? 'running' : 'stopped');
    chip.textContent = !live ? t('botOffline') : running ? t('botRunning') : t('botStopped');
  }

  $('bot3-start').classList.toggle('active', running);
  $('bot3-stop').classList.toggle('active', !running && live);

  // ---- symbol
  const wanted = b.symbol_requested || cfg.symbol || 'XAUUSDm';
  const sym = b.symbol || '';
  const symOk = !!b.symbol_ok && sym;
  const shown = symOk ? sym : wanted;
  $('bot3-symbol').textContent = shown;

  let priceStr = '';
  if (b.ask && b.bid) {
    const askStr = fmtPrice(b.ask);
    const bidStr = fmtPrice(b.bid);
    const spStr = b.spread !== undefined ? b.spread : '';
    priceStr = ` · Ask <b>${askStr}</b> / Bid <b>${bidStr}</b> <span style="font-size:11px;color:var(--txt-faint)">(Spread ${spStr})</span>`;
  }
  $('bot3-symbol-name').innerHTML = `${esc(shown)}${priceStr}`;
  $('bot3-symbol-name').style.color = symOk ? 'var(--accent)' : '#fca5a5';

  // ---- market chips
  const pick = $('bot3-market-chips');
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
    pick.innerHTML = '';
    pick.dataset.n = '';
    pick.classList.add('hidden');
  }

  // inputs update if not focused
  const lotEl = $('bot3-lot');
  if (document.activeElement !== lotEl && cfg.lot !== undefined) lotEl.value = cfg.lot;
  const maxTradesEl = $('bot3-max-trades');
  if (document.activeElement !== maxTradesEl && cfg.max_trades !== undefined) maxTradesEl.value = cfg.max_trades;
  const tpEl = $('bot3-tp');
  if (document.activeElement !== tpEl && cfg.tp !== undefined) tpEl.value = Number(cfg.tp).toFixed(2);
  const slEl = $('bot3-sl');
  if (document.activeElement !== slEl && cfg.sl !== undefined) slEl.value = Number(cfg.sl).toFixed(2);

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

  $('bot3-hh').textContent = curLevels.hh ? fmtPrice(curLevels.hh) : '—';
  $('bot3-lh').textContent = curLevels.lh ? fmtPrice(curLevels.lh) : '—';
  $('bot3-hl').textContent = curLevels.hl ? fmtPrice(curLevels.hl) : '—';
  $('bot3-ll').textContent = curLevels.ll ? fmtPrice(curLevels.ll) : '—';

  $('bot3-hh-sub').textContent = hhSub;
  $('bot3-lh-sub').textContent = lhSub;
  $('bot3-hl-sub').textContent = hlSub;
  $('bot3-ll-sub').textContent = llSub;

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

  const lot = Number(lotEl.value) || Number(cfg.lot || 0.01);
  const maxTrades = Number(maxTradesEl.value) || Number(cfg.max_trades || 1);
  const tpVal = Number(tpEl.value) || Number(cfg.tp || 0.50);
  const slVal = Number(slEl.value) || Number(cfg.sl || 5.00);

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
  $('bot3-cond').innerHTML = conds.map((c) => `<li>${c}</li>`).join('');

  // Running line & pos
  const pos = b.positions || [];
  const runningCount = b.running_count !== undefined ? b.running_count : pos.length;
  $('bot3-running-line').innerHTML = `<b>${isBn ? 'রানিং ট্রেড' : 'Running Trades'}:</b> <b>${runningCount} / ${maxTrades}</b> (Slot: ${maxTrades - runningCount > 0 ? (maxTrades - runningCount) + ' available' : 'FULL'})`;

  $('bot3-pos').innerHTML = pos.length
    ? pos.map((p) => {
        const side = (p.side || 'buy').toUpperCase();
        const sideColor = side === 'BUY' ? 'var(--win)' : 'var(--loss)';
        const net = Number(p.net || 0);
        const netStr = `${net >= 0 ? '+' : ''}$${net.toFixed(2)}`;
        const netColor = net >= 0 ? 'var(--win)' : 'var(--loss)';
        return `<span style="font-family:var(--mono)">#${p.ticket} <b style="color:${sideColor}">${side}</b> ${p.volume} @ ${fmtPrice(p.price_open)} &rarr; <b style="color:${netColor}">${netStr}</b></span>`;
      }).join(' · ')
    : (isBn ? 'নেই' : 'none');

  const stats = b.stats || {};
  const opened = stats.opened || 0;
  const closed = stats.closed || 0;
  const realized = stats.realized !== undefined ? Number(stats.realized).toFixed(2) : '0.00';
  const actionText = b.last_action ? ` · <i>${esc(b.last_action)}</i>` : '';
  $('bot3-last').innerHTML = `${esc(t('botMarketNow'))}: <b>${esc(shown)}</b> · ` +
    `${opened} ${t('botOpened')} / ${closed} ${t('botClosed')} (P/L: $${realized})${actionText}`;
  $('bot3-last').className = 'bot-line' + (b.last_error ? ' warn' : '');
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
  $('bot3-start').addEventListener('click', async () => {
    const lot = Number($('bot3-lot').value || 0.01);
    const maxTrades = Number($('bot3-max-trades').value || 1);
    const tp = Number($('bot3-tp').value || 0.50);
    const sl = Number($('bot3-sl').value || 5.00);
    const ok = window.confirm(
      LANG === 'bn'
        ? `এক্সট্রিম স্ট্রাকচার বট চালু করবেন?\n\nলট: ${lot}\nসর্বোচ্চ রানিং ট্রেড: ${maxTrades}\nTP: $${tp} | SL: $${sl}`
        : `Start Extreme Structure Bot?\n\nLot: ${lot}\nMax Trades: ${maxTrades}\nTP: $${tp} | SL: $${sl}`
    );
    if (!ok) return;
    const res = await bot3Post('/api/bot3/start', { lot, max_trades: maxTrades, tp, sl });
    if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ৩ চালু হয়েছে' : 'Bot 3 Started');
  });

  $('bot3-stop').addEventListener('click', async () => {
    const res = await bot3Post('/api/bot3/stop', {});
    if (res && res.ok) toast('ok', LANG === 'bn' ? 'বট ৩ বন্ধ করা হলো' : 'Bot 3 Stopped');
  });

  $('bot3-close').addEventListener('click', async () => {
    if (!window.confirm(LANG === 'bn' ? 'বট ৩ এর সব খোলা ট্রেড এখনই ক্লোজ করবেন?' : 'Close all Bot 3 open positions right now?')) return;
    const res = await bot3Post('/api/bot3/close', {});
    if (res && res.ok) toast('ok', `${LANG === 'bn' ? 'ক্লোজ করা ট্রেড' : 'Closed positions'} (${res.closed || 0})`);
  });

  $('bot3-lot').addEventListener('change', () => {
    void bot3Post('/api/bot3/config', { lot: Number($('bot3-lot').value || 0.01) });
  });
  $('bot3-max-trades').addEventListener('change', () => {
    void bot3Post('/api/bot3/config', { max_trades: Math.max(1, parseInt($('bot3-max-trades').value || 1, 10)) });
  });
  $('bot3-tp').addEventListener('change', () => {
    void bot3Post('/api/bot3/config', { tp: Number($('bot3-tp').value || 0.50) });
  });
  $('bot3-sl').addEventListener('change', () => {
    void bot3Post('/api/bot3/config', { sl: Number($('bot3-sl').value || 5.00) });
  });

  $('bot3-market-chips').addEventListener('click', async (e) => {
    const chip = e.target && e.target.closest ? e.target.closest('.chip') : null;
    if (!chip || !chip.dataset.sym) return;
    const sym = chip.dataset.sym;

    document.querySelectorAll('#bot3-market-chips .chip')
      .forEach(c => c.classList.toggle('active', c.dataset.sym === sym));
    $('bot3-symbol').textContent = sym;
    $('bot3-symbol-name').textContent = sym;
    $('bot3-market-chips').dataset.n = '';

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

  $('bot3-tf-toggle').addEventListener('click', async (e) => {
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

  const recalcBtn = $('bot3-recalc-btn');
  if (recalcBtn) {
    recalcBtn.addEventListener('click', () => {
      void triggerBot3Recalculate(true);
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
  if (!evs.length) {
    $('feed').innerHTML = '<li class="k-info"><span class="t">--:--</span><span>waiting for activity…</span></li>';
    $('feed-badge').textContent = '0';
    return;
  }

  // 1. Initial load snapshot handling:
  // "2. Update na asha porjonto kisu bolbe na." -> On first load, record existing events so they are not spoken.
  if (!feedInitialized) {
    evs.forEach((e) => {
      const key = `${e.ts}_${e.kind || ''}_${e.message || ''}`;
      seenEventKeys.add(key);
    });
    feedInitialized = true;
  } else {
    // 2. Identify newly arrived events and queue for speech sequentially:
    const newItems = [];
    evs.forEach((e) => {
      const key = `${e.ts}_${e.kind || ''}_${e.message || ''}`;
      if (!seenEventKeys.has(key)) {
        seenEventKeys.add(key);
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

  $('feed-badge').textContent = evs.length;

  // Re-render feed list with unique DOM IDs and keep active lamp glow if currently speaking
  $('feed').innerHTML = evs.slice(0, 40).map((e) => {
    let kind = e.kind;
    if (kind === 'trade') kind = /closed/i.test(e.message) ? (/->\s*-/.test(e.message) ? 'trade loss' : 'trade') : 'trade';
    const cls = kind.startsWith('trade') ? 'k-trade' : 'k-' + kind;
    const txt = String(e.message || '');
    const colored = txt.replace(/->\s*([+-]?[\d.]+)/, (m, v) => `-> <span class="${Number(v) > 0 ? 'value-win' : 'value-loss'}">${v}</span>`);
    
    const key = `${e.ts}_${e.kind || ''}_${e.message || ''}`;
    const elId = `feed-evt-${e.ts}-${Math.abs(hashStr(key))}`;
    const isLit = (currentSpeakingKey === elId);

    return `<li id="${elId}" class="${cls} ${isLit ? 'voice-light-pulse' : ''}"><span class="t">${esc(e.server_time || '')}</span><span>${colored}</span></li>`;
  }).join('');
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
    toast(net >= 0 ? 'trade' : 'trade', `Closed ${top.symbol} ${top.side} ${top.volume} -> ${net > 0 ? '+' : ''}${nf(net)}`);
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
  bindBotUI();
  bindBot2UI();
  bindBot3UI();
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
    await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ poll_interval_ms: Number($('set-poll').value), history_interval_ms: Number($('set-hist').value), history_days: Number($('set-days').value) }) });
    modal.classList.add('hidden');
    toast('ok', 'Settings saved');
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

async function loadConfig(silent) {
  let fresh;
  try {
    const r = await fetch('/api/config');
    fresh = await r.json();
  } catch (e) { if (!silent) CFG = {}; return; }
  const prevTerminals = (CFG.terminals || []).join('|');
  CFG = fresh;

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
  const newest = marketWatches.reduce((m, w) => Math.max(m, Number(w.updated_at) || 0), 0);
  badge.classList.add('live');
  badge.classList.toggle('sim', !anyLive);
  txt.textContent = `${anyLive ? 'LIVE' : 'SIM'} · ${watchClockText(newest)}`;
}

/* Light live frame pushed on every engine tick: the dials only. */
function applyMarketWatchFrame(frame) {
  if (!frame || !Array.isArray(frame.watches) || !frame.watches.length) return;
  marketWatches = frame.watches;
  applyMarketWatchState();
}

/* Structure changed (boot / different symbol set) -> full machine rebuild. */
function applyMarketWatchState() {
  const grid = $('market-watch-grid');
  if (!grid || !marketWatches.length) return;
  const sig = marketWatches.map((w) => String(w.symbol || '')).join(',');
  if (grid.dataset.sig !== sig || !grid.querySelector('.watch-card')) {
    grid.dataset.sig = sig;
    renderMarketWatches();
  }
  updateMarketWatchDials();
  updateWatchSummaryPill();
  updateWatchStreamBadge();
}

/* In-place live patch - keeps the CSS transitions (needle sweep, VU fade) intact. */
function updateMarketWatchDials() {
  const grid = $('market-watch-grid');
  if (!grid) return;
  const activeSym = String((S && S.bot2 && S.bot2.symbol) || (S && S.bot && S.bot.symbol) || '').toUpperCase();
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
    // fast frames arrive ~20x/second: only redo the pressure machinery when it moved
    const pressureKey = `${dominant}|${buyerPct}|${sellerPct}|${flow}`;
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
      if (ann) ann.className = `watch-annunciator ${domCls}`;
      if (annTxt) annTxt.textContent = watchAnnunciatorText(dominant, buyerPct, sellerPct);

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
      const flowEl = card.querySelector('.spec-flow');
      if (flowEl) {
        flowEl.className = `spec-v flow-val spec-flow ${flow}`;
        flowEl.textContent = `${arrow} ${flow.toUpperCase()}`;
      }
      card._mwState = { pressureKey };
    }

    // telemetry deck: spread moves with every tick
    const spreadEl = card.querySelector('.spec-spread');
    if (spreadEl && w.spread != null) spreadEl.textContent = w.spread;

    // live measurement clock on the machine face
    const clockEl = card.querySelector('.machine-clock');
    if (clockEl) {
      clockEl.textContent = clockFor(w.updated_at);
      clockEl.title = isLive ? 'Last live reading' : 'Simulated data - MT5 not connected';
      clockEl.classList.toggle('sim', !isLive);
    }

    // header LED + engaged state
    const led = card.querySelector('.machine-led-pulse');
    if (led) led.className = `machine-led-pulse ${domCls}`;
    card.classList.toggle('active-target', isActive);
    const stateBadge = card.querySelector('.machine-engaged-badge, .machine-standby-badge');
    if (stateBadge) {
      const want = isActive ? 'machine-engaged-badge' : 'machine-standby-badge';
      if (stateBadge.className !== want) {
        stateBadge.className = want;
        stateBadge.textContent = isActive ? 'ENGAGED' : 'STANDBY';
      }
    }
    const targetBtn = card.querySelector('.spec-target');
    if (targetBtn) {
      targetBtn.className = `spec-v target-btn spec-target${isActive ? ' active' : ''}`;
      targetBtn.textContent = isActive ? 'ACTIVE' : 'SELECT';
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
  let totalBuy = 0, totalSell = 0;
  marketWatches.forEach((w) => {
    totalBuy += Number(w.buyer_pct || 50);
    totalSell += Number(w.seller_pct || 50);
  });
  const avgBuy = Math.round(totalBuy / marketWatches.length);
  const avgSell = Math.round(totalSell / marketWatches.length);
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

/* ------------------------------------------------------------------- init */
(function init() {
  const q = new URLSearchParams(location.search);
  if (q.get('lang') === 'bn' || q.get('lang') === 'en') { LANG = q.get('lang'); localStorage.setItem('exdash.lang', LANG); }
  bindUI();
  updateVoiceMuteButtons();
  applyLang();
  renderStatLabels();
  void loadConfig();
  connectWS();
  void loadInitial();
  setInterval(() => { if (!wsAlive) void loadInitial(); }, 3000);
  // while logged out, pick up a freshly installed MT5 terminal automatically
  setInterval(() => { if (!wasConnected) void loadConfig(true); }, 15000);
  setInterval(() => { if (wsAlive) { try { ws.send(JSON.stringify({ type: 'ping' })); } catch (e) {} } }, 20000);
  // live countdown ticker for Bot 2 2.5-second trend check
  setInterval(tickBot2TrendCountdown, 250);
  // live 1-second countdown ticker for Bot 3 60-second extreme setups recalculation
  setInterval(tickBot3RecalcTimer, 1000);
  // Market Pressure Watch Dials: NO ticker at all. One seed fetch here, then every
  // engine-tick WebSocket snapshot patches the dials in place (live time included).
  void fetchMarketWatch();
})();
