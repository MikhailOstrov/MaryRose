import os
import time
import queue
import threading
import random
import requests # Добавлено для загрузки скриншотов
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
        self.chat_queue = queue.Queue() # Для сообщений в чат

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


    def _update_timer(self):
        """Проверяет оставшееся время и отправляет уведомления (вызывается из главного цикла)."""
        if self.remaining_seconds <= 0:
            if self.is_running.is_set():
                logger.info(f"[{self.meeting_id}] Оставшееся время закончилось. Завершаю работу.")
                self.send_chat_message(" Оставшееся время закончилось. Ассистент завершает работу.")
                # Даем время на отправку сообщения в цикле перед остановкой
                self.stop()
            return

        # Уведомления
        if self.remaining_seconds <= 600 and not self.notified_10_min:
            self.send_chat_message("Оставшееся время: 10 минут. Через 10 минут ассистент завершит работу.")
            logger.info(f"[{self.meeting_id}] Оставшееся время: {self.remaining_seconds} секунд. Через 10 минут ассистент завершит работу.")
            self.notified_10_min = True
        
        if self.remaining_seconds <= 300 and not self.notified_5_min:
            self.send_chat_message(" Оставшееся время: 5 минут. Через 5 минут ассистент завершит работу.")
            logger.info(f"[{self.meeting_id}] Оставшееся время: {self.remaining_seconds} секунд. Через 5 минут ассистент завершит работу.")
            self.notified_5_min = True
            
        # Декремент происходит в основном цикле каждую секунду
        self.remaining_seconds -= 1

    # Отслеживание кол-ва участников
    def _check_participants(self):
        """Проверяет количество участников (вызывается из главного цикла)."""
        # Оптимизация: используем evaluate для выполнения проверки прямо в браузере,
        # чтобы минимизировать передачу данных между Python и Playwright.
        
        # Состояние сбоев
        if not hasattr(self, '_part_failures'):
            self._part_failures = 0
        
        try:
            if not self.page:
                return

            # JS-скрипт ищет кнопку участников и парсит цифру из aria-label
            count = self.page.evaluate("""() => {
                try {
                    // Ищем кнопку, внутри которой есть иконка 'people'
                    // XPath аналог: //button[.//i[text()='people'] and @aria-label]
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const targetBtn = buttons.find(btn => {
                        const icon = btn.querySelector('i');
                        return icon && icon.textContent === 'people' && btn.hasAttribute('aria-label');
                    });
                    
                    if (!targetBtn) return -1; // Не найдено
                    
                    const label = targetBtn.getAttribute('aria-label');
                    const match = label.match(/(\d+)/);
                    return match ? parseInt(match[0]) : 0; // Если цифр нет, но кнопка есть - странно, вернем 0
                } catch (e) {
                    return -2; // Ошибка JS
                }
            }""")
            
            if count >= 0:
                logger.info(f"[{self.meeting_id}] Текущее количество участников: {count}")
                self._part_failures = 0 
                if count <= 1:
                    logger.warning(f"[{self.meeting_id}] Встреча пуста (участников <= 1). Завершаю работу...")
                    self.stop()
            else:
                # -1 (не найдено) или -2 (ошибка)
                self._part_failures += 1
                # Логируем только каждую 5-ю ошибку, чтобы не спамить
                if self._part_failures % 5 == 0:
                    logger.warning(f"[{self.meeting_id}] Не удалось считать участников (код {count}). Сбой {self._part_failures}.")

        except Exception as e:
            self._part_failures += 1
            logger.warning(f"[{self.meeting_id}] Ошибка проверки участников (Python): {e}")

        if self._part_failures >= 20: # ~1 минута при проверке раз в 3 сек
            logger.error(f"[{self.meeting_id}] Не удалось найти счетчик участников слишком долго. Аварийный выход.")
            self.stop()
    
    # Инициализация драйвера для подключения
    def _initialize_driver(self):
        """Инициализирует Playwright Context."""
        logger.info(f"[{self.meeting_id}] Инициализация Playwright...")

        with CHROME_LAUNCH_LOCK:
            logger.info(f"[{self.meeting_id}] Блокировка получена. Запуск Playwright Context...")
            
            try:
                self.playwright = sync_playwright().start()
                
                # Аргументы запуска Chromium (максимально приближенные к обычному запуску, но с оптимизацией)
                args = [
                    '--disable-blink-features=AutomationControlled',
                    '--start-maximized', 
                    '--disable-infobars',
                    '--no-default-browser-check',
                    # Оптимизация производительности:
                    '--disable-extensions', # Отключаем расширения
                    '--disable-component-extensions-with-background-pages',
                    '--disable-background-networking', # Отключаем фоновую сетевую активность
                    '--disable-background-timer-throttling', # Но не тротлим таймеры (важно для Meet)
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                    '--disable-translate', # Отключаем переводчик
                    '--disable-sync', # Отключаем синхронизацию
                    '--metrics-recording-only',
                    '--no-first-run',
                    '--headless=new', # <-- ПОПЫТКА: Новый Headless режим Chrome (почти как настоящий)
                    # Если Google спалит headless=new, придется вернуть headless=False
                ]
                
                # Формируем env с PulseAudio
                env = os.environ.copy()
                env['PULSE_SINK'] = self.sink_name
                
                logger.info(f"[{self.meeting_id}] Запуск контекста с PULSE_SINK='{self.sink_name}'...")

                self.browser_context = self.playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.chrome_profile_path),
                    channel="chrome", # Используем настоящий Chrome
                    headless=False, # Игнорируем аргумент headless здесь, так как он передается в args='--headless=new'
                    # Важно: Playwright ругается если передать headless=True И аргумент --headless=new одновременно.
                    # Поэтому ставим headless=False, а режим задаем через args.
                    args=args,
                    env=env,
                    viewport=None, # Отключаем эмуляцию viewport (важно для undetected поведения)
                    permissions=['microphone'], 
                    ignore_default_args=["--enable-automation"],
                    # Убираем ручной user_agent, пусть Chrome подставит свой родной
                )
                
                self.page = self.browser_context.pages[0]
                if not self.page:
                    self.page = self.browser_context.new_page()
                
                # --- STEALTH INJECTION ---
                # Маскируемся под обычный Windows Chrome
                stealth_js = """
                // 1. Добавляем window.chrome (обязательно для Chrome)
                if (!window.chrome) {
                    window.chrome = {
                        runtime: {},
                        loadTimes: function() {},
                        csi: function() {},
                        app: {}
                    };
                }

                // 2. Скрываем webdriver
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // 3. Плагины (для Chrome всегда должны быть)
                if (navigator.plugins.length === 0) {
                     Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                }
                
                // 4. Языки (берем из системы или дефолт)
                if (!navigator.languages || navigator.languages.length === 0) {
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['ru-RU', 'ru', 'en-US', 'en']
                    });
                }
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
        # Блокируем не только типы ресурсов, но и конкретные "тяжелые" домены или пути Google Meet,
        # которые отвечают за логирование, эффекты и предзагрузку.
        req = route.request
        if req.resource_type in ["image", "media", "font", "stylesheet"]:
            # Stylesheet блокировать опасно, может сломаться верстка кнопок. 
            # Но для аудио-бота верстка не важна, главное функционал.
            # Попробуем оставить стили, если будут проблемы - уберем из списка.
            if req.resource_type != "stylesheet": 
                route.abort()
                return

        # Блокировка аналитики и логов Google (снижает трафик и CPU)
        url = req.url
        if "play-log" in url or "gen_204" in url or "batchexecute" in url:
            # batchexecute - это основной канал RPC Google, его блокировать нельзя!
            if "play-log" in url or "gen_204" in url:
                route.abort()
                return
        
        route.continue_()

    # Скриншот для отладки 
    def _save_screenshot(self, name: str):
        """Сохраняет скриншот и загружает его на transfer.sh для удобного просмотра."""
        path = self.output_dir / f'{datetime.now().strftime("%H%M%S")}_{name}.png'
        try:
            if self.page:
                self.page.screenshot(path=str(path))
                logger.info(f"[{self.meeting_id}] Скриншот сохранен локально: {path}")

                # --- UPLOAD TO LOCAL SERVER (DEBUG) ---
                try:
                    # TODO: Замените URL на актуальный ngrok адрес
                    upload_url = "https://57b13066536b.ngrok-free.app/upload-image" 
                    with open(path, 'rb') as f:
                        files = {'file': (path.name, f, 'image/png')}
                        # Timeout поменьше, чтобы не блочить
                        requests.post(upload_url, files=files, timeout=5)
                        logger.info(f"[{self.meeting_id}] 📤 Скриншот отправлен на {upload_url}")
                except Exception as e_upload_custom:
                    # Логируем как warning, чтобы не засорять если сервер недоступен
                    logger.warning(f"Не удалось отправить скриншот на локальный сервер: {e_upload_custom}")
                
                # --- UPLOAD TO TRANSFER.SH ---
                try:
                    with open(path, 'rb') as f:
                        filename = f"{self.meeting_id}_{path.name}"
                        response = requests.put(f"https://transfer.sh/{filename}", data=f)
                        
                        if response.status_code == 200:
                            url = response.text.strip()
                            logger.info(f"\n[{self.meeting_id}] 📸 Скриншот доступен по ссылке:\n👉 {url}\n")
                        else:
                            logger.warning(f"Не удалось загрузить скриншот: код {response.status_code}")
                except Exception as e_upload:
                    logger.warning(f"Ошибка при загрузке скриншота на сервер: {e_upload}")
                    
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

            # --- CSS OPTIMIZATION: Hide Video & Animations ---
            # Это значительно снижает нагрузку на CPU/GPU, так как браузер не рендерит видеопотоки
            logger.info(f"[{self.meeting_id}] Применяю CSS-оптимизации (скрытие видео)...")
            self.page.add_style_tag(content="""
                video { display: none !important; }
                .visual-effects-container { display: none !important; }
                * { 
                    transition: none !important; 
                    animation: none !important; 
                    box-shadow: none !important;
                    text-shadow: none !important;
                }
            """)

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

                # Потоки, не использующие Playwright
                processor_thread = threading.Thread(target=self.audio_handler._process_audio_stream,name=f'VADProcessor-{self.meeting_id}')
                capture_thread = threading.Thread(target=self._audio_capture_thread, name=f'AudioCapture-{self.meeting_id}')

                processor_thread.start()
                capture_thread.start()
                
                logger.info(f"[{self.meeting_id}] Основные фоновые потоки (audio, processing) запущены.")
                
                # --- ГЛАВНЫЙ ЦИКЛ СОБЫТИЙ ---
                # Здесь выполняются все действия с Playwright (в главном потоке)
                last_participant_check = time.time()
                last_timer_update = time.time()
                
                try:
                    while self.is_running.is_set():
                        now = time.time()
                        
                        # 1. Обработка очереди сообщений
                        try:
                            while not self.chat_queue.empty():
                                msg = self.chat_queue.get_nowait()
                                self._perform_chat_action(msg)
                        except Exception as e:
                            logger.error(f"[{self.meeting_id}] Ошибка при обработке очереди чата: {e}")
                        
                        # 2. Проверка участников (раз в 3 сек)
                        if now - last_participant_check > 3:
                            self._check_participants()
                            last_participant_check = now
                            
                        # 3. Таймер (раз в 1 сек)
                        if now - last_timer_update >= 1:
                            self._update_timer()
                            last_timer_update = now
                            
                        # 4. Проверка здоровья потоков
                        if not capture_thread.is_alive():
                            logger.warning(f"[{self.meeting_id}] Поток захвата аудио упал!")
                            self.stop()
                        
                        if not processor_thread.is_alive() and self.is_running.is_set():
                            logger.warning(f"[{self.meeting_id}] Поток обработки аудио завершился неожиданно.")
                            self.stop()

                        time.sleep(0.1) # Небольшая пауза, чтобы не грузить CPU циклом
                        
                except KeyboardInterrupt:
                    logger.info(f"[{self.meeting_id}] Прерывание клавиатуры в главном цикле.")
                    self.stop()
                
                # Ожидание завершения потоков, если они еще живы
                if capture_thread.is_alive():
                    capture_thread.join(timeout=2)
                if processor_thread.is_alive():
                    processor_thread.join(timeout=2)

            else:
                logger.warning(f"[{self.meeting_id}] Не удалось присоединиться к встрече. Завершаю работу.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] ❌ Критическая ошибка в работе бота: {e}", exc_info=True)
        finally:
            # Вызываем очистку (включает закрытие браузера и постобработку)
            self._cleanup()
            logger.info(f"[{self.meeting_id}] Основной метод run завершен.")

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
        # Основная очистка произойдет в run() после выхода из цикла
        
    def _cleanup(self):
        """Освобождение ресурсов (вызывается из run)."""
        if self.joined_successfully:
            self._leave_meeting()
        
        if self.joined_successfully:
            logger.info(f"[{self.meeting_id}] Инициализация потока постобработки...")
            self.post_processing_thread = threading.Thread(
                target=self.audio_handler._perform_post_processing,
                name=f'PostProcessor-{self.meeting_id}'
            )
            self.post_processing_thread.start()
            self.post_processing_thread.join() # Ждем завершения обработки

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
        
        logger.info(f"[{self.meeting_id}] Процедура остановки завершена.")

    def send_chat_message(self, message: str):
        """Добавляет сообщение в очередь отправки (потокобезопасно)."""
        self.chat_queue.put(message)

    def _perform_chat_action(self, message: str):
        """Реальная отправка сообщения через Playwright (вызывается в main loop)."""
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
            
            self.page.locator('button').filter(has_text="send").first.click()
            
            logger.info(f"[{self.meeting_id}] ✅ Сообщение в чат успешно отправлено.")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] ❌ Не удалось отправить сообщение в чат: {e}", exc_info=True)
            self._save_screenshot("99_chat_send_error")

