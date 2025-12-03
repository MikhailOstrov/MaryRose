import os
import time
import queue
import threading
import random
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import subprocess
import shutil
from pathlib import Path

from config.config import STREAM_SAMPLE_RATE, logger, CHROME_PROFILE_DIR, MEET_GUEST_NAME, MEET_AUDIO_CHUNKS_DIR, MEET_FRAME_DURATION_MS
from handlers.audio_handler import AudioHandler
from api.audio_manager import VirtualAudioManager


CHROME_LAUNCH_LOCK = threading.Lock()

class MeetListenerBotPW:

    # Определение атрибутов класса
    def __init__(self, meeting_url: str, meeting_id: str, email: str, remaining_seconds: int):

        self.meeting_url = meeting_url # Ссылка на Google Meet
        self.meeting_id = meeting_id # ID для отслеживания сессии
        self.email = email # Email пользователя
        self.remaining_seconds = remaining_seconds # Оставшееся время для работы бота

        
        self.notified_10_min = remaining_seconds <= 600
        self.notified_5_min = False
        
        # Playwright objects
        self.playwright = None
        self.browser_context = None
        self.page = None
        
        self.audio_queue = queue.Queue() # Для аудиопотока

        self.is_running = threading.Event()
        self.is_running.set()
        self.output_dir = MEET_AUDIO_CHUNKS_DIR / self.meeting_id 
        self.joined_successfully = False 

        self.frame_size = int(STREAM_SAMPLE_RATE * MEET_FRAME_DURATION_MS / 1000) # Для VAD-модели (длительность чанка)
        
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
            email=self.email,
            send_chat_message=self.send_chat_message,
            stop=self.stop
        )


    def _monitor_remaining_seconds(self):
        threading.current_thread().name = f'RemainingSecondsMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг оставшегося времени запущен.")
        while self.is_running.is_set() and self.remaining_seconds > 0:
            if self.remaining_seconds <= 600 and not self.notified_10_min:
                self.send_chat_message("Оставшееся время: 10 минут. Через 10 минут ассистент завершит работу.")
                logger.info(f"[{self.meeting_id}] Оставшееся время: {self.remaining_seconds} секунд. Через 10 минут ассистент завершит работу.")
                self.notified_10_min = True
            if self.remaining_seconds <= 300 and not self.notified_5_min:
                self.send_chat_message(" Оставшееся время: 5 минут. Через 5 минут ассистент завершит работу.")
                logger.info(f"[{self.meeting_id}] Оставшееся время: {self.remaining_seconds} секунд. Через 5 минут ассистент завершит работу.")
                self.notified_5_min = True

            if self.remaining_seconds >= 13*60:
                time.sleep(60)
                self.remaining_seconds -= 60
            else:
                time.sleep(1)
                self.remaining_seconds -= 1
            
        if self.remaining_seconds <= 0 and self.is_running.is_set():
            logger.info(f"[{self.meeting_id}] Оставшееся время закончилось. Завершаю работу.")
            try:
                self.send_chat_message(" Оставшееся время закончилось. Ассистент завершает работу.")
                time.sleep(2)
            except Exception as e:
                logger.warning(f"[{self.meeting_id}] Не удалось отправить сообщение в чат: {e}")
            finally:
                self.stop()
        else:
            logger.info(f"[{self.meeting_id}] Мониторинг оставшегося времени остановлен.")

    # Отслеживание кол-ва участников
    def _monitor_participants(self):
        """Отслеживает количество участников. Если бот остается один, он завершает работу."""
        threading.current_thread().name = f'ParticipantMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг участников запущен.")
        
        participant_locator_xpath = "//button[.//i[text()='people'] and @aria-label]"
        consecutive_failures = 0
        max_failures = 2

        while self.is_running.is_set():
            # Оптимизация: проверяем реже (раз в 3 секунды, 5 раз = 15 сек), чтобы не будить CPU каждую секунду
            for _ in range(5): 
                if not self.is_running.is_set():
                    logger.info(f"[{self.meeting_id}] Мониторинг участников остановлен.")
                    return
                time.sleep(3)
            
            try:
                if not self.page:
                    continue
                    
                # В Playwright используем locator и get_attribute
                participant_element = self.page.locator(participant_locator_xpath).first
                
                # Проверяем видимость, чтобы не падать если элемента нет
                if participant_element.is_visible():
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
                else:
                     # Элемент не виден
                    consecutive_failures += 1
                    logger.warning(f"[{self.meeting_id}] Элемент участников не найден (not visible). Попытка {consecutive_failures}/{max_failures}.")

            except Exception as e:
                consecutive_failures += 1
                logger.warning(f"[{self.meeting_id}] Не удалось найти счетчик участников: {e}. Попытка {consecutive_failures}/{max_failures}.")

            if consecutive_failures >= max_failures:
                logger.error(f"[{self.meeting_id}] Не удалось найти счетчик участников {max_failures} раз подряд. Предполагаю, что встреча завершена.")
                self.stop()
                return
    
    # Инициализация драйвера для подключения
    def _initialize_driver(self):
        """Инициализирует Playwright Context."""
        logger.info(f"[{self.meeting_id}] Инициализация Playwright...")

        with CHROME_LAUNCH_LOCK:
            logger.info(f"[{self.meeting_id}] Блокировка получена. Запуск Playwright Context...")
            
            try:
                self.playwright = sync_playwright().start()
                
                # Аргументы запуска Chromium
                args = [
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--window-size=1280,720', 
                    '--disable-animations',
                    '--enable-gpu-rasterization', # Оставим, может работать с swiftshader
                    '--enable-zero-copy',
                    '--use-gl=swiftshader', # <-- ИЗМЕНЕНО: Программный рендеринг для надежности скриншотов
                    '--ignore-gpu-blocklist',
                    '--blink-settings=imagesEnabled=false',
                    '--disable-blink-features=AutomationControlled' 
                ]
                
                # Формируем env с PulseAudio
                env = os.environ.copy()
                env['PULSE_SINK'] = self.sink_name
                
                logger.info(f"[{self.meeting_id}] Запуск контекста с PULSE_SINK='{self.sink_name}'...")

                self.browser_context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.chrome_profile_path),
                    headless=True, # В Docker обычно True, но Xvfb позволяет False. Оставим как есть.
                    args=args,
                    env=env,
                    viewport={"width": 1280, "height": 720},
                    permissions=['microphone'], 
                    ignore_default_args=["--enable-automation"],
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                
                self.page = self.browser_context.pages[0]
                if not self.page:
                    self.page = self.browser_context.new_page()
                
                # --- STEALTH INJECTION ---
                # Маскируемся под обычный Mac Chrome, скрываем SwiftShader и WebDriver
                stealth_js = """
                // 1. Подмена WebGL (Скрываем SwiftShader)
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    // UNMASKED_VENDOR_WEBGL
                    if (parameter === 37445) {
                        return 'Intel Inc.';
                    }
                    // UNMASKED_RENDERER_WEBGL
                    if (parameter === 37446) {
                        return 'Intel Iris OpenGL Engine';
                    }
                    return getParameter(parameter);
                };

                // 2. Скрываем webdriver
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                // 3. Подменяем платформу
                Object.defineProperty(navigator, 'platform', {
                    get: () => 'MacIntel'
                });

                // 4. Языки
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });

                // 5. Плагины (Chrome всегда имеет PDF Viewer)
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                """
                self.page.add_init_script(stealth_js)
                
                # Настройка блокировки ресурсов (Network Interception)
                self.page.route("**/*", self._handle_route)
                
                logger.info(f"[{self.meeting_id}] ✅ Playwright Context успешно запущен.")
                
            except Exception as e:
                logger.critical(f"[{self.meeting_id}] ❌ Полный провал запуска Playwright: {e}", exc_info=True)
                if self.browser_context:
                    self.browser_context.close()
                if self.playwright:
                    self.playwright.stop()
                raise
            finally:
                 logger.info(f"[{self.meeting_id}] Блокировка запуска Playwright освобождена.")

    def _handle_route(self, route):
        """Блокирует загрузку изображений, медиа и шрифтов для экономии ресурсов."""
        if route.request.resource_type in ["image", "media", "font"]:
            route.abort()
        else:
            route.continue_()

    # Скриншот для отладки 
    def _save_screenshot(self, name: str):
        """Сохраняет скриншот для отладки и выводит Base64 в лог."""
        path = self.output_dir / f'{datetime.now().strftime("%H%M%S")}_{name}.png'
        try:
            if self.page:
                self.page.screenshot(path=str(path))
                logger.info(f"[{self.meeting_id}] Скриншот сохранен: {path}")
                
                # --- ALWAYS OUTPUT BASE64 FOR DEBUGGING ---
                try:
                    import base64
                    with open(path, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                        logger.info(f"\n[{self.meeting_id}] === SCREENSHOT '{name}' BASE64 ===\n{encoded_string}\n===============================================\n")
                except Exception as e_b64:
                    logger.warning(f"Failed to encode screenshot to base64: {e_b64}")
                    
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось сохранить скриншот '{name}': {e}")

    def _handle_mic_dialog(self) -> bool:
        """
        Ищет и кликает по кнопкам включения/выключения микрофона в предбаннике.
        В Playwright мы выдали права через permissions=['microphone'], но Google Meet
        все равно может спросить 'Войти с микрофоном' или 'Без микрофона'.
        """
        logger.info(f"[{self.meeting_id}] [MicDialog] Старт обработки диалога микрофона")
        
        # Селекторы (по тексту)
        with_mic_variants = [
            "use microphone", "join with microphone", "use your microphone",
            "продолжить с микрофоном", "использовать микрофон", "войти с микрофоном",
        ]
        without_mic_variants = [
            "continue without microphone", "join without microphone",
            "продолжить без микрофона", "без микрофона",
        ]

        # Вспомогательная функция для поиска и клика
        def try_click_variant(variants):
            for text in variants:
                # Используем text=... для поиска по тексту (case-insensitive)
                # Ищем кнопку или div role=button
                # Playwright псевдо-селектор :text-matches для case insensitive regex был бы крут, 
                # но text=... обычно работает как 'contains' для Playwright
                try:
                    # Ищем кнопку, содержащую текст (case-insensitive через locator filter)
                    # locator('button').filter(has_text=...)
                    btn = self.page.locator("button, div[role='button']").filter(has_text=text).first
                    if btn.is_visible():
                        btn.click()
                        return True
                except:
                    pass
            return False

        deadline = time.time() + 1.0 # 1 секунда на попытки
        while time.time() < deadline:
            if try_click_variant(without_mic_variants):
                logger.info(f"[{self.meeting_id}] Кнопка 'без микрофона' нажата.")
                return True
            # if try_click_variant(with_mic_variants):
            #     logger.info(f"[{self.meeting_id}] Кнопка 'с микрофоном' нажата.")
            #     return True
            time.sleep(0.25)
            
        logger.info(f"[{self.meeting_id}] Диалог микрофона не найден за 1с — продолжаю.")
        return False

    # Присоединение в Google Meet
    def join_meet_as_guest(self):
        try:
            logger.info(f"[{self.meeting_id}] Подключаюсь к встрече как гость: {self.meeting_url}")
            self.page.goto(self.meeting_url)
            
            logger.info(f"[{self.meeting_id}] Ищу поле для ввода имени...")
            name_input_selector = 'input[placeholder="Your name"], input[aria-label="Your name"], input[placeholder*="name"]'
            
            try:
                name_input = self.page.locator(name_input_selector).first
                name_input.wait_for(state="visible", timeout=30000)
                
                logger.info(f"[{self.meeting_id}] Ввожу имя: {MEET_GUEST_NAME}")
                name_input.fill(MEET_GUEST_NAME)
            except PlaywrightTimeoutError:
                logger.warning(f"[{self.meeting_id}] Поле ввода имени не найдено. Возможно, мы уже залогинены или другой интерфейс.")

            # Обработка диалога микрофона
            logger.info(f"[{self.meeting_id}] Обработка диалога микрофона...")
            self._handle_mic_dialog()

            logger.info(f"[{self.meeting_id}] Ищу кнопку 'Ask to join'...")
            # Селектор кнопки присоединения
            join_button_selector = '//button[.//span[contains(text(), "Ask to join") or contains(text(), "Попросить войти")]]'
            
            try:
                join_button = self.page.locator(join_button_selector).first
                join_button.wait_for(state="visible", timeout=30000)
                join_button.click()
            except PlaywrightTimeoutError:
                 # Попробуем найти кнопку "Присоединиться" (Join now), если вдруг мы сразу можем войти
                 join_now_selector = '//button[.//span[contains(text(), "Join now") or contains(text(), "Присоединиться")]]'
                 if self.page.locator(join_now_selector).is_visible():
                     logger.info(f"[{self.meeting_id}] Найдена кнопка 'Join now', кликаю...")
                     self.page.locator(join_now_selector).click()
                 else:
                     logger.error(f"[{self.meeting_id}] Не найдена кнопка входа. Делаю скриншот...")
                     self._save_screenshot("error_no_join_button")
                     
                     # Дампим HTML для анализа
                     try:
                        html_path = self.output_dir / f"debug_{self.meeting_id}.html"
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(self.page.content())
                        logger.info(f"HTML страницы сохранен в {html_path}")
                     except: pass
                     
                     raise

            self._save_screenshot("03_after_ask_to_join")
            
            logger.info(f"[{self.meeting_id}] Запрос отправлен. Ожидаю одобрения хоста (до 120с)...")
            max_wait_time = 120
            
            # Индикаторы успеха (Исправленные XPath для Playwright)
            success_indicators = [
                '//button[contains(@data-tooltip, "end call") or contains(@aria-label, "end call") or contains(@aria-label, "завершить")]',
                "//button[.//i[text()='people'] and @aria-label]",
                '//div[@data-self-name]', 
                '//div[contains(@class, "control") and (contains(@class, "bar") or contains(@class, "panel"))]',
                '//button[contains(@aria-label, "hand") or contains(@aria-label, "рука") or contains(@data-tooltip, "hand")]'
            ]
            # Индикаторы ошибки
            error_indicators = [
                '//*[contains(text(), "denied") or contains(text(), "отклонен")]',
                '//*[contains(text(), "rejected") or contains(text(), "отказано")]',
                '//*[contains(text(), "error") or contains(text(), "ошибка")]',
                '//*[contains(text(), "unable") or contains(text(), "невозможно")]'
            ]

            # Цикл ожидания
            start_time = time.time()
            while time.time() - start_time < max_wait_time:
                # Проверка успеха
                for selector in success_indicators:
                    if self.page.locator(selector).first.is_visible():
                         self._save_screenshot("04_joined_successfully")
                         logger.info(f"[{self.meeting_id}] ✅ Успешно присоединился к встрече!")
                         self.joined_successfully = True
                         return True
                
                # Проверка ошибок
                for selector in error_indicators:
                    if self.page.locator(selector).first.is_visible():
                        text = self.page.locator(selector).first.text_content()
                        logger.error(f"[{self.meeting_id}] ❌ Присоединение отклонено: {text}")
                        self._save_screenshot("98_join_denied")
                        return False

                time.sleep(2)
                elapsed = int(time.time() - start_time)
                
                # Отладочный вывод URL каждые 10 секунд
                if elapsed % 10 == 0 and elapsed > 0:
                     logger.info(f"[{self.meeting_id}] Текущий URL: {self.page.url}")

                if elapsed % 30 == 0 and elapsed > 0:
                     logger.info(f"[{self.meeting_id}] Ожидание... {elapsed}с прошло.")
                     screenshot_name = f"wait_{elapsed}s"
                     self._save_screenshot(screenshot_name)
                     
                     # --- ADDED FOR DEBUGGING: Base64 скриншота ожидания ---
                     try:
                        path = self.output_dir / f'{datetime.now().strftime("%H%M%S")}_{screenshot_name}.png'
                        # Ищем файл, так как _save_screenshot добавляет timestamp
                        # Это немного криво, но для отладки сойдет. Лучше найдем последний созданный файл.
                        files = sorted(list(self.output_dir.glob(f"*_{screenshot_name}.png")))
                        if files:
                            last_screenshot = files[-1]
                            import base64
                            with open(last_screenshot, "rb") as image_file:
                                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                                logger.info(f"\n[{self.meeting_id}] === WAIT SCREENSHOT {elapsed}s BASE64 ===\n{encoded_string}\n===============================================\n")
                     except Exception as e:
                        logger.warning(f"Failed to base64 log screenshot: {e}")

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

        logger.info(f"[{self.meeting_id}] Бот запускается (Playwright version)...")
        try:

            if not self.audio_manager.create_devices():
                logger.error(f"[{self.meeting_id}] ❌ Не удалось создать аудиоустройства. Завершение работы.")
                return

            self._initialize_driver()
            
            self.joined_successfully = self.join_meet_as_guest()
            
            if self.joined_successfully:
                logger.info(f"[{self.meeting_id}] Успешно вошел в конференцию, запускаю основные процессы.")

                # Оптимизация: скрытие видео уже реализовано через network interception в _initialize_driver

                processor_thread = threading.Thread(target=self.audio_handler._process_audio_stream,name=f'VADProcessor-{self.meeting_id}')
                monitor_thread = threading.Thread(target=self._monitor_participants, name=f'ParticipantMonitor-{self.meeting_id}')
                capture_thread = threading.Thread(target=self._audio_capture_thread, name=f'AudioCapture-{self.meeting_id}')
                remaining_seconds_thread = threading.Thread(target=self._monitor_remaining_seconds, name=f'RemainingSecondsMonitor-{self.meeting_id}')

                processor_thread.start()
                monitor_thread.start()
                capture_thread.start()
                remaining_seconds_thread.start()

                capture_thread.join()
                processor_thread.join()
                monitor_thread.join()
                remaining_seconds_thread.join()
                
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

        if not self.page or not self.joined_successfully:
            logger.info(f"[{self.meeting_id}] Пропускаю выход из встречи - страница не инициализирована или не был в конференции.")
            return
        
        try:
            logger.info(f"[{self.meeting_id}] Пытаюсь покинуть встречу...")
            
            # Надежные селекторы для кнопки "Покинуть видеовстречу"
            leave_button_selectors = [
                'button[aria-label="Покинуть видеовстречу"]',
                'button[aria-label="Leave meeting"]',
                'button[jsname="CQylAd"]',
                'button:has(i.call_end)',
                'button[data-tooltip-id="tt-c49"]'
            ]
            
            button_found = False
            for selector in leave_button_selectors:
                try:
                    # В Playwright можно просто кликнуть по первому видимому
                    btn = self.page.locator(selector).first
                    if btn.is_visible():
                        btn.scroll_into_view_if_needed()
                        time.sleep(0.5)
                        btn.click()
                        logger.info(f"[{self.meeting_id}] ✅ Кнопка 'Покинуть встречу' успешно нажата (селектор: {selector})")
                        button_found = True
                        break
                except Exception as e:
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

        # Закрытие Playwright
        try:
            if self.browser_context:
                logger.info(f"[{self.meeting_id}] Закрытие Browser Context...")
                self.browser_context.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при закрытии Playwright: {e}")
        
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

        if not self.page or not self.joined_successfully:
            logger.warning(f"[{self.meeting_id}] Пропускаю отправку сообщения: бот не в конференции.")
            return

        logger.info(f"[{self.meeting_id}] Попытка отправить сообщение в чат: '{message[:30]}...'")
        
        try:
            # Шаг 1: Открыть панель чата, если закрыта
            textarea_selector = 'textarea[aria-label*="Send a message"], textarea[aria-label*="Отправить сообщение"]'
            
            if not self.page.locator(textarea_selector).is_visible():
                logger.info(f"[{self.meeting_id}] Панель чата закрыта, открываю...")
                chat_btn_selector = 'button[aria-label*="Chat with everyone"], button[aria-label*="Чат со всеми"]'
                self.page.locator(chat_btn_selector).click()
                time.sleep(0.5)

            # --- Шаг 2: Найти поле ввода, ввести текст и отправить ---
            self.page.locator(textarea_selector).fill(message)
            time.sleep(0.2)

            send_button_selector = 'button[aria-label*="Send a message"][aria-label*="Send"], button[aria-label*="Отправить сообщение"][aria-label*="Отправить"]'
            # Иногда кнопка отправки - это просто кнопка с иконкой send.
            # Попробуем более универсальный селектор для кнопки отправки рядом с textarea
            
            # В оригинале: '//button[contains(@aria-label, "Send a message") or contains(@aria-label, "Отправить сообщение")][.//i[text()="send"]]'
            # Перевод в CSS/Locator:
            self.page.locator('button').filter(has_text="send").first.click()
            # Или если там иконка material icons с текстом 'send'
            
            logger.info(f"[{self.meeting_id}] ✅ Сообщение в чат успешно отправлено.")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Не удалось отправить сообщение в чат: {e}", exc_info=True)
            self._save_screenshot("99_chat_send_error")

