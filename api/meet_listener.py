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
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import shutil

# --- Блок конфигурации ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
load_dotenv()
MEET_LINK = os.getenv('MEET_LINK')
INPUT_DEVICE_NAME = os.getenv('INPUT_DEVICE_NAME')
OUTPUT_AUDIO_DIR = os.getenv('OUTPUT_AUDIO_DIR', 'audio_chunks')
GUEST_NAME = "Mary"  # Имя для входа как гость
SAMPLE_RATE = 16000
VAD_AGGRESSIVENESS = 3
FRAME_DURATION_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)
PAUSE_THRESHOLD_S = 0.8
SILENT_FRAMES_THRESHOLD = int(PAUSE_THRESHOLD_S * 1000 / FRAME_DURATION_MS)

class MeetListenerBot:
    def __init__(self, meeting_url: str):
        if not INPUT_DEVICE_NAME:
            logging.error("КРИТИЧЕСКАЯ ОШИБКА: Заполните .env файл (INPUT_DEVICE_NAME).")
            raise ValueError("Необходимые переменные окружения не установлены.")
        self.meeting_url = meeting_url
        self.driver = None
        self.audio_queue = queue.Queue()
        self.is_running = threading.Event()
        self.is_running.set()
        self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        os.makedirs(OUTPUT_AUDIO_DIR, exist_ok=True)
        logging.info(f"Аудиофрагменты будут сохраняться в папку: '{OUTPUT_AUDIO_DIR}'")

    def _monitor_participants(self):
        """
        Отслеживает количество участников в отдельном потоке. 
        Если бот остается один, он завершает работу.
        """
        threading.current_thread().name = 'ParticipantMonitor'
        logging.info("Мониторинг участников запущен. Проверка каждые 15 секунд.")

        # Самый надежный XPath: ищет кнопку, у которой есть иконка "people"
        # и атрибут aria-label, из которого мы извлекаем число.
        participant_locator_xpath = "//button[.//i[text()='people'] and @aria-label]"

        while self.is_running.is_set():
            try:
                # Ждем 15 секунд. Прерываем ожидание, если бот остановлен.
                for _ in range(15):
                    if not self.is_running.is_set():
                        logging.info("Мониторинг участников остановлен.")
                        return
                    time.sleep(1)

                try:
                    # Находим элемент-контейнер для участников
                    participant_element = self.driver.find_element(By.XPATH, participant_locator_xpath)
                    
                    # Извлекаем текст из aria-label, где содержится количество
                    aria_label_text = participant_element.get_attribute('aria-label') or ""

                    # Извлекаем все цифры из найденной строки
                    numbers = ''.join(filter(str.isdigit, aria_label_text))
                    
                    if numbers:
                        participant_count = int(numbers)
                        logging.info(f"Текущее количество участников: {participant_count}")
                        if participant_count <= 1:
                            logging.warning("Встреча пуста или остался только бот. Завершаю работу...")
                            self.stop()
                            return # Завершаем поток
                    else:
                        logging.warning(f"Найден элемент участников, но не удалось извлечь количество. Текст aria-label: '{aria_label_text}'")

                except Exception:
                    # Это не критическая ошибка, как вы и просили.
                    logging.warning("Не удалось найти счетчик участников на этой итерации.")

            except Exception as e:
                if self.is_running.is_set():
                    logging.error(f"Ошибка в потоке мониторинга участников: {e}")

    def _initialize_driver(self):
        logging.info("Запуск undetected_chromedriver с минимальными настройками и сохранением сессии...")
        
        try:
            logging.info("Попытка с автоматическими настройками и профилем...")
            opt = uc.ChromeOptions()
            opt.add_argument('--no-sandbox')
            opt.add_argument('--disable-dev-shm-usage')
            opt.add_argument('--user-data-dir=/app/chrome_profile') 
            
            self.driver = uc.Chrome(
                options=opt,
                headless=False,
                use_subprocess=True,
                no_sandbox=True,
                version_main=138
            )
            logging.info("✅ Chrome запущен с автоматическими настройками и профилем!")
            
            self.driver.execute_cdp_cmd('Page.setDownloadBehavior', {
                'behavior': 'allow',
                'downloadPath': '/app/audio_chunks'
            })
            
        except Exception as e:
            logging.error(f"Автоматический запуск не сработал: {e}")
            logging.info("Пробуем с базовыми ChromeOptions...")
            try:
                # Создаём НОВЫЙ объект ChromeOptions для второй попытки
                opt = uc.ChromeOptions()
                
                # ТОЛЬКО критически важные флаги
                opt.add_argument('--no-sandbox')
                opt.add_argument('--disable-dev-shm-usage')
                opt.add_argument('--disable-gpu')
                opt.add_argument('--user-data-dir=/app/chrome_profile')
                opt.add_argument('--window-size=1280,720')
                
                opt.add_experimental_option("prefs", {
                    "profile.default_content_setting_values.media_stream_mic": 1,
                    "profile.default_content_setting_values.notifications": 2
                })
                
                self.driver = uc.Chrome(options=opt, version_main=138)
                logging.info("✅ Chrome запущен с базовыми настройками!")
                
            except Exception as e2:
                logging.error(f"Полный провал запуска Chrome: {e2}")
                raise

    def join_meet_as_guest(self):
        try:
            # Переходим на встречу напрямую
            logging.info(f"Подключаюсь к встрече как гость: {self.meeting_url}")
            self.driver.get(self.meeting_url)
            time.sleep(8)
            
            # Скриншот после перехода на ссылку
            screenshot_path_1 = os.path.join(OUTPUT_AUDIO_DIR, f'debug_screenshot_meet_page_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
            try:
                self.driver.save_screenshot(screenshot_path_1)
                logging.info(f"Скриншот страницы встречи сохранен: {screenshot_path_1}")
            except Exception as e:
                logging.warning(f"Не удалось сохранить скриншот страницы встречи: {e}")
            
            # Ищем поле для ввода имени
            logging.info("Ищу поле для ввода имени...")
            name_input_xpath = '//input[@placeholder="Your name" or @aria-label="Your name" or contains(@placeholder, "name")]'
            name_input = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, name_input_xpath))
            )
            
            # Вводим имя "Mary"
            logging.info(f"Ввожу имя: {GUEST_NAME}")
            name_input.clear()
            name_input.send_keys(GUEST_NAME)
            time.sleep(2)
            
            # Скриншот после ввода имени
            screenshot_path_2 = os.path.join(OUTPUT_AUDIO_DIR, f'debug_screenshot_name_entered_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
            try:
                self.driver.save_screenshot(screenshot_path_2)
                logging.info(f"Скриншот после ввода имени сохранен: {screenshot_path_2}")
            except Exception as e:
                logging.warning(f"Не удалось сохранить скриншот после ввода имени: {e}")

            # НОВЫЙ ШАГ: Обработка диалогового окна о разрешении микрофона.
            # Это окно перекрывает кнопку "Ask to join", вызывая ошибку.
            try:
                logging.info("Проверяю наличие диалога о разрешении микрофона...")
                # Ищем кнопку по тексту, так как она может не иметь уникального ID
                continue_without_mic_xpath = '//button[.//span[contains(text(), "Continue without microphone")]]'
                
                # Ждем появления кнопки не более 5 секунд
                continue_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, continue_without_mic_xpath))
                )
                
                logging.info("Найдено окно разрешений. Нажимаю 'Продолжить без микрофона'...")
                continue_button.click()
                time.sleep(2) # Небольшая пауза, чтобы окно успело закрыться
                logging.info("Диалог о микрофоне успешно закрыт.")

            except Exception:
                # Если кнопка не найдена за 5 секунд, значит, диалога не было. Это не ошибка.
                logging.info("Диалог о разрешении микрофона не найден, продолжаю выполнение.")
            
            # Ищем и нажимаем кнопку "Ask to join"
            join_button_xpath = '//button[.//span[contains(text(), "Ask to join") or contains(text(), "Попросить войти")]]'
            logging.info("Ищу кнопку 'Ask to join'...")
            join_button = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, join_button_xpath))
            )
            join_button.click()
            
            # Скриншот после нажатия кнопки
            screenshot_path_3 = os.path.join(OUTPUT_AUDIO_DIR, f'debug_screenshot_after_ask_join_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
            try:
                self.driver.save_screenshot(screenshot_path_3)
                logging.info(f"Скриншот после нажатия 'Ask to join' сохранен: {screenshot_path_3}")
            except Exception as e:
                logging.warning(f"Не удалось сохранить скриншот после нажатия кнопки: {e}")
            
            # Ожидаем одобрения от хоста
            logging.info("Запрос на присоединение отправлен. Ожидаю одобрения от хоста...")
            
            # Умное ожидание одобрения с проверкой каждые 2 секунды
            max_wait_time = 120  # Максимум 2 минуты ожидания
            check_interval = 2   # Проверяем каждые 2 секунды
            elapsed_time = 0
            
            # Различные индикаторы успешного присоединения
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
            
            while elapsed_time < max_wait_time:
                logging.info(f"Проверяю статус присоединения... (прошло {elapsed_time}с из {max_wait_time}с)")
                
                # Проверяем каждый индикатор
                for i, xpath in enumerate(success_indicators):
                    try:
                        element = self.driver.find_element(By.XPATH, xpath)
                        if element and element.is_displayed():
                            # Скриншот после успешного присоединения
                            screenshot_path_4 = os.path.join(OUTPUT_AUDIO_DIR, f'debug_screenshot_joined_successfully_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
                            try:
                                self.driver.save_screenshot(screenshot_path_4)
                                logging.info(f"Скриншот успешного присоединения сохранен: {screenshot_path_4}")
                            except Exception as e:
                                logging.warning(f"Не удалось сохранить скриншот успешного присоединения: {e}")
                            
                            logging.info(f"✅ Успешно присоединился к встрече! (найден индикатор #{i+1})")
                            return
                    except:
                        continue
                
                # Проверяем, не появилось ли сообщение об ошибке или отказе
                error_indicators = [
                    '//*[contains(text(), "denied") or contains(text(), "отклонен")]',
                    '//*[contains(text(), "rejected") or contains(text(), "отказано")]',
                    '//*[contains(text(), "error") or contains(text(), "ошибка")]',
                    '//*[contains(text(), "unable") or contains(text(), "невозможно")]'
                ]
                
                for error_xpath in error_indicators:
                    try:
                        error_element = self.driver.find_element(By.XPATH, error_xpath)
                        if error_element and error_element.is_displayed():
                            logging.error(f"Обнаружено сообщение об ошибке или отказе: {error_element.text}")
                            screenshot_path_error = os.path.join(OUTPUT_AUDIO_DIR, f'debug_screenshot_join_error_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
                            try:
                                self.driver.save_screenshot(screenshot_path_error)
                                logging.error(f"Скриншот ошибки сохранен: {screenshot_path_error}")
                            except:
                                pass
                            raise Exception(f"Присоединение отклонено: {error_element.text}")
                    except:
                        continue
                
                # Ждем перед следующей проверкой
                time.sleep(check_interval)
                elapsed_time += check_interval
                
                # Каждые 30 секунд делаем промежуточный скриншот
                if elapsed_time % 30 == 0:
                    screenshot_path_wait = os.path.join(OUTPUT_AUDIO_DIR, f'debug_screenshot_waiting_{elapsed_time}s_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
                    try:
                        self.driver.save_screenshot(screenshot_path_wait)
                        logging.info(f"Промежуточный скриншот ожидания сохранен: {screenshot_path_wait}")
                    except:
                        pass
            
            # Если время ожидания истекло
            logging.warning(f"Превышено время ожидания одобрения ({max_wait_time}с). Попробую продолжить...")
            
            # Делаем финальный скриншот
            screenshot_path_5 = os.path.join(OUTPUT_AUDIO_DIR, f'debug_screenshot_timeout_state_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
            try:
                self.driver.save_screenshot(screenshot_path_5)
                logging.info(f"Скриншот по истечении времени ожидания сохранен: {screenshot_path_5}")
            except Exception as e:
                logging.warning(f"Не удалось сохранить финальный скриншот: {e}")
            
            logging.info("⚠️ Присоединение к встрече завершено (возможно, все еще ожидаю одобрения)")
            
        except Exception as e:
            screenshot_path = os.path.join(OUTPUT_AUDIO_DIR, f'debug_screenshot_guest_join_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
            try:
                self.driver.save_screenshot(screenshot_path)
                logging.error(f"Ошибка при присоединении как гость: {e}. Скриншот сохранен в {screenshot_path}")
            except Exception as screenshot_error:
                logging.error(f"Ошибка при присоединении как гость: {e}. Не удалось сохранить скриншот: {screenshot_error}")
            raise

    def _find_device_id(self):
        logging.info(f"Поиск аудиоустройства с именем, содержащим '{INPUT_DEVICE_NAME}'...")
        try:
            time.sleep(2)
            devices = sd.query_devices()
            logging.debug(f"Найденные аудиоустройства: {devices}")
            for i, device in enumerate(devices):
                if INPUT_DEVICE_NAME in device['name'] and device['max_input_channels'] > 0:
                    logging.info(f"✅ Найдено целевое устройство: ID {i}, Имя: {device['name']}")
                    return i
            raise ValueError(f"Не удалось найти входное аудиоустройство с именем '{INPUT_DEVICE_NAME}'")
        except Exception as e:
            logging.error(f"Ошибка при поиске аудиоустройств: {e}")
            raise

    def _audio_capture_callback(self, indata, frames, time, status):
        if status: logging.warning(f"Статус аудиоустройства: {status}")
        if self.is_running.is_set(): self.audio_queue.put(bytes(indata))

    def _process_audio_stream(self):
        threading.current_thread().name = 'VADProcessor'
        logging.info("Процессор VAD запущен. Ожидание аудиоданных...")
        speech_buffer = []
        silent_frames_count = 0
        while self.is_running.is_set():
            try:
                audio_frame = self.audio_queue.get(timeout=1)
                is_speech = self.vad.is_speech(audio_frame, SAMPLE_RATE)
                if is_speech:
                    speech_buffer.append(audio_frame)
                    silent_frames_count = 0
                else:
                    silent_frames_count += 1
                if speech_buffer and silent_frames_count > SILENT_FRAMES_THRESHOLD:
                    full_speech_chunk_bytes = b''.join(speech_buffer)
                    speech_buffer.clear()
                    silent_frames_count = 0
                    threading.Thread(target=self._save_and_process_chunk, args=(full_speech_chunk_bytes,)).start()
            except queue.Empty: continue
            except Exception as e: logging.error(f"Ошибка в цикле обработки VAD: {e}")

    def _save_and_process_chunk(self, audio_bytes: bytes):
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            unique_id = str(uuid4()).split('-')[0]
            filename = f"speech_chunk_{timestamp}_{unique_id}.wav"
            file_path = os.path.join(OUTPUT_AUDIO_DIR, filename)
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
            write(file_path, SAMPLE_RATE, audio_np)
            logging.info(f"💾 Фрагмент речи сохранен: {file_path} (длительность: {len(audio_np)/SAMPLE_RATE:.2f} сек)")
        except Exception as e: logging.error(f"Ошибка при сохранении аудиофрагмента: {e}")

    def start(self):
        self._initialize_driver()
        self.join_meet_as_guest()
        device_id = self._find_device_id()

        processor_thread = threading.Thread(target=self._process_audio_stream)
        processor_thread.start()

        # Запускаем поток мониторинга участников как демон-поток
        monitor_thread = threading.Thread(target=self._monitor_participants)
        monitor_thread.daemon = True
        monitor_thread.start()

        logging.info(f"Начинаю прослушивание аудиопотока с устройства ID {device_id}...")
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SIZE, device=device_id, dtype='int16', channels=1, callback=self._audio_capture_callback):
            processor_thread.join()

    def stop(self):
        logging.info("Получена команда на завершение...")
        self.is_running.clear()
        if self.driver:
            logging.info("Закрытие WebDriver...")
            self.driver.quit()
        logging.info("Программа завершена.")

if __name__ == "__main__":
    # Этот блок теперь предназначен только для прямого тестирования скрипта,
    # а не для запуска через FastAPI.
    # Убедитесь, что MEET_LINK в .env файле установлен для этого режима.
    if not os.getenv('MEET_LINK'):
        print("Для прямого запуска, пожалуйста, установите MEET_LINK в вашем .env файле.")
    else:
        bot = None
        try:
            bot = MeetListenerBot(meeting_url=os.getenv('MEET_LINK'))
            bot.start()
        except (KeyboardInterrupt, SystemExit): logging.info("Программа прервана пользователем.")
        except Exception as e: logging.critical(f"Непредвиденная ошибка в главном потоке: {e}")
        finally:
            if bot: bot.stop()