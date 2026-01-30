Discord Duplicate Image Detection Bot

Description
A powerful Discord bot that automatically detects duplicate/reposted images using perceptual hashing (pHash) with Hamming distance comparison. Features auto-reactions, comprehensive statistics, batch scanning, and advanced configuration options.

Features
- Automatic duplicate detection when images are posted
- Perceptual hashing (pHash) to detect similar/modified images
- LRU Cache + SQLite with connection pooling for high performance
- Customizable auto-reactions (🔄 Processing, ✅ Unique, ❌ Duplicate)
- User statistics and leaderboards
- Per-channel similarity thresholds
- Whitelist/Blacklist system for channels
- Batch scanning of channel history
- Export data to CSV/JSON
- Auto-delete duplicate option

Installation

Prerequisites
- Python 3.8 or higher
- Discord Bot Token (from Discord Developer Portal)

Step 1: Install Dependencies
Create requirements.txt:
discord.py>=2.3.0
Pillow>=9.0.0
imagehash>=4.3.0
aiohttp>=3.8.0
aiosqlite>=0.19.0
python-dotenv>=1.0.0

Install:
pip install -r requirements.txt

Step 2: Create .env file
DISCORD_BOT_TOKEN=your_bot_token_here
ADMIN_ROLE_NAME=Admin
HASH_THRESHOLD=5
LOG_CHANNEL_NAME=duplicate-logs
AUTO_DELETE_DUPLICATES=false

Step 3: Run the Bot
python main.py

Setup Instructions

1. Invite bot to server with permissions:
   - Read Messages/View Channels
   - Send Messages
   - Add Reactions
   - Manage Messages (if using auto-delete)
   - Attach Files

2. Run setup command:
/setup

3. Whitelist channels to monitor:
/whitelist #general
/whitelist #memes

Command Reference

Setup Commands (Admin only)
- /setup - Initialize bot (blacklists all channels by default)
- /whitelist [channel] - Add/remove channel from scan list
- /blacklist [channel] - Add/remove channel from blacklist
- /whitelistuser [user] - Ignore specific user's images

Scanning Commands (Admin only)
- /scanhistory [channel] [limit] - Scan channel history (max 100k messages)
- /scanall [limit] - Scan all whitelisted channels
- /resumescan [channel] - Resume interrupted scan

Configuration Commands (Admin only)
- /setsimilarity [threshold 0-10] - Set global detection sensitivity
- /setchannelthreshold [channel] [0-10] - Set per-channel threshold
- /setnotifications [mode] [target] - Configure alerts (role/user/silent)
- /toggleautodelete - Toggle auto-delete duplicates on/off
- /autoreaction [type] [emoji] [enabled] [auto_remove] [timeout] - Configure reactions
- /reaction_settings - View current reaction configuration

Statistics Commands
- /stats - Show bot statistics
- /userstats [user] - Show user statistics
- /leaderboard [mode] - Show top posters (unique/duplicates)
- /export [format] - Export duplicate history (CSV/JSON)
- /cachestats - Show cache statistics (Admin only)
- /dbinfo - Show database info (Admin only)

Utility Commands
- /ping - Check bot latency
- /bothelp - Show help menu
- /optimize - Optimize database (Admin only)
- /cleardata - Delete all server data (Admin only, requires confirmation)

Project File Structure
img-detection/
├── main.py                 # Bot entry point
├── config.py              # Configuration constants
├── database.py            # Database operations
├── cache.py               # LRU Cache implementation
├── reaction_manager.py    # Auto-reaction system
├── image_processor.py     # Image hash calculation
├── utils.py               # Helper functions
├── cogs/
│   ├── commands.py        # Slash commands
│   └── events.py          # Event listeners
├── bot_data/              # Database storage (auto-created)
│   └── bot_data.db
├── .env                   # Environment variables
└── README.txt

Configuration Details

Hash Threshold Guide
- 0-2: Exact matches only (very strict)
- 3-5: Similar images (recommended default)
- 6-8: Loose matching (catches edited images)
- 9-10: Very loose (may have false positives)

Environment Variables Explained
DISCORD_BOT_TOKEN - Your Discord bot token (required)
ADMIN_ROLE_NAME - Name of admin role for command permissions (default: Admin)
HASH_THRESHOLD - Default detection sensitivity 0-10 (default: 5)
LOG_CHANNEL_NAME - Channel name for duplicate alerts (default: duplicate-logs)
AUTO_DELETE_DUPLICATES - Automatically delete duplicates true/false (default: false)

How It Works

1. When an image is posted in a whitelisted channel:
   - Bot adds 🔄 (processing) reaction
   - Downloads and calculates pHash
   - Compares with stored hashes in database
   - If similar found: adds ❌ + sends alert
   - If unique: adds ✅ + saves hash

2. Database stores:
   - Image hashes (not actual images)
   - Message IDs and metadata
   - User statistics
   - Duplicate history

3. Caching:
   - Recently seen hashes cached in memory
   - Reduces database queries
   - Improves response time

Troubleshooting

Bot not responding:
- Check if bot token is correct in .env
- Ensure bot has proper permissions in Discord
- Enable MESSAGE CONTENT INTENT in Developer Portal
- Check console for errors

Database errors:
- Stop bot and delete bot_data/ folder to reset
- Check disk space available

Import errors:
- Run: pip install -r requirements.txt --upgrade
- Ensure Python 3.8+ is being used

Privacy Note
- Bot does NOT store actual images
- Only stores perceptual hashes (64-bit identifiers)
- Stores message metadata (ID, timestamp, user ID)
- All data stays in your local SQLite database

Performance Specifications
- Max Cache: 2000 recent hashes in memory
- Database: SQLite with WAL mode (high concurrency)
- Rate Limiting: Automatic Discord API handling
- Max File Size: 10MB download limit
- Supported Formats: PNG, JPG, JPEG, GIF, WEBP, BMP

Version Information
Version: 2.2.0
Last Updated: January 2026
Python: 3.8+
Discord.py: 2.3.0+

Support
For issues or questions, check:
1. Console error logs
2. Database file permissions
3. Discord bot permissions
4. Environment variables configuration

License
MIT License - Free to use and modify for personal or commercial use.
