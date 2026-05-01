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
ADMIN_ROLE_NAME = os.getenv('ADMIN_ROLE_NAME', 'Admin')
LOG_CHANNEL_NAME = os.getenv('LOG_CHANNEL_NAME', 'art-logs')
AUTO_DELETE_DUPLICATES = os.getenv('AUTO_DELETE_DUPLICATES', 'false').lower() == 'true'
HASH_THRESHOLD = int(os.getenv('HASH_THRESHOLD', '5'))

DATA_DIR = 'bot_data'
DB_FILE = os.path.join(DATA_DIR, 'artbot.db')
os.makedirs(DATA_DIR, exist_ok=True)

MAX_CACHE_SIZE = 2000
DB_POOL_SIZE = 5
MAX_CONCURRENT_DOWNLOADS = 3
RATE_LIMIT_DELAY = 1.5
BATCH_COMMIT_SIZE = 50
DOWNLOAD_TIMEOUT = 20
EMBEDDING_DIM = 512
TOP_K_SEARCH = 20

if not TOKEN:
    logger.error("DISCORD_BOT_TOKEN not found!")
    exit(1)

guild_data: dict = {}
guild_indices: dict = {}
