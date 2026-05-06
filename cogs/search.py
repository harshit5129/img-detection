import discord
from discord import app_commands
from discord.ext import commands
import numpy as np

from config import logger, guild_indices, TOP_K_SEARCH
from database import get_tags, get_image_by_message, get_images_by_guild, get_images_by_user, get_image_by_id
from embeddings import embed_text, embed_image, cosine_similarity_batch
from views import ImagePaginator
from helpers import download_image

async def find_similar_context_callback(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer(ephemeral=False)

    guild_id = interaction.guild_id
    if not message.attachments:
        await interaction.followup.send("❌ No image in that message.", ephemeral=True)
        return

    att = message.attachments[0]
    img_record = await get_image_by_message(guild_id, message.id)

    if img_record and img_record.get('embedding'):
        q_emb = np.frombuffer(img_record['embedding'], dtype=np.float32)
    else:
        dl = await download_image(att.url)
        if not dl:
            await interaction.followup.send("❌ Failed to download image.", ephemeral=True)
            return
        import tempfile, os
        tmp_path = None
        try:
            suffix = os.path.splitext(att.filename)[1] or '.png'
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(dl['content'])
                tmp_path = tmp.name
            q_emb = await embed_image(tmp_path)
        except Exception as e:
            logger.error(f"Context embed error: {e}")
            await interaction.followup.send("❌ Failed to process image.", ephemeral=True)
            return
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        if q_emb is None:
            await interaction.followup.send("❌ Failed to embed image.", ephemeral=True)
            return

    idx = guild_indices.get(guild_id)
    if not idx or len(idx['ids']) == 0:
        await interaction.followup.send("❌ No images indexed yet.", ephemeral=True)
        return

    scores = cosine_similarity_batch(q_emb, idx['embeddings'])
    top_k = min(TOP_K_SEARCH, len(scores))
    top_idx = np.argsort(scores)[::-1][:top_k]

    results = []
    for i in top_idx:
        meta = idx['meta'][i].copy()
        if message.id and meta.get('message_id') == message.id:
            continue
        meta['score'] = float(scores[i])
        results.append(meta)

    if not results:
        await interaction.followup.send("❌ No similar images found.", ephemeral=True)
        return

    for r in results:
        r['tags'] = await get_tags(r['id'])

    paginator = ImagePaginator(results, query_info=f"Similar to {message.author.display_name}'s image", author_id=interaction.user.id, guild_id=guild_id)
    await paginator.send(interaction)


class Search(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _search_index(self, guild_id: int, query_emb: np.ndarray, exclude_msg_id: int = None):
        idx = guild_indices.get(guild_id)
        if not idx or len(idx['ids']) == 0:
            return []

        scores = cosine_similarity_batch(query_emb, idx['embeddings'])
        top_k = min(TOP_K_SEARCH, len(scores))
        top_idx = np.argsort(scores)[::-1][:top_k]

        results = []
        for i in top_idx:
            meta = idx['meta'][i].copy()
            if exclude_msg_id and meta.get('message_id') == exclude_msg_id:
                continue
            meta['score'] = float(scores[i])
            results.append(meta)

        return results

    @app_commands.command(name="search", description="Search images by text description")
    @app_commands.describe(query="Describe what you want to find")
    async def search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        guild_id = interaction.guild_id

        if guild_id not in guild_indices or len(guild_indices[guild_id]['ids']) == 0:
            await interaction.followup.send("❌ No images indexed yet. Upload some art first!", ephemeral=True)
            return

        try:
            q_emb = await embed_text(query)
        except Exception as e:
            logger.error(f"Text embed error: {e}")
            await interaction.followup.send("❌ Failed to process search query.", ephemeral=True)
            return

        if q_emb is None:
            await interaction.followup.send("❌ Failed to embed search query.", ephemeral=True)
            return

        results = self._search_index(guild_id, q_emb)
        if not results:
            await interaction.followup.send("❌ No matching images found.", ephemeral=True)
            return

        # Attach tags to results
        for r in results:
            r['tags'] = await get_tags(r['id'])

        paginator = ImagePaginator(results, query_info=f"Search: {query}", author_id=interaction.user.id, guild_id=guild_id)
        await paginator.send(interaction)

    @app_commands.command(name="searchbyimage", description="Upload an image to find similar ones")
    async def searchbyimage(self, interaction: discord.Interaction, image: discord.Attachment):
        await interaction.response.defer()
        guild_id = interaction.guild_id

        if not any(image.filename.lower().endswith(e) for e in ['.png','.jpg','.jpeg','.webp']):
            await interaction.followup.send("❌ Invalid image format.", ephemeral=True)
            return

        dl = await download_image(image.url)
        if not dl:
            await interaction.followup.send("❌ Failed to download image.", ephemeral=True)
            return

        import tempfile, os
        tmp_path = None
        try:
            suffix = os.path.splitext(image.filename)[1] or '.png'
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(dl['content'])
                tmp_path = tmp.name
            q_emb = await embed_image(tmp_path)
        except Exception as e:
            logger.error(f"Image embed error: {e}")
            await interaction.followup.send("❌ Failed to process image.", ephemeral=True)
            return
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        if q_emb is None:
            await interaction.followup.send("❌ Failed to embed image.", ephemeral=True)
            return

        results = self._search_index(guild_id, q_emb)
        if not results:
            await interaction.followup.send("❌ No similar images found.", ephemeral=True)
            return

        for r in results:
            r['tags'] = await get_tags(r['id'])

        paginator = ImagePaginator(results, query_info="Similar images", author_id=interaction.user.id, guild_id=guild_id)
        await paginator.send(interaction)

    @app_commands.command(name="gallery", description="Browse all images or a user's gallery")
    @app_commands.describe(user="User to browse (leave empty for all)")
    async def gallery(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        guild_id = interaction.guild_id

        if user:
            rows = await get_images_by_user(guild_id, user.id, limit=500)
            title = f"{user.display_name}'s Gallery"
        else:
            rows = await get_images_by_guild(guild_id, limit=500)
            title = "Server Gallery"

        if not rows:
            await interaction.followup.send("❌ No images found.", ephemeral=True)
            return

        for r in rows:
            r['tags'] = await get_tags(r['id'])

        paginator = ImagePaginator(rows, query_info=title, author_id=interaction.user.id, guild_id=guild_id)
        await paginator.send(interaction)

    @app_commands.command(name="random", description="Show a random image from the server")
    async def random_image(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild_id

        rows = await get_images_by_guild(guild_id, limit=1000)
        if not rows:
            await interaction.followup.send("❌ No images yet.", ephemeral=True)
            return

        import random
        img = random.choice(rows)
        img['tags'] = await get_tags(img['id'])

        embed = discord.Embed(
            title="🎲 Random Image",
            description=(
                f"**By:** <@{img['user_id']}> (`{img.get('username', 'Unknown')}`)\n"
                f"**Tags:** {', '.join(f'`{t}`' for t in img['tags']) or '`none`'}\n"
                f"**Size:** {img.get('width', '?')}×{img.get('height', '?')} | {img.get('format', '?')}\n"
                f"[🔗 Jump to Message](https://discord.com/channels/{guild_id}/{img['channel_id']}/{img['message_id']})"
            ),
            color=discord.Color.random()
        )
        embed.set_image(url=img['url'])
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="show", description="Show an image by ID")
    @app_commands.describe(image_id="Image ID to display")
    async def show(self, interaction: discord.Interaction, image_id: int):
        await interaction.response.defer()
        guild_id = interaction.guild_id

        img = await get_image_by_id(guild_id, image_id)
        if not img:
            await interaction.followup.send("❌ Image not found.", ephemeral=True)
            return

        img['tags'] = await get_tags(img['id'])

        embed = discord.Embed(
            title=f"🖼️ Image #{img['id']}",
            description=(
                f"**By:** <@{img['user_id']}> (`{img.get('username', 'Unknown')}`)\n"
                f"**Tags:** {', '.join(f'`{t}`' for t in img['tags']) or '`none`'}\n"
                f"**Size:** {img.get('width', '?')}×{img.get('height', '?')} | {img.get('format', '?')}\n"
                f"[🔗 Jump to Message](https://discord.com/channels/{guild_id}/{img['channel_id']}/{img['message_id']})"
            ),
            color=discord.Color.blue()
        )
        embed.set_image(url=img['url'])
        await interaction.followup.send(embed=embed)

async def setup(bot):
    cog = Search(bot)
    await bot.add_cog(cog)
    bot.tree.add_command(
        app_commands.context_menu(name="🔍 Find Similar")(find_similar_context_callback)
    )
