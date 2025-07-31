import os
import time
import queue
import threading
import logging
from datetime import datetime
from uuid import uuid4
import webrtcvad
import numpy as np
from scipy.io.wavfile import write
import sounddevice as sd
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Импортируем конфигурацию из основного файла
from config.config import (STREAM_SAMPLE_RATE,MEET_FRAME_DURATION_MS,
                           MEET_VAD_AGGRESSIVENESS, MEET_PAUSE_THRESHOLD_S, 
                           MEET_AUDIO_CHUNKS_DIR, MEET_INPUT_DEVICE_NAME,
                           CHROME_PROFILE_DIR, MEET_GUEST_NAME, SUMMARY_OUTPUT_DIR)
from handlers.stt_handler import transcribe_chunk
from handlers.ollama_handler import get_mary_response, get_summary_response
from handlers.diarization_handler import run_diarization, process_rttm_and_transcribe
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
        self.vad = webrtcvad.Vad(MEET_VAD_AGGRESSIVENESS)
        self.summary_output_dir = SUMMARY_OUTPUT_DIR
        # Рассчитываем параметры VAD на основе конфига
        self.frame_size = int(STREAM_SAMPLE_RATE * MEET_FRAME_DURATION_MS / 1000)
        self.silent_frames_threshold = int(MEET_PAUSE_THRESHOLD_S * 1000 / MEET_FRAME_DURATION_MS)

        # Папка для сохранения чанков и скриншотов
        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Аудиофрагменты будут сохраняться в: '{self.output_dir}'")

    def _monitor_participants(self):
        """Отслеживает количество участников. Если бот остается один, он завершает работу."""
        threading.current_thread().name = f'ParticipantMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг участников запущен.")
        
        participant_locator_xpath = "//button[.//i[text()='people'] and @aria-label]"

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
                    if count <= 1:
                        logger.warning(f"[{self.meeting_id}] Встреча пуста. Завершаю работу...")
                        self.stop()
                        return
            except Exception:
                logger.warning(f"[{self.meeting_id}] Не удалось найти счетчик участников на этой итерации.")
    
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
            
            # ПОЛНЫЙ И НАДЕЖНЫЙ СПИСОК ИНДИКАТОРОВ УСПЕХА ИЗ ОРИГИНАЛА
            success_indicators = [
                # Кнопки управления встречей (включая русские)
                '//button[@data-tooltip*="microphone" or @aria-label*="microphone" or @aria-label*="микрофон"]',
                '//button[@data-tooltip*="camera" or @aria-label*="camera" or @aria-label*="камера"]', 
                '//button[@data-tooltip*="end call" or @aria-label*="end call" or @aria-label*="завершить"]',
                # Иконки Google
                '//*[contains(@class, "google-material-icons") and (text()="mic" or text()="mic_off")]',
                '//*[contains(@class, "google-material-icons") and (text()="videocam" or text()="videocam_off")]',
                # Другие элементы интерфейса
                '//div[@data-self-name]',
                '//div[contains(@class, "participant") or contains(@class, "Participant")]',
                '//div[contains(@class, "control") and (contains(@class, "bar") or contains(@class, "panel"))]',
                '//button[@aria-label*="hand" or @aria-label*="рука" or @data-tooltip*="hand"]',
                '//*[contains(text(), "participant") or contains(text(), "участник")]'
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
                            return
                    except: continue
                
                for error_xpath in error_indicators:
                    try:
                        error_element = self.driver.find_element(By.XPATH, error_xpath)
                        if error_element.is_displayed():
                            logger.error(f"[{self.meeting_id}] ❌ Присоединение отклонено: {error_element.text}")
                            self._save_screenshot("98_join_denied")
                            raise Exception(f"Присоединение отклонено или произошла ошибка: {error_element.text}")
                    except: continue

                time.sleep(check_interval)
                elapsed_time += check_interval
                if elapsed_time % 30 == 0:
                    logger.info(f"[{self.meeting_id}] Ожидание... {elapsed_time}с прошло.")
                    self._save_screenshot(f"wait_{elapsed_time}s")

            logger.warning(f"[{self.meeting_id}] ⚠️ Превышено время ожидания одобрения ({max_wait_time}с).")
            self._save_screenshot("99_join_timeout")
            raise Exception("Превышено время ожидания одобрения хостом.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка при присоединении: {e}", exc_info=True)
            self._save_screenshot("99_join_fatal_error")
            raise

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
                    print(transcription)
                    if trigger_word == 1:
                        print("Обнаружено слово-триггер")
                        response = get_mary_response(transcription)
                        print(response)

                        # 
                        # !!!Можно вывести в GMeet в чат пока что.
                        #

            except queue.Empty: continue
            except Exception as e: logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}")

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
                output_filename=combined_audio_filename
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

            # Суммаризация
            logger.info(f"[{self.meeting_id}] Создание резюме...")
            summary_text = get_summary_response(dialogue_transcript)
            print(f"Это вывод summary: \n{summary_text} соси")
            
            # Сохранение резюме
            summary_filename = f"summary_{self.meeting_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            summary_filepath = self.summary_output_dir / summary_filename
            with open(summary_filepath, "w", encoding="utf-8") as f:
                f.write(summary_text)
            logger.info(f"[{self.meeting_id}] ✅ Резюме успешно сохранено в: '{summary_filepath}'")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")

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
        threading.current_thread().name = f'MeetBot-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Бот запускается...")
        try:
            self._initialize_driver()
            self.join_meet_as_guest()
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
                # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: как в рабочем оригинале
                processor_thread.join()
            
            logger.info(f"[{self.meeting_id}] Поток прослушивания остановлен.")

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
        
        post_processing_thread = threading.Thread(target=self._perform_post_processing)
        post_processing_thread.daemon = False
        post_processing_thread.start()

        if self.driver:
            try:
                logger.info(f"[{self.meeting_id}] Закрытие WebDriver...")
                self.driver.quit()
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка при закрытии WebDriver: {e}")
        
        logger.info(f"[{self.meeting_id}] Сессия завершена.")