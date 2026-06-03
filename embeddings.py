import asyncio
import numpy as np
from config import logger

_image_model = None
_text_model = None


def _get_image_model():
    global _image_model
    if _image_model is None:
        from fastembed import ImageEmbedding
        _image_model = ImageEmbedding(model_name="Qdrant/clip-ViT-B-32-vision")
        logger.info("Fastembed image model loaded")
    return _image_model


def _get_text_model():
    global _text_model
    if _text_model is None:
        from fastembed import TextEmbedding
        _text_model = TextEmbedding(model_name="Qdrant/clip-ViT-B-32-text")
        logger.info("Fastembed text model loaded")
    return _text_model


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.array(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


async def embed_image(path_or_pil) -> np.ndarray:
    try:
        model = await asyncio.to_thread(_get_image_model)
        embeddings = list(await asyncio.to_thread(model.embed, [path_or_pil]))
        if not embeddings:
            return None
        return _normalize(np.array(embeddings[0], dtype=np.float32))
    except Exception as e:
        logger.error(f"Embed image error: {e}")
        return None


async def embed_text(text: str) -> np.ndarray:
    try:
        model = await asyncio.to_thread(_get_text_model)
        embeddings = list(await asyncio.to_thread(model.embed, [text]))
        if not embeddings:
            return None
        return _normalize(np.array(embeddings[0], dtype=np.float32))
    except Exception as e:
        logger.error(f"Embed text error: {e}")
        return None


def cosine_similarity_batch(query: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
    return np.dot(embeddings, query)
