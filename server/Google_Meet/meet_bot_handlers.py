from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks  
from uuid import uuid4
import logging
import threading

from server.dependencies import get_api_key
from server.request_models import StartRequest, StopRequest, WebsiteSessionStartRequest
from server.Google_Meet.meet_bot_manager import launch_queue, active_bots
from api.session_store import session_to_meeting_map

logger = logging.getLogger(__name__)
router = APIRouter()

# Проверка сервера
@router.get("/health")
async def health_check():
    return {"status": "ok", "message": "Server is running and models are loaded."}

# Проверка бота по ID
@router.get("/status/{meeting_id}")
async def get_status(meeting_id: str):
    if meeting_id in active_bots:
        return {"status": "active", "meeting_id": meeting_id}
    return {"status": "inactive", "meeting_id": meeting_id}

# Запуск бота
@router.post("/api/v1/internal/start-processing", dependencies=[Depends(get_api_key)], status_code=202)
async def start_processing(request: StartRequest):
    """
    Принимает запрос на запуск бота и ставит его в очередь.
    Отвечает немедленно.
    """
    logger.info(f"[API] Получен запрос на запуск бота для meeting_id: {request.meeting_id}")
    
    if request.meeting_id in active_bots:
        raise HTTPException(status_code=409, detail=f"Бот для встречи {request.meeting_id} уже запущен.")

    # Просто добавляем "заявку" в очередь. Воркер ее подхватит.
    launch_queue.put((request.meeting_id, request.meet_url))
    
    logger.info(f"[API] Задача на запуск бота для {request.meeting_id} успешно поставлена в очередь.")
    
    return {"status": "processing_queued", "meeting_id": request.meeting_id}

# Остановка бота
@router.post("/api/v1/internal/stop-processing", dependencies=[Depends(get_api_key)])
async def stop_processing(request: StopRequest):
    logger.info(f"Получен запрос на остановку бота для meeting_id: {request.meeting_id}")
    bot = active_bots.get(request.meeting_id)
    
    if not bot:
        raise HTTPException(status_code=404, detail=f"Бот для встречи {request.meeting_id} не найден или уже остановлен.")
    
    try:
        bot.stop()
        return {"status": "stopping_initiated", "meeting_id": request.meeting_id}
    except Exception as e:
        logger.error(f"Ошибка при остановке бота {request.meeting_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Не удалось инициировать остановку бота: {str(e)}")

# Сессия с сайта
@router.post("/api/v1/internal/website/start-session", dependencies=[Depends(get_api_key)], tags=["Website Sessions"])
async def start_website_session(request: WebsiteSessionStartRequest):
    session_id = str(uuid4())
    session_to_meeting_map[session_id] = request.meeting_id
    logger.info(f"Создана новая сессия {session_id} для meeting_id: {request.meeting_id}")
    return {"status": "success", "session_id": session_id}
