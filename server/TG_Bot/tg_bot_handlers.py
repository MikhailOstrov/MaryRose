from fastapi import APIRouter, UploadFile, File, Depends
import logging

from server.dependencies import get_api_key
from server.request_models import TextPayload
from asr_handler import transcribe_audio_async, TRANSCRIBE_SEMAPHORE

router = APIRouter(prefix="/api/v1/internal", dependencies=[Depends(get_api_key)])

@router.post("/audio")
async def receive_audio(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()

    async with TRANSCRIBE_SEMAPHORE:
        text = await transcribe_audio_async(audio_bytes)

    logging.info(f"Транскрибация завершена: {text[:60]}...")
    return {"status": "ok", "text": text}


@router.post("/text")
async def receive_text(payload: TextPayload):
    logging.info(f"Текст получен: {payload.text}")
    return {"status": "ok", "text_length": len(payload.text)}
