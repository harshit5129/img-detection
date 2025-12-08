"""
Discord Duplicate Image Detection Bot - ASCII Compatible Version
"""
import os
import asyncio
import logging
import sys
import time

# Ensure UTF-8 encoding for Windows
if sys.platform == "win32":
    try:
        # Try to set console to UTF-8
        os.environ['PYTHONIOENCODING'] = 'utf-8'
        sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None
        sys.stderr.reconfigure(encoding='utf-8') if hasattr(sys.stderr, 'reconfigure') else None
    except:
        # If reconfigure isn't available, use this fallback
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

import discord
from discord.ext import commands

from db import init_db_sync
from config import ensure_guild_config
from detection import setup_detection
from commands_general import register_general_commands
from commands_admin import register_admin_commands

# ---------- Logging Configuration ----------
def setup_logging():
    """Configure logging with ASCII characters only."""
    log_format = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # File handler with rotation
    os.makedirs("logs", exist_ok=True)
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        "logs/bot.log", 
        maxBytes=10*1024*1024,
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Configure library loggers
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    
    # ASCII-only welcome message
    logger = logging.getLogger("DuplicateDetector")
    logger.info("=" * 70)
    logger.info("  Discord Duplicate Image Detection Bot - ASCII Compatible Version")
    logger.info("  Starting up with OCR and advanced features...")
    logger.info("=" * 70)
    
    return logger

logger = setup_logging()

# ---------- Token Validation ----------
def get_bot_token() -> str:
    """Get and validate bot token from environment variable."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    
    if not token:
        logger.error("ERROR: DISCORD_BOT_TOKEN environment variable not set")
        print("\n" + "="*70)
        print("ERROR: DISCORD_BOT_TOKEN environment variable not set!")
        print("="*70)
        print("\nTo fix this:")
        print("1. Create a .env file in the project directory")
        print("2. Add the line: DISCORD_BOT_TOKEN=your_token_here")
        print("3. Restart the bot")
        print("\n" + "="*70 + "\n")
        sys.exit(1)
    
    token = token.strip()
    
    # Validate token format
    if len(token) < 50:
        logger.error(f"Invalid token format: too short ({len(token)} characters)")
        print(f"\nERROR: Token is too short ({len(token)} characters)")
        print("Discord bot tokens are typically 59+ characters long")
        print("Please check you've set the correct token\n")
        sys.exit(1)
    
    # Show masked token
    masked = token[:15] + "..." + token[-8:]
    logger.info(f"Token loaded: {masked} ({len(token)} chars)")
    
    return token

# ---------- Bot Setup ----------
def create_bot() -> commands.Bot:
    """Create and configure the bot instance."""
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True
    intents.reactions = True
    
    bot = commands.Bot(
        command_prefix="!",
        intents=intents,
        help_command=None,
        case_insensitive=True,
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="for duplicate images"
        )
    )
    
    setup_error_handlers(bot)
    return bot

def setup_error_handlers(bot: commands.Bot):
    """Setup global error handlers."""
    
    @bot.event
    async def on_error(event: str, *args, **kwargs):
        logger.error(f"Error in event {event}", exc_info=True)
    
    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You don't have permission to use this command.")
            return
            
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"Invalid argument: {error}")
            return
            
        logger.error(f"Command error in {ctx.command}: {error}", exc_info=error)
        await ctx.send("An unexpected error occurred. Please try again later.")

bot = create_bot()

@bot.event
async def on_ready():
    """Bot ready event with safe initialization."""
    logger.info("=" * 70)
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guild(s)")
    logger.info("=" * 70)
    
    if hasattr(bot, "_ready_initialized"):
        logger.info("on_ready fired again, skipping initialization.")
        return
    bot._ready_initialized = True
    
    logger.info("Loading guild configurations...")
    loaded = 0
    failed = 0
    
    for guild in bot.guilds:
        try:
            await ensure_guild_config(guild.id)
            loaded += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Failed to load config for guild {guild.id}: {e}")
            failed += 1
    
    logger.info(f"Loaded {loaded} guild config(s), {failed} failed")
    
    logger.info("Syncing application commands...")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}", exc_info=True)
    
    logger.info("=" * 70)
    logger.info("Bot is fully operational!")
    logger.info("Use /help to see available commands")
    logger.info("=" * 70)

@bot.event
async def on_guild_join(guild: discord.Guild):
    """Handle bot joining new guilds."""
    logger.info(f"Joined guild: {guild.name} (ID: {guild.id})")
    try:
        await ensure_guild_config(guild.id)
    except Exception as e:
        logger.error(f"Failed to initialize guild {guild.id}: {e}")

@bot.event
async def on_guild_remove(guild: discord.Guild):
    """Handle bot leaving guilds."""
    logger.info(f"Left guild: {guild.name} (ID: {guild.id})")

async def shutdown(bot: commands.Bot):
    """Graceful shutdown handler."""
    logger.info("Shutting down bot...")
    try:
        await bot.close()
        logger.info("Bot closed successfully")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

def main():
    """Main entry point with proper error handling."""
    print("\n" + "=" * 70)
    print("  Discord Duplicate Image Detection Bot - ASCII Compatible Version")
    print("  Starting up with OCR and advanced features...")
    print("=" * 70 + "\n")
    
    try:
        # Initialize database
        logger.info("Initializing database...")
        init_db_sync()
        logger.info("Database initialized")
        
        # Create directories
        os.makedirs("bot_data", exist_ok=True)
        os.makedirs("logs", exist_ok=True)
        logger.info("Directories ready")
        
        # Register modules
        logger.info("Registering modules...")
        setup_detection(bot)
        register_general_commands(bot)
        register_admin_commands(bot)
        logger.info("Modules registered")
        
        # Get token
        logger.info("Loading bot token...")
        token = get_bot_token()
        
        # Start bot
        logger.info("Connecting to Discord...")
        logger.info("=" * 70)
        bot.run(token, log_handler=None)
        
    except KeyboardInterrupt:
        logger.info("\nBot stopped by user (Ctrl+C)")
        asyncio.run(shutdown(bot))
    except discord.LoginFailure:
        logger.error("LOGIN FAILED - Invalid Discord bot token!")
        print("\n" + "="*70)
        print("LOGIN FAILED - Invalid Discord bot token!")
        print("="*70)
        print("\nYour token is incorrect or has been regenerated.")
        print("\nTO FIX:")
        print("1. Go to: https://discord.com/developers/applications")
        print("2. Select your application")
        print("3. Go to 'Bot' section")
        print("4. Click 'Reset Token' and copy the NEW token")
        print("5. Set the DISCORD_BOT_TOKEN environment variable")
        print("\n" + "="*70 + "\n")
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        logger.error("PRIVILEGED INTENTS REQUIRED")
        print("\n" + "="*70)
        print("PRIVILEGED INTENTS REQUIRED")
        print("="*70)
        print("\nThe bot needs privileged intents enabled:")
        print("\nTO FIX:")
        print("1. Go to: https://discord.com/developers/applications")
        print("2. Select your application")
        print("3. Go to 'Bot' section")
        print("4. Scroll down to 'Privileged Gateway Intents'")
        print("5. Enable 'MESSAGE CONTENT INTENT'")
        print("6. Enable 'SERVER MEMBERS INTENT'")
        print("7. Click 'Save Changes'")
        print("8. Restart the bot")
        print("\n" + "="*70 + "\n")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"FATAL ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
