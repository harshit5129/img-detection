from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import numpy as np
import os
from contextlib import asynccontextmanager
from embeddings import embed_text, embed_image, text_model, image_model
from config import logger

API_KEY = os.getenv("API_KEY")

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid token")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Embedding server started")
    yield
    logger.info("Embedding server stopped")

app = FastAPI(lifespan=lifespan)

class TextRequest(BaseModel):
    text: str

class ImageRequest(BaseModel):
    image_path: str

@app.post("/embed/text")
async def embed_text_endpoint(req: TextRequest, _: HTTPAuthorizationCredentials = Depends(verify_token)):
    if text_model is None:
        raise HTTPException(status_code=500, detail="Text model not loaded")
    try:
        emb = await embed_text(req.text)
        return {"embedding": emb.tolist()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/embed/image")
async def embed_image_endpoint(req: ImageRequest, _: HTTPAuthorizationCredentials = Depends(verify_token)):
    if image_model is None:
        raise HTTPException(status_code=500, detail="Image model not loaded")
    try:
        emb = await embed_image(req.image_path)
        return {"embedding": emb.tolist()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "healthy", "models_loaded": text_model is not None and image_model is not None}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)