import asyncio
from collections import OrderedDict
from typing import Optional, List

class LRUCache:
    def __init__(self, capacity: int):
        self.cache = OrderedDict()
        self.capacity = capacity
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[List]:
        async with self._lock:
            if key not in self.cache:
                return None
            self.cache.move_to_end(key)
            return self.cache[key]

    async def put(self, key: str, value: List):
        async with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.capacity:
                self.cache.popitem(last=False)

    async def clear_guild(self, guild_id: int):
        async with self._lock:
            keys_to_remove = [k for k in self.cache.keys() if k.startswith(f"{guild_id}_")]
            for key in keys_to_remove:
                del self.cache[key]

    def size(self):
        return len(self.cache)

from config import MAX_CACHE_SIZE
hash_cache = LRUCache(MAX_CACHE_SIZE)
