import logging
import threading
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from uuid import uuid4

from api.meet_listener import MeetListenerBot
from api import websocket_gateway
from api.session_store import session_to_meeting_map
from typing import Dict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
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

# Проверка ключа.
async def get_api_key(api_key: str = Depends(api_key_header)):
    if api_key == API_KEY:
        return api_key
    else:
        logger.warning("Failed API Key validation")
        raise HTTPException(status_code=403, detail="Could not validate credentials")

# Модели данных для запросов
class StartRequest(BaseModel):
    meeting_id: str
    meet_url: str

class StopRequest(BaseModel):
    meeting_id: str

class WebsiteSessionStartRequest(BaseModel):
    meeting_id: int

# Проверка сервера
@app.get("/health")
async def health_check():
    """Проверка, что сервер жив."""
    return {"status": "ok", "message": "Server is running and models are loaded."}

# Проверка бота по ID
@app.get("/status/{meeting_id}")
async def get_status(meeting_id: str):
    """Проверяет статус бота по ID."""
    if meeting_id in active_bots:
        return {"status": "active", "meeting_id": meeting_id}
    else:
        return {"status": "inactive", "meeting_id": meeting_id}
    
#  Функция-обертка для запуска бота в отдельном потоке
def run_bot_thread(meeting_id: str, meet_url: str):
    bot = None
    try:
        logger.info(f"Запуск бота в потоке для встречи {meeting_id}")
        bot = MeetListenerBot(meeting_url=meet_url, meeting_id=meeting_id)
        active_bots[meeting_id] = bot
        bot.run()

    except Exception as e:
        logger.error(f"Ошибка при запуске/работе бота для {meeting_id}: {e}", exc_info=True)
    finally:
        if meeting_id in active_bots:
            del active_bots[meeting_id]
            logger.info(f"Бот для встречи {meeting_id} завершил работу и удален из активных.")

# Запускает сессию бота для указанной встречи
@app.post("/api/v1/internal/start-processing", dependencies=[Depends(get_api_key)])
async def start_processing(request: StartRequest):
    logger.info(f"Получен запрос на запуск бота для meeting_id: {request.meeting_id}")
    if request.meeting_id in active_bots:
        raise HTTPException(status_code=400, detail=f"Бот для встречи {request.meeting_id} уже запущен.")

    thread = threading.Thread(target=run_bot_thread, args=(request.meeting_id, request.meet_url))
    thread.daemon = True # Поток завершится, если основной процесс умрет
    thread.start()
    
    return {"status": "processing_started", "meeting_id": request.meeting_id}

# Останавливает сессию бота для указанной встречи
@app.post("/api/v1/internal/stop-processing", dependencies=[Depends(get_api_key)])
async def stop_processing(request: StopRequest):
    logger.info(f"Получен запрос на остановку бота для meeting_id: {request.meeting_id}")
    bot = active_bots.get(request.meeting_id)
    
    if not bot:
        raise HTTPException(status_code=404, detail=f"Бот для встречи {request.meeting_id} не найден или уже остановлен.")
    
    try:
        bot.stop() # Этот метод инициирует остановку
        # active_bots[request.meeting_id] будет удален автоматически в потоке run_bot_thread
        return {"status": "stopping_initiated", "meeting_id": request.meeting_id}
    except Exception as e:
        logger.error(f"Ошибка при остановке бота {request.meeting_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Не удалось инициировать остановку бота: {str(e)}")

# Функция для инициации сессии с сайта
@app.post("/api/v1/internal/website/start-session", dependencies=[Depends(get_api_key)], tags=["Website Sessions"])
async def start_website_session(request: WebsiteSessionStartRequest):

    session_id = str(uuid4())
    session_to_meeting_map[session_id] = request.meeting_id
    logger.info(f"Создана новая сессия {session_id} для meeting_id: {request.meeting_id}")
    return {"status": "success", "session_id": session_id}

app.include_router(websocket_gateway.router, prefix="/ws")

# --- Команда для запуска сервера из терминала ---
# uvicorn server:app --host 0.0.0.0 --port 8001