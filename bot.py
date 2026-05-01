import discord
from discord.ext import commands

from config import TOKEN, logger

class ArtBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix='!', intents=intents, help_command=None)

    async def setup_hook(self):
        cogs = [
            'cogs.events',
            'cogs.search',
            'cogs.tags',
            'cogs.admin'
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"Loaded {cog}")
            except Exception as e:
                logger.error(f"Failed {cog}: {e}")

        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} commands")
        except Exception as e:
            logger.error(f"Sync error: {e}")

    async def on_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Admin only.", ephemeral=True)
        else:
            logger.error(f"Command error: {error}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {str(error)[:100]}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {str(error)[:100]}", ephemeral=True)

def create_bot():
    return ArtBot()
