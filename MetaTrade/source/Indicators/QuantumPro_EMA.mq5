//+------------------------------------------------------------------+
//|                                                QuantumPro_EMA.mq5|
//|                          Realtime Market Telemetry Platform" API |
//+------------------------------------------------------------------+
#property copyright "Realtime Telemetry Platform"
#property indicator_chart_window
#property indicator_buffers 5
#property indicator_plots   5

#property indicator_label1  "EMA 50"
#property indicator_type1   DRAW_LINE
#property indicator_color1  C'41,98,255'
#property indicator_width1  2

#property indicator_label2  "EMA 200"
#property indicator_type2   DRAW_LINE
#property indicator_color2  C'248,161,63'
#property indicator_width2  2

#property indicator_label3  "EMA 800"
#property indicator_type3   DRAW_LINE
#property indicator_color3  C'255,215,0'
#property indicator_width3  2

#property indicator_label4  "Cross UP"
#property indicator_type4   DRAW_ARROW
#property indicator_color4  C'34,171,148'
#property indicator_width4  7 

#property indicator_label5  "Cross DOWN"
#property indicator_type5   DRAW_ARROW
#property indicator_color5  C'242,54,69'
#property indicator_width5  7 

double Buf50[], Buf200[], Buf800[], BufUp[], BufDn[];
string current_symbol = "";

struct TCsvPoint { datetime time; double e50, e200, e800; };
TCsvPoint csvData[];


void CheckEmaVisibility() {
   int mode = 999; 
   if(GlobalVariableCheck("QPRO_EMA_MODE")) {
      mode = (int)GlobalVariableGet("QPRO_EMA_MODE");
   }

   color c50 =  (mode == 999 || mode == 50)  ? C'41,98,255' : clrNONE;
   color c200 = (mode == 999 || mode == 200) ? C'248,161,63' : clrNONE;
   color c800 = (mode == 999 || mode == 800) ? C'255,215,0' : clrNONE;

   PlotIndexSetInteger(0, PLOT_LINE_COLOR, 0, c50);
   PlotIndexSetInteger(1, PLOT_LINE_COLOR, 0, c200);
   PlotIndexSetInteger(2, PLOT_LINE_COLOR, 0, c800);
}

int OnInit() {
   if (StringFind(_Symbol, "BTC") >= 0) current_symbol = "BTCUSDT"; 
   else if (StringFind(_Symbol, "ETH") >= 0) current_symbol = "ETHUSDT"; 
   else return(INIT_FAILED);

   SetIndexBuffer(0, Buf50, INDICATOR_DATA);
   SetIndexBuffer(1, Buf200, INDICATOR_DATA);
   SetIndexBuffer(2, Buf800, INDICATOR_DATA);
   SetIndexBuffer(3, BufUp, INDICATOR_DATA);
   SetIndexBuffer(4, BufDn, INDICATOR_DATA);

   IndicatorSetString(INDICATOR_SHORTNAME, "Quantum EMA " + current_symbol);

   
   PlotIndexSetInteger(3, PLOT_ARROW, 233); 
   PlotIndexSetInteger(4, PLOT_ARROW, 234); 
   
   
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, 0.0);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, 0.0);
   PlotIndexSetDouble(2, PLOT_EMPTY_VALUE, 0.0);
   PlotIndexSetDouble(3, PLOT_EMPTY_VALUE, 0.0);
   PlotIndexSetDouble(4, PLOT_EMPTY_VALUE, 0.0);

   LoadCSV();
   CheckEmaVisibility();
   EventSetTimer(3);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer() { 
   LoadCSV(); 
   CheckEmaVisibility();
   ChartRedraw(); 
}

void LoadCSV() {
   int handle = FileOpen("QPRO_Chart_" + current_symbol + ".csv", FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE) return;

   long server_utc = 0;
   ArrayResize(csvData, 0);

   while(!FileIsEnding(handle)) {
      string line = FileReadString(handle);
      if (StringLen(line) < 10) continue;

      if (StringFind(line, "#UTC_NOW:") == 0) {
         server_utc = StringToInteger(StringSubstr(line, 9));
         continue;
      }
      if (StringFind(line, "time,") == 0) continue;

      string cols[];
      StringSplit(line, ',', cols);
      if(ArraySize(cols) >= 10) {
         int idx = ArraySize(csvData);
         ArrayResize(csvData, idx + 1);
         long epoch = StringToInteger(cols[0]); 
         csvData[idx].time = (datetime)(epoch > 20000000000 ? epoch/1000 : epoch);
         csvData[idx].e50 = StringToDouble(cols[7]);
         csvData[idx].e200 = StringToDouble(cols[8]);
         csvData[idx].e800 = StringToDouble(cols[9]);
      }
   }
   FileClose(handle);

   int arr_size = ArraySize(csvData);
   if (arr_size > 0 && server_utc > 0) {
      int tz_offset = (int)MathRound((double)(TimeCurrent() - server_utc) / 3600.0) * 3600;
      for(int i = 0; i < arr_size; i++) csvData[i].time += tz_offset;
   }
}

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[], const double &high[], const double &low[], const double &close[], const long &tick_volume[], const long &volume[], const int &spread[]) {
   static datetime last_csv_end = 0;
   int csv_len = ArraySize(csvData); 
   if(csv_len == 0) return(0);

   int limit = prev_calculated == 0 ? 0 : prev_calculated - 10;
   if(limit < 0) limit = 0;
   
   if (csvData[csv_len - 1].time != last_csv_end) { 
      limit = 0; 
      last_csv_end = csvData[csv_len - 1].time;
   }

   if (limit == 0) {
      ArrayInitialize(BufUp, 0.0);
      ArrayInitialize(BufDn, 0.0);
   }

   double k50 = 2.0 / 51.0;
   double k200 = 2.0 / 201.0;
   double k800 = 2.0 / 801.0;

   for(int i = limit; i < rates_total; i++) {
      
      BufUp[i] = 0.0;
      BufDn[i] = 0.0;

      datetime t_start = time[i];
      datetime t_end = t_start + PeriodSeconds();
      
      if (t_end <= csvData[0].time) { 
         Buf50[i]=0; Buf200[i]=0; Buf800[i]=0; 
         continue;
      }

      int end_idx = -1;
      int prev_idx = -1;
      
      for(int k = csv_len - 1; k >= 0; k--) {
         if (end_idx == -1 && csvData[k].time < t_end) end_idx = k;
         if (prev_idx == -1 && csvData[k].time < t_start) prev_idx = k;
         if (end_idx != -1 && prev_idx != -1) break;
      }

      if (end_idx != -1) {
         if (prev_idx == -1) prev_idx = 0;
         
         if (end_idx == prev_idx) {
            if (i > 0 && Buf50[i-1] > 0) { 
               Buf50[i] = (close[i] * k50) + (Buf50[i-1] * (1.0 - k50));
               Buf200[i] = (close[i] * k200) + (Buf200[i-1] * (1.0 - k200));
               Buf800[i] = (close[i] * k800) + (Buf800[i-1] * (1.0 - k800));
            } else {
               Buf50[i] = csvData[end_idx].e50;
               Buf200[i] = csvData[end_idx].e200;
               Buf800[i] = csvData[end_idx].e800;
            }
         } else {
            Buf50[i] = csvData[end_idx].e50;
            Buf200[i] = csvData[end_idx].e200;
            Buf800[i] = csvData[end_idx].e800;
         }
      } else {
         Buf50[i]=0; Buf200[i]=0; Buf800[i]=0;
      }

      
      if (i > 1 && Buf50[i-1] > 0 && Buf200[i-1] > 0 && Buf50[i] > 0 && Buf200[i] > 0) {
         
         double offset = close[i] * 0.0005; 

         
         if (Buf50[i-1] <= Buf200[i-1] && Buf50[i] > Buf200[i]) {
            BufUp[i] = low[i] - offset;
         }
         // Крест смерти
         else if (Buf50[i-1] >= Buf200[i-1] && Buf50[i] < Buf200[i]) {
            BufDn[i] = high[i] + offset;
         }
      }
   }
   return(rates_total);
}