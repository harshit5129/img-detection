"""
Advanced analytics and dashboard features for duplicate detection.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import discord
from discord import app_commands
from discord.ext import commands

from db import (
    get_db,
    get_guild_stats,
    get_user_stats,
    get_leaderboard,
    get_recent_duplicates,
)

logger = logging.getLogger("DuplicateDetector")

def register_analytics_commands(bot: commands.Bot) -> None:
    """Register advanced analytics commands."""
    
    @bot.tree.command(
        name="dashboard",
        description="View comprehensive analytics dashboard for your server"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dashboard(interaction: discord.Interaction):
        """Display advanced analytics dashboard."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            guild_stats = await get_guild_stats(interaction.guild_id)
            leaderboard = await get_leaderboard(interaction.guild_id, limit=5)
            recent_dups = await get_recent_duplicates(interaction.guild_id, hours=24, limit=10)
            
            # Main dashboard embed
            embed = discord.Embed(
                title=f"📊 Analytics Dashboard - {interaction.guild.name}",
                description="Comprehensive duplicate detection statistics",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            
            # Overall statistics
            total_posts = guild_stats["total_unique"] + guild_stats["total_duplicates"]
            dup_rate = (guild_stats["total_duplicates"] / total_posts * 100) if total_posts > 0 else 0
            
            embed.add_field(
                name="📈 Overall Statistics",
                value=(
                    f"**Total Images:** {guild_stats['total_images']:,}\n"
                    f"**Total Messages:** {guild_stats['total_messages']:,}\n"
                    f"**Unique Posts:** {guild_stats['total_unique']:,} ✅\n"
                    f"**Duplicates:** {guild_stats['total_duplicates']:,} ❌\n"
                    f"**Duplicate Rate:** {dup_rate:.1f}%\n"
                    f"**Warnings Issued:** {guild_stats['total_warnings']:,}"
                ),
                inline=False
            )
            
            # Detection breakdown
            ocr_percent = (guild_stats["total_ocr_duplicates"] / guild_stats["total_duplicates"] * 100) if guild_stats["total_duplicates"] > 0 else 0
            hash_duplicates = guild_stats["total_duplicates"] - guild_stats["total_ocr_duplicates"]
            
            embed.add_field(
                name="🔍 Detection Methods",
                value=(
                    f"**Hash-based:** {hash_duplicates:,} ({100-ocr_percent:.1f}%)\n"
                    f"**OCR-based:** {guild_stats['total_ocr_duplicates']:,} ({ocr_percent:.1f}%)"
                ),
                inline=True
            )
            
            # Recent activity (last 24 hours)
            recent_count = len(recent_dups)
            embed.add_field(
                name="🕐 Last 24 Hours",
                value=(
                    f"**Duplicates Detected:** {recent_count:,}\n"
                    f"**Average per Hour:** {recent_count/24:.1f}"
                ),
                inline=True
            )
            
            # Top duplicate posters
            if leaderboard:
                top_users = "\n".join([
                    f"{i+1}. <@{user['user_id']}> - {user['duplicate_count']} duplicates"
                    for i, user in enumerate(leaderboard[:5])
                ])
            else:
                top_users = "*No data yet*"
            
            embed.add_field(
                name="🏆 Top Duplicate Posters",
                value=top_users,
                inline=False
            )
            
            # Health indicators
            health_emoji = "🟢" if dup_rate < 20 else "🟡" if dup_rate < 40 else "🔴"
            health_status = "Excellent" if dup_rate < 20 else "Good" if dup_rate < 40 else "Needs Attention"
            
            embed.add_field(
                name=f"{health_emoji} Server Health",
                value=(
                    f"**Status:** {health_status}\n"
                    f"**Recommendation:** " + (
                        "Keep up the great work! 🎉" if dup_rate < 20 else
                        "Consider educating users about duplicates" if dup_rate < 40 else
                        "Review warning system settings"
                    )
                ),
                inline=False
            )
            
            embed.set_footer(text="Use /analytics for detailed reports")
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in dashboard: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while generating the dashboard.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="analytics",
        description="View detailed analytics for specific time periods"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        period="Time period to analyze",
        channel="Specific channel to analyze (optional)"
    )
    @app_commands.choices(period=[
        app_commands.Choice(name="Last 24 Hours", value="24h"),
        app_commands.Choice(name="Last 7 Days", value="7d"),
        app_commands.Choice(name="Last 30 Days", value="30d"),
        app_commands.Choice(name="All Time", value="all"),
    ])
    async def analytics(
        interaction: discord.Interaction,
        period: str,
        channel: Optional[discord.TextChannel] = None
    ):
        """Display detailed analytics for a time period."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Calculate time range
            hours_map = {"24h": 24, "7d": 168, "30d": 720, "all": None}
            hours = hours_map.get(period)
            
            # Get duplicates for period
            if hours:
                duplicates = await get_recent_duplicates(interaction.guild_id, hours=hours, limit=1000)
            else:
                # Get all duplicates
                db = get_db()
                cursor = db.duplicate_history.find(
                    {"guild_id": interaction.guild_id}
                ).sort("timestamp", -1).limit(1000)
                duplicates = await cursor.to_list(length=1000)
            
            # Filter by channel if specified
            if channel:
                duplicates = [d for d in duplicates if d["channel_id"] == channel.id]
            
            # Analyze data
            total_dups = len(duplicates)
            hash_dups = len([d for d in duplicates if d.get("detection_method") == "hash"])
            ocr_dups = len([d for d in duplicates if d.get("detection_method") == "ocr"])
            
            # User frequency
            user_freq: Dict[int, int] = {}
            for dup in duplicates:
                user_id = dup["user_id"]
                user_freq[user_id] = user_freq.get(user_id, 0) + 1
            
            # Channel frequency
            channel_freq: Dict[int, int] = {}
            for dup in duplicates:
                ch_id = dup["channel_id"]
                channel_freq[ch_id] = channel_freq.get(ch_id, 0) + 1
            
            # Time distribution (by hour of day)
            hour_freq = [0] * 24
            for dup in duplicates:
                hour = dup["timestamp"].hour if isinstance(dup["timestamp"], datetime) else datetime.fromisoformat(str(dup["timestamp"])).hour
                hour_freq[hour] += 1
            
            peak_hour = hour_freq.index(max(hour_freq)) if hour_freq else 0
            
            # Create embed
            period_names = {"24h": "Last 24 Hours", "7d": "Last 7 Days", "30d": "Last 30 Days", "all": "All Time"}
            
            embed = discord.Embed(
                title=f"📊 Detailed Analytics - {period_names.get(period, period)}",
                description=f"Channel: {channel.mention if channel else 'All Channels'}",
                color=discord.Color.purple(),
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📈 Summary",
                value=(
                    f"**Total Duplicates:** {total_dups:,}\n"
                    f"**Hash Detection:** {hash_dups:,}\n"
                    f"**OCR Detection:** {ocr_dups:,}\n"
                    f"**Peak Hour:** {peak_hour}:00 UTC ({hour_freq[peak_hour]} duplicates)"
                ),
                inline=False
            )
            
            # Top channels
            if channel_freq and not channel:
                top_channels = sorted(channel_freq.items(), key=lambda x: x[1], reverse=True)[:5]
                channels_text = "\n".join([
                    f"<#{ch_id}>: {count} duplicates"
                    for ch_id, count in top_channels
                ])
                embed.add_field(
                    name="📍 Top Channels",
                    value=channels_text,
                    inline=False
                )
            
            # Top users
            if user_freq:
                top_users = sorted(user_freq.items(), key=lambda x: x[1], reverse=True)[:5]
                users_text = "\n".join([
                    f"<@{user_id}>: {count} duplicates"
                    for user_id, count in top_users
                ])
                embed.add_field(
                    name="👥 Top Posters",
                    value=users_text,
                    inline=False
                )
            
            # Average per day
            if hours and hours > 24:
                days = hours / 24
                avg_per_day = total_dups / days
                embed.add_field(
                    name="📊 Trends",
                    value=f"**Average per Day:** {avg_per_day:.1f} duplicates",
                    inline=False
                )
            
            embed.set_footer(text="Data based on recorded duplicate history")
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in analytics: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while generating analytics.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="trends",
        description="View duplicate detection trends over time"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trends(interaction: discord.Interaction):
        """Display trend analysis."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            db = get_db()
            
            # Get daily stats for last 30 days
            thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
            cursor = db.daily_stats.find({
                "guild_id": interaction.guild_id,
                "date": {"$gte": thirty_days_ago}
            }).sort("date", -1)
            
            daily_stats = await cursor.to_list(length=30)
            
            if not daily_stats:
                await interaction.followup.send(
                    "📊 Not enough data yet. Start using the bot to build statistics!",
                    ephemeral=True
                )
                return
            
            # Calculate trends
            total_images = sum(d.get("total_images", 0) for d in daily_stats)
            total_dups = sum(d.get("duplicates_detected", 0) for d in daily_stats)
            avg_images_per_day = total_images / len(daily_stats)
            avg_dups_per_day = total_dups / len(daily_stats)
            
            # Last 7 days vs previous 7 days
            if len(daily_stats) >= 14:
                recent_week = daily_stats[:7]
                prev_week = daily_stats[7:14]
                
                recent_dups = sum(d.get("duplicates_detected", 0) for d in recent_week)
                prev_dups = sum(d.get("duplicates_detected", 0) for d in prev_week)
                
                change_percent = ((recent_dups - prev_dups) / prev_dups * 100) if prev_dups > 0 else 0
                trend_emoji = "📈" if change_percent > 0 else "📉" if change_percent < 0 else "➡️"
            else:
                change_percent = 0
                trend_emoji = "➡️"
            
            embed = discord.Embed(
                title="📈 Duplicate Detection Trends",
                description=f"Analysis of last {len(daily_stats)} days",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 Overall Averages",
                value=(
                    f"**Images per Day:** {avg_images_per_day:.1f}\n"
                    f"**Duplicates per Day:** {avg_dups_per_day:.1f}\n"
                    f"**Total Images:** {total_images:,}\n"
                    f"**Total Duplicates:** {total_dups:,}"
                ),
                inline=False
            )
            
            if len(daily_stats) >= 14:
                embed.add_field(
                    name=f"{trend_emoji} Weekly Trend",
                    value=(
                        f"**Last 7 Days:** {recent_dups} duplicates\n"
                        f"**Previous 7 Days:** {prev_dups} duplicates\n"
                        f"**Change:** {change_percent:+.1f}%"
                    ),
                    inline=False
                )
            
            # Recent days breakdown
            recent_days_text = "\n".join([
                f"`{d['date']}`: {d.get('duplicates_detected', 0)} duplicates"
                for d in daily_stats[:7]
            ])
            
            embed.add_field(
                name="📅 Last 7 Days",
                value=recent_days_text,
                inline=False
            )
            
            embed.set_footer(text="Daily statistics are updated automatically")
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in trends: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while generating trends.",
                ephemeral=True
            )
