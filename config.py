import os
import logging
from dotenv import load_dotenv
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, Dict, Any

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('DuplicateDetector')

class ReactionType(Enum):
    PROCESSING = "processing"
    UNIQUE = "unique"
    DUPLICATE = "duplicate"
    ERROR = "error"
    WARNING = "warning"

@dataclass
class ReactionConfig:
    emoji: str
    enabled: bool
    auto_remove: bool
    remove_timeout: int
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)

# Default configurations
DEFAULT_REACTIONS = {
    ReactionType.PROCESSING: ReactionConfig("🔄", True, True, 10),
    ReactionType.UNIQUE: ReactionConfig("✅", True, True, 30),
    ReactionType.DUPLICATE: ReactionConfig("❌", True, False, 0),
    ReactionType.ERROR: ReactionConfig("⚠️", True, True, 60),
    ReactionType.WARNING: ReactionConfig("🚫", True, True, 30)
}

# Environment variables
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ADMIN_ROLE_NAME = os.getenv('ADMIN_ROLE_NAME', 'Admin')
HASH_THRESHOLD = int(os.getenv('HASH_THRESHOLD', '5'))
LOG_CHANNEL_NAME = os.getenv('LOG_CHANNEL_NAME', 'duplicate-logs')
AUTO_DELETE_DUPLICATES = os.getenv('AUTO_DELETE_DUPLICATES', 'false').lower() == 'true'

# Performance settings
MAX_CACHE_SIZE = 2000
DB_POOL_SIZE = 5
MAX_CONCURRENT_DOWNLOADS = 3
RATE_LIMIT_DELAY = 1.5
BATCH_COMMIT_SIZE = 50
MAX_RETRY_ATTEMPTS = 3
DOWNLOAD_TIMEOUT = 20

if not TOKEN:
    logger.error("DISCORD_BOT_TOKEN not found!")
    exit(1)

# Global guild data storage
guild_data: Dict[int, Dict[str, Any]] = {}

# PATHS
DATA_DIR = 'bot_data'
DB_FILE = os.path.join(DATA_DIR, 'bot_data.db')
