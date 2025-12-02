import asyncio
import logging
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from faster_whisper import WhisperModel

from config.load_models import load_asr_model

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inference_service")

# Глобальные переменные
asr_model: WhisperModel | None = None
executor = ThreadPoolExecutor(max_workers=1)  # Один поток для доступа к модели на GPU, чтобы избежать коллизий

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager для загрузки и выгрузки модели.
    Загружает модель при старте и освобождает ресурсы при остановке.
    """
    global asr_model
    logger.info("Инициализация Inference Service...")
    
    # Загрузка модели Whisper
    # Это тяжелая операция, но выполняется один раз при старте контейнера/пода
    logger.info("Загрузка модели Whisper в память GPU...")
    try:
        # Используем функцию из load_models.py, которая теперь только загружает модель, без глобальной переменной
        asr_model = load_asr_model()
        logger.info("Модель Whisper успешно загружена и готова к работе.")
    except Exception as e:
        logger.error(f"Критическая ошибка при загрузке модели: {e}")
        # Если модель не загрузилась, сервис не должен работать корректно
        raise e
    
    yield
    
    logger.info("Остановка Inference Service...")
    asr_model = None
    executor.shutdown(wait=True)

app = FastAPI(lifespan=lifespan, title="MaryRose Inference Service")

@app.get("/health")
async def health_check(response: Response):
    """
    Проверка состояния сервиса.
    Возвращает 200 OK, если модель загружена.
    Возвращает 503 Service Unavailable, если модель не готова.
    """
    if asr_model is not None:
        return {"status": "ok", "model_loaded": True}
    
    response.status_code = 503
    return {"status": "error", "model_loaded": False}

def run_inference_sync(audio_float32: np.ndarray) -> str:
    """
    Синхронная функция инференса, выполняемая в ThreadPoolExecutor.
    """
    if asr_model is None:
        logger.warning("Попытка инференса без загруженной модели.")
        return ""
    
    try:
        # transcribe возвращает генератор сегментов
        # Параметры настроены под клиента audio_handler.py
        segments, info = asr_model.transcribe(
            audio_float32, 
            beam_size=1, 
            best_of=1,
            language="ru",
            vad_filter=False, 
            condition_on_previous_text=False
        )
        
        # Собираем текст из всех сегментов
        text = " ".join([segment.text for segment in segments]).strip()
        return text
    except Exception as e:
        logger.error(f"Ошибка при инференсе: {e}")
        return ""

@app.websocket("/transcribe")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket эндпоинт для транскрибации.
    Протокол:
    1. Клиент подключается.
    2. Клиент отправляет бинарные данные (int16 PCM 16kHz).
    3. Сервер возвращает текстовый ответ (JSON или просто текст? План говорит "text").
    """
    await websocket.accept()
    # logger.info(f"Новое WS соединение: {websocket.client}")
    
    try:
        while True:
            # Ожидание бинарных данных
            data = await websocket.receive_bytes()
            
            if not data:
                continue
                
            # Конвертация байтов float32 numpy array
            # Клиент шлет готовый float32, нормализованный в [-1, 1]
            try:
                audio_float32 = np.frombuffer(data, dtype=np.float32)
            except Exception as e:
                logger.error(f"Ошибка конвертации аудио данных: {e}")
                await websocket.send_text("") # Отправляем пустой ответ или ошибку?
                continue
            
            # Запуск инференса в отдельном потоке
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(executor, run_inference_sync, audio_float32)
            
            # Отправляем распознанный текст обратно клиенту
            # logger.info(f"Распознано: '{text}'")
            await websocket.send_text(text)
            
    except WebSocketDisconnect:
        # logger.info(f"WS соединение закрыто: {websocket.client}")
        pass
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в WS хендлере: {e}")
        try:
            await websocket.close(code=1011, reason=str(e))
        except:
            pass

