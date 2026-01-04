import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
from io import BytesIO
from PIL import Image
import imagehash
import os
import json
import csv
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta
import asyncio
import sqlite3
import aiosqlite
import time
import math
from typing import Optional, List, Tuple, Dict, Set
import logging
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('DuplicateDetector')

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Configuration
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ADMIN_ROLE_NAME = os.getenv('ADMIN_ROLE_NAME', 'Admin')
HASH_THRESHOLD = int(os.getenv('HASH_THRESHOLD', '5'))
LOG_CHANNEL_NAME = os.getenv('LOG_CHANNEL_NAME', 'duplicate-logs')
AUTO_DELETE_DUPLICATES = os.getenv('AUTO_DELETE_DUPLICATES', 'false').lower() == 'true'

# Performance Configuration
MAX_CACHE_SIZE = 2000
DB_POOL_SIZE = 5
MAX_CONCURRENT_DOWNLOADS = 3
RATE_LIMIT_DELAY = 1.5
BATCH_COMMIT_SIZE = 50
MAX_RETRY_ATTEMPTS = 3
DOWNLOAD_TIMEOUT = 20

if not TOKEN:
    logger.error("DISCORD_BOT_TOKEN not found!")
    exit(1)

logger.info(f"Token loaded!")
logger.info(f"Threshold: {HASH_THRESHOLD}")
logger.info(f"Log Channel: {LOG_CHANNEL_NAME}")
logger.info(f"Auto-delete: {AUTO_DELETE_DUPLICATES}")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Data structures
guild_data: Dict[int, Dict] = {}
DATA_DIR = 'bot_data'
DB_FILE = os.path.join(DATA_DIR, 'bot_data.db')
os.makedirs(DATA_DIR, exist_ok=True)

# LRU Cache for image hashes
class LRUCache:
    def __init__(self, capacity: int):
        self.cache = OrderedDict()
        self.capacity = capacity

    def get(self, key: str) -> Optional[List]:
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key: str, value: List):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    def clear_guild(self, guild_id: int):
        keys_to_remove = [k for k in self.cache.keys() if k.startswith(f"{guild_id}_")]
        for key in keys_to_remove:
            del self.cache[key]

    def size(self):
        return len(self.cache)

hash_cache = LRUCache(MAX_CACHE_SIZE)

# Database connection pool
class DatabasePool:
    def __init__(self, db_path: str, pool_size: int):
        self.db_path = db_path
        self.pool_size = pool_size
        self.pool: List[aiosqlite.Connection] = []
        self.available: asyncio.Queue = asyncio.Queue()
        self.initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self):
        async with self._lock:
            if self.initialized:
                return

            conn = await aiosqlite.connect(self.db_path)
            await conn.execute('PRAGMA journal_mode=WAL')
            await conn.execute('PRAGMA synchronous=NORMAL')
            await conn.execute('PRAGMA cache_size=-64000')
            await conn.execute('PRAGMA temp_store=MEMORY')
            await conn.close()

            for _ in range(self.pool_size):
                conn = await aiosqlite.connect(self.db_path)
                conn.row_factory = aiosqlite.Row
                self.pool.append(conn)
                await self.available.put(conn)

            self.initialized = True
            logger.info(f"Database pool initialized with {self.pool_size} connections")

    @asynccontextmanager
    async def acquire(self):
        if not self.initialized:
            await self.initialize()

        conn = await self.available.get()
        try:
            yield conn
        finally:
            await self.available.put(conn)

    async def close_all(self):
        for conn in self.pool:
            await conn.close()
        self.pool.clear()
        logger.info("Database pool closed")

db_pool = DatabasePool(DB_FILE, DB_POOL_SIZE)

# Rate limiter
class RateLimiter:
    def __init__(self, rate: float):
        self.rate = rate
        self.last_call = 0

    async def acquire(self):
        now = time.time()
        time_since_last = now - self.last_call
        if time_since_last < self.rate:
            await asyncio.sleep(self.rate - time_since_last)
        self.last_call = time.time()

rate_limiter = RateLimiter(RATE_LIMIT_DELAY)
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

def init_db_sync():
    """Initialize SQLite database with optimized schema"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()

            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=NORMAL')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS image_hashes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    hash_value TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    UNIQUE(guild_id, hash_value, message_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_stats (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    unique_count INTEGER DEFAULT 0,
                    duplicate_count INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    whitelist TEXT DEFAULT '[]',
                    blacklist TEXT DEFAULT '[]',
                    user_whitelist TEXT DEFAULT '[]',
                    channel_thresholds TEXT DEFAULT '{}',
                    notification_settings TEXT DEFAULT '{}',
                    hash_threshold INTEGER DEFAULT 5,
                    auto_delete INTEGER DEFAULT 0,
                    remove_reactions INTEGER DEFAULT 0,
                    reaction_timeout INTEGER DEFAULT 10
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS duplicate_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    similarity_count INTEGER NOT NULL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scan_progress (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    last_message_id INTEGER,
                    total_processed INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, channel_id)
                )
            ''')

            cursor.execute('CREATE INDEX IF NOT EXISTS idx_guild_hash ON image_hashes(guild_id, hash_value)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_guild_message ON image_hashes(guild_id, message_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_guild_user_stats ON user_stats(guild_id, user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_duplicate_history_guild ON duplicate_history(guild_id, timestamp DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_scan_progress ON scan_progress(guild_id, channel_id)')

            conn.commit()
            logger.info("Database initialized with indexes")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

async def save_guild_data(guild_id: int) -> bool:
    """Save guild configuration to database"""
    try:
        config = guild_data.get(guild_id, {})
        if 'config' not in config:
            return True

        cfg = config['config']

        async with db_pool.acquire() as db:
            await db.execute('''
                INSERT OR REPLACE INTO guild_config 
                (guild_id, whitelist, blacklist, user_whitelist, channel_thresholds, 
                 notification_settings, hash_threshold, auto_delete, remove_reactions, reaction_timeout)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                guild_id,
                json.dumps(list(cfg.get('whitelist', set()))),
                json.dumps(list(cfg.get('blacklist', set()))),
                json.dumps(list(cfg.get('user_whitelist', set()))),
                json.dumps(cfg.get('channel_thresholds', {})),
                json.dumps(cfg.get('notification_settings', {})),
                cfg.get('hash_threshold', HASH_THRESHOLD),
                1 if cfg.get('auto_delete', AUTO_DELETE_DUPLICATES) else 0,
                1 if cfg.get('remove_reactions', False) else 0,
                cfg.get('reaction_timeout', 10)
            ))
            await db.commit()

        return True
    except Exception as e:
        logger.error(f"Save error for guild {guild_id}: {e}")
        return False

async def load_guild_data(guild_id: int) -> bool:
    """Load guild configuration from database"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT whitelist, blacklist, user_whitelist, channel_thresholds, 
                       notification_settings, hash_threshold, auto_delete, remove_reactions, reaction_timeout
                FROM guild_config WHERE guild_id = ?
            ''', (guild_id,)) as cursor:
                row = await cursor.fetchone()

                if row:
                    guild_data[guild_id] = {
                        'config': {
                            'whitelist': set(json.loads(row[0])) if row[0] else set(),
                            'blacklist': set(json.loads(row[1])) if row[1] else set(),
                            'user_whitelist': set(json.loads(row[2])) if row[2] else set(),
                            'channel_thresholds': json.loads(row[3]) if row[3] else {},
                            'notification_settings': json.loads(row[4]) if row[4] else {},
                            'hash_threshold': row[5] if row[5] else HASH_THRESHOLD,
                            'auto_delete': bool(row[6]) if row[6] is not None else AUTO_DELETE_DUPLICATES,
                            'remove_reactions': bool(row[7]) if row[7] is not None else False,
                            'reaction_timeout': row[8] if row[8] is not None else 10
                        }
                    }
                else:
                    guild_data[guild_id] = {
                        'config': {
                            'whitelist': set(),
                            'blacklist': set(),
                            'user_whitelist': set(),
                            'channel_thresholds': {},
                            'notification_settings': {},
                            'hash_threshold': HASH_THRESHOLD,
                            'auto_delete': AUTO_DELETE_DUPLICATES,
                            'remove_reactions': False,
                            'reaction_timeout': 10
                        }
                    }
        return True
    except Exception as e:
        logger.error(f"Load error for guild {guild_id}: {e}")
        return False

async def save_image_hash_batch(batch_data: List[Tuple]) -> bool:
    """Save multiple image hashes in a single transaction"""
    try:
        async with db_pool.acquire() as db:
            await db.executemany('''
                INSERT OR IGNORE INTO image_hashes 
                (guild_id, hash_value, message_id, channel_id, user_id, timestamp, image_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', batch_data)
            await db.commit()

        for data in batch_data:
            guild_id, hash_str, message_id, channel_id, user_id, timestamp, image_url = data
            hash_key = f"{guild_id}_{hash_str}"
            cached = hash_cache.get(hash_key)
            if cached is None:
                cached = []
            cached.append((message_id, channel_id, user_id, timestamp, image_url))
            hash_cache.put(hash_key, cached)

        return True
    except Exception as e:
        logger.error(f"Save image hash batch error: {e}")
        return False

async def save_image_hash(guild_id: int, img_hash, message_id: int, channel_id: int, 
                         user_id: int, timestamp: datetime, image_url: str) -> bool:
    """Save single image hash"""
    return await save_image_hash_batch([
        (guild_id, str(img_hash), message_id, channel_id, user_id, timestamp.isoformat(), image_url)
    ])

async def find_similar_images(guild_id: int, new_hash, channel_id: Optional[int] = None) -> List:
    """Find similar images with optimized caching and querying"""
    try:
        config = guild_data.get(guild_id, {}).get('config', {})
        channel_thresholds = config.get('channel_thresholds', {})
        threshold = channel_thresholds.get(str(channel_id), config.get('hash_threshold', HASH_THRESHOLD))

        similar = []
        checked_hashes = set()

        for hash_key in list(hash_cache.cache.keys()):
            if hash_key.startswith(f"{guild_id}_"):
                try:
                    stored_hash_str = hash_key.split('_', 1)[1]
                    if stored_hash_str in checked_hashes:
                        continue
                    checked_hashes.add(stored_hash_str)

                    stored_hash = imagehash.hex_to_hash(stored_hash_str)
                    hash_diff = new_hash - stored_hash

                    if hash_diff <= threshold:
                        entries = hash_cache.get(hash_key)
                        if entries:
                            for entry in entries:
                                similar.append((stored_hash, entry, hash_diff))
                except Exception as e:
                    logger.debug(f"Cache check error: {e}")
                    continue

        new_hash_str = str(new_hash)
        prefix = new_hash_str[:4]

        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT hash_value, message_id, channel_id, user_id, timestamp, image_url
                FROM image_hashes 
                WHERE guild_id = ? AND hash_value LIKE ?
            ''', (guild_id, f"{prefix}%")) as cursor:
                async for row in cursor:
                    try:
                        hash_str = row[0]
                        if hash_str in checked_hashes:
                            continue
                        checked_hashes.add(hash_str)

                        stored_hash = imagehash.hex_to_hash(hash_str)
                        hash_diff = new_hash - stored_hash

                        if hash_diff <= threshold:
                            entry = (row[1], row[2], row[3], row[4], row[5])
                            similar.append((stored_hash, entry, hash_diff))

                            hash_key = f"{guild_id}_{hash_str}"
                            cached = hash_cache.get(hash_key) or []
                            if entry not in cached:
                                cached.append(entry)
                                hash_cache.put(hash_key, cached)
                    except Exception as e:
                        logger.debug(f"DB row processing error: {e}")
                        continue

        return similar
    except Exception as e:
        logger.error(f"Find similar images error: {e}")
        return []

async def update_user_stats(guild_id: int, user_id: int, is_duplicate: bool = False) -> bool:
    """Update user statistics"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT unique_count, duplicate_count FROM user_stats 
                WHERE guild_id = ? AND user_id = ?
            ''', (guild_id, user_id)) as cursor:
                row = await cursor.fetchone()

            if row:
                unique_count = row[0] + (0 if is_duplicate else 1)
                duplicate_count = row[1] + (1 if is_duplicate else 0)
                await db.execute('''
                    UPDATE user_stats SET unique_count = ?, duplicate_count = ?
                    WHERE guild_id = ? AND user_id = ?
                ''', (unique_count, duplicate_count, guild_id, user_id))
            else:
                await db.execute('''
                    INSERT INTO user_stats (guild_id, user_id, unique_count, duplicate_count)
                    VALUES (?, ?, ?, ?)
                ''', (guild_id, user_id, 0 if is_duplicate else 1, 1 if is_duplicate else 0))

            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Update user stats error: {e}")
        return False

async def get_user_stats(guild_id: int, user_id: int) -> Dict:
    """Get user statistics"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT unique_count, duplicate_count FROM user_stats 
                WHERE guild_id = ? AND user_id = ?
            ''', (guild_id, user_id)) as cursor:
                row = await cursor.fetchone()
                return {'unique': row[0], 'duplicates': row[1]} if row else {'unique': 0, 'duplicates': 0}
    except Exception as e:
        logger.error(f"Get user stats error: {e}")
        return {'unique': 0, 'duplicates': 0}

async def add_duplicate_record(guild_id: int, user_id: int, user_name: str, 
                              channel_id: int, message_id: int, similarity_count: int) -> bool:
    """Add duplicate record to history"""
    try:
        async with db_pool.acquire() as db:
            await db.execute('''
                INSERT INTO duplicate_history 
                (guild_id, timestamp, user_id, user_name, channel_id, message_id, similarity_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (guild_id, datetime.now().isoformat(), user_id, str(user_name), 
                  channel_id, message_id, similarity_count))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Add duplicate record error: {e}")
        return False

async def get_duplicate_history(guild_id: int, limit: int = 100) -> List[Dict]:
    """Get duplicate history for guild"""
    try:
        history = []
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT timestamp, user_name, channel_id, message_id, similarity_count
                FROM duplicate_history WHERE guild_id = ?
                ORDER BY timestamp DESC LIMIT ?
            ''', (guild_id, limit)) as cursor:
                async for row in cursor:
                    history.append({
                        'timestamp': row[0],
                        'user_name': row[1],
                        'channel_id': row[2],
                        'message_id': row[3],
                        'similarity_count': row[4]
                    })
        return history
    except Exception as e:
        logger.error(f"Get duplicate history error: {e}")
        return []

async def get_all_guild_hashes(guild_id: int) -> Dict:
    """Get all image hashes for a guild (optimized)"""
    try:
        hashes = {}
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT hash_value, message_id, channel_id, user_id, timestamp, image_url
                FROM image_hashes WHERE guild_id = ?
            ''', (guild_id,)) as cursor:
                async for row in cursor:
                    try:
                        hash_obj = imagehash.hex_to_hash(row[0])
                        if hash_obj not in hashes:
                            hashes[hash_obj] = []
                        hashes[hash_obj].append((row[1], row[2], row[3], row[4], row[5]))
                    except:
                        continue
        return hashes
    except Exception as e:
        logger.error(f"Get guild hashes error: {e}")
        return {}

async def clear_guild_data(guild_id: int) -> bool:
    """Clear all data for a guild"""
    try:
        async with db_pool.acquire() as db:
            await db.execute('DELETE FROM image_hashes WHERE guild_id = ?', (guild_id,))
            await db.execute('DELETE FROM user_stats WHERE guild_id = ?', (guild_id,))
            await db.execute('DELETE FROM duplicate_history WHERE guild_id = ?', (guild_id,))
            await db.execute('DELETE FROM scan_progress WHERE guild_id = ?', (guild_id,))
            await db.commit()

        hash_cache.clear_guild(guild_id)
        logger.info(f"Cleared data for guild {guild_id}")
        return True
    except Exception as e:
        logger.error(f"Clear guild data error: {e}")
        return False

async def save_scan_progress(guild_id: int, channel_id: int, last_message_id: int, 
                            total_processed: int = 0) -> bool:
    """Save scan progress"""
    try:
        async with db_pool.acquire() as db:
            await db.execute('''
                INSERT OR REPLACE INTO scan_progress 
                (guild_id, channel_id, last_message_id, total_processed)
                VALUES (?, ?, ?, ?)
            ''', (guild_id, channel_id, last_message_id, total_processed))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Save scan progress error: {e}")
        return False

async def get_scan_progress(guild_id: int, channel_id: int) -> Optional[Tuple[int, int]]:
    """Get last scanned message ID and total processed"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT last_message_id, total_processed FROM scan_progress 
                WHERE guild_id = ? AND channel_id = ?
            ''', (guild_id, channel_id)) as cursor:
                row = await cursor.fetchone()
                return (row[0], row[1]) if row else None
    except Exception as e:
        logger.error(f"Get scan progress error: {e}")
        return None

async def check_if_processed(guild_id: int, message_id: int) -> bool:
    """Check if message was already processed"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT COUNT(*) FROM image_hashes 
                WHERE guild_id = ? AND message_id = ?
            ''', (guild_id, message_id)) as cursor:
                row = await cursor.fetchone()
                return row[0] > 0 if row else False
    except:
        return False

@tasks.loop(minutes=5)
async def auto_save():
    """Auto-save all guild data and cleanup cache"""
    logger.info("Running auto-save...")
    for guild_id in list(guild_data.keys()):
        await save_guild_data(guild_id)
    logger.info(f"Auto-save complete. Cache size: {hash_cache.size()}")

def format_time(seconds: float) -> str:
    """Format seconds to human readable time"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds//60:.0f}m {seconds%60:.0f}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours:.0f}h {minutes:.0f}m"

def create_progress_bar(percentage: float, width: int = 20) -> str:
    """Create visual progress bar"""
    filled = int(width * percentage / 100)
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}] {percentage:.1f}%"

async def download_image(url: str, retries: int = MAX_RETRY_ATTEMPTS) -> Optional[Dict]:
    """Download image with retry and rate limiting"""
    async with download_semaphore:
        for attempt in range(retries):
            try:
                timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            size_mb = len(data) / (1024 * 1024)
                            try:
                                with Image.open(BytesIO(data)) as img:
                                    width, height = img.size
                                    format_name = img.format
                                    mp = (width * height) / 1_000_000
                                    return {
                                        'content': data,
                                        'width': width,
                                        'height': height,
                                        'format': format_name,
                                        'size_mb': size_mb,
                                        'megapixels': mp
                                    }
                            except:
                                return {'content': data, 'width': 0, 'height': 0, 'format': 'UNKNOWN', 'size_mb': size_mb, 'megapixels': 0}
                        elif resp.status == 429:
                            retry_after = int(resp.headers.get('Retry-After', 5))
                            await asyncio.sleep(retry_after)
            except Exception as e:
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.error(f"Download failed after {retries} attempts: {e}")
        return None

def calculate_image_hash(image_bytes: bytes):
    """Calculate perceptual hash with error handling"""
    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        return imagehash.phash(img)
    except Exception as e:
        logger.error(f"Hash calculation error: {e}")
        return None

async def get_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Get log channel"""
    return discord.utils.get(guild.channels, name=LOG_CHANNEL_NAME)

def should_check_channel(guild_id: int, channel_id: int) -> bool:
    """Check if channel should be monitored. Default: Blacklist everything except whitelisted."""
    config = guild_data.get(guild_id, {}).get('config', {})
    whitelist = config.get('whitelist', set())
    
    # If whitelist is empty, we don't scan anything by default
    # This implements the "blacklist every channel then only whitelist scan" logic
    # Also ensure the channel actually exists and the bot can see it
    if not whitelist or channel_id not in whitelist:
        return False
        
    return True

def should_check_user(guild_id: int, user_id: int) -> bool:
    """Check if user should be monitored"""
    config = guild_data.get(guild_id, {}).get('config', {})
    user_whitelist = config.get('user_whitelist', set())
    return user_id not in user_whitelist

async def send_duplicate_alert(message: discord.Message, attachment: discord.Attachment, 
                              similar_images: List, scan_times: Dict[str, float], image_metadata: Dict):
    """Send duplicate alert with detailed information matching user screenshot"""
    guild_id = message.guild.id
    log_channel = await get_log_channel(message.guild)
    if not log_channel:
        log_channel = message.channel

    embed = discord.Embed(
        title="🚨 Duplicate Image Detected",
        description=f"A duplicate was found in **{message.guild.name}** > {message.channel.mention}",
        color=discord.Color.red(),
        timestamp=message.created_at
    )

    # User Statistics (placeholder logic for the UI)
    stats = await get_user_stats(guild_id, message.author.id)
    total_posts = stats['unique'] + stats['duplicates']
    dup_rate = (stats['duplicates'] / total_posts * 100) if total_posts > 0 else 0

    embed.add_field(
        name="👤 Posted By",
        value=f"{message.author.mention}\n{message.author.name} (ID: {message.author.id})",
        inline=False
    )
    
    # Check if the bot was mentioned and update tagging preference if needed
    if bot.user.mentioned_in(message):
        config = guild_data[guild_id]['config']
        notif_settings = config.get('notification_settings', {})
        g_id_str = str(guild_id)
        notif_settings[g_id_str] = {
            'type': 'user',
            'user_id': message.author.id
        }
        config['notification_settings'] = notif_settings
        asyncio.create_task(save_guild_data(guild_id))

    # Detection Details
    best_similarity = 0
    best_diff = 10
    if similar_images:
        best_diff = min(diff for _, _, diff in similar_images)
        best_similarity = max(0, 100 - (best_diff * 10))

    embed.add_field(
        name="🔍 Detection Details",
        value=f"**Similarity:** {best_similarity:.1f}%\n"
              f"**Hamming Distance:** {best_diff}/5\n"
              f"**Matches Found:** {len(similar_images)}\n"
              f"**Quality Score:** 85/100 (Good)", # Placeholder
        inline=False
    )

    # Image Info
    embed.add_field(
        name="🖼️ Image Info",
        value=f"**Format:** {image_metadata.get('format', 'UNKNOWN')}\n"
              f"**Dimensions:** {image_metadata.get('width', 0)}x{image_metadata.get('height', 0)}\n"
              f"**Size:** {image_metadata.get('size_mb', 0):.2f} MB\n"
              f"**Megapixels:** {image_metadata.get('megapixels', 0):.1f}MP",
        inline=False
    )

    # User Statistics
    embed.add_field(
        name="📊 User Statistics",
        value=f"**Total Posts:** {total_posts}\n"
              f"**Unique:** {stats['unique']} ✅\n"
              f"**Duplicates:** {stats['duplicates']} ❌\n"
              f"**Duplicate Rate:** {dup_rate:.1f}%\n"
              f"**Warnings:** 0", # Placeholder
        inline=False
    )

    existing_similar = []
    for stored_hash, entry, diff in similar_images:
        msg_id, ch_id, user_id, timestamp, old_url = entry
        try:
            channel = bot.get_channel(ch_id)
            if channel:
                # Optimized check for original message
                try:
                    original_message = await channel.fetch_message(msg_id)
                    if original_message:
                        existing_similar.append((stored_hash, entry, diff))
                except discord.NotFound:
                    continue
                except:
                    continue
        except:
            continue

    if existing_similar:
        stored_hash, entry, diff = existing_similar[0]
        msg_id, ch_id, user_id, timestamp, old_url = entry
        channel = bot.get_channel(ch_id)
        user = bot.get_user(user_id)
        
        # Calculate time since original post for the "a month ago" style detail
        dt = datetime.fromisoformat(timestamp)
        now = datetime.now()
        diff_time = now - dt
        
        if diff_time.days > 30:
            time_str = f"{diff_time.days // 30} month(s) ago"
        elif diff_time.days > 0:
            time_str = f"{diff_time.days} day(s) ago"
        elif diff_time.seconds > 3600:
            time_str = f"{diff_time.seconds // 3600} hour(s) ago"
        else:
            time_str = f"{diff_time.seconds // 60} minute(s) ago"

        embed.add_field(
            name="📍 Original Post",
            value=f"**Original by:** {user.mention if user else 'Unknown'}\n"
                  f"**Channel:** {channel.mention if channel else 'Unknown'}\n"
                  f"**Posted:** {time_str} ({discord.utils.format_dt(dt, 'R')})\n"
                  f"[Jump to Original](https://discord.com/channels/{message.guild.id}/{ch_id}/{msg_id})",
            inline=False
        )

    embed.add_field(
        name="📍 Duplicate Post",
        value=f"[Jump to Duplicate]({message.jump_url})",
        inline=False
    )

    embed.set_thumbnail(url=attachment.url)

    config = guild_data.get(guild_id, {}).get('config', {})
    threshold = config.get('hash_threshold', HASH_THRESHOLD)
    
    footer_text = f"Threshold: {threshold} • Channel: {message.channel.name} | {message.created_at.strftime('%d/%m/%Y %H:%M')}"
    embed.set_footer(text=footer_text)

    notification_settings = config.get('notification_settings', {})
    guild_settings = notification_settings.get(str(message.guild.id), {})
    mention_type = guild_settings.get('type', 'role')

    content = None
    if mention_type == 'role':
        admin_role = discord.utils.get(message.guild.roles, name=ADMIN_ROLE_NAME)
        if admin_role and log_channel != message.channel:
            content = admin_role.mention
    elif mention_type == 'user':
        u_id = guild_settings.get('user_id')
        u = bot.get_user(u_id) if u_id else None
        if u:
            content = u.mention

    await log_channel.send(content=content, embed=embed)

    notification_settings = config.get('notification_settings', {})
    guild_settings = notification_settings.get(str(message.guild.id), {})
    mention_type = guild_settings.get('type', 'role')

    content = None
    if mention_type == 'role':
        admin_role = discord.utils.get(message.guild.roles, name=ADMIN_ROLE_NAME)
        if admin_role and log_channel != message.channel:
            content = admin_role.mention
    elif mention_type == 'user':
        user_id = guild_settings.get('user_id')
        user = bot.get_user(user_id) if user_id else None
        if user:
            content = user.mention

    await log_channel.send(content=content, embed=embed)

    if log_channel != message.channel:
        try:
            await message.reply(
                f"⚠️ Duplicate detected! Check {log_channel.mention} for details.",
                delete_after=15
            )
        except:
            pass

@bot.event
async def on_ready():
    logger.info(f'{bot.user} connected!')
    logger.info(f'Monitoring {len(bot.guilds)} server(s)')

    await asyncio.get_event_loop().run_in_executor(None, init_db_sync)
    await db_pool.initialize()

    for guild in bot.guilds:
        await load_guild_data(guild.id)
        logger.info(f'   - {guild.name}')
        log_channel = await get_log_channel(guild)
        if log_channel:
            logger.info(f'   ✅ Log channel: #{log_channel.name}')

    if not auto_save.is_running():
        auto_save.start()

    try:
        synced = await bot.tree.sync()
        logger.info(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

    # Set bot status
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="for duplicates"))
    logger.info("Bot ready! 🚀")

@bot.event
async def on_guild_join(guild):
    await load_guild_data(guild.id)
    logger.info(f'Joined new guild: {guild.name}')

async def handle_reaction_removal(message, emoji):
    """Wait and remove reaction if enabled"""
    try:
        guild_id = message.guild.id
        if guild_id not in guild_data:
            await load_guild_data(guild_id)
        
        config = guild_data.get(guild_id, {}).get('config', {})
        # Always remove 🔄 immediately when processing starts or ends if needed, 
        # but here we handle the "auto-remove" setting for success/fail reactions
        if config.get('remove_reactions', False):
            timeout = config.get('reaction_timeout', 10)
            await asyncio.sleep(timeout)
            try:
                # Re-fetch message to ensure it still exists
                msg = await message.channel.fetch_message(message.id)
                await msg.remove_reaction(emoji, bot.user)
            except Exception as e:
                logger.debug(f"Failed to remove reaction {emoji}: {e}")
    except Exception as e:
        logger.error(f"Reaction removal task error: {e}")

@bot.tree.command(name="reaction_settings", description="Configure reaction removal settings")
@app_commands.describe(
    enabled="Whether to automatically remove reactions after a timeout",
    timeout="Seconds to wait before removing the reaction"
)
async def reaction_settings(interaction: discord.Interaction, enabled: bool, timeout: Optional[int] = 10):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need 'Manage Server' permissions to use this command.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)
    
    if not guild_data.get(guild_id) or 'config' not in guild_data[guild_id]:
        guild_data[guild_id] = {
            'config': {
                'whitelist': set(),
                'blacklist': set(),
                'user_whitelist': set(),
                'channel_thresholds': {},
                'notification_settings': {},
                'hash_threshold': HASH_THRESHOLD,
                'auto_delete': AUTO_DELETE_DUPLICATES,
                'remove_reactions': False,
                'reaction_timeout': 10
            }
        }
    
    guild_data[guild_id]['config']['remove_reactions'] = enabled
    guild_data[guild_id]['config']['reaction_timeout'] = max(1, timeout)
    
    await save_guild_data(guild_id)
    
    status = "enabled" if enabled else "disabled"
    await interaction.response.send_message(f"Reaction auto-removal {status}. Timeout set to {timeout} seconds.", ephemeral=True)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)

    guild_id = message.guild.id

    if guild_id not in guild_data:
        await load_guild_data(guild_id)

    if not should_check_channel(guild_id, message.channel.id):
        return
    if not should_check_user(guild_id, message.author.id):
        return

    if message.attachments:
        for attachment in message.attachments:
            if not any(attachment.filename.lower().endswith(ext) 
                      for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']):
                continue

            if await check_if_processed(guild_id, message.id):
                try:
                    await message.add_reaction("✅")
                    asyncio.create_task(handle_reaction_removal(message, "✅"))
                except:
                    pass
                continue

            try:
                await message.add_reaction("🔄")
                asyncio.create_task(handle_reaction_removal(message, "🔄"))
            except:
                pass

            scan_times = {
                'download': 0,
                'hash': 0,
                'comparison': 0,
                'total': 0
            }

            start_time = time.time()

            await rate_limiter.acquire()

            download_start = time.time()
            download_result = await download_image(attachment.url)
            scan_times['download'] = time.time() - download_start

            if not download_result:
                try:
                    await message.remove_reaction("🔄", bot.user)
                    await message.add_reaction("⚠️")
                    asyncio.create_task(handle_reaction_removal(message, "⚠️"))
                except:
                    pass
                continue

            image_data = download_result['content']
            
            hash_start = time.time()
            img_hash = calculate_image_hash(image_data)
            scan_times['hash'] = time.time() - hash_start

            if not img_hash:
                try:
                    await message.remove_reaction("🔄", bot.user)
                    await message.add_reaction("❌")
                    asyncio.create_task(handle_reaction_removal(message, "❌"))
                except:
                    pass
                continue

            comparison_start = time.time()
            similar = await find_similar_images(guild_id, img_hash, message.channel.id)
            scan_times['comparison'] = time.time() - comparison_start

            scan_times['total'] = time.time() - start_time

            try:
                await message.remove_reaction("🔄", bot.user)
            except:
                pass

            if similar:
                await update_user_stats(guild_id, message.author.id, is_duplicate=True)
                await add_duplicate_record(
                    guild_id, message.author.id, str(message.author), 
                    message.channel.id, message.id, len(similar)
                )

                try:
                    await message.add_reaction("❌")
                    asyncio.create_task(handle_reaction_removal(message, "❌"))
                except:
                    pass

                await send_duplicate_alert(message, attachment, similar, scan_times, download_result)

                config = guild_data.get(guild_id, {}).get('config', {})
                auto_delete = config.get('auto_delete', AUTO_DELETE_DUPLICATES)

                if auto_delete:
                    try:
                        await message.delete()
                        temp_msg = await message.channel.send(
                            f"🗑️ Duplicate image from {message.author.mention} was automatically deleted."
                        )
                        await asyncio.sleep(2)
                        await temp_msg.delete()
                    except:
                        pass
            else:
                await update_user_stats(guild_id, message.author.id, is_duplicate=False)
                try:
                    await message.add_reaction("✅")
                    asyncio.create_task(handle_reaction_removal(message, "✅"))
                except:
                    pass

            await save_image_hash(
                guild_id, img_hash, message.id, message.channel.id,
                message.author.id, message.created_at, attachment.url
            )

# ==================== SLASH COMMANDS ====================

@bot.tree.command(name="setup", description="Start the bot and reset scanning: Blacklist everything first (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    """Initialize strict whitelist mode by clearing all whitelisted channels"""
    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)
    
    # Reset whitelist to empty set - this effectively blacklists every channel
    guild_data[guild_id]['config']['whitelist'] = set()
    await save_guild_data(guild_id)
    
    embed = discord.Embed(
        title="🚀 Bot Setup Complete",
        description="Every channel in this server has been **blacklisted** by default.\n\n"
                    "**Next Steps:**\n"
                    "1. Use `/whitelist #channel` to choose which channels you want to scan.\n"
                    "2. Use `/listchannels` to verify your monitored channels.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="bothelp", description="Show all available commands and bot information")
async def bothelp(interaction: discord.Interaction):
    """Show all available commands"""
    embed = discord.Embed(
        title="🤖 Duplicate Image Detector - Production Edition",
        description="Automatically detects and manages duplicate images\n"
                    "✅ = Unique | ❌ = Duplicate | ⚠️ = Failed | 🔄 = Processing",
        color=discord.Color.blue()
    )

    # Setup Command
    embed.add_field(
        name="🚀 Getting Started",
        value="`/setup` - Reset and blacklist every channel (Start here)",
        inline=False
    )

    # Scanning & Management Commands
    scanning = (
        "`/scanhistory [channel] [limit]` - Scan channel history for duplicates\n"
        "`/scanall [limit]` - Scan all whitelisted channels in the server\n"
        "`/resumescan [channel]` - Continue an interrupted scan\n"
        "`/whitelist [channel]` - Enable scanning for a specific channel\n"
        "`/blacklist [channel]` - Disable scanning (Note: Only whitelisted channels scan)\n"
        "`/stats` - View bot statistics\n"
        "`/userstats [@user]` - View specific user statistics\n"
        "`/leaderboard [mode]` - View top posters"
    )
    embed.add_field(name="🔍 Scanning & Management", value=scanning, inline=False)

    # Configuration Commands
    config_field = (
        "`/reaction_settings [enabled] [timeout]` - Auto-remove bot reactions\n"
        "`/setnotifications [type] [target]` - Configure duplicate alerts\n"
        "`/setsimilarity [threshold]` - Set detection sensitivity (0-10)\n"
        "`/toggleautodelete [enabled]` - Auto-delete duplicates found\n"
        "`/cleardata` - Wipe all server data from the bot\n"
        "`/listchannels` - Show all whitelisted channels\n"
        "`/ping` - Check bot latency"
    )
    embed.add_field(name="⚙️ Configuration (Admin)", value=config_field, inline=False)

    # Bot Information
    bot_info = (
        "• **System:** Strict Whitelist Mode (Only scans whitelisted channels)\n"
        "• **Detection:** Perceptual Hashing (phash)\n"
        "• **Status:** Active & Watching for duplicates"
    )
    embed.add_field(name="🤖 Bot Info", value=bot_info, inline=False)

    embed.set_footer(text=f"Version 2.1.0 | Ping: {round(bot.latency * 1000)}ms")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="listchannels", description="List all whitelisted and monitored channels")
async def listchannels(interaction: discord.Interaction):
    """List all whitelisted channels"""
    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)
    
    config = guild_data.get(guild_id, {}).get('config', {})
    whitelist = config.get('whitelist', set())
    
    if not whitelist:
        await interaction.response.send_message("❌ No channels are currently whitelisted. The bot is not scanning anything.", ephemeral=True)
        return
        
    channels_mentions = []
    for cid in whitelist:
        channel = bot.get_channel(cid)
        if channel:
            channels_mentions.append(channel.mention)
        else:
            channels_mentions.append(f"`Unknown Channel ({cid})`")
            
    embed = discord.Embed(
        title="📋 Monitored Channels",
        description="\n".join(channels_mentions),
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Total: {len(channels_mentions)} channels")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ping", description="Check bot latency and status")
async def ping(interaction: discord.Interaction):
    """Check bot latency and status"""
    start = time.time()
    await interaction.response.defer()
    end = time.time()

    latency_ws = round(bot.latency * 1000)
    latency_api = round((end - start) * 1000)

    embed = discord.Embed(title="🏓 Pong!", color=discord.Color.green())
    embed.add_field(name="WebSocket", value=f"{latency_ws}ms", inline=True)
    embed.add_field(name="API", value=f"{latency_api}ms", inline=True)
    embed.add_field(name="Cache Size", value=f"{hash_cache.size():,}", inline=True)

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="stats", description="Show detection statistics for this server")
async def stats(interaction: discord.Interaction):
    """Show detection statistics"""
    await interaction.response.defer()

    try:
        hashes = await get_all_guild_hashes(interaction.guild_id)
        total_images = sum(len(entries) for entries in hashes.values())
        unique_hashes = len(hashes)

        config = guild_data.get(interaction.guild_id, {}).get('config', {})
        whitelist = config.get('whitelist', set())
        blacklist = config.get('blacklist', set())
        user_whitelist = config.get('user_whitelist', set())
        threshold = config.get('hash_threshold', HASH_THRESHOLD)
        auto_delete = config.get('auto_delete', AUTO_DELETE_DUPLICATES)

        embed = discord.Embed(title="📊 Detection Statistics", color=discord.Color.blue())
        embed.add_field(name="Total Images", value=f"{total_images:,}", inline=True)
        embed.add_field(name="Unique Hashes", value=f"{unique_hashes:,}", inline=True)
        embed.add_field(name="Cache Size", value=f"{hash_cache.size():,}", inline=True)
        embed.add_field(name="Threshold", value=str(threshold), inline=True)
        embed.add_field(name="Auto-Delete", value="ON" if auto_delete else "OFF", inline=True)
        embed.add_field(name="DB Pool", value=f"{DB_POOL_SIZE} connections", inline=True)
        embed.add_field(name="Whitelist Channels", value=str(len(whitelist)), inline=True)
        embed.add_field(name="Blacklist Channels", value=str(len(blacklist)), inline=True)
        embed.add_field(name="Whitelisted Users", value=str(len(user_whitelist)), inline=True)

        log_channel = discord.utils.get(interaction.guild.channels, name=LOG_CHANNEL_NAME)
        embed.add_field(
            name="Log Channel",
            value=log_channel.mention if log_channel else "❌ Not found",
            inline=False
        )

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error getting stats: {str(e)}")

@bot.tree.command(name="userstats", description="Show statistics for a user")
@app_commands.describe(user="The user to check (leave empty for yourself)")
async def userstats(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    """Show user statistics"""
    await interaction.response.defer()

    if user is None:
        user = interaction.user

    try:
        stats = await get_user_stats(interaction.guild_id, user.id)

        embed = discord.Embed(
            title=f"📊 Stats for {user.display_name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Unique Images", value=f"{stats['unique']:,}", inline=True)
        embed.add_field(name="Duplicates", value=f"{stats['duplicates']:,}", inline=True)

        total = stats['unique'] + stats['duplicates']
        if total > 0:
            percentage = (stats['unique'] / total) * 100
            embed.add_field(name="Originality", value=f"{percentage:.1f}%", inline=True)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

@bot.tree.command(name="leaderboard", description="Show server leaderboard")
@app_commands.describe(mode="Show unique images or duplicates")
@app_commands.choices(mode=[
    app_commands.Choice(name="Unique Images", value="unique"),
    app_commands.Choice(name="Duplicates", value="duplicates")
])
async def leaderboard(interaction: discord.Interaction, mode: str = "unique"):
    """Show leaderboard"""
    await interaction.response.defer()

    try:
        users = []
        async with db_pool.acquire() as db:
            query = f'''
                SELECT user_id, unique_count, duplicate_count FROM user_stats 
                WHERE guild_id = ? ORDER BY {mode}_count DESC LIMIT 10
            '''
            async with db.execute(query, (interaction.guild_id,)) as cursor:
                async for row in cursor:
                    users.append((row[0], {'unique': row[1], 'duplicates': row[2]}))

        embed = discord.Embed(
            title=f"🏆 Leaderboard - Most {mode.title()}",
            color=discord.Color.gold()
        )

        medals = ["🥇", "🥈", "🥉"]
        for idx, (user_id, stats) in enumerate(users, 1):
            user = bot.get_user(user_id)
            if user:
                medal = medals[idx-1] if idx <= 3 else f"#{idx}"
                embed.add_field(
                    name=f"{medal} {user.display_name}",
                    value=f"{stats[mode]:,} {mode}",
                    inline=False
                )

        if not users:
            embed.description = "No data available yet!"

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

@bot.tree.command(name="export", description="Export duplicate history (Admin only)")
@app_commands.describe(format="Export format (CSV or JSON)")
@app_commands.choices(format=[
    app_commands.Choice(name="CSV", value="csv"),
    app_commands.Choice(name="JSON", value="json")
])
@app_commands.checks.has_permissions(administrator=True)
async def export_data(interaction: discord.Interaction, format: str = "csv"):
    """Export duplicate history"""
    await interaction.response.defer()

    try:
        history = await get_duplicate_history(interaction.guild_id, limit=10000)

        if not history:
            await interaction.followup.send("❌ No duplicate history to export!")
            return

        filename = f"duplicates_{interaction.guild_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{format}"

        if format == 'csv':
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['timestamp', 'user_name', 'channel_id', 
                                                       'message_id', 'similarity_count'])
                writer.writeheader()
                writer.writerows(history)
        elif format == 'json':
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)

        await interaction.followup.send(f"✅ Exported {len(history):,} records", file=discord.File(filename))
        os.remove(filename)
    except Exception as e:
        logger.error(f"Export error: {e}")
        await interaction.followup.send(f"❌ Error exporting data: {str(e)}")

@bot.tree.command(name="scanhistory", description="Scan channel history for duplicate images (Admin only)")
@app_commands.describe(
    channel="Channel to scan (leave empty for current channel)",
    limit="Number of messages to scan (default: 100)"
)
@app_commands.checks.has_permissions(administrator=True)
async def scanhistory(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, limit: int = 100):
    """Scan channel history with progress tracking"""

    if channel is None:
        channel = interaction.channel

    if limit > 100000:
        await interaction.response.send_message("❌ Maximum: 100,000 messages!")
        return

    await interaction.response.defer()

    status_msg = await interaction.followup.send("🔍 Initializing scan...")

    start_time = time.time()
    messages_checked = 0
    found = 0
    duplicates_found = 0
    last_update = 0
    processed_messages = set()
    batch_data = []

    progress = await get_scan_progress(interaction.guild_id, channel.id)
    last_message_id = progress[0] if progress and progress[0] else None

    try:
        history_params = {'limit': limit, 'oldest_first': False}
        if last_message_id:
            history_params['after'] = discord.Object(id=last_message_id)
            await status_msg.edit(content=f"🔍 Resuming scan from last checkpoint...")

        async for message in channel.history(**history_params):
            if message.id in processed_messages:
                continue
            processed_messages.add(message.id)

            messages_checked += 1

            if message.attachments:
                for attachment in message.attachments:
                    if not any(attachment.filename.lower().endswith(ext) 
                              for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']):
                        continue

                    if await check_if_processed(interaction.guild_id, message.id):
                        continue

                    await rate_limiter.acquire()

                    image_data = await download_image(attachment.url)
                    if image_data:
                        img_hash = calculate_image_hash(image_data)
                        if img_hash:
                            found += 1
                            similar = await find_similar_images(interaction.guild_id, img_hash, channel.id)
                            if similar:
                                duplicates_found += 1
                                try:
                                    await message.add_reaction("❌")
                                    asyncio.create_task(handle_reaction_removal(message, "❌"))
                                except:
                                    pass
                            else:
                                try:
                                    await message.add_reaction("✅")
                                    asyncio.create_task(handle_reaction_removal(message, "✅"))
                                except:
                                    pass

                            batch_data.append((
                                interaction.guild_id, str(img_hash), message.id, message.channel.id,
                                message.author.id, message.created_at.isoformat(), attachment.url
                            ))

                            if len(batch_data) >= BATCH_COMMIT_SIZE:
                                await save_image_hash_batch(batch_data)
                                batch_data.clear()

            if messages_checked - last_update >= 50:
                last_update = messages_checked
                elapsed = time.time() - start_time
                rate = messages_checked / elapsed if elapsed > 0 else 0
                remaining = (limit - messages_checked) / rate if rate > 0 else 0

                percentage = min(100, (messages_checked / limit) * 100)
                progress_bar = create_progress_bar(percentage)

                await status_msg.edit(
                    content=f"🔍 Scanning {channel.mention}\n"
                           f"{progress_bar}\n"
                           f"Messages: {messages_checked:,}/{limit:,}\n"
                           f"Images: {found:,} | Duplicates: {duplicates_found:,}\n"
                           f"Speed: {rate:.1f} msgs/sec | ETA: {format_time(remaining)}"
                )

            if messages_checked % 500 == 0:
                await save_scan_progress(interaction.guild_id, channel.id, message.id, messages_checked)

        if batch_data:
            await save_image_hash_batch(batch_data)

        await save_scan_progress(interaction.guild_id, channel.id, 0, messages_checked)
        await save_guild_data(interaction.guild_id)

        total_time = time.time() - start_time
        embed = discord.Embed(title="✅ Scan Complete", color=discord.Color.green())
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Messages", value=f"{messages_checked:,}", inline=True)
        embed.add_field(name="Images", value=f"{found:,}", inline=True)
        embed.add_field(name="Duplicates", value=f"{duplicates_found:,}", inline=True)
        embed.add_field(name="Time", value=format_time(total_time), inline=True)
        embed.add_field(name="Speed", value=f"{messages_checked/max(total_time, 1):.1f} msgs/sec", inline=True)

        await status_msg.edit(content="", embed=embed)

    except Exception as e:
        logger.error(f"Scan error: {e}")
        await status_msg.edit(content=f"❌ Error: {str(e)[:100]}")

@bot.tree.command(name="scanall", description="Scan all monitored channels (Admin only)")
@app_commands.describe(limit="Number of messages per channel (default: 100)")
@app_commands.checks.has_permissions(administrator=True)
async def scanall(interaction: discord.Interaction, limit: int = 100):
    """Scan all monitored channels"""

    if limit > 50000:
        await interaction.response.send_message("❌ Maximum: 50,000 messages per channel!")
        return

    await interaction.response.defer()

    status_msg = await interaction.followup.send("🔍 Initializing server-wide scan...")

    start_time = time.time()
    total_channels = 0
    total_messages = 0
    total_images = 0
    total_duplicates = 0
    last_update = 0
    batch_data = []

    channels_to_scan = [
        ch for ch in interaction.guild.text_channels 
        if should_check_channel(interaction.guild_id, ch.id)
    ]

    total_channels_count = len(channels_to_scan)

    for channel_idx, channel in enumerate(channels_to_scan):
        channel_messages = 0
        processed_messages = set()

        try:
            async for message in channel.history(limit=limit):
                if message.id in processed_messages:
                    continue
                processed_messages.add(message.id)

                channel_messages += 1
                total_messages += 1

                if message.attachments:
                    for attachment in message.attachments:
                        if not any(attachment.filename.lower().endswith(ext) 
                                  for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']):
                            continue

                        if await check_if_processed(interaction.guild_id, message.id):
                            continue

                        await rate_limiter.acquire()

                        image_data = await download_image(attachment.url)
                        if image_data:
                            img_hash = calculate_image_hash(image_data)
                            if img_hash:
                                total_images += 1
                                similar = await find_similar_images(interaction.guild_id, img_hash, channel.id)
                                if similar:
                                    total_duplicates += 1
                                    try:
                                        await message.add_reaction("❌")
                                        asyncio.create_task(handle_reaction_removal(message, "❌"))
                                    except:
                                        pass
                                else:
                                    try:
                                        await message.add_reaction("✅")
                                        asyncio.create_task(handle_reaction_removal(message, "✅"))
                                    except:
                                        pass

                                batch_data.append((
                                    interaction.guild_id, str(img_hash), message.id, message.channel.id,
                                    message.author.id, message.created_at.isoformat(), attachment.url
                                ))

                                if len(batch_data) >= BATCH_COMMIT_SIZE:
                                    await save_image_hash_batch(batch_data)
                                    batch_data.clear()

                if total_messages - last_update >= 100:
                    last_update = total_messages
                    elapsed = time.time() - start_time
                    rate = total_messages / elapsed if elapsed > 0 else 0
                    remaining_channels = total_channels_count - (channel_idx + 1)
                    avg_time = elapsed / (channel_idx + 1) if channel_idx > 0 else 0
                    eta = remaining_channels * avg_time

                    overall_pct = ((channel_idx * limit) + channel_messages) / (total_channels_count * limit) * 100
                    channel_pct = (channel_messages / limit) * 100

                    await status_msg.edit(
                        content=f"🔍 Server Scan Progress\n"
                               f"{create_progress_bar(overall_pct)}\n"
                               f"Channels: {channel_idx + 1}/{total_channels_count}\n"
                               f"Current: {channel.mention} ({create_progress_bar(channel_pct)})\n"
                               f"Messages: {total_messages:,} | Images: {total_images:,} | Duplicates: {total_duplicates:,}\n"
                               f"Speed: {rate:.1f} msgs/sec | ETA: {format_time(eta)}"
                    )

            total_channels += 1

        except discord.Forbidden:
            logger.warning(f"No access to channel {channel.name}")
            continue
        except Exception as e:
            logger.error(f"Error scanning {channel.name}: {e}")
            continue

    if batch_data:
        await save_image_hash_batch(batch_data)

    await save_guild_data(interaction.guild_id)

    total_time = time.time() - start_time
    embed = discord.Embed(title="✅ Server Scan Complete", color=discord.Color.green())
    embed.add_field(name="Channels", value=f"{total_channels:,}", inline=True)
    embed.add_field(name="Messages", value=f"{total_messages:,}", inline=True)
    embed.add_field(name="Images", value=f"{total_images:,}", inline=True)
    embed.add_field(name="Duplicates", value=f"{total_duplicates:,}", inline=True)
    embed.add_field(name="Time", value=format_time(total_time), inline=True)
    embed.add_field(name="Speed", value=f"{total_messages/max(total_time, 1):.1f} msgs/sec", inline=True)

    await status_msg.edit(content="", embed=embed)

@bot.tree.command(name="setsimilarity", description="Set global similarity threshold (Admin only)")
@app_commands.describe(threshold="Sensitivity 0-10 (lower = stricter)")
@app_commands.checks.has_permissions(administrator=True)
async def setsimilarity(interaction: discord.Interaction, threshold: app_commands.Range[int, 0, 10]):
    """Set global similarity threshold"""
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)

    config = guild_data[guild_id]['config']
    old = config.get('hash_threshold', HASH_THRESHOLD)
    config['hash_threshold'] = threshold

    await save_guild_data(guild_id)
    await interaction.followup.send(f"✅ Global threshold updated: {old} → {threshold}")

@bot.tree.command(name="whitelist", description="Manage channel whitelist (Admin only)")
@app_commands.describe(channel="Channel to whitelist (leave empty to view list)")
@app_commands.checks.has_permissions(administrator=True)
async def whitelist(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    """Manage channel whitelist"""
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)

    config = guild_data[guild_id]['config']

    if channel is None:
        if config['whitelist']:
            channels = [f"<#{c}>" for c in config['whitelist']]
            await interaction.followup.send(f"📝 Whitelisted channels: {', '.join(channels)}")
        else:
            await interaction.followup.send("📝 No channels whitelisted (monitoring all non-blacklisted)")
        return

    if channel.id in config['whitelist']:
        config['whitelist'].remove(channel.id)
        await interaction.followup.send(f"✅ Removed {channel.mention} from whitelist")
    else:
        config['whitelist'].add(channel.id)
        await interaction.followup.send(f"✅ Added {channel.mention} to whitelist")

    await save_guild_data(guild_id)

@bot.tree.command(name="blacklist", description="Manage channel blacklist (Admin only)")
@app_commands.describe(channel="Channel to blacklist (leave empty to view list)")
@app_commands.checks.has_permissions(administrator=True)
async def blacklist(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    """Manage channel blacklist"""
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)

    config = guild_data[guild_id]['config']

    if channel is None:
        if config['blacklist']:
            channels = [f"<#{c}>" for c in config['blacklist']]
            await interaction.followup.send(f"🚫 Blacklisted channels: {', '.join(channels)}")
        else:
            await interaction.followup.send("🚫 No channels blacklisted")
        return

    if channel.id in config['blacklist']:
        config['blacklist'].remove(channel.id)
        await interaction.followup.send(f"✅ Removed {channel.mention} from blacklist")
    else:
        config['blacklist'].add(channel.id)
        await interaction.followup.send(f"✅ Added {channel.mention} to blacklist")

    await save_guild_data(guild_id)

@bot.tree.command(name="whitelistuser", description="Whitelist user - ignore their images (Admin only)")
@app_commands.describe(user="User to whitelist (leave empty to view list)")
@app_commands.checks.has_permissions(administrator=True)
async def whitelistuser(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    """Whitelist user"""
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)

    config = guild_data[guild_id]['config']

    if user is None:
        if config['user_whitelist']:
            users = [f"<@{u}>" for u in config['user_whitelist']]
            await interaction.followup.send(f"👥 Whitelisted users: {', '.join(users)}")
        else:
            await interaction.followup.send("👥 No users whitelisted")
        return

    if user.id in config['user_whitelist']:
        config['user_whitelist'].remove(user.id)
        await interaction.followup.send(f"✅ Removed {user.mention} from whitelist")
    else:
        config['user_whitelist'].add(user.id)
        await interaction.followup.send(f"✅ Added {user.mention} to whitelist (images will be ignored)")

    await save_guild_data(guild_id)

@bot.tree.command(name="setchannelthreshold", description="Set per-channel similarity threshold (Admin only)")
@app_commands.describe(
    channel="Channel to configure",
    threshold="Sensitivity 0-10 (lower = stricter)"
)
@app_commands.checks.has_permissions(administrator=True)
async def setchannelthreshold(interaction: discord.Interaction, channel: discord.TextChannel, threshold: app_commands.Range[int, 0, 10]):
    """Set per-channel threshold"""
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)

    config = guild_data[guild_id]['config']
    config['channel_thresholds'][str(channel.id)] = threshold

    await save_guild_data(guild_id)
    await interaction.followup.send(f"✅ Set {channel.mention} threshold to {threshold}")

@bot.tree.command(name="setnotifications", description="Set notification mode (Admin only)")
@app_commands.describe(
    mode="Notification mode",
    target="User to mention (only for user mode)"
)
@app_commands.choices(mode=[
    app_commands.Choice(name="Role (mention admin role)", value="role"),
    app_commands.Choice(name="User (mention specific user)", value="user"),
    app_commands.Choice(name="Silent (no mentions)", value="silent")
])
@app_commands.checks.has_permissions(administrator=True)
async def setnotifications(interaction: discord.Interaction, mode: str, target: Optional[discord.Member] = None):
    """Set notification mode"""
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)

    config = guild_data[guild_id]['config']
    config['notification_settings'][str(guild_id)] = {'type': mode}

    if mode == 'user' and target:
        config['notification_settings'][str(guild_id)]['user_id'] = target.id
        await interaction.followup.send(f"✅ Notifications set to mention {target.mention}")
    else:
        await interaction.followup.send(f"✅ Notifications set to: {mode}")

    await save_guild_data(guild_id)

@bot.tree.command(name="toggleautodelete", description="Toggle automatic deletion of duplicates (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def toggleautodelete(interaction: discord.Interaction):
    """Toggle auto-delete"""
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in guild_data:
        await load_guild_data(guild_id)

    config = guild_data[guild_id]['config']
    config['auto_delete'] = not config.get('auto_delete', AUTO_DELETE_DUPLICATES)
    status = "ON ✅" if config['auto_delete'] else "OFF ❌"

    await save_guild_data(guild_id)
    await interaction.followup.send(f"🗑️ Auto-delete duplicates: {status}")

@bot.tree.command(name="cleardata", description="Clear all duplicate detection data for this server (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def cleardata(interaction: discord.Interaction):
    """Clear all duplicate data"""
    await interaction.response.defer()

    view = ConfirmView()
    await interaction.followup.send(
        "⚠️ **WARNING**: This will delete ALL image hashes and statistics for this server!\n"
        "Are you sure you want to continue? This cannot be undone!",
        view=view
    )

    await view.wait()

    if view.value:
        try:
            hashes = await get_all_guild_hashes(interaction.guild_id)
            count = sum(len(entries) for entries in hashes.values())
            await clear_guild_data(interaction.guild_id)
            await interaction.edit_original_response(
                content=f"✅ Database cleared for this server!\nRemoved {count:,} image records.",
                view=None
            )
        except Exception as e:
            logger.error(f"Clear data error: {e}")
            await interaction.edit_original_response(
                content=f"❌ Error clearing data: {str(e)}",
                view=None
            )
    else:
        await interaction.edit_original_response(
            content="❌ Cancelled - no data was deleted.",
            view=None
        )

@bot.tree.command(name="cachestats", description="Show cache statistics (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def cachestats(interaction: discord.Interaction):
    """Show cache statistics"""
    await interaction.response.defer()

    embed = discord.Embed(title="💾 Cache Statistics", color=discord.Color.blue())
    embed.add_field(name="Cache Size", value=f"{hash_cache.size():,} entries", inline=True)
    embed.add_field(name="Max Size", value=f"{MAX_CACHE_SIZE:,}", inline=True)
    embed.add_field(name="Usage", value=f"{(hash_cache.size()/MAX_CACHE_SIZE*100):.1f}%", inline=True)
    embed.add_field(name="DB Pool", value=f"{DB_POOL_SIZE} connections", inline=True)
    embed.add_field(name="Rate Limit", value=f"{RATE_LIMIT_DELAY}s delay", inline=True)
    embed.add_field(name="Max Downloads", value=f"{MAX_CONCURRENT_DOWNLOADS} concurrent", inline=True)

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="resumescan", description="Check for incomplete scans to resume (Admin only)")
@app_commands.describe(channel="Channel to check (leave empty for current channel)")
@app_commands.checks.has_permissions(administrator=True)
async def resumescan(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    """Check for incomplete scans"""
    await interaction.response.defer()

    if channel is None:
        channel = interaction.channel

    progress = await get_scan_progress(interaction.guild_id, channel.id)

    if progress and progress[0]:
        embed = discord.Embed(
            title="📋 Scan Progress Found",
            description=f"Found incomplete scan in {channel.mention}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Last Message ID", value=str(progress[0]), inline=True)
        embed.add_field(name="Processed", value=f"{progress[1]:,} messages", inline=True)
        embed.add_field(
            name="Resume",
            value=f"Use `/scanhistory channel:{channel.mention}` to resume",
            inline=False
        )
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send(f"✅ No incomplete scans found for {channel.mention}")

@bot.tree.command(name="dbinfo", description="Show database information (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def dbinfo(interaction: discord.Interaction):
    """Show database info"""
    await interaction.response.defer()

    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT name, COUNT(*) as count FROM ("
                "SELECT 'image_hashes' as name, guild_id FROM image_hashes WHERE guild_id = ? "
                "UNION ALL "
                "SELECT 'user_stats', guild_id FROM user_stats WHERE guild_id = ? "
                "UNION ALL "
                "SELECT 'duplicate_history', guild_id FROM duplicate_history WHERE guild_id = ?"
                ") GROUP BY name",
                (interaction.guild_id, interaction.guild_id, interaction.guild_id)
            ) as cursor:
                rows = await cursor.fetchall()

        db_size = os.path.getsize(DB_FILE) / (1024 * 1024)

        embed = discord.Embed(title="🗄️ Database Information", color=discord.Color.blue())

        for row in rows:
            embed.add_field(name=row[0], value=f"{row[1]:,} records", inline=True)

        embed.add_field(name="Database Size", value=f"{db_size:.2f} MB", inline=True)
        embed.add_field(name="Pool Size", value=f"{DB_POOL_SIZE} connections", inline=True)
        embed.add_field(name="WAL Mode", value="Enabled ✅", inline=True)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"DB info error: {e}")
        await interaction.followup.send(f"❌ Error getting database info: {str(e)}")

@bot.tree.command(name="optimize", description="Optimize database (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def optimize(interaction: discord.Interaction):
    """Optimize database"""
    await interaction.response.defer()

    try:
        async with db_pool.acquire() as db:
            await db.execute('VACUUM')
            await db.execute('ANALYZE')
            await db.commit()

        await interaction.followup.send("✅ Database optimized successfully!")
    except Exception as e:
        logger.error(f"Optimize error: {e}")
        await interaction.followup.send(f"❌ Optimization failed: {str(e)}")

# Confirmation View for cleardata command
class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global error handler for slash commands"""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need administrator permissions to use this command!", ephemeral=True)
    elif isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"⏱️ Command on cooldown. Try again in {error.retry_after:.1f}s", ephemeral=True)
    elif isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
    else:
        logger.error(f"Command error: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message(f"❌ An error occurred: {str(error)[:100]}", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ An error occurred: {str(error)[:100]}", ephemeral=True)

@bot.event
async def on_disconnect():
    """Handle disconnect gracefully"""
    logger.warning("Bot disconnected - saving data...")
    for guild_id in list(guild_data.keys()):
        await save_guild_data(guild_id)

async def shutdown():
    """Graceful shutdown"""
    logger.info("Shutting down gracefully...")

    if auto_save.is_running():
        auto_save.cancel()

    for guild_id in list(guild_data.keys()):
        await save_guild_data(guild_id)

    await db_pool.close_all()

    logger.info("Shutdown complete")

if __name__ == "__main__":
    logger.info("🚀 Starting Production Duplicate Image Detector Bot with Slash Commands...")
    logger.info("=" * 60)
    logger.info(f"Configuration:")
    logger.info(f"  - Max Cache Size: {MAX_CACHE_SIZE:,}")
    logger.info(f"  - DB Pool Size: {DB_POOL_SIZE}")
    logger.info(f"  - Max Concurrent Downloads: {MAX_CONCURRENT_DOWNLOADS}")
    logger.info(f"  - Rate Limit Delay: {RATE_LIMIT_DELAY}s")
    logger.info(f"  - Batch Commit Size: {BATCH_COMMIT_SIZE}")
    logger.info(f"  - Download Timeout: {DOWNLOAD_TIMEOUT}s")
    logger.info("=" * 60)

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Login failed! Check token and enable MESSAGE CONTENT INTENT in Discord Developer Portal.")
    except KeyboardInterrupt:
        logger.info("\n⚠️ Keyboard interrupt detected")
        asyncio.run(shutdown())
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        asyncio.run(shutdown())
    finally:
        logger.info("Bot stopped.")