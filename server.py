# server.py
import uvicorn
from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
import asyncio
import shutil

import config
from api import bot_manager, diarization_handler, ollama_handler, utils

# --- Безопасность ---
API_KEY = "ваш_секретный_ключ" # Замените на реальный ключ
API_KEY_NAME = "X-Internal-Api-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Depends(api_key_header)):
    if api_key == API_KEY:
        return api_key
    else:
        raise HTTPException(status_code=403, detail="Could not validate credentials")

# --- Модели данных ---
class StartRequest(BaseModel):
    meeting_id: str
    meet_url: str

class StopRequest(BaseModel):
    meeting_id: str

# --- Инициализация FastAPI ---
app = FastAPI(title="Meet AI Assistant Backend")

@app.on_event("startup")
async def startup_event():
    print("Server starting up...")
    config.ensure_dirs_exist()
    # Загружаем модели в фоновом потоке, чтобы не блокировать запуск
    # В реальном приложении лучше использовать download_models.py заранее
    # asyncio.create_task(asyncio.to_thread(handlers.load_all_models))

# --- API Эндпоинты для бота ---
@app.post("/api/v1/internal/start-processing", dependencies=[Depends(get_api_key)])
async def start_processing(request: StartRequest):
    """Запускает бота для присоединения к встрече Google Meet."""
    print(f"Received start request for meeting: {request.meeting_id}")
    result = bot_manager.start_bot_session(
        meeting_id=request.meeting_id,
        meet_url=request.meet_url
    )
    return result

@app.post("/api/v1/internal/stop-processing", dependencies=[Depends(get_api_key)])
async def stop_processing(request: StopRequest):
    """Останавливает бота, записывающего встречу."""
    print(f"Received stop request for meeting: {request.meeting_id}")
    result = bot_manager.stop_bot_session(meeting_id=request.meeting_id)
    return result

# --- API Эндпоинт для офлайн-обработки файла ---
@app.post("/api/v1/internal/process-file-offline", dependencies=[Depends(get_api_key)])
async def process_file_offline(file: UploadFile = File(...)):
    """
    Принимает аудиофайл, конвертирует, диаризирует, транскрибирует и резюмирует.
    """
    upload_path = config.UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # Шаг 1: Конвертация
        wav_path = await asyncio.to_thread(utils.convert_to_standard_wav, upload_path)

        # Шаг 2: Диа-ризация
        rttm_path = await asyncio.to_thread(diarization_handler.run_diarization, str(wav_path), str(config.UPLOADS_DIR))

        # Шаг 3: Нарезка и транскрибация
        dialogue = await asyncio.to_thread(diarization_handler.process_rttm_and_transcribe, rttm_path, str(wav_path))

        # Шаг 4: Резюмирование
        summary = await asyncio.to_thread(ollama_handler.get_summary_response, dialogue)

        return {
            "status": "success",
            "full_transcript": dialogue,
            "summary": summary
        }
    except Exception as e:
        print(f"Error processing offline file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process file: {e}")


# --- Запуск сервера ---
if __name__ == "__main__":
    print("Starting Uvicorn server for Meet Bot Manager...")
    # Убедитесь, что Ollama и все модели загружены
    uvicorn.run(app, host="0.0.0.0", port=8000)
