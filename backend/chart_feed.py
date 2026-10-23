"""Exness (MetaTrader 5) chart + big-print feed.

The dashboard chart is powered by the *same* Exness account the bots trade on,
not by an external crypto exchange. This module is the single place that talks
to MetaTrader 5 for market data:

* ``symbols()``   - the Exness instruments the chart can show
* ``candles()``   - real OHLCV bars for one timeframe
* ``ticker()``    - 24h high / low / change / tick volume + live bid-ask
* ``snapshot()``  - one frame with meta + bars + ticker, used by the WS
* ``ChartFeed``   - a ~20 fps sampler that streams ticks to every browser and
                    detects unusually large price impulses ("big prints")

Every MT5 call is serialised through ``mt5_client.IPC_LOCK`` because the
terminal has a single IPC pipe shared with the engine. Nothing here places an
order, logs in or writes to the store; it is read-only market data.
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

try:  # pragma: no cover - only missing when the package is not installed
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # pragma: no cover
    mt5 = None  # type: ignore

from .mt5_client import IPC_LOCK, preferred_terminal_path

try:  # pragma: no cover - keeps the Exness dashboard intact if the BD module is absent
    from .bd_feed import is_bd_symbol

    def _bd_candles(symbol: str, tf: str, count: int) -> List[List[float]]:
        from . import bd_feed

        return bd_feed.candles(symbol, tf, count)

    def _bd_tick(symbol: str) -> Optional[Dict[str, Any]]:
        from . import bd_feed

        return bd_feed.tick(symbol)

    def _bd_meta(symbol: str) -> Optional[Dict[str, Any]]:
        from . import bd_feed

        return bd_feed.meta(symbol)

    def _bd_m1_volume(symbol: str) -> Tuple[int, int]:
        from . import bd_feed

        return bd_feed.m1_volume(symbol)
except Exception:  # pragma: no cover
    def is_bd_symbol(symbol: str) -> bool:  # type: ignore
        return False
    _bd_candles = _bd_tick = _bd_meta = None
    _bd_m1_volume = None

# Timeframes the chart offers -> (seconds per bar, MT5 constant name)
TF_SEC: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
_TF_NAME = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}

# Exness instrument catalogue for the chart. The first candidate that exists on
# the connected account wins; a missing market is simply not offered.
# Full Exness catalogue: Metals, Crypto, Forex, Energy, Indices, Stocks.
CATALOG: List[Dict[str, Any]] = [
    # ──────────────────── METALS ────────────────────
    {"symbol": "XAUUSDm", "alts": ["XAUUSDm", "XAUUSD247m", "XAUUSD"],
     "label": "GOLD", "pair": "USD", "icon": "🟡", "unit": "oz", "kind": "metals"},
    {"symbol": "XAGUSDm", "alts": ["XAGUSDm", "XAGUSD"],
     "label": "SILVER", "pair": "USD", "icon": "⚪", "unit": "oz", "kind": "metals"},
    {"symbol": "XPTUSDm", "alts": ["XPTUSDm", "XPTUSD"],
     "label": "PLATINUM", "pair": "USD", "icon": "🔘", "unit": "oz", "kind": "metals"},
    {"symbol": "XPDUSDm", "alts": ["XPDUSDm", "XPDUSD"],
     "label": "PALLADIUM", "pair": "USD", "icon": "🪙", "unit": "oz", "kind": "metals"},
    {"symbol": "XAUEURm", "alts": ["XAUEURm", "XAUEUR"],
     "label": "GOLD", "pair": "EUR", "icon": "🟡", "unit": "oz", "kind": "metals"},
    {"symbol": "XAUGBPm", "alts": ["XAUGBPm", "XAUGBP"],
     "label": "GOLD", "pair": "GBP", "icon": "🟡", "unit": "oz", "kind": "metals"},
    {"symbol": "XAUAUDm", "alts": ["XAUAUDm", "XAUAUD"],
     "label": "GOLD", "pair": "AUD", "icon": "🟡", "unit": "oz", "kind": "metals"},
    # ──────────────────── CRYPTO ────────────────────
    {"symbol": "BTCUSDTm", "alts": ["BTCUSDTm", "BTCUSDm", "BTCUSD"],
     "label": "BTC", "pair": "USDT", "icon": "🟠", "unit": "BTC", "kind": "crypto"},
    {"symbol": "ETHUSDm", "alts": ["ETHUSDm", "ETHUSDTm", "ETHUSD"],
     "label": "ETH", "pair": "USD", "icon": "🔷", "unit": "ETH", "kind": "crypto"},
    {"symbol": "XRPUSDm", "alts": ["XRPUSDm", "XRPUSDTm", "XRPUSD"],
     "label": "XRP", "pair": "USD", "icon": "⚫", "unit": "XRP", "kind": "crypto"},
    {"symbol": "SOLUSDm", "alts": ["SOLUSDm", "SOLUSDTm", "SOLUSD"],
     "label": "SOL", "pair": "USD", "icon": "🟣", "unit": "SOL", "kind": "crypto"},
    {"symbol": "DOGEUSDm", "alts": ["DOGEUSDm", "DOGEUSDTm", "DOGEUSD"],
     "label": "DOGE", "pair": "USD", "icon": "🐕", "unit": "DOGE", "kind": "crypto"},
    {"symbol": "LTCUSDm", "alts": ["LTCUSDm", "LTCUSDTm", "LTCUSD"],
     "label": "LTC", "pair": "USD", "icon": "🔵", "unit": "LTC", "kind": "crypto"},
    {"symbol": "BCHUSDm", "alts": ["BCHUSDm", "BCHUSDTm", "BCHUSD"],
     "label": "BCH", "pair": "USD", "icon": "🟢", "unit": "BCH", "kind": "crypto"},
    {"symbol": "LINKUSDm", "alts": ["LINKUSDm", "LINKUSDTm", "LINKUSD"],
     "label": "LINK", "pair": "USD", "icon": "🔗", "unit": "LINK", "kind": "crypto"},
    {"symbol": "AVAXUSDm", "alts": ["AVAXUSDm", "AVAXUSDTm", "AVAXUSD"],
     "label": "AVAX", "pair": "USD", "icon": "🔺", "unit": "AVAX", "kind": "crypto"},
    {"symbol": "DOTUSDm", "alts": ["DOTUSDm", "DOTUSDTm", "DOTUSD"],
     "label": "DOT", "pair": "USD", "icon": "⬡", "unit": "DOT", "kind": "crypto"},
    {"symbol": "MATICUSDm", "alts": ["MATICUSDm", "MATICUSDTm", "MATICUSD"],
     "label": "MATIC", "pair": "USD", "icon": "🟪", "unit": "MATIC", "kind": "crypto"},
    {"symbol": "UNIUSDm", "alts": ["UNIUSDm", "UNIUSDTm", "UNIUSD"],
     "label": "UNI", "pair": "USD", "icon": "🦄", "unit": "UNI", "kind": "crypto"},
    {"symbol": "ADAUSDm", "alts": ["ADAUSDm", "ADAUSDTm", "ADAUSD"],
     "label": "ADA", "pair": "USD", "icon": "🔵", "unit": "ADA", "kind": "crypto"},
    {"symbol": "SHIBUSDm", "alts": ["SHIBUSDm", "SHIBUSDTm", "SHIBUSD"],
     "label": "SHIB", "pair": "USD", "icon": "🐶", "unit": "SHIB", "kind": "crypto"},
    {"symbol": "BNBUSDm", "alts": ["BNBUSDm", "BNBUSDTm", "BNBUSD"],
     "label": "BNB", "pair": "USD", "icon": "💛", "unit": "BNB", "kind": "crypto"},
    {"symbol": "ATOMUSDm", "alts": ["ATOMUSDm", "ATOMUSDTm", "ATOMUSD"],
     "label": "ATOM", "pair": "USD", "icon": "⚛️", "unit": "ATOM", "kind": "crypto"},
    {"symbol": "XLMUSDm", "alts": ["XLMUSDm", "XLMUSDTm", "XLMUSD"],
     "label": "XLM", "pair": "USD", "icon": "⭐", "unit": "XLM", "kind": "crypto"},
    {"symbol": "NEARUSDm", "alts": ["NEARUSDm", "NEARUSDTm", "NEARUSD"],
     "label": "NEAR", "pair": "USD", "icon": "🌐", "unit": "NEAR", "kind": "crypto"},
    {"symbol": "APTUSDm", "alts": ["APTUSDm", "APTUSDTm", "APTUSD"],
     "label": "APT", "pair": "USD", "icon": "🔶", "unit": "APT", "kind": "crypto"},
    {"symbol": "OPUSDm", "alts": ["OPUSDm", "OPUSDTm", "OPUSD"],
     "label": "OP", "pair": "USD", "icon": "🔴", "unit": "OP", "kind": "crypto"},
    {"symbol": "ARBUSDm", "alts": ["ARBUSDm", "ARBUSDTm", "ARBUSD"],
     "label": "ARB", "pair": "USD", "icon": "🔹", "unit": "ARB", "kind": "crypto"},
    # ──────────────────── FOREX MAJORS ────────────────────
    {"symbol": "EURUSDm", "alts": ["EURUSDm", "EURUSD"],
     "label": "EUR", "pair": "USD", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "GBPUSDm", "alts": ["GBPUSDm", "GBPUSD"],
     "label": "GBP", "pair": "USD", "icon": "💷", "unit": "lots", "kind": "forex"},
    {"symbol": "USDJPYm", "alts": ["USDJPYm", "USDJPY"],
     "label": "USD", "pair": "JPY", "icon": "💴", "unit": "lots", "kind": "forex"},
    {"symbol": "USDCHFm", "alts": ["USDCHFm", "USDCHF"],
     "label": "USD", "pair": "CHF", "icon": "🇨🇭", "unit": "lots", "kind": "forex"},
    {"symbol": "AUDUSDm", "alts": ["AUDUSDm", "AUDUSD"],
     "label": "AUD", "pair": "USD", "icon": "🇦🇺", "unit": "lots", "kind": "forex"},
    {"symbol": "NZDUSDm", "alts": ["NZDUSDm", "NZDUSD"],
     "label": "NZD", "pair": "USD", "icon": "🇳🇿", "unit": "lots", "kind": "forex"},
    {"symbol": "USDCADm", "alts": ["USDCADm", "USDCAD"],
     "label": "USD", "pair": "CAD", "icon": "🇨🇦", "unit": "lots", "kind": "forex"},
    # ──────────────────── FOREX CROSSES ────────────────────
    {"symbol": "EURGBPm", "alts": ["EURGBPm", "EURGBP"],
     "label": "EUR", "pair": "GBP", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "EURJPYm", "alts": ["EURJPYm", "EURJPY"],
     "label": "EUR", "pair": "JPY", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "GBPJPYm", "alts": ["GBPJPYm", "GBPJPY"],
     "label": "GBP", "pair": "JPY", "icon": "💷", "unit": "lots", "kind": "forex"},
    {"symbol": "AUDJPYm", "alts": ["AUDJPYm", "AUDJPY"],
     "label": "AUD", "pair": "JPY", "icon": "🇦🇺", "unit": "lots", "kind": "forex"},
    {"symbol": "NZDJPYm", "alts": ["NZDJPYm", "NZDJPY"],
     "label": "NZD", "pair": "JPY", "icon": "🇳🇿", "unit": "lots", "kind": "forex"},
    {"symbol": "CADJPYm", "alts": ["CADJPYm", "CADJPY"],
     "label": "CAD", "pair": "JPY", "icon": "🇨🇦", "unit": "lots", "kind": "forex"},
    {"symbol": "CHFJPYm", "alts": ["CHFJPYm", "CHFJPY"],
     "label": "CHF", "pair": "JPY", "icon": "🇨🇭", "unit": "lots", "kind": "forex"},
    {"symbol": "EURAUDm", "alts": ["EURAUDm", "EURAUD"],
     "label": "EUR", "pair": "AUD", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "EURCADm", "alts": ["EURCADm", "EURCAD"],
     "label": "EUR", "pair": "CAD", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "EURCHFm", "alts": ["EURCHFm", "EURCHF"],
     "label": "EUR", "pair": "CHF", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "EURNZDm", "alts": ["EURNZDm", "EURNZD"],
     "label": "EUR", "pair": "NZD", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "GBPAUDm", "alts": ["GBPAUDm", "GBPAUD"],
     "label": "GBP", "pair": "AUD", "icon": "💷", "unit": "lots", "kind": "forex"},
    {"symbol": "GBPCADm", "alts": ["GBPCADm", "GBPCAD"],
     "label": "GBP", "pair": "CAD", "icon": "💷", "unit": "lots", "kind": "forex"},
    {"symbol": "GBPCHFm", "alts": ["GBPCHFm", "GBPCHF"],
     "label": "GBP", "pair": "CHF", "icon": "💷", "unit": "lots", "kind": "forex"},
    {"symbol": "GBPNZDm", "alts": ["GBPNZDm", "GBPNZD"],
     "label": "GBP", "pair": "NZD", "icon": "💷", "unit": "lots", "kind": "forex"},
    {"symbol": "AUDCADm", "alts": ["AUDCADm", "AUDCAD"],
     "label": "AUD", "pair": "CAD", "icon": "🇦🇺", "unit": "lots", "kind": "forex"},
    {"symbol": "AUDCHFm", "alts": ["AUDCHFm", "AUDCHF"],
     "label": "AUD", "pair": "CHF", "icon": "🇦🇺", "unit": "lots", "kind": "forex"},
    {"symbol": "AUDNZDm", "alts": ["AUDNZDm", "AUDNZD"],
     "label": "AUD", "pair": "NZD", "icon": "🇦🇺", "unit": "lots", "kind": "forex"},
    {"symbol": "NZDCADm", "alts": ["NZDCADm", "NZDCAD"],
     "label": "NZD", "pair": "CAD", "icon": "🇳🇿", "unit": "lots", "kind": "forex"},
    {"symbol": "NZDCHFm", "alts": ["NZDCHFm", "NZDCHF"],
     "label": "NZD", "pair": "CHF", "icon": "🇳🇿", "unit": "lots", "kind": "forex"},
    {"symbol": "CADCHFm", "alts": ["CADCHFm", "CADCHF"],
     "label": "CAD", "pair": "CHF", "icon": "🇨🇦", "unit": "lots", "kind": "forex"},
    # ──────────────────── FOREX EXOTICS ────────────────────
    {"symbol": "USDSGDm", "alts": ["USDSGDm", "USDSGD"],
     "label": "USD", "pair": "SGD", "icon": "🇸🇬", "unit": "lots", "kind": "forex"},
    {"symbol": "USDHKDm", "alts": ["USDHKDm", "USDHKD"],
     "label": "USD", "pair": "HKD", "icon": "🇭🇰", "unit": "lots", "kind": "forex"},
    {"symbol": "USDTRYm", "alts": ["USDTRYm", "USDTRY"],
     "label": "USD", "pair": "TRY", "icon": "🇹🇷", "unit": "lots", "kind": "forex"},
    {"symbol": "USDZARm", "alts": ["USDZARm", "USDZAR"],
     "label": "USD", "pair": "ZAR", "icon": "🇿🇦", "unit": "lots", "kind": "forex"},
    {"symbol": "USDMXNm", "alts": ["USDMXNm", "USDMXN"],
     "label": "USD", "pair": "MXN", "icon": "🇲🇽", "unit": "lots", "kind": "forex"},
    {"symbol": "USDCNHm", "alts": ["USDCNHm", "USDCNH"],
     "label": "USD", "pair": "CNH", "icon": "🇨🇳", "unit": "lots", "kind": "forex"},
    {"symbol": "USDSEKm", "alts": ["USDSEKm", "USDSEK"],
     "label": "USD", "pair": "SEK", "icon": "🇸🇪", "unit": "lots", "kind": "forex"},
    {"symbol": "USDNOKm", "alts": ["USDNOKm", "USDNOK"],
     "label": "USD", "pair": "NOK", "icon": "🇳🇴", "unit": "lots", "kind": "forex"},
    {"symbol": "USDDKKm", "alts": ["USDDKKm", "USDDKK"],
     "label": "USD", "pair": "DKK", "icon": "🇩🇰", "unit": "lots", "kind": "forex"},
    {"symbol": "USDPLNm", "alts": ["USDPLNm", "USDPLN"],
     "label": "USD", "pair": "PLN", "icon": "🇵🇱", "unit": "lots", "kind": "forex"},
    {"symbol": "USDTHBm", "alts": ["USDTHBm", "USDTHB"],
     "label": "USD", "pair": "THB", "icon": "🇹🇭", "unit": "lots", "kind": "forex"},
    {"symbol": "EURTRYm", "alts": ["EURTRYm", "EURTRY"],
     "label": "EUR", "pair": "TRY", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "EURSGDm", "alts": ["EURSGDm", "EURSGD"],
     "label": "EUR", "pair": "SGD", "icon": "💶", "unit": "lots", "kind": "forex"},
    # ──────────────────── ENERGY ────────────────────
    {"symbol": "USOILm", "alts": ["USOILm", "XTIUSDm", "USOIL", "XTIUSD"],
     "label": "WTI OIL", "pair": "USD", "icon": "🛢️", "unit": "bbl", "kind": "energy"},
    {"symbol": "UKOILm", "alts": ["UKOILm", "XBRUSDm", "UKOIL", "XBRUSD"],
     "label": "BRENT", "pair": "USD", "icon": "🛢️", "unit": "bbl", "kind": "energy"},
    {"symbol": "XNGUSDm", "alts": ["XNGUSDm", "NGASm", "XNGUSD", "NGAS"],
     "label": "NAT GAS", "pair": "USD", "icon": "🔥", "unit": "mmbtu", "kind": "energy"},
    # ──────────────────── INDICES ────────────────────
    {"symbol": "USTECm", "alts": ["USTECm", "NAS100m", "US100m", "USTEC"],
     "label": "US100", "pair": "USD", "icon": "📈", "unit": "index", "kind": "indices"},
    {"symbol": "US500m", "alts": ["US500m", "SPX500m", "US500", "SPXm"],
     "label": "US500", "pair": "USD", "icon": "📊", "unit": "index", "kind": "indices"},
    {"symbol": "US30m", "alts": ["US30m", "DJ30m", "US30", "DJIm"],
     "label": "US30", "pair": "USD", "icon": "🏛️", "unit": "index", "kind": "indices"},
    {"symbol": "DE30m", "alts": ["DE30m", "DE40m", "DAXm", "GER40m", "DE30", "GER40"],
     "label": "DAX", "pair": "EUR", "icon": "🇩🇪", "unit": "index", "kind": "indices"},
    {"symbol": "UK100m", "alts": ["UK100m", "FTSE100m", "UK100"],
     "label": "UK100", "pair": "GBP", "icon": "🇬🇧", "unit": "index", "kind": "indices"},
    {"symbol": "FR40m", "alts": ["FR40m", "FRA40m", "FR40"],
     "label": "FR40", "pair": "EUR", "icon": "🇫🇷", "unit": "index", "kind": "indices"},
    {"symbol": "JP225m", "alts": ["JP225m", "JPN225m", "JP225"],
     "label": "JP225", "pair": "JPY", "icon": "🇯🇵", "unit": "index", "kind": "indices"},
    {"symbol": "AUS200m", "alts": ["AUS200m", "AU200m", "AUS200"],
     "label": "AUS200", "pair": "AUD", "icon": "🇦🇺", "unit": "index", "kind": "indices"},
    {"symbol": "HK50m", "alts": ["HK50m", "HK50"],
     "label": "HK50", "pair": "HKD", "icon": "🇭🇰", "unit": "index", "kind": "indices"},
    {"symbol": "STOXX50m", "alts": ["STOXX50m", "EU50m", "STOXX50"],
     "label": "EU50", "pair": "EUR", "icon": "🇪🇺", "unit": "index", "kind": "indices"},
    {"symbol": "US2000m", "alts": ["US2000m", "RUSS2000m", "US2000"],
     "label": "US2000", "pair": "USD", "icon": "📉", "unit": "index", "kind": "indices"},
    {"symbol": "ChinaHm", "alts": ["ChinaHm", "ChinaA50m", "ChinaH"],
     "label": "CHINA-H", "pair": "HKD", "icon": "🇨🇳", "unit": "index", "kind": "indices"},
    # ──────────────────── STOCKS (US CFDs) ────────────────────
    {"symbol": "AAPLm", "alts": ["AAPLm", "AAPL"],
     "label": "APPLE", "pair": "USD", "icon": "🍎", "unit": "shares", "kind": "stocks"},
    {"symbol": "MSFTm", "alts": ["MSFTm", "MSFT"],
     "label": "MSFT", "pair": "USD", "icon": "🪟", "unit": "shares", "kind": "stocks"},
    {"symbol": "AMZNm", "alts": ["AMZNm", "AMZN"],
     "label": "AMZN", "pair": "USD", "icon": "📦", "unit": "shares", "kind": "stocks"},
    {"symbol": "GOOGLm", "alts": ["GOOGLm", "GOOGm", "GOOGL"],
     "label": "GOOGLE", "pair": "USD", "icon": "🔍", "unit": "shares", "kind": "stocks"},
    {"symbol": "TSLAm", "alts": ["TSLAm", "TSLA"],
     "label": "TESLA", "pair": "USD", "icon": "⚡", "unit": "shares", "kind": "stocks"},
    {"symbol": "NVDAm", "alts": ["NVDAm", "NVDA"],
     "label": "NVIDIA", "pair": "USD", "icon": "🖥️", "unit": "shares", "kind": "stocks"},
    {"symbol": "METAm", "alts": ["METAm", "META", "FBm"],
     "label": "META", "pair": "USD", "icon": "👤", "unit": "shares", "kind": "stocks"},
    {"symbol": "JPMm", "alts": ["JPMm", "JPM"],
     "label": "JPMORGAN", "pair": "USD", "icon": "🏦", "unit": "shares", "kind": "stocks"},
    {"symbol": "BAm", "alts": ["BAm", "BA"],
     "label": "BOEING", "pair": "USD", "icon": "✈️", "unit": "shares", "kind": "stocks"},
    {"symbol": "DISm", "alts": ["DISm", "DIS"],
     "label": "DISNEY", "pair": "USD", "icon": "🏰", "unit": "shares", "kind": "stocks"},
    {"symbol": "NKEm", "alts": ["NKEm", "NKE"],
     "label": "NIKE", "pair": "USD", "icon": "👟", "unit": "shares", "kind": "stocks"},
    {"symbol": "COINm", "alts": ["COINm", "COIN"],
     "label": "COINBASE", "pair": "USD", "icon": "🪙", "unit": "shares", "kind": "stocks"},
    {"symbol": "NEFLm", "alts": ["NEFLm", "NFLXm", "NFLX"],
     "label": "NETFLIX", "pair": "USD", "icon": "🎬", "unit": "shares", "kind": "stocks"},
    {"symbol": "PFEm", "alts": ["PFEm", "PFE"],
     "label": "PFIZER", "pair": "USD", "icon": "💊", "unit": "shares", "kind": "stocks"},
    {"symbol": "INTCm", "alts": ["INTCm", "INTC"],
     "label": "INTEL", "pair": "USD", "icon": "🔲", "unit": "shares", "kind": "stocks"},
    {"symbol": "AMDm", "alts": ["AMDm", "AMD"],
     "label": "AMD", "pair": "USD", "icon": "🔳", "unit": "shares", "kind": "stocks"},
    {"symbol": "Vm", "alts": ["Vm", "V"],
     "label": "VISA", "pair": "USD", "icon": "💳", "unit": "shares", "kind": "stocks"},
    {"symbol": "MAm", "alts": ["MAm", "MA"],
     "label": "MASTERCARD", "pair": "USD", "icon": "💳", "unit": "shares", "kind": "stocks"},
    {"symbol": "KOm", "alts": ["KOm", "KO"],
     "label": "COCA-COLA", "pair": "USD", "icon": "🥤", "unit": "shares", "kind": "stocks"},
    {"symbol": "WMTm", "alts": ["WMTm", "WMT"],
     "label": "WALMART", "pair": "USD", "icon": "🛒", "unit": "shares", "kind": "stocks"},
    {"symbol": "XOMm", "alts": ["XOMm", "XOM"],
     "label": "EXXON", "pair": "USD", "icon": "⛽", "unit": "shares", "kind": "stocks"},
]

# How often the sampler wakes up. MT5 quotes are normally grouped into a few
# updates per second, so polling at 10 fps keeps the UI live without saturating
# the terminal's single IPC pipe.
TICK_INTERVAL = 0.1
# Tick-volume (participation) is read from the live M1 bar at this rate.
VOLUME_INTERVAL = 0.1
# A big print needs a price impulse this many times larger than the recent
# median tick move, and at least this many spreads wide.
IMPULSE_MEDIAN_FACTOR = 2.5
IMPULSE_SPREAD_FACTOR = 1.0
PRINT_COOLDOWN = 0.8
FOLLOW_AFTER = 6.0


class ChartUnavailable(RuntimeError):
    """Raised when the Exness terminal / symbol cannot provide data."""


def _tf_const(tf: str) -> int:
    if mt5 is None:
        raise ChartUnavailable("MetaTrader5 package is not installed")
    name = _TF_NAME.get(tf)
    if not name:
        raise ChartUnavailable(f"unknown timeframe: {tf}")
    const = getattr(mt5, name, None)
    if const is None:
        raise ChartUnavailable(f"MetaTrader5 has no {name}")
    return int(const)


# How long an attach probe is trusted before terminal_info() is asked again.
_ATTACH_TTL = 1.0
_attach_checked_at: float = 0.0
_attach_checked_for: Any = None
_last_init_attempt: float = 0.0
_last_init_for: Any = None
_init_cooldown: float = 4.0


def _try_ensure_terminal() -> bool:
    """Non-blocking, cached terminal attach probe. Returns True if MT5 is live."""
    global _attach_checked_at, _attach_checked_for, _last_init_attempt, _last_init_for
    if mt5 is None:
        return False
    now = time.monotonic()
    if (_attach_checked_for is mt5
            and (now - _attach_checked_at) < _ATTACH_TTL):
        return True
    with IPC_LOCK:
        try:
            info = mt5.terminal_info()
            if info is not None:
                _attach_checked_for = mt5
                _attach_checked_at = now
                return True
        except Exception:
            pass
        if _last_init_for is mt5 and (now - _last_init_attempt) < _init_cooldown:
            return False
        _last_init_attempt = now
        _last_init_for = mt5
        kwargs: Dict[str, Any] = {}
        path = preferred_terminal_path()
        if path:
            kwargs["path"] = path
        try:
            # 1200ms timeout prevents freezing API handlers when MT5 is offline
            if mt5.initialize(timeout=1200, **kwargs):
                _attach_checked_for = mt5
                _attach_checked_at = now
                return True
        except Exception:
            pass
    return False


def _ensure_terminal() -> None:
    """Best effort attach to MT5 terminal without ever throwing or freezing."""
    _try_ensure_terminal()


def _symbol_info(symbol: str) -> Optional[Any]:
    if not _try_ensure_terminal():
        return None
    with IPC_LOCK:
        try:
            info = mt5.symbol_info(symbol)
            if info is None:
                return None
            if not getattr(info, "visible", True) or not getattr(info, "select", True):
                try:
                    mt5.symbol_select(symbol, True)
                except Exception:
                    pass
            return info
        except Exception:
            return None


def _meta_from_spec_default(spec: Dict[str, Any], symbol: Optional[str] = None) -> Dict[str, Any]:
    sym = symbol or spec["symbol"]
    kind = spec.get("kind", "forex")
    if kind == "forex":
        digits = 3 if "JPY" in sym else 5
    elif kind in ("crypto", "stocks"):
        digits = 2
    elif kind == "metals":
        digits = 2 if "XAU" in sym else 3
    else:
        digits = 2
    point = float(10 ** -digits)
    return {
        "symbol": sym,
        "label": spec.get("label", sym),
        "pair": spec.get("pair", "USD"),
        "icon": spec.get("icon", "📊"),
        "unit": spec.get("unit", "lots"),
        "kind": spec.get("kind", "other"),
        "alts": [str(a) for a in spec.get("alts", [sym])],
        "description": f"{spec.get('label', sym)} / {spec.get('pair', 'USD')} · Exness MT5",
        "digits": digits,
        "point": point,
        "spread_points": 10,
        "contract": 1.0,
        "currency": spec.get("pair", "USD"),
        "mt5_live": False,
    }


def _meta_from_info(symbol: str, spec: Dict[str, Any], info: Any) -> Dict[str, Any]:
    digits = int(getattr(info, "digits", 2) or 2)
    point = float(getattr(info, "point", 0.0) or (10 ** -digits))
    return {
        "symbol": symbol,
        "label": spec["label"],
        "pair": spec["pair"],
        "icon": spec["icon"],
        "unit": spec["unit"],
        "kind": spec["kind"],
        "alts": [str(a) for a in spec.get("alts", [])],
        "description": str(getattr(info, "description", "") or symbol),
        "digits": digits,
        "point": point,
        "spread_points": int(getattr(info, "spread", 0) or 0),
        "contract": float(getattr(info, "trade_contract_size", 1.0) or 1.0),
        "currency": str(getattr(info, "currency_profit", "USD") or "USD"),
        "mt5_live": True,
    }


def _round_price(value: float, digits: int) -> float:
    try:
        return round(float(value), int(digits))
    except Exception:
        return 0.0


def _tick_dict(tick: Any) -> Optional[Dict[str, Any]]:
    if tick is None:
        return None
    bid = float(getattr(tick, "bid", 0.0) or 0.0)
    ask = float(getattr(tick, "ask", 0.0) or 0.0)
    if bid <= 0 and ask <= 0:
        return None
    ms = int(getattr(tick, "time_msc", 0) or 0)
    if ms <= 0:
        ms = int(getattr(tick, "time", 0) or 0) * 1000
    return {
        "bid": bid,
        "ask": ask,
        "ms": ms,
        "time": int(getattr(tick, "time", 0) or 0),
        "volume": int(getattr(tick, "volume", 0) or 0),
        "volume_real": float(getattr(tick, "volume_real", 0.0) or 0.0),
    }


def _bars_from_rates(rates: Any) -> List[List[float]]:
    if rates is None:
        return []
    out: List[List[float]] = []
    for r in rates:
        try:
            out.append([
                int(r["time"]) * 1000,
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                int(r["tick_volume"]),
            ])
        except Exception:
            continue
    return out


_last_gold_fetch_ts: float = 0.0
_cached_gold_price: float = 4087.50


def _get_live_gold_price() -> float:
    """Fetch live real-world Gold price (PAXG 1oz spot) from Binance or fallback."""
    global _last_gold_fetch_ts, _cached_gold_price
    now = time.time()
    if (now - _last_gold_fetch_ts) > 15.0:
        _last_gold_fetch_ts = now
        try:
            import urllib.request, json
            req = urllib.request.Request(
                "https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode())
                px = float(data.get("price") or 0.0)
                if px > 1000.0:
                    _cached_gold_price = round(px, 2)
                    BASELINE_PRICES["XAUUSD"] = _cached_gold_price
                    BASELINE_PRICES["GOLD"] = _cached_gold_price
                    BASELINE_PRICES["XAU"] = _cached_gold_price
        except Exception:
            pass
    return _cached_gold_price


def _get_anchor_price(symbol: str) -> float:
    clean = symbol.replace("m", "").replace("247", "").upper()
    if "XAU" in clean or "GOLD" in clean:
        return _get_live_gold_price()
    for k, v in BASELINE_PRICES.items():
        if k in clean:
            return v
    return 100.0


BASELINE_PRICES: Dict[str, float] = {
    "XAUUSD": 4087.50,
    "GOLD": 4087.50,
    "XAU": 4087.50,
    "XAGUSD": 48.20,
    "XPTUSD": 1025.00,
    "XPDUSD": 1060.00,
    "BTC": 67850.00,
    "ETH": 2645.00,
    "SOL": 172.50,
    "XRP": 0.545,
    "DOGE": 0.142,
    "LTC": 73.50,
    "ADA": 0.355,
    "BNB": 595.00,
    "EURUSD": 1.0835,
    "GBPUSD": 1.2980,
    "USDJPY": 152.40,
    "AUDUSD": 0.6650,
    "NZDUSD": 0.6020,
    "USDCAD": 1.3850,
    "USDCHF": 0.8660,
    "EURJPY": 165.10,
    "GBPJPY": 197.80,
    "EURGBP": 0.8350,
    "AUDJPY": 101.30,
    "USOIL": 71.80,
    "UKOIL": 75.60,
    "XNGUSD": 2.42,
    "USTEC": 20380.0,
    "NAS100": 20380.0,
    "US500": 5860.0,
    "US30": 42950.0,
    "DE30": 19480.0,
    "UK100": 8260.0,
    "JP225": 38900.0,
    "AAPL": 231.50,
    "NVDA": 139.20,
    "TSLA": 218.40,
    "MSFT": 428.00,
    "AMZN": 186.50,
    "GOOGL": 166.80,
    "META": 578.00,
    "COIN": 215.00,
}


def _generate_baseline_bars(symbol: str, tf: str, count: int = 300) -> List[List[float]]:
    base_px = _get_anchor_price(symbol)
    sec = TF_SEC.get(tf, 300)
    now_sec = int(time.time() // sec * sec)
    start_sec = now_sec - (count - 1) * sec
    bars: List[List[float]] = []
    px = base_px
    volatility = base_px * 0.0012
    import random
    rng = random.Random(hash(symbol + tf) & 0xffffffff)
    for i in range(count):
        tm = (start_sec + i * sec) * 1000
        chg = rng.gauss(0, volatility)
        o = px
        c = px + chg
        h = max(o, c) + abs(rng.gauss(0, volatility * 0.5))
        l = min(o, c) - abs(rng.gauss(0, volatility * 0.5))
        vol = int(abs(rng.gauss(75, 25)) + 12)
        bars.append([tm, round(o, 2), round(h, 2), round(l, 2), round(c, 2), vol])
        px = c
    return bars


def tape_from_bars(bars: List[List[float]], point: float, digits: int,
                   limit: int = 40) -> List[Dict[str, Any]]:
    """Seeds the tape with the biggest 1m bars of the last two hours."""
    if not bars or len(bars) < 12:
        return []
    window = bars[-120:]
    bodies = [abs(float(b[4]) - float(b[1])) for b in window]
    spreads = [max(1e-9, float(b[2]) - float(b[3])) for b in window]
    mean_body = statistics.fmean(bodies) if bodies else 0.0
    mean_range = statistics.fmean(spreads) if spreads else 0.0
    floor = max(mean_body * 1.8, mean_range * 0.6, point * 5)
    out: List[Dict[str, Any]] = []
    for i, b in enumerate(window[:-1]):
        o, h, l, c, vol = (float(b[1]), float(b[2]), float(b[3]),
                           float(b[4]), int(b[5]))
        body = c - o
        if abs(body) < floor:
            continue
        side = "BUY" if body >= 0 else "SELL"
        nxt = window[i + 1]
        follow = (float(nxt[4]) - c) * (1.0 if side == "BUY" else -1.0)
        out.append({
            "id": "h%s" % int(b[0]),
            "t": "print",
            "s": "",
            "tm": int(b[0]),
            "px": _round_price(c, digits),
            "side": side,
            "ticks": vol,
            "mv": _round_price(abs(body), digits),
            "fv": _round_price(follow, digits),
            "src": "1m",
        })
    out.sort(key=lambda r: r["tm"], reverse=True)
    return out[:limit]


class ChartFeed:
    """Owns the MT5 market-data cache, the sampler thread and the WS fan-out."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._clients: Dict[int, Dict[str, Any]] = {}
        self._subs: Dict[str, int] = {}
        self._states: Dict[str, Dict[str, Any]] = {}
        self._next_id = 1
        self._meta_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._symbols_cache: Tuple[float, Dict[str, Any]] = (0.0, {})
        self._rates_cache: Dict[Tuple[str, str, int], Tuple[float, List[List[float]]]] = {}
        self._tick_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
        self._ticker_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._tape_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ terminal
    def symbols(self) -> Dict[str, Any]:
        now = time.time()
        cached_at, cached = self._symbols_cache
        if cached and (now - cached_at) < 15.0 and cached.get("available"):
            return cached

        mt5_live = _try_ensure_terminal()
        acc = None
        if mt5_live:
            try:
                with IPC_LOCK:
                    acc = mt5.account_info()
            except Exception:
                acc = None

        available_names: Optional[set[str]] = None
        if mt5_live and hasattr(mt5, "symbols_get"):
            try:
                with IPC_LOCK:
                    listed = mt5.symbols_get()
                if listed is not None:
                    available_names = {
                        str(getattr(item, "name", "") or "").lower()
                        for item in listed
                    }
            except Exception:
                available_names = None

        rows: List[Dict[str, Any]] = []
        for spec in CATALOG:
            resolved_sym = spec["symbol"]
            info = None
            if mt5_live:
                for cand in spec.get("alts", [spec["symbol"]]):
                    if available_names is not None and cand.lower() not in available_names:
                        continue
                    try:
                        info = _symbol_info(cand)
                    except Exception:
                        info = None
                    if info is not None:
                        resolved_sym = cand
                        break
            # Never advertise an instrument that the connected Exness account
            # does not actually expose. A default catalogue row used to make
            # the browser open a synthetic chart before MT5 was connected.
            if info is not None:
                rows.append(_meta_from_info(resolved_sym, spec, info))

        payload = {
            "available": bool(mt5_live and rows),
            "connected": bool(mt5_live),
            "message": ("Exness MT5 Live" if rows else
                        "Connect and log in to an Exness MetaTrader 5 terminal"),
            "symbols": rows,
            "count": len(rows),
            "account": {
                "login": int(getattr(acc, "login", 0) or 0) if acc else 0,
                "server": str(getattr(acc, "server", "") or "") if acc else "",
            },
            "server_time": int(time.time()),
        }
        self._symbols_cache = (now, payload)
        return payload

    def meta(self, symbol: str) -> Dict[str, Any]:
        symbol = str(symbol or "").strip()
        if not symbol:
            symbol = "XAUUSDm"
        if is_bd_symbol(symbol) and _bd_meta is not None:
            try:
                return _bd_meta(symbol)
            except Exception:
                pass
        now = time.time()
        hit = self._meta_cache.get(symbol)
        if hit and now - hit[0] < 30.0:
            return hit[1]

        spec = next((s for s in CATALOG if symbol in s.get("alts", []) or s["symbol"] == symbol), None)
        if spec is None:
            clean = symbol.replace("m", "").replace("247", "").upper()
            spec = {"symbol": symbol, "label": clean, "pair": "USD", "icon": "📊",
                    "unit": "lots", "kind": "forex", "alts": [symbol]}

        info = _symbol_info(symbol)
        if info is not None:
            meta = _meta_from_info(symbol, spec, info)
        else:
            meta = _meta_from_spec_default(spec, symbol)
        self._meta_cache[symbol] = (now, meta)
        return meta

    # --------------------------------------------------------------- data
    def _read_rates(self, symbol: str, tf: str, count: int) -> List[List[float]]:
        if is_bd_symbol(symbol) and _bd_candles is not None:
            return _bd_candles(symbol, tf, count)
        if not _try_ensure_terminal():
            raise ChartUnavailable("Exness MetaTrader 5 terminal is not connected")
        const = _tf_const(tf)
        try:
            with IPC_LOCK:
                rates = mt5.copy_rates_from_pos(symbol, const, 0, int(count))
            bars = _bars_from_rates(rates)
        except Exception as exc:
            raise ChartUnavailable("Exness MT5 could not return candle data") from exc
        if not bars:
            raise ChartUnavailable("No candles are available for %s in Exness MT5" % symbol)
        return bars

    def candles(self, symbol: str, tf: str = "5m", count: int = 600) -> List[List[float]]:
        if tf not in TF_SEC:
            tf = "5m"
        key = (symbol, tf, int(count))
        now = time.time()
        hit = self._rates_cache.get(key)
        if hit and now - hit[0] < 1.0:
            return hit[1]
        bars = self._read_rates(symbol, tf, count)
        self._rates_cache[key] = (now, bars)
        return bars

    def tick(self, symbol: str, max_age: float = 0.02) -> Optional[Dict[str, Any]]:
        now = time.time()
        hit = self._tick_cache.get(symbol)
        if hit and now - hit[0] < max_age:
            return hit[1]
        if is_bd_symbol(symbol) and _bd_tick is not None:
            tick = _bd_tick(symbol)
            self._tick_cache[symbol] = (now, tick)
            return tick
        if _try_ensure_terminal():
            try:
                with IPC_LOCK:
                    raw = mt5.symbol_info_tick(symbol)
                tick = _tick_dict(raw)
                if tick:
                    self._tick_cache[symbol] = (now, tick)
                    return tick
            except Exception:
                pass
        if is_bd_symbol(symbol):
            return None
        raise ChartUnavailable("Exness MetaTrader 5 has no live tick for %s" % symbol)

    def ticker(self, symbol: str) -> Dict[str, Any]:
        now = time.time()
        hit = self._ticker_cache.get(symbol)
        if hit and now - hit[0] < 5.0:
            return hit[1]
        meta = self.meta(symbol)
        tick = self.tick(symbol, max_age=0.2)
        rates = self._read_rates(symbol, "1m", 1440)
        high = max(float(b[2]) for b in rates)
        low = min(float(b[3]) for b in rates)
        open24 = float(rates[0][1])
        vol = sum(int(b[5]) for b in rates)
        price = float(tick["bid"]) if tick else float(rates[-1][4])
        change = ((price - open24) / open24 * 100.0) if open24 else 0.0
        digits = int(meta["digits"])
        payload = {
            "symbol": symbol,
            "price": _round_price(price, digits),
            "open24": _round_price(open24, digits),
            "change": round(change, 2),
            "high": _round_price(high, digits),
            "low": _round_price(low, digits),
            "vol": int(vol),
            "digits": digits,
            "unit": meta["unit"],
            "currency": meta["currency"],
            "updated_at": int(now * 1000),
        }
        self._ticker_cache[symbol] = (now, payload)
        return payload

    def snapshot(self, symbol: str, tf: str = "5m", count: int = 600) -> Dict[str, Any]:
        symbol = str(symbol or "").strip()
        if tf not in TF_SEC:
            raise ChartUnavailable("unknown timeframe: %s" % tf)
        meta = self.meta(symbol)
        # The rate cache is shared between browsers: never write into it. Only the
        # trailing bar is copied, and only that copy follows the live tick.
        bars = list(self.candles(symbol, tf, count))
        if bars:
            bars[-1] = list(bars[-1])
        tick = self.tick(symbol, max_age=0.1)
        ticker = self.ticker(symbol)
        # A symbol can quote but have no candles yet (fresh listing, closed
        # session); never index into an empty bar list.
        if tick and bars:
            offset = float(tick["ms"]) / 1000.0 - time.time()
            last_bar = bars[-1]
            bucket = int(float(tick["ms"]) / 1000.0 // TF_SEC[tf] * TF_SEC[tf] * 1000)
            price = float(tick["bid"])
            if bucket == int(last_bar[0]):
                last_bar[2] = max(float(last_bar[2]), price)
                last_bar[3] = min(float(last_bar[3]), price)
                last_bar[4] = price
            elif bucket > int(last_bar[0]):
                # First print of a fresh bar: open at the previous close. High
                # and low must both span open <-> price, otherwise the candle
                # would draw an inverted wick when the tick is below the close.
                open_px = float(last_bar[4])
                bars.append([bucket, open_px, max(open_px, price), min(open_px, price), price, 0])
        else:
            offset = 0.0
        return {
            "t": "snap",
            "s": symbol,
            "tf": tf,
            "tf_sec": TF_SEC[tf],
            "offset": round(offset, 3),
            "meta": meta,
            "bars": bars,
            "ticker": ticker,
            "tape": self.tape_history(symbol),
            "now_ms": int(time.time() * 1000),
        }

    def tape_history(self, symbol: str, limit: int = 40) -> List[Dict[str, Any]]:
        now = time.time()
        hit = self._tape_cache.get(symbol)
        if hit and now - hit[0] < 20.0:
            return hit[1]
        meta = self.meta(symbol)
        bars = self._read_rates(symbol, "1m", 180)
        rows = tape_from_bars(bars, float(meta["point"]), int(meta["digits"]), limit)
        for row in rows:
            row["s"] = symbol
        self._tape_cache[symbol] = (now, rows)
        return rows

    def _m1_volume(self, symbol: str) -> Tuple[int, int]:
        """(current M1 bar time, its tick volume) - the participation counter."""
        if is_bd_symbol(symbol) and _bd_m1_volume is not None:
            try:
                return _bd_m1_volume(symbol)
            except Exception:
                return 0, 0
        _ensure_terminal()
        with IPC_LOCK:
            rates = mt5.copy_rates_from_pos(symbol, _tf_const("1m"), 0, 2)
        if rates is None or len(rates) == 0:
            return 0, 0
        last = rates[-1]
        return int(last["time"]), int(last["tick_volume"])

    # ------------------------------------------------------------- clients
    def add_client(self, loop: Any) -> Tuple[int, Any]:
        import asyncio

        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=300)
        with self._lock:
            cid = self._next_id
            self._next_id += 1
            self._clients[cid] = {"loop": loop, "queue": queue, "symbol": None}
        self._start_thread()
        return cid, queue

    def set_symbol(self, cid: int, symbol: str) -> None:
        symbol = str(symbol or "").strip()
        with self._lock:
            client = self._clients.get(cid)
            if not client:
                return
            old = client.get("symbol")
            if old == symbol:
                return
            if old:
                self._subs[old] = max(0, self._subs.get(old, 0) - 1)
                if self._subs[old] == 0:
                    self._subs.pop(old, None)
                    self._states.pop(old, None)
            client["symbol"] = symbol
            if symbol:
                self._subs[symbol] = self._subs.get(symbol, 0) + 1

    def remove_client(self, cid: int) -> None:
        with self._lock:
            client = self._clients.pop(cid, None)
            if not client:
                return
            old = client.get("symbol")
            if old:
                self._subs[old] = max(0, self._subs.get(old, 0) - 1)
                if self._subs[old] == 0:
                    self._subs.pop(old, None)
                    self._states.pop(old, None)

    def _push(self, loop: Any, queue: Any, frame: Dict[str, Any]) -> None:
        def put() -> None:
            try:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except Exception:
                        pass
                queue.put_nowait(frame)
            except Exception:
                pass

        try:
            loop.call_soon_threadsafe(put)
        except Exception:
            pass

    def _broadcast(self, symbol: str, frame: Dict[str, Any]) -> None:
        with self._lock:
            targets = [c for c in self._clients.values() if c.get("symbol") == symbol]
        for client in targets:
            self._push(client["loop"], client["queue"], frame)

    # ------------------------------------------------------------- sampler
    def _start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="chart-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                symbols = [s for s, n in self._subs.items() if n > 0]
            backoff = 0.0
            for symbol in symbols:
                try:
                    self._sample(symbol)
                except ChartUnavailable:
                    # The Exness terminal went away. Back off instead of
                    # hammering a dead pipe at 20 fps - _ensure_terminal in
                    # the next sample re-attaches automatically once the
                    # terminal (or its auto-login) returns.
                    backoff = 0.5
                except Exception:
                    pass
            self._stop.wait(backoff if backoff else TICK_INTERVAL)

    def _state(self, symbol: str) -> Dict[str, Any]:
        state = self._states.get(symbol)
        if state is not None:
            return state
        meta = self.meta(symbol)
        state = {
            "symbol": symbol,
            "point": float(meta["point"]),
            "digits": int(meta["digits"]),
            "spread": 0.0,
            "last_bid": None,
            "last_ms": 0,
            "bar_time": 0,
            "tickvol": 0,
            "vol_at": 0.0,
            "moves": deque(maxlen=400),
            "deltas": deque(maxlen=200),
            "acc_ticks": 0,
            "last_print": 0.0,
            "pending": deque(maxlen=20),
            "prints": deque(maxlen=80),
            "print_id": 0,
        }
        self._states[symbol] = state
        return state

    def _sample(self, symbol: str) -> None:
        state = self._state(symbol)
        now = time.time()
        tick = self.tick(symbol, max_age=TICK_INTERVAL * 0.9)
        if not tick:
            return
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        ms = int(tick["ms"])
        digits = int(state["digits"])
        point = float(state["point"]) or (10 ** -digits)
        spread = max(0.0, ask - bid)
        state["spread"] = spread
        # Follow-through is measured on wall-clock time, so it must run even
        # while the feed is quiet (the Exness demo feed groups ticks).
        self._followups(state, bid, now)

        changed = (ms != state["last_ms"]) or (bid != state["last_bid"])
        if not changed:
            return

        # --- participation: how many ticks printed in this slice -------------
        delta = 0
        if now - float(state["vol_at"]) >= VOLUME_INTERVAL:
            try:
                bar_time, tickvol = self._m1_volume(symbol)
            except Exception:
                bar_time, tickvol = 0, 0
            if bar_time:
                if state["bar_time"] == bar_time:
                    delta = max(0, tickvol - int(state["tickvol"]))
                else:
                    # New minute: the whole new-bar volume belongs to this slice.
                    delta = max(0, min(tickvol, 200))
            state["bar_time"] = bar_time
            state["tickvol"] = tickvol
            state["vol_at"] = now
            if delta:
                state["deltas"].append((now, delta))
                state["acc_ticks"] = min(999, int(state["acc_ticks"]) + delta)

        self._broadcast(symbol, {
            "t": "tick",
            "s": symbol,
            "b": _round_price(bid, digits),
            "a": _round_price(ask, digits),
            "tm": ms,
            "d": delta,
        })

        last_bid = state["last_bid"]
        last_print = float(state["last_print"])
        if last_bid is None:
            state["last_bid"] = bid
            state["last_ms"] = ms
            return

        move = bid - float(last_bid)
        state["moves"].append(abs(move))

        median = statistics.median(state["moves"]) if len(state["moves"]) >= 20 else 0.0
        threshold = max(spread * IMPULSE_SPREAD_FACTOR, median * IMPULSE_MEDIAN_FACTOR,
                        point * 2.0)
        if abs(move) >= threshold and now - last_print >= PRINT_COOLDOWN:
            side = "BUY" if move > 0 else "SELL"
            state["print_id"] = int(state["print_id"]) + 1
            pid = "%s-%d" % (symbol, state["print_id"])
            ticks = int(state["acc_ticks"])
            state["acc_ticks"] = 0
            print_row = {
                "t": "print",
                "id": pid,
                "s": symbol,
                "tm": int(now * 1000),
                "px": _round_price(bid, digits),
                "side": side,
                "ticks": ticks,
                "mv": _round_price(abs(move), digits),
                "src": "live",
            }
            state["prints"].appendleft(print_row)
            state["pending"].append({
                "id": pid,
                "at": now,
                "px": bid,
                "side": side,
                "spread": spread,
            })
            state["last_print"] = now
            self._broadcast(symbol, print_row)

        state["last_bid"] = bid
        state["last_ms"] = ms

    def _followups(self, state: Dict[str, Any], bid: float, now: float) -> None:
        """Reports whether price moved with each big print, 6s after it."""
        if not state["pending"]:
            return
        symbol = str(state["symbol"])
        digits = int(state["digits"])
        point = float(state["point"]) or (10 ** -digits)
        for pending in list(state["pending"]):
            if now - float(pending["at"]) < FOLLOW_AFTER:
                continue
            direction = 1.0 if pending["side"] == "BUY" else -1.0
            follow = (bid - float(pending["px"])) * direction
            floor = max(float(pending["spread"]), point * 2.0)
            if follow > floor:
                verdict = "UP"
            elif follow < -floor:
                verdict = "DOWN"
            else:
                verdict = "FLAT"
            self._broadcast(symbol, {
                "t": "follow",
                "id": pending["id"],
                "s": symbol,
                "mv": _round_price(follow, digits),
                "st": verdict,
                "px": _round_price(bid, digits),
            })
            try:
                state["pending"].remove(pending)
            except ValueError:
                pass

    def recent_prints(self, symbol: str, limit: int = 30) -> List[Dict[str, Any]]:
        state = self._states.get(symbol)
        if not state:
            return []
        return list(state["prints"])[:limit]


_FEED: Optional[ChartFeed] = None
_FEED_LOCK = threading.Lock()


def get_feed() -> ChartFeed:
    global _FEED
    with _FEED_LOCK:
        if _FEED is None:
            _FEED = ChartFeed()
        return _FEED
