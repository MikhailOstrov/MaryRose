from fastapi import APIRouter, UploadFile, File, Depends, Form, HTTPException
import logging
import soundfile as sf
import librosa
import tempfile
import os
import io

from server.dependencies import get_api_key
from server.TG_Bot.asr_handler import transcribe_audio_async, TRANSCRIBE_SEMAPHORE

router = APIRouter(prefix="/api/v1/internal", dependencies=[Depends(get_api_key)])

@router.post("/audio")
async def receive_audio(chat_id: int = Form(...), audio: UploadFile = File(...)):
    try:
        audio_bytes = await audio.read()

        async with TRANSCRIBE_SEMAPHORE:
            # Создаем временный файл и конвертируем в моно с нужными параметрами
            with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_audio:
                temp_audio_path = temp_audio.name

            try:
                
                audio_data, original_sr = sf.read(io.BytesIO(audio_bytes))
                
                if len(audio_data.shape) > 1:
                    audio_data = audio_data.mean(axis=1)
                
                target_sr = 16000
                logging.info(original_sr)
                if original_sr != target_sr:
                    audio_data = librosa.resample(audio_data, orig_sr=original_sr, target_sr=target_sr)
                
                # Сохраняем в моно-режиме с нужными параметрами
                sf.write(
                    temp_audio_path,
                    audio_data,
                    target_sr,
                    subtype="PCM_16",
                    format='WAV'
                )
                
                transcription = await transcribe_audio_async(temp_audio_path)

            finally:
                # Очищаем временный файл
                try:
                    os.unlink(temp_audio_path)
                except:
                    pass

        logging.info(f"[chat_id={chat_id}] Транскрибация завершена.")
        logging.info(transcription)
        return {"status": "ok", "chat_id": chat_id, "text": transcription}

    except Exception as e:
        logging.exception(f"[chat_id={chat_id}] Ошибка при обработке аудио: {e}")
        raise HTTPException(status_code=500, detail=str(e))
