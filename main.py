import asyncio
import discord
from bot import create_bot
from config import TOKEN, logger
from database import db_pool, save_guild_config

async def shutdown(bot):
    logger.info("Shutting down...")
    for gid in list(bot.cogs.keys()):
        cog = bot.get_cog(gid)
        if cog and hasattr(cog, 'auto_save') and cog.auto_save.is_running():
            cog.auto_save.cancel()

    from config import guild_data
    for gid in list(guild_data.keys()):
        await save_guild_config(gid)

    await db_pool.close_all()
    logger.info("Shutdown complete")

def main():
    bot = create_bot()
    logger.info("=" * 60)
    logger.info("🚀 Art Gallery Bot with Semantic Search")
    logger.info("=" * 60)

    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Invalid token. Check DISCORD_BOT_TOKEN.")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        asyncio.run(shutdown(bot))
    except Exception as e:
        logger.error(f"Fatal: {e}")
        asyncio.run(shutdown(bot))

if __name__ == "__main__":
    main()
