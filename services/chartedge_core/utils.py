from __future__ import annotations
import time
import asyncio
from typing import Any

class TokenBucket:
    """
    Simple implementation of the Token Bucket algorithm for rate limiting.
    Specifically designed to handle the 10 orders/sec limit.
    """
    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_fill = time.monotonic()
        self._lock = asyncio.Lock()

    async def consume(self, tokens: int = 1):
        async with self._lock:
            now = time.monotonic()
            # Fill bucket based on elapsed time
            self.tokens = min(self.capacity, self.tokens + (now - self.last_fill) * self.rate)
            self.last_fill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            
            # If not enough tokens, wait
            wait_time = (tokens - self.tokens) / self.rate
            await asyncio.sleep(wait_time)
            
            # Refill after wait
            self.tokens = 0
            self.last_fill = time.monotonic()
            return True

# Initialize a global rate limiter for order execution
# 8 orders per second capacity, refilling at 8 tokens/sec (Institutional Limit)
order_rate_limiter = TokenBucket(rate=8.0, capacity=8.0)
