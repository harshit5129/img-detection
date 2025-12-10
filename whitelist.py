"""
Smart whitelist management with role-based and automatic features.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

from config import ensure_guild_config, guild_data, save_guild_config
from db import get_db, get_user_stats

logger = logging.getLogger("DuplicateDetector")

def register_whitelist_commands(bot: commands.Bot) -> None:
    """Register smart whitelist management commands."""
    
    @bot.tree.command(
        name="whitelist",
        description="Manage user and channel whitelists (exempts from duplicate detection)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        action="Action to perform",
        target="User or channel to whitelist/unwhitelist",
        duration="Duration in hours (optional, for temporary whitelist)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add User", value="add_user"),
        app_commands.Choice(name="Remove User", value="remove_user"),
        app_commands.Choice(name="Add Channel", value="add_channel"),
        app_commands.Choice(name="Remove Channel", value="remove_channel"),
        app_commands.Choice(name="List", value="list"),
    ])
    async def whitelist(
        interaction: discord.Interaction,
        action: str,
        target: Optional[str] = None,
        duration: Optional[int] = None
    ):
        """Manage whitelists with smart features."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            await ensure_guild_config(interaction.guild_id)
            cfg = guild_data[interaction.guild_id]["config"]
            
            if action == "list":
                # Show current whitelists
                user_wl = cfg.get("user_whitelist", set())
                channel_wl = cfg.get("whitelist", set())
                
                embed = discord.Embed(
                    title="📋 Whitelist Configuration",
                    description="Users and channels exempt from duplicate detection",
                    color=discord.Color.blue()
                )
                
                if user_wl:
                    users_text = "\n".join([f"<@{uid}>" for uid in list(user_wl)[:20]])
                    if len(user_wl) > 20:
                        users_text += f"\n*...and {len(user_wl) - 20} more*"
                else:
                    users_text = "*No whitelisted users*"
                
                embed.add_field(
                    name=f"👥 Whitelisted Users ({len(user_wl)})",
                    value=users_text,
                    inline=False
                )
                
                if channel_wl:
                    channels_text = "\n".join([f"<#{cid}>" for cid in list(channel_wl)[:20]])
                    if len(channel_wl) > 20:
                        channels_text += f"\n*...and {len(channel_wl) - 20} more*"
                else:
                    channels_text = "*No whitelisted channels*"
                
                embed.add_field(
                    name=f"📍 Whitelisted Channels ({len(channel_wl)})",
                    value=channels_text,
                    inline=False
                )
                
                embed.set_footer(text="Use /whitelist to add or remove entries")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            # Parse target
            if not target:
                await interaction.response.send_message(
                    "❌ Please specify a target user or channel.",
                    ephemeral=True
                )
                return
            
            # Try to parse as mention or ID
            target_id = None
            if target.startswith("<@") and target.endswith(">"):
                # User mention
                target_id = int(target.strip("<@!>"))
            elif target.startswith("<#") and target.endswith(">"):
                # Channel mention
                target_id = int(target.strip("<#>"))
            elif target.isdigit():
                # Direct ID
                target_id = int(target)
            else:
                await interaction.response.send_message(
                    "❌ Invalid target. Please mention a user/channel or provide an ID.",
                    ephemeral=True
                )
                return
            
            if action == "add_user":
                user_wl = cfg.get("user_whitelist", set())
                user_wl.add(target_id)
                cfg["user_whitelist"] = user_wl
                
                await save_guild_config(interaction.guild_id)
                
                # Get user info if possible
                user = interaction.guild.get_member(target_id)
                user_mention = user.mention if user else f"<@{target_id}>"
                
                embed = discord.Embed(
                    title="✅ User Whitelisted",
                    description=f"{user_mention} has been added to the whitelist",
                    color=discord.Color.green()
                )
                
                if duration:
                    embed.add_field(
                        name="⏰ Duration",
                        value=f"Temporary whitelist for {duration} hours",
                        inline=False
                    )
                    embed.set_footer(text="Note: Temporary whitelist auto-removal not yet implemented")
                
                embed.add_field(
                    name="📝 Effect",
                    value="This user's images will no longer be checked for duplicates",
                    inline=False
                )
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                logger.info(f"Added user {target_id} to whitelist in guild {interaction.guild_id}")
            
            elif action == "remove_user":
                user_wl = cfg.get("user_whitelist", set())
                if target_id in user_wl:
                    user_wl.remove(target_id)
                    cfg["user_whitelist"] = user_wl
                    await save_guild_config(interaction.guild_id)
                    
                    await interaction.response.send_message(
                        f"✅ <@{target_id}> has been removed from the whitelist.",
                        ephemeral=True
                    )
                    logger.info(f"Removed user {target_id} from whitelist in guild {interaction.guild_id}")
                else:
                    await interaction.response.send_message(
                        f"❌ <@{target_id}> is not in the whitelist.",
                        ephemeral=True
                    )
            
            elif action == "add_channel":
                channel_wl = cfg.get("whitelist", set())
                channel_wl.add(target_id)
                cfg["whitelist"] = channel_wl
                
                await save_guild_config(interaction.guild_id)
                
                embed = discord.Embed(
                    title="✅ Channel Whitelisted",
                    description=f"<#{target_id}> has been added to the whitelist",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="📝 Effect",
                    value="Duplicate detection is DISABLED in this channel",
                    inline=False
                )
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                logger.info(f"Added channel {target_id} to whitelist in guild {interaction.guild_id}")
            
            elif action == "remove_channel":
                channel_wl = cfg.get("whitelist", set())
                if target_id in channel_wl:
                    channel_wl.remove(target_id)
                    cfg["whitelist"] = channel_wl
                    await save_guild_config(interaction.guild_id)
                    
                    await interaction.response.send_message(
                        f"✅ <#{target_id}> has been removed from the whitelist.",
                        ephemeral=True
                    )
                    logger.info(f"Removed channel {target_id} from whitelist in guild {interaction.guild_id}")
                else:
                    await interaction.response.send_message(
                        f"❌ <#{target_id}> is not in the whitelist.",
                        ephemeral=True
                    )
            
        except Exception as e:
            logger.error(f"Error in whitelist command: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while managing the whitelist.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="autowhitelist",
        description="Configure automatic whitelist based on roles or user behavior"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        mode="Whitelist mode to configure",
        role="Role to use for role-based whitelist",
        threshold="Minimum unique posts required for trust-based whitelist"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Role-based (whitelist users with specific role)", value="role"),
        app_commands.Choice(name="Trust-based (whitelist users with good history)", value="trust"),
        app_commands.Choice(name="Disable auto-whitelist", value="disable"),
        app_commands.Choice(name="View current settings", value="view"),
    ])
    async def autowhitelist(
        interaction: discord.Interaction,
        mode: str,
        role: Optional[discord.Role] = None,
        threshold: Optional[int] = None
    ):
        """Configure smart automatic whitelist."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            db = get_db()
            
            if mode == "view":
                # Show current auto-whitelist settings
                config = await db.guild_config.find_one({"guild_id": interaction.guild_id})
                auto_wl = config.get("auto_whitelist", {}) if config else {}
                
                embed = discord.Embed(
                    title="🤖 Auto-Whitelist Configuration",
                    description="Automatic whitelist settings for this server",
                    color=discord.Color.blue()
                )
                
                role_id = auto_wl.get("role_id")
                if role_id:
                    embed.add_field(
                        name="👥 Role-based",
                        value=f"**Status:** Enabled\n**Role:** <@&{role_id}>",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="👥 Role-based",
                        value="**Status:** Disabled",
                        inline=False
                    )
                
                trust_threshold = auto_wl.get("trust_threshold")
                if trust_threshold:
                    embed.add_field(
                        name="⭐ Trust-based",
                        value=f"**Status:** Enabled\n**Threshold:** {trust_threshold} unique posts",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="⭐ Trust-based",
                        value="**Status:** Disabled",
                        inline=False
                    )
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            # Update settings
            if mode == "role":
                if not role:
                    await interaction.response.send_message(
                        "❌ Please specify a role for role-based whitelist.",
                        ephemeral=True
                    )
                    return
                
                await db.guild_config.update_one(
                    {"guild_id": interaction.guild_id},
                    {"$set": {"auto_whitelist.role_id": role.id}},
                    upsert=True
                )
                
                embed = discord.Embed(
                    title="✅ Role-based Auto-Whitelist Enabled",
                    description=f"Users with the {role.mention} role will be automatically whitelisted",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="📝 How it works",
                    value=(
                        "• When duplicate detection runs, users with this role are exempt\n"
                        "• This is checked in real-time, no need to manually add users\n"
                        "• Removing the role from a user immediately removes their exemption"
                    ),
                    inline=False
                )
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                logger.info(f"Enabled role-based auto-whitelist for {role.name} in guild {interaction.guild_id}")
            
            elif mode == "trust":
                if threshold is None:
                    threshold = 50  # Default threshold
                
                await db.guild_config.update_one(
                    {"guild_id": interaction.guild_id},
                    {"$set": {"auto_whitelist.trust_threshold": threshold}},
                    upsert=True
                )
                
                embed = discord.Embed(
                    title="✅ Trust-based Auto-Whitelist Enabled",
                    description=f"Users with **{threshold}+** unique posts will be automatically trusted",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="📝 How it works",
                    value=(
                        f"• Users who post {threshold} unique (non-duplicate) images are trusted\n"
                        "• Trusted users are exempt from strict duplicate checking\n"
                        "• Trust status is re-evaluated on each post\n"
                        "• This rewards quality contributors!"
                    ),
                    inline=False
                )
                embed.add_field(
                    name="⚙️ Customize",
                    value=f"Change threshold with `/autowhitelist mode:trust threshold:<number>`",
                    inline=False
                )
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                logger.info(f"Enabled trust-based auto-whitelist (threshold: {threshold}) in guild {interaction.guild_id}")
            
            elif mode == "disable":
                await db.guild_config.update_one(
                    {"guild_id": interaction.guild_id},
                    {"$unset": {"auto_whitelist": ""}},
                    upsert=True
                )
                
                await interaction.response.send_message(
                    "✅ Auto-whitelist has been disabled. Manual whitelist remains active.",
                    ephemeral=True
                )
                logger.info(f"Disabled auto-whitelist in guild {interaction.guild_id}")
            
        except Exception as e:
            logger.error(f"Error in autowhitelist command: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while configuring auto-whitelist.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="trustcheck",
        description="Check if a user qualifies for trust-based whitelist"
    )
    @app_commands.describe(user="User to check trust status for")
    async def trustcheck(interaction: discord.Interaction, user: discord.Member):
        """Check trust status of a user."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            # Get user stats
            stats = await get_user_stats(interaction.guild_id, user.id)
            
            # Get trust threshold
            db = get_db()
            config = await db.guild_config.find_one({"guild_id": interaction.guild_id})
            trust_threshold = config.get("auto_whitelist", {}).get("trust_threshold") if config else None
            
            if not stats:
                await interaction.response.send_message(
                    f"📊 {user.mention} has no posting history yet.",
                    ephemeral=True
                )
                return
            
            unique_count = stats.get("unique_count", 0)
            dup_count = stats.get("duplicate_count", 0)
            total = unique_count + dup_count
            dup_rate = (dup_count / total * 100) if total > 0 else 0
            
            is_trusted = trust_threshold and unique_count >= trust_threshold
            
            embed = discord.Embed(
                title=f"{'✅' if is_trusted else '📊'} Trust Status - {user.display_name}",
                description=f"Analysis of {user.mention}'s posting behavior",
                color=discord.Color.green() if is_trusted else discord.Color.blue()
            )
            
            embed.add_field(
                name="📈 Statistics",
                value=(
                    f"**Total Posts:** {total}\n"
                    f"**Unique Posts:** {unique_count} ✅\n"
                    f"**Duplicates:** {dup_count} ❌\n"
                    f"**Duplicate Rate:** {dup_rate:.1f}%"
                ),
                inline=False
            )
            
            if trust_threshold:
                progress = min(100, (unique_count / trust_threshold * 100))
                progress_bar = "█" * int(progress / 10) + "░" * (10 - int(progress / 10))
                
                embed.add_field(
                    name="⭐ Trust Progress",
                    value=(
                        f"{progress_bar} {progress:.0f}%\n"
                        f"**Current:** {unique_count} unique posts\n"
                        f"**Required:** {trust_threshold} unique posts\n"
                        f"**Status:** {'**TRUSTED** 🎉' if is_trusted else f'{trust_threshold - unique_count} more needed'}"
                    ),
                    inline=False
                )
            else:
                embed.add_field(
                    name="⚙️ Trust System",
                    value="Trust-based whitelist is not enabled on this server.",
                    inline=False
                )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in trustcheck command: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ An error occurred while checking trust status.",
                ephemeral=True
            )
