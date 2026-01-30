import discord
from discord.ext import commands
import asyncio
import sys
from config import TOKEN, logger

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def setup_hook():
    await bot.load_extension('cogs.events')
    await bot.load_extension('cogs.commands')

async def shutdown():
    """Graceful shutdown"""
    logger.info("Shutting down...")
    
    from database import save_guild_data, db_pool
    from config import guild_data
    
    # Save all guild data
    for gid in list(guild_data.keys()):
        await save_guild_data(gid)
    
    # Close DB pool
    await db_pool.close_all()
    
    logger.info("Shutdown complete")

def main():
    if not TOKEN:
        logger.error("No token found! Check your .env file.")
        return
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("Invalid token!")
    except KeyboardInterrupt:
        asyncio.run(shutdown())
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        asyncio.run(shutdown())

if __name__ == "__main__":
    main()
