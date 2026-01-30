import aiosqlite
import sqlite3
import json
import asyncio
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any
from config import ReactionConfig, logger, DATA_DIR, DB_FILE
import config

# Ensure directory exists
os.makedirs(DATA_DIR, exist_ok=True)

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

            # Setup WAL mode
            conn = await aiosqlite.connect(self.db_path)
            await conn.execute('PRAGMA journal_mode=WAL')
            await conn.execute('PRAGMA synchronous=NORMAL')
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

def init_db_sync():
    """Initialize SQLite database with optimized schema"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=NORMAL')

            # Image hashes table
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

            # User statistics
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_stats (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    unique_count INTEGER DEFAULT 0,
                    duplicate_count INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
            ''')

            # Guild configuration
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
                    auto_reactions TEXT DEFAULT '{}'
                )
            ''')

            # Duplicate history
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

            # Scan progress
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scan_progress (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    last_message_id INTEGER,
                    total_processed INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, channel_id)
                )
            ''')

            # Indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_guild_hash ON image_hashes(guild_id, hash_value)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_guild_message ON image_hashes(guild_id, message_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_guild_user_stats ON user_stats(guild_id, user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_duplicate_history_guild ON duplicate_history(guild_id, timestamp DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_scan_progress ON scan_progress(guild_id, channel_id)')

            conn.commit()
            logger.info("Database initialized with indexes")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

async def load_guild_data(guild_id: int) -> bool:
    """Load guild configuration from database"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT whitelist, blacklist, user_whitelist, channel_thresholds, 
                       notification_settings, hash_threshold, auto_delete, auto_reactions
                FROM guild_config WHERE guild_id = ?
            ''', (guild_id,)) as cursor:
                row = await cursor.fetchone()

                if row:
                    reactions = json.loads(row[7]) if row[7] else {}
                    parsed_reactions = {}
                    for k, v in reactions.items():
                        if isinstance(v, dict):
                            parsed_reactions[k] = ReactionConfig.from_dict(v)
                        else:
                            parsed_reactions[k] = v

                    config.guild_data[guild_id] = {
                        'config': {
                            'whitelist': set(json.loads(row[0])) if row[0] else set(),
                            'blacklist': set(json.loads(row[1])) if row[1] else set(),
                            'user_whitelist': set(json.loads(row[2])) if row[2] else set(),
                            'channel_thresholds': json.loads(row[3]) if row[3] else {},
                            'notification_settings': json.loads(row[4]) if row[4] else {},
                            'hash_threshold': row[5] if row[5] is not None else 5,
                            'auto_delete': bool(row[6]) if row[6] is not None else False,
                            'auto_reactions': parsed_reactions
                        }
                    }
                else:
                    config.guild_data[guild_id] = {
                        'config': {
                            'whitelist': set(),
                            'blacklist': set(),
                            'user_whitelist': set(),
                            'channel_thresholds': {},
                            'notification_settings': {},
                            'hash_threshold': 5,
                            'auto_delete': False,
                            'auto_reactions': {}
                        }
                    }
        return True
    except Exception as e:
        logger.error(f"Load error for guild {guild_id}: {e}")
        return False

async def save_guild_data(guild_id: int) -> bool:
    """Save guild configuration to database"""
    try:
        cfg = config.guild_data.get(guild_id, {}).get('config', {})
        
        # Serialize reaction configs
        reactions = cfg.get('auto_reactions', {})
        serialized_reactions = {}
        for k, v in reactions.items():
            if isinstance(v, ReactionConfig):
                serialized_reactions[k] = v.to_dict()
            else:
                serialized_reactions[k] = v

        async with db_pool.acquire() as db:
            await db.execute('''
                INSERT OR REPLACE INTO guild_config 
                (guild_id, whitelist, blacklist, user_whitelist, channel_thresholds, 
                 notification_settings, hash_threshold, auto_delete, auto_reactions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                guild_id,
                json.dumps(list(cfg.get('whitelist', set()))),
                json.dumps(list(cfg.get('blacklist', set()))),
                json.dumps(list(cfg.get('user_whitelist', set()))),
                json.dumps(cfg.get('channel_thresholds', {})),
                json.dumps(cfg.get('notification_settings', {})),
                cfg.get('hash_threshold', 5),
                1 if cfg.get('auto_delete', False) else 0,
                json.dumps(serialized_reactions)
            ))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Save error for guild {guild_id}: {e}")
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

        # Update cache
        from cache import hash_cache
        for data in batch_data:
            guild_id, hash_str, message_id, channel_id, user_id, timestamp, image_url = data
            hash_key = f"{guild_id}_{hash_str}"
            cached = await hash_cache.get(hash_key)
            if cached is None:
                cached = []
            cached.append((message_id, channel_id, user_id, timestamp, image_url))
            await hash_cache.put(hash_key, cached)

        return True
    except Exception as e:
        logger.error(f"Save batch error: {e}")
        return False

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
        logger.error(f"Update stats error: {e}")
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
        logger.error(f"Get stats error: {e}")
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
        logger.error(f"Add history error: {e}")
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
        logger.error(f"Get history error: {e}")
        return []

async def get_all_guild_hashes(guild_id: int) -> Dict:
    """Get all image hashes for a guild"""
    try:
        hashes = {}
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT hash_value, message_id, channel_id, user_id, timestamp, image_url
                FROM image_hashes WHERE guild_id = ?
            ''', (guild_id,)) as cursor:
                async for row in cursor:
                    try:
                        import imagehash as ih
                        hash_obj = ih.hex_to_hash(row[0])
                        if hash_obj not in hashes:
                            hashes[hash_obj] = []
                        hashes[hash_obj].append((row[1], row[2], row[3], row[4], row[5]))
                    except:
                        continue
        return hashes
    except Exception as e:
        logger.error(f"Get hashes error: {e}")
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

        from cache import hash_cache
        await hash_cache.clear_guild(guild_id)
        logger.info(f"Cleared data for guild {guild_id}")
        return True
    except Exception as e:
        logger.error(f"Clear error: {e}")
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
        logger.error(f"Save progress error: {e}")
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
        logger.error(f"Get progress error: {e}")
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

async def get_db_size():
    """Get database file size in MB"""
    try:
        size = os.path.getsize(DB_FILE) / (1024 * 1024)
        return size
    except:
        return 0
