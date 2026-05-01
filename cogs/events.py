import discord
from discord.ext import commands, tasks
import asyncio
import tempfile
import os
import time
import numpy as np

from config import logger, guild_data, guild_indices, HASH_THRESHOLD
from database import (
    db_pool, init_db_sync, load_guild_config, save_guild_config,
    add_image, get_image_by_message, add_tag, get_tags
)
from embeddings import embed_image
from helpers import download_image, calc_phash, generate_auto_tags, rate_limiter

class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.auto_save.start()

    def cog_unload(self):
        self.auto_save.cancel()

    @tasks.loop(minutes=5)
    async def auto_save(self):
        for gid in list(guild_data.keys()):
            await save_guild_config(gid)

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"{self.bot.user} connected to {len(self.bot.guilds)} guilds")
        await asyncio.get_event_loop().run_in_executor(None, init_db_sync)
        await db_pool.initialize()

        for guild in self.bot.guilds:
            await load_guild_config(guild.id)
            await self._build_index(guild.id)
            logger.info(f"  - {guild.name} | index: {len(guild_indices.get(guild.id, {}).get('ids', []))} images")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await load_guild_config(guild.id)

    async def _build_index(self, guild_id: int):
        from database import get_all_guild_embeddings
        rows = await get_all_guild_embeddings(guild_id)
        if not rows:
            guild_indices[guild_id] = {'ids': [], 'embeddings': np.array([]).reshape(0, 512), 'meta': []}
            return

        ids = [r['id'] for r in rows]
        embs = np.stack([r['embedding'] for r in rows])
        meta = [{k: v for k, v in r.items() if k != 'embedding'} for r in rows]

        guild_indices[guild_id] = {'ids': ids, 'embeddings': embs, 'meta': meta}

    async def _add_to_index(self, guild_id: int, image_id: int, embedding: np.ndarray, meta: dict):
        idx = guild_indices.get(guild_id)
        if idx is None:
            await self._build_index(guild_id)
            return
        idx['ids'].append(image_id)
        idx['embeddings'] = np.vstack([idx['embeddings'], embedding.reshape(1, -1)])
        idx['meta'].append(meta)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        guild_id = message.guild.id if message.guild else None
        if not guild_id:
            return

        if guild_id not in guild_data:
            await load_guild_config(guild_id)

        cfg = guild_data.get(guild_id, {})
        whitelist = cfg.get('whitelist', set())
        if whitelist and message.channel.id not in whitelist:
            return
        if message.channel.id in cfg.get('blacklist', set()):
            return
        if message.author.id in cfg.get('user_whitelist', set()):
            return

        if not message.attachments:
            return

        for att in message.attachments:
            if not any(att.filename.lower().endswith(e) for e in ['.png','.jpg','.jpeg','.webp','.bmp']):
                continue

            existing = await get_image_by_message(guild_id, message.id)
            if existing:
                continue

            try:
                await message.add_reaction("🔄")
            except:
                pass

            await rate_limiter.acquire(message.author.id)
            dl = await download_image(att.url)
            if not dl:
                try:
                    await message.remove_reaction("🔄", self.bot.user)
                    await message.add_reaction("⚠️")
                except:
                    pass
                continue

            phash = calc_phash(dl['content'])
            if phash:
                # Optional: check exact duplicate by phash here if desired
                pass

            # Save temp file for embedding
            tmp_path = None
            emb = None
            try:
                suffix = os.path.splitext(att.filename)[1] or '.png'
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(dl['content'])
                    tmp_path = tmp.name
                emb = await embed_image(tmp_path)
            except Exception as e:
                logger.error(f"Embedding error: {e}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

            if emb is None:
                try:
                    await message.remove_reaction("🔄", self.bot.user)
                    await message.add_reaction("❌")
                except:
                    pass
                continue

            img_id = await add_image(
                guild_id, message.channel.id, message.id, message.author.id,
                str(message.author), att.url, dl['width'], dl['height'],
                dl['format'], dl['size_mb'], phash, emb
            )

            if img_id > 0:
                auto_tags = generate_auto_tags(dl['width'], dl['height'], dl['format'], dl['size_mb'])
                for t in auto_tags:
                    await add_tag(img_id, t)

                await self._add_to_index(guild_id, img_id, emb, {
                    'id': img_id, 'message_id': message.id, 'channel_id': message.channel.id,
                    'user_id': message.author.id, 'url': att.url, 'width': dl['width'],
                    'height': dl['height'], 'format': dl['format'], 'size_mb': dl['size_mb'],
                    'username': str(message.author)
                })

                try:
                    await message.remove_reaction("🔄", self.bot.user)
                    await message.add_reaction("🔖")
                except:
                    pass
            else:
                try:
                    await message.remove_reaction("🔄", self.bot.user)
                    await message.add_reaction("⚠️")
                except:
                    pass

async def setup(bot):
    await bot.add_cog(Events(bot))
