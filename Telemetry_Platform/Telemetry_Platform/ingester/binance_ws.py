import time
import asyncio
import websockets
import ujson as json
import logging
import sys
import os
import urllib.request

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BinanceIngester")

STREAM_NAME = "raw_market_stream"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

local_ob = {sym: {"bids": {}, "asks": {}} for sym in SYMBOLS}
RANGES = {
    "BTCUSDT": {"close": (0, 500), "mid": (500, 1000), "macro": (1000, 5000)},
    "ETHUSDT": {"close": (0, 20), "mid": (20, 100), "macro": (100, 250)}
}

MIN_ORDER_USD = 25000
last_pressure_update = {"BTCUSDT": 0, "ETHUSDT": 0}

async def fetch_open_interest_rest(redis_client):
    
    url = "https://fapi.binance.com/fapi/v1/openInterest"
    loop = asyncio.get_event_loop()
    
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    logger.info("Open Interest (REST)")
    while True:
        try:
            for symbol in SYMBOLS:
                req_url = f"{url}?symbol={symbol.upper()}"
                def fetch():
                    req = urllib.request.Request(req_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=10) as response:
                        raw_data = response.read().decode()
                        try:
                            return json.loads(raw_data)
                        except ValueError:
                            
                            logger.error(f"❌ JSON (OI). {raw_data[:200]}...")
                            return None
                            
                data = await loop.run_in_executor(None, fetch)
                if data and 'openInterest' in data:
                    oi_value = float(data['openInterest'])
                    await redis_client.hset(f"live_metrics:{symbol.upper()}", "open_interest", oi_value)
                    
                    
        except Exception as e:
            logger.error(f"❌ REST OI: {e}")
            
        await asyncio.sleep(30) 

async def maintain_local_orderbook(redis_client):
    
    url = "https://fapi.binance.com/fapi/v1/depth"
    loop = asyncio.get_event_loop()
    last_sync = 0
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    
    
    while True:
        try:
            now = time.time()
            if now - last_sync > 300: 
                for symbol in SYMBOLS:
                    
                    req_url = f"{url}?symbol={symbol.upper()}&limit=1000"
                    def fetch():
                        req = urllib.request.Request(req_url, headers=headers)
                        with urllib.request.urlopen(req, timeout=10) as response:
                            raw_data = response.read().decode()
                            try:
                                return json.loads(raw_data)
                            except ValueError:
                                logger.error(f"❌ JSON (Depth). {raw_data[:200]}...")
                                return None
                                
                    data = await loop.run_in_executor(None, fetch)
                    if data and 'bids' in data and 'asks' in data:
                        local_ob[symbol]["bids"] = {float(p): float(q) for p, q in data['bids'] if float(p)*float(q) >= MIN_ORDER_USD}
                        local_ob[symbol]["asks"] = {float(p): float(q) for p, q in data['asks'] if float(p)*float(q) >= MIN_ORDER_USD}
                        logger.info(f"✅  {symbol} ")
                last_sync = now

            for symbol in SYMBOLS:
                bids, asks = local_ob[symbol]["bids"], local_ob[symbol]["asks"]
                if not bids or not asks: continue
                
                top_bid, top_ask = max(bids.keys()), min(asks.keys())
                current_price = (top_bid + top_ask) / 2
                
                zones = RANGES.get(symbol, {"close": 100, "mid": 500, "macro": 2000})
                mapping = {}

                for zone_name, (min_dist, max_dist) in zones.items():
                    max_bid_p, max_bid_v = 0, 0
                    for p, q in list(bids.items()):
                        dist = current_price - p
                        if min_dist <= dist <= max_dist:
                            v = p * q
                            if v > max_bid_v: max_bid_v, max_bid_p = v, p

                    max_ask_p, max_ask_v = 0, 0
                    for p, q in list(asks.items()):
                        dist = p - current_price
                        if min_dist <= dist <= max_dist:
                            v = p * q
                            if v > max_ask_v: max_ask_v, max_ask_p = v, p

                    mapping.update({
                        f"whale_{zone_name}_bid_price": max_bid_p,
                        f"whale_{zone_name}_bid_vol": max_bid_v,
                        f"whale_{zone_name}_ask_price": max_ask_p,
                        f"whale_{zone_name}_ask_vol": max_ask_v
                    })
                
                if mapping:
                    await redis_client.hset(f"live_metrics:{symbol}", mapping=mapping)
                    
        except Exception as e:
            logger.error(f"❌ {e}")
            
        await asyncio.sleep(2)

async def process_binance_message(redis_client, message):
    try:
        data = json.loads(message)
        if 'stream' not in data: return
        stream, data = data['stream'], data['data']
        event_type = data.get('e')
        
        timestamp = data.get('E', int(time.time() * 1000))
        
        
        if event_type == 'aggTrade':
            symbol = data.get('s', '').upper()
            price, qty = float(data['p']), float(data['q'])
            usd_value = price * qty
            side = "SELL" if data.get('m') else "BUY"
            
            

            await redis_client.hset(f"live_metrics:{symbol}", mapping={"binance_price": price})

            if usd_value >= 1000:
                await redis_client.xadd(STREAM_NAME, {
                    "exchange": "binance", 
                    "symbol": symbol, 
                    "type": "trade", 
                    "side": side, 
                    "price": price, 
                    "usd_value": usd_value, 
                    "timestamp": timestamp
                }, maxlen=100000)

        elif event_type == 'forceOrder':
            o = data.get('o', {})
            symbol = o.get('s', '').upper()
            price, qty = float(o.get('p', 0)), float(o.get('q', 0))
            usd_value = price * qty
            side = "SHORT_LIQ" if o.get('S') == "BUY" else "LONG_LIQ"
            
            await redis_client.xadd(STREAM_NAME, {
                "exchange": "binance", 
                "symbol": symbol, 
                "type": "liquidation", 
                "side": side, 
                "price": price, 
                "usd_value": usd_value, 
                "timestamp": timestamp
            }, maxlen=100000)
            

        elif event_type == 'markPriceUpdate':
            symbol = data.get('s', '').upper()
            r_str = data.get('r')
            funding_rate = float(r_str if r_str else 0) * 100
            
            await redis_client.hset(f"live_metrics:{symbol}", mapping={"funding_rate": funding_rate})
            logger.debug(f" {symbol} : {funding_rate}%") 

        elif event_type == 'depthUpdate':
            symbol = data.get('s', '').upper()
            bids, asks = local_ob[symbol]["bids"], local_ob[symbol]["asks"]
            
            for p_str, q_str in data.get('b', []):
                p, q = float(p_str), float(q_str)
                if q == 0 or (p * q < MIN_ORDER_USD): 
                    bids.pop(p, None)
                else: 
                    bids[p] = q
                    
            for p_str, q_str in data.get('a', []):
                p, q = float(p_str), float(q_str)
                if q == 0 or (p * q < MIN_ORDER_USD): 
                    asks.pop(p, None)
                else: 
                    asks[p] = q

            now = time.time()
            if now - last_pressure_update[symbol] > 1.0: 
                try:
                    bids_sorted = sorted(bids.keys(), reverse=True)[:50]
                    asks_sorted = sorted(asks.keys())[:50]
                    
                    bids_usd = sum(p * bids[p] for p in bids_sorted)
                    asks_usd = sum(p * asks[p] for p in asks_sorted)
                    imbalance = bids_usd - asks_usd

                    await redis_client.hset(f"live_metrics:{symbol}", mapping={
                        "ob_bids_usd": bids_usd,
                        "ob_asks_usd": asks_usd,
                        "ob_imbalance_usd": imbalance
                    })
                    last_pressure_update[symbol] = now
                    
                    imbalance_str = "BUYERS" if imbalance > 0 else "SELLERS"
                    
                    
                except Exception as e:
                    logger.error(f"❌ (Pressure Calc): {e}")

    except Exception as e: 
        logger.error(f"⚠️  WS: {e}")

async def binance_ws_loop():
    redis_client = await redis_manager.connect()
    oi_task = asyncio.create_task(fetch_open_interest_rest(redis_client))
    whale_task = asyncio.create_task(maintain_local_orderbook(redis_client))
    
    url = "wss://fstream.binancefuture.com/stream?streams="
    
    streams = []
    for sym in SYMBOLS:
        s = sym.lower()
        streams.extend([f"{s}@aggTrade", f"{s}@forceOrder", f"{s}@markPrice@1s", f"{s}@depth"])
    
    ws_url = url + "/".join(streams)
    
    while True:
        try:
            logger.info(f"Binance WebSocket: {ws_url}")
            async with websockets.connect(ws_url, ping_interval=None, max_size=10**7) as ws:
                logger.info("✅")
                
                while True:
                    message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    await process_binance_message(redis_client, message)
                    
        except asyncio.TimeoutError:
            logger.error("No data from Binance 30s...")
        except Exception as e:
            logger.error(f"❌ WS: {e}")
            
        
        await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        logger.info("Binance Ingester...")
        asyncio.run(binance_ws_loop())
    except KeyboardInterrupt:
        logger.info("🛑 Binance Ingester STOP")