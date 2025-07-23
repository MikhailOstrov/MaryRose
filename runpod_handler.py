import runpod
import asyncio
from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File
import shutil
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# Импортируем все необходимые модули из вашего проекта
import config
from api import bot_manager, diarization_handler, ollama_handler, utils, stt_handler, tts_handler, speaker_handler

# --- Глобальные переменные и инициализация ---
# Создаем экземпляр FastAPI, который будет обрабатывать HTTP запросы
app = FastAPI()

# --- Безопасность (адаптировано для RunPod) ---
# Ключ будет считываться из переменных окружения, которые вы зададите в RunPod UI
API_KEY = config.RUNPOD_API_KEY 
API_KEY_NAME = "X-Internal-Api-Key"

async def get_api_key(api_key: str = Depends(APIKeyHeader(name=API_KEY_NAME, auto_error=False))):
    if api_key == API_KEY:
        return api_key
    else:
        raise HTTPException(status_code=403, detail="Could not validate credentials")

# --- Модели данных (копируем из server.py) ---
class StartRequest(BaseModel):
    meeting_id: str
    meet_url: str

class StopRequest(BaseModel):
    meeting_id: str

# --- Логика загрузки моделей ---
def load_models():
    """Загружает все модели в память. Вызывается один раз при старте воркера."""
    print("--- Loading all models for RunPod worker ---")
    config.ensure_dirs_exist()
    tts_handler.load_tts_model()
    stt_handler.load_asr_model()
    diarization_handler.load_diarizer_model()
    speaker_handler.load_speaker_model()
    print("--- All models loaded successfully ---")

# Загружаем модели сразу при старте скрипта
load_models()

# --- Определение эндпоинтов FastAPI ---
# Копируем эндпоинты из вашего server.py, они будут работать под управлением RunPod
@app.post("/api/v1/internal/start-processing", dependencies=[Depends(get_api_key)])
async def start_processing(request: StartRequest):
    return bot_manager.start_bot_session(meeting_id=request.meeting_id, meet_url=request.meet_url)

@app.post("/api/v1/internal/stop-processing", dependencies=[Depends(get_api_key)])
async def stop_processing(request: StopRequest):
    return bot_manager.stop_bot_session(meeting_id=request.meeting_id)

@app.post("/api/v1/internal/process-file-offline", dependencies=[Depends(get_api_key)])
async def process_file_offline(file: UploadFile = File(...)):
    upload_path = config.UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    try:
        wav_path = await asyncio.to_thread(utils.convert_to_standard_wav, upload_path)
        rttm_path = await asyncio.to_thread(diarization_handler.run_diarization, str(wav_path), str(config.UPLOADS_DIR))
        dialogue = await asyncio.to_thread(diarization_handler.process_rttm_and_transcribe, rttm_path, str(wav_path))
        summary = await asyncio.to_thread(ollama_handler.get_summary_response, dialogue)
        return {"status": "success", "full_transcript": dialogue, "summary": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process file: {e}")

# --- Основной обработчик RunPod ---
async def handler(job):
    """
    Эта функция является точкой входа для каждого запроса к Serverless Endpoint.
    Она передает запрос нашему приложению FastAPI.
    """
    # Используем asgi_proxy для интеграции FastAPI с RunPod
    async for result in runpod.serverless.asgi_proxy(app, job):
        yield result

# --- Запуск обработчика ---
if __name__ == "__main__":
    print("Starting RunPod Serverless handler...")
    runpod.serverless.start({"handler": handler})

