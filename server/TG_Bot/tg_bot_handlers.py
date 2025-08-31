from fastapi import APIRouter, UploadFile, File, Depends, Form, HTTPException
import logging

from server.dependencies import get_api_key
from server.TG_Bot.asr_handler import transcribe_audio_async, TRANSCRIBE_SEMAPHORE

router = APIRouter(prefix="/api/v1/internal", dependencies=[Depends(get_api_key)])

@router.post("/audio")
async def receive_audio(chat_id: int = Form(...), audio: UploadFile = File(...)):
    try:
        audio_bytes = await audio.read()

        async with TRANSCRIBE_SEMAPHORE:

            transcription = await transcribe_audio_async(audio_bytes)

        logging.info(f"[chat_id={chat_id}] Транскрибация завершена.")

        return {"status": "ok", "chat_id": chat_id, "text": transcription}

    except Exception as e:
        logging.exception(f"[chat_id={chat_id}] Ошибка при обработке аудио: {e}")
        raise HTTPException(status_code=500, detail=str(e))
