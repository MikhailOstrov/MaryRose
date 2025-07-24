# server.py (новая, объединенная версия)

import asyncio
import shutil
import logging
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# Импортируем всю бизнес-логику и конфигурацию
import config
from api import bot_manager, diarization_handler, ollama_handler, utils, stt_handler, tts_handler, speaker_handler

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Инициализация приложения и загрузка моделей ---
app = FastAPI(title="AI Meeting Bot Server")

def load_models():
    """Загружает все модели в память. Вызывается один раз при старте сервера."""
    logger.info("--- Loading all ML models for the server ---")
    config.ensure_dirs_exist()
    tts_handler.load_tts_model()
    stt_handler.load_asr_model()
    diarization_handler.load_diarizer_model()
    speaker_handler.load_speaker_model()
    logger.info("--- All models loaded successfully ---")

# ЗАГРУЖАЕМ МОДЕЛИ ПРИ СТАРТЕ ПРИЛОЖЕНИЯ
load_models()

# --- Безопасность: API ключ из переменных окружения ---
# Вы можете задать этот ключ в docker-compose.yml или через -e флаг в docker run
# Например: docker run -e INTERNAL_API_KEY="your-secret-key" ...
API_KEY = 'key' # Предполагается, что вы добавите это в config.py
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
    """Простой эндпоинт для проверки, что сервер жив."""
    return {"status": "ok", "message": "Server is running and models are loaded."}

@app.post("/api/v1/internal/start-processing", dependencies=[Depends(get_api_key)])
async def start_processing(request: StartRequest):
    """Запускает сессию бота для указанной встречи."""
    logger.info(f"Received request to start bot for meeting_id: {request.meeting_id}")
    return bot_manager.start_bot_session(meeting_id=request.meeting_id, meet_url=request.meet_url)

@app.post("/api/v1/internal/stop-processing", dependencies=[Depends(get_api_key)])
async def stop_processing(request: StopRequest):
    """Останавливает сессию бота для указанной встречи."""
    logger.info(f"Received request to stop bot for meeting_id: {request.meeting_id}")
    return bot_manager.stop_bot_session(meeting_id=request.meeting_id)

@app.post("/api/v1/internal/process-file-offline", dependencies=[Depends(get_api_key)])
async def process_file_offline(file: UploadFile = File(...)):
    """Принимает аудиофайл, диаризует, транскрибирует и суммирует его."""
    logger.info(f"Received file for offline processing: {file.filename}")
    
    upload_path = config.UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        # Запускаем тяжелые CPU/GPU задачи в отдельном потоке, чтобы не блокировать event loop FastAPI
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