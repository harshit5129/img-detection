import asyncio
import aiohttp
import time
from io import BytesIO
from PIL import Image
from typing import Optional, Dict
from config import RATE_LIMIT_DELAY, MAX_CONCURRENT_DOWNLOADS, DOWNLOAD_TIMEOUT, logger

async def auto_remove_reaction(message, emoji):
    await asyncio.sleep(3)
    try:
        await message.remove_reaction(emoji, message.guild.me)
    except:
        pass

class RateLimiter:
    def __init__(self, rate: float):
        self.rate = rate
        self.user_locks: Dict[int, float] = {}

    async def acquire(self, user_id: int = None):
        if user_id is None:
            user_id = 0
        now = time.time()
        if user_id in self.user_locks:
            diff = now - self.user_locks[user_id]
            if diff < self.rate:
                await asyncio.sleep(self.rate - diff)
        self.user_locks[user_id] = time.time()

rate_limiter = RateLimiter(RATE_LIMIT_DELAY)
download_sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

async def download_image(url: str) -> Optional[dict]:
    async with download_sem:
        try:
            timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(url) as r:
                    if r.status == 200:
                        data = await r.read()
                        size_mb = len(data) / (1024 * 1024)
                        try:
                            with Image.open(BytesIO(data)) as img:
                                w, h = img.size
                                fmt = img.format
                                return {
                                    'content': data, 'width': w, 'height': h,
                                    'format': fmt, 'size_mb': size_mb
                                }
                        except:
                            return {'content': data, 'width': 0, 'height': 0, 'format': 'UNKNOWN', 'size_mb': size_mb}
                    elif r.status == 429:
                        await asyncio.sleep(int(r.headers.get('Retry-After', 5)))
        except Exception as e:
            logger.error(f"Download error: {e}")
    return None

def generate_auto_tags(width: int, height: int, fmt: str, size_mb: float) -> list:
    tags = []
    if fmt:
        tags.append(f"format:{fmt.lower()}")
    if size_mb < 1:
        tags.append("size:small")
    elif size_mb > 5:
        tags.append("size:large")
    else:
        tags.append("size:medium")

    if width and height:
        if width == height:
            tags.append("orientation:square")
        elif width > height:
            tags.append("orientation:landscape")
        else:
            tags.append("orientation:portrait")

        mp = (width * height) / 1_000_000
        if mp > 8:
            tags.append("resolution:4k")
        elif mp > 2:
            tags.append("resolution:high")
        elif width > 1920 or height > 1080:
            tags.append("resolution:wallpaper")
        elif width < 256 and height < 256:
            tags.append("resolution:icon")

    return tags
