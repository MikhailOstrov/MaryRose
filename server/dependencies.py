from fastapi.security import APIKeyHeader
from typing import Dict
from api.meet_listener import MeetListenerBot
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
import asyncio

import logging

logger = logging.getLogger(__name__)

active_meetings: Dict[str, MeetListenerBot] = {}

app = FastAPI(
    title="MaryRose API",
    description="API для управления ботом MaryRose и получения результатов встреч.",
    version="1.0.0"
)

active_bots = {}

# API ключи
API_KEY = 'key' 
API_KEY_NAME = "X-Internal-Api-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# Проверка ключа
async def get_api_key(api_key: str = Depends(api_key_header)):
    if api_key == API_KEY:
        return api_key
    else:
        logger.warning("Failed API Key validation")
        raise HTTPException(status_code=403, detail="Could not validate credentials")
