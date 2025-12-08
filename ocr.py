"""
OCR processing module for the duplicate detection bot.
"""
import io
import logging
import os
import re
from typing import Dict, Optional
import asyncio
import aiohttp
from PIL import Image
import pytesseract
from discord import Attachment

logger = logging.getLogger("DuplicateDetector")

# Configure Tesseract path (if needed)
try:
    # Try to get Tesseract path from environment
    TESSERACT_PATH = os.getenv("TESSERACT_PATH", "")
    if TESSERACT_PATH and os.path.exists(TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
except Exception as e:
    logger.debug(f"Error setting Tesseract path: {e}")

def get_ocr_engine():
    """Get available OCR engine."""
    try:
        # Test if Tesseract is available
        pytesseract.get_tesseract_version()
        return "tesseract"
    except Exception:
        logger.warning("Tesseract OCR not available. OCR features will be limited.")
        return None

async def process_image_for_ocr(url: str) -> Optional[Dict[str, any]]:
    """Process an image for OCR."""
    if not get_ocr_engine():
        return None
    
    try:
        # Download the image
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.debug(f"Failed to download image for OCR: HTTP {response.status}")
                    return None
                
                image_data = await response.read()
        
        # Process the image
        with Image.open(io.BytesIO(image_data)) as img:
            # Preprocess the image for better OCR results
            img = preprocess_image(img)
            
            # Perform OCR
            text = pytesseract.image_to_string(img)
            
            # Clean up the text
            text = clean_ocr_text(text)
            
            # Calculate confidence (simple heuristic)
            confidence = calculate_confidence(text)
            
            return {
                "text": text,
                "confidence": confidence,
                "engine": "tesseract"
            }
    
    except Exception as e:
        logger.debug(f"OCR processing failed: {e}")
        return None

def preprocess_image(img: Image.Image) -> Image.Image:
    """Preprocess image for better OCR results."""
    # Convert to grayscale
    img = img.convert('L')
    
    # Increase contrast
    img = ImageEnhance.Contrast(img).enhance(2.0)
    
    # Binarize
    img = img.point(lambda x: 0 if x < 128 else 255, '1')
    
    # Resize for better recognition
    width, height = img.size
    new_width = int(width * 1.5)
    new_height = int(height * 1.5)
    img = img.resize((new_width, new_height), Image.LANCZOS)
    
    # Denoise
    img = img.filter(ImageFilter.MedianFilter())
    
    return img

def clean_ocr_text(text: str) -> str:
    """Clean up OCR text by removing noise and normalizing."""
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Remove non-printable characters
    text = re.sub(r'[^\x20-\x7E]+', '', text)
    
    # Fix common OCR errors
    text = text.replace('O', '0').replace('l', '1').replace('I', '1')
    
    return text

def calculate_confidence(text: str) -> float:
    """Calculate a simple confidence score for OCR results."""
    if not text:
        return 0.0
    
    # Basic heuristic: longer text with more variety is more likely valid
    length_score = min(1.0, len(text) / 100)  # Cap at 100 characters
    
    # Character variety score (0-1)
    unique_chars = len(set(text))
    variety_score = min(1.0, unique_chars / 50)
    
    # Word count score (more words = more likely valid)
    words = text.split()
    word_score = min(1.0, len(words) / 20)
    
    # Combined score (weighted average)
    confidence = (length_score * 0.3 + variety_score * 0.4 + word_score * 0.3) * 100
    
    return confidence

def format_ocr_results(ocr_data: Dict[str, any]) -> str:
    """Format OCR results for display."""
    if not ocr_data or not ocr_data.get("text"):
        return "No text detected"
    
    confidence = ocr_data.get("confidence", 0)
    return (
        f"**OCR Results** (Confidence: {confidence:.1f}%)\n"
        f"```\n{ocr_data['text']}\n```"
    )
