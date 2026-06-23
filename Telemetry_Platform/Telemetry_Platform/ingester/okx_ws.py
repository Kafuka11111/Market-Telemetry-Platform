import asyncio
import websockets
import ujson as json
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logger = logging.getLogger("OKXIngester")
STREAM_NAME = "raw_market_stream"
SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]

async def process_okx_message(redis_client, message):
    try:
        data = json.loads(message)
        if 'data' not in data or 'arg' not in data:
            return

        channel = data['arg']['channel']
        instId = data['arg'].get('instId', '')
        
        symbol = instId.replace("-SWAP", "").replace("-", "")
        
        if not symbol and len(data['data']) > 0 and 'instId' in data['data'][0]:
            instId = data['data'][0]['instId']
            symbol = instId.replace("-SWAP", "").replace("-", "")

        if symbol not in ["BTCUSDT", "ETHUSDT"]:
            return

        multiplier = 0.01 if "BTC" in symbol else 0.1

        if channel == "trades":
            for trade in data['data']:
                price = float(trade['px'])
                qty = float(trade['sz']) * multiplier
                usd_value = price * qty
                
                if usd_value < 1000:
                    continue

                side = "BUY" if trade['side'] == "buy" else "SELL"
                
                await redis_client.xadd(STREAM_NAME, {
                    "exchange": "okx",
                    "symbol": symbol,
                    "type": "trade",
                    "side": side,
                    "price": price,
                    "usd_value": usd_value,
                    "timestamp": trade.get('ts', data.get('ts'))
                }, maxlen=100000, approximate=True)

        elif channel == "liquidation-orders":
            for liq in data['data']:
                liq_instId = liq.get('instId', '')
                if liq_instId not in SYMBOLS:
                    continue
                    
                liq_symbol = liq_instId.replace("-SWAP", "").replace("-", "")
                
                details = liq.get('details', [])
                for detail in details:
                    price = float(detail['bkPx'])
                    qty = float(detail['sz']) * multiplier
                    usd_value = price * qty
                    
                    side = "SHORT_LIQ" if detail['side'] == "buy" else "LONG_LIQ"
                    
                    await redis_client.xadd(STREAM_NAME, {
                        "exchange": "okx",
                        "symbol": liq_symbol,
                        "type": "liquidation",
                        "side": side,
                        "price": price,
                        "usd_value": usd_value,
                        "timestamp": detail.get('ts', liq.get('ts'))
                    }, maxlen=100000, approximate=True)
                    logger.info(f"OKX LIQ: {liq_symbol} {side} for ${usd_value:,.0f}")

        elif channel == "open-interest":
            for item in data['data']:
                if 'oiCcy' in item:
                    oi_coins = float(item['oiCcy'])
                    await redis_client.hset(f"live_metrics:{symbol}", "okx_open_interest", oi_coins)

        elif channel == "funding-rate":
            for item in data['data']:
                if 'fundingRate' in item:
                    fr = float(item['fundingRate']) * 100
                    await redis_client.hset(f"live_metrics:{symbol}", "okx_funding_rate", fr)

        elif channel == "books":
            for item in data['data']:
                bids = [(float(p), float(q) * multiplier) for p, q, *_ in item.get('bids', []) if p and q]
                asks = [(float(p), float(q) * multiplier) for p, q, *_ in item.get('asks', []) if p and q]
                
                if bids or asks:
                    bids_usd = sum(p * q for p, q in bids)
                    asks_usd = sum(p * q for p, q in asks)
                    imbalance_usd = bids_usd - asks_usd

                    updates = {}
                    if bids_usd > 0: updates["okx_ob_bids_usd"] = bids_usd
                    if asks_usd > 0: updates["okx_ob_asks_usd"] = asks_usd
                    if imbalance_usd != 0: updates["okx_ob_imbalance_usd"] = imbalance_usd
                    
                    if updates:
                        await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

    except Exception as e:
        logger.error(f"⚠️  {e}")

async def okx_ws_loop():
    redis_client = await redis_manager.connect()
    url = "wss://ws.okx.com:8443/ws/v5/public"
    
    args = []
    for sym in SYMBOLS:
        args.extend([
            {"channel": "trades", "instId": sym},
            {"channel": "open-interest", "instId": sym},
            {"channel": "funding-rate", "instId": sym},
            {"channel": "books", "instId": sym}
        ])
    args.append({"channel": "liquidation-orders", "instType": "SWAP"})
    
    sub_msg = json.dumps({
        "op": "subscribe",
        "args": args
    })

    while True:
        try:
            logger.info("Connecting to OKX WS...")
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=10**7) as ws:
                await ws.send(sub_msg)
                logger.info("✅ OKX Subscribed to Trades, Liq, OI, Funding, Books")
                async for message in ws:
                    await process_okx_message(redis_client, message)
        except Exception as e:
            logger.error(f"❌ OKX Error: {e}. Reconnecting...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(okx_ws_loop())