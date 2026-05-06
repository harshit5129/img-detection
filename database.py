import sqlite3
import asyncio
import aiosqlite
import json
from datetime import datetime
from contextlib import asynccontextmanager
import numpy as np

from config import DB_FILE, DB_POOL_SIZE, logger

class DatabasePool:
    def __init__(self, db_path: str, pool_size: int):
        self.db_path = db_path
        self.pool_size = pool_size
        self.pool = []
        self.available = asyncio.Queue()
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

def init_db_sync():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA synchronous=NORMAL')

            c.execute('''
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    url TEXT NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    format TEXT,
                    size_mb REAL,
                    embedding BLOB,
                    created_at TEXT,
                    UNIQUE(guild_id, message_id)
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    UNIQUE(image_id, tag)
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    whitelist TEXT DEFAULT '[]',
                    blacklist TEXT DEFAULT '[]',
                    user_whitelist TEXT DEFAULT '[]',
                    mod_roles TEXT DEFAULT '[]',
                    hash_threshold INTEGER DEFAULT 5,
                    auto_delete INTEGER DEFAULT 0,
                    log_channel_id INTEGER
                )
            ''')

            c.execute('CREATE INDEX IF NOT EXISTS idx_img_guild ON images(guild_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_img_user ON images(guild_id, user_id)')
            
            c.execute('CREATE INDEX IF NOT EXISTS idx_tags_image ON tags(image_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag COLLATE NOCASE)')

            conn.commit()
            logger.info("Database initialized")
    except Exception as e:
        logger.error(f"DB init error: {e}")

async def load_guild_config(guild_id: int):
    from config import guild_data
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                'SELECT guild_id, whitelist, blacklist, user_whitelist, mod_roles, hash_threshold, auto_delete, log_channel_id FROM guild_config WHERE guild_id = ?',
                (guild_id,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    guild_data[guild_id] = {
                        'whitelist': set(json.loads(row[1])) if row[1] else set(),
                        'blacklist': set(json.loads(row[2])) if row[2] else set(),
                        'user_whitelist': set(json.loads(row[3])) if row[3] else set(),
                        'mod_roles': set(json.loads(row[4])) if row[4] else set(),
                        'hash_threshold': row[5] if row[5] is not None else 5,
                        'auto_delete': bool(row[6]) if row[6] is not None else False,
                        'log_channel_id': row[7]
                    }
                else:
                    guild_data[guild_id] = {
                        'whitelist': set(),
                        'blacklist': set(),
                        'user_whitelist': set(),
                        'mod_roles': set(),
                        'hash_threshold': 5,
                        'auto_delete': False,
                        'log_channel_id': None
                    }
    except Exception as e:
        logger.error(f"Load config error: {e}")

async def save_guild_config(guild_id: int):
    from config import guild_data
    try:
        cfg = guild_data.get(guild_id, {})
        async with db_pool.acquire() as db:
            await db.execute('''
                INSERT OR REPLACE INTO guild_config
                (guild_id, whitelist, blacklist, user_whitelist, mod_roles, hash_threshold, auto_delete, log_channel_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                guild_id,
                json.dumps(list(cfg.get('whitelist', set()))),
                json.dumps(list(cfg.get('blacklist', set()))),
                json.dumps(list(cfg.get('user_whitelist', set()))),
                json.dumps(list(cfg.get('mod_roles', set()))),
                cfg.get('hash_threshold', 5),
                1 if cfg.get('auto_delete', False) else 0,
                cfg.get('log_channel_id')
            ))
            await db.commit()
    except Exception as e:
        logger.error(f"Save config error: {e}")

async def add_image(guild_id, channel_id, message_id, user_id, username, url,
                    width, height, fmt, size_mb, embedding: np.ndarray) -> int:
    try:
        emb_bytes = embedding.astype(np.float32).tobytes()
        async with db_pool.acquire() as db:
            cur = await db.execute('''
                INSERT OR IGNORE INTO images
                (guild_id, channel_id, message_id, user_id, username, url,
                 width, height, format, size_mb, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (guild_id, channel_id, message_id, user_id, username, url,
                  width, height, fmt, size_mb,
                  emb_bytes, datetime.now().isoformat()))
            await db.commit()
            return cur.lastrowid
    except Exception as e:
        logger.error(f"Add image error: {e}")
        return -1

async def get_image_by_message(guild_id: int, message_id: int):
    try:
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT * FROM images WHERE guild_id = ? AND message_id = ?
            ''', (guild_id, message_id)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"Get image error: {e}")
        return None

async def get_image_by_id(guild_id: int, image_id: int):
    try:
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT * FROM images WHERE guild_id = ? AND id = ?
            ''', (guild_id, image_id)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"Get image error: {e}")
        return None

async def get_images_by_guild(guild_id: int, limit: int = 1000):
    try:
        rows = []
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT * FROM images WHERE guild_id = ? ORDER BY id DESC LIMIT ?
            ''', (guild_id, limit)) as cur:
                async for row in cur:
                    rows.append(dict(row))
        return rows
    except Exception as e:
        logger.error(f"Get guild images error: {e}")
        return []

async def get_images_by_user(guild_id: int, user_id: int, limit: int = 1000):
    try:
        rows = []
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT * FROM images WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?
            ''', (guild_id, user_id, limit)) as cur:
                async for row in cur:
                    rows.append(dict(row))
        return rows
    except Exception as e:
        logger.error(f"Get user images error: {e}")
        return []

async def get_all_guild_embeddings(guild_id: int):
    try:
        rows = []
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT id, message_id, user_id, url, width, height, format, size_mb,
                       username, channel_id, embedding
                FROM images WHERE guild_id = ? AND embedding IS NOT NULL
            ''', (guild_id,)) as cur:
                async for row in cur:
                    d = dict(row)
                    d['embedding'] = np.frombuffer(d['embedding'], dtype=np.float32)
                    rows.append(d)
        return rows
    except Exception as e:
        logger.error(f"Get embeddings error: {e}")
        return []

async def add_tag(image_id: int, tag: str):
    try:
        async with db_pool.acquire() as db:
            await db.execute('INSERT OR IGNORE INTO tags (image_id, tag) VALUES (?, ?)', (image_id, tag))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Add tag error: {e}")
        return False

async def remove_tag(image_id: int, tag: str):
    try:
        async with db_pool.acquire() as db:
            await db.execute('DELETE FROM tags WHERE image_id = ? AND tag = ?', (image_id, tag))
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Remove tag error: {e}")
        return False

async def get_tags(image_id: int):
    try:
        async with db_pool.acquire() as db:
            async with db.execute('SELECT tag FROM tags WHERE image_id = ?', (image_id,)) as cur:
                return [row[0] async for row in cur]
    except Exception as e:
        logger.error(f"Get tags error: {e}")
        return []

async def search_by_tag(guild_id: int, tag: str, limit: int = 100):
    try:
        rows = []
        async with db_pool.acquire() as db:
            async with db.execute('''
                SELECT i.* FROM images i
                JOIN tags t ON i.id = t.image_id
                WHERE i.guild_id = ? AND t.tag = ? COLLATE NOCASE
                ORDER BY i.id DESC LIMIT ?
            ''', (guild_id, tag, limit)) as cur:
                async for row in cur:
                    rows.append(dict(row))
        return rows
    except Exception as e:
        logger.error(f"Tag search error: {e}")
        return []

async def delete_guild_data(guild_id: int):
    try:
        async with db_pool.acquire() as db:
            await db.execute('DELETE FROM images WHERE guild_id = ?', (guild_id,))
            await db.execute('DELETE FROM tags WHERE image_id NOT IN (SELECT id FROM images)')
            await db.execute('DELETE FROM guild_config WHERE guild_id = ?', (guild_id,))
            await db.commit()
    except Exception as e:
        logger.error(f"Delete guild error: {e}")

async def get_db_stats(guild_id: int):
    try:
        async with db_pool.acquire() as db:
            async with db.execute('SELECT COUNT(*) FROM images WHERE guild_id = ?', (guild_id,)) as cur:
                img_count = (await cur.fetchone())[0]
            async with db.execute('SELECT COUNT(DISTINCT tag) FROM tags WHERE image_id IN (SELECT id FROM images WHERE guild_id = ?)', (guild_id,)) as cur:
                tag_count = (await cur.fetchone())[0]
            async with db.execute('SELECT COUNT(DISTINCT user_id) FROM images WHERE guild_id = ?', (guild_id,)) as cur:
                user_count = (await cur.fetchone())[0]
        return {'images': img_count, 'tags': tag_count, 'users': user_count}
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return {'images': 0, 'tags': 0, 'users': 0}
