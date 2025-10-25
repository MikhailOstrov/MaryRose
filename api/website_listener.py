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
import re

from config.config import STREAM_SAMPLE_RATE, MEET_AUDIO_CHUNKS_DIR, MEET_FRAME_DURATION_MS
from handlers.llm_handler import get_summary_response, get_title_response
from config.load_models import asr_model, te_model
from utils.backend_request import send_results_to_backend

logger = logging.getLogger(__name__)

class WebsiteListenerBot:
    
    def __init__(self, session_id: str, meeting_id: int):
        self.session_id = session_id
        self.meeting_id = meeting_id

        self.is_running = threading.Event()
        self.is_running.set()

        self.asr_model = asr_model
        self.te_model = te_model

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

    def split_audio_into_chunks(audio_path, chunk_duration=29, sample_rate=16000):
        try:
            # Читаем аудиофайл
            audio_data, sr = sf.read(audio_path)
            
            samples_per_chunk = chunk_duration * sample_rate
            total_samples = len(audio_data)
            
            # Создаем списки для хранения путей к чанкам и их длительностей
            chunk_paths = []
            chunk_durations = []  # Новый список для длительностей в секундах
            
            # Разделяем аудио на чанки
            for i, start_sample in enumerate(range(0, total_samples, samples_per_chunk)):
                end_sample = min(start_sample + samples_per_chunk, total_samples)
                chunk_data = audio_data[start_sample:end_sample]
                
                # Пропускаем пустые чанки
                if len(chunk_data) == 0:
                    continue
                
                # Создаем временный файл для чанка
                chunk_path = audio_path.parent / f"chunk_{i:04d}.wav"
                sf.write(chunk_path, chunk_data, sample_rate, subtype='PCM_16')
                chunk_paths.append(chunk_path)
                
                # Рассчитываем длительность чанка
                chunk_duration_sec = len(chunk_data) / sample_rate
                chunk_durations.append(chunk_duration_sec)
                
                print(f"Создан чанк {i+1}: {chunk_path} ({chunk_duration_sec:.2f} сек)")
            
            print(f"Всего создано {len(chunk_paths)} чанков")
            return chunk_paths, chunk_durations  # Возвращаем оба списка
            
        except Exception as e:
            print(f"Ошибка при разделении аудио: {e}")
            return [], []

    # Постобработка: транскрипция всего файла + summary + title
    def _perform_post_processing(self):
        threading.current_thread().name = f'PostProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Запускаю постобработку...")
        
        # Получаем чанки и их длительности
        chunk_paths, chunk_durations = self.split_audio_into_chunks(self.full_audio_path)
        
        if not chunk_paths:
            logger.error(f"[{self.meeting_id}] Нет чанков для обработки")
            return
        
        full_text_parts = []  # Список для частей full_text
        current_offset = 0.0  # Накопленное время начала текущего чанка
        
        try:
            for idx, chunk in enumerate(chunk_paths):
                if idx >= len(chunk_durations):
                    logger.warning(f"[{self.meeting_id}] Несоответствие длительностей для чанка {idx}")
                    break
                
                chunk_duration = chunk_durations[idx]
                logger.info(f"[{self.meeting_id}] Обрабатываю чанк {idx+1}: offset={current_offset:.2f}s, duration={chunk_duration:.2f}s")
                
                # Распознавание: возвращает только текст
                transcription = self.asr_model.recognize(chunk)
                transcription_te = te_model(transcription, lan='ru')
                # Абсолютное время для всего чанка
                absolute_start = current_offset
                absolute_end = current_offset + chunk_duration
                
                # Формируем строку для этого чанка
                start_str = self.format_time_hms(absolute_start)
                end_str = self.format_time_hms(absolute_end)
                full_text_parts.append(f"[{start_str} - {end_str}] {transcription_te.strip()}")
                
                # Обновляем offset для следующего чанка
                current_offset += chunk_duration
            
            # Собираем полный текст
            full_text = "\n".join(full_text_parts)
            logger.info(f"[{self.meeting_id}] Полный текст собран: {len(chunk_paths)} чанков")
            
            # Очищаем от временных меток для суммаризации
            cleaned_dialogue = re.sub(r"\[\d{2}:\d{2}:\d{2}\s*-\s*\d{2}:\d{2}:\d{2}\]\s*", "", full_text)
            
            # Суммаризация
            logger.info(f"[{self.meeting_id}] Создание summary...")
            summary_text = get_summary_response(cleaned_dialogue)
            
            # Заголовок
            logger.info(f"[{self.meeting_id}] Создание title...")
            title_text = get_title_response(cleaned_dialogue)
            
            # Отправляем результат
            send_results_to_backend(
                meeting_id=self.meeting_id,
                full_text=full_text,
                summary=summary_text or "",
                title=title_text or ""
            )
            
        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка постобработки: {e}", exc_info=True)
        finally:
            # Опционально: удаляем временные чанки
            for path in chunk_paths:
                try:
                    path.unlink()
                except:
                    pass
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")

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

        VAD_FRAME_SIZE = int(STREAM_SAMPLE_RATE * (MEET_FRAME_DURATION_MS / 1000) * 2)

        try:

            ffmpeg_command = [
                "ffmpeg", "-i", input_file_path,
                "-loglevel", "error",
                "-f", "s16le",
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
