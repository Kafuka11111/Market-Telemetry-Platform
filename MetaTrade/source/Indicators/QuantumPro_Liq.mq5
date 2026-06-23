#property indicator_separate_window
#property indicator_buffers 2
#property indicator_plots   2
#property indicator_label1  "Short Liqs"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  C'34,171,148' 
#property indicator_width1  2
#property indicator_label2  "Long Liqs"
#property indicator_type2   DRAW_HISTOGRAM
#property indicator_color2  C'242,54,69'  
#property indicator_width2  2

double BufferShorts[], BufferLongs[];
string current_symbol = "";
struct TCsvPoint { datetime time; double liq_long; double liq_short; };
TCsvPoint csvData[];

int OnInit() {
   if (StringFind(_Symbol, "BTC") >= 0) current_symbol = "BTCUSDT"; else if (StringFind(_Symbol, "ETH") >= 0) current_symbol = "ETHUSDT"; else return(INIT_FAILED);
   SetIndexBuffer(0, BufferShorts, INDICATOR_DATA); SetIndexBuffer(1, BufferLongs, INDICATOR_DATA);
   IndicatorSetString(INDICATOR_SHORTNAME, "Liquidations (Rolling 1H)"); IndicatorSetInteger(INDICATOR_DIGITS, 2);
   LoadCSV(); EventSetTimer(3); return(INIT_SUCCEEDED);
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() { LoadCSV(); ChartRedraw(); }

void LoadCSV() {
   int handle = FileOpen("QPRO_Chart_" + current_symbol + ".csv", FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE) return;
   long server_utc = 0; ArrayResize(csvData, 0);
   while(!FileIsEnding(handle)) {
      string line = FileReadString(handle); if(line == "") continue;
      if(StringFind(line, "#UTC_NOW:") == 0) { server_utc = StringToInteger(StringSubstr(line, 9)); continue; }
      if(StringFind(line, "#") == 0 || StringFind(line, "time") == 0) continue;
      string cols[]; StringSplit(line, ',', cols);
      if(ArraySize(cols) >= 6) { 
         int idx = ArraySize(csvData); ArrayResize(csvData, idx + 1);
         long epoch = StringToInteger(cols[0]); csvData[idx].time = (datetime)(epoch > 20000000000 ? epoch/1000 : epoch);
         csvData[idx].liq_long = StringToDouble(cols[4]) / 1000000.0; csvData[idx].liq_short = StringToDouble(cols[5]) / 1000000.0;
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
   static int last_mode = -1;
   
   int csv_len = ArraySize(csvData); 
   if(csv_len == 0) return(0);
   
   int mode = 0;
   if(GlobalVariableCheck("QPRO_LIQ_MODE")) mode = (int)GlobalVariableGet("QPRO_LIQ_MODE"); 
   
   int limit = prev_calculated == 0 ? 0 : prev_calculated - 10;
   if(limit < 0) limit = 0;
   
   if (csvData[csv_len - 1].time != last_csv_end || mode != last_mode) { 
      limit = 0; 
      last_csv_end = csvData[csv_len - 1].time;
      last_mode = mode; 
   }

   for(int i = limit; i < rates_total; i++) {
      datetime t_start = time[i];
      datetime t_end = t_start + PeriodSeconds();
      if (t_end <= csvData[0].time) { BufferShorts[i] = 0; BufferLongs[i] = 0; continue; }

      int end_idx = -1; int prev_idx = -1;
      
      
      for(int k = csv_len - 1; k >= 0; k--) {
         if (end_idx == -1 && csvData[k].time < t_end) end_idx = k;
         if (prev_idx == -1 && csvData[k].time < t_start) prev_idx = k;
         if (end_idx != -1 && prev_idx != -1) break;
      }

      if (end_idx != -1) {
         if(prev_idx == -1) prev_idx = 0;
         
         if (end_idx == prev_idx) { 
             BufferShorts[i] = (i > 0) ? BufferShorts[i-1] : 0;
             BufferLongs[i] = (i > 0) ? BufferLongs[i-1] : 0;
         } else {
             if (mode == 1) { 
                 BufferShorts[i] = csvData[end_idx].liq_short - csvData[prev_idx].liq_short;
                 BufferLongs[i] = -(csvData[end_idx].liq_long - csvData[prev_idx].liq_long);
             } else { 
                 BufferShorts[i] = csvData[end_idx].liq_short;
                 BufferLongs[i] = -csvData[end_idx].liq_long;
             }
         }
      } else if (i > 0) {
         BufferShorts[i] = (mode == 1) ? 0 : BufferShorts[i-1];
         BufferLongs[i] = (mode == 1) ? 0 : BufferLongs[i-1];
      }
   }
   return(rates_total);
}