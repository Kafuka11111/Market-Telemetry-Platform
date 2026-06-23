#property copyright "Realtime Telemetry Platform"
#property indicator_separate_window
#property indicator_buffers 2
#property indicator_plots   1
#property indicator_label1  "Volume"
#property indicator_type1   DRAW_COLOR_HISTOGRAM
#property indicator_color1  C'34,171,148', C'242,54,69'
#property indicator_width1  2

double BufferVol[], BufferColor[];
string current_symbol = "";
int global_tz_offset = 0; 
struct TCsvPoint { datetime time; double vol; double cvd; };
TCsvPoint csvData[];

int OnInit() {
   if (StringFind(_Symbol, "BTC") >= 0) current_symbol = "BTCUSDT"; else if (StringFind(_Symbol, "ETH") >= 0) current_symbol = "ETHUSDT"; else return(INIT_FAILED);
   SetIndexBuffer(0, BufferVol, INDICATOR_DATA); SetIndexBuffer(1, BufferColor, INDICATOR_COLOR_INDEX);
   IndicatorSetString(INDICATOR_SHORTNAME, "Volume"); IndicatorSetInteger(INDICATOR_DIGITS, 2);
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
      if(ArraySize(cols) >= 4) { 
         int idx = ArraySize(csvData); ArrayResize(csvData, idx + 1);
         long epoch = StringToInteger(cols[0]); csvData[idx].time = (datetime)(epoch > 20000000000 ? epoch/1000 : epoch);
         csvData[idx].cvd = StringToDouble(cols[2]) / 1000000.0;
         csvData[idx].vol = StringToDouble(cols[3]) / 1000000.0;
      }
   }
   FileClose(handle);
   int arr_size = ArraySize(csvData);
   if (arr_size > 0 && server_utc > 0) {
      global_tz_offset = (int)MathRound((double)(TimeCurrent() - server_utc) / 3600.0) * 3600;
      for(int i = 0; i < arr_size; i++) csvData[i].time += global_tz_offset;
   }
}

datetime GetSessionStartUTC(datetime utc_t, int mode) {
   if(mode <= 0) return 0; MqlDateTime tm; TimeToStruct(utc_t, tm);
   int cm = tm.hour * 60 + tm.min; int tg = 0;
   if(mode == 1) tg = 0; if(mode == 2) tg = 8 * 60; if(mode == 3) tg = 13 * 60 + 30;
   if(cm >= tg) { tm.hour = tg / 60; tm.min = tg % 60; tm.sec = 0; return StructToTime(tm); } 
   else { datetime prev = utc_t - 86400; TimeToStruct(prev, tm); tm.hour = tg / 60; tm.min = tg % 60; tm.sec = 0; return StructToTime(tm); }
}

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[], const double &high[], const double &low[], const double &close[], const long &tick_volume[], const long &volume[], const int &spread[]) {
   
   static datetime last_csv_end = 0;
   static int last_v = -1;
   
   int csv_len = ArraySize(csvData); 
   if(csv_len == 0) return(0);
   
   int v_mode = 0;
   if(GlobalVariableCheck("QPRO_VOL_MODE")) v_mode = (int)GlobalVariableGet("QPRO_VOL_MODE");
   
   int limit = prev_calculated == 0 ? 0 : prev_calculated - 10;
   if(limit < 0) limit = 0;
   
   
   if (csvData[csv_len - 1].time != last_csv_end || v_mode != last_v) { 
      limit = 0; 
      last_csv_end = csvData[csv_len - 1].time;
      last_v = v_mode; 
   }

   for(int i = limit; i < rates_total; i++) {
      datetime t_start = time[i];
      datetime t_end = t_start + PeriodSeconds();
      
      if (t_end <= csvData[0].time) { 
         BufferVol[i] = 0; BufferColor[i] = 0; continue;
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
             BufferVol[i] = (i > 0) ? BufferVol[i-1] : 0;
             BufferColor[i] = (i > 0) ? BufferColor[i-1] : 0;
         } else {
             
             double cvd_delta = csvData[end_idx].cvd - csvData[prev_idx].cvd;

             if (v_mode == 1) { 
                BufferVol[i] = MathAbs(cvd_delta); 
             } else {
                BufferVol[i] = csvData[end_idx].vol;
             }
             
             if (cvd_delta > 0) BufferColor[i] = 0;
             else if (cvd_delta < 0) BufferColor[i] = 1;
             else if (i > 0) BufferColor[i] = BufferColor[i-1]; 
             else BufferColor[i] = 0;
         }
      } else if (i > 0) { 
         BufferVol[i] = (v_mode == 1) ? 0 : BufferVol[i-1]; 
         BufferColor[i] = BufferColor[i-1]; 
      }
   }
   return(rates_total);
}