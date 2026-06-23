import asyncio
import websockets
import ujson as json
import logging
import time
import sys
import os
import aiohttp

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("GateIngester")

SYMBOLS = ["BTC_USDT", "ETH_USDT"]

MIN_TRADE_USD_FILTER = 50.0         
MAX_TRADE_USD_FILTER = 900_000_000.0  

async def fetch_gate_metrics(redis_client):
    url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                for sym in SYMBOLS:
                    async with session.get(f"{url}?contract={sym}") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, list) and len(data) > 0:
                                ticker = data[0]
                                
                                size_contracts = float(ticker.get("total_size", 0))
                                
                                multiplier = 0.0001 if "BTC" in sym else 0.01
                                raw_oi = size_contracts * multiplier
                                
                                funding = float(ticker.get("funding_rate", 0)) * 100
                                
                                redis_sym = sym.replace("_", "")
                                await redis_client.hset(f"live_metrics:{redis_sym}", mapping={
                                    "gate_open_interest": raw_oi,
                                    "gate_funding_rate": funding
                                })
        except Exception as e:
            logger.error(f"❌ Gate REST Error: {e}")
        await asyncio.sleep(30)

async def gate_ws_worker(redis_client):
    url = "wss://fx-ws.gateio.ws/v4/ws/usdt"
    while True:
        try:
            async with websockets.connect(url, ping_interval=15, ping_timeout=15) as ws:
                logger.info("✅ Gate.io WS Connected")
                
                await ws.send(json.dumps({
                    "time": int(time.time()), "channel": "futures.trades", 
                    "event": "subscribe", "payload": SYMBOLS
                }))
                
                await ws.send(json.dumps({
                    "time": int(time.time()), "channel": "futures.liquidates", 
                    "event": "subscribe", "payload": SYMBOLS
                }))

                await ws.send(json.dumps({
                    "time": int(time.time()), "channel": "futures.book_ticker", 
                    "event": "subscribe", "payload": SYMBOLS
                }))

                while True:
                    msg = await ws.recv()
                    
                    try:
                        data = json.loads(msg)
                    except ValueError:
                        continue 
                        
                    if not isinstance(data, dict):
                        continue
                    
                    if data.get("event") != "update":
                        continue
                        
                    channel = data.get("channel")
                    pipe = redis_client.pipeline()
                    
                    if channel == "futures.trades":
                        for trade in data.get("result", []):
                            symbol_raw = trade.get("contract", "")
                            symbol = symbol_raw.replace("_", "")
                            price = float(trade.get("price", 0))
                            
                            size_contracts_raw = float(trade.get("size", 0))
                            size_contracts = abs(size_contracts_raw) 
                            
                            multiplier = 0.0001 if "BTC" in symbol else 0.01
                            usd_value = size_contracts * multiplier * price
                            
                            if usd_value < MIN_TRADE_USD_FILTER or usd_value > MAX_TRADE_USD_FILTER:
                                continue
                                
                            side = "BUY" if size_contracts_raw > 0 else "SELL"
                            timestamp = int(trade.get("create_time_ms", time.time() * 1000))
                            
                            pipe.xadd("raw_market_stream", {
                                "exchange": "gate", "symbol": symbol, "type": "trade",
                                "side": side, "price": price, "usd_value": usd_value, "timestamp": timestamp
                            }, id=b'*')

                    elif channel == "futures.book_ticker":
                        book = data.get("result")
                        
                        if not isinstance(book, dict):
                            continue
                            
                        symbol = book.get("s", "").replace("_", "")
                        bid_sz = float(book.get("B", 0))
                        ask_sz = float(book.get("A", 0))
                        
                        imbalance_contracts = bid_sz - ask_sz
                        multiplier = 0.0001 if "BTC" in symbol else 0.01
                        
                        price = float(book.get("b", book.get("a", 0)))
                        usd_imbalance = imbalance_contracts * multiplier * price
                        
                        timestamp = int(book.get("t", time.time() * 1000))
                        pipe.xadd("raw_market_stream", {
                            "exchange": "gate", "symbol": symbol, "type": "book_imbalance",
                            "usd_value": usd_imbalance, "timestamp": timestamp
                        }, id=b'*')

                    elif channel == "futures.liquidates":
                        for liq in data.get("result", []):
                            symbol = liq.get("contract", "").replace("_", "")
                            price = float(liq.get("price", 0))
                            
                            size_contracts_raw = float(liq.get("size", 0))
                            size_contracts = abs(size_contracts_raw)
                            
                            multiplier = 0.0001 if "BTC" in symbol else 0.01
                            usd_value = size_contracts * multiplier * price
                            
                            side = "SHORT_LIQ" if size_contracts_raw > 0 else "LONG_LIQ"
                            timestamp = int(liq.get("time", time.time())) * 1000
                            
                            pipe.xadd("raw_market_stream", {
                                "exchange": "gate", "symbol": symbol, "type": "liquidation",
                                "side": side, "price": price, "usd_value": usd_value, "timestamp": timestamp
                            }, id=b'*')
                            logger.info(f"GATE LIQ: {symbol} {side} for ${usd_value:,.0f}")

                    await pipe.execute()

        except Exception as e:
            logger.error(f"❌ Gate WS Error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def main():
    redis_client = await redis_manager.connect()
    logger.info("🚀 Starting Gate.io Ingester (Secure Mode)...")
    await asyncio.gather(
        fetch_gate_metrics(redis_client),
        gate_ws_worker(redis_client)
    )

if __name__ == "__main__":
    asyncio.run(main())