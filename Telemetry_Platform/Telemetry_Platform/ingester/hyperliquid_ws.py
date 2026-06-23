import asyncio
import websockets
import ujson as json
import logging
import sys
import os
import urllib.request

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logger = logging.getLogger("HyperliquidIngester")
STREAM_NAME = "raw_market_stream"
SYMBOLS = ["BTC", "ETH"] 

async def fetch_hl_context(redis_client):
    url = "https://api.hyperliquid.xyz/info"
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            def fetch():
                req = urllib.request.Request(
                    url, 
                    data=json.dumps({"type": "metaAndAssetCtxs"}).encode('utf-8'), 
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read().decode())
                    
            data = await loop.run_in_executor(None, fetch)
            
            if len(data) == 2:
                meta_universe = data[0].get("universe", [])
                asset_ctxs = data[1]
                
                for i, asset in enumerate(meta_universe):
                    coin = asset.get("name")
                    if coin in SYMBOLS:
                        ctx = asset_ctxs[i]
                        symbol = f"{coin}USDT"
                        
                        updates = {}
                        if "openInterest" in ctx:
                            updates["hyperliquid_open_interest"] = float(ctx["openInterest"])
                        if "funding" in ctx:
                            updates["hyperliquid_funding_rate"] = float(ctx["funding"]) * 100
                        if "markPx" in ctx:
                            updates["hyperliquid_price"] = float(ctx["markPx"])
                            
                        if updates:
                            await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)
            
        except Exception as e:
            pass 
            
        await asyncio.sleep(15)

async def process_hyperliquid_message(redis_client, message):
    try:
        data = json.loads(message)
        channel = data.get('channel')

        if channel == 'trades':
            trades = data['data']
            for trade in trades:
                price = float(trade['px'])
                qty = float(trade['sz'])
                usd_value = price * qty
                
                if usd_value < 1000: continue
                
                side = "BUY" if trade['side'] == "B" else "SELL"
                symbol = f"{trade['coin']}USDT"
                
                await redis_client.xadd(STREAM_NAME, {
                        "exchange": "hyperliquid",
                        "symbol": symbol,
                        "type": "trade",
                        "side": side,
                        "price": price,
                        "usd_value": usd_value,
                        "timestamp": trade['time']
                    },
                    maxlen=100000,
                    approximate=True
                )
                
        elif channel == 'l2Book':
            coin = data['data']['coin']
            if coin not in SYMBOLS: return
            symbol = f"{coin}USDT"
            
            levels = data['data']['levels']
            if len(levels) == 2:
                bids = [(float(lvl['px']), float(lvl['sz'])) for lvl in levels[0]]
                asks = [(float(lvl['px']), float(lvl['sz'])) for lvl in levels[1]]
                
                bids_usd = sum(p * q for p, q in bids)
                asks_usd = sum(p * q for p, q in asks)
                imbalance_usd = bids_usd - asks_usd

                updates = {}
                if bids_usd > 0: updates["hyperliquid_ob_bids_usd"] = bids_usd
                if asks_usd > 0: updates["hyperliquid_ob_asks_usd"] = asks_usd
                if imbalance_usd != 0: updates["hyperliquid_ob_imbalance_usd"] = imbalance_usd
                
                if updates:
                    await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

    except Exception as e:
        logger.error(f"⚠️ {e}")

async def hyperliquid_ws_loop():
    redis_client = await redis_manager.connect()
    url = "wss://api.hyperliquid.xyz/ws"
    
    hl_task = asyncio.create_task(fetch_hl_context(redis_client))
    
    sub_msgs = []
    for coin in SYMBOLS:
        sub_msgs.append(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}))
        sub_msgs.append(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}}))

    while True:
        try:
            logger.info("Connecting to Hyperliquid WS...")
            async with websockets.connect(url, ping_interval=20, max_size=10**7) as ws:
                for msg in sub_msgs:
                    await ws.send(msg)
                logger.info("✅ Hyperliquid Subscribed to Trades and Orderbook")
                
                async for message in ws:
                    await process_hyperliquid_message(redis_client, message)
        except Exception as e:
            logger.error(f"❌ Hyperliquid Error: {e}. Reconnecting...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(hyperliquid_ws_loop())