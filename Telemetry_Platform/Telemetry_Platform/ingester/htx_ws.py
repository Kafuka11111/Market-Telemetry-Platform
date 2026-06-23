import asyncio
import websockets
import ujson as json
import gzip
import logging
import time
import sys
import os
import aiohttp

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HTXIngester")

SYMBOLS = ["BTC-USDT", "ETH-USDT"]
MIN_TRADE_USD_FILTER = 50.0
MAX_TRADE_USD_FILTER = 900000000.0

async def fetch_htx_oi(redis_client):
    url_oi = "https://api.hbdm.com/linear-swap-api/v1/swap_open_interest"
    url_fund = "https://api.hbdm.com/linear-swap-api/v1/swap_funding_rate"
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                for sym in SYMBOLS:
                    redis_sym = sym.replace("-", "")
                    
                    async with session.get(f"{url_oi}?contract_code={sym}") as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            if data.get("status") == "ok" and "data" in data:
                                for item in data["data"]:
                                    oi_coins = float(item.get("amount", item.get("volume", 0)))
                                    await redis_client.hset(f"live_metrics:{redis_sym}", "htx_open_interest", oi_coins)
                                    
                    async with session.get(f"{url_fund}?contract_code={sym}") as f_resp:
                        if f_resp.status == 200:
                            f_data = await f_resp.json(content_type=None)
                            if f_data.get("status") == "ok" and "data" in f_data:
                                funding = float(f_data["data"].get("funding_rate", 0))
                                await redis_client.hset(f"live_metrics:{redis_sym}", "htx_funding_rate", funding)
                                
        except Exception as e:
            logger.error(f"❌ HTX REST Error: {e}")
        await asyncio.sleep(30)

async def htx_market_worker(redis_client):
    url = "wss://api.hbdm.com/linear-swap-ws"
    while True:
        try:
            async with websockets.connect(url, ping_interval=15, ping_timeout=15) as ws:
                logger.info("✅ HTX Market WS Connected (Trades & BBO)")
                for sym in SYMBOLS:
                    await ws.send(json.dumps({"sub": f"market.{sym}.trade.detail", "id": f"trade_{sym}"}))
                    await ws.send(json.dumps({"sub": f"market.{sym}.bbo", "id": f"bbo_{sym}"}))

                while True:
                    msg = await ws.recv()
                    data = json.loads(gzip.decompress(msg).decode('utf-8'))
                    
                    if "ping" in data:
                        await ws.send(json.dumps({"pong": data["ping"]}))
                        continue
                        
                    if "ch" in data and "tick" in data:
                        symbol_raw = data["ch"].split('.')[1]
                        symbol = symbol_raw.replace("-", "") 
                        timestamp = int(data.get("ts", time.time() * 1000))
                        
                        pipe = redis_client.pipeline()
                        
                        if "trade.detail" in data["ch"]:
                            last_price = 0  
                            
                            for trade in data["tick"]["data"]:
                                price = float(trade["price"])
                                last_price = price  
                                
                                if "trade_turnover" in trade:
                                    usd_value = float(trade["trade_turnover"])
                                elif "quantity" in trade:
                                    usd_value = float(trade["quantity"]) * price
                                else:
                                    amount_cont = float(trade["amount"])
                                    contract_size = 0.001 if "BTC" in symbol else 0.01
                                    usd_value = amount_cont * contract_size * price
                                
                                if usd_value < MIN_TRADE_USD_FILTER:
                                    continue
                                    
                                if usd_value > MAX_TRADE_USD_FILTER:
                                    continue

                                side = "BUY" if trade["direction"] == "buy" else "SELL"
                                pipe.xadd("raw_market_stream", {
                                    "exchange": "htx", "symbol": symbol, "type": "trade",
                                    "side": side, "price": price, "usd_value": usd_value, "timestamp": timestamp
                                }, id=b'*')

                            if last_price > 0:
                                await redis_client.hset(f"live_metrics:{symbol}", mapping={"htx_price": last_price})
                                

                        elif "bbo" in data["ch"]:
                            tick = data["tick"]
                            bid_sz = float(tick.get("bid", [0, 0])[1])
                            ask_sz = float(tick.get("ask", [0, 0])[1])
                            price = float(tick.get("bid", [0, 0])[0])
                            
                            contract_size = 0.001 if "BTC" in symbol else 0.01
                            usd_imbalance = (bid_sz - ask_sz) * contract_size * price
                            
                            pipe.xadd("raw_market_stream", {
                                "exchange": "htx", "symbol": symbol, "type": "book_imbalance",
                                "usd_value": usd_imbalance, "timestamp": timestamp
                            }, id=b'*')

                        await pipe.execute()
                        
        except Exception as e:
            logger.error(f"❌ HTX Market WS Error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def htx_notification_worker(redis_client):
    url = "wss://api.hbdm.com/linear-swap-notification"
    while True:
        try:
            async with websockets.connect(url, ping_interval=15, ping_timeout=15) as ws:
                logger.info("✅ HTX Notification WS Connected (Liquidations)")
                for sym in SYMBOLS:
                    await ws.send(json.dumps({"op": "sub", "topic": f"public.{sym}.liquidation_orders"}))

                while True:
                    msg = await ws.recv()
                    data = json.loads(gzip.decompress(msg).decode('utf-8'))
                    
                    if data.get("op") == "ping":
                        await ws.send(json.dumps({"op": "pong", "ts": data.get("ts")}))
                        continue
                    if "ping" in data:
                        await ws.send(json.dumps({"pong": data["ping"]}))
                        continue

                    if data.get("op") == "notify" and "liquidation_orders" in data.get("topic", ""):
                        symbol_raw = data["topic"].split('.')[1]
                        symbol = symbol_raw.replace("-", "") 
                        timestamp = int(data.get("ts", time.time() * 1000))
                        pipe = redis_client.pipeline()
                        
                        for liq in data.get("data", []):
                            price = float(liq["price"])
                            
                            if "trade_turnover" in liq:
                                usd_value = float(liq["trade_turnover"])
                            elif "quantity" in liq:
                                usd_value = float(liq["quantity"]) * price
                            else:
                                volume_raw = float(liq.get("volume", liq.get("amount", 0)))
                                contract_size = 0.001 if "BTC" in symbol else 0.01
                                usd_value = volume_raw * contract_size * price
                            
                            side = "SHORT_LIQ" if liq["direction"] == "buy" else "LONG_LIQ"
                            
                            pipe.xadd("raw_market_stream", {
                                "exchange": "htx", "symbol": symbol, "type": "liquidation",
                                "side": side, "price": price, "usd_value": usd_value, "timestamp": timestamp
                            }, id=b'*')
                            logger.info(f"HTX LIQ: {symbol} {side} for ${usd_value:,.0f}")
                            
                        await pipe.execute()
        except Exception as e:
            logger.error(f"❌ HTX Notification WS Error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def main():
    redis_client = await redis_manager.connect()
    logger.info("Starting HTX Multi-Threaded Ingester...")
    
    await asyncio.gather(
        fetch_htx_oi(redis_client),
        htx_market_worker(redis_client),
        htx_notification_worker(redis_client)
    )

if __name__ == "__main__":
    asyncio.run(main())