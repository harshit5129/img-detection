import discord
from discord import app_commands
from discord.ext import commands
import os
import tempfile
import numpy as np

from config import DB_FILE, guild_data, guild_indices, logger
from database import db_pool, delete_guild_data, get_db_stats, load_guild_config, save_guild_config, add_image, get_image_by_message, add_tag
from embeddings import embed_image
from helpers import download_image, generate_auto_tags, rate_limiter

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def check_mod(self, interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        mod_role_ids = guild_data.get(interaction.guild_id, {}).get('mod_roles', set())
        for role in interaction.user.roles:
            if role.id in mod_role_ids:
                return True
        return False

    @app_commands.command(name="setmod", description="Set moderator role")
    async def setmod(self, interaction: discord.Interaction, role: discord.Role):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        guild_id = interaction.guild_id
        await load_guild_config(guild_id)
        if 'mod_roles' not in guild_data[guild_id]:
            guild_data[guild_id]['mod_roles'] = set()
        guild_data[guild_id]['mod_roles'].add(role.id)
        await save_guild_config(guild_id)
        await interaction.response.send_message(f"✅ {role.mention} is now a mod role.", ephemeral=True)

    @app_commands.command(name="whitelist", description="Whitelist a channel for auto-indexing")
    async def whitelist(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        if not await self.check_mod(interaction):
            await interaction.followup.send("❌ Admin/Mod only.", ephemeral=True)
            return
        guild_id = interaction.guild_id
        await load_guild_config(guild_id)
        guild_data[guild_id]['whitelist'].add(channel.id)
        await save_guild_config(guild_id)
        await interaction.response.send_message(f"✅ {channel.mention} whitelisted.", ephemeral=True)

    @app_commands.command(name="blacklist", description="Blacklist a channel")
    async def blacklist(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        if not await self.check_mod(interaction):
            await interaction.followup.send("❌ Admin/Mod only.", ephemeral=True)
            return
        guild_id = interaction.guild_id
        await load_guild_config(guild_id)
        guild_data[guild_id]['blacklist'].add(channel.id)
        if channel.id in guild_data[guild_id]['whitelist']:
            guild_data[guild_id]['whitelist'].remove(channel.id)
        await save_guild_config(guild_id)
        await interaction.response.send_message(f"✅ {channel.mention} blacklisted.", ephemeral=True)

    @app_commands.command(name="stats", description="Show bot statistics")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild_id
        stats = await get_db_stats(guild_id)
        idx_count = len(guild_indices.get(guild_id, {}).get('ids', []))

        embed = discord.Embed(title="📊 Art Bot Stats", color=discord.Color.blue())
        embed.add_field(name="Images", value=f"{stats['images']:,}", inline=True)
        embed.add_field(name="Unique Tags", value=f"{stats['tags']:,}", inline=True)
        embed.add_field(name="Contributors", value=f"{stats['users']:,}", inline=True)
        embed.add_field(name="Index Size", value=f"{idx_count:,}", inline=True)

        db_size = os.path.getsize(DB_FILE) / (1024 * 1024)
        embed.add_field(name="DB Size", value=f"{db_size:.2f} MB", inline=True)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="cleardata", description="Wipe ALL data for this server")
    async def cleardata(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        if not await self.check_mod(interaction):
            await interaction.followup.send("❌ Admin/Mod only.", ephemeral=True)
            return
        await delete_guild_data(interaction.guild_id)
        if interaction.guild_id in guild_indices:
            guild_indices[interaction.guild_id] = {'ids': [], 'embeddings': np.array([]).reshape(0, 512), 'meta': []}
        await interaction.followup.send("✅ All server data wiped.", ephemeral=True)

    @app_commands.command(name="setup", description="Initialize bot for this server and auto-track all channels")
    async def setup(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild_id
        await load_guild_config(guild_id)
        
        # Auto-track all text channels
        all_channels = {ch.id for ch in interaction.guild.text_channels}
        guild_data[guild_id] = {
            'whitelist': all_channels,
            'blacklist': set(),
            'user_whitelist': set(),
            'mod_roles': set(),
            'hash_threshold': 5,
            'auto_delete': False,
            'log_channel_id': None
        }
        await save_guild_config(guild_id)
        
        embed = discord.Embed(
            title="✅ Art Bot Initialized",
            description=f"Auto-tracking {len(all_channels)} channels. Use `/untrack #channel` to stop tracking specific channels.",
            color=discord.Color.green()
        )
        embed.add_field(name="Tracking", value=f"{len(all_channels)} channels", inline=True)
        embed.add_field(name="Next steps", value="Use `/track` or `/untrack` to manage channels", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="track", description="Start tracking a channel for images")
    async def track(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        if not await self.check_mod(interaction):
            await interaction.followup.send("❌ Admin/Mod only.", ephemeral=True)
            return
        
        guild_id = interaction.guild_id
        await load_guild_config(guild_id)
        
        if channel.id in guild_data[guild_id]['whitelist']:
            await interaction.followup.send(f"✅ {channel.mention} is already being tracked.", ephemeral=True)
            return
        
        guild_data[guild_id]['whitelist'].add(channel.id)
        if channel.id in guild_data[guild_id]['blacklist']:
            guild_data[guild_id]['blacklist'].remove(channel.id)
        await save_guild_config(guild_id)
        
        await interaction.followup.send(f"✅ Now tracking {channel.mention}", ephemeral=True)

    @app_commands.command(name="untrack", description="Stop tracking a channel")
    async def untrack(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        if not await self.check_mod(interaction):
            await interaction.followup.send("❌ Admin/Mod only.", ephemeral=True)
            return
        
        guild_id = interaction.guild_id
        await load_guild_config(guild_id)
        
        guild_data[guild_id]['whitelist'].discard(channel.id)
        guild_data[guild_id]['blacklist'].add(channel.id)
        await save_guild_config(guild_id)
        
        await interaction.followup.send(f"✅ Stopped tracking {channel.mention}", ephemeral=True)

    @app_commands.command(name="tracked", description="Show tracked and untracked channels")
    async def tracked(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild_id
        await load_guild_config(guild_id)
        
        whitelist = guild_data.get(guild_id, {}).get('whitelist', set())
        blacklist = guild_data.get(guild_id, {}).get('blacklist', set())
        
        tracked = []
        untracked = []
        
        for channel in interaction.guild.text_channels:
            if channel.id in whitelist:
                tracked.append(channel)
            elif channel.id in blacklist:
                untracked.append(channel)
            else:
                untracked.append(channel)
        
        embed = discord.Embed(
            title="📊 Channel Tracking Status",
            color=discord.Color.blue()
        )
        
        if tracked:
            tracked_list = "\n".join(f"• {ch.mention}" for ch in tracked[:20])
            if len(tracked) > 20:
                tracked_list += f"\n... and {len(tracked) - 20} more"
            embed.add_field(name=f"✅ Tracked ({len(tracked)})", value=tracked_list or "None", inline=False)
        
        if untracked:
            untracked_list = "\n".join(f"• {ch.mention}" for ch in untracked[:20])
            if len(untracked) > 20:
                untracked_list += f"\n... and {len(untracked) - 20} more"
            embed.add_field(name=f"❌ Untracked ({len(untracked)})", value=untracked_list or "None", inline=False)
        
        embed.set_footer(text=f"Total: {len(interaction.guild.text_channels)} channels")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="optimize", description="Optimize database")
    async def optimize(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        if not await self.check_mod(interaction):
            await interaction.followup.send("❌ Admin/Mod only.", ephemeral=True)
            return
        try:
            async with db_pool.acquire() as db:
                await db.execute('VACUUM')
                await db.execute('ANALYZE')
                await db.commit()
            await interaction.followup.send("✅ Database optimized.", ephemeral=True)
        except Exception as e:
            logger.error(f"Optimize error: {e}")
            await interaction.followup.send("❌ Optimization failed.", ephemeral=True)

    @app_commands.command(name="bothelp", description="Show help and commands list")
    async def help_cmd(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 Art Bot Help",
            description="Semantic image search using CLIP embeddings - automatically indexes images from tracked channels",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="🔍 Search",
            value="• `/search [query]` - Search by text description\n• `/searchbyimage` - Upload image to find similar\n• `/gallery [user]` - Browse all or user's gallery\n• `/random` - Show random image\n• `/show [id]` - Show image by ID",
            inline=False
        )
        embed.add_field(
            name="🏷️ Tags",
            value="• `/tag [msg_id] [tag]` - Add tag to image\n• `/untag [msg_id] [tag]` - Remove tag\n• `/tags [msg_id]` - List image tags\n• `/tagsearch [tag]` - Search by tag\n• `/mygallery` - Browse your uploads",
            inline=False
        )
        embed.add_field(
            name="⚙️ Admin",
            value="• `/setup` - Initialize & auto-track all channels\n• `/track #channel` - Start tracking channel\n• `/untrack #channel` - Stop tracking channel\n• `/tracked` - Show tracked/untracked channels\n• `/scan` - Scan all tracked channels\n• `/scanchannel #channel` - Scan specific channel\n• `/setmod @role` - Set moderator role\n• `/stats` - Show server statistics\n• `/cleardata` - Wipe all server data\n• `/optimize` - Optimize database",
            inline=False
        )
        embed.add_field(
            name="💡 Tips",
            value="• Right-click any image → Apps → 🔍 Find Similar\n• Use `/setup` to start tracking all channels\n• Images are auto-indexed when posted in tracked channels",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="scan", description="Scan all channels and collect embeddings")
    async def scan(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logger.error(f"Defer failed: {e}")
            return
        
        if not await self.check_mod(interaction):
            await interaction.followup.send("❌ Admin/Mod only.", ephemeral=True)
            return
        guild = interaction.guild
        guild_id = guild.id
        
        await load_guild_config(guild_id)
        
        total_scanned = 0
        total_added = 0
        failed = 0
        
        await interaction.followup.send("⏳ Starting full scan...", ephemeral=True)
        
        logger.info(f"Scan started for guild {guild_id}, channels: {len(guild.text_channels)}")
        
        for channel in guild.text_channels:
            if channel.id in guild_data.get(guild_id, {}).get('blacklist', set()):
                continue
            
            async for message in channel.history(limit=None):
                if message.author.bot:
                    continue
                if not message.attachments:
                    continue
                
                for att in message.attachments:
                    if not any(att.filename.lower().endswith(e) for e in ['.png','.jpg','.jpeg','.webp','.bmp']):
                        continue
                    
                    existing = await get_image_by_message(guild_id, message.id)
                    if existing:
                        continue
                    
                    total_scanned += 1
                    
                    try:
                        await rate_limiter.acquire(0)
                        dl = await download_image(att.url)
                        if not dl:
                            failed += 1
                            continue
                        
                        suffix = os.path.splitext(att.filename)[1] or '.png'
                        tmp_path = None
                        emb = None
                        try:
                            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                                tmp.write(dl['content'])
                                tmp_path = tmp.name
                            emb = await embed_image(tmp_path)
                        finally:
                            if tmp_path and os.path.exists(tmp_path):
                                os.unlink(tmp_path)
                        
                        if emb is None:
                            failed += 1
                            continue
                        
                        img_id = await add_image(
                            guild_id, message.channel.id, message.id, message.author.id,
                            str(message.author), att.url, dl['width'], dl['height'],
                            dl['format'], dl['size_mb'], emb
                        )
                        
                        if img_id > 0:
                            auto_tags = generate_auto_tags(dl['width'], dl['height'], dl['format'], dl['size_mb'])
                            for t in auto_tags:
                                await add_tag(img_id, t)
                            total_added += 1
                    except Exception as e:
                        logger.error(f"Scan error: {e}")
                        failed += 1
        
        from cogs.events import Events
        events_cog = self.bot.get_cog('Events')
        if events_cog:
            await events_cog._build_index(guild_id)
        
        embed = discord.Embed(
            title="✅ Scan Complete",
            color=discord.Color.green()
        )
        embed.add_field(name="Scanned", value=total_scanned, inline=True)
        embed.add_field(name="Added", value=total_added, inline=True)
        embed.add_field(name="Failed", value=failed, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="scanchannel", description="Scan a specific channel")
    async def scanchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        if not await self.check_mod(interaction):
            await interaction.followup.send("❌ Admin/Mod only.", ephemeral=True)
            return
        guild_id = interaction.guild_id
        
        await load_guild_config(guild_id)
        
        total_scanned = 0
        total_added = 0
        failed = 0
        
        await interaction.followup.send(f"⏳ Scanning {channel.mention}...", ephemeral=True)
        
        async for message in channel.history(limit=None):
            if message.author.bot:
                continue
            if not message.attachments:
                continue
            
            for att in message.attachments:
                if not any(att.filename.lower().endswith(e) for e in ['.png','.jpg','.jpeg','.webp','.bmp']):
                    continue
                
                existing = await get_image_by_message(guild_id, message.id)
                if existing:
                    continue
                
                total_scanned += 1
                
                try:
                    await rate_limiter.acquire(0)
                    dl = await download_image(att.url)
                    if not dl:
                        failed += 1
                        continue
                    
                    suffix = os.path.splitext(att.filename)[1] or '.png'
                    tmp_path = None
                    emb = None
                    try:
                        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                            tmp.write(dl['content'])
                            tmp_path = tmp.name
                        emb = await embed_image(tmp_path)
                    finally:
                        if tmp_path and os.path.exists(tmp_path):
                            os.unlink(tmp_path)
                    
                    if emb is None:
                        failed += 1
                        continue
                    
                    img_id = await add_image(
                        guild_id, message.channel.id, message.id, message.author.id,
                        str(message.author), att.url, dl['width'], dl['height'],
                        dl['format'], dl['size_mb'], emb
                    )
                    
                    if img_id > 0:
                        auto_tags = generate_auto_tags(dl['width'], dl['height'], dl['format'], dl['size_mb'])
                        for t in auto_tags:
                            await add_tag(img_id, t)
                        total_added += 1
                except Exception as e:
                    logger.error(f"Scan error: {e}")
                    failed += 1
        
        from cogs.events import Events
        events_cog = self.bot.get_cog('Events')
        if events_cog:
            await events_cog._build_index(guild_id)
        
        embed = discord.Embed(
            title=f"✅ Scan Complete - {channel.name}",
            color=discord.Color.green()
        )
        embed.add_field(name="Scanned", value=total_scanned, inline=True)
        embed.add_field(name="Added", value=total_added, inline=True)
        embed.add_field(name="Failed", value=failed, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Admin(bot))
