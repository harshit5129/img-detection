import discord
from discord import app_commands
from discord.ext import commands

from config import logger
from database import get_image_by_message, add_tag, remove_tag, get_tags, search_by_tag, get_images_by_user
from views import ImagePaginator

class Tags(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="tag", description="Add a tag to an image (use message_id from the image message)")
    @app_commands.describe(message_id="ID of the message containing the image", tag="Tag to add")
    async def tag(self, interaction: discord.Interaction, message_id: str, tag: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        try:
            mid = int(message_id)
        except:
            await interaction.followup.send("❌ Invalid message ID.", ephemeral=True)
            return

        img = await get_image_by_message(guild_id, mid)
        if not img:
            await interaction.followup.send("❌ Image not found in database. Make sure the bot saw it before.", ephemeral=True)
            return

        await add_tag(img['id'], tag.lower().strip())
        await interaction.followup.send(f"✅ Added tag `#{tag.lower().strip()}` to image.", ephemeral=True)

    @app_commands.command(name="untag", description="Remove a tag from an image")
    @app_commands.describe(message_id="ID of the message containing the image", tag="Tag to remove")
    async def untag(self, interaction: discord.Interaction, message_id: str, tag: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        try:
            mid = int(message_id)
        except:
            await interaction.followup.send("❌ Invalid message ID.", ephemeral=True)
            return

        img = await get_image_by_message(guild_id, mid)
        if not img:
            await interaction.followup.send("❌ Image not found.", ephemeral=True)
            return

        await remove_tag(img['id'], tag.lower().strip())
        await interaction.followup.send(f"✅ Removed tag `#{tag.lower().strip()}`.", ephemeral=True)

    @app_commands.command(name="tags", description="List tags for an image")
    @app_commands.describe(message_id="ID of the message containing the image")
    async def tags(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        try:
            mid = int(message_id)
        except:
            await interaction.followup.send("❌ Invalid message ID.", ephemeral=True)
            return

        img = await get_image_by_message(guild_id, mid)
        if not img:
            await interaction.followup.send("❌ Image not found.", ephemeral=True)
            return

        tags = await get_tags(img['id'])
        if tags:
            await interaction.followup.send(f"🏷️ Tags: {', '.join(f'`{t}`' for t in tags)}", ephemeral=True)
        else:
            await interaction.followup.send("🏷️ No tags on this image.", ephemeral=True)

    @app_commands.command(name="tagsearch", description="Search images by tag")
    @app_commands.describe(tag="Tag to search for")
    async def tagsearch(self, interaction: discord.Interaction, tag: str):
        await interaction.response.defer()
        guild_id = interaction.guild_id

        rows = await search_by_tag(guild_id, tag, limit=200)
        if not rows:
            await interaction.followup.send(f"❌ No images found with tag `#{tag}`.", ephemeral=True)
            return

        for r in rows:
            r['tags'] = await get_tags(r['id'])

        paginator = ImagePaginator(rows, query_info=f"Tag: #{tag}", author_id=interaction.user.id, guild_id=guild_id)
        await paginator.send(interaction)

    @app_commands.command(name="mygallery", description="Browse your uploaded images")
    async def mygallery(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild_id

        rows = await get_images_by_user(guild_id, interaction.user.id, limit=500)
        if not rows:
            await interaction.followup.send("❌ You haven't uploaded any indexed images yet.", ephemeral=True)
            return

        for r in rows:
            r['tags'] = await get_tags(r['id'])

        paginator = ImagePaginator(rows, query_info="Your Gallery", author_id=interaction.user.id, guild_id=guild_id)
        await paginator.send(interaction)

async def setup(bot):
    await bot.add_cog(Tags(bot))
