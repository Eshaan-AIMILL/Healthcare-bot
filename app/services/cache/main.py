import json
import logging
from typing import Any, Optional
from fastapi import FastAPI
from pydantic import BaseModel
import redis.asyncio as aioredis

from app.config import settings
from app.services.cache.utils import get_domain_ttl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cache_service")

app = FastAPI(title="Response Cache Service", version="1.0.0")

# Redis Connection client
redis_client: Optional[aioredis.Redis] = None

class CacheGetRequest(BaseModel):
    cache_key: str

class CacheSetRequest(BaseModel):
    cache_key: str
    response: str
    domain: str
    ttl_override: Optional[int] = None

class CacheInvalidateRequest(BaseModel):
    pattern: Optional[str] = None
    domain: Optional[str] = None

# Metrics tracking
stats = {
    "hits": 0,
    "misses": 0,
    "sets": 0,
    "invalidations": 0
}

@app.on_event("startup")
async def startup_event():
    global redis_client
    logger.info(f"Connecting to Redis at {settings.redis_url}")
    try:
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        # Quick ping to test connection
        await redis_client.ping()
        logger.info("Connected to Redis successfully.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        redis_client = None

@app.on_event("shutdown")
async def shutdown_event():
    global redis_client
    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed.")

@app.get("/health")
async def health():
    if not redis_client:
        return {"status": "unhealthy", "redis": "disconnected"}
    try:
        await redis_client.ping()
        return {"status": "ok", "redis": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "redis": f"error: {str(e)}"}

@app.post("/cache/get")
async def cache_get(request: CacheGetRequest):
    if not redis_client:
        stats["misses"] += 1
        return {"hit": False, "response": None, "metadata": {"error": "Redis disconnected"}}
    
    try:
        val = await redis_client.get(request.cache_key)
        if val:
            stats["hits"] += 1
            try:
                data = json.loads(val)
                if isinstance(data, dict) and "response" in data:
                    return {
                        "hit": True,
                        "response": data["response"],
                        "metadata": data.get("metadata", {})
                    }
            except json.JSONDecodeError:
                pass
            return {"hit": True, "response": val, "metadata": {}}
        
        stats["misses"] += 1
        return {"hit": False, "response": None, "metadata": {}}
    except Exception as e:
        logger.error(f"Error reading from cache: {e}")
        stats["misses"] += 1
        return {"hit": False, "response": None, "metadata": {"error": str(e)}}

@app.post("/cache/set")
async def cache_set(request: CacheSetRequest):
    if not redis_client:
        return {"success": False, "error": "Redis disconnected"}
    
    try:
        ttl = request.ttl_override if request.ttl_override is not None else get_domain_ttl(request.domain)
        
        payload = {
            "response": request.response,
            "metadata": {
                "domain": request.domain,
                "ttl": ttl
            }
        }
        
        await redis_client.set(request.cache_key, json.dumps(payload), ex=ttl)
        stats["sets"] += 1
        return {"success": True, "ttl_applied": ttl}
    except Exception as e:
        logger.error(f"Error writing to cache: {e}")
        return {"success": False, "error": str(e)}

@app.post("/cache/invalidate")
async def cache_invalidate(request: CacheInvalidateRequest):
    if not redis_client:
        return {"success": False, "error": "Redis disconnected"}
        
    try:
        count = 0
        if request.pattern:
            keys = await redis_client.keys(request.pattern)
            if keys:
                count = await redis_client.delete(*keys)
        elif request.domain:
            keys = await redis_client.keys("*")
            keys_to_delete = []
            for k in keys:
                val = await redis_client.get(k)
                if val:
                    try:
                        data = json.loads(val)
                        if isinstance(data, dict) and data.get("metadata", {}).get("domain") == request.domain:
                            keys_to_delete.append(k)
                    except json.JSONDecodeError:
                        pass
            if keys_to_delete:
                count = await redis_client.delete(*keys_to_delete)
        else:
            await redis_client.flushdb()
            count = -1
            
        stats["invalidations"] += 1
        return {"success": True, "deleted_count": count}
    except Exception as e:
        logger.error(f"Error invalidating cache: {e}")
        return {"success": False, "error": str(e)}

@app.get("/cache/stats")
async def cache_stats():
    total_keys = 0
    used_memory = "unknown"
    if redis_client:
        try:
            total_keys = await redis_client.dbsize()
            info = await redis_client.info(section="memory")
            used_memory = info.get("used_memory_human", "unknown")
        except Exception as e:
            logger.error(f"Error fetching stats from Redis: {e}")
            
    return {
        "stats": stats,
        "redis": {
            "total_keys": total_keys,
            "used_memory": used_memory
        }
    }
