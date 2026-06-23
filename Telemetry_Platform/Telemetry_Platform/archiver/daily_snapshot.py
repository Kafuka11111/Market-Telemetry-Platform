import asyncio
import pandas as pd
from datetime import datetime, timedelta, timezone
import logging
import sys
import os


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Archiver")

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots")


os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

async def archive_yesterday_data():
    
    redis_client = await redis_manager.connect()
    
    
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    
    start_of_yesterday = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc)
    end_of_yesterday = start_of_yesterday + timedelta(days=1)
    
    start_ts = int(start_of_yesterday.timestamp() * 1000)
    end_ts = int(end_of_yesterday.timestamp() * 1000)

    logger.info(f"SNAPSHOT {start_of_yesterday.strftime('%Y-%m-%d')}...")

    for symbol in SYMBOLS:
        pipe = redis_client.pipeline()
        timestamps = []
        
        
        for ts in range(start_ts, end_ts, 60000):
            keys_name = f"agg:minute:{symbol}:{ts}"
            pipe.hgetall(keys_name)
            timestamps.append(ts)
            
        results = await pipe.execute()
        
        EXCHANGES = ["binance", "bybit", "okx", "bitget", "deribit", "hyperliquid", "dydx", "htx", "gate"]
        
        rows = []
        for i, minute_data in enumerate(results):
            if minute_data: 
                row = {
                    "timestamp_ms": timestamps[i],
                    "datetime": datetime.fromtimestamp(timestamps[i]/1000, tz=timezone.utc),
                    
                    
                    "close_price": float(minute_data.get("close_price", 0)),
                    "open_interest": float(minute_data.get("open_interest", 0)),
                    "funding_global": float(minute_data.get("funding_global", 0)),
                    "ema50": float(minute_data.get("ema50", 0)),
                    "ema200": float(minute_data.get("ema200", 0)),
                    "ema800": float(minute_data.get("ema800", 0)),
                    
                    
                    "ob_imb": float(minute_data.get("ob_imb", 0)),
                    "ob_bids": float(minute_data.get("ob_bids", 0)),
                    "ob_asks": float(minute_data.get("ob_asks", 0)),

                    
                    "buy_vol": float(minute_data.get("buy_vol", 0)),
                    "sell_vol": float(minute_data.get("sell_vol", 0)),
                    "liq_short_vol": float(minute_data.get("liq_short_vol", 0)),
                    "liq_long_vol": float(minute_data.get("liq_long_vol", 0)),
                    "trades_count": int(minute_data.get("trades_count", 0))
                }
                
                
                for zone in ['close', 'mid', 'macro']:
                    for side in ['bid', 'ask']:
                        row[f"whale_{zone}_{side}_price"] = float(minute_data.get(f"whale_{zone}_{side}_price", 0))
                        row[f"whale_{zone}_{side}_vol"] = float(minute_data.get(f"whale_{zone}_{side}_vol", 0))
                
                
                for ex in EXCHANGES:
                    row[f"{ex}_buy_vol"] = float(minute_data.get(f"{ex}_buy_vol", 0))
                    row[f"{ex}_sell_vol"] = float(minute_data.get(f"{ex}_sell_vol", 0))
                    row[f"{ex}_liq_short_vol"] = float(minute_data.get(f"{ex}_liq_short_vol", 0))
                    row[f"{ex}_liq_long_vol"] = float(minute_data.get(f"{ex}_liq_long_vol", 0))
                    row[f"oi_{ex}"] = float(minute_data.get(f"oi_{ex}", 0))
                    row[f"funding_{ex}"] = float(minute_data.get(f"funding_{ex}", 0))

                rows.append(row)
                
        if rows:
            df = pd.DataFrame(rows)
            df.sort_values("timestamp_ms", inplace=True)
            
            date_str = start_of_yesterday.strftime('%Y-%m-%d')
            filename = os.path.join(SNAPSHOTS_DIR, f"{symbol}_{date_str}.parquet")
            
            df.to_parquet(filename, index=False)
            logger.info(f"NAPSHOT: {filename} ({len(df)} safe)")
        else:
            logger.warning(f"⚠️ NO {symbol} for {start_of_yesterday.strftime('%Y-%m-%d')}")

async def archiver_loop():
    while True:
        now = datetime.now(timezone.utc)
        target_time = datetime(now.year, now.month, now.day, 0, 5, 0, tzinfo=timezone.utc)
        if now >= target_time:
            target_time += timedelta(days=1)
            
        sleep_seconds = (target_time - now).total_seconds()
        
        
        await asyncio.sleep(sleep_seconds)
        
        try:
            await archive_yesterday_data()
        except Exception as e:
            logger.error(f"ERROR: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(archiver_loop())
    except KeyboardInterrupt:
        logger.info("Archiver stopped.")