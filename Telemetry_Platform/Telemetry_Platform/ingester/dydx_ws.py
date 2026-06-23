import asyncio
import websockets
import ujson as json
import logging
import sys
import os
import time
import urllib.request

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logger = logging.getLogger("dYdXIngester")
STREAM_NAME = "raw_market_stream"
SYMBOLS = ["BTC-USD", "ETH-USD"]

async def fetch_dydx_oi(redis_client):
    url = "https://indexer.dydx.trade/v4/perpetualMarkets"
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            def fetch():
                req = urllib.request.Request(
                    url, 
                    headers={
                        'Accept': 'application/json',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                    }
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read().decode())
                    
            data = await loop.run_in_executor(None, fetch)
            
            if "markets" in data:
                for sym_raw in ["BTC-USD", "ETH-USD"]:
                    if sym_raw in data["markets"]:
                        market = data["markets"][sym_raw]
                        oi = float(market.get("openInterest", 0))
                        
                        symbol = sym_raw.replace("-USD", "USDT")
                        updates = {"dydx_open_interest": oi}
                        
                        if "nextFundingRate" in market and market["nextFundingRate"] is not None:
                            updates["dydx_funding_rate"] = float(market["nextFundingRate"]) * 100
                            
                        await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)
                        logger.info(f"✅ dYdX OI {symbol} (REST): {oi:,.1f}")
                        
        except Exception as e:
            logger.error(f"❌ dYdX REST OI: {e}")
            
        await asyncio.sleep(15)

async def process_dydx_message(redis_client, message):
    try:
        data = json.loads(message)
        if data.get('type') != 'channel_data' or 'contents' not in data: return
        
        channel = data.get('channel')
        symbol_raw = data.get('id')
        if not symbol_raw: return
        symbol = symbol_raw.replace("-USD", "USDT") 

        contents = data['contents']

        if channel == 'v4_trades' and 'trades' in contents:
            for trade in contents['trades']:
                price = float(trade['price'])
                qty = float(trade['size'])
                usd_value = price * qty
                
                if usd_value < 1000: continue
                side = trade['side'] 
                
                trade_type = trade.get('type', '')
                is_liq = (trade_type == 'LIQUIDATION')
                
                ts = int(time.time() * 1000)
                
                if is_liq:
                    liq_side = "SHORT_LIQ" if side == "BUY" else "LONG_LIQ"
                    await redis_client.xadd(STREAM_NAME, {
                        "exchange": "dydx",
                        "symbol": symbol,
                        "type": "liquidation",
                        "side": liq_side,
                        "price": price,
                        "usd_value": usd_value,
                        "timestamp": ts
                    }, maxlen=100000, approximate=True)
                    logger.info(f"dYdX LIQ: {symbol} {liq_side} for ${usd_value:,.0f}")
                else:
                    await redis_client.xadd(STREAM_NAME, {
                        "exchange": "dydx",
                        "symbol": symbol,
                        "type": "trade",
                        "side": side,
                        "price": price,
                        "usd_value": usd_value,
                        "timestamp": ts
                    }, maxlen=100000, approximate=True)

        elif channel == 'v4_markets':
            markets = contents.get('markets', {})
            if symbol_raw in markets:
                m_data = markets[symbol_raw]
                updates = {}
                
                if 'openInterest' in m_data:
                    updates['dydx_open_interest'] = float(m_data['openInterest'])
                    logger.info(f"dYdX OI {symbol}: {updates['dydx_open_interest']:,.1f}")
                if 'nextFundingRate' in m_data:
                    updates['dydx_funding_rate'] = float(m_data['nextFundingRate']) * 100
                    
                if updates:
                    await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

        elif channel == 'v4_orderbook':
            bids = contents.get('bids', [])
            asks = contents.get('asks', [])
            
            bids_usd = sum(float(p) * float(s) for p, s in bids)
            asks_usd = sum(float(p) * float(s) for p, s in asks)
            imbalance_usd = bids_usd - asks_usd

            updates = {}
            if bids_usd > 0: updates["dydx_ob_bids_usd"] = bids_usd
            if asks_usd > 0: updates["dydx_ob_asks_usd"] = asks_usd
            if imbalance_usd != 0: updates["dydx_ob_imbalance_usd"] = imbalance_usd
            
            if updates:
                await redis_client.hset(f"live_metrics:{symbol}", mapping=updates)

    except Exception as e:
        logger.error(f"⚠️ {e}")

async def dydx_ws_loop():
    redis_client = await redis_manager.connect()
    asyncio.create_task(fetch_dydx_oi(redis_client))
    url = "wss://indexer.dydx.trade/v4/ws"
    
    sub_msgs = []
    for sym in SYMBOLS:
        sub_msgs.append(json.dumps({"type": "subscribe", "channel": "v4_trades", "id": sym}))
        sub_msgs.append(json.dumps({"type": "subscribe", "channel": "v4_markets", "id": sym}))
        sub_msgs.append(json.dumps({"type": "subscribe", "channel": "v4_orderbook", "id": sym}))

    while True:
        try:
            logger.info("Connecting to dYdX WS...")
            async with websockets.connect(url, ping_interval=20, max_size=10**7) as ws:
                for msg in sub_msgs:
                    await ws.send(msg)
                logger.info("dYdX Subscribed to Trades, Markets, Orderbook")
                async for message in ws:
                    await process_dydx_message(redis_client, message)
        except Exception as e:
            logger.error(f"❌ dYdX Error: {e}. Reconnecting...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(dydx_ws_loop())