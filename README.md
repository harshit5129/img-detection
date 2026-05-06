# Art Detection Bot

 Discord bot for semantic image search using external embedding API (CLIP). Requires EMBED_API_URL and API_KEY environment variables.

## Features

- Automatic image indexing from uploads
- Semantic text search for similar images
- Image similarity search
- Tag-based organization
- Gallery browsing
- Random image picker

## Commands

### User Commands
- `/search [query]` - Search images by text description
- `/searchbyimage` - Find similar images by uploading one
- `/gallery [user]` - Browse all or a specific user's images
- `/random` - Show a random image
- `/show [id]` - Show image by ID
- `/tags add|remove|list [image_id] [tag]` - Manage tags

### Admin/Mod Commands
- `/setup` - Initialize bot for the server
- `/whitelist #channel` - Enable auto-indexing in a channel
- `/blacklist #channel` - Block a channel
- `/scan` - Scan all channels for images
- `/scanchannel #channel` - Scan a specific channel
- `/setmod @role` - Set moderator role
- `/stats` - Show bot statistics
- `/cleardata` - Wipe all server data
- `/optimize` - Optimize database
- `/help` - Show help

## Setup

```bash
# Install dependencies
uv sync

# Run the bot
uv run main.py
```

## Environment Variables

Create a `.env` file:
```
DISCORD_BOT_TOKEN=your_token_here
EMBED_API_URL=https://your-embed-api.com
API_KEY=your_api_key
```

## Permissions Required

- Read Messages
- Send Messages
- Manage Messages (for reactions)
- Add Reactions
- Use Application Commands

## Tech Stack

- Python 3.12+
- discord.py
- NumPy
- aiosqlite
- PIL
- aiohttp