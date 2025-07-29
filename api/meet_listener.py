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
            logger.info(f"[{self.meeting_id}] Попытка с автоматическими настройками и профилем...")
            opt = uc.ChromeOptions()
            opt.add_argument('--no-sandbox')
            opt.add_argument('--disable-dev-shm-usage')
            opt.add_argument(f'--user-data-dir={config.CHROME_PROFILE_DIR}')
            
            self.driver = uc.Chrome(
                options=opt,
                use_subprocess=True,
                version_main=138 # Закрепляем версию для стабильности
            )
            logger.info(f"[{self.meeting_id}] Chrome запущен с автоматическими настройками!")
            
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Автоматический запуск не сработал: {e}")
            logger.info(f"[{self.meeting_id}] Пробуем с базовыми ChromeOptions...")
            try:
                opt = uc.ChromeOptions()
                opt.add_argument('--no-sandbox')
                opt.add_argument('--disable-dev-shm-usage')
                opt.add_argument('--disable-gpu')
                opt.add_argument(f'--user-data-dir={config.CHROME_PROFILE_DIR}')
                opt.add_argument('--window-size=1280,720')
                
                opt.add_experimental_option("prefs", {
                    "profile.default_content_setting_values.media_stream_mic": 1,
                    "profile.default_content_setting_values.notifications": 2
                })
                
                self.driver = uc.Chrome(options=opt, version_main=138)
                logger.info(f"[{self.meeting_id}] Chrome запущен с базовыми настройками!")
                
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

    def join_meet_as_guest(self):
        try:
            logger.info(f"[{self.meeting_id}] Подключаюсь к встрече как гость: {self.meeting_url}")
            self.driver.get(self.meeting_url)
            time.sleep(8)
            self._save_screenshot("01_meet_page")
            
            logger.info(f"[{self.meeting_id}] Ищу поле для ввода имени...")
            name_input_xpath = '//input[@placeholder="Your name" or @aria-label="Your name" or contains(@placeholder, "name")]'
            name_input = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, name_input_xpath))
            )
            
            logger.info(f"[{self.meeting_id}] Ввожу имя: {config.MEET_GUEST_NAME}")
            name_input.clear()
            name_input.send_keys(config.MEET_GUEST_NAME)
            time.sleep(2)
            self._save_screenshot("02_name_entered")

            # Обработка диалогового окна о разрешении микрофона
            try:
                logger.info(f"[{self.meeting_id}] Проверяю наличие диалога о разрешении микрофона...")
                continue_without_mic_xpath = '//button[.//span[contains(text(), "Continue without microphone")]]'
                continue_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, continue_without_mic_xpath))
                )
                logger.info(f"[{self.meeting_id}] Нажимаю 'Продолжить без микрофона'...")
                continue_button.click()
                time.sleep(2)
                self._save_screenshot("02a_mic_dialog_closed")
            except Exception:
                logger.info(f"[{self.meeting_id}] Диалог о разрешении микрофона не найден, продолжаю.")
            
            # Нажатие кнопки "Попросить войти"
            join_button_xpath = '//button[.//span[contains(text(), "Ask to join") or contains(text(), "Попросить войти")]]'
            logger.info(f"[{self.meeting_id}] Ищу кнопку 'Ask to join'...")
            join_button = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, join_button_xpath))
            )
            join_button.click()
            self._save_screenshot("03_after_ask_join")
            
            logger.info(f"[{self.meeting_id}] Запрос на присоединение отправлен. Ожидаю одобрения хоста...")
            
            # Умное ожидание одобрения (ПОЛНАЯ ВЕРСИЯ ИЗ ОРИГИНАЛА)
            max_wait_time = 120
            check_interval = 2
            elapsed_time = 0
            
            success_indicators = [
                # Кнопки управления встречей
                '//button[@data-tooltip*="microphone" or @aria-label*="microphone" or @aria-label*="микрофон"]',
                '//button[@data-tooltip*="camera" or @aria-label*="camera" or @aria-label*="камера"]', 
                '//button[@data-tooltip*="end call" or @aria-label*="end call" or @aria-label*="завершить"]',
                # Иконки материал дизайна
                '//*[contains(@class, "google-material-icons") and (text()="mic" or text()="mic_off")]',
                '//*[contains(@class, "google-material-icons") and (text()="videocam" or text()="videocam_off")]',
                # Элементы интерфейса встречи
                '//div[@data-self-name]',
                '//div[contains(@class, "participant") or contains(@class, "Participant")]',
                # Панель управления внизу
                '//div[contains(@class, "control") and (contains(@class, "bar") or contains(@class, "panel"))]',
                # Кнопка "Поднять руку" или меню
                '//button[@aria-label*="hand" or @aria-label*="рука" or @data-tooltip*="hand"]',
                # Индикатор количества участников
                '//*[contains(text(), "participant") or contains(text(), "участник")]'
            ]
            
            error_indicators = [
                '//*[contains(text(), "denied") or contains(text(), "отклонен")]',
                '//*[contains(text(), "rejected") or contains(text(), "отказано")]',
                '//*[contains(text(), "error") or contains(text(), "ошибка")]',
                '//*[contains(text(), "unable") or contains(text(), "невозможно")]'
            ]

            while elapsed_time < max_wait_time:
                logger.info(f"[{self.meeting_id}] Проверяю статус присоединения... ({elapsed_time}с/{max_wait_time}с)")
                
                # Проверка на успех
                for i, xpath in enumerate(success_indicators):
                    try:
                        if self.driver.find_element(By.XPATH, xpath).is_displayed():
                            self._save_screenshot("04_joined_successfully")
                            logger.info(f"[{self.meeting_id}] Успешно присоединился к встрече! (индикатор #{i+1})")
                            return
                    except:
                        continue
                
                # Проверка на отказ
                for error_xpath in error_indicators:
                    try:
                        error_element = self.driver.find_element(By.XPATH, error_xpath)
                        if error_element.is_displayed():
                            logger.error(f"[{self.meeting_id}] Обнаружено сообщение об отказе/ошибке: {error_element.text}")
                            self._save_screenshot("98_join_denied_or_error")
                            raise Exception(f"Присоединение отклонено или произошла ошибка: {error_element.text}")
                    except:
                        pass

                time.sleep(check_interval)
                elapsed_time += check_interval
            
            logger.warning(f"[{self.meeting_id}] Превышено время ожидания одобрения ({max_wait_time}с).")
            self._save_screenshot("99_join_timeout")
            raise Exception("Превышено время ожидания одобрения хостом.")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] Критическая ошибка при присоединении как гость: {e}", exc_info=True)
            self._save_screenshot("99_join_fatal_error")
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