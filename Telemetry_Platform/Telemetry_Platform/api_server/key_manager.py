# api_server/key_manager.py
import asyncio
import uuid
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.redis_client import redis_manager

async def create_key(client_name: str):
    redis_client = await redis_manager.connect()
    
    
    new_api_key = f"TRADER-{uuid.uuid4().hex[:16].upper()}"
    
    
    await redis_client.hset("valid_api_keys", new_api_key, client_name)
    
    print(f"✅ A key has been created for the client: {client_name}")
    print(f"🔑 API KEY: {new_api_key}")
    
    await redis_manager.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python key_manager.py <Name>")
    else:
        asyncio.run(create_key(sys.argv[1]))