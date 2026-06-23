import os
import redis.asyncio as aioredis
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("RedisClient")

class RedisPool:
    def __init__(self):
        self.host = os.getenv("REDIS_HOST", "localhost")
        self.port = int(os.getenv("REDIS_PORT", 6379))
        self.db = 0
        self.pool = None

    async def connect(self):
        if not self.pool:
            self.pool = aioredis.ConnectionPool.from_url(
                f"redis://{self.host}:{self.port}/{self.db}",
                max_connections=50,
                decode_responses=True 
            )
            logger.info(f"✅ Connected to Redis at {self.host}:{self.port}/{self.db}")
        return aioredis.Redis(connection_pool=self.pool)

    async def close(self):
        
        if self.pool:
            await self.pool.disconnect()
            logger.info("❌ Redis connection closed.")


redis_manager = RedisPool()