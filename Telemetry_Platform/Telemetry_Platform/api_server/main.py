from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from api_server.mt5_auth import verify_smart_key
import asyncio
import ujson as json
import logging
import sys
import os
import time
from collections import deque

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("APIServer")

app = FastAPI(title="Quantum Radar API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async def is_key_valid(redis_client, api_key: str) -> bool:
    
    if not api_key:
        return False
        
    
    if api_key.startswith("DEMO-"):
        
        if await redis_client.exists(f"demo_active:{api_key}"):
            return True
        
        if await redis_client.sismember("demo_keys_pending", api_key):
            await redis_client.srem("demo_keys_pending", api_key)
            await redis_client.setex(f"demo_active:{api_key}", 3600, "active")
            logger.info(f"⏳ Demo-Key {api_key} 1 Hour")
            return True
        return False
        
    
    key_value = await redis_client.hget("valid_api_keys", api_key)
    
    
    if not key_value:
        return False

    
    if key_value == "sub_31d":
        expiration_ts = time.time() + (31 * 86400)
        await redis_client.hset("valid_api_keys", api_key, str(expiration_ts))
        logger.info(f"Key {api_key} 31 Days")
        return True
        
    if key_value == "sub_367d":
        expiration_ts = time.time() + (367 * 86400)
        await redis_client.hset("valid_api_keys", api_key, str(expiration_ts))
        logger.info(f"Key {api_key} 367 Days")
        return True
    
    if key_value in ["active", "admin_generated"] or not key_value.replace('.', '', 1).isdigit():
        return True 
        
    
    try:
        expiration_ts = float(key_value)
        if time.time() < expiration_ts:
            return True 
        else:
            return False 
    except ValueError:
        
        return True
async def get_subscription_info(redis_client, api_key: str) -> dict:
    
    if not api_key:
        return None
        
    
    if api_key.startswith("DEMO-"):
        ttl = await redis_client.ttl(f"demo_active:{api_key}")
        if ttl > 0:
            mins = ttl // 60
            return {"type": "demo", "timeLeft": f"{mins} min"}
        return None
        
    
    key_value = await redis_client.hget("valid_api_keys", api_key)
    if not key_value:
        return None

    
    if key_value == "sub_31d":
        return {"type": "subscription", "timeLeft": "31 days", "isExpiringSoon": False}
    if key_value == "sub_367d":
        return {"type": "subscription", "timeLeft": "367 days", "isExpiringSoon": False}
    
    if key_value in ["active", "admin_generated"] or not key_value.replace('.', '', 1).isdigit():
        return {"type": "lifetime"}
        
    
    try:
        exp_ts = float(key_value)
        rem_seconds = exp_ts - time.time()
        
        if rem_seconds > 0:
            days = int(rem_seconds // 86400)
            if days > 0:
                time_left = f"{days} days"
            else:
                hours = int(rem_seconds // 3600)
                time_left = f"{hours} hours"
                
            return {
                "type": "subscription",
                "timeLeft": time_left,
                "isExpiringSoon": days <= 3 
            }
    except ValueError:
        return {"type": "lifetime"}
        
    return None

MAX_IPS_PER_KEY = 2
MAX_DEVICES_PER_IP = 5

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, dict[str, list[WebSocket]]] = {}

    async def connect(self, websocket: WebSocket, api_key: str) -> bool:
        redis_client = await redis_manager.connect()
        try:
            
            if api_key.startswith("MT5"):
                if not await verify_smart_key(redis_client, api_key, websocket):
                    await websocket.close(code=1008, reason="Invalid MT5 Key")
                    return False
            
            else:
                if not await is_key_valid(redis_client, api_key):
                    await websocket.close(code=1008, reason="Invalid Web Key")
                    return False
                    
        except HTTPException as e:
            await websocket.close(code=1008, reason=str(e.detail))
            return False
            
        client_ip = websocket.client.host if websocket.client else "unknown_ip"
        if api_key not in self.active_connections:
            self.active_connections[api_key] = {}
        user_sessions = self.active_connections[api_key]
        if client_ip not in user_sessions and len(user_sessions) >= MAX_IPS_PER_KEY:
            oldest_ip = list(user_sessions.keys())[0]
            for old_ws in user_sessions[oldest_ip]:
                try: await old_ws.close(code=1008, reason="IP evicted")
                except Exception: pass
            del user_sessions[oldest_ip]
        if client_ip not in user_sessions:
            user_sessions[client_ip] = []
        if len(user_sessions[client_ip]) >= MAX_DEVICES_PER_IP:
            await websocket.close(code=1008, reason="Max devices per IP limit reached")
            return False
        await websocket.accept()
        user_sessions[client_ip].append(websocket)
        return True

    def disconnect(self, websocket: WebSocket, api_key: str):
        client_ip = websocket.client.host if websocket.client else "unknown_ip"
        if api_key in self.active_connections and client_ip in self.active_connections[api_key]:
            if websocket in self.active_connections[api_key][client_ip]:
                self.active_connections[api_key][client_ip].remove(websocket)
            if len(self.active_connections[api_key][client_ip]) == 0:
                del self.active_connections[api_key][client_ip]
            if len(self.active_connections[api_key]) == 0:
                del self.active_connections[api_key]

    async def broadcast(self, message: dict):
        if not self.active_connections: return
        tasks = []
        for api_key, ip_dict in list(self.active_connections.items()):
            for client_ip, sockets_list in list(ip_dict.items()):
                for ws in list(sockets_list):
                    async def send_to_client(w, k):
                        try: await w.send_json(message)
                        except Exception: self.disconnect(w, k)
                    tasks.append(send_to_client(ws, api_key))
        if tasks: await asyncio.gather(*tasks)

manager = ConnectionManager()

recent_whales = {"BTCUSDT": deque(maxlen=30), "ETHUSDT": deque(maxlen=30)}
recent_liqs = {"BTCUSDT": deque(maxlen=30), "ETHUSDT": deque(maxlen=30)}

async def listen_to_whales():
    redis_client = await redis_manager.connect()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("whale_tape")
    async for message in pubsub.listen():
        if message["type"] == "message":
            data = json.loads(message["data"])
            msg_type = data.get("type")
            symbol = data.get("symbol")
            usd_val = float(data.get("usd_value", 0))
            
            trade_ts = int(data.get("timestamp", time.time() * 1000)) // 1000
            data["ts"] = trade_ts
            
            if msg_type == "whale_trade" and usd_val >= 100000:
                merged = False
                for w in recent_whales[symbol]:
                    if w['side'] == data['side'] and abs(w['ts'] - trade_ts) <= 2:
                        w['usd_value'] += usd_val
                        merged = True
                        break
                if not merged:
                    recent_whales[symbol].append(data)
                    
            elif msg_type == "liquidation" and usd_val >= 30000:
                merged = False
                for w in recent_liqs[symbol]:
                    if w['side'] == data['side'] and abs(w['ts'] - trade_ts) <= 2:
                        w['usd_value'] += usd_val
                        merged = True
                        break
                if not merged:
                    recent_liqs[symbol].append(data)
                
            await manager.broadcast(data)

async def broadcast_market_data():
    redis_client = await redis_manager.connect()
    symbols = ["BTCUSDT", "ETHUSDT"]
    while True:
        if manager.active_connections:
            response = {}
            for symbol in symbols:
                hist_data = await redis_client.get(f"market:state:{symbol}")
                live_data_raw = await redis_client.hgetall(f"live_metrics:{symbol}")
                live_state = {"price": 0.0, "funding_rate": 0.0, "ob_imbalance_usd": 0.0, "ob_bids_usd": 0.0, "ob_asks_usd": 0.0}
                if live_data_raw: live_state.update({k: float(v) for k, v in live_data_raw.items()})
                
                
                vol_profile_raw = await redis_client.hgetall(f"vol_profile:{symbol}")
                vol_profile = {}
                if vol_profile_raw:
                    for k, v in vol_profile_raw.items():
                        try:
                            price_level, side = k.split(':') 
                            if price_level not in vol_profile:
                                vol_profile[price_level] = {"buy": 0.0, "sell": 0.0}
                            vol_profile[price_level][side] = float(v)
                        except Exception:
                            pass
                

                coin_key = symbol.replace("USDT", "")
                if hist_data:
                    parsed_data = json.loads(hist_data)
                    parsed_data["live"] = live_state
                    parsed_data["vol_profile"] = vol_profile 
                    response[coin_key] = parsed_data
                else: response[coin_key] = "Loading..."
            await manager.broadcast(response)
        await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(listen_to_whales())
    asyncio.create_task(broadcast_market_data())

@app.get("/")
async def root():
    try:
        with open("api_server/dashboard.html", "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    except Exception as e: return HTMLResponse(content=f"<h1>Ошибка</h1><p>{e}</p>")

@app.get("/api/history/{symbol}")
async def get_chart_history(symbol: str, api_key: str):
    redis_client = await redis_manager.connect()
    if not await is_key_valid(redis_client, api_key): 
        return {"error": "Invalid or Expired API Key"}
    history_data = await redis_client.lrange(f"chart:history:{symbol}", 0, -1)
    return [json.loads(item) for item in history_data]

@app.websocket("/ws/market_data")
async def websocket_endpoint(websocket: WebSocket, api_key: str = Query(...)):
    is_connected = await manager.connect(websocket, api_key)
    if not is_connected: return
    
    
    try:
        redis_client = await redis_manager.connect()
        sub_info = await get_subscription_info(redis_client, api_key)
        if sub_info:
            await websocket.send_json({"subscription_info": sub_info})
    except Exception as e:
        logger.error(f"Failed to send subscription info: {e}")
    
    
    try:
        while True: await websocket.receive_text()
    except Exception: manager.disconnect(websocket, api_key)

@app.get("/api/mt5/init/{symbol}")
async def get_mt5_init(request: Request, symbol: str, api_key: str):
    redis_client = await redis_manager.connect()
    
    if not await verify_smart_key(redis_client, api_key, request): 
        return {"error": "Invalid API Key"}
    symbol = symbol.upper()
    hist_data_raw = await redis_client.get(f"market:state:{symbol}")
    chart_history_raw = await redis_client.lrange(f"chart:history:{symbol}", -1000, -1)
    return {
        "symbol": symbol,
        "state": json.loads(hist_data_raw) if hist_data_raw else {},
        "chart": [json.loads(item) for item in chart_history_raw]
    }

@app.get("/api/mt5/live/{symbol}")
async def get_mt5_live(request: Request, symbol: str, api_key: str):
    redis_client = await redis_manager.connect()
    if not await verify_smart_key(redis_client, api_key, request): 
        return {"error": "Invalid API Key"}
    
    symbol = symbol.upper()
    live_data_raw = await redis_client.hgetall(f"live_metrics:{symbol}")
    chart_history = await redis_client.lrange(f"chart:history:{symbol}", -1440, -1)
    last_point_raw = chart_history[-1] if chart_history else None
    
    whales_list = recent_whales.get(symbol, [])
    whales_str = "|".join([f"{w['side']}:{w['price']}:{w['usd_value']}:{w.get('ts')}" for w in whales_list])
    
    liqs_list = recent_liqs.get(symbol, [])
    liqs_str = "|".join([f"{w['side']}:{w['price']}:{w['usd_value']}:{w.get('ts')}" for w in liqs_list])
    
    hist_data_raw = await redis_client.get(f"market:state:{symbol}")
    magnets_str = ""
    current_price = float(live_data_raw.get("price", 0)) if live_data_raw else 0
    
    if hist_data_raw:
        try:
            state = json.loads(hist_data_raw)
            heatmap = state.get("heatmap", {})
            longs = heatmap.get("longs", {})
            shorts = heatmap.get("shorts", {})
            
            noise_threshold = 20000000 if "BTC" in symbol else 10000000 
            
            valid_longs = {float(p): float(v) for p, v in longs.items() if float(p) < current_price and float(v) >= noise_threshold}
            valid_shorts = {float(p): float(v) for p, v in shorts.items() if float(p) > current_price and float(v) >= noise_threshold}
            
            def get_smart_hybrid_levels(valid_dict, curr_p):
                if not valid_dict: return []
                sorted_by_vol = sorted(valid_dict.items(), key=lambda x: x[1], reverse=True)
                if len(sorted_by_vol) <= 3: return sorted_by_vol
                max_vol = sorted_by_vol[0][1]
                
                major_pools = [item for item in sorted_by_vol if item[1] >= max_vol * 0.50]
                if not major_pools: major_pools = sorted_by_vol[:3]
                
                closest_local = sorted(major_pools, key=lambda x: abs(x[0] - curr_p))[0]
                remaining = [item for item in sorted_by_vol if item[0] != closest_local[0]]
                top_2_global = remaining[:2]
                return top_2_global + [closest_local]

            final_longs = get_smart_hybrid_levels(valid_longs, current_price)
            final_shorts = get_smart_hybrid_levels(valid_shorts, current_price)
            
            mag_list = []
            for p, v in final_longs: mag_list.append(f"LONG:{p}:{v}")
            for p, v in final_shorts: mag_list.append(f"SHORT:{p}:{v}")
            magnets_str = "|".join(mag_list)
        except Exception: pass

    poc_price = 0
    market_phase = "Neutral|NONE"
    
    if chart_history and current_price > 0:
        try:
            vol_profile = {}
            
            bucket_size = 20 if "BTC" in symbol else 1 
            
            for item in chart_history:
                c = json.loads(item)
                
                bucket = round(c['price'] / bucket_size) * bucket_size 
                vol_profile[bucket] = vol_profile.get(bucket, 0) + c.get('volume', 0)
                
            if vol_profile:
                poc_price = max(vol_profile, key=vol_profile.get)
            
            if len(chart_history) >= 60:
                past_1h = json.loads(chart_history[-60])['price']
                price_delta = current_price - past_1h
                cvd_delta = sum(json.loads(x).get('cvd', 0) for x in chart_history[-60:])
                
                if price_delta > 0 and cvd_delta > 0: market_phase = "Long Build-up|BUY"
                elif price_delta > 0 and cvd_delta <= 0: market_phase = "Short Squeeze|WARN"
                elif price_delta < 0 and cvd_delta < 0: market_phase = "Short Build-up|SELL"
                elif price_delta < 0 and cvd_delta >= 0: market_phase = "Absorption|BUY"
        except: pass

    return {
        "server_utc": int(time.time()), 
        "live": {k: float(v) for k, v in live_data_raw.items()} if live_data_raw else {},
        "state": json.loads(hist_data_raw) if hist_data_raw else {}, 
        "last_closed_minute": json.loads(last_point_raw) if last_point_raw else None,
        "whales_str": whales_str,
        "magnets_str": magnets_str,
        "liqs_str": liqs_str,
        "poc_price": poc_price,
        "market_phase": market_phase
    }

@app.get("/api/mt5/chart_csv/{symbol}", response_class=PlainTextResponse)
async def get_mt5_chart_csv(request: Request, symbol: str, api_key: str):
    redis_client = await redis_manager.connect()
    
    if not await verify_smart_key(redis_client, api_key, request): 
        return {"error": "Invalid API Key"}
    
    symbol = symbol.upper()
    chart_data = await redis_client.lrange(f"chart:history:{symbol}", -1440, -1)
    
    
    csv_lines = [f"#UTC_NOW:{int(time.time())}", "time,price,cvd,volume,liq_long,liq_short,open_interest,ema50,ema200,ema800,funding"]
    
    for item in chart_data:
        p = json.loads(item)
        oi = p.get('open_interest', 0)
        ema50 = p.get('ema50', 0)
        ema200 = p.get('ema200', 0)
        ema800 = p.get('ema800', 0)
        
        
        funding = p.get('funding_global', 0) 
        
        
        csv_lines.append(f"{p['time']},{p['price']},{p['cvd']},{p['volume']},{p['liq_long']},{p['liq_short']},{oi},{ema50},{ema200},{ema800},{funding}")
        
    return "\n".join(csv_lines)