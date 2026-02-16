import threading
import logging
import time
import queue
import numpy as np 
import torch
import os
import soundfile as sf
import tempfile
import re
import asyncio
from collections import deque
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from handlers.llm_handler import llm_response, get_summary_response, get_title_response, mary_check
from utils.kb_requests import save_info_in_kb, get_info_from_kb
from config.load_models import create_new_vad_model
from config.config import (STREAM_SAMPLE_RATE, MEET_AUDIO_CHUNKS_DIR, SUMMARY_OUTPUT_DIR, TRIGGER_WORDS, STOP_WORDS)
from utils.backend_request import send_results_to_backend

logger = logging.getLogger(__name__)

class AudioHandler:
    def __init__(self, meeting_id, audio_queue, is_running, email, send_chat_message, stop):
        self.meeting_id = meeting_id
        self.audio_queue = audio_queue
        self.is_running = is_running
        self.vad = create_new_vad_model()
        #self.speak_via_meet = speak_via_meet
        self.email = email
        self.start_time = time.time()

        self.global_offset = 0.0
        self.all_segments = []

        self.summary_output_dir = SUMMARY_OUTPUT_DIR
        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id 

        self.send_chat_message = send_chat_message
        self.stop = stop
        
        self.ws_url = "ws://localhost:8000/transcribe"
        self.ws_connection = None
        self.TRIGGER_WORDS_SET = frozenset(TRIGGER_WORDS)
        self.STOP_WORDS_SET = frozenset(STOP_WORDS)

    # Преобразование временных меток
    def format_time_hms(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _connect_websocket(self):
        """Устанавливает WS соединение с Inference Service с повторными попытками."""
        while self.is_running.is_set():
            try:
                self.ws_connection = connect(self.ws_url)
                logger.info(f"[{self.meeting_id}] ✅ Подключено к Inference Service (WS).")
                return
            except Exception as e:
                logger.warning(f"[{self.meeting_id}] ⚠️ Не удалось подключиться к Inference Service: {e}. Повтор через 2с...")
                time.sleep(2)

    def _send_audio_to_service(self, audio_bytes: bytes) -> str:
        """Отправляет аудио и получает текст."""
        if not self.ws_connection:
            self._connect_websocket()
        
        start_ts = time.time()
        try:
            # logger.info(f"[{self.meeting_id}] Sending audio chunk: {len(audio_bytes)} bytes")
            self.ws_connection.send(audio_bytes)
            text = self.ws_connection.recv()
            
            latency = time.time() - start_ts
            if text:
                logger.info(f"[{self.meeting_id}] Transcribe latency: {latency:.3f}s. Text: {str(text)[:50]}...")
            
            return str(text)
        except (ConnectionClosed, InvalidStatusCode) as e:
            logger.warning(f"[{self.meeting_id}] 🔌 Разрыв соединения WS: {e}. Переподключение...")
            self._connect_websocket()
            # Повторная отправка (один раз)
            try:
                start_ts = time.time()
                self.ws_connection.send(audio_bytes)
                text = self.ws_connection.recv()
                latency = time.time() - start_ts
                logger.info(f"[{self.meeting_id}] Transcribe latency (retry): {latency:.3f}s. Text: {str(text)[:50]}...")
                return str(text)
            except Exception as e2:
                 logger.error(f"[{self.meeting_id}] ❌ Ошибка повторной отправки: {e2}")
                 return ""
        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка WS: {e}")
            return ""

    def _handle_transcription_logic(self, transcription, pipeline_start_time):
        """Обрабатывает полученный текст (триггеры, ответы LLM)."""
        transcription_lower = transcription.lower()
        
        first_word = transcription_lower.partition(" ")[0]

        if first_word in self.TRIGGER_WORDS_SET and any(sw in transcription_lower for sw in self.STOP_WORDS_SET):
             logger.info(f"[{self.meeting_id}] Провожу постобработку и завершаю работу")
             #self.speak_via_meet("Услышала Вас, завершаю работу!")
             self.send_chat_message("Услышала Вас, завершаю работу!")
             self.stop()
             return
        
        has_trigger = any(word in transcription_lower for word in self.TRIGGER_WORDS_SET)
        
        if has_trigger:
            choice = mary_check(transcription)
            logger.info(f"Решение (mary_check): {choice}")
            
            if choice == 1:
                #self.speak_via_meet("Секунду...")
                self.send_chat_message("Услышала Вас, действую...")
                try:
                    key, response = llm_response(transcription)
                    logger.info(f"Ответ от LLM: {key, response}")
                    
                    if key == 0:
                        asyncio.run(save_info_in_kb(response, self.email))
                        #self.speak_via_meet("Ваша информация сохранена.")
                        self.send_chat_message("Ваша информация сохранена.")
                    elif key == 1:
                        info_from_kb = asyncio.run(get_info_from_kb(response, self.email))
                        if info_from_kb is None:
                            #self.speak_via_meet("Не нашла информации в вашей базе знаний.")
                            self.send_chat_message("Не нашла информации в вашей базе знаний.")
                        else:
                            #self.speak_via_meet("Вывожу в чат найденную информацию...")
                            self.send_chat_message(info_from_kb)
                    elif key == 3:
                        #self.speak_via_meet(response)
                        self.send_chat_message(response)

                except Exception as chat_err:
                    logger.error(f"[{self.meeting_id}] Ошибка при отправке ответа в чат: {chat_err}")
            else:
                 logger.info(f"[{self.meeting_id}] mary_check=0, игнорируем (не обращение).")
        else:
            # Нет триггерных слов - логируем для отладки
            logger.debug(f"[{self.meeting_id}] Триггерных слов не найдено в транскрипции: '{transcription}'")

    def _process_speech_buffer(self, speech_buffer, start_ts, end_ts, min_duration=0.5):
        """Собирает аудио из буфера, отправляет на транскрибацию и обрабатывает результат."""
        if not speech_buffer:
            return

        full_audio_np = np.concatenate(speech_buffer)
        chunk_duration = len(full_audio_np) / STREAM_SAMPLE_RATE

        if chunk_duration < min_duration:
            return

        # ОТПРАВКА НА СЕРВЕР (WS)
        transcribed_text = self._send_audio_to_service(full_audio_np.tobytes())
        
        if not transcribed_text:
            return

        dialog = f"[{self.format_time_hms(start_ts)} - {self.format_time_hms(end_ts)}] {transcribed_text.strip()}"
        
        self.all_segments.append(dialog)
        print(dialog)

        # Чистый текст без таймингов
        transcription = re.sub(r"\[\d{2}:\d{2}:\d{2}\s*-\s*\d{2}:\d{2}:\d{2}\]\s*", "", dialog)
        
        self.global_offset += chunk_duration
        
        # Обработка логики (триггеры и т.д.)
        self._handle_transcription_logic(transcription, None)

    # Обработка аудиопотока -- транскрибация -- ответ (если обнаружен триггер)
    def _process_audio_stream(self):
        threading.current_thread().name = f'VADProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] VAD процессор запущен (Silero).")

        # Инициализируем соединение при старте потока
        self._connect_websocket()

        VAD_CHUNK_SIZE = 512
        STREAM_SR = 16000
        CHUNK_DURATION_MS = (VAD_CHUNK_SIZE / STREAM_SR) * 1000

        VAD_THRESHOLD = 0.3
        SILENCE_DURATION_MS = 600
        MIN_SPEECH_DURATION_S = 0.5
        MAX_SPEECH_DURATION_S = 15.0

        vad_buffer = deque(maxlen=VAD_CHUNK_SIZE * 8)

        speech_buffer_for_asr = [] 
        recent_probs = deque(maxlen=3)

        is_speaking = False
        silence_accum_ms = 0.0
        speech_start_walltime = None

        while self.is_running.is_set():
            try:
                audio_frame_bytes = self.audio_queue.get(timeout=1.0)
                if not audio_frame_bytes:
                    continue

                audio_np = np.frombuffer(audio_frame_bytes, dtype=np.int16)
                audio_float = audio_np.astype(np.float32) / 32768.0

                vad_buffer.extend(audio_float)

                while len(vad_buffer) >= VAD_CHUNK_SIZE:
                    chunk_list = list(vad_buffer)[:VAD_CHUNK_SIZE]
                    chunk_to_process_np = np.array(chunk_list, dtype=np.float32)

                    for _ in range(VAD_CHUNK_SIZE):
                        vad_buffer.popleft()

                    chunk_tensor = torch.from_numpy(chunk_to_process_np)

                    speech_prob = self.vad(chunk_tensor, STREAM_SR).item()

                    recent_probs.append(speech_prob)
                    if len(recent_probs) > 0:
                        smooth_prob = sum(recent_probs) / len(recent_probs)
                    else:
                        smooth_prob = 0.0

                    now = time.time()
                    meeting_elapsed_sec = now - self.start_time

                    if smooth_prob > VAD_THRESHOLD:
                        if not is_speaking:
                            logger.info(f"[{self.meeting_id}] Начало речи")
                            is_speaking = True
                            speech_start_walltime = meeting_elapsed_sec

                        speech_buffer_for_asr.append(chunk_to_process_np)
                        silence_accum_ms = 0.0

                        current_speech_duration_s = len(speech_buffer_for_asr) * (VAD_CHUNK_SIZE / STREAM_SR)
                        if current_speech_duration_s >= MAX_SPEECH_DURATION_S:
                            logger.info(
                                f"[{self.meeting_id}] Принудительная отсечка речи по тайм-ауту "
                                f"({MAX_SPEECH_DURATION_S}с)"
                            )

                            speech_end_walltime = speech_start_walltime + current_speech_duration_s
                            self._process_speech_buffer(
                                speech_buffer_for_asr,
                                speech_start_walltime,
                                speech_end_walltime,
                                MIN_SPEECH_DURATION_S
                            )

                            speech_buffer_for_asr.clear()
                            speech_start_walltime = speech_end_walltime

                    else:
                        if is_speaking:
                            silence_accum_ms += CHUNK_DURATION_MS

                            if silence_accum_ms >= SILENCE_DURATION_MS:
                                if speech_buffer_for_asr:
                                    actual_duration_s = len(speech_buffer_for_asr) * (VAD_CHUNK_SIZE / STREAM_SR)
                                    speech_end_walltime = speech_start_walltime + actual_duration_s

                                    self._process_speech_buffer(
                                        speech_buffer_for_asr,
                                        speech_start_walltime,
                                        speech_end_walltime,
                                        MIN_SPEECH_DURATION_S
                                    )
                                    speech_buffer_for_asr.clear()

                                logger.info(f"[{self.meeting_id}] Конец речи (тишина {silence_accum_ms:.0f} мс)")
                                is_speaking = False
                                silence_accum_ms = 0.0
                                speech_start_walltime = None

            except queue.Empty:
                # Если очередь пуста, но есть накопленная речь → принудительно завершаем
                if is_speaking and speech_buffer_for_asr:
                    logger.info(f"[{self.meeting_id}] Тайм-аут очереди — обрабатываем остаток речи")
                    actual_duration_s = len(speech_buffer_for_asr) * (VAD_CHUNK_SIZE / STREAM_SR)
                    speech_end_walltime = speech_start_walltime + actual_duration_s

                    self._process_speech_buffer(
                        speech_buffer_for_asr,
                        speech_start_walltime,
                        speech_end_walltime,
                        MIN_SPEECH_DURATION_S
                    )
                    speech_buffer_for_asr.clear()
                    is_speaking = False
                    silence_accum_ms = 0.0
                    speech_start_walltime = None

                continue

            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}", exc_info=True)

        # Cleanup
        logger.info(f"[{self.meeting_id}] VAD процессор завершает работу")
        if self.ws_connection:
            try:
                self.ws_connection.close()
            except Exception:
                pass

    # Постобработка: суммаризация -- генерация заголовка -- отправка результатов на внешний сервер
    def _perform_post_processing(self):
        threading.current_thread().name = f'PostProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Начинаю постобработку...")

        try:
            full = "\n".join(self.all_segments)
            #print(f"Финальный диалог: \n {full}")
            now = time.time()
            meeting_elapsed_sec = now - self.start_time

            # Очистка диалога от временных меток
            cleaned_dialogue = re.sub(r"\[\d{2}:\d{2}:\d{2}\s*-\s*\d{2}:\d{2}:\d{2}\]\s*", "", full).strip()

            send_results_to_backend(self.meeting_id, cleaned_dialogue, int(meeting_elapsed_sec))

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")
