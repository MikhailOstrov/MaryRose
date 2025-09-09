import os
import time
import queue
import threading
import random
from undetected_chromedriver.patcher import Patcher
from datetime import datetime
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import subprocess
import shutil
from pathlib import Path

from config.config import STREAM_SAMPLE_RATE, logger, CHROME_PROFILE_DIR, MEET_GUEST_NAME, MEET_AUDIO_CHUNKS_DIR
from handlers.audio_handler import AudioHandler
from api.audio_manager import VirtualAudioManager


CHROME_LAUNCH_LOCK = threading.Lock()

class MeetListenerBot:

    # Определение атрибутов класса
    def __init__(self, meeting_url: str, meeting_id: str, email: str):

        self.meeting_url = meeting_url # Ссылка на Google Meet
        self.meeting_id = meeting_id # ID для отслеживания сессии
        self.email = email # Email пользователя
        self.driver = None 
        self.audio_queue = queue.Queue() # Для аудиопотока

        self.is_running = threading.Event()
        self.is_running.set()
        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id 
        self.joined_successfully = False 

        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Аудиофрагменты будут сохраняться в: '{self.output_dir}'")
        
        self.chrome_profile_path = Path(CHROME_PROFILE_DIR) / self.meeting_id

        if self.chrome_profile_path.exists():
            shutil.rmtree(self.chrome_profile_path)
        os.makedirs(self.chrome_profile_path, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Временный профиль Chrome создан в: '{self.chrome_profile_path}'")

        self.audio_manager = VirtualAudioManager(self.meeting_id)
        self.sink_name = self.audio_manager.sink_name
        self.monitor_name = self.audio_manager.monitor_name
        self.post_processing_thread = None

        self.audio_handler = AudioHandler(
        meeting_id=self.meeting_id,
        audio_queue=self.audio_queue,
        is_running=self.is_running,
        meeting_start_time=self.meeting_start_time,
        email=self.email,
        send_chat_message=self.send_chat_message,
        stop=self.stop
        )

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

        logger.info(f"[{self.meeting_id}] Полная изоляция и запуск Chrome...")

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

        with CHROME_LAUNCH_LOCK:
            logger.info(f"[{self.meeting_id}] Блокировка получена. Настройка PulseAudio env vars...")
            
            original_pulse_sink = os.environ.get('PULSE_SINK')
            original_pulse_source = os.environ.get('PULSE_SOURCE')
            
            os.environ['PULSE_SINK'] = self.sink_name
            # os.environ['PULSE_SOURCE'] = self.source_name
            
            logger.info(f"[{self.meeting_id}] Запуск Chrome с PULSE_SINK='{self.sink_name}...")
            
            try:
                opt = uc.ChromeOptions()
                opt.add_argument('--no-sandbox')
                opt.add_argument('--disable-dev-shm-usage')
                opt.add_argument('--window-size=1280,720')
                opt.add_argument(f'--user-data-dir={self.chrome_profile_path}')

                port = random.randint(10000, 20000)
                opt.add_argument(f'--remote-debugging-port={port}')
                logger.info(f"[{self.meeting_id}] Используется порт для отладки: {port}")

                # opt.add_experimental_option("prefs", {
                #     "profile.default_content_setting_values.media_stream_mic": 1,
                #     "profile.default_content_setting_values.notifications": 2
                # })
                
                self.driver = uc.Chrome(
                    options=opt,
                    headless=False,
                    use_subprocess=True,
                    version_main=138,
                    # --- ШАГ 4: Указываем путь к НАШЕЙ КОПИИ драйвера ---
                    driver_executable_path=str(driver_copy_path) if driver_copy_path else None
                )
                
                logger.info(f"[{self.meeting_id}] ✅ Chrome успешно запущен с полной изоляцией.")
                
                # try:
                #     self.driver.execute_cdp_cmd("Browser.grantPermissions", {
                #         "origin": "https://meet.google.com",
                #         "permissions": ["audioCapture"]
                #     })
                #     logger.info(f"[{self.meeting_id}] Разрешение на микрофон выдано через CDP.")
                # except Exception as e_grant:
                #     logger.warning(f"[{self.meeting_id}] Не удалось выдать CDP-разрешение: {e_grant}")

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

    def _handle_mic_dialog(self) -> bool:

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
        # if js_scan_click(with_mic_variants, total_timeout=1.0):
        #     logger.info(f"[{self.meeting_id}] Кнопка 'с микрофоном' нажата за {time.time()-t0:.2f}s")
        #     return True
        if js_scan_click(without_mic_variants, total_timeout=1):
            logger.info(f"[{self.meeting_id}] Кнопка 'без микрофона' нажата за {time.time()-t0:.2f}s")
            return True
        logger.info(f"[{self.meeting_id}] Диалог микрофона не найден за {time.time()-t0:.2f}s — продолжаю.")
        return False

    def _log_pulse_audio_state(self):
        """
        Выполняет команду 'pactl list sink-inputs', чтобы получить информацию
        о том, какие приложения куда направляют свой звук, и выводит это в лог.
        """
        try:
            logger.info(f"[{self.meeting_id}] PULSE_DEBUG: Получение снимка состояния аудиопотоков...")
            
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
            # if mic_dialog_found:
            #     self._handle_chrome_permission_prompt()

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
                            # По требованию: сразу после входа эмулируем Ctrl+D для включения/выключения микрофона
                            # try:
                            #     self.toggle_mic_hotkey()
                            # except Exception as e_toggle:
                            #     logger.warning(f"[{self.meeting_id}] Не удалось отправить хоткей Ctrl+D после входа: {e_toggle}")
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
        
        logger.info(f"[{self.meeting_id}] 🎤 Запуск аудиозахвата с помощью parec")

        # Таймер для подсчета статистики захвата
        chunk_count = 0
        capture_start_time = time.time()

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

                # Статистика захвата (раз в 30 секунд)
                chunk_count += 1
                if chunk_count % 15000 == 0:  # ~30 сек при 512 семплах/чанк
                    elapsed = time.time() - capture_start_time
                    logger.info(f"[{self.meeting_id}] 🎤 Захвачено {chunk_count} чанков за {elapsed:.0f} сек")

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

    # Запуск работы бота
    def run(self):

        logger.info(f"[{self.meeting_id}] Бот запускается...")
        try:

            if not self.audio_manager.create_devices():
                logger.error(f"[{self.meeting_id}] ❌ Не удалось создать аудиоустройства. Завершение работы.")
                return

            self._initialize_driver()
            
            self.joined_successfully = self.join_meet_as_guest()
            
            if self.joined_successfully:
                logger.info(f"[{self.meeting_id}] Успешно вошел в конференцию, запускаю основные процессы.")

                self.meeting_start_time = time.time()

                processor_thread = threading.Thread(target=self.audio_handler._process_audio_stream, args=(self.meeting_start_time,), name=f'VADProcessor-{self.meeting_id}')
                monitor_thread = threading.Thread(target=self._monitor_participants, name=f'ParticipantMonitor-{self.meeting_id}')
                capture_thread = threading.Thread(target=self._audio_capture_thread, name=f'AudioCapture-{self.meeting_id}')
                
                processor_thread.start()
                monitor_thread.start()
                capture_thread.start()
                capture_thread.join()
                processor_thread.join()
                monitor_thread.join()
                
                logger.info(f"[{self.meeting_id}] Основные рабочие потоки завершены.")
            else:
                logger.warning(f"[{self.meeting_id}] Не удалось присоединиться к встрече. Завершаю работу.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка в работе бота: {e}", exc_info=True)
        finally:

            if self.post_processing_thread:
                logger.info(f"[{self.meeting_id}] Ожидание завершения потока постобработки...")
                self.post_processing_thread.join()
                logger.info(f"[{self.meeting_id}] Поток постобработки успешно завершен.")

            self.stop()
            logger.info(f"[{self.meeting_id}] Основной метод run завершен. Процесс готов к выходу.")

    def _leave_meeting(self):

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

                    leave_button = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", leave_button)
                    time.sleep(0.5)
                    leave_button.click()
                    
                    logger.info(f"[{self.meeting_id}] ✅ Кнопка 'Покинуть встречу' успешно нажата (селектор: {selector})")
                    button_found = True
                    break
                    
                except Exception as e:
                    logger.debug(f"[{self.meeting_id}] Селектор '{selector}' не сработал: {e}")
                    continue
            
            if not button_found:
                logger.warning(f"[{self.meeting_id}] ⚠️ Не удалось найти кнопку 'Покинуть встречу' ни одним из селекторов.")
            
            time.sleep(2)
            
        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Ошибка при попытке покинуть встречу: {e}")

    # Остановка бота
    def stop(self):

        if not self.is_running.is_set():
            return
        
        logger.info(f"[{self.meeting_id}] Получена команда на завершение...")

        self.is_running.clear()

        if self.joined_successfully:
            self._leave_meeting()
        
        if self.joined_successfully:
            logger.info(f"[{self.meeting_id}] Инициализация потока постобработки...")
            self.post_processing_thread = threading.Thread(
                target=self.audio_handler._perform_post_processing,
                name=f'PostProcessor-{self.meeting_id}'
            )
            self.post_processing_thread.start()
        else:
            logger.info(f"[{self.meeting_id}] Пропускаю постобработку, так как вход в конференцию не был успешен.")

        if self.driver:
            try:
                logger.info(f"[{self.meeting_id}] Закрытие WebDriver...")
                self.driver.quit()
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка при закрытии WebDriver: {e}")
        
        if self.audio_manager:
            self.audio_manager.destroy_devices()
        
        try:
            if self.chrome_profile_path.exists():
                logger.info(f"[{self.meeting_id}] Удаление временного профиля Chrome: {self.chrome_profile_path}")
                shutil.rmtree(self.chrome_profile_path, ignore_errors=True)
                logger.info(f"[{self.meeting_id}] Временный профиль Chrome успешно удален.")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при удалении профиля Chrome: {e}")
        
        logger.info(f"[{self.meeting_id}] Процедура остановки инициирована, основные ресурсы освобождены.")

    def send_chat_message(self, message: str):

        if not self.driver or not self.joined_successfully:
            logger.warning(f"[{self.meeting_id}] Пропускаю отправку сообщения: бот не в конференции.")
            return

        logger.info(f"[{self.meeting_id}] Попытка отправить сообщение в чат: '{message[:30]}...'")
        
        try:

            try:
                WebDriverWait(self.driver, 2).until(
                    EC.presence_of_element_located((By.XPATH, '//textarea[contains(@aria-label, "Send a message")]'))
                )
                logger.info(f"[{self.meeting_id}] Панель чата уже открыта.")
            except:
                logger.info(f"[{self.meeting_id}] Панель чата закрыта, открываю...")
                chat_button_xpath = '//button[contains(@aria-label, "Chat with everyone") or contains(@aria-label, "Чат со всеми")]'
                chat_button = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, chat_button_xpath))
                )
                
                # ИСПОЛЬЗУЕМ JAVASCRIPT CLICK
                self.driver.execute_script("arguments[0].click();", chat_button)

            # --- Шаг 2: Найти поле ввода, ввести текст и отправить ---
            textarea_xpath = '//textarea[contains(@aria-label, "Send a message") or contains(@aria-label, "Отправить сообщение")]'
            message_input = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, textarea_xpath))
            )

            message_input.clear()
            message_input.send_keys(message)
            time.sleep(0.2)

            send_button_xpath = '//button[contains(@aria-label, "Send a message") or contains(@aria-label, "Отправить сообщение")][.//i[text()="send"]]'
            send_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, send_button_xpath))
            )
            
            # ИСПОЛЬЗУЕМ JAVASCRIPT CLICK
            self.driver.execute_script("arguments[0].click();", send_button)
            logger.info(f"[{self.meeting_id}] ✅ Сообщение в чат успешно отправлено.")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Не удалось отправить сообщение в чат: {e}", exc_info=True)
            self._save_screenshot("99_chat_send_error")