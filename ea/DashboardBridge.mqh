//+------------------------------------------------------------------+
//|  DashboardBridge.mqh                                             |
//|  Drop this include into any MetaTrader 5 Expert Advisor to push   |
//|  live trades into the Exness Trading Dashboard backend.           |
//|                                                                   |
//|  Minimal usage:                                                   |
//|     #include "DashboardBridge.mqh"                                |
//|     int OnInit(){ BridgeInit("http://127.0.0.1:8777", InpToken);  |
//|                   EventSetTimer(3); return(INIT_SUCCEEDED); }     |
//|     void OnTimer(){ BridgeHeartbeat(); BridgePushSnapshot(); }    |
//|     void OnTradeTransaction(...){ BridgeOnTradeTransaction(...);} |
//+------------------------------------------------------------------+
#property strict
#ifndef DASHBOARD_BRIDGE_MQH
#define DASHBOARD_BRIDGE_MQH

//--- configuration (set by BridgeInit) -------------------------------
string   g_bridgeUrl      = "http://127.0.0.1:8777/api/ea/event";
string   g_bridgeToken    = "";
string   g_bridgeEAName   = "MT5-EA";
bool     g_bridgeVerbose  = false;
bool     g_bridgeReady    = false;

//--- statistics ------------------------------------------------------
long     g_bridgeSent     = 0;
long     g_bridgeFailed   = 0;
int      g_bridgeLastErr  = 0;
datetime g_bridgeLastSend = 0;

//+------------------------------------------------------------------+
//| Re-ping the backend when the connection was lost (backend restart,|
//| network hiccup). Called by BridgePost - never gives up forever.   |
//+------------------------------------------------------------------+
bool BridgeEnsureConnected()
{
   if(g_bridgeReady)
      return(true);
   string base = g_bridgeUrl;
   int cut = StringFind(base, "/api/ea/event");
   if(cut > 0)
      base = StringSubstr(base, 0, cut);
   g_bridgeReady = BridgePing(base + "/api/ea/ping");
   if(g_bridgeReady && g_bridgeVerbose)
      Print("[Bridge] reconnected to ", base);
   return(g_bridgeReady);
}

//+------------------------------------------------------------------+
//| Initialise the bridge. Returns true when the backend is reachable.|
//+------------------------------------------------------------------+
bool BridgeInit(const string baseUrl, const string token, const string eaName = "MT5-EA",
                const bool verbose = false)
{
   g_bridgeEAName  = eaName;
   g_bridgeVerbose = verbose;
   g_bridgeToken   = token;

   string url = baseUrl;
   StringTrimLeft(url);
   StringTrimRight(url);
   if(StringLen(url) == 0)
      url = "http://127.0.0.1:8777";
   if(StringSubstr(url, StringLen(url) - 1, 1) == "/")
      url = StringSubstr(url, 0, StringLen(url) - 1);
   if(StringFind(url, "/api/ea/event") < 0)
      g_bridgeUrl = url + "/api/ea/event";
   else
      g_bridgeUrl = url;

   string pingUrl = url + "/api/ea/ping";
   g_bridgeReady  = BridgePing(pingUrl);

   if(g_bridgeVerbose)
   {
      if(g_bridgeReady)
         PrintFormat("[Bridge] connected to %s (token %s)", url, StringSubstr(token, 0, 6) + "...");
      else
         PrintFormat("[Bridge] cannot reach %s - error %d. Add the URL to Tools > Options > Expert Advisors > Allow WebRequest.",
                     url, g_bridgeLastErr);
   }
   return(g_bridgeReady);
}

//+------------------------------------------------------------------+
//| GET request (used for the health ping)                            |
//| The token travels on the ping too, so a wrong token shows up as   |
//| OFFLINE on the status line instead of silent 401s afterwards.     |
//+------------------------------------------------------------------+
bool BridgePing(const string pingUrl)
{
   char   post[], result[];
   string headers = "Content-Type: application/json\r\n";
   if(StringLen(g_bridgeToken) > 0)
      headers += "X-EA-Token: " + g_bridgeToken + "\r\n";
   headers += "\r\n";
   string resHeaders = "";
   ResetLastError();
   int code = WebRequest("GET", pingUrl, headers, 3000, post, result, resHeaders);
   g_bridgeLastErr = GetLastError();
   if(code == 200)
      return(true);
   return(false);
}

//+------------------------------------------------------------------+
//| Escape a string for JSON                                          |
//+------------------------------------------------------------------+
string BridgeEscape(const string src)
{
   string out = "";
   int len = StringLen(src);
   for(int i = 0; i < len; i++)
   {
      ushort c = StringGetCharacter(src, i);
      if(c == '"')
         out += "\\\"";
      else if(c == '\\')
         out += "\\\\";
      else if(c == '\n')
         out += "\\n";
      else if(c == '\r')
         out += "\\r";
      else if(c == '\t')
         out += "\\t";
      else if(c < 0x20)
         out += StringFormat("\\u%04x", (int)c);   // raw control chars = invalid JSON
      else
         out += ShortToString(c);
   }
   return(out);
}

//+------------------------------------------------------------------+
//| POST a JSON payload to the backend                                |
//+------------------------------------------------------------------+
bool BridgePost(const string json)
{
   if(!g_bridgeReady && !BridgeEnsureConnected())
      return(false);

   char   data[], result[];
   string headers = "Content-Type: application/json\r\n";
   if(StringLen(g_bridgeToken) > 0)
      headers += "X-EA-Token: " + g_bridgeToken + "\r\n";
   headers += "\r\n";

   // StringToCharArray counts *elements*; a UTF-8 body can need more bytes
   // than characters, so convert whole and drop the trailing null - a
   // char-counted conversion truncated multibyte content mid-sequence and
   // the backend rejected the JSON
   int copied = StringToCharArray(json, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(copied > 0)
      ArrayResize(data, copied - 1);
   else
      ArrayResize(data, 0);

   string resHeaders = "";
   ResetLastError();
   int code = WebRequest("POST", g_bridgeUrl, headers, 3000, data, result, resHeaders);
   g_bridgeLastErr = GetLastError();

   if(code == 200 || code == 201)
   {
      g_bridgeSent++;
      g_bridgeLastSend = TimeCurrent();
      if(g_bridgeVerbose)
         Print("[Bridge] sent: ", StringSubstr(json, 0, 160));
      return(true);
   }

   g_bridgeFailed++;
   if(code == -1)
   {
      // transport failure - force a fresh ping on the next timer tick
      g_bridgeReady = false;
      if(g_bridgeLastErr == 4060 && g_bridgeVerbose)
         Print("[Bridge] WebRequest blocked (4060). Tools > Options > Expert Advisors > add ",
               g_bridgeUrl, " to the allowed URLs.");
   }
   else if(g_bridgeVerbose)
      PrintFormat("[Bridge] HTTP %d (error %d)", code, g_bridgeLastErr);
   return(false);
}

//+------------------------------------------------------------------+
//| JSON for one open position                                        |
//+------------------------------------------------------------------+
string BridgePositionJson(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return("");

   string symbol = PositionGetString(POSITION_SYMBOL);
   int    type   = (int)PositionGetInteger(POSITION_TYPE);
   string side   = (type == POSITION_TYPE_BUY) ? "buy" : "sell";
   int    digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);

   return(StringFormat(
      "{\"ticket\":%I64u,\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%.2f,"
      "\"price_open\":%.*f,\"price_current\":%.*f,\"sl\":%.*f,\"tp\":%.*f,"
      "\"profit\":%.2f,\"swap\":%.2f,\"time\":%d,\"time_update\":%d,"
      "\"magic\":%I64d,\"comment\":\"%s\"}",
      ticket, BridgeEscape(symbol), side,
      PositionGetDouble(POSITION_VOLUME),
      digits, PositionGetDouble(POSITION_PRICE_OPEN),
      digits, PositionGetDouble(POSITION_PRICE_CURRENT),
      digits, PositionGetDouble(POSITION_SL),
      digits, PositionGetDouble(POSITION_TP),
      PositionGetDouble(POSITION_PROFIT),
      PositionGetDouble(POSITION_SWAP),
      (int)PositionGetInteger(POSITION_TIME),
      (int)PositionGetInteger(POSITION_TIME_UPDATE),
      (long)PositionGetInteger(POSITION_MAGIC),
      BridgeEscape(PositionGetString(POSITION_COMMENT))));
}

//+------------------------------------------------------------------+
//| Push the account + every open position (call from OnTimer)        |
//+------------------------------------------------------------------+
bool BridgePushSnapshot()
{
   if(!g_bridgeReady)
      return(false);

   string items = "";
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      string one = BridgePositionJson(ticket);
      if(StringLen(one) == 0)
         continue;
      if(StringLen(items) > 0)
         items += ",";
      items += one;
   }

   string account = StringFormat(
      "\"account\":{\"login\":%I64d,\"name\":\"%s\",\"server\":\"%s\",\"currency\":\"%s\","
      "\"leverage\":%d,\"balance\":%.2f,\"equity\":%.2f,\"margin\":%.2f,\"margin_free\":%.2f,"
      "\"margin_level\":%.2f,\"profit\":%.2f}",
      AccountInfoInteger(ACCOUNT_LOGIN),
      BridgeEscape(AccountInfoString(ACCOUNT_NAME)),
      BridgeEscape(AccountInfoString(ACCOUNT_SERVER)),
      BridgeEscape(AccountInfoString(ACCOUNT_CURRENCY)),
      (int)AccountInfoInteger(ACCOUNT_LEVERAGE),
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      AccountInfoDouble(ACCOUNT_MARGIN),
      AccountInfoDouble(ACCOUNT_MARGIN_FREE),
      AccountInfoDouble(ACCOUNT_MARGIN_LEVEL),
      AccountInfoDouble(ACCOUNT_PROFIT));

   string json = "{\"type\":\"snapshot\",\"ea_name\":\"" + BridgeEscape(g_bridgeEAName) + "\"," +
                 account + ",\"positions\":[" + items + "]}";
   return(BridgePost(json));
}

//+------------------------------------------------------------------+
//| Heartbeat (keeps the dashboard light green)                       |
//+------------------------------------------------------------------+
bool BridgeHeartbeat()
{
   string json = StringFormat(
      "{\"type\":\"heartbeat\",\"ea_name\":\"%s\",\"ticket\":0,\"symbol\":\"%s\","
      "\"time\":%d,\"sent\":%I64d,\"failed\":%I64d}",
      BridgeEscape(g_bridgeEAName), _Symbol, (int)TimeCurrent(), g_bridgeSent, g_bridgeFailed);
   return(BridgePost(json));
}

//+------------------------------------------------------------------+
//| Report a single deal (open or close)                              |
//+------------------------------------------------------------------+
bool BridgeReportDeal(const ulong dealTicket)
{
   if(!HistoryDealSelect(dealTicket))
      return(false);

   long   entry  = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
   long   dtype  = HistoryDealGetInteger(dealTicket, DEAL_TYPE);
   ulong  posId  = (ulong)HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
   string symbol = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
   double volume = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
   double price  = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
   double profit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
   double comm   = HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
   double swap   = HistoryDealGetDouble(dealTicket, DEAL_SWAP);
   long   magic  = HistoryDealGetInteger(dealTicket, DEAL_MAGIC);
   string comment= HistoryDealGetString(dealTicket, DEAL_COMMENT);
   datetime dtime= (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
   string side   = (dtype == DEAL_TYPE_BUY) ? "buy" : "sell";

   if(entry == DEAL_ENTRY_IN)
   {
      string json = StringFormat(
         "{\"type\":\"open\",\"ea_name\":\"%s\",\"account\":%I64d,\"trade\":{\"position_id\":\"%I64u\",\"ticket\":%I64u,"
         "\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%.2f,\"price_open\":%.*f,\"price_current\":%.*f,"
         "\"profit\":0,\"swap\":0,\"time\":%d,\"magic\":%I64d,\"comment\":\"%s\"}}",
         BridgeEscape(g_bridgeEAName), AccountInfoInteger(ACCOUNT_LOGIN), posId, posId,
         BridgeEscape(symbol), side, volume,
         (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS), price,
         (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS), price,
         (int)dtime, magic, BridgeEscape(comment));
      return(BridgePost(json));
   }

   if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY || entry == DEAL_ENTRY_INOUT)
   {
      string json = StringFormat(
         "{\"type\":\"close\",\"ea_name\":\"%s\",\"account\":%I64d,\"trade\":{\"position_id\":\"%I64u\",\"ticket\":%I64u,"
         "\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%.2f,\"close_price\":%.*f,"
         "\"profit\":%.2f,\"commission\":%.2f,\"swap\":%.2f,\"net_profit\":%.2f,"
         "\"close_time\":%d,\"magic\":%I64d,\"comment\":\"%s\"}}",
         BridgeEscape(g_bridgeEAName), AccountInfoInteger(ACCOUNT_LOGIN), posId, dealTicket,
         BridgeEscape(symbol), side, volume,
         (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS), price,
         profit, comm, swap, profit + comm + swap,
         (int)dtime, magic, BridgeEscape(comment));
      return(BridgePost(json));
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Call this from OnTradeTransaction()                               |
//+------------------------------------------------------------------+
void BridgeOnTradeTransaction(const MqlTradeTransaction &trans,
                              const MqlTradeRequest &request,
                              const MqlTradeResult &result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD && trans.deal > 0)
   {
      BridgeReportDeal(trans.deal);
      BridgePushSnapshot();
   }
}

//+------------------------------------------------------------------+
//| Small status line for the chart                                   |
//+------------------------------------------------------------------+
string BridgeStatusText()
{
   return(StringFormat("Dashboard bridge: %s | sent %I64d | failed %I64d | err %d | last %s",
                       g_bridgeReady ? "ONLINE" : "OFFLINE", g_bridgeSent, g_bridgeFailed,
                       g_bridgeLastErr,
                       (g_bridgeLastSend > 0 ? TimeToString(g_bridgeLastSend, TIME_DATE | TIME_SECONDS) : "never")));
}

#endif // DASHBOARD_BRIDGE_MQH
//+------------------------------------------------------------------+
