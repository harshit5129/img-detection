import aiohttp
import imagehash
from PIL import Image
from io import BytesIO
import asyncio
import logging

logger = logging.getLogger('DuplicateDetector')
download_sem = asyncio.Semaphore(3)

async def download_image(url, retries=3):
    async with download_sem:
        for attempt in range(retries):
            try:
                timeout = aiohttp.ClientTimeout(total=20)
                async with aiohttp.ClientSession(timeout=timeout) as s:
                    async with s.get(url) as r:
                        if r.status == 200:
                            cl = r.headers.get('Content-Length')
                            if cl and int(cl) > 10*1024*1024:
                                return None
                            
                            data = await r.read()
                            if len(data) > 10*1024*1024:
                                return None
                                
                            try:
                                with Image.open(BytesIO(data)) as img:
                                    return {
                                        'content': data,
                                        'width': img.size[0], 'height': img.size[1],
                                        'format': img.format,
                                        'size_mb': len(data)/(1024*1024),
                                        'megapixels': (img.size[0]*img.size[1])/1_000_000
                                    }
                            except:
                                return {'content': data, 'width': 0, 'height': 0, 
                                       'format': 'UNKNOWN', 'size_mb': len(data)/(1024*1024), 'megapixels': 0}
                        elif r.status == 429:
                            await asyncio.sleep(int(r.headers.get('Retry-After', 5)))
            except Exception as e:
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
        return None

def calculate_hash(image_bytes):  # Yeh function name exact match hona chahiye
    """Calculate perceptual hash - renamed to calculate_hash for consistency"""
    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        return imagehash.phash(img)
    except Exception as e:
        logger.error(f"Hash error: {e}")
        return None

async def find_similar_images(guild_id, new_hash, channel_id=None, threshold=5):
    try:
        from cache import hash_cache
        from database import db_pool
        
        similar = []
        checked = set()
        
        # Check cache
        for key in list(hash_cache.cache.keys()):
            if key.startswith(f"{guild_id}_"):
                try:
                    hash_str = key.split('_', 1)[1]
                    if hash_str in checked:
                        continue
                    checked.add(hash_str)
                    
                    stored = imagehash.hex_to_hash(hash_str)
                    diff = new_hash - stored
                    if diff <= threshold:
                        entries = await hash_cache.get(key)
                        if entries:
                            for entry in entries:
                                similar.append((stored, entry, diff))
                except:
                    continue
        
        # Check DB
        prefix = str(new_hash)[:4]
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT hash_value, message_id, channel_id, user_id, timestamp, image_url 
                FROM image_hashes WHERE guild_id = ? AND hash_value LIKE ?
            ''', (guild_id, f"{prefix}%")) as cur:
                async for row in cur:
                    try:
                        if row[0] in checked:
                            continue
                        checked.add(row[0])
                        stored = imagehash.hex_to_hash(row[0])
                        diff = new_hash - stored
                        if diff <= threshold:
                            entry = (row[1], row[2], row[3], row[4], row[5])
                            similar.append((stored, entry, diff))
                            
                            key = f"{guild_id}_{row[0]}"
                            cached = await hash_cache.get(key) or []
                            if entry not in cached:
                                cached.append(entry)
                                await hash_cache.put(key, cached)
                    except:
                        continue
        return similar
    except Exception as e:
        logger.error(f"Find error: {e}")
        return []
