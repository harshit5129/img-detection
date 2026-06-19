import asyncio
import aiohttp
import time
import re
import os
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

def generate_auto_tags(width: int, height: int, fmt: str, size_mb: float, filename: str = None, message_content: str = None) -> list:
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

    # Extract metadata tags from filename
    if filename:
        name = os.path.splitext(filename)[0]
        # Split on underscores and spaces first
        parts = re.split(r'[_\s]+', name)
        extracted = set()

        GENERIC_WORDS = {
            'img', 'image', 'pic', 'photo', 'screen', 'shot', 'screenshot',
            'output', 'untitled', 'draft', 'art', 'artwork', 'drawing',
            'sketch', 'render', 'final', 'version', 'edit', 'copy', 'file',
            'download', 'export', 'capture', 'clip', 'snap', 'frame',
        }

        for part in parts:
            part = part.strip()
            if not part or len(part) <= 1 or part.isdigit():
                continue
            lower = part.lower()
            if lower in GENERIC_WORDS:
                continue

            # If hyphenated, keep the full compound name (e.g. Jin-Woo)
            if '-' in part:
                full_name = re.sub(r'[^\w\-]', '', lower)
                if len(full_name) >= 3:
                    extracted.add(f"char:{full_name}")

            # Split CamelCase (BlueFlowers -> blue, flowers)
            words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)', part)
            if not words:
                words = [lower]

            for w in words:
                w = w.lower().strip()
                if len(w) >= 3 and w not in GENERIC_WORDS:
                    extracted.add(f"char:{w}")

        tags.extend(sorted(extracted))

    # Extract metadata tags from message content
    if message_content:
        content = message_content.strip()
        # char:Name, series:Name, artist:Name, genre:Name patterns
        prefix_re = re.compile(r'\b(char|series|artist|genre):(\S+)', re.IGNORECASE)
        for prefix, value in prefix_re.findall(content):
            tag = f"{prefix.lower()}:{value.lower().strip(',.;:')}"
            if tag not in tags:
                tags.append(tag)

    return tags
