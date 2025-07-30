# server.py
import asyncio
import shutil
import logging
import threading
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# Импортируем бизнес-логику и конфигурацию
import config
from api import diarization_handler, ollama_handler, utils, stt_handler, tts_handler, speaker_handler
# НОВЫЙ ИМПОРТ: наш бот для Google Meet
from api.meet_listener import MeetListenerBot

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Инициализация приложения и загрузка моделей ---
app = FastAPI(title="AI Meeting Bot Server")

# Словарь для отслеживания активных ботов
active_bots = {}

def load_models():
    """Загружает все ML-модели. Вызывается один раз при старте сервера."""
    logger.info("--- Loading all ML models for the server ---")
    config.ensure_dirs_exist()
    tts_handler.load_tts_model()
    stt_handler.load_asr_model()
    diarization_handler.load_diarizer_model()
    speaker_handler.load_speaker_model()
    logger.info("--- All models loaded successfully ---")

# ЗАГРУЖАЕМ МОДЕЛИ ПРИ СТАРТЕ ПРИЛОЖЕНИЯ
load_models()

# --- Безопасность: API ключ ---
API_KEY = 'key' 
API_KEY_NAME = "X-Internal-Api-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Depends(api_key_header)):
    if api_key == API_KEY:
        return api_key
    else:
        logger.warning("Failed API Key validation")
        raise HTTPException(status_code=403, detail="Could not validate credentials")

# --- Модели данных для запросов ---
class StartRequest(BaseModel):
    meeting_id: str
    meet_url: str

class StopRequest(BaseModel):
    meeting_id: str

# --- Эндпоинты API ---

@app.get("/health")
async def health_check():
    """Проверка, что сервер жив."""
    return {"status": "ok", "message": "Server is running and models are loaded."}

@app.get("/status/{meeting_id}")
async def get_status(meeting_id: str):
    """Проверяет статус бота по ID."""
    if meeting_id in active_bots:
        return {"status": "active", "meeting_id": meeting_id}
    else:
        return {"status": "inactive", "meeting_id": meeting_id}

# --- Управление ботом для Google Meet (новая логика) ---

def run_bot_thread(meeting_id: str, meet_url: str):
    """
    Функция-обертка для запуска бота в отдельном потоке.
    Обеспечивает удаление бота из словаря после завершения работы.
    """
    #it working
    bot = None
    try:
        logger.info(f"Запуск бота в потоке для встречи {meeting_id}")
        bot = MeetListenerBot(meeting_url=meet_url, meeting_id=meeting_id)
        active_bots[meeting_id] = bot
        bot.run() # Прямой вызов блокирующего метода

    except Exception as e:
        logger.error(f"Ошибка при запуске/работе бота для {meeting_id}: {e}", exc_info=True)
    finally:
        if meeting_id in active_bots:
            del active_bots[meeting_id]
            logger.info(f"Бот для встречи {meeting_id} завершил работу и удален из активных.")

@app.post("/api/v1/internal/start-processing", dependencies=[Depends(get_api_key)])
async def start_processing(request: StartRequest):
    """Запускает сессию бота для указанной встречи."""
    logger.info(f"Получен запрос на запуск бота для meeting_id: {request.meeting_id}")
    if request.meeting_id in active_bots:
        raise HTTPException(status_code=400, detail=f"Бот для встречи {request.meeting_id} уже запущен.")

    # Запускаем бота в отдельном фоновом потоке
    thread = threading.Thread(target=run_bot_thread, args=(request.meeting_id, request.meet_url))
    thread.daemon = True # Поток завершится, если основной процесс умрет
    thread.start()
    
    return {"status": "processing_started", "meeting_id": request.meeting_id}

@app.post("/api/v1/internal/stop-processing", dependencies=[Depends(get_api_key)])
async def stop_processing(request: StopRequest):
    """Останавливает сессию бота для указанной встречи."""
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

# --- Эндпоинт для офлайн-обработки (без изменений) ---

@app.post("/api/v1/internal/process-file-offline", dependencies=[Depends(get_api_key)])
async def process_file_offline(file: UploadFile = File(...)):
    """Принимает аудиофайл, диаризует, транскрибирует и суммирует его."""
    logger.info(f"Received file for offline processing: {file.filename}")
    
    upload_path = config.UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        wav_path = await asyncio.to_thread(utils.convert_to_standard_wav, upload_path)
        rttm_path = await asyncio.to_thread(diarization_handler.run_diarization, str(wav_path), str(config.UPLOADS_DIR))
        dialogue = await asyncio.to_thread(diarization_handler.process_rttm_and_transcribe, rttm_path, str(wav_path))
        summary = await asyncio.to_thread(ollama_handler.get_summary_response, dialogue)

        logger.info(f"Successfully processed file: {file.filename}")
        return {"status": "success", "full_transcript": dialogue, "summary": summary}
    except Exception as e:
        logger.error(f"Failed to process file {file.filename}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process file: {str(e)}")

# --- Команда для запуска сервера из терминала ---
# uvicorn server:app --host 0.0.0.0 --port 8001