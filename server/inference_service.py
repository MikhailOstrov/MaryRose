import asyncio
import logging
import numpy as np
import io
import time
import soundfile as sf
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, UploadFile, File

from config.load_models import load_asr_model
import sys

# Настройка логирования: принудительно пишем в stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)
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
    
    logger.info("Загрузка ASR модели...")
    try:
        asr_model = load_asr_model()
        logger.info(f"ASR модель успешно загружена: {type(asr_model)}")
    except Exception as e:
        logger.error(f"Критическая ошибка при загрузке ASR модели: {e}")

    yield
    
    logger.info("Остановка Inference Service...")
    asr_model = None
    te_model = None
    executor.shutdown(wait=True)

app = FastAPI(lifespan=lifespan, title="MaryRose Inference Service")

@app.get("/health")
async def health_check(response: Response):
    if asr_model is not None:
        return {"status": "ok", "model_loaded": True}
    
    response.status_code = 503
    return {"status": "error", "model_loaded": False}

def run_inference_sync(audio_float32: np.ndarray) -> str:
    """
    Синхронная функция инференса (Raw Audio), выполняемая в ThreadPoolExecutor.
    """
    if asr_model is None:
        logger.warning("Попытка инференса без загруженной модели.")
        return ""
    
    start_time = time.time()
    try:
        # Универсальный вызов метода распознавания
        text = ""
        try:
            segments, info = asr_model.transcribe(
                audio_float32,
                beam_size=1,
                temperature=0.0,
                best_of=1,
                language="ru",
                vad_filter=False,
                condition_on_previous_text=False,
                word_timestamps=False,
                without_timestamps=True)
            for segment in segments:
                text = segment.text.strip()
        except Exception as e:
            logger.error(f"Ошибка при вызове модели: {e}")
            return ""

        duration = time.time() - start_time
        msg = f"Inference time (stream): {duration:.3f}s. Text: {text[:50]}..."
        logger.info(msg)
        print(msg) # Дублируем в stdout для гарантии видимости
        return text
    except Exception as e:
        logger.error(f"Ошибка при инференсе: {e}", exc_info=True)
        print(f"ERROR INFERENCE: {e}")
        return ""

def run_file_inference_sync(file_obj) -> str:
    """
    Синхронная функция инференса для аудиофайлов с использованием Whisper.
    Возвращает объединённый текст транскрипции.
    """
    if asr_model is None:
        logger.error("ASR model is not loaded")
        return ""

    start_time = time.time()
    try:
        # Читаем аудио
        audio_data, sr = sf.read(file_obj)

        # Whisper ожидает mono float32 на 16 kHz
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=1)  # stereo → mono

        audio_data = audio_data.astype(np.float32)

        if sr != 16000:
            logger.warning(f"Sample rate {sr} → resampling to 16000 Hz required")

        segments, info = asr_model.transcribe(
            audio_data,
            beam_size=1,
            temperature=0.0,
            best_of=1)
        
        if hasattr(segments, '__iter__'): 
            text_parts = [segment.text.strip() for segment in segments if segment.text.strip()]
            text = " ".join(text_parts)
        else:
            text = str(segments).strip()

        text = text.strip()

        duration = time.time() - start_time
        logger.info(
            f"Inference time (file): {duration:.2f}s | "
            f"Text preview: {text[:70]!r}..."
        )

        return text

    except Exception as e:
        logger.exception("File inference failed")
        return ""

@app.post("/transcribe_file")
async def transcribe_file_endpoint(file: UploadFile = File(...)):
    """
    HTTP эндпоинт для транскрибации аудиофайлов.
    """
    content = await file.read()
    file_obj = io.BytesIO(content)
    logger.info(f"Получен файл для транскрибации, размер: {len(content)} байт")
    
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
            data = await websocket.receive_bytes()
            if not data:
                continue
            start_time = time.time()
            try:
                audio_float32 = np.frombuffer(data, dtype=np.float32)
            except Exception as e:
                logger.error(f"Ошибка конвертации аудио данных: {e}")
                continue

            duration = time.time() - start_time
            logger.info(f"Conversion time: {duration:.3f}s ----------")
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(executor, run_inference_sync, audio_float32)

            duration = time.time() - start_time
            logger.info(f"Inference time: {duration:.3f}s ----------")
            # Отправляем только если текст не пустой (опционально)
            if text:
                await websocket.send_text(text)
            
    except WebSocketDisconnect:
        logger.info(f"WS соединение закрыто: {websocket.client}")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в WS хендлере: {e}")
        try:
            await websocket.close(code=1011, reason=str(e))
        except:
            pass
