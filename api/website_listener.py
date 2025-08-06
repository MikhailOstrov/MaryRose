import os
import queue
import threading
import logging
from datetime import datetime
from uuid import uuid4

import numpy as np
import torch
from scipy.io.wavfile import write
import requests

from config.config import (
    STREAM_SAMPLE_RATE,
    MEET_FRAME_DURATION_MS,
    MEET_AUDIO_CHUNKS_DIR,
    SUMMARY_OUTPUT_DIR,
)

from handlers.ollama_handler import get_mary_response, get_summary_response, get_title_response
from handlers.diarization_handler import run_diarization, process_rttm_and_transcribe
from api.utils import combine_audio_chunks
from config.load_models import vad_model, asr_model
from config.config import SILENCE_THRESHOLD_FRAMES

logger = logging.getLogger(__name__)

class WebsiteListenerBot:
    """
    Класс для обработки аудиопотока, получаемого с веб-сайта через WebSocket.
    Логика обработки полностью синхронизирована с MeetListenerBot.
    """
    def __init__(self, session_id: str, meeting_id: int):

        self.session_id = session_id
        self.meeting_id = meeting_id # Ключевой ID для отправки результата
        self.audio_queue = queue.Queue()
        self.is_running = threading.Event()
        self.is_running.set()
        self.vad = vad_model
        self.asr_model = asr_model

        # ПРАВИЛЬНЫЙ РАСЧЕТ: (частота * длительность_в_сек * 2 байта_на_сэмпл)
        self.frame_size = int(STREAM_SAMPLE_RATE * (MEET_FRAME_DURATION_MS / 1000) * 2)
        self.silent_frames_threshold = SILENCE_THRESHOLD_FRAMES

        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.session_id
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"[{self.session_id}] Аудиофрагменты сессии (meet_id: {self.meeting_id}) будут сохраняться в: '{self.output_dir}'")

        self.processor_thread = threading.Thread(target=self._process_audio_stream)
        self.processor_thread.daemon = True
        self.processor_thread.start()

    def feed_audio_chunk(self, chunk: bytes):
        if self.is_running.is_set():
            self.audio_queue.put(chunk)

    def _process_audio_stream(self):
        """
        Полностью идентичен _process_audio_stream из MeetListenerBot.
        Выполняет VAD, онлайн-транскрипцию и реагирует на триггер-слова.
        """
        threading.current_thread().name = f'VADProcessor-{self.session_id}'
        logger.info(f"[{self.session_id}] Процессор VAD для сайта запущен.")
        speech_buffer = []
        silent_frames_count = 0

        TRIGGER_WORD = "мэри"
        
        while self.is_running.is_set():
            try:
                # Получаем аудио-фрейм из очереди (ожидаем байты)
                audio_frame_bytes = self.audio_queue.get(timeout=1)
                
                # --- ИЗМЕНЕНИЕ 1: Новая логика VAD ---
                # Преобразуем байты в numpy-массив float32, как требует модель
                audio_np = np.frombuffer(audio_frame_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                
                # Преобразуем в тензор PyTorch
                audio_tensor = torch.from_numpy(audio_np)
                
                # Получаем вероятность речи от модели Silero
                speech_prob = self.vad_model(audio_tensor, self.sample_rate).item()
                is_speech = speech_prob > 0.1

                if is_speech:
                    # ИЗМЕНЕНИЕ: Добавляем в буфер numpy-массив, а не байты
                    speech_buffer.append(audio_np)
                    silent_frames_count = 0
                else:
                    silent_frames_count += 1

                # Если буфер речи не пуст и обнаружена достаточная пауза
                if speech_buffer and silent_frames_count > self.silent_frames_threshold:
                    # --- ИЗМЕНЕНИЕ 2: Объединение numpy-массивов ---
                    full_audio_np = np.concatenate(speech_buffer)
                    speech_buffer.clear()
                    silent_frames_count = 0

                    # Запускаем сохранение фрагмента в отдельном потоке (опционально, но полезно)
                    # Для этого нужно преобразовать numpy обратно в байты
                    full_speech_chunk_bytes = (full_audio_np * 32768.0).astype(np.int16).tobytes()
                    threading.Thread(target=self._save_chunk, args=(full_speech_chunk_bytes,)).start()

                    # Выполняем транскрибацию, передавая numpy-массив
                    segments, info = self.asr_model.transcribe(full_audio_np, beam_size=5)
                    transcription = " ".join([seg.text for seg in segments]).strip()
                    
                    if transcription:
                        logger.info(f"[{self.meeting_id}] Распознано: {transcription}")

                        # Приводим распознанный текст к нижнему регистру и убираем пробелы в начале
                        text_to_check = transcription.lstrip().lower()

                        # Проверяем, начинается ли фраза с триггерного слова
                        if text_to_check.startswith(TRIGGER_WORD):
                            logger.info(f"[{self.meeting_id}] Обнаружено слово-триггер в начале фразы.")
                            
                            # Убираем лишние символы с краев всей исходной фразы
                            command_text = transcription.strip(" ,.:")
                            
                            # Отправляем ВСЮ команду (включая триггер) и получаем ответ
                            if command_text:
                                logger.info(f"[{self.meeting_id}] Отправка команды: '{command_text}'")
                                response = get_mary_response(command_text)
                                logger.info(f"[{self.meeting_id}] Ответ от Мэри: {response}")

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}", exc_info=True)

    def _save_chunk(self, audio_bytes: bytes):
        try:
            filename = f'chunk_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid4().hex[:6]}.wav'
            file_path = self.output_dir / filename
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
            write(str(file_path), STREAM_SAMPLE_RATE, audio_np)
        except Exception as e:
            logger.error(f"[{self.session_id}] ❌ Ошибка при сохранении аудиофрагмента: {e}")

    def _perform_post_processing(self):
        threading.current_thread().name = f'PostProcessor-{self.session_id}'
        logger.info(f"[{self.session_id}] Начинаю постобработку для сессии с сайта...")

        try:
            combined_audio_filename = f"combined_website_session_{self.session_id}.wav"
            combined_audio_filepath = self.output_dir / combined_audio_filename

            combine_audio_chunks(
                output_dir=self.output_dir,
                stream_sample_rate=STREAM_SAMPLE_RATE,
                meeting_id=self.session_id,
                output_filename=combined_audio_filename
            )
            
            if not os.path.exists(combined_audio_filepath):
                return
            
            rttm_path = run_diarization(str(combined_audio_filepath), str(self.output_dir))
            
            dialogue_transcript = process_rttm_and_transcribe(rttm_path, str(combined_audio_filepath))

            import re
            pattern = r"\[speaker_\d+\]:\s*"
            cleaned_dialogue = re.sub(pattern, "", dialogue_transcript)

            summary_text = get_summary_response(cleaned_dialogue)
            
            title_text = get_title_response(cleaned_dialogue)
            
            self._send_results_to_backend(dialogue_transcript, summary_text, title_text)

        except Exception as e:
            logger.error(f"[{self.session_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.session_id}] Постобработка для сессии с сайта завершена.")

    def _send_results_to_backend(self, full_text: str, summary: str, title: str):
        try:
            payload = {"meeting_id": self.meeting_id, "full_text": full_text, "summary": summary, "title": title}
            headers = {"X-Internal-Api-Key": "key", "Content-Type": "application/json"}
            # Используем переменную окружения или дефолтный домен
            backend_url = os.getenv('MAIN_BACKEND_URL', 'https://puny-goats-smell.loca.lt')
            # backend_url = os.getenv('MAIN_BACKEND_URL', 'https://maryrose.by')
            url = f"{backend_url}/meetings/internal/result"
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            logger.info(f"[{self.session_id}] ✅ Результаты для meeting_id {self.meeting_id} успешно отправлены.")
        except Exception as e:
            logger.error(f"[{self.session_id}] ❌ Ошибка при отправке результатов на Main Backend: {e}")

    def stop(self):
        if not self.is_running.is_set():
            return
        
        self.is_running.clear()
        self.processor_thread.join()
        
        post_processing_thread = threading.Thread(target=self._perform_post_processing)
        post_processing_thread.daemon = False
        post_processing_thread.start()
        
        logger.info(f"[{self.session_id}] Сессия с сайта завершена, запущена финальная обработка.") 