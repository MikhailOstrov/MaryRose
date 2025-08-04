import os
import time
import queue
import threading
import logging
import requests
from datetime import datetime
from uuid import uuid4
import webrtcvad
import numpy as np
from scipy.io.wavfile import write

# Импортируем конфигурацию из основного файла
from config.config import (STREAM_SAMPLE_RATE, MEET_FRAME_DURATION_MS,
                           MEET_VAD_AGGRESSIVENESS, MEET_PAUSE_THRESHOLD_S, 
                           MEET_AUDIO_CHUNKS_DIR, SUMMARY_OUTPUT_DIR)
from handlers.stt_handler import transcribe_chunk
from handlers.ollama_handler import get_mary_response, get_summary_response
from handlers.diarization_handler import run_diarization, process_rttm_and_transcribe
from api.utils import combine_audio_chunks

logger = logging.getLogger(__name__)

class LocalStreamProcessor:
    """
    Класс для обработки локального аудиопотока через WebSocket.
    Логика VAD, STT и постобработки скопирована из MeetListenerBot без изменений.
    """
    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id # ID для отслеживания сессии
        self.audio_queue = queue.Queue()
        self.is_running = threading.Event()
        self.is_running.set()
        self.vad = webrtcvad.Vad(MEET_VAD_AGGRESSIVENESS)
        self.summary_output_dir = SUMMARY_OUTPUT_DIR
        
        # Рассчитываем параметры VAD на основе конфига
        self.frame_size = int(STREAM_SAMPLE_RATE * MEET_FRAME_DURATION_MS / 1000)
        self.silent_frames_threshold = int(MEET_PAUSE_THRESHOLD_S * 1000 / MEET_FRAME_DURATION_MS)

        # Папка для сохранения чанков
        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Локальные аудиофрагменты будут сохраняться в: '{self.output_dir}'")
        
    def process_websocket_audio(self, audio_bytes: bytes):
        """
        ✅ КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Получает raw PCM данные из WebSocket (как в meet_listener)
        и напрямую разбивает их на фреймы для VAD, без конвертации через ffmpeg.
        """
        try:
            if not self.is_running.is_set():
                return
                
            # ✅ Принимаем raw PCM bytes напрямую (16-bit signed, 16kHz, mono)
            # Преобразуем bytes в numpy array для работы с данными
            pcm_data = np.frombuffer(audio_bytes, dtype=np.int16)
            
            logger.debug(f"[{self.meeting_id}] Получен raw PCM чанк: {len(audio_bytes)} байт ({len(pcm_data)} семплов)")
            
            # ✅ Разбиваем PCM данные на фреймы точно как в meet_listener
            frame_size = int(STREAM_SAMPLE_RATE * MEET_FRAME_DURATION_MS / 1000)
            for i in range(0, len(pcm_data), frame_size):
                frame = pcm_data[i:i + frame_size]
                if len(frame) == frame_size:  # Только полные фреймы
                    # ✅ Точно как в meet_listener: bytes(indata) -> audio_queue
                    self.audio_queue.put(frame.tobytes())
                
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при обработке raw PCM данных: {e}")

    def _process_audio_stream(self):
        """
        🔥 ТОЧНАЯ КОПИЯ из MeetListenerBot - обработка аудиопотока с VAD, STT и wake-word detection.
        """
        threading.current_thread().name = f'VADProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Процессор VAD запущен.")
        speech_buffer = []
        silent_frames_count = 0
        while self.is_running.is_set():
            try:
                audio_frame = self.audio_queue.get(timeout=1)
                is_speech = self.vad.is_speech(audio_frame, STREAM_SAMPLE_RATE)
                if is_speech:
                    speech_buffer.append(audio_frame)
                    silent_frames_count = 0
                else:
                    silent_frames_count += 1
                if speech_buffer and silent_frames_count > self.silent_frames_threshold:
                    full_speech_chunk_bytes = b''.join(speech_buffer)
                    speech_buffer.clear()
                    silent_frames_count = 0
                    # Сначала сохраняем фрагмент в любом случае для последующей обработки
                    threading.Thread(target=self._save_chunk, args=(full_speech_chunk_bytes,)).start()

                    # Затем выполняем транскрипцию и проверяем на триггерные слова
                    transcription, trigger_word = transcribe_chunk(full_speech_chunk_bytes)
                    print(transcription)  # ✅ Вывод в консоль RunPod
                    if trigger_word == 1:
                        print("Обнаружено слово-триггер")
                        response = get_mary_response(transcription)
                        print(response)  # ✅ Вывод в консоль RunPod

            except queue.Empty: 
                continue
            except Exception as e: 
                logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}")

    def _save_chunk(self, audio_bytes: bytes):
        """
        🔥 ТОЧНАЯ КОПИЯ из MeetListenerBot - сохранение аудиофрагмента на диск.
        """
        try:
            filename = f'chunk_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid4().hex[:6]}.wav'
            file_path = self.output_dir / filename
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
            write(str(file_path), STREAM_SAMPLE_RATE, audio_np)
            logger.info(f"[{self.meeting_id}] 💾 Фрагмент сохранен: {file_path} (длительность: {len(audio_np)/STREAM_SAMPLE_RATE:.2f} сек)")
        except Exception as e: 
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при сохранении аудиофрагмента: {e}")

    def _perform_post_processing(self):
        """
        🔥 ТОЧНАЯ КОПИЯ из MeetListenerBot - финальная обработка: диаризация, транскрипция, суммаризация.
        """
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
                output_filename=combined_audio_filename
            )
            
            if not os.path.exists(combined_audio_filepath):
                logger.error(f"[{self.meeting_id}] Объединенный аудиофайл не был создан: {combined_audio_filepath}")
                return
            
            # Диаризация
            logger.info(f"[{self.meeting_id}] Запуск диаризации...")
            rttm_path = run_diarization(str(combined_audio_filepath), str(self.output_dir))
            
            # Обработка RTTM и транскрипция
            logger.info(f"[{self.meeting_id}] Обработка диаризации и транскрипция...")
            dialogue_transcript = process_rttm_and_transcribe(rttm_path, str(combined_audio_filepath))
            print(f"Это вывод диалога: \n{dialogue_transcript}")

            # Суммаризация
            logger.info(f"[{self.meeting_id}] Создание резюме...")
            summary_text = get_summary_response(dialogue_transcript)
            print(f"Это вывод summary: \n{summary_text}")
            
            # Отправка результатов на внешний сервер
            self._send_results_to_backend(dialogue_transcript, summary_text)
            
        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")

    def _send_results_to_backend(self, full_text: str, summary: str):
        """
        🔥 ТОЧНАЯ КОПИЯ из MeetListenerBot - отправка результатов в Main Backend.
        """
        try:
            # Преобразуем meeting_id в число если это строка
            meeting_id_int = int(self.meeting_id) if isinstance(self.meeting_id, str) else self.meeting_id
            
            # Данные для отправки
            payload = {
                "meeting_id": meeting_id_int,
                "full_text": full_text,
                "summary": summary
            }
            
            # Заголовки
            headers = {
                "X-Internal-Api-Key": "key",
                "Content-Type": "application/json"
            }
            
            # Отправляем запрос
            logger.info(f"[{self.meeting_id}] Отправляю результаты на backend...")
            response = requests.post(
                "http://35.246.252.4/meetings/internal/result",
                json=payload,
                headers=headers,
                timeout=30
            )
            
            response.raise_for_status()
            logger.info(f"[{self.meeting_id}] ✅ Результаты успешно отправлены на backend")
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Ошибка при отправке результатов на backend: {e}")
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при отправке результатов: {e}")
        except ValueError as e:
            print(f"❌ Ошибка преобразования meeting_id в число: {e}")
            logger.error(f"[{self.meeting_id}] ❌ Ошибка meeting_id: {e}")
        except Exception as e:
            print(f"❌ Неожиданная ошибка при отправке результатов: {e}")
            logger.error(f"[{self.meeting_id}] ❌ Неожиданная ошибка: {e}")

    def run(self):
        """
        Запускает обработку аудиопотока (без браузера и микрофона).
        Вместо sounddevice используется WebSocket поток.
        """
        threading.current_thread().name = f'LocalStreamBot-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Локальный процессор запускается...")
        
        try:
            # Запускаем поток обработки аудио
            processor_thread = threading.Thread(target=self._process_audio_stream)
            processor_thread.start()
            
            logger.info(f"[{self.meeting_id}] 🎤 Начинаю обработку WebSocket аудиопотока...")
            
            # Ждем завершения обработки (в отличие от MeetListenerBot, здесь нет sounddevice)
            processor_thread.join()
            
            logger.info(f"[{self.meeting_id}] Обработка аудиопотока завершена.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка в работе процессора: {e}", exc_info=True)
        finally:
            self.stop()
            logger.info(f"[{self.meeting_id}] Локальный процессор полностью остановлен.")

    def stop(self):
        """
        Останавливает обработку и запускает постобработку.
        """
        if not self.is_running.is_set():
            return
        
        logger.info(f"[{self.meeting_id}] Получена команда на завершение...")

        self.is_running.clear()
        
        # Запускаем постобработку в отдельном потоке
        post_processing_thread = threading.Thread(target=self._perform_post_processing)
        post_processing_thread.daemon = False
        post_processing_thread.start()
        
        logger.info(f"[{self.meeting_id}] Локальная сессия завершена.") 