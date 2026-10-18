//+------------------------------------------------------------------+
//|  DashboardBridge.mq5                                             |
//|  Standalone EA: attach it to ANY chart and every trade on the     |
//|  account is streamed to the Exness Trading Dashboard backend.     |
//|                                                                   |
//|  IMPORTANT (one time setup):                                      |
//|    Tools > Options > Expert Advisors >                            |
//|      [x] Allow WebRequest for listed URL                          |
//|      add  http://127.0.0.1:8777                                   |
//|    Then paste the BridgeToken printed in the backend console      |
//|    at startup (same value is in config/config.json: ea_secret).   |
//+------------------------------------------------------------------+
#property copyright "Exness Trading Dashboard"
#property version   "1.00"
#property strict

#include "DashboardBridge.mqh"

//--- inputs ---------------------------------------------------------
input string InpBridgeUrl   = "http://127.0.0.1:8777";  // Dashboard URL
input string InpBridgeToken = "";                        // BridgeToken (from the dashboard)
input int    InpPushSeconds = 3;                         // Snapshot push interval (seconds)
input bool   InpVerbose     = false;                     // Print every push to the Experts log
input bool   InpShowComment = true;                      // Show status in the chart corner

//+------------------------------------------------------------------+
int OnInit()
{
   BridgeInit(InpBridgeUrl, InpBridgeToken, "DashboardBridge", InpVerbose);
   EventSetTimer(MathMax(InpPushSeconds, 1));
   BridgePushSnapshot();
   if(InpShowComment)
      Comment(BridgeStatusText());
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}

//+------------------------------------------------------------------+
void OnTimer()
{
   // a heartbeat alone keeps the light green even with zero open trades
   BridgeHeartbeat();
   BridgePushSnapshot();
   if(InpShowComment)
      Comment(BridgeStatusText());
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   BridgeOnTradeTransaction(trans, request, result);
}

//+------------------------------------------------------------------+
void OnTick()
{
   // nothing to do here - all pushes happen on a timer / on trade events
}
//+------------------------------------------------------------------+
