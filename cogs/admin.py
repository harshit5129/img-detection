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

    @app_commands.command(name="setup", description="Initialize bot for this server")
    async def setup(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        guild_id = interaction.guild_id

        whitelists = guild_data.get(guild_id, {}).get('whitelist', set())
        if whitelists:
            await interaction.response.send_message("✅ Already set up. Use `/whitelist` to add channels.", ephemeral=True)
            return

        guild_data[guild_id] = {
            'whitelist': set(),
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
            description="Now run `/whitelist #channel` to enable auto-indexing.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
            description="Semantic image search using CLIP embeddings",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="🔍 Search",
            value="• `/search [query]` - Search by text\n• `/searchbyimage` - Find similar images\n• `/gallery [user]` - Browse someone's gallery\n• `/random` - Random image\n• `/show [id]` - Show image by ID",
            inline=False
        )
        embed.add_field(
            name="🏷️ Tags",
            value="• `/tag [msg_id] [tag]` - Add tag\n• `/untag [msg_id] [tag]` - Remove tag\n• `/tags [msg_id]` - List tags\n• `/tagsearch [tag]` - Search by tag\n• `/mygallery` - My uploaded images",
            inline=False
        )
        embed.add_field(
            name="⚙️ Admin",
            value="• `/setup` - Initialize server\n• `/whitelist #channel` - Enable indexing\n• `/blacklist #channel` - Block channel\n• `/scan` - Scan all channels\n• `/scanchannel #channel` - Scan one channel\n• `/setmod @role` - Set mod role\n• `/stats` - Show stats\n• `/cleardata` - Wipe data\n• `/optimize` - Optimize DB",
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
