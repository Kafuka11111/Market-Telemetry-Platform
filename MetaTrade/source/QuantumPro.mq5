//+------------------------------------------------------------------+
//|                                                    QuantumPro.mq5|
//|                          Realtime Market Telemetry Platform" API |
//+------------------------------------------------------------------+
#property copyright "Quantum Radar"
#property link      "https://Address"
#property version   "1.00"

input group "--- API Settings ---"
input string   InpServer   = "https://Address"; 


input group "--- Timezone Settings ---"
input int      InpUtcOffset = 0;

input group "--- Display Filters (Whales) ---"
input double   InpChartWhaleVol  = 2000000;
input double   InpFooterWhaleVol = 500000;     

enum ENUM_EMA_MODE { EMA_NONE = 0, EMA_50 = 50, EMA_200 = 200, EMA_800 = 800, EMA_ALL = 999 };
enum ENUM_SESSION_MODE { SES_24H = 0, SES_ASIA = 1, SES_EU = 2, SES_US = 3 };
enum ENUM_VOL_MODE { VOL_TOTAL = 0, VOL_BAR = 1 };
enum ENUM_LIQ_MODE { LIQ_GROSS = 0, LIQ_NET = 1 };

#resource "\\Indicators\\QuantumPro_EMA.ex5"
#resource "\\Indicators\\QuantumPro_Volume.ex5"
#resource "\\Indicators\\QuantumPro_CVD.ex5"
#resource "\\Indicators\\QuantumPro_Liq.ex5"
#resource "\\Indicators\\QuantumPro_OI.ex5"
#resource "\\Indicators\\QuantumPro_Funding.ex5"

input group "--- Visuals & Levels ---"
input bool              InpShowWalls   = true;      
input ENUM_EMA_MODE     InpEmaMode     = EMA_800;   
input ENUM_SESSION_MODE InpSessionMode = SES_24H;   
input ENUM_VOL_MODE     InpVolumeMode  = VOL_TOTAL; 
input ENUM_LIQ_MODE     InpLiqMode     = LIQ_GROSS; 

input group "--- Sound Alerts ---"
input bool     InpEnableSounds   = true;
input double   InpAlertWhaleVol  = 2000000;    

string current_symbol = "";
int timer_ticks = 0; 
int current_tab = 0;

color clrBg     = C'8,10,14'; 
color clrBuy    = C'34,171,148'; 
color clrSell   = C'242,54,69'; 
color clrText   = clrWhite; 
color clrMuted  = C'120,123,134'; 
color clrHeader = C'41,98,255'; 
color clrTabBg  = C'25,29,38'; 
color clrPoc    = C'156,39,176'; 

struct TMarketData {
   double price, oi_usd, fund, imb; 
   string fg_status;
   double v1, cvd1, liqL1, liqS1, whl1; 
   double v6, cvd6, liqL6, liqS6, whl6; 
   double v24, cvd24, liqL24, liqS24, whl24;
   double ask_p_close, ask_v_close, bid_p_close, bid_v_close; 
   double ask_p_mid, ask_v_mid, bid_p_mid, bid_v_mid; 
   double ask_p_macro, ask_v_macro, bid_p_macro, bid_v_macro;
   double poc; 
   string phase_name; 
   color phase_color; 
   int tz_offset;
} MD;

struct TLevel { 
   string label; 
   double price; 
   double vol; 
   color clr; 
};

TLevel lvl_up[]; 
TLevel lvl_dn[]; 
int up_cnt = 0; 
int dn_cnt = 0;


string FormatK(double val) { 
   double absVal = MathAbs(val); 
   if (absVal >= 1000000000.0) return DoubleToString(val / 1000000000.0, 2) + "B"; 
   if (absVal >= 1000000.0) return DoubleToString(val / 1000000.0, 1) + "M"; 
   if (absVal >= 1000.0) return DoubleToString(val / 1000.0, 1) + "k"; 
   return DoubleToString(val, 0); 
}

double GetJsonDouble(string json, string key, int start_pos=0) { 
   if(start_pos < 0) return 0.0; 
   string search = "\"" + key + "\":"; 
   int pos = StringFind(json, search, start_pos); 
   if (pos < 0) return 0.0; 
   pos += StringLen(search); 
   while(pos < StringLen(json) && StringSubstr(json, pos, 1) == " ") pos++; 
   int endComma = StringFind(json, ",", pos); 
   int endBrace = StringFind(json, "}", pos); 
   int end = -1; 
   if (endComma > 0 && endBrace > 0) end = MathMin(endComma, endBrace); 
   else if (endComma > 0) end = endComma; 
   else if (endBrace > 0) end = endBrace; 
   if (end < 0) return 0.0; 
   string val = StringSubstr(json, pos, end - pos); 
   StringReplace(val, " ", ""); 
   StringReplace(val, "\"", ""); 
   return StringToDouble(val); 
}

string GetJsonString(string json, string key) { 
   string search = "\"" + key + "\":\""; 
   int pos = StringFind(json, search); 
   if (pos < 0) return ""; 
   pos += StringLen(search); 
   int end = StringFind(json, "\"", pos); 
   if (end < 0) return ""; 
   return StringSubstr(json, pos, end - pos); 
}


void AddLevel(string lbl, double p, double v, int type, color c) { 
   if(p <= 0 || v <= 0) return; 
   if(type == 1) { 
      ArrayResize(lvl_up, up_cnt + 1); 
      lvl_up[up_cnt].label = lbl; 
      lvl_up[up_cnt].price = p; 
      lvl_up[up_cnt].vol = v; 
      lvl_up[up_cnt].clr = c; 
      up_cnt++; 
   } else { 
      ArrayResize(lvl_dn, dn_cnt + 1); 
      lvl_dn[dn_cnt].label = lbl; 
      lvl_dn[dn_cnt].price = p; 
      lvl_dn[dn_cnt].vol = v; 
      lvl_dn[dn_cnt].clr = c; 
      dn_cnt++; 
   } 
}

void SortLevels() { 
   for(int i=0; i<up_cnt-1; i++) {
      for(int j=0; j<up_cnt-i-1; j++) {
         if(lvl_up[j].price > lvl_up[j+1].price) { 
            TLevel tmp = lvl_up[j]; lvl_up[j] = lvl_up[j+1]; lvl_up[j+1] = tmp; 
         } 
      }
   }
   for(int i=0; i<dn_cnt-1; i++) {
      for(int j=0; j<dn_cnt-i-1; j++) {
         if(lvl_dn[j].price < lvl_dn[j+1].price) { 
            TLevel tmp = lvl_dn[j]; lvl_dn[j] = lvl_dn[j+1]; lvl_dn[j+1] = tmp; 
         } 
      }
   }
}

void DrawPOCLine(double price) { 
   string name = "QPRO_POC_LINE"; 
   if (price <= 0) { ObjectDelete(0, name); return; } 
   
   if (ObjectFind(0, name) < 0) { 
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price); 
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrPoc); 
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID); 
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 2); 
      ObjectSetString(0, name, OBJPROP_TOOLTIP, "24H Point of Control (POC)\nPrice: " + DoubleToString(price, 2)); 
      ObjectSetInteger(0, name, OBJPROP_BACK, true); 
      ObjectSetInteger(0, name, OBJPROP_ZORDER, -3); 
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false); 
   } else {
      ObjectSetDouble(0, name, OBJPROP_PRICE, price); 
   }
}

void DrawWhaleLine(string id, double price, double vol, color clr, string lbl) { 
   string lineName = "QPRO_LINE_" + id; 
   if (!InpShowWalls || vol < 500000 || price == 0) { ObjectDelete(0, lineName); return; } 
   
   if (ObjectFind(0, lineName) < 0) { 
      ObjectCreate(0, lineName, OBJ_HLINE, 0, 0, price); 
      ObjectSetInteger(0, lineName, OBJPROP_COLOR, clr); 
      ObjectSetInteger(0, lineName, OBJPROP_STYLE, STYLE_DASH); 
      ObjectSetInteger(0, lineName, OBJPROP_WIDTH, 1); 
      ObjectSetString(0, lineName, OBJPROP_TOOLTIP, lbl + "\nVol: $" + FormatK(vol) + "\nPrice: " + DoubleToString(price, 2)); 
      ObjectSetInteger(0, lineName, OBJPROP_BACK, true); 
      ObjectSetInteger(0, lineName, OBJPROP_ZORDER, -1); 
      ObjectSetInteger(0, lineName, OBJPROP_SELECTABLE, false); 
   } else { 
      ObjectSetDouble(0, lineName, OBJPROP_PRICE, price); 
      ObjectSetString(0, lineName, OBJPROP_TOOLTIP, lbl + "\nVol: $" + FormatK(vol) + "\nPrice: " + DoubleToString(price, 2)); 
   } 
}

void DrawMagnetLine(string side, int index, double price, double vol) { 
   string name = "QPRO_MAG_" + side + "_" + IntegerToString(index);
   
   
   color clrFill = (side == "SHORT") ? C'10,40,30' : C'40,15,15'; 
   
   
   double offset = price * 0.0025; 
   double p1 = price + offset;
   double p2 = price - offset;
   
   
   datetime current_bar = TimeCurrent();
   datetime t1 = current_bar - PeriodSeconds() * 100;
   datetime t2 = current_bar + PeriodSeconds() * 20;

   if (ObjectFind(0, name) < 0) { 
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrFill); 
      ObjectSetInteger(0, name, OBJPROP_BACK, true); 
      ObjectSetInteger(0, name, OBJPROP_FILL, true); 
      ObjectSetInteger(0, name, OBJPROP_ZORDER, -5); 
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   } else {
      
      ObjectSetInteger(0, name, OBJPROP_TIME, 0, t1);
      ObjectSetInteger(0, name, OBJPROP_TIME, 1, t2);
      ObjectSetDouble(0, name, OBJPROP_PRICE, 0, p1);
      ObjectSetDouble(0, name, OBJPROP_PRICE, 1, p2);
   }
   ObjectSetString(0, name, OBJPROP_TOOLTIP, "Heatmap Cluster (" + side + ")\nVol: $" + FormatK(vol));
}

void DrawLiquidationCross(string side, double price, double vol, long ts) { 
   if (vol < 20000) return; 
   string name = "QPRO_LIQ_" + IntegerToString((int)ts) + "_" + DoubleToString(price, 0); 
   if (ObjectFind(0, name) >= 0) return; 
   
   datetime liq_time = (datetime)(ts + MD.tz_offset); 
   ObjectCreate(0, name, OBJ_ARROW, 0, liq_time, price); 
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 3); 
   ObjectSetInteger(0, name, OBJPROP_COLOR, side == "SHORT_LIQ" ? clrBuy : clrSell); 
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_CENTER); 
   ObjectSetInteger(0, name, OBJPROP_WIDTH, vol >= 500000 ? 4 : (vol >= 100000 ? 2 : 1)); 
   ObjectSetString(0, name, OBJPROP_TOOLTIP, "Liquidation (" + side + ")\n$" + FormatK(vol)); 
   ObjectSetInteger(0, name, OBJPROP_BACK, false); 
   ObjectSetInteger(0, name, OBJPROP_ZORDER, 5); 
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false); 
}

void DrawWhaleArrow(string side, double price, double vol, long ts) { 
   if (vol < InpChartWhaleVol) return; 
   string name = "QPRO_WHL_" + IntegerToString((int)ts) + "_" + DoubleToString(price, 0); 
   if (ObjectFind(0, name) < 0) { 
      datetime arrow_time = (datetime)(ts + MD.tz_offset); 
      ObjectCreate(0, name, OBJ_ARROW, 0, arrow_time, price); 
      ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 108); 
      ObjectSetInteger(0, name, OBJPROP_COLOR, side == "BUY" ? clrBuy : clrSell); 
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, side == "BUY" ? ANCHOR_TOP : ANCHOR_BOTTOM); 
      ObjectSetInteger(0, name, OBJPROP_WIDTH, vol >= 5000000 ? 5 : 3); 
      ObjectSetString(0, name, OBJPROP_TOOLTIP, "Whale " + side + "\n$" + FormatK(vol)); 
      ObjectSetInteger(0, name, OBJPROP_BACK, true); 
      ObjectSetInteger(0, name, OBJPROP_ZORDER, -5); 
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false); 
      if (InpEnableSounds && vol >= InpAlertWhaleVol) PlaySound(side == "BUY" ? "ok.wav" : "alert.wav"); 
   } 
}


void SetRow(int idx, string lbl, string val, color valClr) { 
   ObjectSetString(0, "QPRO_LBL_ROW_"+IntegerToString(idx), OBJPROP_TEXT, lbl); 
   ObjectSetString(0, "QPRO_VAL_ROW_"+IntegerToString(idx), OBJPROP_TEXT, val); 
   ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(idx), OBJPROP_COLOR, valClr); 
}

void CreateTabButton(string id, string text, int x, int y, int w, int h) { 
   ObjectCreate(0, id, OBJ_BUTTON, 0, 0, 0); 
   ObjectSetInteger(0, id, OBJPROP_XDISTANCE, x); 
   ObjectSetInteger(0, id, OBJPROP_YDISTANCE, y); 
   ObjectSetInteger(0, id, OBJPROP_XSIZE, w); 
   ObjectSetInteger(0, id, OBJPROP_YSIZE, h); 
   ObjectSetString(0, id, OBJPROP_TEXT, text); 
   ObjectSetString(0, id, OBJPROP_FONT, "Trebuchet MS"); 
   ObjectSetInteger(0, id, OBJPROP_FONTSIZE, 10); 
   ObjectSetInteger(0, id, OBJPROP_COLOR, clrText); 
   ObjectSetInteger(0, id, OBJPROP_BGCOLOR, clrTabBg); 
   ObjectSetInteger(0, id, OBJPROP_BORDER_COLOR, clrBg); 
   ObjectSetInteger(0, id, OBJPROP_SELECTABLE, false); 
   ObjectSetInteger(0, id, OBJPROP_HIDDEN, true); 
   ObjectSetInteger(0, id, OBJPROP_ZORDER, 20); 
}

void UpdateButtonStates() { 
   for(int i=0; i<5; i++) { 
      ObjectSetInteger(0, "QPRO_TAB_"+IntegerToString(i), OBJPROP_STATE, current_tab == i); 
      ObjectSetInteger(0, "QPRO_TAB_"+IntegerToString(i), OBJPROP_BGCOLOR, current_tab == i ? clrHeader : clrTabBg); 
      ObjectSetInteger(0, "QPRO_TAB_"+IntegerToString(i), OBJPROP_COLOR, current_tab == i ? clrWhite : clrMuted); 
   } 
}

void CreateDashboard() {
   string bgName = "QPRO_BG"; 
   ObjectCreate(0, bgName, OBJ_RECTANGLE_LABEL, 0, 0, 0); 
   ObjectSetInteger(0, bgName, OBJPROP_CORNER, CORNER_LEFT_UPPER); 
   ObjectSetInteger(0, bgName, OBJPROP_XSIZE, 420); 
   ObjectSetInteger(0, bgName, OBJPROP_YSIZE, ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS)); 
   ObjectSetInteger(0, bgName, OBJPROP_BGCOLOR, clrBg); 
   ObjectSetInteger(0, bgName, OBJPROP_BORDER_TYPE, BORDER_FLAT); 
   ObjectSetInteger(0, bgName, OBJPROP_COLOR, clrBg); 
   ObjectSetInteger(0, bgName, OBJPROP_SELECTABLE, false); 
   ObjectSetInteger(0, bgName, OBJPROP_HIDDEN, true); 
   ObjectSetInteger(0, bgName, OBJPROP_BACK, false); 
   ObjectSetInteger(0, bgName, OBJPROP_ZORDER, 10); 
   
   CreateTabButton("QPRO_TAB_0", "LIVE", 10, 35, 76, 30); 
   CreateTabButton("QPRO_TAB_1", "1H", 90, 35, 76, 30); 
   CreateTabButton("QPRO_TAB_2", "24H", 170, 35, 76, 30); 
   CreateTabButton("QPRO_TAB_3", "LIQS", 250, 35, 76, 30); 
   CreateTabButton("QPRO_TAB_4", "MAX", 330, 35, 76, 30); 
   UpdateButtonStates();

   for(int i=0; i<6; i++) { 
      ObjectCreate(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJ_LABEL, 0, 0, 0); 
      ObjectSetInteger(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_CORNER, CORNER_LEFT_UPPER); 
      ObjectSetInteger(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_XDISTANCE, 15); 
      ObjectSetString(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_FONT, "Trebuchet MS"); 
      ObjectSetInteger(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_FONTSIZE, 10); 
      ObjectSetInteger(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_COLOR, clrMuted); 
      ObjectSetInteger(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_SELECTABLE, false); 
      ObjectSetInteger(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_HIDDEN, true); 
      ObjectSetInteger(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_ZORDER, 30); 
      
      ObjectCreate(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJ_LABEL, 0, 0, 0); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_CORNER, CORNER_LEFT_UPPER); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_XDISTANCE, 405); 
      ObjectSetString(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_FONT, "Trebuchet MS"); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_FONTSIZE, 10); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_COLOR, clrText); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_ANCHOR, ANCHOR_RIGHT_UPPER); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_SELECTABLE, false); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_HIDDEN, true); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_ZORDER, 30); 
   }
   
   ObjectCreate(0, "QPRO_FOOTER_LBL", OBJ_LABEL, 0, 0, 0); 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_CORNER, CORNER_LEFT_UPPER); 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_ANCHOR, ANCHOR_UPPER); 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_XDISTANCE, 210); 
   ObjectSetString(0, "QPRO_FOOTER_LBL", OBJPROP_FONT, "Trebuchet MS"); 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_FONTSIZE, 10); 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_COLOR, clrMuted); 
   ObjectSetString(0, "QPRO_FOOTER_LBL", OBJPROP_TEXT, "Latest Whale:"); 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_SELECTABLE, false); 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_HIDDEN, true); 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_ZORDER, 30); 
   
   ObjectCreate(0, "QPRO_FOOTER_VAL", OBJ_LABEL, 0, 0, 0); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_CORNER, CORNER_LEFT_UPPER); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_ANCHOR, ANCHOR_UPPER); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_XDISTANCE, 210); 
   ObjectSetString(0, "QPRO_FOOTER_VAL", OBJPROP_FONT, "Trebuchet MS"); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_FONTSIZE, 11); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_COLOR, clrText); 
   ObjectSetString(0, "QPRO_FOOTER_VAL", OBJPROP_TEXT, "Waiting..."); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_SELECTABLE, false); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_HIDDEN, true); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_ZORDER, 30);
}

void UpdateLayout() {
   long chartHeight = ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS); 
   int startY = 80; 
   int footerReserve = 110; 
   int availableHeight = (int)chartHeight - startY - footerReserve; 
   
   if (availableHeight < 120) availableHeight = 120; 
   int step = availableHeight / 6; 
   if(step < 18) step = 18; 
   if(step > 45) step = 45; 
   int last_row_y = 0;
   
   for(int i=0; i<6; i++) { 
      int current_y = startY + (i * step); 
      ObjectSetInteger(0, "QPRO_LBL_ROW_"+IntegerToString(i), OBJPROP_YDISTANCE, current_y); 
      ObjectSetInteger(0, "QPRO_VAL_ROW_"+IntegerToString(i), OBJPROP_YDISTANCE, current_y); 
      if(i == 5) last_row_y = current_y; 
   }
   
   int footer_lbl_y = last_row_y + step + 20;  
   int footer_val_y = footer_lbl_y + 35; 
   ObjectSetInteger(0, "QPRO_FOOTER_LBL", OBJPROP_YDISTANCE, footer_lbl_y); 
   ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_YDISTANCE, footer_val_y);
   
   int bgHeight = footer_val_y + 25; 
   ObjectSetInteger(0, "QPRO_BG", OBJPROP_YSIZE, bgHeight > chartHeight ? bgHeight : chartHeight);
}
string GenerateServerToken() {
   long account = AccountInfoInteger(ACCOUNT_LOGIN);
   
   string secret = "QuantumTerminalPro_SuperSecret_2026!"; 
   string raw_data = IntegerToString(account) + secret;
   
   uchar data[], hash[];
   StringToCharArray(raw_data, data, 0, StringLen(raw_data));
   
   
   if(CryptEncode(CRYPT_HASH_SHA256, data, hash, hash)) {
      string token = "";
      for(int i=0; i<ArraySize(hash); i++) token += StringFormat("%02x", hash[i]);
      return token;
   }
   return "";
}

string GetSecurityHeader() {
   long account = AccountInfoInteger(ACCOUNT_LOGIN);
   
   return "X-MT5-Account: " + IntegerToString(account) + "\r\n" +
          "X-Quantum-Hash: " + GenerateServerToken() + "\r\n";
}

string GetActiveKey() {
  
   return "MT5_SECURE"; 
}


void AutoDeployIndicators() {
   SmartDeploy(0, "::Indicators\\QuantumPro_EMA.ex5", "EMA");
   SmartDeploy(-1, "::Indicators\\QuantumPro_Volume.ex5", "VOLUME");
   
   
   SmartDeploy(-1, "::Indicators\\QuantumPro_CVD.ex5", "DELTA"); 
   
   SmartDeploy(-1, "::Indicators\\QuantumPro_Liq.ex5", "LIQUIDATIONS");
   
   
   SmartDeploy(-1, "::Indicators\\QuantumPro_OI.ex5", "INTEREST"); 
   
   SmartDeploy(-1, "::Indicators\\QuantumPro_Funding.ex5", "FUNDING");
}
void SmartDeploy(int target_window, string resource_path, string check_name) {
   string search_str = check_name;
   StringToUpper(search_str); 

   
   int windows = (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL);
   for(int w = windows - 1; w >= 0; w--) {
      int total = ChartIndicatorsTotal(0, w);
      for(int i = 0; i < total; i++) {
         string ind_name = ChartIndicatorName(0, w, i);
         
        
         StringToUpper(ind_name); 
         
         
         if(StringFind(ind_name, search_str) >= 0) {
             return; 
         }
      }
   }
   
   
   int handle = iCustom(_Symbol, PERIOD_CURRENT, resource_path);
   if (handle != INVALID_HANDLE) {
      int w = (target_window == -1) ? (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL) : target_window;
      ChartIndicatorAdd(0, w, handle);
   } else {
      Print("Indicator loading error: ", resource_path);
   }
}

int OnInit() {
   
   if (MQLInfoInteger(MQL_TESTER)) {
      Print("Quantum Pro: Working in Strategy Tester is not supported (MT5 limitation).");
      return(INIT_SUCCEEDED); 
   }

   
   if (StringFind(_Symbol, "BTC") >= 0) current_symbol = "BTCUSDT";
   else if (StringFind(_Symbol, "ETH") >= 0) current_symbol = "ETHUSDT"; 
   else return(INIT_FAILED);
   
   ChartSetInteger(0, CHART_SHIFT, true);
   
   
   GlobalVariableSet("QPRO_EMA_MODE", (int)InpEmaMode); 
   GlobalVariableSet("QPRO_SESSION_MODE", (int)InpSessionMode); 
   GlobalVariableSet("QPRO_VOL_MODE", (int)InpVolumeMode);
   GlobalVariableSet("QPRO_UTC_OFFSET", InpUtcOffset * 3600); 
   GlobalVariableSet("QPRO_LIQ_MODE", (int)InpLiqMode);

   ZeroMemory(MD); 
   CreateDashboard();
   EventSetTimer(3); 
   AutoDeployIndicators();
   OnTimer(); 
   return(INIT_SUCCEEDED);
}
void OnDeinit(const int reason) { 
   EventKillTimer(); 
   ObjectsDeleteAll(0, "QPRO_");

   if (reason == REASON_REMOVE || reason == REASON_CHARTCLOSE) {
      int windows = (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL);
      for(int w = windows - 1; w >= 0; w--) { 
         int total = ChartIndicatorsTotal(0, w);
         for(int i = total - 1; i >= 0; i--) { 
            string ind_name = ChartIndicatorName(0, w, i);
            
            string name_upper = ind_name;
            StringToUpper(name_upper);
            
            
            if(StringFind(name_upper, "QUANTUM") >= 0 || 
               StringFind(name_upper, "EMA") >= 0 || 
               StringFind(name_upper, "VOLUME") >= 0 || 
               StringFind(name_upper, "DELTA") >= 0 ||     
               StringFind(name_upper, "LIQUIDATIONS") >= 0 || 
               StringFind(name_upper, "INTEREST") >= 0 ||  
               StringFind(name_upper, "FUNDING") >= 0) {
               
               ChartIndicatorDelete(0, w, ind_name);
            }
         }
      }
   }
}

void OnTimer() {
   timer_ticks++; 
   char post[], result[]; 
   string headers;
   up_cnt = 0; 
   dn_cnt = 0; 

   static bool is_blocked = false;
   if (is_blocked) return;

   static bool web_error = false;
   if (web_error) return; 
   
   
   if (timer_ticks % 2 == 0) {
      string url_csv = InpServer + "/api/mt5/chart_csv/" + current_symbol + "?api_key=" + GetActiveKey();
      ResetLastError();
      int res_csv = WebRequest("GET", url_csv, GetSecurityHeader(), 5000, post, result, headers);

      if (res_csv == 403) { BlockTerminal(); is_blocked = true; return; }
      else if (res_csv == -1 && GetLastError() == 4060) {
         Print("WARNING: WebRequest is not allowed! Add the URL to your settings.");
         is_blocked = true; return;
      }
      else if (res_csv == 200) { 
         int handle = FileOpen("QPRO_Chart_" + current_symbol + ".csv", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
         if (handle != INVALID_HANDLE) { 
            FileWriteString(handle, CharArrayToString(result)); 
            FileClose(handle);
         } 
      }
   } 
   
   
   if (timer_ticks == 1 || timer_ticks % 20 == 0) {
      string url_init = InpServer + "/api/mt5/init/" + current_symbol + "?api_key=" + GetActiveKey();
      int res_init = WebRequest("GET", url_init, GetSecurityHeader(), 5000, post, result, headers);
      
      if (res_init == 403) { BlockTerminal(); is_blocked = true; return; }
      else if (res_init == 200) {
         string json = CharArrayToString(result);
         int p1h = StringFind(json, "\"last_1h\":"); 
         int p6h = StringFind(json, "\"last_6h\":"); 
         int p24h = StringFind(json, "\"last_24h\":");
         
         MD.v1 = GetJsonDouble(json, "total_volume_usd", p1h);
         MD.cvd1 = GetJsonDouble(json, "cvd_usd", p1h); 
         MD.liqL1 = GetJsonDouble(json, "liq_long_usd", p1h);  
         MD.liqS1 = GetJsonDouble(json, "liq_short_usd", p1h); 
         MD.whl1 = GetJsonDouble(json, "whale_trades_count", p1h);
         
         MD.v6 = GetJsonDouble(json, "total_volume_usd", p6h); 
         MD.cvd6 = GetJsonDouble(json, "cvd_usd", p6h); 
         MD.liqL6 = GetJsonDouble(json, "liq_long_usd", p6h);  
         MD.liqS6 = GetJsonDouble(json, "liq_short_usd", p6h);
         MD.whl6 = GetJsonDouble(json, "whale_trades_count", p6h);
         
         MD.v24 = GetJsonDouble(json, "total_volume_usd", p24h); 
         MD.cvd24 = GetJsonDouble(json, "cvd_usd", p24h); 
         MD.liqL24 = GetJsonDouble(json, "liq_long_usd", p24h);
         MD.liqS24 = GetJsonDouble(json, "liq_short_usd", p24h); 
         MD.whl24 = GetJsonDouble(json, "whale_trades_count", p24h);
      }
   }

   
   string url_live = InpServer + "/api/mt5/live/" + current_symbol + "?api_key=" + GetActiveKey();
   int res_live = WebRequest("GET", url_live, GetSecurityHeader(), 3000, post, result, headers);
   
   if (res_live == 403) { BlockTerminal(); is_blocked = true; return; }
   else if (res_live == 429) { 
      
      return; 
   }
   else if (res_live == 200) {
      string json = CharArrayToString(result);
      MD.price = GetJsonDouble(json, "price"); 
      MD.oi_usd = GetJsonDouble(json, "global_open_interest") * MD.price;
      
      long server_utc = (long)GetJsonDouble(json, "server_utc");
      if (server_utc > 0) { 
         datetime mt5_last_m1 = (datetime)SeriesInfoInteger(current_symbol, PERIOD_M1, SERIES_LASTBAR_DATE);
         if(mt5_last_m1 == 0) mt5_last_m1 = TimeCurrent(); 
         int raw_diff = (int)(mt5_last_m1 - server_utc); 
         MD.tz_offset = (int)MathRound((double)raw_diff / 3600.0) * 3600;
      }
      
      if (StringFind(json, "\"global_funding_rate\":") >= 0) MD.fund = GetJsonDouble(json, "global_funding_rate");
      else MD.fund = GetJsonDouble(json, "funding_rate");
      
      MD.imb = GetJsonDouble(json, "ob_imbalance_usd"); 
      
      int p1h = StringFind(json, "\"last_1h\":");
      if (p1h > 0) { 
         MD.cvd1 = GetJsonDouble(json, "cvd_usd", p1h);
         MD.liqL1 = GetJsonDouble(json, "liq_long_usd", p1h); 
         MD.liqS1 = GetJsonDouble(json, "liq_short_usd", p1h);
      }
      
      
      double fgValue = 50.0;
      double fundScore = (MD.fund / 0.05) * 15.0; 
      fgValue += MathMax(-15.0, MathMin(15.0, fundScore));
      double cvdThreshold = (StringFind(current_symbol, "BTC") >= 0) ? 250000000.0 : 120000000.0; 
      double cvdScore = (MD.cvd1 / cvdThreshold) * 20.0;
      fgValue += MathMax(-20.0, MathMin(20.0, cvdScore)); 
      
      double totalLiq = MD.liqL1 + MD.liqS1;
      if (totalLiq > 200000.0) fgValue += ((MD.liqS1 - MD.liqL1) / totalLiq) * 10.0;
      double imbThreshold = (StringFind(current_symbol, "BTC") >= 0) ? 30000000.0 : 15000000.0; 
      fgValue += MathMax(-5.0, MathMin(5.0, (MD.imb / imbThreshold) * 5.0));
      fgValue = MathMax(0.0, MathMin(100.0, fgValue));
      
      if(fgValue >= 80) MD.fg_status = "Ext. Greed"; 
      else if(fgValue >= 60) MD.fg_status = "Greed";
      else if(fgValue <= 20) MD.fg_status = "Ext. Fear"; 
      else if(fgValue <= 40) MD.fg_status = "Fear"; 
      else MD.fg_status = "Neutral";
      MD.fg_status = IntegerToString((int)MathRound(fgValue)) + "/100 (" + MD.fg_status + ")";
      
      MD.ask_p_close = GetJsonDouble(json, "whale_close_ask_price"); 
      MD.ask_v_close = GetJsonDouble(json, "whale_close_ask_vol");
      MD.bid_p_close = GetJsonDouble(json, "whale_close_bid_price"); 
      MD.bid_v_close = GetJsonDouble(json, "whale_close_bid_vol"); 
      
      MD.ask_p_mid = GetJsonDouble(json, "whale_mid_ask_price"); 
      MD.ask_v_mid = GetJsonDouble(json, "whale_mid_ask_vol"); 
      MD.bid_p_mid = GetJsonDouble(json, "whale_mid_bid_price");
      MD.bid_v_mid = GetJsonDouble(json, "whale_mid_bid_vol"); 
      
      MD.ask_p_macro = GetJsonDouble(json, "whale_macro_ask_price"); 
      MD.ask_v_macro = GetJsonDouble(json, "whale_macro_ask_vol"); 
      MD.bid_p_macro = GetJsonDouble(json, "whale_macro_bid_price"); 
      MD.bid_v_macro = GetJsonDouble(json, "whale_macro_bid_vol");
      MD.poc = GetJsonDouble(json, "poc_price");
      
      string phase_raw = GetJsonString(json, "market_phase"); 
      if (phase_raw != "") { 
         string p_arr[];
         if (StringSplit(phase_raw, '|', p_arr) == 2) { 
            MD.phase_name = p_arr[0];
            if (p_arr[1] == "BUY") MD.phase_color = clrBuy; 
            else if (p_arr[1] == "SELL") MD.phase_color = clrSell;
            else if (p_arr[1] == "WARN") MD.phase_color = C'255,165,0'; 
            else MD.phase_color = clrText;
         } 
      }
      
      string whales_str = GetJsonString(json, "whales_str");
      if (whales_str != "") {
         string w_arr[];
         int w_count = StringSplit(whales_str, '|', w_arr); 
         bool found_footer = false;
         for(int i = w_count - 1; i >= 0; i--) { 
            string w_data[];
            StringSplit(w_arr[i], ':', w_data); 
            if (ArraySize(w_data) == 4 && StringToDouble(w_data[2]) >= InpFooterWhaleVol) { 
               ObjectSetString(0, "QPRO_FOOTER_VAL", OBJPROP_TEXT, w_data[0] + " $" + FormatK(StringToDouble(w_data[2])) + " @ " + DoubleToString(StringToDouble(w_data[1]), 0));
               ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_COLOR, w_data[0] == "BUY" ? clrBuy : clrSell); 
               found_footer = true; 
               break;
            } 
         }
         
         if (!found_footer) { 
            ObjectSetString(0, "QPRO_FOOTER_VAL", OBJPROP_TEXT, "Waiting...");
            ObjectSetInteger(0, "QPRO_FOOTER_VAL", OBJPROP_COLOR, clrMuted); 
         }
         
         for(int i=0; i<w_count; i++) { 
            string w_data[];
            StringSplit(w_arr[i], ':', w_data); 
            if (ArraySize(w_data) == 4) DrawWhaleArrow(w_data[0], StringToDouble(w_data[1]), StringToDouble(w_data[2]), StringToInteger(w_data[3]));
         }
      }
      
      string magnets_str = GetJsonString(json, "magnets_str");
      if (magnets_str != "") { 
         string m_arr[];
         int m_count = StringSplit(magnets_str, '|', m_arr); 
         int short_idx = 0; 
         int long_idx = 0;
         for(int i=0; i<m_count; i++) { 
            string m_data[];
            StringSplit(m_arr[i], ':', m_data); 
            if (ArraySize(m_data) == 3) { 
               if(m_data[0] == "SHORT") { 
                  DrawMagnetLine(m_data[0], short_idx++, StringToDouble(m_data[1]), StringToDouble(m_data[2]));
                  AddLevel("Short Liq", StringToDouble(m_data[1]), StringToDouble(m_data[2]), 1, clrBuy); 
               } else { 
                  DrawMagnetLine(m_data[0], long_idx++, StringToDouble(m_data[1]), StringToDouble(m_data[2]));
                  AddLevel("Long Liq", StringToDouble(m_data[1]), StringToDouble(m_data[2]), -1, clrSell); 
               } 
            } 
         }
         for(int i=short_idx; i<3; i++) ObjectDelete(0, "QPRO_MAG_SHORT_" + IntegerToString(i));
         for(int i=long_idx; i<3; i++) ObjectDelete(0, "QPRO_MAG_LONG_" + IntegerToString(i));
      }
      
      string liqs_str = GetJsonString(json, "liqs_str");
      if (liqs_str != "") { 
         string l_arr[];
         int l_count = StringSplit(liqs_str, '|', l_arr); 
         for(int i=0; i<l_count; i++) { 
            string l_data[];
            StringSplit(l_arr[i], ':', l_data); 
            if (ArraySize(l_data) == 4) DrawLiquidationCross(l_data[0], StringToDouble(l_data[1]), StringToDouble(l_data[2]), StringToInteger(l_data[3]));
         } 
      }
      
      SortLevels();
      if (MD.poc > 0) DrawPOCLine(MD.poc);
   }

   RenderVisualDOM();
   UpdateLayout(); 
   RenderData(); 
}


void RenderData() {
   if(current_tab == 0) { 
      SetRow(0, "Live Price:", "$" + DoubleToString(MD.price, 2), clrText); 
      SetRow(1, "Global OI (9 Exch):", "$" + FormatK(MD.oi_usd), clrText); 
      SetRow(2, "Funding 8H:", DoubleToString(MD.fund, 4) + "%", MD.fund > 0 ? clrBuy : clrSell); 
      SetRow(3, "Market State:", MD.fg_status, clrHeader); 
      SetRow(4, "OB Imbalance:", (MD.imb > 0 ? "+$" : "-$") + FormatK(MathAbs(MD.imb)), MD.imb > 0 ? clrBuy : clrSell); 
      SetRow(5, "Phase(1H):", MD.phase_name != "" ? MD.phase_name : "Neutral", MD.phase_name != "" ? MD.phase_color : clrText);
   } 
   else if(current_tab == 1) { 
      SetRow(0, "Total Volume (1H):", "$" + FormatK(MD.v1), clrText); 
      SetRow(1, "CVD Delta (1H):", (MD.cvd1 > 0 ? "+$" : "-$") + FormatK(MathAbs(MD.cvd1)), MD.cvd1 > 0 ? clrBuy : clrSell); 
      SetRow(2, "Liq Longs (1H):", "$" + FormatK(MD.liqL1), clrSell); 
      SetRow(3, "Liq Shorts (1H):", "$" + FormatK(MD.liqS1), clrBuy); 
      SetRow(4, "Whale Trades (1H):", DoubleToString(MD.whl1, 0), clrText); 
      SetRow(5, " ", " ", clrText);
   } 
   else if(current_tab == 2) { 
      SetRow(0, "Total Volume (24H):", "$" + FormatK(MD.v24), clrText); 
      SetRow(1, "CVD Delta (24H):", (MD.cvd24 > 0 ? "+$" : "-$") + FormatK(MathAbs(MD.cvd24)), MD.cvd24 > 0 ? clrBuy : clrSell); 
      SetRow(2, "Liq Longs (24H):", "$" + FormatK(MD.liqL24), clrSell); 
      SetRow(3, "Liq Shorts (24H):", "$" + FormatK(MD.liqS24), clrBuy); 
      SetRow(4, "Whale Trades (24H):", DoubleToString(MD.whl24, 0), clrText); 
      SetRow(5, " ", " ", clrText);
   } 
   else if(current_tab == 3) { 
      SetRow(0, up_cnt > 2 ? lvl_up[2].label : "Short Liq 3:", up_cnt > 2 ? "$"+DoubleToString(lvl_up[2].price, 0)+" ("+FormatK(lvl_up[2].vol)+")" : "---", up_cnt > 2 ? lvl_up[2].clr : clrMuted); 
      SetRow(1, up_cnt > 1 ? lvl_up[1].label : "Short Liq 2:", up_cnt > 1 ? "$"+DoubleToString(lvl_up[1].price, 0)+" ("+FormatK(lvl_up[1].vol)+")" : "---", up_cnt > 1 ? lvl_up[1].clr : clrMuted); 
      SetRow(2, up_cnt > 0 ? lvl_up[0].label : "Short Liq 1:", up_cnt > 0 ? "$"+DoubleToString(lvl_up[0].price, 0)+" ("+FormatK(lvl_up[0].vol)+")" : "---", up_cnt > 0 ? lvl_up[0].clr : clrMuted); 
      SetRow(3, dn_cnt > 0 ? lvl_dn[0].label : "Long Liq 1:", dn_cnt > 0 ? "$"+DoubleToString(lvl_dn[0].price, 0)+" ("+FormatK(lvl_dn[0].vol)+")" : "---", dn_cnt > 0 ? lvl_dn[0].clr : clrMuted); 
      SetRow(4, dn_cnt > 1 ? lvl_dn[1].label : "Long Liq 2:", dn_cnt > 1 ? "$"+DoubleToString(lvl_dn[1].price, 0)+" ("+FormatK(lvl_dn[1].vol)+")" : "---", dn_cnt > 1 ? lvl_dn[1].clr : clrMuted); 
      SetRow(5, dn_cnt > 2 ? lvl_dn[2].label : "Long Liq 3:", dn_cnt > 2 ? "$"+DoubleToString(lvl_dn[2].price, 0)+" ("+FormatK(lvl_dn[2].vol)+")" : "---", dn_cnt > 2 ? lvl_dn[2].clr : clrMuted);
   } 
   else if(current_tab == 4) { 
      SetRow(0, "Max Ask (C):", MD.ask_v_close > 500000 ? "$" + DoubleToString(MD.ask_p_close, 0) + " (" + FormatK(MD.ask_v_close) + ")" : "Clear", clrSell); 
      SetRow(1, "Max Bid (C):", MD.bid_v_close > 500000 ? "$" + DoubleToString(MD.bid_p_close, 0) + " (" + FormatK(MD.bid_v_close) + ")" : "Clear", clrBuy); 
      SetRow(2, "Max Ask (M):", MD.ask_v_mid > 500000 ? "$" + DoubleToString(MD.ask_p_mid, 0) + " (" + FormatK(MD.ask_v_mid) + ")" : "Clear", clrSell); 
      SetRow(3, "Max Bid (M):", MD.bid_v_mid > 500000 ? "$" + DoubleToString(MD.bid_p_mid, 0) + " (" + FormatK(MD.bid_v_mid) + ")" : "Clear", clrBuy); 
      SetRow(4, "Max Ask (L):", MD.ask_v_macro > 500000 ? "$" + DoubleToString(MD.ask_p_macro, 0) + " (" + FormatK(MD.ask_v_macro) + ")" : "Clear", clrSell); 
      SetRow(5, "Max Bid (L):", MD.bid_v_macro > 500000 ? "$" + DoubleToString(MD.bid_p_macro, 0) + " (" + FormatK(MD.bid_v_macro) + ")" : "Clear", clrBuy); 
   }
   ChartRedraw();
}

void DrawDOMBar(string id, double price, double vol, double max_vol, color clr, string label, int width=5) {
   if(price <= 0 || vol <= 0) {
      ObjectDelete(0, "QPRO_DOM_" + id);
      ObjectDelete(0, "QPRO_DOM_" + id + "_TXT");
      return;
   }
   
   string name = "QPRO_DOM_" + id;

   datetime t_start = (datetime)SeriesInfoInteger(_Symbol, PERIOD_CURRENT, SERIES_LASTBAR_DATE) + PeriodSeconds() * 3;
   

   int bars = (int)MathMax(2, (vol / max_vol) * 25); 
   datetime t_end = t_start + PeriodSeconds() * bars;
   

   if(ObjectFind(0, name) < 0) {
      ObjectCreate(0, name, OBJ_TREND, 0, t_start, price, t_end, price);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   } else {
      ObjectSetInteger(0, name, OBJPROP_TIME, 0, t_start);
      ObjectSetInteger(0, name, OBJPROP_TIME, 1, t_end);
      ObjectSetDouble(0, name, OBJPROP_PRICE, 0, price);
      ObjectSetDouble(0, name, OBJPROP_PRICE, 1, price);
   }
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width); 
   ObjectSetString(0, name, OBJPROP_TOOLTIP, label + "\n$" + FormatK(vol));


   string txt_name = name + "_TXT";
   if(ObjectFind(0, txt_name) < 0) {
      ObjectCreate(0, txt_name, OBJ_TEXT, 0, t_end, price);
      ObjectSetInteger(0, txt_name, OBJPROP_ANCHOR, ANCHOR_LEFT);
      ObjectSetInteger(0, txt_name, OBJPROP_FONTSIZE, 8);
      ObjectSetString(0, txt_name, OBJPROP_FONT, "Trebuchet MS");
      ObjectSetInteger(0, txt_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, txt_name, OBJPROP_HIDDEN, true);
   } else {
      ObjectSetInteger(0, txt_name, OBJPROP_TIME, 0, t_end);
      ObjectSetDouble(0, txt_name, OBJPROP_PRICE, 0, price);
   }
   ObjectSetString(0, txt_name, OBJPROP_TEXT, " " + FormatK(vol));
   ObjectSetInteger(0, txt_name, OBJPROP_COLOR, clr);
}

void RenderVisualDOM() {
   if (!InpShowWalls) return;


   double max_v = 1000000; 
   for(int i=0; i<up_cnt; i++) if(lvl_up[i].vol > max_v) max_v = lvl_up[i].vol;
   for(int i=0; i<dn_cnt; i++) if(lvl_dn[i].vol > max_v) max_v = lvl_dn[i].vol;
   if(MD.ask_v_close > max_v) max_v = MD.ask_v_close;
   if(MD.bid_v_close > max_v) max_v = MD.bid_v_close;
   if(MD.ask_v_mid > max_v) max_v = MD.ask_v_mid;
   if(MD.bid_v_mid > max_v) max_v = MD.bid_v_mid;
   if(MD.ask_v_macro > max_v) max_v = MD.ask_v_macro;
   if(MD.bid_v_macro > max_v) max_v = MD.bid_v_macro;

   
   for(int i=0; i<up_cnt; i++) DrawDOMBar("MAG_UP_"+IntegerToString(i), lvl_up[i].price, lvl_up[i].vol, max_v, lvl_up[i].clr, "Short Liq Magnet", 2);
   for(int i=0; i<dn_cnt; i++) DrawDOMBar("MAG_DN_"+IntegerToString(i), lvl_dn[i].price, lvl_dn[i].vol, max_v, lvl_dn[i].clr, "Long Liq Magnet", 2);

   
   for(int i=up_cnt; i<3; i++) { ObjectDelete(0, "QPRO_DOM_MAG_UP_"+IntegerToString(i)); ObjectDelete(0, "QPRO_DOM_MAG_UP_"+IntegerToString(i)+"_TXT"); }
   for(int i=dn_cnt; i<3; i++) { ObjectDelete(0, "QPRO_DOM_MAG_DN_"+IntegerToString(i)); ObjectDelete(0, "QPRO_DOM_MAG_DN_"+IntegerToString(i)+"_TXT"); }

   
   DrawDOMBar("WALL_ASK_C", MD.ask_p_close, MD.ask_v_close, max_v, clrSell, "Ask Wall (Close)", 4);
   DrawDOMBar("WALL_BID_C", MD.bid_p_close, MD.bid_v_close, max_v, clrBuy, "Bid Wall (Close)", 4);
   DrawDOMBar("WALL_ASK_M", MD.ask_p_mid, MD.ask_v_mid, max_v, clrSell, "Ask Wall (Mid)", 6);
   DrawDOMBar("WALL_BID_M", MD.bid_p_mid, MD.bid_v_mid, max_v, clrBuy, "Bid Wall (Mid)", 6);
   DrawDOMBar("WALL_ASK_L", MD.ask_p_macro, MD.ask_v_macro, max_v, clrSell, "Ask Wall (Macro)", 8);
   DrawDOMBar("WALL_BID_L", MD.bid_p_macro, MD.bid_v_macro, max_v, clrBuy, "Bid Wall (Macro)", 8);
}

bool BlockTerminal(string msg = "") {
   
   if (msg != "") {
      Comment(msg); 
   }
   return false; 
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam) {
   if(id == CHARTEVENT_OBJECT_CLICK) {
      if(sparam == "QPRO_TAB_0") { current_tab = 0; UpdateButtonStates(); RenderData(); ChartRedraw(); }
      else if(sparam == "QPRO_TAB_1") { current_tab = 1; UpdateButtonStates(); RenderData(); ChartRedraw(); }
      else if(sparam == "QPRO_TAB_2") { current_tab = 2; UpdateButtonStates(); RenderData(); ChartRedraw(); }
      else if(sparam == "QPRO_TAB_3") { current_tab = 3; UpdateButtonStates(); RenderData(); ChartRedraw(); }
      else if(sparam == "QPRO_TAB_4") { current_tab = 4; UpdateButtonStates(); RenderData(); ChartRedraw(); }
   }
}