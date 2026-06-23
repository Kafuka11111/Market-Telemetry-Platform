import ujson as json
import asyncio
import logging
import sys
import os


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logger = logging.getLogger("Aggregator")

STREAM_NAME = "raw_market_stream"
GROUP_NAME = "aggregator_group"
CONSUMER_NAME = "worker_1"
TTL_SECONDS = 48 * 3600

async def setup_consumer_group(redis_client):
    
    try:
        
        await redis_client.xgroup_create(STREAM_NAME, GROUP_NAME, id='0-0', mkstream=True)
        logger.info(f"✅ Created Consumer Group '{GROUP_NAME}'")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            logger.info(f"Consumer Group '{GROUP_NAME}' already exists. Resuming...")
        else:
            logger.error(f"Error creating group: {e}")

async def process_messages():
    
    redis_client = await redis_manager.connect()
    await setup_consumer_group(redis_client)

    logger.info("⏳ Aggregator is listening for raw data...")
    
    
    last_prices = {}
    
    while True:
        try:
            messages = await redis_client.xreadgroup(
                groupname=GROUP_NAME,
                consumername=CONSUMER_NAME,
                streams={STREAM_NAME: ">"},
                count=100,
                block=1000
            )

            if not messages:
                continue

            pipe = redis_client.pipeline()
            processed_ids = []

            for stream, msg_list in messages:
                for msg_id, data in msg_list:
                    symbol = data.get("symbol")
                    event_type = data.get("type")
                    side = data.get("side")
                    usd_value = float(data.get("usd_value", 0))
                    
                    ts_ms = int(data.get("timestamp"))
                    minute_ts = (ts_ms // 60000) * 60000 
                    minute_key = f"agg:minute:{symbol}:{minute_ts}"

                    
                    raw_price = data.get("price")
                    price = float(raw_price) if raw_price is not None else 0.0
                    
                    BIN_SIZE = 50 if "BTC" in symbol.upper() else 5
                    
                    current_hour = (ts_ms // 3600000) * 3600000
                    exchange = data.get("exchange", "unknown") 

                    if event_type == "trade":
                        
                        last_price = last_prices.get(symbol, price)
                        min_p = min(last_price, price)
                        max_p = max(last_price, price)
                        
                        start_bin = int(min_p // BIN_SIZE) * BIN_SIZE
                        end_bin = int(max_p // BIN_SIZE) * BIN_SIZE
                        
                        bins_to_clear = [str(b) for b in range(start_bin, end_bin + BIN_SIZE, BIN_SIZE)]

                        if bins_to_clear:
                            for i in range(24):
                                h = current_hour - (i * 3600000)
                                pipe.hdel(f"liq:heatmap:longs:{symbol.upper()}:{h}", *bins_to_clear)
                                pipe.hdel(f"liq:heatmap:shorts:{symbol.upper()}:{h}", *bins_to_clear)
                        
                        last_prices[symbol] = price
                        
                        vol_bin = int(price // BIN_SIZE) * BIN_SIZE
                        profile_key = f"vol_profile:{symbol.upper()}"
                        
                        field = f"{vol_bin}:buy" if side == "BUY" else f"{vol_bin}:sell"
                        pipe.hincrbyfloat(profile_key, field, usd_value)
                        
                        
                        far_high_bin = int((price * 1.15) // BIN_SIZE) * BIN_SIZE
                        far_low_bin = int((price * 0.85) // BIN_SIZE) * BIN_SIZE
                        pipe.hdel(profile_key, f"{far_high_bin}:buy", f"{far_high_bin}:sell", f"{far_low_bin}:buy", f"{far_low_bin}:sell")
                        

                        
                        vol_field = "buy_vol" if side == "BUY" else "sell_vol"
                        pipe.hincrbyfloat(minute_key, vol_field, usd_value)
                        pipe.hincrby(minute_key, "trades_count", 1)
                        
                        
                        pipe.hincrbyfloat(minute_key, f"{exchange}_{vol_field}", usd_value)

                        if usd_value >= 10000:
                            whale_msg = {
                                "type": "whale_trade", "symbol": symbol, "side": side,
                                "price": price, "usd_value": usd_value, "exchange": exchange
                            }
                            pipe.publish("whale_tape", json.dumps(whale_msg))
                            
                        if usd_value >= 50000:
                            profiles = [
                                {"leverage": 100, "drop": 0.01, "weight": 0.20},
                                {"leverage": 50,  "drop": 0.02, "weight": 0.30},
                                {"leverage": 25,  "drop": 0.04, "weight": 0.50}
                            ]
                            for prof in profiles:
                                liq_vol = usd_value * prof["weight"]
                                if side == "BUY":
                                    liq_price = price * (1 - prof["drop"])
                                    liq_bin = int(liq_price // BIN_SIZE) * BIN_SIZE
                                    syn_key = f"liq:heatmap:longs:{symbol.upper()}:{current_hour}"
                                    pipe.hincrbyfloat(syn_key, str(liq_bin), liq_vol)
                                    pipe.expire(syn_key, 86400)
                                elif side == "SELL":
                                    liq_price = price * (1 + prof["drop"])
                                    liq_bin = int(liq_price // BIN_SIZE) * BIN_SIZE
                                    syn_key = f"liq:heatmap:shorts:{symbol.upper()}:{current_hour}"
                                    pipe.hincrbyfloat(syn_key, str(liq_bin), liq_vol)
                                    pipe.expire(syn_key, 86400)

                    elif event_type == "liquidation":
                        
                        liq_field = "liq_short_vol" if side == "SHORT_LIQ" else "liq_long_vol"
                        pipe.hincrbyfloat(minute_key, liq_field, usd_value)
                        
                        
                        pipe.hincrbyfloat(minute_key, f"{exchange}_{liq_field}", usd_value)
                        
                        price_bin = int(price // BIN_SIZE) * BIN_SIZE
                        hist_key = f"liq:heatmap:history:{symbol.upper()}:{current_hour}"
                        pipe.hincrbyfloat(hist_key, str(price_bin), usd_value)
                        pipe.expire(hist_key, 86400)

                    
                    elif event_type == "book_imbalance":
                        
                        pipe.hincrbyfloat(minute_key, "ob_imb", usd_value)

                    pipe.expire(minute_key, TTL_SECONDS)
                    processed_ids.append(msg_id)

            await pipe.execute()

            if processed_ids:
                await redis_client.xack(STREAM_NAME, GROUP_NAME, *processed_ids)

        except Exception as e:
            error_msg = str(e)
            if "NOGROUP" in error_msg:
                logger.warning("⚠️ Pipe lost (NOGROUP)...")
                try:
                    
                    await redis_client.xgroup_create(STREAM_NAME, GROUP_NAME, id='0-0', mkstream=True)
                    logger.info("✅ Pipe has been successfully restored.")
                except Exception:
                    pass
            else:
                logger.error(f"Error in aggregator loop: {e}")
            
            await asyncio.sleep(2) 

if __name__ == "__main__":
    try:
        asyncio.run(process_messages())
    except KeyboardInterrupt:
        logger.info("Aggregator stopped by user.")