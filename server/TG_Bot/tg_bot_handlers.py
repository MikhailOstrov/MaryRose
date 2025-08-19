from fastapi import APIRouter, UploadFile, File, Depends, Form, HTTPException
import logging

from server.dependencies import get_api_key
from server.request_models import TextPayload
from server.TG_Bot.asr_handler import transcribe_audio_async, TRANSCRIBE_SEMAPHORE
from handlers.llm_handler import tg_bot_response
router = APIRouter(prefix="/api/v1/internal", dependencies=[Depends(get_api_key)])

@router.post("/audio")
async def receive_audio(chat_id: int = Form(...), audio: UploadFile = File(...)):
    try:
        audio_bytes = await audio.read()

        async with TRANSCRIBE_SEMAPHORE:

            transcription = await transcribe_audio_async(audio_bytes)
            text = await tg_bot_response(transcription)

        logging.info(f"[chat_id={chat_id}] Транскрибация завершена.")

        return {"status": "ok", "chat_id": chat_id, "text": text}

    except Exception as e:
        logging.exception(f"[chat_id={chat_id}] Ошибка при обработке аудио: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/text")
async def receive_text(payload: TextPayload):
    try:
        logging.info(f"[chat_id={payload.chat_id}] Текст получен.")

        text = await tg_bot_response(payload.text)

        return {"status": "ok", "chat_id": payload.chat_id, "text": text}

    except Exception as e:
        logging.exception(f"[chat_id={payload.chat_id}] Ошибка при обработке текста: {e}")
        raise HTTPException(status_code=500, detail=str(e))
