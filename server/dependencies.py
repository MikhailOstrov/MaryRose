from fastapi.security import APIKeyHeader
from typing import Dict
from fastapi import Depends, HTTPException, status, Header

from api.meet_listener import MeetListenerBot
from config.config import INTERNAL_API_KEY, API_KEY_NAME, LOG_ACCESS_KEY

import logging

logger = logging.getLogger(__name__)

active_meetings: Dict[str, MeetListenerBot] = {}

# API ключи
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# Проверка ключа
async def get_api_key(api_key: str = Depends(api_key_header)):
    if api_key == INTERNAL_API_KEY:
        return api_key
    else:
        logger.warning("Failed API Key validation")
        raise HTTPException(status_code=403, detail="Could not validate credentials")

# --- Security Dependency for Log Access ---
async def verify_log_access_key(x_log_access_key: str | None = Header(None, alias="X-Log-Access-Key")):
    if not LOG_ACCESS_KEY:
        logger.error("LOG_ACCESS_KEY is not set on the server.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Log access is not configured on the server."
        )
    if x_log_access_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header 'X-Log-Access-Key' is missing."
        )
    if x_log_access_key != LOG_ACCESS_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid log access key."
        )
