"""
Enhanced image duplicate detection with OCR and comprehensive features.
"""
import io
import logging
import re
import os
from typing import Any, Dict, List, Optional, Tuple
import asyncio
from datetime import datetime, timedelta
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

import aiohttp
import discord
from discord.ext import commands, tasks
from discord.ext.commands import Context
from PIL import Image
import imagehash

from db import (
    image_already_processed,
    save_image_hash,
    get_all_hashes_for_guild,
    update_user_stats,
    insert_duplicate_history,
    get_user_stats,
    get_user_warnings,
    add_user_warning,
    get_image_by_hash,
    get_similar_ocr_text,
    quarantine_message,
)
from config import (
    ensure_guild_config, 
    get_detection_params, 
    guild_data,
    get_warning_config,
    get_quarantine_config
)
from utils import (
    is_image_attachment, 
    hamming_distance_hex, 
    add_controlled_reaction, 
    format_number
)
from ocr import get_ocr_engine
from ocr import process_image_for_ocr

logger = logging.getLogger("DuplicateDetector")

# Constants
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
MIN_IMAGE_SIZE = 10 * 1024  # 10KB (ignore tiny images)
DOWNLOAD_TIMEOUT = 30
LOG_CHANNEL_NAME = "duplicate-logs"
QUARANTINE_CHANNEL_NAME = "duplicate-quarantine"
OCR_MIN_TEXT_LENGTH = 10  # Minimum text length to consider for OCR comparison
OCR_SIMILARITY_THRESHOLD = 85  # Minimum similarity percentage

# Warning thresholds
WARNING_LEVELS = {
    1: {"threshold": 3, "action": "warn", "message": "⚠️ First Warning"},
    2: {"threshold": 5, "action": "warn", "message": "⚠️ Second Warning"},
    3: {"threshold": 10, "action": "mute", "message": "🔇 Auto-Muted"},
    4: {"threshold": 20, "action": "kick", "message": "👢 Auto-Kicked"},
}

class ImageProcessingError(Exception):
    """Custom exception for image processing errors."""
    pass

def is_user_whitelisted(guild_id: int, user_id: int) -> bool:
    """Check if user is whitelisted."""
    try:
        cfg = guild_data.get(guild_id, {}).get("config", {})
        whitelist = cfg.get("user_whitelist", set())
        return user_id in whitelist
    except Exception:
        return False

def is_channel_blacklisted(guild_id: int, channel_id: int) -> bool:
    """Check if channel is blacklisted."""
    try:
        cfg = guild_data.get(guild_id, {}).get("config", {})
        blacklist = cfg.get("blacklist", set())
        return channel_id in blacklist
    except Exception:
        return False

def is_channel_whitelisted(guild_id: int, channel_id: int) -> bool:
    """Check if channel is whitelisted (ignore duplicates)."""
    try:
        cfg = guild_data.get(guild_id, {}).get("config", {})
        whitelist = cfg.get("whitelist", set())
        return channel_id in whitelist
    except Exception:
        return False

def get_channel_threshold(guild_id: int, channel_id: int, default: int) -> int:
    """Get per-channel threshold or default."""
    try:
        cfg = guild_data.get(guild_id, {}).get("config", {})
        thresholds = cfg.get("channel_thresholds", {})
        return int(thresholds.get(str(channel_id), default))
    except Exception:
        return default

async def get_or_create_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Get or create the #duplicate-logs channel."""
    try:
        for channel in guild.text_channels:
            if channel.name == LOG_CHANNEL_NAME:
                return channel
        
        logger.info(f"Creating #{LOG_CHANNEL_NAME} channel in {guild.name}")
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(send_messages=False),
            guild.me: discord.PermissionOverwrite(
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True
            )
        }
        
        channel = await guild.create_text_channel(
            name=LOG_CHANNEL_NAME,
            topic="🔍 Automatic duplicate image detection logs | Configure with /setsimilarity",
            overwrites=overwrites,
            reason="Created by Duplicate Detector Bot"
        )
        
        embed = discord.Embed(
            title="🔍 Duplicate Detection Logs - Enhanced",
            description=(
                "**This channel tracks all duplicate detections with advanced features:**\n\n"
                "📊 **Features Enabled:**\n"
                "• Detailed similarity reports with percentages\n"
                "• User statistics and duplicate rates\n"
                "• Image metadata (size, format, dimensions)\n"
                "• OCR text analysis for similar content\n"
                "• Warning system for repeat offenders\n"
                "• Per-channel sensitivity settings\n"
                "• Whitelist/blacklist management\n"
                "• Time-based filtering\n"
                "• Credit to original posters\n\n"
                "⚙️ **Admin Commands:**\n"
                "`/setsimilarity` - Detection sensitivity\n"
                "`/toggleautodelete` - Auto-delete duplicates\n"
                "`/whitelist` - Manage whitelisted users/channels\n"
                "`/blacklist` - Manage blacklisted channels\n"
                "`/warnings` - View user warnings\n"
                "`/stats` - Server statistics\n\n"
                "🎯 **Detection Intelligence:**\n"
                "• Ignores images smaller than 10KB\n"
                "• Tracks original content creators\n"
                "• Smart similarity scoring\n"
                "• Automatic warning escalation"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Duplicate Detector Bot v3.0 - Ultimate Edition")
        await channel.send(embed=embed)
        
        logger.info(f"Created #{LOG_CHANNEL_NAME} channel successfully")
        return channel
        
    except discord.Forbidden:
        logger.error(f"No permission to create #{LOG_CHANNEL_NAME} channel")
        return None
    except Exception as e:
        logger.error(f"Error creating log channel: {e}")
        return None

async def get_or_create_quarantine_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Get or create the #duplicate-quarantine channel."""
    try:
        for channel in guild.text_channels:
            if channel.name == QUARANTINE_CHANNEL_NAME:
                return channel
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(
                send_messages=True,
                read_messages=True,
                manage_messages=True
            )
        }
        
        channel = await guild.create_text_channel(
            name=QUARANTINE_CHANNEL_NAME,
            topic="Quarantined duplicate images for review",
            overwrites=overwrites,
            reason="Created by Duplicate Detector Bot"
        )
        
        embed = discord.Embed(
            title="🛡️ Duplicate Image Quarantine",
            description=(
                "This channel contains images that were flagged as potential duplicates for manual review.\n\n"
                "**How to review:**\n"
                "1. Check if the image is truly a duplicate\n"
                "2. Use `/release <message_id>` to release valid content\n"
                "3. Use `/delete <message_id>` to permanently delete duplicates\n"
                "4. Use `/reportfalsepositive` for mistakes\n\n"
                "**Note:** Images are automatically released after 24 hours unless marked as duplicates."
            ),
            color=discord.Color.orange()
        )
        await channel.send(embed=embed)
        
        return channel
        
    except Exception as e:
        logger.error(f"Error creating quarantine channel: {e}")
        return None

async def _download_image(url: str, session: aiohttp.ClientSession) -> bytes:
    """Download image with timeout and size limits."""
    try:
        async with session.get(
            url, 
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                raise ImageProcessingError(f"HTTP {resp.status}")
            
            content_length = resp.headers.get('content-length')
            if content_length and int(content_length) > MAX_IMAGE_SIZE:
                raise ImageProcessingError(f"Image too large: {content_length} bytes")
            
            data = await resp.read()
            
            if len(data) > MAX_IMAGE_SIZE:
                raise ImageProcessingError(f"Image too large: {len(data)} bytes")
            
            # Ignore very small images (likely emojis/icons)
            if len(data) < MIN_IMAGE_SIZE:
                raise ImageProcessingError(f"Image too small: {len(data)} bytes")
            
            return data
            
    except asyncio.TimeoutError:
        raise ImageProcessingError("Download timeout")
    except aiohttp.ClientError as e:
        raise ImageProcessingError(f"Download failed: {e}")

def _compute_hash(data: bytes) -> str:
    """Compute perceptual hash of image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            ph = imagehash.phash(img, hash_size=8)
            return str(ph)
            
    except Exception as e:
        raise ImageProcessingError(f"Hash computation failed: {e}")

def _get_image_info(data: bytes) -> Dict[str, Any]:
    """Get detailed image information."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return {
                "format": img.format or "Unknown",
                "mode": img.mode,
                "size": img.size,
                "width": img.width,
                "height": img.height,
                "file_size": len(data),
                "megapixels": (img.width * img.height) / 1_000_000,
            }
    except Exception:
        return {
            "format": "Unknown",
            "mode": "Unknown",
            "size": (0, 0),
            "width": 0,
            "height": 0,
            "file_size": len(data),
            "megapixels": 0,
        }

def _calculate_quality_score(image_info: Dict[str, Any]) -> Tuple[int, str]:
    """Calculate image quality score (0-100)."""
    try:
        width = image_info["width"]
        height = image_info["height"]
        file_size = image_info["file_size"]
        
        # Resolution score
        pixels = width * height
        if pixels >= 2_073_600:  # 1920x1080+
            res_score = 100
            quality = "Excellent"
        elif pixels >= 921_600:  # 1280x720+
            res_score = 80
            quality = "Good"
        elif pixels >= 307_200:  # 640x480+
            res_score = 60
            quality = "Average"
        else:
            res_score = 40
            quality = "Low"
        
        # File size score (compression check)
        bytes_per_pixel = file_size / pixels if pixels > 0 else 0
        if bytes_per_pixel > 5:
            compression_score = 100
        elif bytes_per_pixel > 2:
            compression_score = 70
        else:
            compression_score = 50
        
        final_score = int((res_score + compression_score) / 2)
        return final_score, quality
        
    except Exception:
        return 50, "Unknown"

async def handle_user_warnings(
    guild: discord.Guild,
    user: discord.Member,
    duplicate_count: int
) -> Optional[str]:
    """Handle user warning escalation system."""
    try:
        warning_config = get_warning_config(guild.id)
        if not warning_config.get("enabled", True):
            return None
            
        # Check warning thresholds
        action_taken = None
        
        for config in warning_config.get("thresholds", []):
            if duplicate_count == config["count"]:
                action = config["action"]
                message = config["message"]
                
                if action == "warn":
                    await add_user_warning(
                        guild.id, 
                        user.id, 
                        "duplicate", 
                        f"Duplicate #{duplicate_count}",
                        action_taken=message
                    )
                    action_taken = f"⚠️ {message} (Level {config['count']})"
                    
                    # Send DM warning
                    try:
                        embed = discord.Embed(
                            title="⚠️ Duplicate Image Warning",
                            description=(
                                f"You have posted **{duplicate_count}** duplicate images in {guild.name}.\n\n"
                                f"Please post original content to avoid further action."
                            ),
                            color=discord.Color.orange()
                        )
                        embed.set_footer(text=f"Warning Level: {config['count']}")
                        await user.send(embed=embed)
                    except discord.Forbidden:
                        pass
                    
                elif action == "mute" and guild.me.guild_permissions.moderate_members:
                    timeout_duration = warning_config.get("mute_duration", 3600)
                    timeout_until = discord.utils.utcnow() + timedelta(seconds=timeout_duration)
                    try:
                        await user.timeout(timeout_until, reason=f"Auto-mute: {duplicate_count} duplicates")
                        action_taken = f"🔇 User muted for {format_duration(timeout_duration)}"
                    except discord.Forbidden:
                        action_taken = "⚠️ Cannot mute (missing permissions)"
                    
                elif action == "kick" and guild.me.guild_permissions.kick_members:
                    try:
                        await user.kick(reason=f"Auto-kick: {duplicate_count} duplicates")
                        action_taken = "👢 User kicked from server"
                    except discord.Forbidden:
                        action_taken = "⚠️ Cannot kick (missing permissions)"
        
        return action_taken
        
    except Exception as e:
        logger.error(f"Error handling warnings: {e}")
        return None

async def send_detailed_log(
    guild: discord.Guild,
    message: discord.Message,
    duplicate_count: int,
    first_match: Dict[str, Any],
    image_info: Dict[str, Any],
    similarity_distance: int,
    ocr_similarity: Optional[float] = None,
    threshold: int = 5,
    detection_method: str = "hash",
    warning_action: Optional[str] = None,
) -> None:
    """Send enhanced detailed log to #duplicate-logs channel."""
    try:
        log_channel = await get_or_create_log_channel(guild)
        if not log_channel:
            return
        
        user_stats = await get_user_stats(guild.id, message.author.id)
        warnings = await get_user_warnings(guild.id, message.author.id)
        
        # Calculate similarity
        max_distance = 64
        similarity_percent = ((max_distance - similarity_distance) / max_distance) * 100
        
        # Get quality score
        quality_score, quality_label = _calculate_quality_score(image_info)
        
        # Main embed
        embed = discord.Embed(
            title="🚨 Duplicate Image Detected" if detection_method == "hash" else "📝 Similar Text Detected",
            description=f"A duplicate was found in {message.channel.mention}",
            color=discord.Color.red() if detection_method == "hash" else discord.Color.purple(),
            timestamp=datetime.utcnow()
        )
        
        # User info with warnings
        warning_text = f" • ⚠️ **{len(warnings)} warning(s)**" if warnings else ""
        embed.add_field(
            name="👤 Posted By",
            value=(
                f"{message.author.mention}\n"
                f"`{message.author}` (ID: {message.author.id}){warning_text}"
            ),
            inline=False
        )
        
        # Detection details
        detection_details = (
            f"**Similarity:** {similarity_percent:.1f}%\n"
            f"**Hamming Distance:** {similarity_distance}/{threshold}\n"
            f"**Matches Found:** {duplicate_count}"
        )
        
        if ocr_similarity:
            detection_details += f"\n**OCR Similarity:** {ocr_similarity:.1f}%"
            
        embed.add_field(
            name="🔍 Detection Details",
            value=detection_details,
            inline=True
        )
        
        # Image details
        file_size_mb = image_info["file_size"] / (1024 * 1024)
        embed.add_field(
            name="🖼️ Image Info",
            value=(
                f"**Format:** {image_info['format']}\n"
                f"**Dimensions:** {image_info['width']}×{image_info['height']}\n"
                f"**Size:** {file_size_mb:.2f} MB\n"
                f"**Megapixels:** {image_info['megapixels']:.1f}MP"
            ),
            inline=True
        )
        
        # User statistics
        if user_stats:
            total_posts = user_stats["unique_count"] + user_stats["duplicate_count"]
            dup_rate = (user_stats["duplicate_count"] / total_posts * 100) if total_posts > 0 else 0
            
            embed.add_field(
                name="📊 User Statistics",
                value=(
                    f"**Total Posts:** {total_posts}\n"
                    f"**Unique:** {user_stats['unique_count']} ✅\n"
                    f"**Duplicates:** {user_stats['duplicate_count']} ❌\n"
                    f"**Duplicate Rate:** {dup_rate:.1f}%\n"
                    f"**Warnings:** {len(warnings)}"
                ),
                inline=False
            )
        
        # Original post info with credit
        original_user_id = first_match.get("user_id")
        original_user = guild.get_member(original_user_id) if original_user_id else None
        original_channel = guild.get_channel(first_match["channel_id"])
        original_link = (
            f"https://discord.com/channels/"
            f"{guild.id}/{first_match['channel_id']}/{first_match['message_id']}"
        )
        
        original_credit = ""
        if original_user:
            original_credit = f"**Original by:** {original_user.mention}\n"
        
        embed.add_field(
            name="📍 Original Post",
            value=(
                f"{original_credit}"
                f"**Channel:** {original_channel.mention if original_channel else 'Unknown'}\n"
                f"**Posted:** <t:{int(datetime.fromisoformat(first_match['timestamp']).timestamp())}:R>\n"
                f"[Jump to Original]({original_link})"
            ),
            inline=False
        )
        
        # Current post
        embed.add_field(
            name="📍 Current Post",
            value=f"[Jump to Current]({message.jump_url})",
            inline=False
        )
        
        # Warning action
        if warning_action:
            embed.add_field(
                name="⚖️ Action Taken",
                value=warning_action,
                inline=False
            )
        
        if message.attachments:
            embed.set_thumbnail(url=message.attachments[0].url)
        
        embed.set_footer(
            text=f"Detection: {detection_method.upper()} • Threshold: {threshold}",
            icon_url=message.author.display_avatar.url
        )
        
        await log_channel.send(embed=embed)
        
        # Image comparison
        if message.attachments and first_match.get("image_url"):
            comparison_embed = discord.Embed(
                title="🔄 Image Comparison",
                description="Side-by-side comparison of original and duplicate",
                color=discord.Color.orange()
            )
            comparison_embed.add_field(
                name="Original Image",
                value=f"[View]({first_match['image_url']})",
                inline=True
            )
            comparison_embed.add_field(
                name="Duplicate Image",
                value=f"[View]({message.attachments[0].url})",
                inline=True
            )
            comparison_embed.set_image(url=message.attachments[0].url)
            await log_channel.send(embed=comparison_embed)
        
        # OCR comparison if applicable
        if detection_method == "ocr" and first_match.get("ocr_text"):
            ocr_embed = discord.Embed(
                title="🔍 Text Comparison",
                description="Text content comparison between original and duplicate",
                color=discord.Color.purple()
            )
            
            # Clean up text for display
            original_text = first_match["ocr_text"]
            duplicate_text = message.content
            
            # Show first 500 characters of each
            ocr_embed.add_field(
                name="Original Text",
                value=f"```\n{original_text[:500] + '...' if len(original_text) > 500 else original_text}\n```",
                inline=False
            )
            
            ocr_embed.add_field(
                name="Duplicate Text",
                value=f"```\n{duplicate_text[:500] + '...' if len(duplicate_text) > 500 else duplicate_text}\n```",
                inline=False
            )
            
            await log_channel.send(embed=ocr_embed)
        
    except Exception as e:
                logger.error(f"Error sending detailed log: {e}", exc_info=True)

async def process_message_for_duplicates(
    message: discord.Message,
    scope: str = "live",
    with_loading: bool = True,
) -> None:
    """
    Advanced duplicate detection with hash and OCR analysis.
    """
    if not message.guild or message.author.bot:
        return
    
    guild_id = message.guild.id
    
    try:
        await ensure_guild_config(guild_id)
        threshold, ocr_threshold, auto_delete, ocr_enabled = get_detection_params(guild_id)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return
    
    # Check if user is whitelisted
    if is_user_whitelisted(guild_id, message.author.id):
        logger.debug(f"User {message.author.id} is whitelisted, skipping")
        return
    
    # Check if channel is whitelisted (ignore duplicates)
    if is_channel_whitelisted(guild_id, message.channel.id):
        logger.debug(f"Channel {message.channel.id} is whitelisted, skipping")
        return
    
    # Check if channel is blacklisted
    if is_channel_blacklisted(guild_id, message.channel.id):
        logger.debug(f"Channel {message.channel.id} is blacklisted, skipping")
        return
    
    # Get per-channel threshold
    threshold = get_channel_threshold(guild_id, message.channel.id, threshold)
    
    attachments = [a for a in message.attachments if is_image_attachment(a)]
    
    # Check for OCR text if enabled
    ocr_text = None
    ocr_confidence = None
    
    if ocr_enabled and (attachments or message.content.strip()):
        try:
            # Extract text from image attachments
            for attachment in attachments:
                if attachment.size < MAX_IMAGE_SIZE:
                    ocr_result = await process_image_for_ocr(attachment.url)
                    if ocr_result and len(ocr_result["text"]) >= OCR_MIN_TEXT_LENGTH:
                        ocr_text = ocr_result["text"]
                        ocr_confidence = ocr_result["confidence"]
                        break
            
            # If no image OCR, check message content
            if not ocr_text and message.content.strip():
                ocr_text = message.content
                ocr_confidence = 100.0
            
        except Exception as e:
            logger.debug(f"OCR processing failed: {e}")
    
    # Check if message has already been processed
    try:
        if await image_already_processed(guild_id, message.id):
            await add_controlled_reaction(message, "✅", guild_id, scope)
            return
    except Exception as e:
        logger.error(f"Error checking processed status: {e}")
        return
    
    loading_added = False
    if with_loading:
        try:
            await message.add_reaction("🔄")
            loading_added = True
        except (discord.Forbidden, discord.HTTPException):
            pass
    
    try:
        existing_hashes = await get_all_hashes_for_guild(guild_id)
        hash_duplicates = 0
        ocr_duplicates = 0
        first_duplicate_match = None
        best_similarity = 100
        ocr_similarity = None
        processed_hashes = []
        first_image_info = None
        
        # Hash-based detection
        if attachments:
            timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for attachment in attachments:
                    try:
                        data = await _download_image(attachment.url, session)
                        hash_val = _compute_hash(data)
                        img_info = _get_image_info(data)
                        
                        dup_count = 0
                        first_match = None
                        
                        for row in existing_hashes:
                            try:
                                dist = hamming_distance_hex(hash_val, row["hash_value"])
                                if dist <= threshold:
                                    dup_count += 1
                                    if first_match is None or dist < best_similarity:
                                        first_match = row
                                        best_similarity = dist
                            except Exception:
                                continue
                        
                        processed_hashes.append((hash_val, attachment.url, ocr_text, ocr_confidence))
                        hash_duplicates += dup_count
                        
                        if dup_count > 0 and first_duplicate_match is None:
                            first_duplicate_match = first_match
                            first_image_info = img_info
                            
                    except ImageProcessingError as e:
                        logger.debug(f"Skipping attachment: {e}")
                        continue
        
        # OCR-based detection
        if ocr_enabled and ocr_text and len(ocr_text) >= OCR_MIN_TEXT_LENGTH:
            similar_images = await get_similar_ocr_text(guild_id, ocr_text, ocr_threshold)
            
            if similar_images:
                ocr_duplicates = len(similar_images)
                if not first_duplicate_match and similar_images:
                    first_duplicate_match = similar_images[0]
                    best_similarity = first_duplicate_match.get("ocr_similarity", 0)
                    ocr_similarity = best_similarity
        
        # Determine if this is a duplicate
        is_duplicate = hash_duplicates > 0 or ocr_duplicates > 0
        detection_method = "hash" if hash_duplicates > 0 else ("ocr" if ocr_duplicates > 0 else "none")
        
        # Save hashes
        for hash_val, img_url, text, confidence in processed_hashes:
            try:
                await save_image_hash(
                    guild_id=guild_id,
                    hash_value=hash_val,
                    message_id=message.id,
                    channel_id=message.channel.id,
                    user_id=message.author.id,
                    image_url=img_url,
                    ocr_text=text,
                    ocr_confidence=confidence
                )
            except Exception as e:
                logger.error(f"Failed to save hash: {e}")
        
        # Update stats
        try:
            await update_user_stats(
                guild_id=guild_id,
                user_id=message.author.id,
                unique_delta=1 if not is_duplicate else 0,
                duplicate_delta=1 if is_duplicate else 0,
                ocr_duplicate_delta=1 if detection_method == "ocr" else 0
            )
        except Exception as e:
            logger.error(f"Failed to update stats: {e}")
        
        # Handle duplicates
        if is_duplicate and first_duplicate_match:
            try:
                await insert_duplicate_history(
                    guild_id=guild_id,
                    user_id=message.author.id,
                    user_name=str(message.author),
                    channel_id=message.channel.id,
                    message_id=message.id,
                    similarity_count=hash_duplicates + ocr_duplicates,
                    similarity_percent=((64 - best_similarity) / 64) * 100,
                    ocr_similarity_percent=ocr_similarity,
                    original_message_id=first_duplicate_match.get("message_id"),
                    action_taken=None,
                    detection_method=detection_method
                )
            except Exception as e:
                logger.error(f"Failed to log history: {e}")
            
            # Check warnings and take action
            user_stats = await get_user_stats(guild_id, message.author.id)
            warning_action = None
            if user_stats and isinstance(message.author, discord.Member):
                warning_action = await handle_user_warnings(
                    message.guild,
                    message.author,
                    user_stats["duplicate_count"]
                )
            
            # Send detailed log
            await send_detailed_log(
                guild=message.guild,
                message=message,
                duplicate_count=hash_duplicates + ocr_duplicates,
                first_match=first_duplicate_match,
                image_info=first_image_info or {},
                similarity_distance=best_similarity,
                ocr_similarity=ocr_similarity,
                threshold=threshold,
                detection_method=detection_method,
                warning_action=warning_action,
            )
            
            # Send alert
            try:
                original_user_id = first_duplicate_match.get("user_id")
                original_user = message.guild.get_member(original_user_id)
                credit_text = f"\n**Original by:** {original_user.mention}" if original_user else ""
                
                similarity_percent = ((64 - best_similarity) / 64) * 100
                detection_type = "image duplicate" if detection_method == "hash" else "text similarity"
                
                embed = discord.Embed(
                    title="🚨 Duplicate Detected" if detection_method == "hash" else "📝 Similar Content Detected",
                    description=f"This {detection_type} is {similarity_percent:.1f}% similar to a previously posted item.{credit_text}",
                    color=discord.Color.red() if detection_method == "hash" else discord.Color.purple(),
                )
                embed.add_field(name="Posted by", value=f"{message.author.mention}", inline=True)
                embed.add_field(name="Matches", value=str(hash_duplicates + ocr_duplicates), inline=True)
                embed.set_footer(text=f"View details in #{LOG_CHANNEL_NAME} • Detection: {detection_method.upper()}")
                
                await message.channel.send(embed=embed, delete_after=30)
                
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")
            
            # Auto-delete or quarantine
            quarantine_config = get_quarantine_config(guild_id)
            if auto_delete:
                if quarantine_config.get("enabled", False) and quarantine_config.get("channel_id"):
                    try:
                        quarantine_channel = await get_or_create_quarantine_channel(message.guild)
                        if quarantine_channel:
                            await quarantine_message(
                                guild_id,
                                message.id,
                                message.channel.id,
                                message.author.id,
                                message.attachments[0].url if message.attachments else "",
                                f"Auto-quarantined due to {detection_method} detection"
                            )
                            await message.delete()
                            logger.info(f"Quarantined duplicate {message.id}")
                    except Exception as e:
                        logger.warning(f"Failed to quarantine: {e}")
                        try:
                            await message.delete()
                            logger.info(f"Auto-deleted duplicate {message.id}")
                        except Exception as e2:
                            logger.warning(f"Failed to delete: {e2}")
                else:
                    try:
                        await message.delete()
                        logger.info(f"Auto-deleted duplicate {message.id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete: {e}")
        
        # Reactions
        if loading_added:
            try:
                await message.remove_reaction("🔄", message.guild.me)
            except Exception:
                pass
        
        if is_duplicate:
            await add_controlled_reaction(message, "❌", guild_id, scope)
        else:
            await add_controlled_reaction(message, "✅", guild_id, scope)
            
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        
        if loading_added:
            try:
                await message.remove_reaction("🔄", message.guild.me)
            except Exception:
                pass
        
        try:
            await add_controlled_reaction(message, "⚠️", guild_id, scope)
        except Exception:
            pass

def setup_detection(bot: commands.Bot) -> None:
    """Register message event handler."""
    
    @bot.event
    async def on_message(message: discord.Message):
        """Handle incoming messages."""
        if message.author.bot:
            await bot.process_commands(message)
            return
        
        # Process the message for duplicates
        asyncio.create_task(process_message_for_duplicates(
            message=message,
            scope="live",
            with_loading=True,
        ))
        
        await bot.process_commands(message)

