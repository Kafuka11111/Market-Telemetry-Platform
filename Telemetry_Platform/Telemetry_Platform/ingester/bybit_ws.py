import asyncio
import websockets
import ujson as json
import logging
import sys
import os
import urllib.request

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logger = logging.getLogger("BybitIngester")

STREAM_NAME = "raw_market_stream"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

async def fetch_bybit_oi(redis_client):
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            for symbol in SYMBOLS:
                req_url = f"{url}&symbol={symbol}"
                def fetch():
                    req = urllib.request.Request(
                        req_url, 
                        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    )
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.loads(response.read().decode())
                        
                data = await loop.run_in_executor(None, fetch)
                
                if data.get("retCode") == 0:
                    res_list = data.get("result", {}).get("list", [])
                    if res_list:
                        oi = float(res_list[0].get("openInterest", 0))
                        funding = float(res_list[0].get("fundingRate", 0)) * 100
                        await redis_client.hset(f"live_metrics:{symbol}", mapping={
                            "bybit_open_interest": oi,
                            "bybit_funding_rate": funding
                        })
        except Exception:
            pass
        await asyncio.sleep(15)

async def process_bybit_message(redis_client, message):
    try:
        data = json.loads(message)
        if 'topic' not in data: return
        
        topic = data['topic']
        raw_data = data.get('data')
        if not raw_data: return
        
        symbol = None
        if isinstance(raw_data, dict):
            symbol = raw_data.get('symbol')
        elif isinstance(raw_data, list) and len(raw_data) > 0:
            symbol = raw_data[0].get('symbol')
            
        if not symbol:
            parts = topic.split('.')
            if len(parts) > 1: symbol = parts[-1]
        
        if symbol not in SYMBOLS: return

        if topic.startswith("publicTrade"):
            trade_list = raw_data if isinstance(raw_data, list) else [raw_data]
            for trade in trade_list:
                price = float(trade['p'])
                qty = float(trade['v'])
                usd_value = price * qty
                
                await redis_client.hset(f"live_metrics:{symbol}", mapping={"price": price})
                
                if usd_value < 1000: continue
                
                side = "BUY" if trade['S'] == "Buy" else "SELL"
                await redis_client.xadd(STREAM_NAME, {
                    "exchange": "bybit", "symbol": symbol, "type": "trade",
                    "side": side, "price": price, "usd_value": usd_value,
                    "timestamp": trade['T']
                }, maxlen=100000, approximate=True)

        elif topic.startswith("allLiquidation"):
            liq_list = raw_data if isinstance(raw_data, list) else [raw_data]
            for liq in liq_list:
                price = float(liq.get('p', 0))
                qty = float(liq.get('v', 0))
                usd_value = price * qty
                if usd_value > 0:
                    side = "SHORT_LIQ" if liq.get('S') == "Buy" else "LONG_LIQ"
                    await redis_client.xadd(STREAM_NAME, {
                        "exchange": "bybit", "symbol": symbol, "type": "liquidation",
                        "side": side, "price": price, "usd_value": usd_value,
                        "timestamp": liq.get('T', 0)
                    }, maxlen=100000, approximate=True)
                    logger.info(f"BYBIT LIQ: {symbol} {side} for ${usd_value:,.0f}")

        elif topic.startswith("orderbook"):
            ob_data = raw_data[0] if isinstance(raw_data, list) else raw_data
            bids = ob_data.get('b', [])
            asks = ob_data.get('a', [])
            
            bids_usd = sum(float(p) * float(q) for p, q in bids)
            asks_usd = sum(float(p) * float(q) for p, q in asks)
            imbalance_usd = bids_usd - asks_usd

            updates = {}
            if bids_usd > 0: updates["bybit_ob_bids_usd"] = bids_usd
            if asks_usd > 0: updates["bybit_ob_asks_usd"] = asks_usd
            if imbalance_usd != 0: updates["bybit_ob_imbalance_usd"] = imbalance_usd
            
            if updates:
                await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

    except Exception as e:
        logger.error(f"⚠️ {e}")

async def bybit_ws_loop():
    redis_client = await redis_manager.connect()
    asyncio.create_task(fetch_bybit_oi(redis_client))
    
    url = "wss://stream.bybit.com/v5/public/linear"
    args = []
    for sym in SYMBOLS:
        args.extend([f"publicTrade.{sym}", f"allLiquidation.{sym}", f"orderbook.50.{sym}"])
    
    sub_msg = json.dumps({"op": "subscribe", "args": args})

    while True:
        try:
            logger.info("Connecting to Bybit WS...")
            async with websockets.connect(url, ping_interval=20, max_size=10**7) as ws:
                await ws.send(sub_msg)
                logger.info(f"✅ Bybit Subscribed")
                async for message in ws:
                    await process_bybit_message(redis_client, message)
        except Exception as e:
            logger.error(f"❌ Bybit Error: {e}. Reconnecting...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(bybit_ws_loop())