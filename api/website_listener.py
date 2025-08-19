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
import soundfile as sf

from config.config import (
    STREAM_SAMPLE_RATE,
    MEET_FRAME_DURATION_MS,
    MEET_AUDIO_CHUNKS_DIR,
    SUMMARY_OUTPUT_DIR,
    SILENCE_THRESHOLD_FRAMES,
)

from handlers.llm_handler import get_mary_response, get_summary_response, get_title_response
from handlers.diarization_handler import run_diarization, process_rttm_and_transcribe
from api.utils import combine_audio_chunks
from config.load_models import create_new_vad_model, asr_model

logger = logging.getLogger(__name__)

class WebsiteListenerBot:

     # Определение атрибутов класса
    def __init__(self, session_id: str, meeting_id: int):

        self.session_id = session_id # ID для отслеживания сессии
        self.meeting_id = meeting_id # ID для отслеживания сессии
        self.audio_queue = queue.Queue() # Для аудиопотока

        self.is_running = threading.Event()
        self.is_running.set()

        self.vad = create_new_vad_model()
        self.asr_model = asr_model # Whisper (from config.load_models import asr_model)

        self.frame_size = int(STREAM_SAMPLE_RATE * MEET_FRAME_DURATION_MS / 1000) # Для VAD-модели (длительность чанка)
        self.silent_frames_threshold = SILENCE_THRESHOLD_FRAMES # Пауза в речи в сек.

        self.global_offset = 0.0
        self.all_segments = []

        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.session_id
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"[{self.session_id}] Аудиофрагменты сессии (meet_id: {self.meeting_id}) будут сохраняться в: '{self.output_dir}'")

        self.processor_thread = threading.Thread(target=self._process_audio_stream)
        self.processor_thread.daemon = True
        self.processor_thread.start()

    # Проверка на чанк
    def feed_audio_chunk(self, chunk: bytes):
        if self.is_running.is_set():
            self.audio_queue.put(chunk)

    # Обработка аудиопотока -- транскрибация -- ответ (если обнаружен триггер)
    def _process_audio_stream(self):
        threading.current_thread().name = f'VADProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Процессор VAD запущен с моделью Silero.")

        vad_buffer = None
        VAD_CHUNK_SIZE = 512
        speech_buffer_for_asr = []
        is_speaking = False
        silent_frames_after_speech = 0

        while self.is_running.is_set():
            try:

                audio_frame_bytes = self.audio_queue.get(timeout=1)
                if not audio_frame_bytes:
                    continue

                audio_np = np.frombuffer(audio_frame_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                new_audio_tensor = torch.from_numpy(audio_np)

                if vad_buffer is None:
                    vad_buffer = new_audio_tensor
                else:
                    vad_buffer = torch.cat([vad_buffer, new_audio_tensor])

                while vad_buffer is not None and vad_buffer.shape[0] >= VAD_CHUNK_SIZE:
                    
                    chunk_to_process = vad_buffer[:VAD_CHUNK_SIZE]
                    vad_buffer = vad_buffer[VAD_CHUNK_SIZE:]
                    speech_prob = self.vad(chunk_to_process, STREAM_SAMPLE_RATE).item()
                    
                    if speech_prob > 0.3:
                        if not is_speaking:
                            logger.info(f"[{self.meeting_id}] Обнаружено начало речи.")
                            is_speaking = True
                        
                        speech_buffer_for_asr.append(chunk_to_process.numpy())
                        silent_frames_after_speech = 0
                    else:
                        if is_speaking:
                            
                            silent_frames_after_speech += 1
                            
                            if silent_frames_after_speech > self.silent_frames_threshold:
                                logger.info(f"[{self.meeting_id}] Обнаружен конец фразы.")
                                is_speaking = False
                                silent_frames_after_speech = 0
                                
                                if speech_buffer_for_asr:
                                    full_audio_np = np.concatenate(speech_buffer_for_asr)
                                    speech_buffer_for_asr = []
                                    
                                    self._save_chunk(full_audio_np)

                                    segments, _ = self.asr_model.transcribe(full_audio_np, beam_size=1, best_of=1, condition_on_previous_text=False, vad_filter=False, language="ru")

                                    transcription = "".join([seg.text for seg in segments]).strip()

                                    segments_l = list(segments)

                                    for seg in segments_l:
                                        segment_data = {
                                            "start": round(self.global_offset + seg.start, 2),
                                            "end": round(self.global_offset + seg.end, 2),
                                            "text": seg.text.strip()
                                        }
                                        self.all_segments.append(segment_data)
                                        print("Добавлен сегмент:", segment_data)

                                    chunk_duration = len(full_audio_np) / 16000.0
                                    self.global_offset += chunk_duration

                                    print(f"Распознано: {transcription}")
                
            except queue.Empty:
                if is_speaking and speech_buffer_for_asr:
                    logger.info(f"[{self.meeting_id}] Тайм-аут, обрабатываем оставшуюся речь.")
                    is_speaking = False
                continue
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}", exc_info=True)

    # Постобработка: объединение аудиочанков -- запуск диаризации и объединение с транскрибацией -- суммаризация -- генерация заголовка -- отправка результатов на внешний сервер
    def _perform_post_processing(self):

        threading.current_thread().name = f'PostProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Начинаю постобработку...")

        try:
            # Объединение аудио чанков
            combined_audio_filename = f"combined_meeting_{self.meeting_id}.wav"
            combined_audio_filepath = self.output_dir / combined_audio_filename

            combine_audio_chunks(
                output_dir=self.output_dir,
                stream_sample_rate=STREAM_SAMPLE_RATE,
                meeting_id=self.meeting_id,
                output_filename=combined_audio_filename,
                pattern="chunk_*.wav"
            )
            
            if not os.path.exists(combined_audio_filepath):
                logger.error(f"[{self.meeting_id}] Объединенный аудиофайл не был создан: {combined_audio_filepath}")
                return
            
            dialog = "\n".join(
                f"[{seg['start']} - {seg['end']}] {seg['text']}"
                for seg in self.all_segments
            )
            print("Итоговый диалог:\n", dialog)

            cleaned_dialogue = "\n".join(seg['text'] for seg in self.all_segments)

            # Суммаризация
            logger.info(f"[{self.meeting_id}] Создание резюме...")
            summary_text = get_summary_response(cleaned_dialogue)
            print(f"Это вывод summary: \n{summary_text}")
            
            # Генерация заголовка
            logger.info(f"[{self.meeting_id}] Создание заголовка...")
            title_text = get_title_response(cleaned_dialogue)
            print(f"Это вывод заголовка: \n{title_text}")
            
            # Отправка результатов на внешний сервер
            self._send_results_to_backend(dialog, summary_text, title_text)
            
            # Сохранение резюме
            # summary_filename = f"summary_{self.meeting_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            # summary_filepath = self.summary_output_dir / summary_filename

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")

    # Сохранение аудиочанков
    def _save_chunk(self, audio_np):
        """Сохраняет аудио-чанк в файл WAV."""
        if audio_np.size == 0:
            return
        filename = f'chunk_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid4().hex[:6]}.wav'
        file_path = self.output_dir / filename
        try:
            sf.write(file_path, audio_np, STREAM_SAMPLE_RATE)
            logger.info(f"💾 Фрагмент сохранен: {filename} (длительность: {len(audio_np)/STREAM_SAMPLE_RATE:.2f} сек)")
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении аудиофрагмента: {e}")

    # Функция отправки результатов на внешний сервер
    def _send_results_to_backend(self, full_text: str, summary: str, title: str):
        try:
            payload = {"meeting_id": self.meeting_id, "full_text": full_text, "summary": summary, "title": title}
            headers = {"X-Internal-Api-Key": "key", "Content-Type": "application/json"}

            backend_url = os.getenv('MAIN_BACKEND_URL', 'https://maryrose.by')
            
            url = f"{backend_url}/meetings/internal/result"
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            logger.info(f"[{self.session_id}] ✅ Результаты для meeting_id {self.meeting_id} успешно отправлены.")
        except Exception as e:
            logger.error(f"[{self.session_id}] ❌ Ошибка при отправке результатов на Main Backend: {e}")

    # Остановка бота
    def stop(self):
        if not self.is_running.is_set():
            return
        
        logger.info(f"[{self.session_id}] Получена команда на завершение...")
        self.is_running.clear()
        self.processor_thread.join()
        
        # Запускаем постобработку в отдельном потоке
        post_processing_thread = threading.Thread(target=self._perform_post_processing)
        post_processing_thread.daemon = False
        post_processing_thread.start()
        
        logger.info(f"[{self.session_id}] Сессия с сайта завершена, запущена финальная обработка.") 