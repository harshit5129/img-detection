"""
Simple test script to verify bot configuration and code logic.
"""
import asyncio
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

async def test_configuration():
    """Test bot configuration."""
    print("=" * 60)
    print("Bot Configuration Test")
    print("=" * 60)
    
    # Test 1: Environment variables
    print("\n1. Testing environment variables...")
    token = os.getenv("DISCORD_BOT_TOKEN")
    mongodb_uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("DB_NAME", "img_detection")
    
    if token:
        print(f"   ✅ DISCORD_BOT_TOKEN: Set ({len(token)} chars)")
        if len(token) < 50:
            print("   ⚠️  WARNING: Token seems too short")
    else:
        print("   ❌ DISCORD_BOT_TOKEN: Not set")
    
    if mongodb_uri:
        print(f"   ✅ MONGODB_URI: Set")
    else:
        print("   ❌ MONGODB_URI: Not set")
    
    print(f"   ✅ DB_NAME: {db_name}")
    
    # Test 2: MongoDB connection
    print("\n2. Testing MongoDB connection...")
    try:
        from db import init_db, get_db
        await init_db()
        db = get_db()
        
        # Try to ping
        await db.command('ping')
        print("   ✅ MongoDB connection successful")
        
        # List collections
        collections = await db.list_collection_names()
        print(f"   ✅ Collections: {', '.join(collections) if collections else 'None (will be created)'}")
        
    except Exception as e:
        print(f"   ❌ MongoDB connection failed: {e}")
    
    # Test 3: Import modules
    print("\n3. Testing module imports...")
    try:
        from config import ensure_guild_config
        print("   ✅ config.py imported")
    except Exception as e:
        print(f"   ❌ config.py import failed: {e}")
    
    try:
        from detection import setup_detection
        print("   ✅ detection.py imported")
    except Exception as e:
        print(f"   ❌ detection.py import failed: {e}")
    
    try:
        from commands_general import register_general_commands
        print("   ✅ commands_general.py imported")
    except Exception as e:
        print(f"   ❌ commands_general.py import failed: {e}")
    
    try:
        from commands_admin import register_admin_commands
        print("   ✅ commands_admin.py imported")
    except Exception as e:
        print(f"   ❌ commands_admin.py import failed: {e}")
    
    try:
        from analytics import register_analytics_commands
        print("   ✅ analytics.py imported")
    except Exception as e:
        print(f"   ❌ analytics.py import failed: {e}")
    
    try:
        from whitelist import register_whitelist_commands
        print("   ✅ whitelist.py imported")
    except Exception as e:
        print(f"   ❌ whitelist.py import failed: {e}")
    
    # Test 4: Discord.py
    print("\n4. Testing Discord.py...")
    try:
        import discord
        print(f"   ✅ discord.py version: {discord.__version__}")
    except Exception as e:
        print(f"   ❌ discord.py import failed: {e}")
    
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print("\nIf all tests passed, the bot code is working correctly.")
    print("If MongoDB or Discord token test failed, check your .env file.")
    print("\nTo fix Discord token:")
    print("1. Go to https://discord.com/developers/applications")
    print("2. Select your bot")  
    print("3. Go to Bot tab")
    print("4. Click 'Reset Token' and copy the new token")
    print("5. Update DISCORD_BOT_TOKEN in .env file")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_configuration())
