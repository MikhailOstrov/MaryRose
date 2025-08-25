import asyncio
import logging
import subprocess
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Path
from typing import Dict

# Импортируем WebsiteListenerBot и словарь из server.py
from api.website_listener import WebsiteListenerBot
from api.session_store import session_to_meeting_map # Предполагаем, что он будет в server.py
from config.config import STREAM_SAMPLE_RATE, MEET_FRAME_DURATION_MS

logger = logging.getLogger(__name__)
router = APIRouter()

# Словарь для хранения активных экземпляров ботов
active_bots: Dict[str, WebsiteListenerBot] = {}

# ПРАВИЛЬНЫЙ РАСЧЕТ: (частота * длительность_в_сек * 2 байта_на_сэмпл)
VAD_FRAME_SIZE = int(STREAM_SAMPLE_RATE * (MEET_FRAME_DURATION_MS / 1000) * 2)

@router.websocket("/listen/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str = Path(...)
):
    meeting_id = session_to_meeting_map.get(session_id)

    if meeting_id is None:
        await websocket.close(code=1008, reason="Invalid session ID")
        return

    await websocket.accept()
    logger.info(f"[{session_id}] WebSocket соединение установлено для meeting_id: {meeting_id}.")

    bot = WebsiteListenerBot(session_id=session_id, meeting_id=meeting_id)
    active_bots[session_id] = bot
    
    ffmpeg_command = [
        "ffmpeg", "-i", "-", "-f", "s16le",
        "-ar", str(STREAM_SAMPLE_RATE), "-ac", "1", "-"
    ]

    try:
        ffmpeg_process = await asyncio.create_subprocess_exec(
            *ffmpeg_command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        async def writer():
            try:
                while True:
                    message = await websocket.receive()

                    # Обрабатываем текстовые сообщения (служебные)
                    if "text" in message:
                        text_message = message['text']
                        logger.info(f"[{session_id}] Получено текстовое сообщение: {text_message}")
                        
                        # Если это heartbeat от клиента, отправляем ответ!
                        if text_message == "heartbeat":
                            await websocket.send_text("heartbeat_ack") # ack = Acknowledgment (подтверждение)
                            logger.info(f"[{session_id}] Отправлен ответ на heartbeat.")
                        
                        # Продолжаем слушать дальше, не передаем текст в FFmpeg
                        continue

                    # Обрабатываем бинарные данные (аудио)
                    if "bytes" in message:
                        audio_chunk = message["bytes"]
                        if ffmpeg_process.stdin:
                            ffmpeg_process.stdin.write(audio_chunk)
                            await ffmpeg_process.stdin.drain()
                    else:
                        logger.warning(f"[{session_id}] Получено неизвестное сообщение: {message}")

            except WebSocketDisconnect:
                logger.info(f"[{session_id}] Клиент отключился.")
            finally:
                if ffmpeg_process.stdin:
                    ffmpeg_process.stdin.close()

        async def reader():
            try:
                while True:
                    pcm_chunk = await ffmpeg_process.stdout.read(VAD_FRAME_SIZE)
                    if not pcm_chunk:
                        break
                    
                    if len(pcm_chunk) < VAD_FRAME_SIZE:
                        pcm_chunk += b'\x00' * (VAD_FRAME_SIZE - len(pcm_chunk))
                    bot.feed_audio_chunk(pcm_chunk)
            except Exception as e:
                logger.error(f"[{session_id}] Ошибка при чтении из FFmpeg: {e}")

        writer_task = asyncio.create_task(writer())
        reader_task = asyncio.create_task(reader())
        await asyncio.gather(writer_task, reader_task)

    except Exception as e:
        logger.error(f"[{session_id}] Ошибка в WebSocket-эндпоинте: {e}", exc_info=True)

    finally:
        logger.info(f"[{session_id}] Завершение сессии...")
        if session_id in session_to_meeting_map:
            del session_to_meeting_map[session_id]
        
        bot.stop()
        
        if session_id in active_bots:
            del active_bots[session_id]

        if 'ffmpeg_process' in locals() and ffmpeg_process.returncode is None:
            ffmpeg_process.terminate()
            await ffmpeg_process.wait()
        
        logger.info(f"[{session_id}] Ресурсы для сессии очищены.") 