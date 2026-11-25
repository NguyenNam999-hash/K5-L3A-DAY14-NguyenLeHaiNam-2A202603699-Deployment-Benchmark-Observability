"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set) → mọi worker / instance dùng chung và
    vẫn còn sau khi restart agent
  • Kiểm tra + ghi nằm trong 1 Lua script → nguyên tử, không bị race khi nhiều
    request đồng thời (threadpool, nhiều worker, nhiều instance)
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60

# KEYS[1]=bucket  ARGV: now, window, limit, member
# Trả về {allowed(0/1), score của phần tử cũ nhất}
_SLIDING_WINDOW = r.register_script("""
local bucket = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', bucket, 0, now - window)
if redis.call('ZCARD', bucket) >= limit then
  local oldest = redis.call('ZRANGE', bucket, 0, 0, 'WITHSCORES')
  return {0, oldest[2] or tostring(now)}
end
redis.call('ZADD', bucket, now, ARGV[4])
redis.call('EXPIRE', bucket, math.ceil(window) + 1)
return {1, tostring(now)}
""")


def check_rate_limit(key: str) -> None:
    # TODO: gợi ý dùng Redis sorted set: ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE
    # Không lưu API key thô trong Redis, chỉ lưu hash
    bucket = "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:32]
    now = time.time()
    limit = settings.rate_limit_per_minute
    allowed, oldest = _SLIDING_WINDOW(
        keys=[bucket], args=[now, WINDOW_SECONDS, limit, f"{now}-{uuid.uuid4().hex[:8]}"])
    if int(allowed) == 0:
        retry_after = max(1, math.ceil(float(oldest) + WINDOW_SECONDS - now))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s",
            headers={"Retry-After": str(retry_after)},
        )
