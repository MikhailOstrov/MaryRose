# api/meet_listener.py
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
import config

# Используем стандартный логгер, настроенный в server.py
logger = logging.getLogger(__name__)

class MeetListenerBot:
    """
    Класс для управления ботом, который подключается к Google Meet,
    слушает аудиопоток и сохраняет его в виде фрагментов (чанков).
    """
    def __init__(self, meeting_url: str, meeting_id: str):
        self.meeting_url = meeting_url
        self.meeting_id = meeting_id # ID для отслеживания сессии
        self.driver = None
        self.audio_queue = queue.Queue()
        self.is_running = threading.Event()
        self.is_running.set()
        self.vad = webrtcvad.Vad(config.MEET_VAD_AGGRESSIVENESS)

        # Рассчитываем параметры VAD на основе конфига
        self.frame_size = int(config.STREAM_SAMPLE_RATE * config.MEET_FRAME_DURATION_MS / 1000)
        self.silent_frames_threshold = int(config.MEET_PAUSE_THRESHOLD_S * 1000 / config.MEET_FRAME_DURATION_MS)

        # Папка для сохранения чанков и скриншотов
        self.output_dir = config.MEET_AUDIO_CHUNKS_DIR / self.meeting_id
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

    def _initialize_driver(self):
        logger.info(f"[{self.meeting_id}] Запуск undetected_chromedriver...")
        try:
            options = uc.ChromeOptions()
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument(f'--user-data-dir={config.CHROME_PROFILE_DIR}')
            options.add_argument('--window-size=1280,720')
            options.add_argument("--autoplay-policy=no-user-gesture-required")
            
            # Отключаем запросы на уведомления и микрофон в самом Chrome
            options.add_experimental_option("prefs", {
                "profile.default_content_setting_values.media_stream_mic": 2, # 1=Allow, 2=Block
                "profile.default_content_setting_values.notifications": 2
            })

            self.driver = uc.Chrome(options=options, use_subprocess=True, headless=False)
            logger.info(f"[{self.meeting_id}] Chrome запущен успешно.")
        except Exception as e:
            logger.critical(f"[{self.meeting_id}] Полный провал запуска Chrome: {e}", exc_info=True)
            raise

    def _save_screenshot(self, name: str):
        """Сохраняет скриншот для отладки."""
        path = self.output_dir / f'{datetime.now().strftime("%H%M%S")}_{name}.png'
        try:
            self.driver.save_screenshot(str(path))
            logger.info(f"[{self.meeting_id}] Скриншот сохранен: {path}")
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось сохранить скриншот '{name}': {e}")

    def join_meet_as_guest(self):
        try:
            logger.info(f"[{self.meeting_id}] Подключаюсь к встрече: {self.meeting_url}")
            self.driver.get(self.meeting_url)
            self._save_screenshot("01_page_loaded")

            # Ввод имени
            name_input_xpath = '//input[@placeholder="Your name" or @aria-label="Your name"]'
            name_input = WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.XPATH, name_input_xpath)))
            name_input.clear()
            name_input.send_keys(config.MEET_GUEST_NAME)
            self._save_screenshot("02_name_entered")

            # Кнопка "Попросить войти"
            join_button_xpath = '//button[.//span[contains(text(), "Ask to join")]]'
            join_button = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, join_button_xpath)))
            join_button.click()
            self._save_screenshot("03_ask_to_join_clicked")

            logger.info(f"[{self.meeting_id}] Запрос на присоединение отправлен. Ожидаю одобрения...")
            
            # Ожидание индикатора успешного входа
            success_indicator_xpath = '//button[@data-tooltip*="microphone"]'
            WebDriverWait(self.driver, 120).until(EC.presence_of_element_located((By.XPATH, success_indicator_xpath)))
            self._save_screenshot("04_joined_successfully")
            logger.info(f"[{self.meeting_id}] Успешно присоединился к встрече!")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при присоединении как гость: {e}", exc_info=True)
            self._save_screenshot("99_join_error")
            raise

    def _find_device_id(self):
        logger.info(f"[{self.meeting_id}] Поиск аудиоустройства с именем '{config.MEET_INPUT_DEVICE_NAME}'...")
        try:
            devices = sd.query_devices()
            for i, device in enumerate(devices):
                if config.MEET_INPUT_DEVICE_NAME in device['name'] and device['max_input_channels'] > 0:
                    logger.info(f"[{self.meeting_id}] Найдено целевое устройство: ID {i}, Имя: {device['name']}")
                    return i
            raise ValueError(f"Не удалось найти входное аудиоустройство с именем '{config.MEET_INPUT_DEVICE_NAME}'")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при поиске аудиоустройств: {e}", exc_info=True)
            raise

    def _audio_capture_callback(self, indata, frames, time, status):
        if status: logger.warning(f"[{self.meeting_id}] Статус аудиоустройства: {status}")
        if self.is_running.is_set(): self.audio_queue.put(bytes(indata))

    def _process_audio_stream(self):
        threading.current_thread().name = f'VADProcessor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Процессор VAD запущен.")
        speech_buffer = []
        silent_frames_count = 0
        while self.is_running.is_set():
            try:
                audio_frame = self.audio_queue.get(timeout=1)
                is_speech = self.vad.is_speech(audio_frame, config.STREAM_SAMPLE_RATE)
                if is_speech:
                    speech_buffer.append(audio_frame)
                    silent_frames_count = 0
                else:
                    silent_frames_count += 1
                if speech_buffer and silent_frames_count > self.silent_frames_threshold:
                    full_speech_chunk_bytes = b''.join(speech_buffer)
                    speech_buffer.clear()
                    silent_frames_count = 0
                    # Сохранение вынесено в отдельный поток, чтобы не блокировать VAD
                    threading.Thread(target=self._save_chunk, args=(full_speech_chunk_bytes,)).start()
            except queue.Empty: continue
            except Exception as e: logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}")

    def _save_chunk(self, audio_bytes: bytes):
        try:
            filename = f'chunk_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid4().hex[:6]}.wav'
            file_path = self.output_dir / filename
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
            write(str(file_path), config.STREAM_SAMPLE_RATE, audio_np)
            logger.info(f"[{self.meeting_id}] Фрагмент сохранен: {file_path} (длительность: {len(audio_np)/config.STREAM_SAMPLE_RATE:.2f} сек)")
        except Exception as e: logger.error(f"[{self.meeting_id}] Ошибка при сохранении аудиофрагмента: {e}")

    def start(self):
        """Основной метод запуска бота в отдельном потоке."""
        main_thread = threading.Thread(target=self._run)
        main_thread.daemon = True
        main_thread.start()

    def _run(self):
        """Внутренний метод, выполняющий всю работу."""
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

            logger.info(f"[{self.meeting_id}] Начинаю прослушивание аудио с устройства ID {device_id}...")
            with sd.RawInputStream(
                samplerate=config.STREAM_SAMPLE_RATE,
                blocksize=self.frame_size,
                device=device_id,
                dtype='int16',
                channels=1,
                callback=self._audio_capture_callback
            ):
                # Поток будет жить, пока is_running не будет сброшен
                self.is_running.wait()
            
            processor_thread.join() # Дожидаемся завершения обработчика
            logger.info(f"[{self.meeting_id}] Поток прослушивания остановлен.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] Критическая ошибка в работе бота: {e}", exc_info=True)
        finally:
            self.stop()
            logger.info(f"[{self.meeting_id}] Бот полностью остановлен.")


    def stop(self):
        if not self.is_running.is_set():
            return # Уже остановлен
        
        logger.info(f"[{self.meeting_id}] Получена команда на завершение...")
        self.is_running.clear() # Сигнализируем всем потокам о завершении
        
        if self.driver:
            try:
                logger.info(f"[{self.meeting_id}] Закрытие WebDriver...")
                self.driver.quit()
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка при закрытии WebDriver: {e}")
        
        logger.info(f"[{self.meeting_id}] Сессия завершена.")