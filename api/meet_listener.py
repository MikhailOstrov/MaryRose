import os
import time
import queue
import threading
import logging
import requests
from datetime import datetime
from uuid import uuid4
import torch
import numpy as np
from scipy.io.wavfile import write
import sounddevice as sd
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Импортируем конфигурацию из основного файла
from config.config import (STREAM_SAMPLE_RATE,SILENCE_THRESHOLD_FRAMES, MEET_FRAME_DURATION_MS,
                           MEET_AUDIO_CHUNKS_DIR, MEET_INPUT_DEVICE_NAME,
                           CHROME_PROFILE_DIR, MEET_GUEST_NAME, SUMMARY_OUTPUT_DIR)
from handlers.ollama_handler import get_mary_response, get_summary_response, get_title_response
from handlers.diarization_handler import run_diarization, process_rttm_and_transcribe
from config.load_models import vad_model, asr_model
from api.utils import combine_audio_chunks

logger = logging.getLogger(__name__)

class MeetListenerBot:
    """
    Класс для управления ботом, который подключается к Google Meet,
    слушает аудиопоток и сохраняет его в виде фрагментов (чанков).
    ЛОГИКА ПОЛНОСТЬЮ СКОПИРОВАНА ИЗ РАБОЧЕГО join_meet/meet_listener.py
    """
    def __init__(self, meeting_url: str, meeting_id: str):
        self.meeting_url = meeting_url
        self.meeting_id = meeting_id # ID для отслеживания сессии
        self.driver = None
        self.audio_queue = queue.Queue()
        self.is_running = threading.Event()
        self.is_running.set()
        self.vad = vad_model
        self.asr_model = asr_model
        self.summary_output_dir = SUMMARY_OUTPUT_DIR
        self.joined_successfully = False # Флаг для контроля успешного входа
        # Рассчитываем параметры VAD на основе конфига
        self.frame_size = int(STREAM_SAMPLE_RATE * MEET_FRAME_DURATION_MS / 1000)
        self.silent_frames_threshold = SILENCE_THRESHOLD_FRAMES

        # Папка для сохранения чанков и скриншотов
        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Аудиофрагменты будут сохраняться в: '{self.output_dir}'")

    def _monitor_participants(self):
        """Отслеживает количество участников. Если бот остается один, он завершает работу."""
        threading.current_thread().name = f'ParticipantMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг участников запущен.")
        
        participant_locator_xpath = "//button[.//i[text()='people'] and @aria-label]"
        consecutive_failures = 0
        max_failures = 10

        while self.is_running.is_set():
            for _ in range(15): # Проверяем каждые 15 секунд
                if not self.is_running.is_set():
                    logger.info(f"[{self.meeting_id}] Мониторинг участников остановлен.")
                    return
                time.sleep(1)
            
            try:
                participant_element = self.driver.find_element(By.XPATH, participant_locator_xpath)
                aria_label = participant_element.get_attribute('aria-label') or ""
                numbers = ''.join(filter(str.isdigit, aria_label))
                if numbers:
                    count = int(numbers)
                    logger.info(f"[{self.meeting_id}] Текущее количество участников: {count}")
                    consecutive_failures = 0 # Сбрасываем счетчик при успехе
                    if count <= 1:
                        logger.warning(f"[{self.meeting_id}] Встреча пуста. Завершаю работу...")
                        self.stop()
                        return
                else:
                    # Это может произойти, если элемент найден, но в нем нет цифр
                    consecutive_failures += 1
                    logger.warning(f"[{self.meeting_id}] Не удалось извлечь число участников из элемента. Попытка {consecutive_failures}/{max_failures}.")

            except Exception:
                consecutive_failures += 1
                logger.warning(f"[{self.meeting_id}] Не удалось найти счетчик участников. Попытка {consecutive_failures}/{max_failures}.")

            if consecutive_failures >= max_failures:
                logger.error(f"[{self.meeting_id}] Не удалось найти счетчик участников {max_failures} раз подряд. Предполагаю, что встреча завершена.")
                self.stop()
                return
    
    # --- КРИТИЧЕСКИ ВАЖНЫЙ БЛОК: ИНИЦИАЛИЗАЦИЯ ДРАЙВЕРА 1-в-1 КАК В join_meet ---
    def _initialize_driver(self):
        logger.info(f"[{self.meeting_id}] Запуск undetected_chromedriver с настройками из join_meet...")
        try:
            logger.info(f"[{self.meeting_id}] Попытка №1: с user-data-dir и use_subprocess=True")
            opt = uc.ChromeOptions()
            opt.add_argument('--no-sandbox')
            opt.add_argument('--disable-dev-shm-usage')
            opt.add_argument(f'--user-data-dir={CHROME_PROFILE_DIR}') 
            
            self.driver = uc.Chrome(
                options=opt,
                headless=False, # Важно для работы в Xvfb
                use_subprocess=True, # Важно для стабильности
                version_main=138 # Закрепляем версию для надежности
            )
            logger.info(f"[{self.meeting_id}] ✅ Chrome запущен (Попытка №1)!")
            
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Попытка №1 не сработала: {e}")
            logger.info(f"[{self.meeting_id}] Попытка №2: с базовыми опциями...")
            try:
                opt = uc.ChromeOptions()
                opt.add_argument('--no-sandbox')
                opt.add_argument('--disable-dev-shm-usage')
                opt.add_argument('--disable-gpu')
                opt.add_argument(f'--user-data-dir={CHROME_PROFILE_DIR}')
                opt.add_argument('--window-size=1280,720')
                
                opt.add_experimental_option("prefs", {
                    "profile.default_content_setting_values.media_stream_mic": 1,
                    "profile.default_content_setting_values.notifications": 2
                })
                
                self.driver = uc.Chrome(options=opt, version_main=138)
                logger.info(f"[{self.meeting_id}] ✅ Chrome запущен (Попытка №2)!")
                
            except Exception as e2:
                logger.critical(f"[{self.meeting_id}] Полный провал запуска Chrome: {e2}", exc_info=True)
                raise

    def _save_screenshot(self, name: str):
        """Сохраняет скриншот для отладки."""
        path = self.output_dir / f'{datetime.now().strftime("%H%M%S")}_{name}.png'
        try:
            self.driver.save_screenshot(str(path))
            logger.info(f"[{self.meeting_id}] Скриншот сохранен: {path}")
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось сохранить скриншот '{name}': {e}")
            
    # --- КРИТИЧЕСКИ ВАЖНЫЙ БЛОК: ЛОГИКА ПРИСОЕДИНЕНИЯ 1-в-1 КАК В join_meet ---
    def join_meet_as_guest(self):
        try:
            logger.info(f"[{self.meeting_id}] Подключаюсь к встрече как гость: {self.meeting_url}")
            self.driver.get(self.meeting_url)
            time.sleep(8)
            self._save_screenshot("01_meet_page_loaded")
            
            logger.info(f"[{self.meeting_id}] Ищу поле для ввода имени...")
            name_input_xpath = '//input[@placeholder="Your name" or @aria-label="Your name" or contains(@placeholder, "name")]'
            name_input = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, name_input_xpath))
            )
            
            logger.info(f"[{self.meeting_id}] Ввожу имя: {MEET_GUEST_NAME}")
            name_input.clear()
            name_input.send_keys(MEET_GUEST_NAME)
            time.sleep(2)
            self._save_screenshot("02_name_entered")

            try:
                logger.info(f"[{self.meeting_id}] Проверяю наличие диалога о микрофоне...")
                continue_without_mic_xpath = '//button[.//span[contains(text(), "Continue without microphone")]]'
                continue_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, continue_without_mic_xpath))
                )
                logger.info(f"[{self.meeting_id}] Нажимаю 'Продолжить без микрофона'...")
                continue_button.click()
                time.sleep(2)
                self._save_screenshot("02a_mic_dialog_closed")
            except Exception:
                logger.info(f"[{self.meeting_id}] Диалог о микрофоне не найден, продолжаю.")
            
            join_button_xpath = '//button[.//span[contains(text(), "Ask to join") or contains(text(), "Попросить войти")]]'
            logger.info(f"[{self.meeting_id}] Ищу кнопку 'Ask to join'...")
            join_button = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, join_button_xpath))
            )
            join_button.click()
            self._save_screenshot("03_after_ask_to_join")
            
            logger.info(f"[{self.meeting_id}] Запрос отправлен. Ожидаю одобрения хоста (до 120с)...")
            max_wait_time, check_interval, elapsed_time = 120, 2, 0
            
            # ОБНОВЛЕННЫЙ И НАДЕЖНЫЙ СПИСОК ИНДИКАТОРОВ УСПЕХА
            success_indicators = [
                # Кнопка завершения звонка - самый надежный индикатор
                '//button[@data-tooltip*="end call" or @aria-label*="end call" or @aria-label*="завершить"]',
                # Кнопка списка участников, которая появляется только внутри встречи
                "//button[.//i[text()='people'] and @aria-label]",
                # Другие надежные элементы интерфейса
                '//div[@data-self-name]', # Элемент с именем самого бота
                '//div[contains(@class, "control") and (contains(@class, "bar") or contains(@class, "panel"))]', # Панель управления
                '//button[@aria-label*="hand" or @aria-label*="рука" or @data-tooltip*="hand"]' # Кнопка "поднять руку"
            ]
            # ПОЛНЫЙ СПИСОК ИНДИКАТОРОВ ОШИБКИ
            error_indicators = [
                '//*[contains(text(), "denied") or contains(text(), "отклонен")]',
                '//*[contains(text(), "rejected") or contains(text(), "отказано")]',
                '//*[contains(text(), "error") or contains(text(), "ошибка")]',
                '//*[contains(text(), "unable") or contains(text(), "невозможно")]'
            ]

            while elapsed_time < max_wait_time:
                for i, xpath in enumerate(success_indicators):
                    try:
                        if self.driver.find_element(By.XPATH, xpath).is_displayed():
                            self._save_screenshot("04_joined_successfully")
                            logger.info(f"[{self.meeting_id}] ✅ Успешно присоединился к встрече! (индикатор #{i+1})")
                            self.joined_successfully = True
                            return True
                    except: continue
                
                for error_xpath in error_indicators:
                    try:
                        error_element = self.driver.find_element(By.XPATH, error_xpath)
                        if error_element.is_displayed():
                            logger.error(f"[{self.meeting_id}] ❌ Присоединение отклонено: {error_element.text}")
                            self._save_screenshot("98_join_denied")
                            return False
                    except: continue

                time.sleep(check_interval)
                elapsed_time += check_interval
                if elapsed_time % 30 == 0:
                    logger.info(f"[{self.meeting_id}] Ожидание... {elapsed_time}с прошло.")
                    self._save_screenshot(f"wait_{elapsed_time}s")

            logger.warning(f"[{self.meeting_id}] ⚠️ Превышено время ожидания одобрения ({max_wait_time}с).")
            self._save_screenshot("99_join_timeout")
            return False

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка при присоединении: {e}", exc_info=True)
            self._save_screenshot("99_join_fatal_error")
            return False

    def _find_device_id(self):
        logger.info(f"[{self.meeting_id}] Поиск аудиоустройства с именем '{MEET_INPUT_DEVICE_NAME}'...")
        try:
            devices = sd.query_devices()
            logger.debug(f"Найденные аудиоустройства: {devices}")
            for i, device in enumerate(devices):
                if MEET_INPUT_DEVICE_NAME in device['name'] and device['max_input_channels'] > 0:
                    logger.info(f"[{self.meeting_id}] ✅ Найдено целевое устройство: ID {i}, Имя: {device['name']}")
                    return i
            raise ValueError(f"Не удалось найти входное аудиоустройство с именем '{MEET_INPUT_DEVICE_NAME}'")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при поиске аудиоустройств: {e}", exc_info=True)
            raise

    # --- Упрощенный callback, как в оригинале, без лишнего логирования ---
    def _audio_capture_callback(self, indata, frames, time, status):
        if status:
            logger.warning(f"[{self.meeting_id}] Статус аудиоустройства: {status}")
        if self.is_running.is_set():
            self.audio_queue.put(bytes(indata))

    # --- Процессор VAD без лишнего логирования ---
    def _process_audio_stream(self):
        threading.current_thread().name = f'VADProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Процессор VAD запущен с моделью Silero.")

        # --- НАЧАЛО ИЗМЕНЕНИЙ ---
        
        # 1. Буфер для накопления аудио перед отправкой в VAD. Хранит тензор.
        vad_buffer = None
        # 2. Размер чанка для анализа VAD. Должен быть кратен 512. 1536 (96 мс) - хорошее значение.
        VAD_CHUNK_SIZE = 512
        
        # 3. Буфер для накопления речевых сегментов перед отправкой в ASR.
        speech_buffer_for_asr = []
        
        # 4. Состояние: говорим ли мы сейчас
        is_speaking = False
        
        # 5. Счетчик кадров тишины после того, как речь была обнаружена
        silent_frames_after_speech = 0
        
        TRIGGER_WORD = "мэри"

        while self.is_running.is_set():
            try:
                # Получаем сырые байты из очереди
                audio_frame_bytes = self.audio_queue.get(timeout=1)
                if not audio_frame_bytes:
                    continue

                # Конвертируем в тензор
                audio_np = np.frombuffer(audio_frame_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                new_audio_tensor = torch.from_numpy(audio_np)

                # Добавляем новый фрейм в буфер VAD
                if vad_buffer is None:
                    vad_buffer = new_audio_tensor
                else:
                    vad_buffer = torch.cat([vad_buffer, new_audio_tensor])

                # Обрабатываем буфер, пока в нем есть данные для анализа
                while vad_buffer is not None and vad_buffer.shape[0] >= VAD_CHUNK_SIZE:
                    
                    # Берем чанк для обработки из начала буфера
                    chunk_to_process = vad_buffer[:VAD_CHUNK_SIZE]
                    # Оставляем остаток в буфере
                    vad_buffer = vad_buffer[VAD_CHUNK_SIZE:]
                    
                    # Отправляем чанк нужного размера в модель VAD
                    speech_prob = self.vad(chunk_to_process, STREAM_SAMPLE_RATE).item()
                    
                    # Логика определения начала и конца речи
                    if speech_prob > 0.3: # Порог можно настроить
                        if not is_speaking:
                            logger.info(f"[{self.meeting_id}] Обнаружено начало речи.")
                            is_speaking = True
                        
                        # Добавляем речевой чанк в буфер для ASR
                        speech_buffer_for_asr.append(chunk_to_process.numpy())
                        silent_frames_after_speech = 0
                    else:
                        if is_speaking:
                            # Речь была, но сейчас тишина. Начинаем считать "кадры тишины".
                            silent_frames_after_speech += 1
                            
                            # Если тишина длится достаточно долго, считаем, что фраза закончилась.
                            if silent_frames_after_speech > self.silent_frames_threshold:
                                logger.info(f"[{self.meeting_id}] Обнаружен конец фразы.")
                                is_speaking = False
                                silent_frames_after_speech = 0
                                
                                # --- БЛОК ОБРАБОТКИ ЗАКОНЧЕННОЙ ФРАЗЫ ---
                                if speech_buffer_for_asr:
                                    full_audio_np = np.concatenate(speech_buffer_for_asr)
                                    speech_buffer_for_asr = [] # Очищаем буфер
                                    
                                    self._save_chunk(full_audio_np)

                                    segments, _ = self.asr_model.transcribe(full_audio_np, beam_size=5)
                                    transcription = "".join([seg.text for seg in segments]).strip()
                                    
                                    if transcription:
                                        logger.info(f"[{self.meeting_id}] Распознано: '{transcription}'")
                                        
                                        # Проверка на триггерное слово
                                        if transcription.lower().lstrip().startswith(TRIGGER_WORD):
                                            logger.info(f"[{self.meeting_id}] Обнаружено слово-триггер. Отправка команды...")
                                            response = get_mary_response(transcription)
                                            logger.info(f"[{self.meeting_id}] Ответ от Мэри: {response}")
                
            except queue.Empty:
                # Если очередь пуста, и у нас есть накопленная речь, обрабатываем ее.
                if is_speaking and speech_buffer_for_asr:
                    logger.info(f"[{self.meeting_id}] Тайм-аут, обрабатываем оставшуюся речь.")
                    is_speaking = False
                    # Тут можно скопировать блок обработки законченной фразы, если нужно
                continue
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}", exc_info=True)

    def _perform_post_processing(self):
        """
        Выполняет всю постобработку: объединение аудио, транскрипцию,
        диаризацию и суммаризацию. Вызывается в отдельном потоке.
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
                output_filename=combined_audio_filename,
                pattern="chunk_*.wav"
            )
            
            if not os.path.exists(combined_audio_filepath):
                logger.error(f"[{self.meeting_id}] Объединенный аудиофайл не был создан: {combined_audio_filepath}")
                return
            
            # Диаризация
            logger.info(f"[{self.meeting_id}] Запуск диаризации...")
            rttm_path = run_diarization(str(combined_audio_filepath), str(self.output_dir))
            
            # Обработка RTTM и транскрипция (возможно, слияние с результатами онлайн STT)
            logger.info(f"[{self.meeting_id}] Обработка диаризации и транскрипция...")
            dialogue_transcript = process_rttm_and_transcribe(rttm_path, str(combined_audio_filepath))
            print(f"Это вывод диалога: \n{dialogue_transcript}")

            # Убираем метки спикеров, что икслючить засорение промптов
            import re
            pattern = r"\[speaker_\d+\]:\s*"
            cleaned_dialogue = re.sub(pattern, "", dialogue_transcript)

            # Суммаризация
            logger.info(f"[{self.meeting_id}] Создание резюме...")
            summary_text = get_summary_response(cleaned_dialogue)
            print(f"Это вывод summary: \n{summary_text}")
            
            # Генерация заголовка
            logger.info(f"[{self.meeting_id}] Создание заголовка...")
            title_text = get_title_response(cleaned_dialogue)
            print(f"Это вывод заголовка: \n{title_text}")
            
            # Отправка результатов на внешний сервер
            self._send_results_to_backend(dialogue_transcript, summary_text, title_text)
            
            # Сохранение резюме
            summary_filename = f"summary_{self.meeting_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            summary_filepath = self.summary_output_dir / summary_filename
            # with open(summary_filepath, "w", encoding="utf-8") as f:
            #     f.write(summary_text)
            # logger.info(f"[{self.meeting_id}] ✅ Резюме успешно сохранено в: '{summary_filepath}'")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")

    def _send_results_to_backend(self, full_text: str, summary: str, title: str):
        """Отправляет результаты встречи на внешний backend"""
        try:
            # Преобразуем meeting_id в число если это строка
            meeting_id_int = int(self.meeting_id) if isinstance(self.meeting_id, str) else self.meeting_id
            
            # Данные для отправки
            payload = {
                "meeting_id": meeting_id_int,
                "full_text": full_text,
                "summary": summary,
                "title": title
            }
            
            # Заголовки
            headers = {
                "X-Internal-Api-Key": "key",
                "Content-Type": "application/json"
            }
            
            # Используем переменную окружения или дефолтный домен
            backend_url = os.getenv('MAIN_BACKEND_URL', 'https://maryrose.by')
            url = f"{backend_url}/meetings/internal/result"
            
            # Отправляем запрос
            logger.info(f"[{self.meeting_id}] Отправляю результаты на backend...")
            response = requests.post(
                url,
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

    def _save_chunk(self, audio_bytes: bytes):
        try:
            filename = f'chunk_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid4().hex[:6]}.wav'
            file_path = self.output_dir / filename
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
            write(str(file_path), STREAM_SAMPLE_RATE, audio_np)
            logger.info(f"[{self.meeting_id}] 💾 Фрагмент сохранен: {file_path} (длительность: {len(audio_np)/STREAM_SAMPLE_RATE:.2f} сек)")
        except Exception as e: logger.error(f"[{self.meeting_id}] ❌ Ошибка при сохранении аудиофрагмента: {e}")

    def run(self):
        """Основной метод, выполняющий всю работу."""
        logger.info(f"[{self.meeting_id}] Бот запускается...")
        try:
            self._initialize_driver()
            
            # Попытка присоединиться к встрече
            joined_successfully = self.join_meet_as_guest()
            
            if joined_successfully:
                logger.info(f"[{self.meeting_id}] Успешно вошел в конференцию, запускаю основные процессы.")
                device_id = self._find_device_id()

                processor_thread = threading.Thread(target=self._process_audio_stream)
                processor_thread.start()
                
                monitor_thread = threading.Thread(target=self._monitor_participants)
                monitor_thread.daemon = True
                monitor_thread.start()

                logger.info(f"[{self.meeting_id}] 🎤 Начинаю прослушивание аудио с устройства ID {device_id}...")
                with sd.RawInputStream(
                    samplerate=STREAM_SAMPLE_RATE,
                    blocksize=self.frame_size,
                    device=device_id,
                    dtype='int16',
                    channels=1,
                    callback=self._audio_capture_callback
                ):
                    processor_thread.join()
                
                logger.info(f"[{self.meeting_id}] Поток прослушивания остановлен.")
            else:
                logger.warning(f"[{self.meeting_id}] Не удалось присоединиться к встрече. Завершаю работу.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка в работе бота: {e}", exc_info=True)
        finally:
            self.stop()
            logger.info(f"[{self.meeting_id}] Бот полностью остановлен.")

    def stop(self):
        if not self.is_running.is_set():
            return
        
        logger.info(f"[{self.meeting_id}] Получена команда на завершение...")

        self.is_running.clear()
        
        # Запускаем постобработку только если бот успешно вошел в конференцию
        if self.joined_successfully:
            post_processing_thread = threading.Thread(target=self._perform_post_processing)
            post_processing_thread.daemon = False
            post_processing_thread.start()
        else:
            logger.info(f"[{self.meeting_id}] Пропускаю постобработку, так как вход в конференцию не был успешно завершен.")

        if self.driver:
            try:
                logger.info(f"[{self.meeting_id}] Закрытие WebDriver...")
                self.driver.quit()
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка при закрытии WebDriver: {e}")
        
        logger.info(f"[{self.meeting_id}] Сессия завершена.")