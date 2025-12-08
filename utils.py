"""
Utility functions for the duplicate detection bot.
"""
import asyncio
import logging
from typing import Optional

import discord

from config import get_reaction_config

logger = logging.getLogger("DuplicateDetector")

# ---------- Image Detection ----------
def is_image_attachment(attachment: discord.Attachment) -> bool:
    """
    Check if attachment is an image file.
    
    Args:
        attachment: Discord attachment to check
        
    Returns:
        True if attachment is an image, False otherwise
    """
    # Check content type first (most reliable)
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True
    
    # Fallback to filename extension
    filename = (attachment.filename or "").lower()
    image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff")
    return any(filename.endswith(ext) for ext in image_extensions)

# ---------- Hash Comparison ----------
def hamming_distance_hex(hash1: str, hash2: str) -> int:
    """
    Calculate Hamming distance between two hex hash strings.
    
    Args:
        hash1: First hash as hex string
        hash2: Second hash as hex string
        
    Returns:
        Hamming distance (number of differing bits)
    """
    try:
        # Convert hex strings to integers
        val1 = int(str(hash1), 16)
        val2 = int(str(hash2), 16)
    except (ValueError, TypeError) as e:
        logger.warning(f"Invalid hash format: {e}")
        return 64  # Maximum distance for invalid hashes
    
    # XOR and count set bits
    xor_result = val1 ^ val2
    return bin(xor_result).count("1")

# ---------- Formatting ----------
def format_number(n: int) -> str:
    """
    Format number with K/M suffixes for readability.
    
    Args:
        n: Number to format
        
    Returns:
        Formatted string (e.g., "1.2K", "3.5M")
    """
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def format_duration(seconds: int) -> str:
    """
    Format duration in human-readable format.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted string (e.g., "2h 30m", "45s")
    """
    if seconds < 60:
        return f"{seconds}s"
    
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes > 0:
        return f"{hours}h {remaining_minutes}m"
    return f"{hours}h"

# ---------- Reaction Management ----------
async def remove_reaction_later(
    message: discord.Message,
    emoji: str,
    delay: int
) -> None:
    """
    Remove a reaction after a delay.
    
    Args:
        message: Message to remove reaction from
        emoji: Emoji string to remove
        delay: Delay in seconds before removal
    """
    await asyncio.sleep(delay)
    
    try:
        if message.guild and message.guild.me:
            await message.remove_reaction(emoji, message.guild.me)
    except discord.NotFound:
        # Message or reaction was already deleted
        pass
    except discord.Forbidden:
        logger.debug(f"No permission to remove reaction from message {message.id}")
    except discord.HTTPException as e:
        logger.warning(f"Failed to remove reaction: {e}")

async def add_controlled_reaction(
    message: discord.Message,
    emoji: str,
    guild_id: int,
    scope: str
) -> None:
    """
    Add reaction to message with configuration-based behavior.
    
    Args:
        message: Message to add reaction to
        emoji: Emoji string to add
        guild_id: Guild ID for configuration lookup
        scope: "live" or "scan" - determines which config to use
    """
    try:
        enabled, ttl = get_reaction_config(guild_id, scope)
        
        if not enabled:
            return
        
        # Add reaction
        await message.add_reaction(emoji)
        
        # Schedule removal if TTL is set
        if ttl > 0:
            asyncio.create_task(
                remove_reaction_later(message, emoji, ttl)
            )
            
    except discord.Forbidden:
        logger.debug(f"No permission to add reaction to message {message.id}")
    except discord.NotFound:
        logger.debug(f"Message {message.id} not found for reaction")
    except discord.HTTPException as e:
        logger.warning(f"Failed to add reaction to message {message.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error adding reaction: {e}")

# ---------- Permission Checks ----------
def bot_has_permissions(
    channel: discord.TextChannel,
    *permissions: str
) -> tuple[bool, list[str]]:
    """
    Check if bot has required permissions in a channel.
    
    Args:
        channel: Channel to check permissions in
        *permissions: Permission names to check (e.g., "send_messages", "add_reactions")
        
    Returns:
        Tuple of (all_granted, missing_permissions)
    """
    if not channel.guild or not channel.guild.me:
        return False, list(permissions)
    
    bot_permissions = channel.permissions_for(channel.guild.me)
    missing = []
    
    for perm_name in permissions:
        if not getattr(bot_permissions, perm_name, False):
            missing.append(perm_name)
    
    return len(missing) == 0, missing

# ---------- Validation ----------
def validate_threshold(value: int) -> tuple[bool, Optional[str]]:
    """
    Validate similarity threshold value.
    
    Args:
        value: Threshold value to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(value, int):
        return False, "Threshold must be an integer"
    
    if value < 1 or value > 64:
        return False, "Threshold must be between 1 and 64"
    
    return True, None

def validate_ttl(value: int) -> tuple[bool, Optional[str]]:
    """
    Validate TTL (time-to-live) value for reactions.
    
    Args:
        value: TTL value in seconds to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(value, int):
        return False, "TTL must be an integer"
    
    if value < 0 or value > 3600:
        return False, "TTL must be between 0 and 3600 seconds"
    
    return True, None

# ---------- Embed Builders ----------
def create_error_embed(
    title: str = "Error",
    description: str = "An error occurred",
    details: Optional[str] = None
) -> discord.Embed:
    """
    Create a standardized error embed.
    
    Args:
        title: Error title
        description: Error description
        details: Optional additional details
        
    Returns:
        Discord embed object
    """
    embed = discord.Embed(
        title=f"❌ {title}",
        description=description,
        color=discord.Color.red()
    )
    
    if details:
        embed.add_field(name="Details", value=details, inline=False)
    
    return embed

def create_success_embed(
    title: str = "Success",
    description: str = "Operation completed successfully"
) -> discord.Embed:
    """
    Create a standardized success embed.
    
    Args:
        title: Success title
        description: Success description
        
    Returns:
        Discord embed object
    """
    embed = discord.Embed(
        title=f"✅ {title}",
        description=description,
        color=discord.Color.green()
    )
    
    return embed

# ---------- Rate Limiting ----------
class RateLimiter:
    """Simple rate limiter for preventing abuse."""
    
    def __init__(self, max_calls: int, period: int):
        """
        Initialize rate limiter.
        
        Args:
            max_calls: Maximum number of calls allowed
            period: Time period in seconds
        """
        self.max_calls = max_calls
        self.period = period
        self.calls: dict[int, list[float]] = {}
    
    def is_allowed(self, user_id: int) -> bool:
        """
        Check if user is within rate limit.
        
        Args:
            user_id: User ID to check
            
        Returns:
            True if allowed, False if rate limited
        """
        import time
        
        now = time.time()
        
        if user_id not in self.calls:
            self.calls[user_id] = []
        
        # Remove old calls
        self.calls[user_id] = [
            call_time for call_time in self.calls[user_id]
            if now - call_time < self.period
        ]
        
        # Check if under limit
        if len(self.calls[user_id]) < self.max_calls:
            self.calls[user_id].append(now)
            return True
        
        return False
    
    def get_remaining(self, user_id: int) -> int:
        """
        Get remaining calls for user.
        
        Args:
            user_id: User ID to check
            
        Returns:
            Number of remaining calls
        """
        if user_id not in self.calls:
            return self.max_calls
        
        return max(0, self.max_calls - len(self.calls[user_id]))

logger.info("Utility functions loaded successfully")
