import asyncio
import websockets
import ujson as json
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logger = logging.getLogger("BitgetIngester")
STREAM_NAME = "raw_market_stream"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

async def process_bitget_message(redis_client, message):
    if message == "pong": return
    try:
        data = json.loads(message)
        
        if 'error' in data:
            logger.error(f"❌ BIDGET REFUSED CONNECTION: {data['error']}")
            return
        if 'data' not in data or 'arg' not in data: return

        channel = data['arg']['channel']
        symbol_raw = data['arg']['instId']
        
        symbol = symbol_raw.replace("_UMCBL", "").replace("_DMCBL", "").upper()

        if symbol not in SYMBOLS:
            return

        if channel == "trade":
            for trade in data['data']:
                price = float(trade.get('price', trade.get('px', 0)))
                qty = float(trade.get('size', trade.get('sz', 0)))
                usd_value = price * qty
                
                if usd_value < 1000: continue
                
                side_raw = str(trade.get('side', '')).lower()
                side = "BUY" if "buy" in side_raw else "SELL"
                
                await redis_client.xadd(STREAM_NAME, {
                    "exchange": "bitget",
                    "symbol": symbol,
                    "type": "trade",
                    "side": side,
                    "price": price,
                    "usd_value": usd_value,
                    "timestamp": trade.get('ts', data.get('ts', 0))
                }, maxlen=100000, approximate=True)

        elif channel == "ticker":
            for ticker in data['data']:
                updates = {}
                oi_raw = ticker.get('holdingAmount', ticker.get('openInterest', 0))
                if oi_raw:
                    updates["bitget_open_interest"] = float(oi_raw)
                
                if 'fundingRate' in ticker and ticker['fundingRate']:
                    updates["bitget_funding_rate"] = float(ticker['fundingRate']) * 100

                if updates:
                    await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

        elif channel == "books":
            for book in data['data']:
                bids = [(float(p), float(q)) for p, q in book.get('bids', []) if p and q]
                asks = [(float(p), float(q)) for p, q in book.get('asks', []) if p and q]
                
                if bids or asks:
                    bids_usd = sum(p * q for p, q in bids)
                    asks_usd = sum(p * q for p, q in asks)
                    imbalance_usd = bids_usd - asks_usd

                    updates = {}
                    if bids_usd > 0: updates["bitget_ob_bids_usd"] = bids_usd
                    if asks_usd > 0: updates["bitget_ob_asks_usd"] = asks_usd
                    if imbalance_usd != 0: updates["bitget_ob_imbalance_usd"] = imbalance_usd
                    
                    if updates:
                        await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

        elif channel == "liquidation":
            for liq in data['data']:
                price = float(liq.get('price', liq.get('p', 0)))
                qty = float(liq.get('size', liq.get('sz', liq.get('v', 0))))
                usd_value = price * qty
                
                if usd_value == 0: continue

                side_raw = str(liq.get('side', liq.get('S', ''))).lower()
                
                side = "SHORT_LIQ" if "buy" in side_raw else "LONG_LIQ"
                
                await redis_client.xadd(STREAM_NAME, {
                    "exchange": "bitget",
                    "symbol": symbol,
                    "type": "liquidation",
                    "side": side,
                    "price": price,
                    "usd_value": usd_value,
                    "timestamp": liq.get('ts', liq.get('cTime', 0))
                }, maxlen=100000, approximate=True)
                logger.info(f"🚨 BITGET LIQ: {symbol} {side} for ${usd_value:,.0f}")

    except Exception as e:
        logger.error(f"⚠️ {e}")

async def bitget_ws_loop():
    redis_client = await redis_manager.connect()
    url = "wss://ws.bitget.com/v2/ws/public"
    
    args = []
    for sym in SYMBOLS:
        args.extend([
            {"instType": "USDT-FUTURES", "channel": "trade", "instId": sym},
            {"instType": "USDT-FUTURES", "channel": "ticker", "instId": sym},
            {"instType": "USDT-FUTURES", "channel": "books", "instId": sym},
            {"instType": "USDT-FUTURES", "channel": "liquidation", "instId": sym}
        ])

    sub_msg = json.dumps({
        "op": "subscribe",
        "args": args
    })

    while True:
        try:
            logger.info("Connecting to Bitget WS...")
            async with websockets.connect(url, ping_interval=None, max_size=10**7) as ws:
                await ws.send(sub_msg)
                logger.info("✅ Bitget Subscribed to Trades, Tick, Books, Liq")
                
                async def keep_alive():
                    while True:
                        await asyncio.sleep(20)
                        try:
                            await ws.send("ping")
                        except:
                            break
                            
                asyncio.create_task(keep_alive())

                async for message in ws:
                    if message == "pong":
                        continue
                    await process_bitget_message(redis_client, message)
        except Exception as e:
            logger.error(f"❌ Bitget Error: {e}. Reconnecting...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(bitget_ws_loop())