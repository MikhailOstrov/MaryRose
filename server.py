# server.py
import asyncio
import shutil
import logging
import threading
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# Импортируем бизнес-логику и конфигурацию
from config.config import UPLOADS_DIR
from api import utils
from handlers import diarization_handler, ollama_handler, stt_handler, tts_handler
from api.meet_listener import MeetListenerBot
from api.local_stream_processor import LocalStreamProcessor

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Инициализация приложения и загрузка моделей ---
app = FastAPI(title="AI Meeting Bot Server")

# Словарь для отслеживания активных ботов
active_bots = {}

# Словарь для отслеживания активных локальных процессоров
active_local_processors = {}

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

async def verify_websocket_api_key(websocket: WebSocket):
    """Проверяет API ключ для WebSocket соединений."""
    api_key = websocket.headers.get(API_KEY_NAME)
    if api_key != API_KEY:
        logger.warning(f"WebSocket: Failed API Key validation. Provided: {api_key}")
        await websocket.close(code=4001, reason="Invalid API Key")
        return False
    return True

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
    
    upload_path = UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        wav_path = await asyncio.to_thread(utils.convert_to_standard_wav, upload_path)
        rttm_path = await asyncio.to_thread(diarization_handler.run_diarization, str(wav_path), str(UPLOADS_DIR))
        dialogue = await asyncio.to_thread(diarization_handler.process_rttm_and_transcribe, rttm_path, str(wav_path))
        summary = await asyncio.to_thread(ollama_handler.get_summary_response, dialogue)

        logger.info(f"Successfully processed file: {file.filename}")
        return {"status": "success", "full_transcript": dialogue, "summary": summary}
    except Exception as e:
        logger.error(f"Failed to process file {file.filename}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process file: {str(e)}")

# --- WebSocket эндпоинт для локальной обработки аудиопотока ---

@app.websocket("/ws/process-local-audio/{meeting_id}")
async def process_local_audio_websocket(websocket: WebSocket, meeting_id: str):
    """
    WebSocket эндпоинт для обработки локального аудиопотока в реальном времени.
    Использует LocalStreamProcessor для VAD, STT и wake-word detection.
    """
    processor = None
    processor_thread = None
    
    try:
        # 1. Проверка API ключа
        if not await verify_websocket_api_key(websocket):
            return
        
        # 2. Проверяем, что процессор для этой встречи еще не существует
        if meeting_id in active_local_processors:
            logger.warning(f"WebSocket: Процессор для встречи {meeting_id} уже активен")
            await websocket.close(code=4002, reason="Processor already exists")
            return
        
        # 3. Принимаем WebSocket соединение
        await websocket.accept()
        logger.info(f"🔌 WebSocket соединение установлено для локальной встречи {meeting_id}")
        
        # 4. Создаем и запускаем LocalStreamProcessor
        processor = LocalStreamProcessor(meeting_id)
        active_local_processors[meeting_id] = processor
        
        # Запускаем обработку в отдельном потоке
        processor_thread = threading.Thread(target=processor.run)
        processor_thread.daemon = True
        processor_thread.start()
        
        logger.info(f"🎤 Локальный процессор запущен для встречи {meeting_id}")
        
        # 5. Основной цикл получения и обработки аудио данных
        while True:
            try:
                # Получаем аудио данные от Main Backend
                audio_data = await websocket.receive_bytes()
                logger.debug(f"📤 Получен аудио чанк для встречи {meeting_id}: {len(audio_data)} байт")
                
                # Отправляем в процессор для обработки
                processor.process_websocket_audio(audio_data)
                
            except WebSocketDisconnect:
                logger.info(f"🔌 WebSocket отключился для встречи {meeting_id}")
                break
            except Exception as e:
                logger.error(f"❌ Ошибка при обработке аудио данных для встречи {meeting_id}: {e}")
                break
                
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка WebSocket для встречи {meeting_id}: {e}", exc_info=True)
        try:
            await websocket.close(code=4500, reason="Internal server error")
        except:
            pass
        
    finally:
        # Очистка ресурсов
        try:
            if processor:
                logger.info(f"🛑 Остановка процессора для встречи {meeting_id}")
                processor.stop()
            
            # Удаляем из активных процессоров
            if meeting_id in active_local_processors:
                del active_local_processors[meeting_id]
                logger.info(f"🗑️ Процессор для встречи {meeting_id} удален из активных")
                
        except Exception as e:
            logger.error(f"⚠️ Ошибка при очистке ресурсов для встречи {meeting_id}: {e}")

# --- Команда для запуска сервера из терминала ---
# uvicorn server:app --host 0.0.0.0 --port 8001