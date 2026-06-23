import asyncio
import websockets
import ujson as json
import logging
import sys
import os
import urllib.request

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logger = logging.getLogger("DeribitIngester")
STREAM_NAME = "raw_market_stream"
SYMBOLS = ["BTC-PERPETUAL", "ETH-PERPETUAL"]

async def fetch_deribit_oi(redis_client):
    url = "https://www.deribit.com/api/v2/public/ticker?instrument_name="
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            for sym in SYMBOLS:
                req_url = f"{url}{sym}"
                def fetch():
                    req = urllib.request.Request(
                        req_url, 
                        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    )
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.loads(response.read().decode())
                        
                data = await loop.run_in_executor(None, fetch)
                
                if "result" in data:
                    res = data["result"]
                    contracts = float(res.get("open_interest", 0))
                    price = float(res.get("mark_price", 1))
                    
                    
                    oi_coins = (contracts * multiplier) / price
                    
                    symbol_usdt = sym.replace("-PERPETUAL", "USDT")
                    await redis_client.hset(f"live_metrics:{symbol_usdt}", "deribit_open_interest", oi_coins)
                    
        except Exception as e:
            pass
            
        await asyncio.sleep(15)

async def process_deribit_message(redis_client, message):
    try:
        data = json.loads(message)
        
        if 'error' in data:
            logger.error(f"❌ DERIBIT REFUSED CONNECTION: {data['error']}")
            return
        if 'params' not in data or 'data' not in data['params']: return

        channel = data['params']['channel']
        payload = data['params']['data']

        if channel.startswith("trades"):
            for trade in payload:
                usd_value = float(trade['amount'])
                price = float(trade['price'])
                
                coin_base = trade['instrument_name'].split('-')[0]
                symbol = f"{coin_base}USDT"
                
                side = "BUY" if trade['direction'] == "buy" else "SELL"
                is_liq = trade.get('liquidation')
                
                if is_liq:
                    liq_side = "SHORT_LIQ" if side == "BUY" else "LONG_LIQ"
                    await redis_client.xadd(STREAM_NAME, {
                        "exchange": "deribit", "symbol": symbol, "type": "liquidation",
                        "side": liq_side, "price": price, "usd_value": usd_value,
                        "timestamp": trade['timestamp']
                    }, maxlen=100000, approximate=True)
                    logger.info(f"DERIBIT LIQ: {symbol} {liq_side} for ${usd_value:,.0f}")
                else:
                    if usd_value >= 1000:
                        await redis_client.xadd(STREAM_NAME, {
                            "exchange": "deribit", "symbol": symbol, "type": "trade",
                            "side": side, "price": price, "usd_value": usd_value,
                            "timestamp": trade['timestamp']
                        }, maxlen=100000, approximate=True)

        elif channel.startswith("ticker"):
            coin_base = channel.split('.')[1].split('-')[0]
            symbol = f"{coin_base}USDT"
            
            price = float(payload.get('mark_price', payload.get('index_price', 0)))
            raw_oi = float(payload.get('open_interest', 0))
            funding = float(payload.get('current_funding', 0)) * 100
            
            updates = {}
            if price > 0 and raw_oi > 0:
                updates["deribit_open_interest"] = raw_oi / price
                
            if funding != 0:
                updates["deribit_funding_rate"] = funding
                
            if updates:
                await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

        elif channel.startswith("book"):
            coin_base = payload['instrument_name'].split('-')[0]
            symbol = f"{coin_base}USDT"
            
            bids = payload.get('bids', [])
            asks = payload.get('asks', [])
            
            bids_usd = sum(float(amount) for price, amount in bids)
            asks_usd = sum(float(amount) for price, amount in asks)
            imbalance_usd = bids_usd - asks_usd

            updates = {}
            if bids_usd > 0: updates["deribit_ob_bids_usd"] = bids_usd
            if asks_usd > 0: updates["deribit_ob_asks_usd"] = asks_usd
            if imbalance_usd != 0: updates["deribit_ob_imbalance_usd"] = imbalance_usd
            
            if updates:
                await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

    except Exception as e:
        logger.error(f"⚠️  {e}")

async def deribit_ws_loop():
    redis_client = await redis_manager.connect()
    asyncio.create_task(fetch_deribit_oi(redis_client))
    url = "wss://www.deribit.com/ws/api/v2"
    
    channels = []
    for sym in SYMBOLS:
        channels.extend([
            f"trades.{sym}.100ms",
            f"ticker.{sym}.100ms",
            f"book.{sym}.none.20.100ms"
        ])
        
    sub_msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "public/subscribe",
        "params": {"channels": channels}
    })

    while True:
        try:
            logger.info("Connecting to Deribit WS...")
            async with websockets.connect(url, ping_interval=15, ping_timeout=15, max_size=10**7) as ws:
                await ws.send(sub_msg)
                logger.info("✅ Deribit Subscribed to Trades, Ticker, Book")
                async for message in ws:
                    await process_deribit_message(redis_client, message)
        except Exception as e:
            logger.error(f"❌ Deribit Error: {e}. Reconnecting...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(deribit_ws_loop())