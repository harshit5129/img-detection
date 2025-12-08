"""
Configuration management with thread-safe operations and OCR support.
"""
import json
import logging
import asyncio
from typing import Any, Dict, Tuple

from db import fetch_guild_config_row, upsert_guild_config_row

logger = logging.getLogger("DuplicateDetector")

# In-memory cache with lock
guild_data: Dict[int, Dict[str, Any]] = {}
_config_lock = asyncio.Lock()

# Default values
HASH_THRESHOLD_DEFAULT = 5
OCR_THRESHOLD_DEFAULT = 85  # Percentage match for OCR text
AUTO_DELETE_DEFAULT = False
OCR_ENABLED_DEFAULT = True
MAX_OCR_SIZE = 10 * 1024 * 1024  # 10MB for OCR processing

def default_reaction_settings() -> Dict[str, Dict[str, Any]]:
    """Get default reaction settings."""
    return {
        "live": {"enabled": True, "ttl": 0},
        "scan": {"enabled": True, "ttl": 0},
    }

def default_warning_config() -> Dict[str, Any]:
    """Get default warning configuration."""
    return {
        "thresholds": [
            {"count": 3, "action": "warn", "message": "⚠️ First Warning"},
            {"count": 5, "action": "warn", "message": "⚠️ Second Warning"},
            {"count": 10, "action": "mute", "message": "🔇 Auto-Muted"},
            {"count": 20, "action": "kick", "message": "👢 Auto-Kicked"},
        ],
        "enabled": True,
        "mute_duration": 3600,  # 1 hour
    }

def default_quarantine_config() -> Dict[str, Any]:
    """Get default quarantine configuration."""
    return {
        "enabled": False,
        "channel_id": None,
        "auto_release": False,
        "release_after": 86400,  # 24 hours
        "notify_admins": True,
    }

async def ensure_guild_config(guild_id: int) -> None:
    """
    Load guild configuration into memory if not already loaded.
    Thread-safe with proper error handling.
    """
    # Check without lock first (fast path)
    if guild_id in guild_data:
        return
    
    # Acquire lock for initialization
    async with _config_lock:
        # Double-check after acquiring lock
        if guild_id in guild_data:
            return
        
        try:
            row = await fetch_guild_config_row(guild_id)
            
            if row:
                # Parse existing configuration
                config = _parse_guild_config_row(row)
            else:
                # Create default configuration
                config = _create_default_config()
            
            guild_data[guild_id] = {"config": config}
            logger.debug(f"Loaded config for guild {guild_id}")
            
        except Exception as e:
            logger.error(f"Error loading config for guild {guild_id}: {e}", exc_info=True)
            # Fallback to default config
            guild_data[guild_id] = {"config": _create_default_config()}

def _parse_guild_config_row(row: Any) -> Dict[str, Any]:
    """Parse database row into configuration dict."""
    try:
        # Parse JSON fields with fallbacks
        whitelist = set(json.loads(row["whitelist"])) if row["whitelist"] else set()
        blacklist = set(json.loads(row["blacklist"])) if row["blacklist"] else set()
        user_whitelist = set(json.loads(row["user_whitelist"])) if row["user_whitelist"] else set()
        channel_thresholds = json.loads(row["channel_thresholds"]) if row["channel_thresholds"] else {}
        notification_settings = json.loads(row["notification_settings"]) if row["notification_settings"] else {}
        
        # Parse scalar fields
        hash_threshold = (
            int(row["hash_threshold"])
            if row["hash_threshold"] is not None
            else HASH_THRESHOLD_DEFAULT
        )
        ocr_threshold = (
            int(row["ocr_threshold"])
            if row["ocr_threshold"] is not None
            else OCR_THRESHOLD_DEFAULT
        )
        auto_delete = (
            bool(row["auto_delete"])
            if row["auto_delete"] is not None
            else AUTO_DELETE_DEFAULT
        )
        ocr_enabled = (
            bool(row["ocr_enabled"])
            if row["ocr_enabled"] is not None
            else OCR_ENABLED_DEFAULT
        )
        
        # Parse reaction settings with merge
        reactions = default_reaction_settings()
        if row["reaction_settings"]:
            try:
                loaded = json.loads(row["reaction_settings"])
                if isinstance(loaded, dict):
                    for scope, cfg in loaded.items():
                        if scope in reactions and isinstance(cfg, dict):
                            reactions[scope].update(cfg)
                        elif isinstance(cfg, dict):
                            reactions[scope] = cfg
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid reaction_settings JSON: {e}")
        
        # Parse warning configuration
        warnings = default_warning_config()
        if row["warning_config"]:
            try:
                loaded = json.loads(row["warning_config"])
                if isinstance(loaded, dict):
                    warnings.update(loaded)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid warning_config JSON: {e}")
        
        # Parse quarantine configuration
        quarantine = default_quarantine_config()
        if row["quarantine_config"]:
            try:
                loaded = json.loads(row["quarantine_config"])
                if isinstance(loaded, dict):
                    quarantine.update(loaded)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid quarantine_config JSON: {e}")
        
        return {
            "whitelist": whitelist,
            "blacklist": blacklist,
            "user_whitelist": user_whitelist,
            "channel_thresholds": channel_thresholds,
            "notification_settings": notification_settings,
            "hash_threshold": hash_threshold,
            "ocr_threshold": ocr_threshold,
            "auto_delete": auto_delete,
            "ocr_enabled": ocr_enabled,
            "reaction_settings": reactions,
            "warning_config": warnings,
            "quarantine_config": quarantine,
        }
        
    except Exception as e:
        logger.error(f"Error parsing guild config row: {e}")
        return _create_default_config()

def _create_default_config() -> Dict[str, Any]:
    """Create default configuration."""
    return {
        "whitelist": set(),
        "blacklist": set(),
        "user_whitelist": set(),
        "channel_thresholds": {},
        "notification_settings": {},
        "hash_threshold": HASH_THRESHOLD_DEFAULT,
        "ocr_threshold": OCR_THRESHOLD_DEFAULT,
        "auto_delete": AUTO_DELETE_DEFAULT,
        "ocr_enabled": OCR_ENABLED_DEFAULT,
        "reaction_settings": default_reaction_settings(),
        "warning_config": default_warning_config(),
        "quarantine_config": default_quarantine_config(),
    }

async def save_guild_config(guild_id: int) -> None:
    """
    Persist in-memory configuration to database.
    Thread-safe operation.
    """
    async with _config_lock:
        if guild_id not in guild_data:
            logger.warning(f"Attempted to save config for unloaded guild {guild_id}")
            return
        
        try:
            cfg = guild_data[guild_id]["config"]
            
            # Serialize to JSON
            whitelist = json.dumps(list(cfg.get("whitelist", set())))
            blacklist = json.dumps(list(cfg.get("blacklist", set())))
            user_whitelist = json.dumps(list(cfg.get("user_whitelist", set())))
            channel_thresholds = json.dumps(cfg.get("channel_thresholds", {}))
            notification_settings = json.dumps(cfg.get("notification_settings", {}))
            hash_threshold = int(cfg.get("hash_threshold", HASH_THRESHOLD_DEFAULT))
            ocr_threshold = int(cfg.get("ocr_threshold", OCR_THRESHOLD_DEFAULT))
            auto_delete = 1 if cfg.get("auto_delete", AUTO_DELETE_DEFAULT) else 0
            ocr_enabled = 1 if cfg.get("ocr_enabled", OCR_ENABLED_DEFAULT) else 0
            reaction_settings = json.dumps(
                cfg.get("reaction_settings", default_reaction_settings())
            )
            warning_config = json.dumps(
                cfg.get("warning_config", default_warning_config())
            )
            quarantine_config = json.dumps(
                cfg.get("quarantine_config", default_quarantine_config())
            )
            
            # Save to database
            await upsert_guild_config_row(
                guild_id,
                whitelist,
                blacklist,
                user_whitelist,
                channel_thresholds,
                notification_settings,
                hash_threshold,
                ocr_threshold,
                auto_delete,
                ocr_enabled,
                reaction_settings,
                warning_config,
                quarantine_config,
            )
            
            logger.debug(f"Saved config for guild {guild_id}")
            
        except Exception as e:
            logger.error(f"Error saving config for guild {guild_id}: {e}", exc_info=True)
            raise

def get_detection_params(guild_id: int) -> Tuple[int, int, bool, bool]:
    """
    Get detection parameters for a guild.
    
    Returns:
        Tuple of (hash_threshold, ocr_threshold, auto_delete, ocr_enabled)
    """
    cfg = guild_data.get(guild_id, {}).get("config", {})
    threshold = int(cfg.get("hash_threshold", HASH_THRESHOLD_DEFAULT))
    ocr_threshold = int(cfg.get("ocr_threshold", OCR_THRESHOLD_DEFAULT))
    auto_delete = bool(cfg.get("auto_delete", AUTO_DELETE_DEFAULT))
    ocr_enabled = bool(cfg.get("ocr_enabled", OCR_ENABLED_DEFAULT))
    return threshold, ocr_threshold, auto_delete, ocr_enabled

def get_reaction_config(guild_id: int, scope: str) -> Tuple[bool, int]:
    """
    Get reaction configuration for a specific scope.
    
    Args:
        guild_id: Guild ID
        scope: "live" or "scan"
        
    Returns:
        Tuple of (enabled, ttl_seconds)
    """
    cfg = guild_data.get(guild_id, {}).get("config", {})
    reactions = cfg.get("reaction_settings", default_reaction_settings())
    
    # Ensure scope exists
    if scope not in reactions:
        reactions[scope] = {"enabled": True, "ttl": 0}
    
    scope_cfg = reactions[scope]
    enabled = bool(scope_cfg.get("enabled", True))
    
    # Parse TTL with validation
    ttl = scope_cfg.get("ttl", 0)
    try:
        ttl = max(0, min(3600, int(ttl)))  # Clamp between 0-3600
    except (TypeError, ValueError):
        logger.warning(f"Invalid TTL value for guild {guild_id}, scope {scope}: {ttl}")
        ttl = 0
    
    return enabled, ttl

def get_warning_config(guild_id: int) -> Dict[str, Any]:
    """Get warning configuration for a guild."""
    cfg = guild_data.get(guild_id, {}).get("config", {})
    return cfg.get("warning_config", default_warning_config())

def get_quarantine_config(guild_id: int) -> Dict[str, Any]:
    """Get quarantine configuration for a guild."""
    cfg = guild_data.get(guild_id, {}).get("config", {})
    return cfg.get("quarantine_config", default_quarantine_config())

async def clear_guild_config(guild_id: int) -> None:
    """Remove guild configuration from memory (e.g., when bot leaves guild)."""
    async with _config_lock:
        if guild_id in guild_data:
            del guild_data[guild_id]
            logger.info(f"Cleared config for guild {guild_id}")

def get_all_loaded_guilds() -> list[int]:
    """Get list of all guilds with loaded configurations."""
    return list(guild_data.keys())
