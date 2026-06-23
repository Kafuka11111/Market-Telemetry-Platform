#property copyright "Realtime Telemetry Platform"
#property indicator_separate_window
#property indicator_buffers 2
#property indicator_plots   1
#property indicator_label1  "Funding"
#property indicator_type1   DRAW_COLOR_HISTOGRAM
#property indicator_color1  C'34,171,148', C'242,54,69'
#property indicator_width1  2

double BufferFund[], BufferColor[];
string current_symbol = "";
struct TCsvPoint { datetime time; double fund; };
TCsvPoint csvData[];

int OnInit() {
   if (StringFind(_Symbol, "BTC") >= 0) current_symbol = "BTCUSDT"; else if (StringFind(_Symbol, "ETH") >= 0) current_symbol = "ETHUSDT"; else return(INIT_FAILED);
   SetIndexBuffer(0, BufferFund, INDICATOR_DATA); SetIndexBuffer(1, BufferColor, INDICATOR_COLOR_INDEX);
   IndicatorSetString(INDICATOR_SHORTNAME, "Funding Rate (%)"); IndicatorSetInteger(INDICATOR_DIGITS, 4);
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
      if(ArraySize(cols) >= 11) { 
         int idx = ArraySize(csvData); ArrayResize(csvData, idx + 1);
         long epoch = StringToInteger(cols[0]); csvData[idx].time = (datetime)(epoch > 20000000000 ? epoch/1000 : epoch);
         csvData[idx].fund = StringToDouble(cols[10]); 
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

   for(int i = limit; i < rates_total; i++) {
      datetime t_end = time[i] + PeriodSeconds();
      if (t_end <= csvData[0].time) { BufferFund[i] = 0; BufferColor[i] = 0; continue; }

      int end_idx = -1;
      for(int k = csv_len - 1; k >= 0; k--) {
         if (csvData[k].time < t_end) { end_idx = k; break; }
      }

      if (end_idx != -1) {
         double fund = csvData[end_idx].fund;
         BufferFund[i] = fund;
         
         
         if (fund > 0) BufferColor[i] = 0;
         else if (fund < 0) BufferColor[i] = 1;
         else BufferColor[i] = (i > 0) ? BufferColor[i-1] : 0;

      } else if (i > 0) { 
         BufferFund[i] = BufferFund[i-1];
         BufferColor[i] = BufferColor[i-1];
      }
   }
   return(rates_total);
}