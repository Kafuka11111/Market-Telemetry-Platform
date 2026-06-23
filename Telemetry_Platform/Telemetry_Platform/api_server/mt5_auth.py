import hashlib
import time
import logging
from fastapi import Request, HTTPException

logger = logging.getLogger("MT5_Auth")

async def verify_smart_key(redis_client, api_key: str, request: Request) -> bool:
    client_ip = request.client.host if request.client else "unknown"

    
    rate_limit_key = f"rate_limit:{client_ip}"
    requests_this_second = await redis_client.incr(rate_limit_key)
    if requests_this_second == 1:
        await redis_client.expire(rate_limit_key, 1) 
        
    if requests_this_second > 50:
        raise HTTPException(status_code=429, detail="Too Many Requests")

    
    if api_key in ["MT5DEMO", "MT5_SECURE"]:
        mt5_account = request.headers.get("X-MT5-Account")
        client_hash = request.headers.get("X-Quantum-Hash")
        
        if not mt5_account or not client_hash:
            raise HTTPException(status_code=401, detail="Missing MT5 headers")

        
        SECRET_SALT = "QuantumTerminalPro_SuperSecret_2026!"
        expected_hash = hashlib.sha256(f"{mt5_account}{SECRET_SALT}".encode('utf-8')).hexdigest()
        
        if client_hash != expected_hash:
            logger.warning(f"⚠️ Hack! IP: {client_ip}. expected: {expected_hash[:10]}")
            raise HTTPException(status_code=403, detail="Invalid Signature")

        
        if api_key == "MT5DEMO":
            current_time = time.time()
            demo_acc_key = f"mt5_demo_acc:{mt5_account}"
            
            active_until = await redis_client.get(demo_acc_key)
            
            if not active_until:
                
                if await redis_client.exists(f"demo_ip_used:{client_ip}"):
                    raise HTTPException(status_code=403, detail="DEMO_LIMIT_REACHED")
                
                
                expire_time = current_time + 3600
                await redis_client.set(demo_acc_key, str(expire_time))
                await redis_client.setex(f"demo_ip_used:{client_ip}", 2592000, "used") # Блок IP на 30 дней
                logger.info(f"DEMO MT: {mt5_account}, IP: {client_ip}")
                return True
            else:
                
                if current_time > float(active_until):
                    raise HTTPException(status_code=403, detail="DEMO_EXPIRED")
                return True

        
        elif api_key == "MT5_SECURE":
            
            await redis_client.sadd("active_mt5_pro_users", mt5_account)
            return True

    
    return False