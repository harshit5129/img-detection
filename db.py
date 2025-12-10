"""
MongoDB database layer with async operations for Discord bot.
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger("DuplicateDetector")

# MongoDB connection
_db_client: Optional[AsyncIOMotorClient] = None
_database: Optional[AsyncIOMotorDatabase] = None

def get_mongodb_uri() -> str:
    """Get MongoDB connection URI from environment."""
    uri = os.getenv("MONGODB_URI", "mongodb+srv://img:51290@img.k6wn8ym.mongodb.net/")
    if not uri:
        raise ValueError("MONGODB_URI not set in environment variables")
    return uri

def get_database_name() -> str:
    """Get database name from environment or use default."""
    return os.getenv("DB_NAME", "img_detection")

async def init_db():
    """Initialize MongoDB connection and create indexes."""
    global _db_client, _database
    
    try:
        if _db_client is None:
            mongodb_uri = get_mongodb_uri()
            db_name = get_database_name()
            
            logger.info(f"Connecting to MongoDB: {db_name}")
            _db_client = AsyncIOMotorClient(
                mongodb_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            
            # Test connection
            await _db_client.admin.command('ping')
            logger.info("MongoDB connection successful")
            
            _database = _db_client[db_name]
            
            # Create indexes
            await _create_indexes()
            logger.info("MongoDB indexes created")
            
    except Exception as e:
        logger.error(f"MongoDB initialization error: {e}", exc_info=True)
        raise

async def _create_indexes():
    """Create all necessary indexes for collections."""
    db = get_db()
    
    # Guild config indexes
    await db.guild_config.create_index("guild_id", unique=True)
    
    # Image hashes indexes
    await db.image_hashes.create_indexes([
        IndexModel([("guild_id", ASCENDING)]),
        IndexModel([("guild_id", ASCENDING), ("hash_value", ASCENDING)]),
        IndexModel([("guild_id", ASCENDING), ("message_id", ASCENDING)]),
        IndexModel([("guild_id", ASCENDING), ("user_id", ASCENDING)]),
        IndexModel([("guild_id", ASCENDING), ("hash_value", ASCENDING), ("message_id", ASCENDING)], unique=True),
    ])
    
    # User stats indexes
    await db.user_stats.create_indexes([
        IndexModel([("guild_id", ASCENDING), ("user_id", ASCENDING)], unique=True),
        IndexModel([("guild_id", ASCENDING), ("duplicate_count", DESCENDING)]),
    ])
    
    # User warnings indexes
    await db.user_warnings.create_indexes([
        IndexModel([("guild_id", ASCENDING), ("user_id", ASCENDING)]),
        IndexModel([("issued_at", DESCENDING)]),
    ])
    
    # Duplicate history indexes
    await db.duplicate_history.create_indexes([
        IndexModel([("guild_id", ASCENDING), ("timestamp", DESCENDING)]),
        IndexModel([("guild_id", ASCENDING), ("user_id", ASCENDING)]),
    ])
    
    # Daily stats indexes
    await db.daily_stats.create_indexes([
        IndexModel([("guild_id", ASCENDING), ("date", DESCENDING)]),
    ])
    
    # Quarantine indexes
    await db.quarantine.create_indexes([
        IndexModel([("guild_id", ASCENDING)]),
        IndexModel([("released", ASCENDING)]),
    ])
    
    logger.info("All indexes created successfully")

def get_db() -> AsyncIOMotorDatabase:
    """Get database instance."""
    if _database is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _database

def init_db_sync():
    """Synchronous wrapper for init_db (for compatibility)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        # If loop is running, schedule the coroutine
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, init_db())
            future.result()
    else:
        loop.run_until_complete(init_db())

async def close_db():
    """Close database connection."""
    global _db_client
    if _db_client:
        _db_client.close()
        logger.info("MongoDB connection closed")

# ---------- Guild Config ----------
async def fetch_guild_config_row(guild_id: int) -> Optional[Dict[str, Any]]:
    """Fetch guild configuration from database."""
    try:
        db = get_db()
        config = await db.guild_config.find_one({"guild_id": guild_id})
        return config
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
        import json
        timestamp = datetime.utcnow()
        
        db = get_db()
        config_doc = {
            "guild_id": guild_id,
            "whitelist": json.loads(whitelist),
            "blacklist": json.loads(blacklist),
            "user_whitelist": json.loads(user_whitelist),
            "channel_thresholds": json.loads(channel_thresholds),
            "notification_settings": json.loads(notification_settings),
            "hash_threshold": hash_threshold,
            "ocr_threshold": ocr_threshold,
            "auto_delete": bool(auto_delete),
            "ocr_enabled": bool(ocr_enabled),
            "reaction_settings": json.loads(reaction_settings),
            "warning_config": json.loads(warning_config),
            "quarantine_config": json.loads(quarantine_config),
            "updated_at": timestamp,
        }
        
        # Check if exists
        existing = await db.guild_config.find_one({"guild_id": guild_id})
        if existing:
            await db.guild_config.update_one(
                {"guild_id": guild_id},
                {"$set": config_doc}
            )
        else:
            config_doc["created_at"] = timestamp
            await db.guild_config.insert_one(config_doc)
            
    except Exception as e:
        logger.error(f"Error upserting guild config: {e}")
        raise

# ---------- Image Hashes ----------
async def image_already_processed(guild_id: int, message_id: int) -> bool:
    """Check if message has already been processed."""
    try:
        db = get_db()
        count = await db.image_hashes.count_documents({
            "guild_id": guild_id,
            "message_id": message_id
        }, limit=1)
        return count > 0
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
        timestamp = datetime.utcnow()
        
        db = get_db()
        doc = {
            "guild_id": guild_id,
            "hash_value": hash_value,
            "message_id": message_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "timestamp": timestamp,
            "image_url": image_url,
            "ocr_text": ocr_text,
            "ocr_confidence": ocr_confidence,
        }
        
        try:
            await db.image_hashes.insert_one(doc)
        except DuplicateKeyError:
            # Already exists, ignore
            pass
            
    except Exception as e:
        logger.error(f"Error saving image hash: {e}")
        raise

async def get_all_hashes_for_guild(guild_id: int) -> List[Dict[str, Any]]:
    """Get all image hashes for a guild."""
    try:
        db = get_db()
        cursor = db.image_hashes.find(
            {"guild_id": guild_id}
        ).sort("timestamp", DESCENDING)
        
        hashes = await cursor.to_list(length=None)
        return hashes
    except Exception as e:
        logger.error(f"Error fetching hashes: {e}")
        return []

async def get_image_by_hash(guild_id: int, hash_value: str) -> Optional[Dict[str, Any]]:
    """Get image by hash value."""
    try:
        db = get_db()
        image = await db.image_hashes.find_one(
            {"guild_id": guild_id, "hash_value": hash_value},
            sort=[("timestamp", ASCENDING)]
        )
        return image
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
        if not text or len(text) < 10:
            return []
            
        db = get_db()
        # Use regex for pattern matching
        pattern = text[:min(20, len(text))]
        cursor = db.image_hashes.find({
            "guild_id": guild_id,
            "ocr_text": {"$regex": pattern, "$options": "i"}
        }).sort("timestamp", DESCENDING).limit(10)
        
        rows = await cursor.to_list(length=10)
        
        # Filter by similarity threshold
        results = []
        for row in rows:
            if row.get("ocr_text"):
                similarity = calculate_text_similarity(text, row["ocr_text"])
                if similarity >= similarity_threshold:
                    row["ocr_similarity"] = similarity
                    results.append(row)
        
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
        timestamp = datetime.utcnow()
        
        db = get_db()
        await db.user_stats.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {
                "$inc": {
                    "unique_count": unique_delta,
                    "duplicate_count": duplicate_delta,
                    "ocr_duplicate_count": ocr_duplicate_delta,
                },
                "$set": {"last_updated": timestamp}
            },
            upsert=True
        )
            
    except Exception as e:
        logger.error(f"Error updating user stats: {e}")
        raise

async def get_user_stats(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Get statistics for a specific user."""
    try:
        db = get_db()
        stats = await db.user_stats.find_one({
            "guild_id": guild_id,
            "user_id": user_id
        })
        return stats
    except Exception as e:
        logger.error(f"Error fetching user stats: {e}")
        return None

async def get_leaderboard(guild_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Get leaderboard of users with most duplicates."""
    try:
        db = get_db()
        cursor = db.user_stats.find({
            "guild_id": guild_id,
            "$or": [
                {"unique_count": {"$gt": 0}},
                {"duplicate_count": {"$gt": 0}}
            ]
        }).sort([
            ("duplicate_count", DESCENDING),
            ("ocr_duplicate_count", DESCENDING),
            ("unique_count", DESCENDING)
        ]).limit(limit)
        
        leaderboard = await cursor.to_list(length=limit)
        return leaderboard
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
        timestamp = datetime.utcnow()
        
        db = get_db()
        doc = {
            "guild_id": guild_id,
            "user_id": user_id,
            "warning_type": warning_type,
            "reason": reason,
            "action_taken": action_taken,
            "issued_at": timestamp,
            "issued_by": issued_by,
        }
        await db.user_warnings.insert_one(doc)
            
    except Exception as e:
        logger.error(f"Error adding warning: {e}")

async def get_user_warnings(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
    """Get warning history for a user."""
    try:
        db = get_db()
        cursor = db.user_warnings.find({
            "guild_id": guild_id,
            "user_id": user_id
        }).sort("issued_at", DESCENDING)
        
        warnings = await cursor.to_list(length=None)
        return warnings
    except Exception as e:
        logger.error(f"Error fetching warnings: {e}")
        return []

async def clear_user_warnings(guild_id: int, user_id: int) -> None:
    """Clear all warnings for a user."""
    try:
        db = get_db()
        await db.user_warnings.delete_many({
            "guild_id": guild_id,
            "user_id": user_id
        })
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
        timestamp = datetime.utcnow()
        
        db = get_db()
        doc = {
            "guild_id": guild_id,
            "timestamp": timestamp,
            "user_id": user_id,
            "user_name": user_name,
            "channel_id": channel_id,
            "message_id": message_id,
            "similarity_count": similarity_count,
            "similarity_percent": similarity_percent,
            "ocr_similarity_percent": ocr_similarity_percent,
            "original_message_id": original_message_id,
            "action_taken": action_taken,
            "detection_method": detection_method,
        }
        await db.duplicate_history.insert_one(doc)
            
    except Exception as e:
        logger.error(f"Error inserting duplicate history: {e}")

async def get_recent_duplicates(guild_id: int, hours: int = 24, limit: int = 50) -> List[Dict[str, Any]]:
    """Get recent duplicates within timeframe."""
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
        db = get_db()
        cursor = db.duplicate_history.find({
            "guild_id": guild_id,
            "timestamp": {"$gte": cutoff}
        }).sort("timestamp", DESCENDING).limit(limit)
        
        duplicates = await cursor.to_list(length=limit)
        return duplicates
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
        db = get_db()
        
        # Image counts
        total_images = await db.image_hashes.count_documents({"guild_id": guild_id})
        result["total_images"] = total_images
        
        # Distinct messages
        messages = await db.image_hashes.distinct("message_id", {"guild_id": guild_id})
        result["total_messages"] = len(messages)
        
        # User stats aggregation
        pipeline = [
            {"$match": {"guild_id": guild_id}},
            {"$group": {
                "_id": None,
                "total_unique": {"$sum": "$unique_count"},
                "total_duplicates": {"$sum": "$duplicate_count"},
                "total_ocr_duplicates": {"$sum": "$ocr_duplicate_count"}
            }}
        ]
        async for doc in db.user_stats.aggregate(pipeline):
            result["total_unique"] = doc.get("total_unique", 0)
            result["total_duplicates"] = doc.get("total_duplicates", 0)
            result["total_ocr_duplicates"] = doc.get("total_ocr_duplicates", 0)
        
        # Warning counts
        total_warnings = await db.user_warnings.count_documents({"guild_id": guild_id})
        result["total_warnings"] = total_warnings
        
    except Exception as e:
        logger.error(f"Error fetching guild stats: {e}")
    
    return result

# ---------- Daily Stats ----------
async def update_daily_stats(guild_id: int) -> None:
    """Update daily statistics."""
    try:
        today = datetime.utcnow().date()
        today_str = today.isoformat()
        
        db = get_db()
        
        # Count today's activity
        start_of_day = datetime.combine(today, datetime.min.time())
        end_of_day = datetime.combine(today, datetime.max.time())
        
        total_images = await db.image_hashes.count_documents({
            "guild_id": guild_id,
            "timestamp": {"$gte": start_of_day, "$lte": end_of_day}
        })
        
        duplicates = await db.duplicate_history.count_documents({
            "guild_id": guild_id,
            "timestamp": {"$gte": start_of_day, "$lte": end_of_day}
        })
        
        ocr_duplicates = await db.duplicate_history.count_documents({
            "guild_id": guild_id,
            "timestamp": {"$gte": start_of_day, "$lte": end_of_day},
            "detection_method": "ocr"
        })
        
        # Upsert daily stats
        await db.daily_stats.update_one(
            {"guild_id": guild_id, "date": today_str},
            {
                "$set": {
                    "total_images": total_images,
                    "duplicates_detected": duplicates,
                    "ocr_duplicates_detected": ocr_duplicates,
                }
            },
            upsert=True
        )
            
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
        timestamp = datetime.utcnow()
        
        db = get_db()
        doc = {
            "guild_id": guild_id,
            "message_id": message_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "image_url": image_url,
            "timestamp": timestamp,
            "reason": reason,
            "released": False,
            "release_timestamp": None,
        }
        await db.quarantine.insert_one(doc)
            
    except Exception as e:
        logger.error(f"Error quarantining message: {e}")

async def release_quarantined_message(
    guild_id: int,
    message_id: int,
    admin_id: int
) -> bool:
    """Release a quarantined message."""
    try:
        timestamp = datetime.utcnow()
        
        db = get_db()
        result = await db.quarantine.update_one(
            {"guild_id": guild_id, "message_id": message_id},
            {
                "$set": {
                    "released": True,
                    "release_timestamp": timestamp,
                    "released_by": admin_id
                }
            }
        )
        
        return result.modified_count > 0
            
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
        query = {"guild_id": guild_id}
        
        if unreleased_only:
            query["released"] = False
        
        db = get_db()
        cursor = db.quarantine.find(query).sort("timestamp", DESCENDING).limit(limit)
        
        messages = await cursor.to_list(length=limit)
        return messages
    except Exception as e:
        logger.error(f"Error getting quarantined messages: {e}")
        return []

# ---------- Log Channel Management ----------
async def set_log_channel(guild_id: int, channel_id: Optional[int]) -> None:
    """Set the log channel for a guild."""
    try:
        db = get_db()
        await db.guild_config.update_one(
            {"guild_id": guild_id},
            {"$set": {"log_channel_id": channel_id}},
            upsert=True
        )
        logger.info(f"Set log channel for guild {guild_id} to {channel_id}")
    except Exception as e:
        logger.error(f"Error setting log channel: {e}")

async def get_log_channel(guild_id: int) -> Optional[int]:
    """Get the log channel for a guild."""
    try:
        db = get_db()
        config = await db.guild_config.find_one({"guild_id": guild_id})
        if config:
            return config.get("log_channel_id")
        return None
    except Exception as e:
        logger.error(f"Error getting log channel: {e}")
        return None
