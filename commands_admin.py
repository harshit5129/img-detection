"""
Enhanced admin commands with logging channel management and OCR support.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import ensure_guild_config, guild_data, save_guild_config
from detection import process_message_for_duplicates
from db import (
    get_quarantined_messages, 
    release_quarantined_message,
    get_user_warnings,
    clear_user_warnings,
    get_guild_stats,
    get_user_stats,
    get_leaderboard,
    set_log_channel,
    get_log_channel,
)

logger = logging.getLogger("DuplicateDetector")

def register_admin_commands(bot: commands.Bot) -> None:
    """Register all admin commands."""
    
    # ---------- /setsimilarity ----------
    @bot.tree.command(
        name="setsimilarity",
        description="Set image similarity threshold (1-64). Lower = stricter. Admin only.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        threshold="Max Hamming distance for images to be considered similar (1-64)",
        ocr_threshold="Minimum text similarity percentage (0-100)"
    )
    async def setsimilarity(
        interaction: discord.Interaction,
        threshold: app_commands.Range[int, 1, 64],
        ocr_threshold: app_commands.Range[int, 0, 100] = 85
    ):
        """Set the similarity detection threshold."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            await ensure_guild_config(interaction.guild_id)
            cfg = guild_data[interaction.guild_id]["config"]
            
            old_threshold = cfg.get("hash_threshold", 5)
            old_ocr_threshold = cfg.get("ocr_threshold", 85)
            cfg["hash_threshold"] = int(threshold)
            cfg["ocr_threshold"] = int(ocr_threshold)
            
            await save_guild_config(interaction.guild_id)
            
            # Calculate approximate similarity percentage
            max_dist = 64
            new_percent = ((max_dist - threshold) / max_dist) * 100
            
            embed = discord.Embed(
                title="✅ Similarity Threshold Updated",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Image Threshold",
                value=f"**Old:** {old_threshold} → **New:** {threshold}\n"
                      f"Similarity: ~{new_percent:.1f}%",
                inline=True
            )
            embed.add_field(
                name="OCR Threshold",
                value=f"**Old:** {old_ocr_threshold}% → **New:** {ocr_threshold}%\n"
                      f"Minimum text similarity for detection",
                inline=True
            )
            embed.add_field(
                name="📊 What This Means",
                value=(
                    f"**Image Detection:**\n"
                    f"• Lower threshold = Stricter matching (fewer false positives)\n"
                    f"• Higher threshold = Lenient matching (catches more variations)\n\n"
                    f"**OCR Detection:**\n"
                    f"• Higher percentage = More similar text required\n"
                    f"• Lower percentage = More variations accepted"
                ),
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Guild {interaction.guild_id}: threshold {old_threshold} → {threshold}, ocr {old_ocr_threshold} → {ocr_threshold}")
            
        except Exception as e:
            logger.error(f"Error in setsimilarity: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while updating the threshold.",
                ephemeral=True
            )
    
    # ---------- /toggleautodelete ----------
    @bot.tree.command(
        name="toggleautodelete",
        description="Enable/disable automatic deletion of duplicate messages. Admin only.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        enabled="Whether to automatically delete duplicate messages",
        quarantine="Enable quarantine mode instead of direct deletion"
    )
    async def toggleautodelete(
        interaction: discord.Interaction,
        enabled: bool,
        quarantine: bool = False
    ):
        """Toggle automatic deletion of duplicates."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            await ensure_guild_config(interaction.guild_id)
            cfg = guild_data[interaction.guild_id]["config"]
            
            old_value = cfg.get("auto_delete", False)
            old_quarantine = cfg.get("quarantine_config", {}).get("enabled", False)
            
            cfg["auto_delete"] = bool(enabled)
            
            if "quarantine_config" not in cfg:
                cfg["quarantine_config"] = {}
            cfg["quarantine_config"]["enabled"] = bool(quarantine)
            
            await save_guild_config(interaction.guild_id)
            
            embed = discord.Embed(
                title=f"{'🗑️' if enabled else '💬'} Auto-Delete {'Enabled' if enabled else 'Disabled'}",
                color=discord.Color.green() if enabled else discord.Color.greyple()
            )
            
            if enabled:
                if quarantine:
                    embed.description = (
                        "✅ **Auto-quarantine is now ENABLED**\n\n"
                        "Duplicate messages will be moved to quarantine for review.\n"
                        "• Logs will still be sent to #duplicate-logs\n"
                        "• Users will see their message removed\n"
                        "• Original messages are kept\n\n"
                        "⚠️ **Note:** Quarantined images can be reviewed with `/quarantine`"
                    )
                else:
                    embed.description = (
                        "✅ **Auto-delete is now ENABLED**\n\n"
                        "Duplicate messages will be automatically deleted.\n"
                        "• Logs will still be sent to #duplicate-logs\n"
                        "• Users will see their message removed\n"
                        "• Original messages are kept\n\n"
                        "⚠️ **Warning:** Deleted messages cannot be recovered!"
                    )
            else:
                embed.description = (
                    "✅ **Auto-delete is now DISABLED**\n\n"
                    "Duplicate messages will be flagged but NOT deleted.\n"
                    "• Duplicates get ❌ reaction\n"
                    "• Alert sent in channel\n"
                    "• Detailed log sent to #duplicate-logs\n"
                    "• Messages remain visible"
                )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Guild {interaction.guild_id}: auto_delete {old_value} → {enabled}, quarantine {old_quarantine} → {quarantine}")
            
        except Exception as e:
            logger.error(f"Error in toggleautodelete: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while updating auto-delete settings.",
                ephemeral=True
            )
    
    # ---------- /setreactions ----------
    @bot.tree.command(
        name="setreactions",
        description="Configure emoji reactions for duplicate detection. Admin only.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        scope="Where to apply these settings",
        enabled="Turn reactions on or off",
        ttl="Auto-remove reactions after N seconds (0 = never, max 3600)"
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="Live messages", value="live"),
            app_commands.Choice(name="History scans (/scanhistory, /scanall)", value="scan"),
            app_commands.Choice(name="Both live and scans", value="both"),
        ]
    )
    async def setreactions(
        interaction: discord.Interaction,
        scope: str,
        enabled: bool,
        ttl: app_commands.Range[int, 0, 3600] = 0,
    ):
        """Configure reaction settings."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            await ensure_guild_config(interaction.guild_id)
            cfg = guild_data[interaction.guild_id]["config"]
            reactions = cfg.get("reaction_settings", {})
            
            if not reactions:
                reactions = {"live": {}, "scan": {}}
            
            targets = ["live", "scan"] if scope == "both" else [scope]
            
            for target in targets:
                current = reactions.get(target, {})
                current["enabled"] = bool(enabled)
                current["ttl"] = int(ttl)
                reactions[target] = current
            
            cfg["reaction_settings"] = reactions
            await save_guild_config(interaction.guild_id)
            
            scope_names = {
                "live": "live messages",
                "scan": "history scans",
                "both": "live messages & history scans"
            }
            
            embed = discord.Embed(
                title="✅ Reaction Settings Updated",
                description=f"**Scope:** {scope_names.get(scope, scope)}",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="Status",
                value="ON ✅" if enabled else "OFF ❌",
                inline=True
            )
            embed.add_field(
                name="Auto-remove",
                value=f"{ttl} seconds" if ttl > 0 else "Never (permanent)",
                inline=True
            )
            
            embed.add_field(
                name="📊 Reaction Types",
                value=(
                    "• 🔄 Processing...\n"
                    "• ✅ Unique image\n"
                    "• ❌ Duplicate detected\n"
                    "• ⚠️ Error processing"
                ),
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Guild {interaction.guild_id}: reactions updated for {scope}")
            
        except Exception as e:
            logger.error(f"Error in setreactions: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while updating reaction settings.",
                ephemeral=True
            )
    
    # ---------- /setuplogchannel ----------
    @bot.tree.command(
        name="setuplogchannel",
        description="Create or reset the #duplicate-logs channel. Admin only.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setuplogchannel(interaction: discord.Interaction):
        """Setup the duplicate logs channel."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Try to find existing channel
            existing_channel = None
            for channel in interaction.guild.text_channels:
                if channel.name == "duplicate-logs":
                    existing_channel = channel
                    break
            
            if existing_channel:
                # Save channel ID to database
                await set_log_channel(interaction.guild_id, existing_channel.id)
                
                embed = discord.Embed(
                    title="✅ Log Channel Already Exists",
                    description=f"The {existing_channel.mention} channel is already set up.",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="Channel Info",
                    value=(
                        f"**Name:** #{existing_channel.name}\n"
                        f"**ID:** {existing_channel.id}\n"
                        f"**Created:** <t:{int(existing_channel.created_at.timestamp())}:R>"
                    )
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                # Create new channel
                overwrites = {
                    interaction.guild.default_role: discord.PermissionOverwrite(send_messages=False),
                    interaction.guild.me: discord.PermissionOverwrite(
                        send_messages=True,
                        embed_links=True,
                        attach_files=True,
                        read_message_history=True
                    )
                }
                
                channel = await interaction.guild.create_text_channel(
                    name="duplicate-logs",
                    topic="🔍 Automatic duplicate image detection logs • Configure with /setsimilarity",
                    overwrites=overwrites,
                    reason=f"Created by {interaction.user}"
                )
                
                # Send welcome message
                welcome_embed = discord.Embed(
                    title="🔍 Duplicate Detection Logs",
                    description=(
                        "This channel tracks all duplicate image detections with detailed information.\n\n"
                        "**What You'll See Here:**\n"
                        "• Detailed duplicate reports with similarity scores\n"
                        "• User statistics and trends\n"
                        "• Image comparison links\n"
                        "• Original vs duplicate message links\n"
                        "• File size and format information\n"
                        "• OCR text comparison for similar content\n\n"
                        "**Configuration:**\n"
                        "• `/setsimilarity` - Adjust detection sensitivity\n"
                        "• `/toggleautodelete` - Auto-delete duplicates\n"
                        "• `/setreactions` - Configure reactions\n"
                        "• `/stats` - View server statistics"
                    ),
                    color=discord.Color.blue()
                )
                welcome_embed.set_footer(text="Duplicate Detector Bot • Keeping your server clean")
                await channel.send(embed=welcome_embed)
                
                # Save channel ID to database
                await set_log_channel(interaction.guild_id, channel.id)
                
                # Confirm to admin
                embed = discord.Embed(
                    title="✅ Log Channel Created",
                    description=f"Successfully created {channel.mention}",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Channel Details",
                    value=(
                        f"**Name:** #{channel.name}\n"
                        f"**ID:** {channel.id}\n"
                        f"**Permissions:** Read-only for members"
                    )
                )
                embed.add_field(
                    name="Next Steps",
                    value=(
                        "The bot will automatically log duplicates here.\n"
                        "Configure detection settings with `/setsimilarity`"
                    ),
                    inline=False
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                logger.info(f"Created duplicate-logs channel in {interaction.guild.name}")
                
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to create channels.\n"
                "Please grant me the 'Manage Channels' permission.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in setuplogchannel: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while setting up the log channel.",
                ephemeral=True
            )
    
    # ---------- /scanhistory ----------
    @bot.tree.command(
        name="scanhistory",
        description="Scan recent messages in this channel for duplicates. Manage Server permission required.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        limit="Number of recent messages to scan (1-1000, default: 200)"
    )
    async def scanhistory(
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 1000] = 200,
    ):
        """Scan channel history for duplicates."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ This command can only be used in text channels.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            channel = interaction.channel
            scanned = 0
            errors = 0
            duplicates_found = 0
            
            status_msg = await interaction.followup.send(
                f"🔄 Scanning {channel.mention}... (0/{limit} messages)",
                ephemeral=True
            )
            
            async for msg in channel.history(limit=limit, oldest_first=True):
                if msg.author.bot:
                    continue
                
                try:
                    # Track duplicates before scanning
                    stats_before = await get_user_stats(interaction.guild_id, msg.author.id)
                    
                    await process_message_for_duplicates(
                        message=msg,
                        scope="scan",
                        with_loading=False,
                    )
                    
                    # Check if duplicates increased
                    stats_after = await get_user_stats(interaction.guild_id, msg.author.id)
                    if stats_after and stats_before:
                        if stats_after["duplicate_count"] > stats_before["duplicate_count"]:
                            duplicates_found += 1
                    
                    scanned += 1
                    
                    if scanned % 50 == 0:
                        try:
                            await status_msg.edit(
                                content=f"🔄 Scanning {channel.mention}... ({scanned}/{limit} messages, {duplicates_found} duplicates)"
                            )
                        except Exception:
                            pass
                            
                except Exception as e:
                    logger.error(f"Error scanning message {msg.id}: {e}")
                    errors += 1
            
            embed = discord.Embed(
                title="✅ Channel Scan Complete",
                description=f"Finished scanning **{channel.mention}**",
                color=discord.Color.green()
            )
            embed.add_field(name="Messages scanned", value=str(scanned), inline=True)
            embed.add_field(name="Duplicates found", value=str(duplicates_found), inline=True)
            if errors > 0:
                embed.add_field(name="Errors", value=str(errors), inline=True)
            
            embed.set_footer(text="Check #duplicate-logs for detailed reports")
            
            await status_msg.edit(content=None, embed=embed)
            logger.info(f"Scan complete: {scanned} messages, {duplicates_found} duplicates")
            
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to read message history in this channel.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in scanhistory: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while scanning channel history.",
                ephemeral=True
            )
    
    # ---------- /scanall ----------
    @bot.tree.command(
        name="scanall",
        description="Scan messages in ALL text channels for duplicates. Administrator only.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        per_channel_limit="Messages per channel to scan (1-500, default: 100)"
    )
    async def scanall(
        interaction: discord.Interaction,
        per_channel_limit: app_commands.Range[int, 1, 500] = 100,
    ):
        """Scan all channels for duplicates."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            total_scanned = 0
            total_channels = 0
            total_duplicates = 0
            errors = 0
            
            status_msg = await interaction.followup.send(
                "🔄 Starting full server scan...",
                ephemeral=True
            )
            
            for channel in interaction.guild.text_channels:
                try:
                    channel_scanned = 0
                    
                    async for msg in channel.history(limit=per_channel_limit, oldest_first=True):
                        if msg.author.bot:
                            continue
                        
                        try:
                            stats_before = await get_user_stats(interaction.guild_id, msg.author.id)
                            
                            await process_message_for_duplicates(
                                message=msg,
                                scope="scan",
                                with_loading=False,
                            )
                            
                            stats_after = await get_user_stats(interaction.guild_id, msg.author.id)
                            if stats_after and stats_before:
                                if stats_after["duplicate_count"] > stats_before["duplicate_count"]:
                                    total_duplicates += 1
                            
                            channel_scanned += 1
                            total_scanned += 1
                        except Exception as e:
                            logger.error(f"Error scanning message {msg.id}: {e}")
                            errors += 1
                    
                    if channel_scanned > 0:
                        total_channels += 1
                    
                    if total_channels % 5 == 0:
                        try:
                            await status_msg.edit(
                                content=f"🔄 Scanning... {total_channels} channels, {total_scanned} messages, {total_duplicates} duplicates"
                            )
                        except Exception:
                            pass
                            
                except discord.Forbidden:
                    logger.warning(f"No permission in channel {channel.id}")
                    errors += 1
                except Exception as e:
                    logger.error(f"Error scanning channel {channel.id}: {e}")
                    errors += 1
            
            embed = discord.Embed(
                title="✅ Full Server Scan Complete",
                description=f"Scanned **{total_channels}** accessible channels",
                color=discord.Color.green()
            )
            embed.add_field(name="Total messages", value=str(total_scanned), inline=True)
            embed.add_field(name="Duplicates found", value=str(total_duplicates), inline=True)
            if errors > 0:
                embed.add_field(name="Errors/skipped", value=str(errors), inline=True)
            
            embed.set_footer(text="Use /stats to see updated statistics • Check #duplicate-logs for details")
            
            await status_msg.edit(content=None, embed=embed)
            logger.info(f"Full scan complete: {total_scanned} messages, {total_duplicates} duplicates")
            
        except Exception as e:
            logger.error(f"Error in scanall: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred during the full server scan.",
                ephemeral=True
            )
    
    # ---------- /quarantine ----------
    @bot.tree.command(
        name="quarantine",
        description="Manage quarantined duplicate images. Admin only.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(
        action=[
            app_commands.Choice(name="List", value="list"),
            app_commands.Choice(name="Release", value="release"),
            app_commands.Choice(name="Delete", value="delete"),
        ]
    )
    @app_commands.describe(
        message_id="Message ID to release or delete (required for release/delete)",
        reason="Reason for action (optional)"
    )
    async def quarantine(
        interaction: discord.Interaction,
        action: str,
        message_id: str = None,
        reason: str = None
    ):
        """Manage quarantined duplicate images."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            if action == "list":
                messages = await get_quarantined_messages(interaction.guild_id)
                
                if not messages:
                    await interaction.response.send_message(
                        "✅ No messages currently in quarantine.",
                        ephemeral=True
                    )
                    return
                
                # Create embeds for each quarantined message
                embeds = []
                for i, msg in enumerate(messages[:5]):  # Limit to 5 messages
                    embed = discord.Embed(
                        title=f"Quarantined Message #{i+1}",
                        description=(
                            f"**User:** <@{msg['user_id']}>\n"
                            f"**Channel:** <#{msg['channel_id']}>\n"
                            f"**Time:** <t:{int(datetime.fromisoformat(msg['timestamp']).timestamp())}:R>\n"
                            f"**Reason:** {msg['reason']}"
                        ),
                        color=discord.Color.orange()
                    )
                    embed.set_image(url=msg['image_url'])
                    embed.add_field(
                        name="Action",
                        value="Use `/quarantine release` with this message ID to release",
                        inline=False
                    )
                    embeds.append(embed)
                
                await interaction.response.send_message(
                    embeds=embeds,
                    ephemeral=True
                )
            
            elif action == "release":
                if not message_id or not message_id.isdigit():
                    await interaction.response.send_message(
                        "❌ Please provide a valid message ID.",
                        ephemeral=True
                    )
                    return
                
                success = await release_quarantined_message(
                    interaction.guild_id,
                    int(message_id),
                    interaction.user.id
                )
                
                if success:
                    await interaction.response.send_message(
                        f"✅ Message {message_id} has been released from quarantine.",
                        ephemeral=True
                    )
                    logger.info(f"Released quarantined message {message_id} in guild {interaction.guild_id}")
                else:
                    await interaction.response.send_message(
                        f"❌ Message {message_id} was not found in quarantine.",
                        ephemeral=True
                    )
            
            elif action == "delete":
                if not message_id or not message_id.isdigit():
                    await interaction.response.send_message(
                        "❌ Please provide a valid message ID.",
                        ephemeral=True
                    )
                    return
                
                try:
                    # Get channel and message
                    channel_id = None
                    for msg in await get_quarantined_messages(interaction.guild_id, unreleased_only=False):
                        if msg['message_id'] == int(message_id):
                            channel_id = msg['channel_id']
                            break
                    
                    if not channel_id:
                        await interaction.response.send_message(
                            f"❌ Message {message_id} not found in quarantine records.",
                            ephemeral=True
                        )
                        return
                    
                    # Delete the message
                    channel = interaction.guild.get_channel(channel_id)
                    if channel:
                        try:
                            message = await channel.fetch_message(int(message_id))
                            await message.delete(reason=f"Deleted by {interaction.user} - {reason or 'Quarantine cleanup'}")
                            
                            # Mark as released in database
                            await release_quarantined_message(
                                interaction.guild_id,
                                int(message_id),
                                interaction.user.id
                            )
                            
                            await interaction.response.send_message(
                                f"✅ Message {message_id} has been permanently deleted.",
                                ephemeral=True
                            )
                            logger.info(f"Deleted quarantined message {message_id} in guild {interaction.guild_id}")
                        except discord.NotFound:
                            await interaction.response.send_message(
                                f"❌ Message {message_id} was already deleted.",
                                ephemeral=True
                            )
                    else:
                        await interaction.response.send_message(
                            f"❌ Could not find channel for message {message_id}.",
                            ephemeral=True
                        )
                except Exception as e:
                    logger.error(f"Error deleting quarantined message: {e}")
                    await interaction.response.send_message(
                        f"❌ An error occurred while deleting message {message_id}.",
                        ephemeral=True
                    )
            
        except Exception as e:
            logger.error(f"Error in quarantine command: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while managing quarantine.",
                ephemeral=True
            )
    
    # ---------- /warnings ----------
    @bot.tree.command(
    name="userwarnings",
    description="Manage user warnings for duplicate posts. Admin only.",
    )       
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        user="User to view or manage warnings for",
        action="Action to perform (view, clear)",
        reason="Reason for clearing warnings"
    )
    async def warnings(
        interaction: discord.Interaction,
        user: discord.Member,
        action: str = "view",
        reason: str = None
    ):
        """Manage user warnings for duplicate posts."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            warnings = await get_user_warnings(interaction.guild_id, user.id)
            
            if action == "view":
                if not warnings:
                    await interaction.response.send_message(
                        f"✅ {user.mention} has no warnings.",
                        ephemeral=True
                    )
                    return
                
                # Create embeds for warnings
                embeds = []
                for i, warning in enumerate(warnings[:5]):  # Limit to 5 warnings
                    embed = discord.Embed(
                        title=f"Warning #{i+1}",
                        description=(
                            f"**Type:** {warning['warning_type']}\n"
                            f"**Reason:** {warning['reason']}\n"
                            f"**Action Taken:** {warning['action_taken'] or 'None'}\n"
                            f"**Time:** <t:{int(datetime.fromisoformat(warning['issued_at']).timestamp())}:R>"
                        ),
                        color=discord.Color.orange()
                    )
                    embeds.append(embed)
                
                await interaction.response.send_message(
                    embeds=embeds,
                    ephemeral=True
                )
            
            elif action == "clear":
                await clear_user_warnings(interaction.guild_id, user.id)
                
                await interaction.response.send_message(
                    f"✅ Cleared all warnings for {user.mention}.",
                    ephemeral=True
                )
                logger.info(f"Cleared warnings for user {user.id} in guild {interaction.guild_id}")
            
        except Exception as e:
            logger.error(f"Error in warnings command: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while managing user warnings.",
                ephemeral=True
            )
    
    # ---------- /warningconfig ----------
    @bot.tree.command(
        name="warningconfig",
        description="Configure warning system for duplicate posts. Admin only.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        enabled="Enable or disable the warning system",
        threshold="Number of duplicates to trigger this warning level",
        action="Action to take (warn, mute, kick)",
        message="Message to show for this warning level"
    )
    async def warningconfig(
        interaction: discord.Interaction,
        enabled: bool = None,
        threshold: app_commands.Range[int, 1, 100] = None,
        action: str = None,
        message: str = None
    ):
        """Configure the warning system for duplicate posts."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            await ensure_guild_config(interaction.guild_id)
            cfg = guild_data[interaction.guild_id]["config"]
            
            # Initialize warning config if missing
            if "warning_config" not in cfg:
                cfg["warning_config"] = {
                    "enabled": True,
                    "thresholds": [
                        {"count": 3, "action": "warn", "message": "⚠️ First Warning"},
                        {"count": 5, "action": "warn", "message": "⚠️ Second Warning"},
                        {"count": 10, "action": "mute", "message": "🔇 Auto-Muted"},
                        {"count": 20, "action": "kick", "message": "👢 Auto-Kicked"},
                    ],
                    "mute_duration": 3600,
                }
            
            warning_config = cfg["warning_config"]
            
            # Update configuration based on provided parameters
            if enabled is not None:
                warning_config["enabled"] = bool(enabled)
            
            if threshold is not None and action is not None:
                # Find existing threshold or create new one
                updated = False
                for i, level in enumerate(warning_config["thresholds"]):
                    if level["count"] == threshold:
                        warning_config["thresholds"][i]["action"] = action
                        if message:
                            warning_config["thresholds"][i]["message"] = message
                        updated = True
                        break
                
                if not updated:
                    warning_config["thresholds"].append({
                        "count": threshold,
                        "action": action,
                        "message": message or f"Auto-{action.capitalize()}",
                    })
                
                # Sort thresholds by count
                warning_config["thresholds"] = sorted(
                    warning_config["thresholds"],
                    key=lambda x: x["count"]
                )
            
            # Save updated config
            await save_guild_config(interaction.guild_id)
            
            # Create response embed
            embed = discord.Embed(
                title="✅ Warning Configuration Updated",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="System Enabled",
                value="Yes ✅" if warning_config["enabled"] else "No ❌",
                inline=True
            )
            
            # Format thresholds
            thresholds_text = ""
            for level in warning_config["thresholds"]:
                thresholds_text += (
                    f"• **{level['count']}** duplicates: "
                    f"**{level['action'].upper()}** - {level['message']}\n"
                )
            
            embed.add_field(
                name="Warning Thresholds",
                value=thresholds_text or "No thresholds configured",
                inline=False
            )
            
            if warning_config.get("mute_duration"):
                embed.add_field(
                    name="Mute Duration",
                    value=f"{format_duration(warning_config['mute_duration'])}",
                    inline=True
                )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Updated warning config for guild {interaction.guild_id}")
            
        except Exception as e:
            logger.error(f"Error in warningconfig: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while updating warning configuration.",
                ephemeral=True
            )
    
    logger.info("Admin commands registered successfully")
