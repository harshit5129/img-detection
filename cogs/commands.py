import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import csv
import json
import os
import time
import asyncio
from typing import Optional

from config import guild_data, TOKEN, ADMIN_ROLE_NAME, HASH_THRESHOLD, LOG_CHANNEL_NAME, AUTO_DELETE_DUPLICATES, ReactionType, ReactionConfig, DEFAULT_REACTIONS, logger
from database import (load_guild_data, save_guild_data, get_user_stats, update_user_stats, 
                     add_duplicate_record, get_duplicate_history, get_all_guild_hashes, 
                     clear_guild_data, save_scan_progress, get_scan_progress, check_if_processed,
                     db_pool, get_db_size, save_image_hash_batch)
from cache import hash_cache
from reaction_manager import reaction_mgr
from image_processor import download_image, calculate_hash, find_similar_images
from utils import format_time, create_progress_bar, get_time_ago

class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()

class CommandsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rate_limiter = asyncio.Lock()
    
    @app_commands.command(name="setup", description="Start the bot - blacklist all channels first (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        if gid not in guild_data:
            await load_guild_data(gid)
        
        guild_data[gid]['config']['whitelist'] = set()
        await save_guild_data(gid)
        
        embed = discord.Embed(
            title="🚀 Bot Setup Complete",
            description="Every channel has been blacklisted by default.\nUse `/whitelist #channel` to enable scanning.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="bothelp", description="Show all available commands")
    async def bothelp(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 Duplicate Image Detector",
            description="Complete command reference",
            color=discord.Color.blue()
        )
        
        setup = "`/setup` - Initialize bot (blacklist all)\n`/whitelist` - Add channel to scan\n`/blacklist` - Remove channel from scan"
        scanning = "`/scanhistory` - Scan channel history\n`/scanall` - Scan all whitelisted\n`/resumescan` - Resume interrupted scan"
        config_cmd = "`/autoreaction` - Configure reactions\n`/setsimilarity` - Set detection threshold\n`/setchannelthreshold` - Per-channel threshold\n`/setnotifications` - Alert settings\n`/toggleautodelete` - Auto-delete duplicates"
        stats = "`/stats` - Bot statistics\n`/userstats` - User statistics\n`/leaderboard` - Top posters\n`/export` - Export data"
        admin = "`/cleardata` - Clear all data\n`/dbinfo` - Database info\n`/optimize` - Optimize DB\n`/cachestats` - Cache info"
        
        embed.add_field(name="🚀 Setup", value=setup, inline=False)
        embed.add_field(name="🔍 Scanning", value=scanning, inline=False)
        embed.add_field(name="⚙️ Configuration", value=config_cmd, inline=False)
        embed.add_field(name="📊 Statistics", value=stats, inline=False)
        embed.add_field(name="🔧 Admin", value=admin, inline=False)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction):
        start = time.time()
        await interaction.response.defer()
        end = time.time()
        
        embed = discord.Embed(title="🏓 Pong!", color=discord.Color.green())
        embed.add_field(name="WebSocket", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="API", value=f"{round((end - start) * 1000)}ms", inline=True)
        embed.add_field(name="Cache", value=f"{hash_cache.size()} entries", inline=True)
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="whitelist", description="Manage channel whitelist (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def whitelist(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        cfg = guild_data[gid]['config']
        
        if channel is None:
            if cfg['whitelist']:
                channels = [f"<#{c}>" for c in cfg['whitelist']]
                await interaction.followup.send(f"📝 Whitelisted: {', '.join(channels)}")
            else:
                await interaction.followup.send("📝 No channels whitelisted")
            return
        
        if channel.id in cfg['whitelist']:
            cfg['whitelist'].remove(channel.id)
            msg = f"✅ Removed {channel.mention} from whitelist"
        else:
            cfg['whitelist'].add(channel.id)
            msg = f"✅ Added {channel.mention} to whitelist"
        
        await save_guild_data(gid)
        await interaction.followup.send(msg)
    
    @app_commands.command(name="blacklist", description="Manage channel blacklist (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def blacklist(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        cfg = guild_data[gid]['config']
        
        if channel is None:
            if cfg.get('blacklist', set()):
                channels = [f"<#{c}>" for c in cfg['blacklist']]
                await interaction.followup.send(f"🚫 Blacklisted: {', '.join(channels)}")
            else:
                await interaction.followup.send("🚫 No channels blacklisted")
            return
        
        if channel.id in cfg.get('blacklist', set()):
            cfg['blacklist'].remove(channel.id)
            msg = f"✅ Removed {channel.mention} from blacklist"
        else:
            cfg['blacklist'].add(channel.id)
            msg = f"✅ Added {channel.mention} to blacklist"
        
        await save_guild_data(gid)
        await interaction.followup.send(msg)
    
    @app_commands.command(name="whitelistuser", description="Whitelist user from detection (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def whitelistuser(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        cfg = guild_data[gid]['config']
        
        if user is None:
            if cfg.get('user_whitelist', set()):
                users = [f"<@{u}>" for u in cfg['user_whitelist']]
                await interaction.followup.send(f"👥 Whitelisted users: {', '.join(users)}")
            else:
                await interaction.followup.send("👥 No users whitelisted")
            return
        
        if user.id in cfg.get('user_whitelist', set()):
            cfg['user_whitelist'].remove(user.id)
            msg = f"✅ Removed {user.mention} from whitelist"
        else:
            cfg['user_whitelist'].add(user.id)
            msg = f"✅ Added {user.mention} to whitelist (will be ignored)"
        
        await save_guild_data(gid)
        await interaction.followup.send(msg)
    
    @app_commands.command(name="setsimilarity", description="Set global similarity threshold 0-10 (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setsimilarity(self, interaction: discord.Interaction, threshold: app_commands.Range[int, 0, 10]):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        old = guild_data[gid]['config'].get('hash_threshold', HASH_THRESHOLD)
        guild_data[gid]['config']['hash_threshold'] = threshold
        await save_guild_data(gid)
        
        await interaction.followup.send(f"✅ Global threshold: {old} → {threshold}")
    
    @app_commands.command(name="setchannelthreshold", description="Set per-channel threshold (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setchannelthreshold(self, interaction: discord.Interaction, channel: discord.TextChannel, 
                                   threshold: app_commands.Range[int, 0, 10]):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        guild_data[gid]['config']['channel_thresholds'][str(channel.id)] = threshold
        await save_guild_data(gid)
        
        await interaction.followup.send(f"✅ {channel.mention} threshold set to {threshold}")
    
    @app_commands.command(name="setnotifications", description="Configure notifications (Admin only)")
    @app_commands.describe(mode="Notification type", target="User to mention (if user mode)")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Role (mention admin role)", value="role"),
        app_commands.Choice(name="User (mention specific user)", value="user"),
        app_commands.Choice(name="Silent (no mentions)", value="silent")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def setnotifications(self, interaction: discord.Interaction, mode: str, 
                               target: Optional[discord.Member] = None):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        settings = {'type': mode}
        if mode == 'user' and target:
            settings['user_id'] = target.id
            msg = f"✅ Notifications will mention {target.mention}"
        else:
            msg = f"✅ Notifications set to: {mode}"
        
        guild_data[gid]['config']['notification_settings'][str(gid)] = settings
        await save_guild_data(gid)
        await interaction.followup.send(msg)
    
    @app_commands.command(name="toggleautodelete", description="Toggle auto-delete duplicates (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def toggleautodelete(self, interaction: discord.Interaction):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        current = guild_data[gid]['config'].get('auto_delete', AUTO_DELETE_DUPLICATES)
        guild_data[gid]['config']['auto_delete'] = not current
        await save_guild_data(gid)
        
        status = "ON ✅" if not current else "OFF ❌"
        await interaction.followup.send(f"🗑️ Auto-delete: {status}")
    
    @app_commands.command(name="autoreaction", description="Configure auto-reactions (Admin only)")
    @app_commands.describe(
        reaction_type="Type: processing, unique, duplicate, error",
        emoji="Emoji to use",
        enabled="Enable or disable",
        auto_remove="Auto-remove after timeout",
        timeout="Seconds before removal (0=never)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def autoreaction(self, interaction: discord.Interaction, reaction_type: str,
                          emoji: str = None, enabled: bool = None, 
                          auto_remove: bool = None, timeout: int = None):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        reactions = guild_data[gid]['config'].get('auto_reactions', {})
        current = reactions.get(reaction_type, DEFAULT_REACTIONS[ReactionType(reaction_type)])
        if isinstance(current, dict):
            current = ReactionConfig.from_dict(current)
        
        new_config = ReactionConfig(
            emoji=emoji or current.emoji,
            enabled=enabled if enabled is not None else current.enabled,
            auto_remove=auto_remove if auto_remove is not None else current.auto_remove,
            remove_timeout=max(0, timeout if timeout is not None else current.remove_timeout)
        )
        
        reactions[reaction_type] = new_config
        guild_data[gid]['config']['auto_reactions'] = reactions
        await save_guild_data(gid)
        
        status = "enabled" if new_config.enabled else "disabled"
        remove_status = f"(auto-remove: {new_config.remove_timeout}s)" if new_config.auto_remove else "(persistent)"
        
        await interaction.followup.send(f"✅ {reaction_type.title()} reaction {status} with {new_config.emoji} {remove_status}")
    
    @app_commands.command(name="reaction_settings", description="View current reaction settings (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def reaction_settings(self, interaction: discord.Interaction):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        configs = reaction_mgr.get_all_configs(gid)
        
        embed = discord.Embed(title="⚙️ Reaction Settings", color=discord.Color.blue())
        for r_type, cfg in configs.items():
            status = "✅" if cfg.enabled else "❌"
            auto = f"⏱️{cfg.remove_timeout}s" if cfg.auto_remove else "📍"
            embed.add_field(
                name=f"{status} {r_type.value.title()}",
                value=f"{cfg.emoji} {auto}",
                inline=True
            )
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="stats", description="Show bot statistics")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        try:
            hashes = await get_all_guild_hashes(gid)
            total_images = sum(len(entries) for entries in hashes.values())
            unique_hashes = len(hashes)
            
            cfg = guild_data.get(gid, {}).get('config', {})
            
            embed = discord.Embed(title="📊 Bot Statistics", color=discord.Color.blue())
            embed.add_field(name="Images Stored", value=f"{total_images:,}", inline=True)
            embed.add_field(name="Unique Hashes", value=f"{unique_hashes:,}", inline=True)
            embed.add_field(name="Cache Size", value=f"{hash_cache.size():,}", inline=True)
            embed.add_field(name="Threshold", value=str(cfg.get('hash_threshold', HASH_THRESHOLD)), inline=True)
            embed.add_field(name="Auto-Delete", value="ON" if cfg.get('auto_delete') else "OFF", inline=True)
            embed.add_field(name="Whitelisted Ch.", value=str(len(cfg.get('whitelist', set()))), inline=True)
            
            log_ch = discord.utils.get(interaction.guild.channels, name=LOG_CHANNEL_NAME)
            embed.add_field(name="Log Channel", value=log_ch.mention if log_ch else "Not found", inline=False)
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}")
    
    @app_commands.command(name="userstats", description="Show user statistics")
    @app_commands.describe(user="User to check (default: yourself)")
    async def userstats(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        if not user:
            user = interaction.user
        
        await interaction.response.defer()
        stats = await get_user_stats(interaction.guild_id, user.id)
        
        embed = discord.Embed(
            title=f"📊 Stats for {user.display_name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Unique Images", value=f"{stats['unique']:,}", inline=True)
        embed.add_field(name="Duplicates", value=f"{stats['duplicates']:,}", inline=True)
        
        total = stats['unique'] + stats['duplicates']
        if total > 0:
            rate = (stats['duplicates'] / total) * 100
            embed.add_field(name="Duplicate Rate", value=f"{rate:.1f}%", inline=True)
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="leaderboard", description="Show server leaderboard")
    @app_commands.describe(mode="Sort by")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Unique Images", value="unique"),
        app_commands.Choice(name="Duplicates", value="duplicates")
    ])
    async def leaderboard(self, interaction: discord.Interaction, mode: str = "unique"):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        try:
            users = []
            async with db_pool.acquire() as db:
                async with db.execute(f'''
                    SELECT user_id, unique_count, duplicate_count 
                    FROM user_stats WHERE guild_id = ? 
                    ORDER BY {mode}_count DESC LIMIT 10
                ''', (gid,)) as cur:
                    async for row in cur:
                        users.append((row[0], row[1], row[2]))
            
            embed = discord.Embed(
                title=f"🏆 Leaderboard - Most {mode.title()}",
                color=discord.Color.gold()
            )
            
            medals = ["🥇", "🥈", "🥉"]
            for idx, (user_id, unique, dup) in enumerate(users, 1):
                user = self.bot.get_user(user_id)
                if user:
                    medal = medals[idx-1] if idx <= 3 else f"#{idx}"
                    value = unique if mode == "unique" else dup
                    embed.add_field(
                        name=f"{medal} {user.display_name}",
                        value=f"{value:,} {mode}",
                        inline=False
                    )
            
            if not users:
                embed.description = "No data yet!"
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}")
    
    @app_commands.command(name="export", description="Export duplicate history (Admin only)")
    @app_commands.describe(format="Export format")
    @app_commands.choices(format=[
        app_commands.Choice(name="CSV", value="csv"),
        app_commands.Choice(name="JSON", value="json")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def export(self, interaction: discord.Interaction, format: str = "csv"):
        await interaction.response.defer()
        gid = interaction.guild_id
        
        history = await get_duplicate_history(gid, limit=10000)
        
        if not history:
            await interaction.followup.send("❌ No data to export!")
            return
        
        filename = f"duplicates_{gid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{format}"
        
        try:
            if format == 'csv':
                with open(filename, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=['timestamp', 'user_name', 'channel_id', 'message_id', 'similarity_count'])
                    writer.writeheader()
                    writer.writerows(history)
            else:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(history, f, indent=2, ensure_ascii=False)
            
            await interaction.followup.send(f"✅ Exported {len(history)} records", file=discord.File(filename))
            os.remove(filename)
        except Exception as e:
            await interaction.followup.send(f"❌ Export error: {e}")
    
    @app_commands.command(name="scanhistory", description="Scan channel history (Admin only)")
    @app_commands.describe(channel="Channel to scan (default: current)", limit="Messages to scan (max 100000)")
    @app_commands.checks.has_permissions(administrator=True)
    async def scanhistory(self, interaction: discord.Interaction, 
                         channel: Optional[discord.TextChannel] = None, 
                         limit: app_commands.Range[int, 1, 100000] = 100):
        if not channel:
            channel = interaction.channel
        
        await interaction.response.defer()
        
        status_msg = await interaction.followup.send("🔍 Initializing scan...")
        
        start_time = time.time()
        checked = 0
        found = 0
        dups = 0
        batch = []
        last_update = 0
        
        progress = await get_scan_progress(interaction.guild_id, channel.id)
        last_msg_id = progress[0] if progress else None
        
        try:
            kwargs = {'limit': limit, 'oldest_first': False}
            if last_msg_id:
                kwargs['after'] = discord.Object(id=last_msg_id)
            
            async for msg in channel.history(**kwargs):
                checked += 1
                
                if msg.attachments:
                    for att in msg.attachments:
                        if not any(att.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                            continue
                        
                        if await check_if_processed(interaction.guild_id, msg.id):
                            continue
                        
                        img_data = await download_image(att.url)
                        if img_data:
                            img_hash = calculate_hash(img_data['content'])
                            if img_hash:
                                found += 1
                                
                                threshold = guild_data[interaction.guild_id]['config'].get('hash_threshold', 5)
                                similar = await find_similar_images(interaction.guild_id, img_hash, channel.id, threshold)
                                
                                if similar:
                                    dups += 1
                                    await reaction_mgr.add_reaction(msg, ReactionType.DUPLICATE)
                                else:
                                    await reaction_mgr.add_reaction(msg, ReactionType.UNIQUE)
                                
                                batch.append((
                                    interaction.guild_id, str(img_hash), msg.id, 
                                    msg.channel.id, msg.author.id, msg.created_at.isoformat(), att.url
                                ))
                                
                                if len(batch) >= 50:
                                    await save_image_hash_batch(batch)
                                    batch.clear()
                
                if checked - last_update >= 50:
                    last_update = checked
                    elapsed = time.time() - start_time
                    rate = checked / elapsed if elapsed > 0 else 0
                    remaining = (limit - checked) / rate if rate > 0 else 0
                    pct = min(100, (checked / limit) * 100)
                    
                    await status_msg.edit(content=
                        f"🔍 Scanning {channel.mention}\n"
                        f"{create_progress_bar(pct)}\n"
                        f"Checked: {checked:,}/{limit:,} | Images: {found} | Dups: {dups}\n"
                        f"Speed: {rate:.1f} msg/s | ETA: {format_time(remaining)}"
                    )
                
                if checked % 500 == 0:
                    await save_scan_progress(interaction.guild_id, channel.id, msg.id, checked)
            
            if batch:
                await save_image_hash_batch(batch)
            
            await save_scan_progress(interaction.guild_id, channel.id, 0, checked)
            
            total_time = time.time() - start_time
            embed = discord.Embed(title="✅ Scan Complete", color=discord.Color.green())
            embed.add_field(name="Messages", value=f"{checked:,}")
            embed.add_field(name="Images", value=f"{found}")
            embed.add_field(name="Duplicates", value=f"{dups}")
            embed.add_field(name="Time", value=format_time(total_time))
            
            await status_msg.edit(content="", embed=embed)
            
        except Exception as e:
            logger.error(f"Scan error: {e}")
            await status_msg.edit(content=f"❌ Error: {e}")
    
    @app_commands.command(name="scanall", description="Scan all whitelisted channels (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def scanall(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 50000] = 100):
        await interaction.response.defer()
        
        status_msg = await interaction.followup.send("🔍 Starting server-wide scan...")
        
        gid = interaction.guild_id
        if gid not in guild_data:
            await load_guild_data(gid)
        
        whitelist = guild_data[gid]['config'].get('whitelist', set())
        if not whitelist:
            await interaction.followup.send("❌ No channels whitelisted!")
            return
        
        start = time.time()
        total_msgs = 0
        total_imgs = 0
        total_dups = 0
        
        for channel_id in whitelist:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            
            try:
                async for msg in channel.history(limit=limit):
                    total_msgs += 1
                    
                    if msg.attachments:
                        for att in msg.attachments:
                            if any(att.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                                if not await check_if_processed(gid, msg.id):
                                    img = await download_image(att.url)
                                    if img:
                                        h = calculate_hash(img['content'])
                                        if h:
                                            total_imgs += 1
                                            similar = await find_similar_images(gid, h, channel_id)
                                            if similar:
                                                total_dups += 1
                                                await reaction_mgr.add_reaction(msg, ReactionType.DUPLICATE)
                                            else:
                                                await reaction_mgr.add_reaction(msg, ReactionType.UNIQUE)
                                            
                                            await save_image_hash_batch([(
                                                gid, str(h), msg.id, channel_id, 
                                                msg.author.id, msg.created_at.isoformat(), att.url
                                            )])
                    
                    if total_msgs % 100 == 0:
                        await status_msg.edit(content=f"🔍 Scanning... Checked: {total_msgs}, Images: {total_imgs}, Dups: {total_dups}")
                        
            except Exception as e:
                logger.error(f"Error scanning {channel_id}: {e}")
                continue
        
        embed = discord.Embed(title="✅ Server Scan Complete", color=discord.Color.green())
        embed.add_field(name="Total Checked", value=f"{total_msgs:,}")
        embed.add_field(name="Images Found", value=f"{total_imgs:,}")
        embed.add_field(name="Duplicates", value=f"{total_dups:,}")
        embed.add_field(name="Time", value=format_time(time.time() - start))
        
        await status_msg.edit(content="", embed=embed)
    
    @app_commands.command(name="resumescan", description="Resume interrupted scan (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def resumescan(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not channel:
            channel = interaction.channel
        
        await interaction.response.defer()
        
        progress = await get_scan_progress(interaction.guild_id, channel.id)
        
        if progress and progress[0]:
            embed = discord.Embed(
                title="📋 Scan Progress Found",
                description=f"Channel: {channel.mention}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Last Message ID", value=str(progress[0]))
            embed.add_field(name="Processed", value=f"{progress[1]:,} messages")
            embed.add_field(name="Action", value=f"Use `/scanhistory channel:{channel.mention}` to resume", inline=False)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"✅ No incomplete scans found for {channel.mention}")
    
    @app_commands.command(name="cleardata", description="Clear ALL server data (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def cleardata(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        view = ConfirmView()
        await interaction.followup.send(
            "⚠️ **WARNING**: This will delete ALL image hashes and statistics!\n"
            "This cannot be undone!",
            view=view
        )
        
        await view.wait()
        
        if view.value:
            try:
                hashes = await get_all_guild_hashes(interaction.guild_id)
                count = sum(len(entries) for entries in hashes.values())
                await clear_guild_data(interaction.guild_id)
                await interaction.edit_original_response(
                    content=f"✅ Cleared {count:,} records!",
                    view=None
                )
            except Exception as e:
                await interaction.edit_original_response(content=f"❌ Error: {e}", view=None)
        else:
            await interaction.edit_original_response(content="❌ Cancelled", view=None)
    
    @app_commands.command(name="dbinfo", description="Show database info (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def dbinfo(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            size = await get_db_size()
            
            embed = discord.Embed(title="🗄️ Database Info", color=discord.Color.blue())
            embed.add_field(name="File Size", value=f"{size:.2f} MB")
            embed.add_field(name="Location", value="bot_data/bot_data.db")
            embed.add_field(name="Mode", value="WAL (Write-Ahead Logging)")
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}")
    
    @app_commands.command(name="optimize", description="Optimize database (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def optimize(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            async with db_pool.acquire() as db:
                await db.execute('VACUUM')
                await db.execute('ANALYZE')
                await db.commit()
            
            await interaction.followup.send("✅ Database optimized!")
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}")
    
    @app_commands.command(name="cachestats", description="Show cache statistics (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def cachestats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        embed = discord.Embed(title="💾 Cache Statistics", color=discord.Color.blue())
        embed.add_field(name="Current Size", value=f"{hash_cache.size():,}")
        embed.add_field(name="Max Size", value=f"{MAX_CACHE_SIZE:,}")
        embed.add_field(name="Usage", value=f"{(hash_cache.size()/MAX_CACHE_SIZE*100):.1f}%")
        
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(CommandsCog(bot))
