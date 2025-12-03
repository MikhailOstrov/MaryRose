import threading
import logging
import time
import queue
import numpy as np 
import torch
import re
import asyncio
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from handlers.llm_handler import llm_response, get_summary_response, get_title_response
from utils.kb_requests import save_info_in_kb, get_info_from_kb
from config.config import (STREAM_SAMPLE_RATE, STREAM_TRIGGER_WORD, STREAM_STOP_WORD_1, STREAM_STOP_WORD_2, MEET_AUDIO_CHUNKS_DIR,
                        STREAM_STOP_WORD_3, MEET_FRAME_DURATION_MS, SUMMARY_OUTPUT_DIR)
from config.load_models import create_new_vad_model
from utils.backend_request import send_results_to_backend

logger = logging.getLogger(__name__)

class AudioHandler:
    def __init__(self, meeting_id, audio_queue, is_running, email, send_chat_message, stop):
        self.meeting_id = meeting_id
        self.audio_queue = audio_queue
        self.is_running = is_running
        self.vad = create_new_vad_model()
        # self.asr_model = asr_model # Модель больше не нужна локально, используем WS
        self.email = email
        self.start_time = time.time()

        self.global_offset = 0.0
        self.all_segments = []

        self.summary_output_dir = SUMMARY_OUTPUT_DIR # Директория сохранения summary
        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id 

        self.send_chat_message = send_chat_message
        self.stop = stop
        
        self.ws_url = "ws://localhost:8000/transcribe"
        self.ws_connection = None

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
        if transcription.lower().lstrip().startswith(STREAM_TRIGGER_WORD):
            clean_transcription = ''.join(char for char in transcription.lower() if char.isalnum() or char.isspace())

            if STREAM_STOP_WORD_1 in clean_transcription or STREAM_STOP_WORD_2 in clean_transcription or STREAM_STOP_WORD_3 in clean_transcription:
                logger.info(f"[{self.meeting_id}] Провожу постобработку и завершаю работу")
                self.send_chat_message("Услышала Вас, завершаю работу!")
                self.stop()
            else:
                self.send_chat_message("Услышала Вас, действую...")
                try:
                    key, response = llm_response(transcription)
                    logger.info(f"Ответ от LLM: {key, response}")
                    if response:
                        print("Отправляю ответ в чат...")
                    if key == 0:
                        asyncio.run(save_info_in_kb(response, self.email))
                        self.send_chat_message("Ваша информация сохранена.")
                    elif key == 1:
                        info_from_kb = asyncio.run(get_info_from_kb(response, self.email))
                        if info_from_kb == None:
                            self.send_chat_message("Не нашла информации в вашей базе знаний.")
                        else:
                            self.send_chat_message(info_from_kb)
                    elif key == 3:
                        self.send_chat_message(response)

                except Exception as chat_err:
                    logger.error(f"[{self.meeting_id}] Ошибка при отправке ответа в чат: {chat_err}")

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

        vad_buffer = None
        # Silero VAD поддерживает только 512 сэмплов (при 16k)
        VAD_CHUNK_SIZE = 512
        # Буфер для накопления чанков перед прогоном через модель (оптимизация)
        # Обрабатываем пачкой, но подаем в модель кусочками по 512
        accumulated_chunks = []
        
        speech_buffer_for_asr = []
        is_speaking = False
        recent_probs = []                     # для сглаживания

        # Настройки
        vad_threshold = 0.3                   # вероятность речи
        silence_duration_ms = 600             # сколько тишины нужно для конца речи
        min_speech_duration = 0.5             # минимальная длина речи
        MAX_SPEECH_DURATION_S = 30.0          # Максимальная длина речи перед принудительной отправкой
        sr = STREAM_SAMPLE_RATE

        silence_accum_ms = 0
        speech_start_walltime = None

        # Таймер для всего пайплайна обработки речи
        pipeline_start_time = None

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

                # Определяем устройство, на котором находится модель
                device = next(self.vad.parameters()).device

                while vad_buffer is not None and vad_buffer.shape[0] >= VAD_CHUNK_SIZE:
                    chunk_to_process = vad_buffer[:VAD_CHUNK_SIZE]
                    vad_buffer = vad_buffer[VAD_CHUNK_SIZE:]

                    # Переносим чанк на то же устройство, что и модель (GPU если доступно)
                    chunk_to_process_device = chunk_to_process.to(device)
                    
                    # ВАЖНО: Silero VAD требует точный размер входа (512), батчинг внутри модели не поддерживается "из коробки" для streaming
                    # Поэтому вызываем модель для каждого чанка 512, но сам чанк уже на GPU
                    speech_prob = self.vad(chunk_to_process_device, sr).item()

                    recent_probs.append(speech_prob)
                    if len(recent_probs) > 3:
                        recent_probs.pop(0)
                    smooth_prob = sum(recent_probs) / len(recent_probs)

                    now = time.time()
                    meeting_elapsed_sec = now - self.start_time

                    if smooth_prob > vad_threshold:
                        if not is_speaking:
                            logger.info(f"[{self.meeting_id}] ▶️ Начало речи")
                            is_speaking = True
                            speech_start_walltime = meeting_elapsed_sec
                            pipeline_start_time = time.time()  # Запуск таймера пайплайна

                        speech_buffer_for_asr.append(chunk_to_process.numpy())
                        silence_accum_ms = 0
                        
                        # --- ПРОВЕРКА НА МАКСИМАЛЬНУЮ ДЛИТЕЛЬНОСТЬ ---
                        current_duration_s = (len(speech_buffer_for_asr) * VAD_CHUNK_SIZE) / sr
                        if current_duration_s >= MAX_SPEECH_DURATION_S:
                            logger.info(f"[{self.meeting_id}] ✂️ Принудительная отсечка речи по тайм-ауту ({MAX_SPEECH_DURATION_S}с)")
                            
                            # Рассчитываем конец текущего куска
                            speech_end_walltime = speech_start_walltime + current_duration_s
                            
                            # Обрабатываем накопленный буфер
                            self._process_speech_buffer(speech_buffer_for_asr, speech_start_walltime, speech_end_walltime, min_speech_duration)
                            
                            # Очищаем буфер и обновляем начало следующего куска
                            speech_buffer_for_asr.clear()
                            speech_start_walltime = speech_end_walltime # Следующий кусок начинается сразу
                            # is_speaking остается True, так как мы все еще в блоке "речь идет"

                    else:
                        if is_speaking:
                            silence_accum_ms += (VAD_CHUNK_SIZE / sr) * 1000
                            if silence_accum_ms >= silence_duration_ms:

                                if speech_buffer_for_asr:
                                    chunk_duration = (len(speech_buffer_for_asr) * VAD_CHUNK_SIZE) / sr
                                    speech_end_walltime = speech_start_walltime + chunk_duration
                                    
                                    self._process_speech_buffer(speech_buffer_for_asr, speech_start_walltime, speech_end_walltime, min_speech_duration)
                                    speech_buffer_for_asr.clear()

                                is_speaking = False
                                silence_accum_ms = 0
                                pipeline_start_time = None

            except queue.Empty:
                if is_speaking and speech_buffer_for_asr:
                    logger.info(f"[{self.meeting_id}] Тайм-аут, обрабатываем оставшуюся речь.")
                    chunk_duration = (len(speech_buffer_for_asr) * VAD_CHUNK_SIZE) / sr
                    speech_end_walltime = speech_start_walltime + chunk_duration
                    self._process_speech_buffer(speech_buffer_for_asr, speech_start_walltime, speech_end_walltime, min_speech_duration)
                    speech_buffer_for_asr.clear()
                    is_speaking = False
                continue
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}", exc_info=True)
        
        # Закрываем сокет
        if self.ws_connection:
            try:
                self.ws_connection.close()
            except:
                pass

    # Постобработка: объединение аудиочанков -- запуск диаризации и объединение с транскрибацией -- суммаризация -- генерация заголовка -- отправка результатов на внешний сервер
    def _perform_post_processing(self):
        threading.current_thread().name = f'PostProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Начинаю постобработку...")

        try:

            full = "\n".join(self.all_segments)
        
            print(f"Финальный диалог: \n {full}")

            now = time.time()
            meeting_elapsed_sec = now - self.start_time

            # Очистка диалога от временных меток
            cleaned_dialogue = re.sub(r"\[\d{2}:\d{2}:\d{2}\s*-\s*\d{2}:\d{2}:\d{2}\]\s*", "", full).strip()

            summary_text = ""
            title_text = ""
            
            if cleaned_dialogue:
                # Суммаризация
                logger.info(f"[{self.meeting_id}] Создание резюме...")
                summary_text = get_summary_response(cleaned_dialogue)
                print(f"Это вывод summary: \n{summary_text}")
                
                # Генерация заголовка
                logger.info(f"[{self.meeting_id}] Создание заголовка...")
                title_text = get_title_response(cleaned_dialogue)
                print(f"Это вывод заголовка: \n{title_text}")

                # Отправка результатов на внешний сервер
                send_results_to_backend(self.meeting_id, full, summary_text, title_text, int(meeting_elapsed_sec))
            else:
                logger.warning(f"[{self.meeting_id}] Диалог пуст, пропускаю создание резюме и заголовка.")
            

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")
