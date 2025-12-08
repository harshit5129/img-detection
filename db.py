"""
Enhanced database layer with warnings, reports, analytics, and OCR support.
"""
import os
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

import aiosqlite

logger = logging.getLogger("DuplicateDetector")

DATA_DIR = "bot_data"
DB_FILE = os.path.join(DATA_DIR, "bot_data.db")
os.makedirs(DATA_DIR, exist_ok=True)

_db_pool: Optional[aiosqlite.Connection] = None
_pool_lock = asyncio.Lock()

@asynccontextmanager
async def get_db():
    """Get database connection with proper error handling."""
    global _db_pool
    
    try:
        async with _pool_lock:
            if _db_pool is None:
                _db_pool = await aiosqlite.connect(DB_FILE)
                _db_pool.row_factory = aiosqlite.Row
                await _db_pool.execute("PRAGMA journal_mode=WAL")
                await _db_pool.execute("PRAGMA synchronous=NORMAL")
                await _db_pool.execute("PRAGMA cache_size=10000")
                await _db_pool.execute("PRAGMA temp_store=MEMORY")
        
        yield _db_pool
        
    except Exception as e:
        logger.error(f"Database connection error: {e}", exc_info=True)
        raise

def init_db_sync():
    """Initialize database with ALL tables for enhanced features."""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        
        # Image hashes table
        c.execute("""
            CREATE TABLE IF NOT EXISTS image_hashes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                hash_value TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                image_url TEXT NOT NULL,
                ocr_text TEXT,
                ocr_confidence REAL,
                UNIQUE(guild_id, hash_value, message_id)
            )
        """)
        
        c.execute("CREATE INDEX IF NOT EXISTS idx_image_hashes_guild ON image_hashes(guild_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_image_hashes_message ON image_hashes(guild_id, message_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_image_hashes_user ON image_hashes(guild_id, user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_image_hashes_hash ON image_hashes(guild_id, hash_value)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_image_hashes_ocr ON image_hashes(guild_id, ocr_text)")
        
        # User stats table
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                unique_count INTEGER DEFAULT 0,
                duplicate_count INTEGER DEFAULT 0,
                ocr_duplicate_count INTEGER DEFAULT 0,
                last_updated TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        
        c.execute("CREATE INDEX IF NOT EXISTS idx_user_stats_guild ON user_stats(guild_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_user_stats_duplicates ON user_stats(guild_id, duplicate_count DESC)")
        
        # Guild config table
        c.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                whitelist TEXT DEFAULT '[]',
                blacklist TEXT DEFAULT '[]',
                user_whitelist TEXT DEFAULT '[]',
                channel_thresholds TEXT DEFAULT '{}',
                notification_settings TEXT DEFAULT '{}',
                hash_threshold INTEGER DEFAULT 5,
                ocr_threshold INTEGER DEFAULT 85,
                auto_delete INTEGER DEFAULT 0,
                ocr_enabled INTEGER DEFAULT 1,
                reaction_settings TEXT DEFAULT '{}',
                warning_config TEXT DEFAULT '{}',
                quarantine_config TEXT DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        
        # User warnings table
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                warning_type TEXT NOT NULL,
                reason TEXT,
                action_taken TEXT,
                issued_at TEXT NOT NULL,
                issued_by INTEGER,
                UNIQUE(guild_id, user_id, issued_at)
            )
        """)
        
        c.execute("CREATE INDEX IF NOT EXISTS idx_warnings_user ON user_warnings(guild_id, user_id)")
        
        # Duplicate history table (enhanced)
        c.execute("""
            CREATE TABLE IF NOT EXISTS duplicate_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                similarity_count INTEGER NOT NULL,
                similarity_percent REAL,
                ocr_similarity_percent REAL,
                original_message_id INTEGER,
                action_taken TEXT,
                detection_method TEXT
            )
        """)
        
        c.execute("CREATE INDEX IF NOT EXISTS idx_duplicate_history_guild ON duplicate_history(guild_id, timestamp DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_duplicate_history_user ON duplicate_history(guild_id, user_id)")
        
        # Add new columns to existing tables (safe migrations)
        try:
            c.execute("ALTER TABLE duplicate_history ADD COLUMN ocr_similarity_percent REAL")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE duplicate_history ADD COLUMN detection_method TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE image_hashes ADD COLUMN ocr_text TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE image_hashes ADD COLUMN ocr_confidence REAL")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE user_stats ADD COLUMN ocr_duplicate_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        
        # Daily statistics table
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                guild_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                total_images INTEGER DEFAULT 0,
                duplicates_detected INTEGER DEFAULT 0,
                ocr_duplicates_detected INTEGER DEFAULT 0,
                unique_posters INTEGER DEFAULT 0,
                top_duplicate_user INTEGER,
                PRIMARY KEY (guild_id, date)
            )
        """)
        
        c.execute("CREATE INDEX IF NOT EXISTS idx_daily_stats_guild ON daily_stats(guild_id, date DESC)")
        
        # Quarantine table
        c.execute("""
            CREATE TABLE IF NOT EXISTS quarantine (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                image_url TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                reason TEXT,
                released BOOLEAN DEFAULT 0,
                release_timestamp TEXT
            )
        """)
        
        c.execute("CREATE INDEX IF NOT EXISTS idx_quarantine_guild ON quarantine(guild_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_quarantine_released ON quarantine(released)")
        
        conn.commit()
        conn.close()
        
        logger.info("Enhanced database initialized with all features")
        
    except Exception as e:
        logger.error(f"Database initialization error: {e}", exc_info=True)
        raise

# ---------- Guild Config ----------
async def fetch_guild_config_row(guild_id: int) -> Optional[aiosqlite.Row]:
    """Fetch guild configuration from database."""
    try:
        async with get_db() as db:
            async with db.execute("""
                SELECT whitelist, blacklist, user_whitelist, channel_thresholds,
                       notification_settings, hash_threshold, ocr_threshold,
                       auto_delete, ocr_enabled, reaction_settings,
                       warning_config, quarantine_config
                FROM guild_config
                WHERE guild_id = ?
            """, (guild_id,)) as cur:
                return await cur.fetchone()
    except Exception as e:
        logger.error(f"Error fetching guild config: {e}")
        return None

async def upsert_guild_config_row(
    guild_id: int,
    whitelist: str,
    blacklist: str,
    user_whitelist: str,
    channel_thresholds: str,
    notification_settings: str,
    hash_threshold: int,
    ocr_threshold: int,
    auto_delete: int,
    ocr_enabled: int,
    reaction_settings: str,
    warning_config: str,
    quarantine_config: str,
) -> None:
    """Insert or update guild configuration."""
    try:
        timestamp = datetime.utcnow().isoformat()
        
        async with get_db() as db:
            async with db.execute(
                "SELECT 1 FROM guild_config WHERE guild_id = ?", 
                (guild_id,)
            ) as cur:
                exists = await cur.fetchone()
            
            if exists:
                await db.execute("""
                    UPDATE guild_config
                    SET whitelist = ?, blacklist = ?, user_whitelist = ?,
                        channel_thresholds = ?, notification_settings = ?,
                        hash_threshold = ?, ocr_threshold = ?,
                        auto_delete = ?, ocr_enabled = ?,
                        reaction_settings = ?, warning_config = ?,
                        quarantine_config = ?,
                        updated_at = ?
                    WHERE guild_id = ?
                """, (
                    whitelist, blacklist, user_whitelist,
                    channel_thresholds, notification_settings,
                    hash_threshold, ocr_threshold,
                    auto_delete, ocr_enabled,
                    reaction_settings, warning_config,
                    quarantine_config,
                    timestamp, guild_id
                ))
            else:
                await db.execute("""
                    INSERT INTO guild_config
                    (guild_id, whitelist, blacklist, user_whitelist,
                     channel_thresholds, notification_settings,
                     hash_threshold, ocr_threshold,
                     auto_delete, ocr_enabled,
                     reaction_settings, warning_config,
                     quarantine_config,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    guild_id, whitelist, blacklist, user_whitelist,
                    channel_thresholds, notification_settings,
                    hash_threshold, ocr_threshold,
                    auto_delete, ocr_enabled,
                    reaction_settings, warning_config,
                    quarantine_config,
                    timestamp, timestamp
                ))
            
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error upserting guild config: {e}")
        raise

# ---------- Image Hashes ----------
async def image_already_processed(guild_id: int, message_id: int) -> bool:
    """Check if message has already been processed."""
    try:
        async with get_db() as db:
            async with db.execute("""
                SELECT 1 FROM image_hashes
                WHERE guild_id = ? AND message_id = ?
                LIMIT 1
            """, (guild_id, message_id)) as cur:
                return (await cur.fetchone()) is not None
    except Exception as e:
        logger.error(f"Error checking processed status: {e}")
        return False

async def save_image_hash(
    guild_id: int,
    hash_value: str,
    message_id: int,
    channel_id: int,
    user_id: int,
    image_url: str,
    ocr_text: Optional[str] = None,
    ocr_confidence: Optional[float] = None,
) -> None:
    """Save image hash to database."""
    try:
        timestamp = datetime.utcnow().isoformat()
        
        async with get_db() as db:
            await db.execute("""
                INSERT OR IGNORE INTO image_hashes
                (guild_id, hash_value, message_id, channel_id, user_id, timestamp, image_url, ocr_text, ocr_confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                guild_id, hash_value, message_id,
                channel_id, user_id, timestamp, image_url,
                ocr_text, ocr_confidence
            ))
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error saving image hash: {e}")
        raise

async def get_all_hashes_for_guild(guild_id: int) -> List[Dict[str, Any]]:
    """Get all image hashes for a guild."""
    try:
        async with get_db() as db:
            async with db.execute("""
                SELECT hash_value, channel_id, message_id, user_id, timestamp, image_url, ocr_text, ocr_confidence
                FROM image_hashes
                WHERE guild_id = ?
                ORDER BY timestamp DESC
            """, (guild_id,)) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching hashes: {e}")
        return []

async def get_image_by_hash(guild_id: int, hash_value: str) -> Optional[Dict[str, Any]]:
    """Get image by hash value."""
    try:
        async with get_db() as db:
            async with db.execute("""
                SELECT * FROM image_hashes
                WHERE guild_id = ? AND hash_value = ?
                ORDER BY timestamp ASC
                LIMIT 1
            """, (guild_id, hash_value)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error fetching image by hash: {e}")
        return None

async def get_similar_ocr_text(
    guild_id: int, 
    text: str, 
    similarity_threshold: int = 85
) -> List[Dict[str, Any]]:
    """Get images with similar OCR text."""
    try:
        if not text or len(text) < 10:  # Minimum text length for meaningful comparison
            return []
            
        async with get_db() as db:
            # Use LIKE for simple pattern matching
            # For better results, we'd need FTS but this is a good start
            pattern = f"%{text[:min(20, len(text))]}%"
            
            async with db.execute("""
                SELECT * FROM image_hashes
                WHERE guild_id = ? AND ocr_text LIKE ? AND ocr_text IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 10
            """, (guild_id, pattern)) as cur:
                rows = await cur.fetchall()
                
                # Filter by similarity threshold
                results = []
                for row in rows:
                    if row["ocr_text"]:
                        similarity = calculate_text_similarity(text, row["ocr_text"])
                        if similarity >= similarity_threshold:
                            result = dict(row)
                            result["ocr_similarity"] = similarity
                            results.append(result)
                
                return results
    except Exception as e:
        logger.error(f"Error fetching similar OCR text: {e}")
        return []

def calculate_text_similarity(text1: str, text2: str) -> float:
    """Calculate similarity between two text strings."""
    if not text1 or not text2:
        return 0.0
        
    # Simple character-based similarity
    set1 = set(text1.lower())
    set2 = set(text2.lower())
    
    intersection = set1 & set2
    union = set1 | set2
    
    if not union:
        return 0.0
        
    return (len(intersection) / len(union)) * 100

# ---------- User Stats ----------
async def update_user_stats(
    guild_id: int,
    user_id: int,
    unique_delta: int,
    duplicate_delta: int,
    ocr_duplicate_delta: int = 0,
) -> None:
    """Update user statistics."""
    try:
        timestamp = datetime.utcnow().isoformat()
        
        async with get_db() as db:
            await db.execute("""
                INSERT OR IGNORE INTO user_stats
                (guild_id, user_id, unique_count, duplicate_count, ocr_duplicate_count, last_updated)
                VALUES (?, ?, 0, 0, 0, ?)
            """, (guild_id, user_id, timestamp))
            
            await db.execute("""
                UPDATE user_stats
                SET unique_count = unique_count + ?,
                    duplicate_count = duplicate_count + ?,
                    ocr_duplicate_count = ocr_duplicate_count + ?,
                    last_updated = ?
                WHERE guild_id = ? AND user_id = ?
            """, (
                unique_delta, duplicate_delta, ocr_duplicate_delta,
                timestamp, guild_id, user_id
            ))
            
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error updating user stats: {e}")
        raise

async def get_user_stats(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Get statistics for a specific user."""
    try:
        async with get_db() as db:
            async with db.execute("""
                SELECT unique_count, duplicate_count, ocr_duplicate_count
                FROM user_stats
                WHERE guild_id = ? AND user_id = ?
            """, (guild_id, user_id)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error fetching user stats: {e}")
        return None

async def get_leaderboard(guild_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Get leaderboard of users with most duplicates."""
    try:
        async with get_db() as db:
            async with db.execute("""
                SELECT user_id, unique_count, duplicate_count, ocr_duplicate_count
                FROM user_stats
                WHERE guild_id = ? AND (unique_count > 0 OR duplicate_count > 0)
                ORDER BY duplicate_count DESC, ocr_duplicate_count DESC, unique_count DESC
                LIMIT ?
            """, (guild_id, limit)) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        return []

# ---------- User Warnings ----------
async def add_user_warning(
    guild_id: int,
    user_id: int,
    warning_type: str,
    reason: str,
    action_taken: Optional[str] = None,
    issued_by: Optional[int] = None
) -> None:
    """Add a warning to a user."""
    try:
        timestamp = datetime.utcnow().isoformat()
        
        async with get_db() as db:
            await db.execute("""
                INSERT INTO user_warnings
                (guild_id, user_id, warning_type, reason, action_taken, issued_at, issued_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (guild_id, user_id, warning_type, reason, action_taken, timestamp, issued_by))
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error adding warning: {e}")

async def get_user_warnings(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
    """Get warning history for a user."""
    try:
        async with get_db() as db:
            async with db.execute("""
                SELECT * FROM user_warnings
                WHERE guild_id = ? AND user_id = ?
                ORDER BY issued_at DESC
            """, (guild_id, user_id)) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching warnings: {e}")
        return []

async def clear_user_warnings(guild_id: int, user_id: int) -> None:
    """Clear all warnings for a user."""
    try:
        async with get_db() as db:
            await db.execute("""
                DELETE FROM user_warnings
                WHERE guild_id = ? AND user_id = ?
            """, (guild_id, user_id))
            await db.commit()
    except Exception as e:
        logger.error(f"Error clearing warnings: {e}")

# ---------- Duplicate History ----------
async def insert_duplicate_history(
    guild_id: int,
    user_id: int,
    user_name: str,
    channel_id: int,
    message_id: int,
    similarity_count: int,
    similarity_percent: Optional[float] = None,
    ocr_similarity_percent: Optional[float] = None,
    original_message_id: Optional[int] = None,
    action_taken: Optional[str] = None,
    detection_method: str = "hash"
) -> None:
    """Record duplicate detection event."""
    try:
        timestamp = datetime.utcnow().isoformat()
        
        async with get_db() as db:
            await db.execute("""
                INSERT INTO duplicate_history
                (guild_id, timestamp, user_id, user_name,
                 channel_id, message_id, similarity_count,
                 similarity_percent, ocr_similarity_percent,
                 original_message_id, action_taken, detection_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                guild_id, timestamp, user_id, user_name,
                channel_id, message_id, similarity_count,
                similarity_percent, ocr_similarity_percent,
                original_message_id, action_taken, detection_method
            ))
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error inserting duplicate history: {e}")

async def get_recent_duplicates(guild_id: int, hours: int = 24, limit: int = 50) -> List[Dict[str, Any]]:
    """Get recent duplicates within timeframe."""
    try:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        
        async with get_db() as db:
            async with db.execute("""
                SELECT * FROM duplicate_history
                WHERE guild_id = ? AND timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (guild_id, cutoff, limit)) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching recent duplicates: {e}")
        return []

# ---------- Guild Stats ----------
async def get_guild_stats(guild_id: int) -> Dict[str, Any]:
    """Get aggregated statistics for a guild."""
    result = {
        "total_images": 0,
        "total_messages": 0,
        "total_unique": 0,
        "total_duplicates": 0,
        "total_ocr_duplicates": 0,
        "total_warnings": 0,
    }
    
    try:
        async with get_db() as db:
            # Image counts
            async with db.execute("""
                SELECT COUNT(*) AS c, COUNT(DISTINCT message_id) AS m
                FROM image_hashes
                WHERE guild_id = ?
            """, (guild_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    result["total_images"] = row["c"] or 0
                    result["total_messages"] = row["m"] or 0
            
            # User stats
            async with db.execute("""
                SELECT 
                    COALESCE(SUM(unique_count), 0) AS su,
                    COALESCE(SUM(duplicate_count), 0) AS sd,
                    COALESCE(SUM(ocr_duplicate_count), 0) AS sod
                FROM user_stats
                WHERE guild_id = ?
            """, (guild_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    result["total_unique"] = row["su"] or 0
                    result["total_duplicates"] = row["sd"] or 0
                    result["total_ocr_duplicates"] = row["sod"] or 0
            
            # Warning counts
            async with db.execute("""
                SELECT COUNT(*) as count
                FROM user_warnings
                WHERE guild_id = ?
            """, (guild_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    result["total_warnings"] = row["count"] or 0
        
    except Exception as e:
        logger.error(f"Error fetching guild stats: {e}")
    
    return result

# ---------- Daily Stats ----------
async def update_daily_stats(guild_id: int) -> None:
    """Update daily statistics."""
    try:
        today = datetime.utcnow().date().isoformat()
        
        async with get_db() as db:
            # Count today's activity
            async with db.execute("""
                SELECT COUNT(*) as count
                FROM image_hashes
                WHERE guild_id = ? AND date(timestamp) = ?
            """, (guild_id, today)) as cur:
                row = await cur.fetchone()
                total_images = row["count"] if row else 0
            
            async with db.execute("""
                SELECT COUNT(*) as count
                FROM duplicate_history
                WHERE guild_id = ? AND date(timestamp) = ?
            """, (guild_id, today)) as cur:
                row = await cur.fetchone()
                duplicates = row["count"] if row else 0
            
            # Get OCR duplicates
            async with db.execute("""
                SELECT COUNT(*) as count
                FROM duplicate_history
                WHERE guild_id = ? AND date(timestamp) = ? AND detection_method = 'ocr'
            """, (guild_id, today)) as cur:
                row = await cur.fetchone()
                ocr_duplicates = row["count"] if row else 0
            
            # Insert or update
            await db.execute("""
                INSERT OR REPLACE INTO daily_stats
                (guild_id, date, total_images, duplicates_detected, ocr_duplicates_detected)
                VALUES (?, ?, ?, ?, ?)
            """, (guild_id, today, total_images, duplicates, ocr_duplicates))
            
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error updating daily stats: {e}")

# ---------- Quarantine Management ----------
async def quarantine_message(
    guild_id: int,
    message_id: int,
    channel_id: int,
    user_id: int,
    image_url: str,
    reason: str
) -> None:
    """Quarantine a message."""
    try:
        timestamp = datetime.utcnow().isoformat()
        
        async with get_db() as db:
            await db.execute("""
                INSERT INTO quarantine
                (guild_id, message_id, channel_id, user_id, image_url, timestamp, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (guild_id, message_id, channel_id, user_id, image_url, timestamp, reason))
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error quarantining message: {e}")

async def release_quarantined_message(
    guild_id: int,
    message_id: int,
    admin_id: int
) -> bool:
    """Release a quarantined message."""
    try:
        timestamp = datetime.utcnow().isoformat()
        
        async with get_db() as db:
            await db.execute("""
                UPDATE quarantine
                SET released = 1, release_timestamp = ?, released_by = ?
                WHERE guild_id = ? AND message_id = ?
            """, (timestamp, admin_id, guild_id, message_id))
            
            rows = await db.total_changes()
            await db.commit()
            
            return rows > 0
            
    except Exception as e:
        logger.error(f"Error releasing message: {e}")
        return False

async def get_quarantined_messages(
    guild_id: int,
    limit: int = 20,
    unreleased_only: bool = True
) -> List[Dict[str, Any]]:
    """Get quarantined messages."""
    try:
        query = """
            SELECT * FROM quarantine
            WHERE guild_id = ?
        """
        params = [guild_id]
        
        if unreleased_only:
            query += " AND released = 0"
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        async with get_db() as db:
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error getting quarantined messages: {e}")
        return []
