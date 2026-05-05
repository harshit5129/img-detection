import asyncio
import numpy as np
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from config import logger, EMBED_API_URL, EMBED_API_KEY, USE_EMBED_API

if USE_EMBED_API:
    image_model = None
    text_model = None
else:
    try:
        from fastembed import ImageEmbedding, TextEmbedding
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

async def embed_image(path_or_pil) -> np.ndarray:
    if USE_EMBED_API:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {EMBED_API_KEY}"}
            with open(path_or_pil, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('file', f, filename='image.png', content_type='image/png')
            async with session.post(f"{EMBED_API_URL}/embed/image", headers=headers, data=data) as resp:
                result = await resp.json()
                return np.array(result['embedding'], dtype=np.float32)
    else:
        if image_model is None:
            raise RuntimeError("Image model not loaded")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, _embed_image_sync, path_or_pil)

async def embed_text(text: str) -> np.ndarray:
    if USE_EMBED_API:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {EMBED_API_KEY}"}
            async with session.post(f"{EMBED_API_URL}/embed/text", headers=headers, json={"text": text}) as resp:
                result = await resp.json()
                return np.array(result['embedding'], dtype=np.float32)
    else:
        if text_model is None:
            raise RuntimeError("Text model not loaded")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, _embed_text_sync, text)

def _embed_image_sync(path_or_pil):
    if image_model is None:
        raise RuntimeError("Image model not loaded")
    label = path_or_pil if isinstance(path_or_pil, str) else type(path_or_pil).__name__
    logger.info(f"Creating image embedding for: {label}")
    emb = list(image_model.embed([path_or_pil]))[0]
    logger.info(f"Image embedding created, shape: {emb.shape}")
    return _norm(emb)

def _embed_text_sync(text: str):
    if text_model is None:
        raise RuntimeError("Text model not loaded")
    logger.info(f"Creating text embedding for: {text[:50]}...")
    emb = list(text_model.embed([text]))[0]
    logger.info(f"Text embedding created, shape: {emb.shape}")
    return _norm(emb)

def cosine_similarity_batch(query: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
    return np.dot(embeddings, query)