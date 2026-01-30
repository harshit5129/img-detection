import discord
from discord.ext import commands
import logging
import time
import asyncio
from datetime import datetime

from config import guild_data, HASH_THRESHOLD, AUTO_DELETE_DUPLICATES, LOG_CHANNEL_NAME, ReactionType, logger
from database import load_guild_data, update_user_stats, add_duplicate_record, save_image_hash_batch, check_if_processed

logger = logging.getLogger('DuplicateDetector')

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        from reaction_manager import reaction_mgr
        self.reaction_mgr = reaction_mgr
    
    def should_check_channel(self, guild_id: int, channel_id: int) -> bool:
        """Check if channel should be monitored"""
        config = guild_data.get(guild_id, {}).get('config', {})
        whitelist = config.get('whitelist', set())
        blacklist = config.get('blacklist', set())
        
        # Blacklist takes precedence
        if channel_id in blacklist:
            return False
        
        # If no whitelist, nothing is scanned (strict mode)
        if not whitelist:
            return False
            
        return channel_id in whitelist
    
    def should_check_user(self, guild_id: int, user_id: int) -> bool:
        """Check if user should be monitored"""
        config = guild_data.get(guild_id, {}).get('config', {})
        user_whitelist = config.get('user_whitelist', set())
        return user_id not in user_whitelist
    
    @commands.Cog.listener()
    async def on_ready(self):
        from database import init_db_sync, db_pool, load_guild_data
        
        logger.info(f'{self.bot.user} connected to {len(self.bot.guilds)} servers!')
        
        # Initialize DB
        init_db_sync()
        await db_pool.initialize()
        
        # Load guild data
        for guild in self.bot.guilds:
            await load_guild_data(guild.id)
            logger.info(f'Loaded config for {guild.name}')
        
        # Sync commands
        try:
            synced = await self.bot.tree.sync()
            logger.info(f"Synced {len(synced)} commands")
        except Exception as e:
            logger.error(f"Sync error: {e}")
        
        await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="for duplicates"))
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        from database import load_guild_data
        await load_guild_data(guild.id)
        logger.info(f'Joined new guild: {guild.name}')
    
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        
        from image_processor import download_image, calculate_hash, find_similar_images
        
        gid = message.guild.id
        
        if gid not in guild_data:
            await load_guild_data(gid)
        
        # Check filters
        if not self.should_check_channel(gid, message.channel.id):
            return
        if not self.should_check_user(gid, message.author.id):
            return
        
        if not message.attachments:
            return
        
        for attachment in message.attachments:
            # Check if image
            if not any(attachment.filename.lower().endswith(ext) 
                      for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']):
                continue
            
            # Check if already processed
            if await check_if_processed(gid, message.id):
                await self.reaction_mgr.add_reaction(message, ReactionType.UNIQUE)
                continue
            
            # Processing sequence
            await self.reaction_mgr.handle_sequence(message, "processing")
            
            start_time = time.time()
            
            # Download
            download_result = await download_image(attachment.url)
            if not download_result:
                await self.reaction_mgr.handle_sequence(message, "error")
                continue
            
            # Hash
            img_hash = calculate_hash(download_result['content'])
            if not img_hash:
                await self.reaction_mgr.handle_sequence(message, "error")
                continue
            
            # Find similar
            config = guild_data[gid]['config']
            channel_threshold = config.get('channel_thresholds', {}).get(str(message.channel.id))
            threshold = channel_threshold if channel_threshold else config.get('hash_threshold', HASH_THRESHOLD)
            
            similar = await find_similar_images(gid, img_hash, message.channel.id, threshold)
            
            if similar:
                # Duplicate found
                await update_user_stats(gid, message.author.id, is_duplicate=True)
                await add_duplicate_record(gid, message.author.id, str(message.author), 
                                          message.channel.id, message.id, len(similar))
                
                await self.reaction_mgr.handle_sequence(message, "duplicate")
                
                # Send alert
                await self.send_duplicate_alert(message, attachment, similar, download_result, time.time() - start_time)
                
                # Auto delete if enabled
                if config.get('auto_delete', AUTO_DELETE_DUPLICATES):
                    try:
                        await message.delete()
                        temp = await message.channel.send(f"🗑️ Duplicate deleted from {message.author.mention}")
                        await asyncio.sleep(2)
                        await temp.delete()
                    except:
                        pass
            else:
                # Unique
                await update_user_stats(gid, message.author.id, is_duplicate=False)
                await self.reaction_mgr.handle_sequence(message, "unique")
            
            # Save hash
            await save_image_hash_batch([(
                gid, str(img_hash), message.id, message.channel.id,
                message.author.id, message.created_at.isoformat(), attachment.url
            )])
    
    async def send_duplicate_alert(self, message, attachment, similar, metadata, process_time):
        """Send detailed duplicate alert"""
        try:
            from utils import get_time_ago
            
            log_ch = discord.utils.get(message.guild.channels, name=LOG_CHANNEL_NAME)
            if not log_ch:
                log_ch = message.channel
            
            embed = discord.Embed(
                title="🚨 Duplicate Image Detected",
                description=f"Found in {message.channel.mention}",
                color=discord.Color.red(),
                timestamp=message.created_at
            )
            
            # User info
            from database import get_user_stats
            stats = await get_user_stats(message.guild.id, message.author.id)
            total = stats['unique'] + stats['duplicates']
            rate = (stats['duplicates'] / total * 100) if total > 0 else 0
            
            embed.add_field(name="👤 User", value=f"{message.author.mention}\n(ID: {message.author.id})", inline=False)
            
            # Detection info
            best_diff = min(diff for _, _, diff in similar)
            similarity = max(0, 100 - (best_diff * 10))
            
            embed.add_field(
                name="🔍 Detection",
                value=f"Similarity: {similarity:.1f}%\nMatches: {len(similar)}\nTime: {process_time:.2f}s",
                inline=False
            )
            
            # Image info
            embed.add_field(
                name="🖼️ Image",
                value=f"Size: {metadata.get('width', 0)}x{metadata.get('height', 0)}\n"
                      f"Format: {metadata.get('format', 'Unknown')}\n"
                      f"Size: {metadata.get('size_mb', 0):.2f} MB",
                inline=True
            )
            
            # User stats
            embed.add_field(
                name="📊 User Stats",
                value=f"Total: {total}\nUnique: {stats['unique']}\nDups: {stats['duplicates']} ({rate:.1f}%)",
                inline=True
            )
            
            # Original post info
            if similar:
                stored_hash, entry, diff = similar[0]
                msg_id, ch_id, user_id, timestamp, url = entry
                channel = self.bot.get_channel(ch_id)
                user = self.bot.get_user(user_id)
                
                try:
                    dt = datetime.fromisoformat(timestamp)
                    time_ago = get_time_ago(dt)
                    
                    embed.add_field(
                        name="📍 Original",
                        value=f"By: {user.mention if user else 'Unknown'}\n"
                              f"Channel: {channel.mention if channel else 'Unknown'}\n"
                              f"When: {time_ago}\n"
                              f"[Jump](https://discord.com/channels/{message.guild.id}/{ch_id}/{msg_id})",
                        inline=False
                    )
                except:
                    pass
            
            # Current post
            embed.add_field(name="📍 This Post", value=f"[Jump]({message.jump_url})", inline=False)
            embed.set_thumbnail(url=attachment.url)
            
            # Notification settings
            from config import ADMIN_ROLE_NAME
            config_data = guild_data[message.guild.id]['config']
            notif_settings = config_data.get('notification_settings', {}).get(str(message.guild.id), {})
            mention_type = notif_settings.get('type', 'role')
            
            content = None
            if mention_type == 'role':
                admin_role = discord.utils.get(message.guild.roles, name=ADMIN_ROLE_NAME)
                if admin_role:
                    content = admin_role.mention
            elif mention_type == 'user':
                uid = notif_settings.get('user_id')
                if uid:
                    user = self.bot.get_user(uid)
                    if user:
                        content = user.mention
            
            await log_ch.send(content=content, embed=embed)
            
        except Exception as e:
            logger.error(f"Alert error: {e}")

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
