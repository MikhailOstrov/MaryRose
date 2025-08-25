import os
import threading
import logging
import asyncio
import subprocess
from datetime import datetime
from uuid import uuid4

import numpy as np
import soundfile as sf
import requests

from config.config import STREAM_SAMPLE_RATE, MEET_AUDIO_CHUNKS_DIR, MEET_FRAME_DURATION_MS
from handlers.llm_handler import get_summary_response, get_title_response
from config.load_models import asr_model

logger = logging.getLogger(__name__)

class WebsiteListenerBot:
    
    def __init__(self, session_id: str, meeting_id: int):
        self.session_id = session_id
        self.meeting_id = meeting_id

        self.is_running = threading.Event()
        self.is_running.set()

        self.asr_model = asr_model  # Whisper

        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.session_id
        os.makedirs(self.output_dir, exist_ok=True)

        # Файл, куда будем писать всё аудио
        self.full_audio_path = self.output_dir / f"meeting_{self.meeting_id}.wav"
        self.audio_file = sf.SoundFile(
            self.full_audio_path,
            mode="w",
            samplerate=STREAM_SAMPLE_RATE,
            channels=1,
            subtype="PCM_16"
        )

        logger.info(
            f"[{self.session_id}] Запись аудио сессии (meeting_id={self.meeting_id}) "
            f"в файл: {self.full_audio_path}"
        )

    # Принимаем аудиоданные напрямую в файл
    def feed_audio_chunk(self, chunk: bytes):
        if self.is_running.is_set():
            try:
                audio_np = np.frombuffer(chunk, dtype=np.int16)
                self.audio_file.write(audio_np)
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка при записи аудио: {e}")

    # Постобработка: транскрипция всего файла + summary + title
    def _perform_post_processing(self):
        threading.current_thread().name = f'PostProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Запускаю постобработку...")

        try:
            # Запускаем ASR на полном файле
            segments, _ = self.asr_model.transcribe(
                str(self.full_audio_path),
                beam_size=3, best_of=3,
                condition_on_previous_text=False,
                vad_filter=False,
                language="ru"
            )

            full_text = "\n".join(
                f"[{self.format_time_hms(seg.start)} - {self.format_time_hms(seg.end)}] {seg.text.strip()}"
                for seg in segments
            )

            import re
            cleaned_dialogue = re.sub(r"\[\d{2}:\d{2}:\d{2}\s*-\s*\d{2}:\d{2}:\d{2}\]\s*", "", full_text)

            # Суммаризация
            logger.info(f"[{self.meeting_id}] Создание summary...")
            summary_text = get_summary_response(cleaned_dialogue)

            # Заголовок
            logger.info(f"[{self.meeting_id}] Создание title...")
            title_text = get_title_response(cleaned_dialogue)

            # Отправляем результат
            self._send_results_to_backend(full_text, summary_text, title_text)

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка постобработки: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")

    # Отправка результатов на backend
    def _send_results_to_backend(self, full_text: str, summary: str, title: str):
        try:
            meeting_id_int = int(self.meeting_id) if isinstance(self.meeting_id, str) else self.meeting_id

            payload = {
                "meeting_id": meeting_id_int,
                "full_text": full_text,
                "summary": summary,
                "title": title
            }
            headers = {
                "X-Internal-Api-Key": "key",
                "Content-Type": "application/json"
            }
            backend_url = os.getenv('MAIN_BACKEND_URL', 'https://maryrose.by')
            url = f"{backend_url}/meetings/internal/result"

            logger.info(f"[{self.meeting_id}] Отправка результатов на backend...")
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            logger.info(f"[{self.meeting_id}] ✅ Результаты отправлены успешно")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при отправке на backend: {e}")

    def format_time_hms(self, seconds: float) -> str:
        """Перевод секунд в формат HH:MM:SS"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    # Завершение записи и запуск постобработки
    def stop(self):
        if not self.is_running.is_set():
            return

        logger.info(f"[{self.session_id}] Завершаем сессию...")
        self.is_running.clear()

        try:
            self.audio_file.close()
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при закрытии файла: {e}")

        post_processing_thread = threading.Thread(target=self._perform_post_processing)
        post_processing_thread.start()

        logger.info(f"[{self.session_id}] Сессия завершена, постобработка запущена.")

    # Новый метод для обработки готового аудио файла
    def process_audio_file(self, input_file_path: str):
        """Обрабатывает готовый аудио файл (.webm) используя ту же логику что и вебсокет."""
        threading.current_thread().name = f'AudioProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Запускаю обработку файла: {input_file_path}")

        # Размер фрейма для чтения PCM данных (как в websocket_gateway.py)
        VAD_FRAME_SIZE = int(STREAM_SAMPLE_RATE * (MEET_FRAME_DURATION_MS / 1000) * 2)

        try:
            # Запускаем FFmpeg для конвертации файла в PCM поток (как в websocket_gateway.py)
            ffmpeg_command = [
                "ffmpeg", "-i", input_file_path, "-f", "s16le",
                "-ar", str(STREAM_SAMPLE_RATE), "-ac", "1", "-"
            ]

            logger.info(f"[{self.meeting_id}] Запуск FFmpeg: {' '.join(ffmpeg_command)}")

            ffmpeg_process = subprocess.Popen(
                ffmpeg_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            try:
                # Читаем PCM данные из stdout и передаем в bot (как в websocket_gateway.py)
                while True:
                    pcm_chunk = ffmpeg_process.stdout.read(VAD_FRAME_SIZE)
                    if not pcm_chunk:
                        break

                    if len(pcm_chunk) < VAD_FRAME_SIZE:
                        pcm_chunk += b'\x00' * (VAD_FRAME_SIZE - len(pcm_chunk))

                    self.feed_audio_chunk(pcm_chunk)

                logger.info(f"[{self.meeting_id}] Конвертация завершена, начинаем постобработку")

                # Останавливаем бота для запуска постобработки
                self.stop()

                # Очищаем входной файл
                try:
                    os.remove(input_file_path)
                    logger.info(f"[{self.meeting_id}] Удален входной файл: {input_file_path}")
                except Exception as e:
                    logger.warning(f"[{self.meeting_id}] Не удалось удалить входной файл: {e}")

            finally:
                # Останавливаем FFmpeg процесс
                ffmpeg_process.terminate()
                ffmpeg_process.wait()

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка обработки файла: {e}", exc_info=True)
