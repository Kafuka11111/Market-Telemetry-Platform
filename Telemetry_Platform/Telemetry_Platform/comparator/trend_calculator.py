import asyncio
import ujson as json
import time
import logging
import sys
import os


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logger = logging.getLogger("Comparator")

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

EXCHANGES = ["binance", "bybit", "okx", "hyperliquid", "dydx", "bitget", "deribit", "htx", "gate"]

async def get_window_stats(redis_client, symbol, start_ts, end_ts):
    pipe = redis_client.pipeline()
    keys = [f"agg:minute:{symbol}:{ts}" for ts in range(start_ts, end_ts, 60000)]
    for key in keys:
        pipe.hgetall(key)
    results = await pipe.execute()

    stats = {
        "buy_vol": 0.0, "sell_vol": 0.0,
        "liq_short_vol": 0.0, "liq_long_vol": 0.0,
        "trades_count": 0,
        "ob_imb_sum": 0.0, "ob_bids_sum": 0.0, "ob_asks_sum": 0.0,
        "minutes_with_ob": 0
    }
    
    for ex in EXCHANGES:
        stats[f"{ex}_buy_vol"] = 0.0
        stats[f"{ex}_sell_vol"] = 0.0

    for minute_data in results:
        if not minute_data: 
            continue 
            
        stats["buy_vol"] += float(minute_data.get("buy_vol", 0))
        stats["sell_vol"] += float(minute_data.get("sell_vol", 0))
        stats["liq_short_vol"] += float(minute_data.get("liq_short_vol", 0))
        stats["liq_long_vol"] += float(minute_data.get("liq_long_vol", 0))
        stats["trades_count"] += int(minute_data.get("trades_count", 0))
        
        for ex in EXCHANGES:
            stats[f"{ex}_buy_vol"] += float(minute_data.get(f"{ex}_buy_vol", 0))
            stats[f"{ex}_sell_vol"] += float(minute_data.get(f"{ex}_sell_vol", 0))
        
        if "ob_imb" in minute_data:
            stats["ob_imb_sum"] += float(minute_data["ob_imb"])
            stats["ob_bids_sum"] += float(minute_data.get("ob_bids", 0))
            stats["ob_asks_sum"] += float(minute_data.get("ob_asks", 0))
            stats["minutes_with_ob"] += 1

    if stats["minutes_with_ob"] > 0:
        stats["avg_ob_imb"] = stats["ob_imb_sum"] / stats["minutes_with_ob"]
        stats["avg_ob_bids"] = stats["ob_bids_sum"] / stats["minutes_with_ob"]
        stats["avg_ob_asks"] = stats["ob_asks_sum"] / stats["minutes_with_ob"]
    else:
        stats["avg_ob_imb"] = stats["avg_ob_bids"] = stats["avg_ob_asks"] = 0

    return stats

def calc_delta_pct(current, previous):
    if previous == 0: 
        return 0.0
    return ((current - previous) / previous) * 100.0

async def get_historical_price(redis_client, symbol, target_ts, max_lookback=15):
    for offset in range(max_lookback):
        ts = target_ts - (offset * 60000)
        price = await redis_client.hget(f"agg:minute:{symbol}:{ts}", "close_price")
        if price:
            return float(price)
    return None

async def calculate_trends_loop():
    redis_client = await redis_manager.connect()
    logger.info("Comparator is running. Calculating market trends (All)...")
    
    while True:
        try:
            now_ms = int(time.time() * 1000)
            target_minute_ts = ((now_ms // 60000) * 60000) - 60000

            for symbol in SYMBOLS:
                live_data = await redis_client.hgetall(f"live_metrics:{symbol}")
                current_price = float(live_data.get("price", 0))
                if current_price == 0:  
                    current_price = float(live_data.get("bybit_price", 0)) or float(live_data.get("hyperliquid_price", 0))
                
                current_ob_bids = sum(float(v) for k, v in live_data.items() if "ob_bids_usd" in k)
                current_ob_asks = sum(float(v) for k, v in live_data.items() if "ob_asks_usd" in k)
                current_ob_imb = current_ob_bids - current_ob_asks
                
                
                oi_map = {}
                for ex in EXCHANGES:
                    key = "open_interest" if ex == "binance" else f"{ex}_open_interest"
                    oi_map[ex] = float(live_data.get(key, 0))
                
                global_oi = sum(oi_map.values())
                
                if global_oi > 0:
                    await redis_client.hset(f"live_metrics:{symbol}", "global_open_interest", global_oi)

                
                funding_map = {}
                weighted_f = 0
                total_8h_oi = 0
                
                for ex in EXCHANGES:
                    key = "funding_rate" if ex == "binance" else f"{ex}_funding_rate"
                    f_val = float(live_data.get(key, 0))
                    
                    funding_map[ex] = f_val
                    
                    
                    if f_val != 0 and oi_map[ex] > 0:
                        weighted_f += f_val * oi_map[ex]
                        total_8h_oi += oi_map[ex]

                global_funding = (weighted_f / total_8h_oi) if total_8h_oi > 0 else funding_map.get("binance", 0)

                if global_funding != 0:
                    await redis_client.hset(f"live_metrics:{symbol}", "global_funding_rate", global_funding)

                
                if current_price > 0:
                    minute_mapping = {
                        "close_price": current_price,
                        "ob_imb": current_ob_imb,
                        "ob_bids": current_ob_bids,
                        "ob_asks": current_ob_asks,
                        "open_interest": global_oi
                    }
                    for ex, val in oi_map.items():
                        minute_mapping[f"oi_{ex}"] = val
                        
                    await redis_client.hset(f"agg:minute:{symbol}:{target_minute_ts}", mapping=minute_mapping)

                windows = {
                    "1h": get_window_stats(redis_client, symbol, target_minute_ts - (60 * 60000), target_minute_ts),
                    "6h": get_window_stats(redis_client, symbol, target_minute_ts - (360 * 60000), target_minute_ts),
                    "12h": get_window_stats(redis_client, symbol, target_minute_ts - (720 * 60000), target_minute_ts),
                    "24h": get_window_stats(redis_client, symbol, target_minute_ts - (1440 * 60000), target_minute_ts)
                }
                
                keys = list(windows.keys())
                results = await asyncio.gather(*windows.values())
                stats = dict(zip(keys, results))
                
                prices = {
                    "1h": await get_historical_price(redis_client, symbol, target_minute_ts - (60 * 60000)),
                    "6h": await get_historical_price(redis_client, symbol, target_minute_ts - (360 * 60000)),
                    "12h": await get_historical_price(redis_client, symbol, target_minute_ts - (720 * 60000)),
                    "24h": await get_historical_price(redis_client, symbol, target_minute_ts - (1440 * 60000))
                }
                
                oi_1h_raw = await redis_client.hget(f"agg:minute:{symbol}:{target_minute_ts - (60 * 60000)}", "open_interest")
                oi_1h_ago = float(oi_1h_raw) if oi_1h_raw else global_oi
                
                final_state = {
                    "symbol": symbol.upper(),
                    "timestamp": now_ms,
                    "oi_change_1h": calc_delta_pct(global_oi, oi_1h_ago),
                    "price_change": {
                        f"{tf}_pct": calc_delta_pct(current_price, prices[tf] or current_price) for tf in ["1h", "6h", "12h", "24h"]
                    }
                }

                
                for tf in ["1h", "6h", "12h", "24h"]:
                    global_vol = stats[tf]["buy_vol"] + stats[tf]["sell_vol"]
                    final_state[f"last_{tf}"] = {
                        "total_volume_usd": round(global_vol, 2),
                        "cvd_usd": round(stats[tf]["buy_vol"] - stats[tf]["sell_vol"], 2),
                        "liq_long_usd": round(stats[tf]["liq_long_vol"], 2),
                        "liq_short_usd": round(stats[tf]["liq_short_vol"], 2),
                        "whale_trades_count": stats[tf]["trades_count"],
                        "avg_ob_imb": round(stats[tf].get("avg_ob_imb", 0), 2),
                        "avg_ob_bids": round(stats[tf].get("avg_ob_bids", 0), 2),
                        "avg_ob_asks": round(stats[tf].get("avg_ob_asks", 0), 2)
                    }
                    
                    for ex in EXCHANGES:
                        buy_ex = stats[tf][f"{ex}_buy_vol"]
                        sell_ex = stats[tf][f"{ex}_sell_vol"]
                        final_state[f"last_{tf}"][f"cvd_{ex}"] = round(buy_ex - sell_ex, 2)
                        final_state[f"last_{tf}"][f"vol_{ex}"] = round(buy_ex + sell_ex, 2)

                
                longs_merged = {}
                shorts_merged = {}
                current_hour = (now_ms // 3600000) * 3600000
                
                for i in range(24):
                    h = current_hour - (i * 3600000)
                    l_raw = await redis_client.hgetall(f"liq:heatmap:longs:{symbol.upper()}:{h}")
                    s_raw = await redis_client.hgetall(f"liq:heatmap:shorts:{symbol.upper()}:{h}")
                    
                    for p, v in l_raw.items():
                        longs_merged[p] = longs_merged.get(p, 0) + float(v)
                    for p, v in s_raw.items():
                        shorts_merged[p] = shorts_merged.get(p, 0) + float(v)
                
                final_state["heatmap"] = {
                    "longs": longs_merged,
                    "shorts": shorts_merged
                }

                await redis_client.set(f"market:state:{symbol}", json.dumps(final_state))
                
                
                emas_raw = await redis_client.hgetall(f"market:emas:{symbol}")
                prev_50 = float(emas_raw.get("50", current_price))
                prev_200 = float(emas_raw.get("200", current_price))
                prev_800 = float(emas_raw.get("800", current_price))
                
                curr_50 = (current_price * (2/51)) + (prev_50 * (1 - (2/51)))
                curr_200 = (current_price * (2/201)) + (prev_200 * (1 - (2/201)))
                curr_800 = (current_price * (2/801)) + (prev_800 * (1 - (2/801)))
                
                await redis_client.hset(f"market:emas:{symbol}", mapping={
                    "50": curr_50, "200": curr_200, "800": curr_800
                })

                
                point = {
                    "time": target_minute_ts // 1000, 
                    "price": current_price,
                    "ema50": round(curr_50, 2),
                    "ema200": round(curr_200, 2),
                    "ema800": round(curr_800, 2),
                    "cvd": final_state["last_1h"]["cvd_usd"],
                    "volume": final_state["last_1h"]["total_volume_usd"],
                    "liq_long": final_state["last_1h"]["liq_long_usd"],
                    "liq_short": final_state["last_1h"]["liq_short_usd"],
                    "open_interest": global_oi * current_price,
                    "funding_global": global_funding
                }
                
                for ex in EXCHANGES:
                    point[f"oi_{ex}"] = oi_map[ex] * current_price
                    point[f"cvd_{ex}"] = final_state["last_1h"].get(f"cvd_{ex}", 0)
                    point[f"vol_{ex}"] = final_state["last_1h"].get(f"vol_{ex}", 0)
                    point[f"funding_{ex}"] = funding_map[ex]

                await redis_client.rpush(f"chart:history:{symbol}", json.dumps(point))
                await redis_client.ltrim(f"chart:history:{symbol}", -1440, -1)
                
            sleep_time = 60 - (time.time() % 60)
            await asyncio.sleep(sleep_time + 2.0)
        except Exception as e:
            logger.error(f"Error: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(calculate_trends_loop())
    except KeyboardInterrupt:
        logger.info("Comparator stopped.")