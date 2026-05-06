import numpy as np
import aiohttp
from config import EMBED_API_URL, EMBED_API_KEY, USE_EMBED_API, logger

if not USE_EMBED_API:
    raise RuntimeError("USE_EMBED_API must be True (fastembed has been removed)")


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.array(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


async def embed_image(path_or_pil) -> np.ndarray:
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {EMBED_API_KEY}"}
            data = aiohttp.FormData()
            with open(path_or_pil, 'rb') as f:
                data.add_field('file', f, filename='image.png', content_type='image/png')
                async with session.post(f"{EMBED_API_URL}/embed/image", headers=headers, data=data) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(f"Embed API error: {resp.status} - {text}")
                        return None
                    result = await resp.json()
                    emb = np.array(result['embedding'], dtype=np.float32)
                    return _normalize(emb)
    except Exception as e:
        logger.error(f"Embed image error: {e}")
        return None


async def embed_text(text: str) -> np.ndarray:
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {EMBED_API_KEY}"}
            async with session.post(f"{EMBED_API_URL}/embed/text", headers=headers, json={"text": text}) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Embed API error: {resp.status} - {text}")
                    return None
                result = await resp.json()
                emb = np.array(result['embedding'], dtype=np.float32)
                return _normalize(emb)
    except Exception as e:
        logger.error(f"Embed text error: {e}")
        return None


def cosine_similarity_batch(query: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
    return np.dot(embeddings, query)