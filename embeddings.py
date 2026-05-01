import asyncio
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from fastembed import ImageEmbedding, TextEmbedding
from config import logger

try:
    image_model = ImageEmbedding(model_name="Qdrant/clip-ViT-B-32-vision")
    text_model = TextEmbedding(model_name="Qdrant/clip-ViT-B-32-text")
    logger.info("FastEmbed models loaded")
except Exception as e:
    logger.error(f"Failed to load FastEmbed: {e}")
    image_model = None
    text_model = None

_executor = ThreadPoolExecutor(max_workers=2)

def _norm(v: np.ndarray) -> np.ndarray:
    v = np.array(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v if n == 0 else v / n

def _embed_image_sync(path_or_pil):
    if image_model is None:
        raise RuntimeError("Image model not loaded")
    emb = list(image_model.embed([path_or_pil]))[0]
    return _norm(emb)

def _embed_text_sync(text: str):
    if text_model is None:
        raise RuntimeError("Text model not loaded")
    emb = list(text_model.embed([text]))[0]
    return _norm(emb)

async def embed_image(path_or_pil) -> np.ndarray:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _embed_image_sync, path_or_pil)

async def embed_text(text: str) -> np.ndarray:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _embed_text_sync, text)

def cosine_similarity_batch(query: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
    return np.dot(embeddings, query)
