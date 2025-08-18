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
import soundfile as sf
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
import subprocess

from config.config import (STREAM_SAMPLE_RATE,SILENCE_THRESHOLD_FRAMES, MEET_FRAME_DURATION_MS,
                           MEET_AUDIO_CHUNKS_DIR, MEET_INPUT_DEVICE_NAME, STREAM_TRIGGER_WORD, CHROME_PROFILE_DIR,
                           MEET_GUEST_NAME, SUMMARY_OUTPUT_DIR, STREAM_STOP_WORD_1, STREAM_STOP_WORD_2, STREAM_STOP_WORD_3)
from handlers.llm_handler import get_mary_response, get_summary_response, get_title_response
from handlers.diarization_handler import run_diarization, process_rttm_and_transcribe
from config.load_models import create_new_vad_model, asr_model, create_new_tts_model
from api.utils import combine_audio_chunks
from handlers.tts_handler import synthesize_speech_to_bytes
from api.audio_manager import VirtualAudioManager
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

CHROME_LAUNCH_LOCK = threading.Lock()

class MeetListenerBot:

    # Определение атрибутов класса
    def __init__(self, meeting_url: str, meeting_id: str):

        self.meeting_url = meeting_url # Ссылка на Google Meet
        self.meeting_id = meeting_id # ID для отслеживания сессии
        self.driver = None 
        self.audio_queue = queue.Queue() # Для аудиопотока

        self.is_running = threading.Event()
        self.is_running.set()

        self.vad = create_new_vad_model()
        self.tts_model = create_new_tts_model()
        self.asr_model = asr_model # Whisper (from config.load_models import asr_model)
        self.summary_output_dir = SUMMARY_OUTPUT_DIR # Директория сохранения summary
        self.joined_successfully = False 

        self.frame_size = int(STREAM_SAMPLE_RATE * MEET_FRAME_DURATION_MS / 1000) # Для VAD-модели (длительность чанка)
        self.silent_frames_threshold = SILENCE_THRESHOLD_FRAMES # Пауза в речи в сек.

        # --- ИЗМЕНЕНИЕ 1: Создание уникальных путей для изоляции ---
        # Уникальная директория для аудио-чанков этой сессии
        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id 
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Аудиофрагменты будут сохраняться в: '{self.output_dir}'")
        
        # Уникальная директория для профиля Chrome этой сессии
        self.chrome_profile_path = Path(CHROME_PROFILE_DIR) / self.meeting_id
        # Гарантированно очищаем старый профиль, если он остался от предыдущего сбойного запуска
        if self.chrome_profile_path.exists():
            shutil.rmtree(self.chrome_profile_path)
        os.makedirs(self.chrome_profile_path, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Временный профиль Chrome создан в: '{self.chrome_profile_path}'")
        # --- КОНЕЦ ИЗМЕНЕНИЯ 1 ---

        # Управление автоозвучкой
        self.enable_auto_tts = True
        
        # Инициализация нашего менеджера аудиоустройств
        self.audio_manager = VirtualAudioManager(self.meeting_id)
        # Эти имена будут использоваться для привязки Chrome и воспроизведения звука
        self.sink_name = self.audio_manager.sink_name
        self.source_name = self.audio_manager.source_name
        self.monitor_name = self.audio_manager.monitor_name

    # Отслеживание кол-ва участников
    def _monitor_participants(self):
        """Отслеживает количество участников. Если бот остается один, он завершает работу."""
        threading.current_thread().name = f'ParticipantMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг участников запущен.")
        
        participant_locator_xpath = "//button[.//i[text()='people'] and @aria-label]"
        consecutive_failures = 0
        max_failures = 2

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
    
    # Инициализация драйвера для подключения
    def _initialize_driver(self):
        """
        Инициализирует драйвер с ПОЛНОЙ ИЗОЛЯЦИЕЙ:
        1. Использует уникальную копию бинарного файла chromedriver.
        2. Использует уникальный порт для remote debugging.
        3. Использует переменные окружения PulseAudio для изоляции звука.
        """
        logger.info(f"[{self.meeting_id}] Полная изоляция и запуск Chrome...")

        # --- ШАГ 1: Создаем уникальную копию chromedriver ---
        # Это предотвратит конфликт, когда несколько ботов пытаются патчить один и тот же файл
        try:
            # Находим путь к оригинальному, кэшированному chromedriver
            patcher = Patcher()
            original_driver_path = patcher.executable_path
            
            # Создаем уникальный путь для копии драйвера этого бота
            driver_copy_path = self.chrome_profile_path / "chromedriver"
            shutil.copy(original_driver_path, driver_copy_path)
            
            logger.info(f"[{self.meeting_id}] Создана изолированная копия chromedriver в: {driver_copy_path}")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Не удалось создать копию chromedriver: {e}. Продолжаю с драйвером по умолчанию.")
            driver_copy_path = None


        # --- ШАГ 2: Блокировка для безопасного изменения os.environ ---
        # Эта часть остается, так как изменение env переменных - глобальная операция
        with CHROME_LAUNCH_LOCK:
            logger.info(f"[{self.meeting_id}] Блокировка получена. Настройка PulseAudio env vars...")
            
            original_pulse_sink = os.environ.get('PULSE_SINK')
            original_pulse_source = os.environ.get('PULSE_SOURCE')
            
            os.environ['PULSE_SINK'] = self.sink_name
            os.environ['PULSE_SOURCE'] = self.source_name
            
            logger.info(f"[{self.meeting_id}] Запуск Chrome с PULSE_SINK='{self.sink_name}' и PULSE_SOURCE='{self.source_name}'...")
            
            try:
                opt = uc.ChromeOptions()
                opt.add_argument('--no-sandbox')
                opt.add_argument('--disable-dev-shm-usage')
                opt.add_argument('--window-size=1280,720')
                opt.add_argument(f'--user-data-dir={self.chrome_profile_path}')
                
                # --- ШАГ 3: Используем уникальный порт ---
                # Это дополнительная мера гигиены для предотвращения конфликтов
                port = random.randint(10000, 20000)
                opt.add_argument(f'--remote-debugging-port={port}')
                logger.info(f"[{self.meeting_id}] Используется порт для отладки: {port}")

                opt.add_experimental_option("prefs", {
                    "profile.default_content_setting_values.media_stream_mic": 1,
                    "profile.default_content_setting_values.notifications": 2
                })
                
                self.driver = uc.Chrome(
                    options=opt,
                    headless=False,
                    use_subprocess=True,
                    version_main=138,
                    # --- ШАГ 4: Указываем путь к НАШЕЙ КОПИИ драйвера ---
                    driver_executable_path=str(driver_copy_path) if driver_copy_path else None
                )
                
                logger.info(f"[{self.meeting_id}] ✅ Chrome успешно запущен с полной изоляцией.")
                
                try:
                    self.driver.execute_cdp_cmd("Browser.grantPermissions", {
                        "origin": "https://meet.google.com",
                        "permissions": ["audioCapture"]
                    })
                    logger.info(f"[{self.meeting_id}] Разрешение на микрофон выдано через CDP.")
                except Exception as e_grant:
                    logger.warning(f"[{self.meeting_id}] Не удалось выдать CDP-разрешение: {e_grant}")

            except Exception as e:
                logger.critical(f"[{self.meeting_id}] ❌ Полный провал запуска Chrome: {e}", exc_info=True)
                raise
            finally:
                # --- Гарантированно очищаем переменные окружения ---
                logger.info(f"[{self.meeting_id}] Очистка переменных окружения PulseAudio.")
                if original_pulse_sink is None:
                    if 'PULSE_SINK' in os.environ: del os.environ['PULSE_SINK']
                else:
                    os.environ['PULSE_SINK'] = original_pulse_sink
                
                if original_pulse_source is None:
                    if 'PULSE_SOURCE' in os.environ: del os.environ['PULSE_SOURCE']
                else:
                    os.environ['PULSE_SOURCE'] = original_pulse_source
        
        logger.info(f"[{self.meeting_id}] Блокировка запуска Chrome освобождена.")

    # Скриншот для отладки 
    def _save_screenshot(self, name: str):
        """Сохраняет скриншот для отладки."""
        path = self.output_dir / f'{datetime.now().strftime("%H%M%S")}_{name}.png'
        try:
            self.driver.save_screenshot(str(path))
            logger.info(f"[{self.meeting_id}] Скриншот сохранен: {path}")
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось сохранить скриншот '{name}': {e}")

    def toggle_mic_hotkey(self):
        """Простая эмуляция Ctrl+D для переключения микрофона в Meet.
        Без дополнительных проверок состояния и наличия кнопки.
        """
        try:
            # Стараемся сфокусировать страницу и убрать возможный фокус с инпутов
            try:
                self.driver.execute_script("window.focus();")
            except Exception:
                pass
            try:
                body = self.driver.find_element(By.TAG_NAME, 'body')
                body.click()
            except Exception:
                pass

            actions = ActionChains(self.driver)
            actions.key_down(Keys.CONTROL).send_keys('d').key_up(Keys.CONTROL).perform()
            logger.info(f"[{self.meeting_id}] Отправлено сочетание Ctrl+D (toggle mic)")
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось отправить Ctrl+D: {e}")

    def _handle_mic_dialog(self) -> bool:
        """
        Быстрый JS-скан диалога выбора микрофона с общим лимитом ~7-8 секунд.
        1) До 5 сек ищем кнопку "с микрофоном" (RU/EN) и кликаем.
        2) Если не нашли — до 2 сек пробуем "без микрофона".
        Возвращает True, если был найден и нажат любой вариант (с/без микрофона), иначе False.
        """
        logger.info(f"[{self.meeting_id}] [MicDialog] Старт обработки диалога микрофона")
        with_mic_variants = [
            "use microphone", "join with microphone", "use your microphone",
            "продолжить с микрофоном", "использовать микрофон", "войти с микрофоном",
        ]
        without_mic_variants = [
            "continue without microphone", "join without microphone",
            "продолжить без микрофона", "без микрофона",
        ]

        def js_scan_click(phrases: list[str], total_timeout: float) -> bool:
            deadline = time.time() + total_timeout
            js = """
            const phrases = arguments[0];
            const nodes = Array.from(document.querySelectorAll('button, div[role="button"]'));
            for (const el of nodes) {
              const t = (el.innerText||'').trim().toLowerCase();
              if (!t) continue;
              if (phrases.some(p => t.includes(p))) { el.scrollIntoView({block:'center'}); el.click(); return true; }
            }
            return false;
            """
            while time.time() < deadline:
                try:
                    ok = self.driver.execute_script(js, phrases)
                    if ok:
                        return True
                except Exception:
                    pass
                time.sleep(0.25)
            return False

        t0 = time.time()
        if js_scan_click(with_mic_variants, total_timeout=1.0):
            logger.info(f"[{self.meeting_id}] Кнопка 'с микрофоном' нажата за {time.time()-t0:.2f}s")
            return True
        if js_scan_click(without_mic_variants, total_timeout=0.2):
            logger.info(f"[{self.meeting_id}] Кнопка 'без микрофона' нажата за {time.time()-t0:.2f}s")
            return True
        logger.info(f"[{self.meeting_id}] Диалог микрофона не найден за {time.time()-t0:.2f}s — продолжаю.")
        return False

    def _log_permissions_state(self):
        """Пытается залогировать состояние Permissions API для микрофона."""
        try:
            state = self.driver.execute_script(
                "return (navigator.permissions && navigator.permissions.query) ? undefined : 'unsupported';"
            )
            if state == 'unsupported':
                logger.info(f"[{self.meeting_id}] [Perms] Permissions API недоступен")
                return
            js = """
            const cb = arguments[0];
            navigator.permissions.query({name:'microphone'}).then(r=>cb(r.state)).catch(()=>cb('error'));
            """
            result = self.driver.execute_async_script(js)
            logger.info(f"[{self.meeting_id}] [Perms] microphone permission state: {result}")
        except Exception as e:
            logger.info(f"[{self.meeting_id}] [Perms] Не удалось получить состояние: {e}")

    def _handle_chrome_permission_prompt(self):
        """
        Обрабатывает всплывающее окно разрешений Chrome: пытается разрешить доступ к микрофону.
        Безопасно выходим, если промпт отсутствует.
        """
        allow_site_ru = [
            "Разрешить при нахождении на сайте",
        ]
        allow_site_en = [
            "Allow on every visit",
            "Allow while on site",
            "Always allow on this site",
        ]
        allow_once_ru = [
            "Разрешить в этот раз",
        ]
        allow_once_en = [
            "Allow this time",
            "Allow once",
        ]

        def try_click_phrases(phrases, timeout_each=2):
            for phrase in phrases:
                xpaths = [
                    f"//button[normalize-space()='{phrase}']",
                    f"//button[contains(., '{phrase}')]",
                    f"//div[@role='button' and normalize-space()='{phrase}']",
                    f"//div[@role='button' and contains(., '{phrase}')]",
                    f"//span[normalize-space()='{phrase}']/ancestor::button",
                ]
                for xp in xpaths:
                    try:
                        btn = WebDriverWait(self.driver, timeout_each).until(
                            EC.element_to_be_clickable((By.XPATH, xp))
                        )
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        btn.click()
                        logger.info(f"[{self.meeting_id}] Нажал кнопку разрешения: '{phrase}'")
                        return True
                    except Exception:
                        continue
            return False

        try:
            exists = self.driver.execute_script(
                "return !!document.querySelector('button, div[role\\'button\\']') && Array.from(document.querySelectorAll('button, div[role\\'button\\']')).some(el => (el.innerText||'').includes('Разрешить при нахождении') || (el.innerText||'').includes('Allow'));"
            )
            if not exists:
                logger.info(f"[{self.meeting_id}] Баннер разрешений не виден — пропускаю обработку.")
                return
        except Exception:
            pass

        if try_click_phrases(allow_site_ru, timeout_each=3) or try_click_phrases(allow_site_en, timeout_each=3):
            # time.sleep(0.1)
            self._save_screenshot("02b_permission_allowed_site")
            return
        if try_click_phrases(allow_once_ru, timeout_each=2) or try_click_phrases(allow_once_en, timeout_each=2):
            # time.sleep(0.1)
            self._save_screenshot("02b_permission_allowed_once")
            return
        logger.info(f"[{self.meeting_id}] Всплывающее окно разрешений не обнаружено.")

    def _log_pulse_audio_state(self):
        """
        Выполняет команду 'pactl list sink-inputs', чтобы получить информацию
        о том, какие приложения куда направляют свой звук, и выводит это в лог.
        """
        try:
            logger.info(f"[{self.meeting_id}] PULSE_DEBUG: Получение снимка состояния аудиопотоков...")
            
            # Команда 'pactl list sink-inputs' показывает только активные аудиопотоки приложений.
            result = subprocess.run(
                ["pactl", "list", "sink-inputs"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )
            
            # Выводим результат в лог в удобном для чтения виде.
            log_message = f"\n--- PULSEAUDIO SINK INPUTS SNAPSHOT (after bot {self.meeting_id} joined) ---\n"
            log_message += result.stdout
            log_message += "\n-----------------------------------------------------------------"
            
            logger.info(log_message)

        except FileNotFoundError:
             logger.error(f"[{self.meeting_id}] PULSE_DEBUG: Команда 'pactl' не найдена. Невозможно сделать снимок состояния.")
        except subprocess.CalledProcessError as e:
            # Если нет активных потоков, команда может завершиться с ошибкой. Логируем stdout, так как там может быть полезная информация.
            logger.warning(f"[{self.meeting_id}] PULSE_DEBUG: Команда 'pactl list sink-inputs' не вернула успешный результат. Возможно, нет активных потоков. Output: {e.stdout.strip()} Stderr: {e.stderr.strip()}")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] PULSE_DEBUG: Неожиданная ошибка при получении состояния PulseAudio: {e}")

    

    def _speak_via_meet(self, text: str):
        """
        Синтезирует TTS и проигрывает его в УНИКАЛЬНЫЙ sink этого бота.
        """
        if not text:
            return
        try:
            audio_bytes = synthesize_speech_to_bytes(text, self.tts_model)
            if not audio_bytes:
                logger.warning(f"[{self.meeting_id}] TTS вернул пустые байты для текста: '{text}'")
                return

            toggled_on = False
            try:
                # Включаем микрофон в Meet перед началом озвучки
                self.toggle_mic_hotkey()
                toggled_on = True
                time.sleep(0.3)

                # --- "ЛАКМУСОВАЯ БУМАЖКА" ---
                # Этот лог подтвердит, что выполняется именно новый код.
                # Он также покажет, какое именно имя sink'а используется.
                logger.info(f"[{self.meeting_id}] ROUTING_CHECK: Попытка воспроизвести звук в конкретный sink: '{self.sink_name}'")
                
                # Команда, которая использует уникальное имя sink'а этого бота
                subprocess.run(
                    ["paplay", "-d", self.sink_name, "/dev/stdin"],
                    input=audio_bytes,
                    capture_output=True,
                    check=True,
                    timeout=20
                )
                logger.info(f"[{self.meeting_id}] ✅ Ответ ассистента успешно озвучен в '{self.sink_name}'.")

            except subprocess.CalledProcessError as e:
                logger.error(f"[{self.meeting_id}] ❌ Ошибка выполнения paplay для sink '{self.sink_name}': {e.stderr.strip()}")
            except Exception as e:
                logger.error(f"[{self.meeting_id}] ❌ Ошибка во время автоозвучки для sink '{self.sink_name}': {e}.")
            finally:
                if toggled_on:
                    # Небольшая пауза и выключаем микрофон
                    time.sleep(0.2)
                    self.toggle_mic_hotkey()

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Критическая ошибка в _speak_via_meet: {e}")

    # Присоединение в Google Meet
    def join_meet_as_guest(self):
        try:
            logger.info(f"[{self.meeting_id}] Подключаюсь к встрече как гость: {self.meeting_url}")
            self.driver.get(self.meeting_url)
            # time.sleep(2)
            
            logger.info(f"[{self.meeting_id}] Ищу поле для ввода имени...")
            name_input_xpath = '//input[@placeholder="Your name" or @aria-label="Your name" or contains(@placeholder, "name")]'
            name_input = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, name_input_xpath))
            )
            
            logger.info(f"[{self.meeting_id}] Ввожу имя: {MEET_GUEST_NAME}")
            name_input.clear()
            name_input.send_keys(MEET_GUEST_NAME)


            # Обработка диалога микрофона и баннера разрешений
            logger.info(f"[{self.meeting_id}] Обработка диалога микрофона...")
            mic_dialog_found = self._handle_mic_dialog()
            # Если диалог микрофона не показывался — сразу идем дальше, пропуская поиск баннера разрешений
            if mic_dialog_found:
                self._handle_chrome_permission_prompt()

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
                            self._log_pulse_audio_state()
                            # По требованию: сразу после входа эмулируем Ctrl+D для включения/выключения микрофона
                            try:
                                self.toggle_mic_hotkey()
                            except Exception as e_toggle:
                                logger.warning(f"[{self.meeting_id}] Не удалось отправить хоткей Ctrl+D после входа: {e_toggle}")
                            self._log_permissions_state()
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
    
    # Поиск и определение аудиоустройства
    

    def _audio_capture_thread(self):
        """
        Запускает `parec` в подпроцессе и читает из него аудиопоток.
        Это надежная замена для sounddevice.
        """
        threading.current_thread().name = f'AudioCapture-{self.meeting_id}'
        
        # Команда для запуска PulseAudio Recorder (parec)
        # Он будет записывать с нашего виртуального монитора в сыром формате
        command = [
            'parec',
            '--device', self.monitor_name,
            '--format=s16le',             # 16-bit signed integer, little-endian
            f'--rate={STREAM_SAMPLE_RATE}',
            '--channels=1',
            '--raw'                       # Вывод сырых PCM данных без заголовков
        ]
        
        logger.info(f"[{self.meeting_id}] 🎤 Запуск аудиозахвата с помощью parec: {' '.join(command)}")
        
        process = None
        try:
            # Запускаем подпроцесс
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Размер чанка в байтах (int16 = 2 байта на семпл)
            chunk_size_bytes = self.frame_size * 2 

            while self.is_running.is_set():
                # Читаем ровно один фрейм данных из stdout процесса
                audio_chunk_bytes = process.stdout.read(chunk_size_bytes)
                
                if not audio_chunk_bytes:
                    # Проверяем, не завершился ли процесс
                    if process.poll() is not None:
                        logger.warning(f"[{self.meeting_id}] Поток аудио из parec прервался, процесс завершился.")
                        break
                    # Если процесс жив, но данных нет, просто продолжаем цикл
                    continue
                
                # Помещаем сырые байты в очередь для дальнейшей обработки
                self.audio_queue.put(audio_chunk_bytes)
        
        except FileNotFoundError:
            logger.critical(f"[{self.meeting_id}] ❌ КОМАНДА 'parec' НЕ НАЙДЕНА! Установите пакет 'pulseaudio-utils'.")
            self.stop() # Останавливаем бота, если инструмент не найден
        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка в потоке аудиозахвата: {e}", exc_info=True)
            self.stop()
        finally:
            logger.info(f"[{self.meeting_id}] Завершение потока аудиозахвата...")
            if process:
                # Мягко завершаем процесс
                process.terminate()
                try:
                    # Ждем недолго и принудительно убиваем, если он завис
                    process.wait(timeout=2)
                    logger.info(f"[{self.meeting_id}] Процесс parec успешно завершен.")
                except subprocess.TimeoutExpired:
                    logger.warning(f"[{self.meeting_id}] Процесс parec не завершился, убиваем принудительно.")
                    process.kill()
                # Читаем остатки из stderr для отладки, если там что-то было
                stderr_output = process.stderr.read().decode('utf-8', errors='ignore').strip()
                if stderr_output:
                    logger.warning(f"[{self.meeting_id}] stderr от parec: {stderr_output}")

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

                                    segments, _ = self.asr_model.transcribe(full_audio_np, beam_size=5, language="ru")
                                    transcription = "".join([seg.text for seg in segments]).strip()
                                    print(f"Распознано: {transcription}")

                                    if transcription.lower().lstrip().startswith(STREAM_TRIGGER_WORD):

                                        clean_transcription = ''.join(char for char in transcription.lower() if char.isalnum() or char.isspace())

                                        if STREAM_STOP_WORD_1 in clean_transcription or STREAM_STOP_WORD_2 in clean_transcription or STREAM_STOP_WORD_3 in clean_transcription:
                                            logger.info(f"[{self.meeting_id}] Провожу постобработку и завершаю работу")
                                            self.stop()
                                        else:
                                            logger.info(f"[{self.meeting_id}] Мэри услышала вас")
                                            response = get_mary_response(transcription)
                                            logger.info(f"[{self.meeting_id}] Ответ от Мэри: {response}")
                                            try:
                                                if self.enable_auto_tts and response:
                                                    self._speak_via_meet(response)
                                            except Exception as tts_err:
                                                logger.error(f"[{self.meeting_id}] Ошибка при озвучивании ответа: {tts_err}")
            except queue.Empty:
                if is_speaking and speech_buffer_for_asr:
                    logger.info(f"[{self.meeting_id}] Тайм-аут, обрабатываем оставшуюся речь.")
                    is_speaking = False
                continue
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка в цикле VAD: {e}", exc_info=True)

    # Постобработка: объединение аудиочанков -- запуск диаризации и объединение с транскрибацией -- суммаризация -- генерация заголовка -- отправка результатов на внешний сервер
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
            # summary_filename = f"summary_{self.meeting_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            # summary_filepath = self.summary_output_dir / summary_filename

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при постобработке: {e}", exc_info=True)
        finally:
            logger.info(f"[{self.meeting_id}] Постобработка завершена.")

    # Функция отправки результатов на внешний сервер
    def _send_results_to_backend(self, full_text: str, summary: str, title: str):
        try:
            meeting_id_int = int(self.meeting_id) if isinstance(self.meeting_id, str) else self.meeting_id
            
            payload = {
                "meeting_id": meeting_id_int,
                "full_text": full_text,
                "summary": summary,
                "title": title
            }
            headers = {
                "X-Internal-Api-Key": "key",
                "Content-Type": "application/json"
            }
            backend_url = os.getenv('MAIN_BACKEND_URL', 'https://maryrose.by')
            url = f"{backend_url}/meetings/internal/result"
            
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

    # Запуск работы бота
    def run(self):
        """Основной метод, выполняющий всю работу."""
        logger.info(f"[{self.meeting_id}] Бот запускается...")
        try:
            # 1. Создаем уникальные виртуальные аудиоустройства для этого бота
            if not self.audio_manager.create_devices():
                logger.error(f"[{self.meeting_id}] ❌ Не удалось создать аудиоустройства. Завершение работы.")
                return

            # 2. Инициализируем драйвер, который привяжется к этим устройствам
            self._initialize_driver()
            
            # 3. Попытка присоединиться к встрече
            joined_successfully = self.join_meet_as_guest()
            
            if joined_successfully:
                logger.info(f"[{self.meeting_id}] Успешно вошел в конференцию, запускаю основные процессы.")
                
                # Поток обработки аудио (VAD, ASR) - он остается без изменений
                processor_thread = threading.Thread(target=self._process_audio_stream, name=f'VADProcessor-{self.meeting_id}')
                processor_thread.start()
                
                # Поток мониторинга участников - также без изменений
                monitor_thread = threading.Thread(target=self._monitor_participants, name=f'ParticipantMonitor-{self.meeting_id}')
                monitor_thread.daemon = True
                monitor_thread.start()

                # --- НОВАЯ ЛОГИКА ЗАПУСКА ЗАХВАТА ---
                # Запускаем наш новый поток захвата аудио через parec
                capture_thread = threading.Thread(target=self._audio_capture_thread, name=f'AudioCapture-{self.meeting_id}')
                capture_thread.start()
                
                # Ожидаем завершения потоков. Они остановятся, когда будет вызван self.stop()
                # (когда is_running станет False)
                capture_thread.join()
                processor_thread.join()
                
                logger.info(f"[{self.meeting_id}] Основные потоки (обработка и захват) завершены.")
            else:
                logger.warning(f"[{self.meeting_id}] Не удалось присоединиться к встрече. Завершаю работу.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка в работе бота: {e}", exc_info=True)
        finally:
            # Гарантированно вызываем stop(), который очистит все ресурсы
            self.stop()
            logger.info(f"[{self.meeting_id}] Основной метод run завершен.")

    def _leave_meeting(self):
        """
        Нажимает кнопку "Покинуть видеовстречу" в Google Meet.
        Использует надежные селекторы по aria-label для русского и английского интерфейса.
        """
        if not self.driver or not self.joined_successfully:
            logger.info(f"[{self.meeting_id}] Пропускаю выход из встречи - драйвер не инициализирован или не был в конференции.")
            return
        
        try:
            logger.info(f"[{self.meeting_id}] Пытаюсь покинуть встречу...")
            
            # Надежные селекторы для кнопки "Покинуть видеовстречу"
            leave_button_selectors = [
                # По aria-label (русский и английский)
                '//button[@aria-label="Покинуть видеовстречу"]',
                '//button[@aria-label="Leave meeting"]',
                # По jsname (самый надежный селектор)
                '//button[@jsname="CQylAd"]',
                # По иконке call_end
                '//button[.//i[contains(@class, "call_end")]]',
                # По классу кнопки
                '//button[contains(@class, "VYBDae-Bz112c-LgbsSe") and contains(@class, "hk9qKe")]',
                # По data-tooltip-id
                '//button[@data-tooltip-id="tt-c49"]',
                # По jscontroller
                '//button[@jscontroller="PIVayb"]'
            ]
            
            button_found = False
            for selector in leave_button_selectors:
                try:
                    # Ждем появления кнопки до 5 секунд
                    leave_button = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    
                    # Прокручиваем к кнопке и кликаем
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", leave_button)
                    time.sleep(0.5)  # Небольшая пауза для стабилизации
                    leave_button.click()
                    
                    logger.info(f"[{self.meeting_id}] ✅ Кнопка 'Покинуть встречу' успешно нажата (селектор: {selector})")
                    button_found = True
                    break
                    
                except Exception as e:
                    logger.debug(f"[{self.meeting_id}] Селектор '{selector}' не сработал: {e}")
                    continue
            
            if not button_found:
                logger.warning(f"[{self.meeting_id}] ⚠️ Не удалось найти кнопку 'Покинуть встречу' ни одним из селекторов.")
            
            # Небольшая пауза после нажатия кнопки
            time.sleep(2)
            
        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при попытке покинуть встречу: {e}")
            # Продолжаем завершение работы даже при ошибке

    # Остановка бота
    def stop(self):
        if not self.is_running.is_set():
            return
        
        logger.info(f"[{self.meeting_id}] Получена команда на завершение...")

        self.is_running.clear()
        
        # Сначала пытаемся покинуть встречу
        if self.joined_successfully:
            self._leave_meeting()
        
        if self.joined_successfully:
            # Запускаем постобработку в отдельном потоке, чтобы не блокировать завершение
            post_processing_thread = threading.Thread(target=self._perform_post_processing)
            post_processing_thread.start()
        else:
            logger.info(f"[{self.meeting_id}] Пропускаю постобработку, так как вход в конференцию не был успешен.")

        if self.driver:
            try:
                logger.info(f"[{self.meeting_id}] Закрытие WebDriver...")
                self.driver.quit()
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка при закрытии WebDriver: {e}")
        
        # Уничтожаем виртуальные аудиоустройства, созданные для этого бота
        if self.audio_manager:
            self.audio_manager.destroy_devices()
        
        # --- ИЗМЕНЕНИЕ 4: Очистка временного профиля Chrome ---
        # Это критически важный шаг для предотвращения накопления мусора на диске.
        try:
            if self.chrome_profile_path.exists():
                logger.info(f"[{self.meeting_id}] Удаление временного профиля Chrome: {self.chrome_profile_path}")
                shutil.rmtree(self.chrome_profile_path)
                logger.info(f"[{self.meeting_id}] Временный профиль Chrome успешно удален.")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при удалении профиля Chrome: {e}")
        # --- КОНЕЦ ИЗМЕНЕНИЯ 4 ---

        logger.info(f"[{self.meeting_id}] Сессия полностью завершена.")