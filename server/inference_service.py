import asyncio
import logging
import numpy as np
import io
import time
import soundfile as sf
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, UploadFile, File

from config.load_models import load_asr_model, load_te_model
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
    
    logger.info("Загрузка ASR модели (GigaAM-v2-CTC ONNX)...")
    try:
        asr_model = load_asr_model()
        logger.info(f"ASR модель успешно загружена: {type(asr_model)}")
    except Exception as e:
        logger.error(f"Критическая ошибка при загрузке ASR модели: {e}")
        # raise e # Можно не ронять сервис, если упала только одна модель, но лучше упасть

    logger.info("Загрузка TE модели (Silero)...")
    try:
        te_model = load_te_model()
        logger.info("TE модель успешно загружена.")
    except Exception as e:
        logger.error(f"Ошибка при загрузке TE модели (пунктуация будет недоступна): {e}")

    yield
    
    logger.info("Остановка Inference Service...")
    asr_model = None
    te_model = None
    executor.shutdown(wait=True)

app = FastAPI(lifespan=lifespan, title="MaryRose Inference Service")

@app.get("/health")
async def health_check(response: Response):
    if asr_model is not None:
        return {"status": "ok", "model_loaded": True, "te_loaded": te_model is not None}
    
    response.status_code = 503
    return {"status": "error", "model_loaded": False}

def apply_punctuation(text: str) -> str:
    """Применяет TE модель к тексту, если она загружена."""
    if not text or not te_model:
        return text
    
    try:
        # Silero TE принимает текст и lan='ru'
        # Возвращает строку с пунктуацией
        return te_model(text, lan='ru')
    except Exception as e:
        logger.error(f"Ошибка при расстановке пунктуации: {e}")
        return text

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
            if hasattr(asr_model, "recognize"):
                # Пробуем передать beam_size=1 для Greedy Decoding (меньше нагрузка на CPU)
                try:
                    text = asr_model.recognize(audio_float32, beam_size=1)
                except TypeError:
                    # Если аргумент не поддерживается, вызываем без него
                    text = asr_model.recognize(audio_float32)
            elif hasattr(asr_model, "transcribe"):
                 text = asr_model.transcribe(audio_float32)
            else:
                 # Fallback: вызов как callable
                 text = asr_model(audio_float32)
        except Exception as e:
            logger.error(f"Ошибка при вызове модели: {e}")
            return ""

        # Обработка результата (если вернулся список сегментов)
        if isinstance(text, list):
            if text and hasattr(text[0], 'text'):
                text = " ".join([t.text for t in text])
            else:
                text = " ".join(map(str, text))
        
        text = str(text).strip()
        
        # === Применение TE (Пунктуация) ===
        if text:
            text = apply_punctuation(text)

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
    Синхронная функция инференса для файлов.
    """
    if asr_model is None:
        return ""
    
    start_time = time.time()
    try:
        audio_data, sr = sf.read(file_obj)
        
        if sr != 16000:
            logger.warning(f"Внимание: Sample rate {sr}, ожидается 16000.")
            
        if hasattr(asr_model, "recognize"):
             text = asr_model.recognize(audio_data)
        elif hasattr(asr_model, "transcribe"):
             text = asr_model.transcribe(audio_data)
        else:
             text = asr_model(audio_data)

        if isinstance(text, list):
            if text and hasattr(text[0], 'text'):
                text = " ".join([t.text for t in text])
            else:
                text = " ".join(map(str, text))
                
        text = str(text).strip()
        
        # === Применение TE (Пунктуация) ===
        if text:
            text = apply_punctuation(text)
        
        duration = time.time() - start_time
        logger.info(f"Inference time (file): {duration:.3f}s. Text: {text[:50]}...")
        return text
    except Exception as e:
        logger.error(f"File inference error: {e}")
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
