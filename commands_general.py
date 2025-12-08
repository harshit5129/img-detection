"""
General user commands with enhanced formatting and error handling.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from db import get_guild_stats, get_user_stats, get_leaderboard
from utils import format_number, format_duration
from config import guild_data

logger = logging.getLogger("DuplicateDetector")

def register_general_commands(bot: commands.Bot) -> None:
    """Register all general user commands."""
    
    @bot.tree.command(
        name="ping",
        description="Check bot responsiveness and latency."
    )
    async def ping(interaction: discord.Interaction):
        """Simple ping command to test bot responsiveness."""
        try:
            latency_ms = round(bot.latency * 1000, 2)
            
            embed = discord.Embed(
                title="🏓 Pong!",
                description=f"Bot is online and responsive.",
                color=discord.Color.green()
            )
            embed.add_field(name="Latency", value=f"{latency_ms}ms", inline=True)
            embed.add_field(name="Guilds", value=str(len(bot.guilds)), inline=True)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in ping command: {e}")
            await interaction.response.send_message(
                "❌ Error checking bot status.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="stats",
        description="View duplicate detection statistics for this server."
    )
    async def stats(interaction: discord.Interaction):
        """Display server-wide duplicate detection statistics."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            data = await get_guild_stats(interaction.guild_id)
            
            embed = discord.Embed(
                title=f"📊 Statistics for {interaction.guild.name}",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="📸 Total Images Tracked",
                value=format_number(data["total_images"]),
                inline=True,
            )
            embed.add_field(
                name="💬 Messages with Images",
                value=format_number(data["total_messages"]),
                inline=True,
            )
            embed.add_field(
                name="✨ Unique Images",
                value=format_number(data["total_unique"]),
                inline=True,
            )
            embed.add_field(
                name="🔄 Duplicate Posts",
                value=format_number(data["total_duplicates"]),
                inline=True,
            )
            embed.add_field(
                name="📝 OCR Duplicates",
                value=format_number(data["total_ocr_duplicates"]),
                inline=True,
            )
            
            # Calculate duplicate rate
            if data["total_images"] > 0:
                dup_rate = (data["total_duplicates"] / data["total_images"]) * 100
                embed.add_field(
                    name="📈 Duplicate Rate",
                    value=f"{dup_rate:.1f}%",
                    inline=True,
                )
            
            embed.set_footer(text="Use /userstats to see your personal statistics")
            
            if interaction.guild.icon:
                embed.set_thumbnail(url=interaction.guild.icon.url)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in stats command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Error retrieving server statistics.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="userstats",
        description="View duplicate detection statistics for a specific user."
    )
    @app_commands.describe(
        member="User to check (leave empty to check yourself)"
    )
    async def userstats(
        interaction: discord.Interaction,
        member: discord.Member = None
    ):
        """Display user-specific duplicate detection statistics."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            member = member or interaction.user
            
            if not isinstance(member, discord.Member):
                await interaction.response.send_message(
                    "❌ User not found in this server.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            data = await get_user_stats(interaction.guild_id, member.id)
            
            if not data or (data["unique_count"] == 0 and 
                           data["duplicate_count"] == 0 and
                           data["ocr_duplicate_count"] == 0):
                await interaction.followup.send(
                    f"📊 No statistics recorded yet for {member.mention}.\n"
                    f"*Stats are tracked when users post images or similar text.*",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title=f"📈 Statistics for {member.display_name}",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.set_thumbnail(url=member.display_avatar.url)
            
            embed.add_field(
                name="✨ Unique Images Posted",
                value=format_number(data["unique_count"]),
                inline=True,
            )
            embed.add_field(
                name="🔄 Duplicate Posts",
                value=format_number(data["duplicate_count"]),
                inline=True,
            )
            embed.add_field(
                name="📝 OCR Duplicates",
                value=format_number(data["ocr_duplicate_count"]),
                inline=True,
            )
            
            # Calculate user's duplicate rate
            total = data["unique_count"] + data["duplicate_count"]
            if total > 0:
                dup_rate = (data["duplicate_count"] / total) * 100
                embed.add_field(
                    name="📊 Duplicate Rate",
                    value=f"{dup_rate:.1f}%",
                    inline=True,
                )
            
            embed.set_footer(text=f"User ID: {member.id}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in userstats command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Error retrieving user statistics.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="leaderboard",
        description="View top users with most duplicate posts in this server."
    )
    async def leaderboard(interaction: discord.Interaction):
        """Display leaderboard of users with most duplicates."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            records = await get_leaderboard(interaction.guild_id, limit=10)
            
            if not records:
                await interaction.followup.send(
                    "📊 No leaderboard data yet for this server.\n"
                    "*Data will appear as members post images.*",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title=f"🏆 Duplicate Leaderboard — {interaction.guild.name}",
                description="*Top users by duplicate post count*",
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow()
            )
            
            medal_emojis = ["🥇", "🥈", "🥉"]
            
            lines = []
            for idx, row in enumerate(records, start=1):
                user = interaction.guild.get_member(row["user_id"])
                
                if user:
                    name = user.mention
                else:
                    name = f"*Unknown User* (`{row['user_id']}`)"
                
                medal = medal_emojis[idx - 1] if idx <= 3 else f"**{idx}.**"
                
                lines.append(
                    f"{medal} {name}\n"
                    f"├ Duplicates: **{format_number(row['duplicate_count'])}**\n"
                    f"├ OCR Duplicates: {format_number(row['ocr_duplicate_count'])}\n"
                    f"└ Unique: {format_number(row['unique_count'])}"
                )
            
            embed.description += "\n\n" + "\n\n".join(lines)
            
            if interaction.guild.icon:
                embed.set_thumbnail(url=interaction.guild.icon.url)
            
            embed.set_footer(text="Use /userstats to see detailed stats for a specific user")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in leaderboard command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Error retrieving leaderboard data.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="warnings",
        description="View your warning history for duplicate posts."
    )
    async def my_warnings(interaction: discord.Interaction):
        """View user's warning history for duplicate posts."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            warnings = await get_user_warnings(interaction.guild_id, interaction.user.id)
            
            if not warnings:
                await interaction.followup.send(
                    "✅ You have no warnings.",
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
            
            await interaction.followup.send(
                embeds=embeds,
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Error in warnings command: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Error retrieving warning history.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="help",
        description="Display information about bot commands and features."
    )
    async def help_command(interaction: discord.Interaction):
        """Display help information."""
        embed = discord.Embed(
            title="🤖 Duplicate Image Detector - Help",
            description="I automatically detect and track duplicate images and similar text posted in your server.",
            color=discord.Color.blue()
        )
        
        # General commands
        embed.add_field(
            name="📊 General Commands",
            value=(
                "`/ping` - Check bot status\n"
                "`/stats` - View server statistics\n"
                "`/userstats [user]` - View user statistics\n"
                "`/leaderboard` - View top duplicate posters\n"
                "`/warnings` - View your warning history\n"
                "`/help` - Show this help message"
            ),
            inline=False
        )
        
        # Admin commands
        if interaction.guild and interaction.user.guild_permissions.administrator:
            embed.add_field(
                name="⚙️ Admin Commands",
                value=(
                    "`/setsimilarity` - Set detection threshold\n"
                    "`/toggleautodelete` - Enable/disable auto-deletion\n"
                    "`/setreactions` - Configure reaction settings\n"
                    "`/scanhistory` - Scan channel history\n"
                    "`/scanall` - Scan all channels\n"
                    "`/quarantine` - Manage quarantined images\n"
                    "`/warningconfig` - Configure warning system"
                ),
                inline=False
            )
        
        # How it works
        embed.add_field(
            name="🔍 How It Works",
            value=(
                "• Images are analyzed using perceptual hashing\n"
                "• Similar images are detected even if resized or compressed\n"
                "• Text content is analyzed with OCR for similar content\n"
                "• Reactions indicate status: ✅ unique, ❌ duplicate, ⚠️ error\n"
                "• Admins can configure auto-deletion and sensitivity"
            ),
            inline=False
        )
        
        embed.set_footer(text="Made with ❤️ using discord.py")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    logger.info("General commands registered successfully")
