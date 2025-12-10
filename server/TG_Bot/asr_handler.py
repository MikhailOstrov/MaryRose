import asyncio
import io
import aiohttp
import logging
import time

# from config.load_models import asr_model # УБРАНО
logger = logging.getLogger(__name__)

TRANSCRIBE_SEMAPHORE = asyncio.Semaphore(6)
INFERENCE_URL = "http://localhost:8000/transcribe_file"

async def transcribe_audio_async(audio_bytes: bytes) -> str:
    """
    Отправляет аудиофайл в Inference Service для распознавания.
    """
    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData()
        # Передаем байты как файл 'audio'
        data.add_field('file', 
                       audio_bytes, 
                       filename='voice.ogg', 
                       content_type='audio/ogg') # Или 'application/octet-stream', whisper разберется

        try:
            start_ts = time.time()
            async with session.post(INFERENCE_URL, data=data) as response:
                latency = time.time() - start_ts
                if response.status == 200:
                    result = await response.json()
                    text = result.get("text", "")
                    logger.info(f"TG Bot Transcribe latency: {latency:.3f}s. Text: {text[:50]}...")
                    return text
                else:
                    logger.error(f"Inference Service error: {response.status} - {await response.text()}")
                    return ""
        except Exception as e:
            logger.error(f"Failed to connect to Inference Service: {e}")
            return ""
