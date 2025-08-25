from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from uuid import uuid4
import logging
import threading
import os

from server.dependencies import get_api_key
from server.request_models import StartRequest, StopRequest, WebsiteSessionStartRequest
from server.Google_Meet.meet_bot_manager import start_bot_process, stop_bot_process, get_bot_status
from api.session_store import session_to_meeting_map
from api.website_listener import WebsiteListenerBot
from config.config import MEET_AUDIO_CHUNKS_DIR


logger = logging.getLogger(__name__)
router = APIRouter()

# Проверка сервера
@router.get("/health")
async def health_check():
    return {"status": "ok", "message": "Server is running and models are loaded."}

# Проверка бота по ID
@router.get("/status/{meeting_id}")
async def get_status(meeting_id: str):
    """Проверяет статус бота по его ID."""
    status = get_bot_status(meeting_id)
    return {"status": status, "meeting_id": meeting_id}

# Запуск бота
@router.post("/api/v1/internal/start-processing", dependencies=[Depends(get_api_key)])
async def start_processing(request: StartRequest):
    """Запускает процесс обработки для новой встречи."""
    logger.info(f"Получен запрос на запуск процесса для meeting_id: {request.meeting_id}")
    
    if get_bot_status(request.meeting_id) == "active":
        raise HTTPException(status_code=400, detail=f"Бот для встречи {request.meeting_id} уже запущен.")

    success = start_bot_process(request.meeting_id, request.meet_url)
    
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось запустить процесс бота.")

    return {"status": "processing_started", "meeting_id": request.meeting_id}

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


@router.post("/api/v1/internal/audio/upload", dependencies=[Depends(get_api_key)])
async def upload_audio_file(
    meeting_id: str = Form(...),
    audio_file: UploadFile = File(...)
):
    """Принимает аудио файл от основного бэкенда и запускает обработку."""
    logger.info(f"Получен файл для обработки, meeting_id: {meeting_id}, filename: {audio_file.filename}")

    try:
        # Создаем директорию для временных файлов если не существует
        temp_dir = MEET_AUDIO_CHUNKS_DIR / f"upload_{meeting_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Сохраняем файл временно
        temp_file_path = temp_dir / f"{meeting_id}.webm"
        with open(temp_file_path, "wb") as temp_file:
            content = await audio_file.read()
            temp_file.write(content)

        logger.info(f"Файл сохранен временно: {temp_file_path}")

        # Создаем экземпляр WebsiteListenerBot для обработки
        # Используем meeting_id как session_id для простоты
        listener_bot = WebsiteListenerBot(
            session_id=f"upload_{meeting_id}",
            meeting_id=int(meeting_id)
        )

        # Запускаем обработку файла в отдельном потоке
        processing_thread = threading.Thread(
            target=listener_bot.process_audio_file,
            args=(str(temp_file_path),)
        )
        processing_thread.start()

        return {"status": "processing_started", "meeting_id": meeting_id}

    except Exception as e:
        logger.error(f"Ошибка при обработке файла для meeting_id {meeting_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось обработать файл: {str(e)}"
        )
