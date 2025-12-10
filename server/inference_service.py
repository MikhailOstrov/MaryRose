import asyncio
import logging
import numpy as np
import io
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, UploadFile, File
import soundfile as sf

from config.load_models import load_asr_model, load_te_model

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inference_service")

# Глобальные переменные
asr_model = None
te_model = None
executor = ThreadPoolExecutor(max_workers=1)  # Один поток для доступа к модели на GPU

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager для загрузки и выгрузки модели.
    """
    global asr_model, te_model
    logger.info("Инициализация Inference Service...")
    
    logger.info("Загрузка модели GigaAM (ONNX)...")
    try:
        asr_model = load_asr_model()
        if asr_model:
            logger.info("✅ Модель GigaAM успешно загружена.")
        else:
            logger.error("❌ Не удалось загрузить модель GigaAM.")
    except Exception as e:
        logger.error(f"Критическая ошибка при загрузке ASR модели: {e}")

    logger.info("Загрузка модели пунктуации (Silero TE)...")
    try:
        te_model = load_te_model()
        if te_model:
            logger.info("✅ Модель пунктуации успешно загружена.")
        else:
            logger.warning("⚠️ Не удалось загрузить модель пунктуации.")
    except Exception as e:
        logger.error(f"Ошибка при загрузке TE модели: {e}")
    
    yield
    
    logger.info("Остановка Inference Service...")
    asr_model = None
    te_model = None
    executor.shutdown(wait=True)

app = FastAPI(lifespan=lifespan, title="MaryRose Inference Service")

@app.get("/health")
async def health_check(response: Response):
    """
    Проверка состояния сервиса.
    """
    if asr_model is not None:
        return {"status": "ok", "model_loaded": True}
    
    response.status_code = 503
    return {"status": "error", "model_loaded": False}

def apply_punctuation(text: str) -> str:
    """Применяет модель восстановления пунктуации."""
    if not text or not te_model:
        return text
    try:
        # te_model ожидает (text, lan='ru')
        return te_model(text, lan='ru')
    except Exception as e:
        logger.error(f"TE error: {e}")
        return text

def run_inference_sync(audio_float32: np.ndarray) -> str:
    """
    Синхронная функция инференса (Raw Audio).
    """
    if asr_model is None:
        logger.warning("Попытка инференса без загруженной модели.")
        return ""
    
    start_time = time.time()
    try:
        # Инференс GigaAM
        text = ""
        # Пробуем разные варианты API
        if hasattr(asr_model, 'recognize'):
            text = asr_model.recognize(audio_float32)
        elif hasattr(asr_model, 'transcribe'): 
             res = asr_model.transcribe(audio_float32)
             if isinstance(res, tuple): segments = res[0]
             else: segments = res
             text = " ".join([getattr(s, 'text', str(s)) for s in segments])
        else:
             # Попытка вызвать как callable (model(audio))
            text = asr_model(audio_float32)

        if isinstance(text, list):
             text = " ".join([str(x) for x in text])

        text = str(text).strip()

        # Применяем пунктуацию
        if text:
            text = apply_punctuation(text)

        duration = time.time() - start_time
        logger.info(f"Inference time (stream): {duration:.3f}s. Text: {text[:50]}...")
        return text
    except Exception as e:
        logger.error(f"Ошибка при инференсе GigaAM: {e}", exc_info=True)
        return ""

def run_file_inference_sync(file_obj) -> str:
    """
    Синхронная функция инференса для файла.
    """
    if asr_model is None:
        return ""
    
    start_time = time.time()
    try:
        # GigaAM ONNX требует numpy array
        # Используем soundfile, он читает из file-like object (BytesIO)
        audio_data, sr = sf.read(file_obj)
        
        # Если стерео, усредняем до моно
        if len(audio_data.shape) > 1:
            audio_data = audio_data.mean(axis=1)
            
        audio_float32 = audio_data.astype(np.float32)

        # Вызываем инференс
        text = run_inference_sync(audio_float32)
        
        duration = time.time() - start_time
        logger.info(f"Inference time (file): {duration:.3f}s. Text: {text[:50]}...")
        return text
    except Exception as e:
        logger.error(f"File inference error: {e}", exc_info=True)
        return ""

@app.post("/transcribe_file")
async def transcribe_file_endpoint(file: UploadFile = File(...)):
    """
    HTTP эндпоинт для транскрибации аудиофайлов.
    """
    content = await file.read()
    file_obj = io.BytesIO(content)
    # logger.info(f"Получен файл для транскрибации, размер: {len(content)} байт")
    
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(executor, run_file_inference_sync, file_obj)
    return {"text": text}

@app.websocket("/transcribe")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket эндпоинт для потоковой транскрибации.
    """
    await websocket.accept()
    logger.info(f"Новое WS соединение: {websocket.client}")
    
    try:
        while True:
            # Ожидание бинарных данных
            data = await websocket.receive_bytes()
            
            if not data:
                continue
            
            try:
                # Клиент шлет float32 байты
                audio_float32 = np.frombuffer(data, dtype=np.float32)
            except Exception as e:
                logger.error(f"Ошибка конвертации аудио данных: {e}")
                continue
            
            # Запуск инференса в отдельном потоке
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(executor, run_inference_sync, audio_float32)
            
            # Отправляем распознанный текст обратно клиенту
            await websocket.send_text(text)
            
    except WebSocketDisconnect:
        logger.info(f"WS соединение закрыто: {websocket.client}")
        pass
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в WS хендлере: {e}")
        try:
            await websocket.close(code=1011, reason=str(e))
        except:
            pass
