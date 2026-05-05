import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ArtBot')

TOKEN = os.getenv('DISCORD_BOT_TOKEN')
EMBED_API_URL = os.getenv('EMBED_API_URL')
EMBED_API_KEY = os.getenv('API_KEY')
USE_EMBED_API = bool(EMBED_API_URL and EMBED_API_KEY)

DATA_DIR = 'bot_data'
DB_FILE = os.path.join(DATA_DIR, 'artbot.db')
os.makedirs(DATA_DIR, exist_ok=True)

DB_POOL_SIZE = 5
MAX_CONCURRENT_DOWNLOADS = 3
RATE_LIMIT_DELAY = 1.5
DOWNLOAD_TIMEOUT = 20
EMBEDDING_DIM = 512
TOP_K_SEARCH = 20

if not TOKEN:
    logger.error("DISCORD_BOT_TOKEN not found!")
    exit(1)

guild_data: dict = {}
guild_indices: dict = {}
