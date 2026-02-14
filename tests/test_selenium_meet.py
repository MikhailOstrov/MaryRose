"""
Автономный тестовый файл для экспериментов с Selenium + Google Meet.
Без аудио, без загрузки моделей. Только: подключение, чат, проверки.

Зависимости: selenium, undetected-chromedriver
Запуск из корня MaryRose:
  python tests/test_selenium_meet.py
  или: python -m tests.test_selenium_meet  (из папки MaryRose)
"""

import os
import sys
import time
import random
import shutil
import logging
import threading
from pathlib import Path
from datetime import datetime

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# --- Минимальная конфигурация (без импорта config — там torch, openai и т.д.) ---
TESTS_DIR = Path(__file__).resolve().parent
MARYROSE_ROOT = TESTS_DIR.parent
CHROME_PROFILE_DIR = MARYROSE_ROOT / "chrome_profile"
MEET_GUEST_NAME = "Mary"
SCREENSHOTS_DIR = TESTS_DIR / "screenshots"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

CHROME_LAUNCH_LOCK = threading.Lock()


class MeetSeleniumBot:
    """
    Бот для подключения к Google Meet через Selenium.
    Без аудио. Только: Chrome, Meet, чат, мониторинг участников, таймер.
    """

    def __init__(self, meeting_url: str, meeting_id: str, email: str = "", remaining_seconds: int = 3600):
        self.meeting_url = meeting_url
        self.meeting_id = meeting_id
        self.email = email
        self.remaining_seconds = remaining_seconds

        self.notified_10_min = remaining_seconds <= 600
        self.notified_5_min = False
        self.driver = None

        self.is_running = threading.Event()
        self.is_running.set()
        self.output_dir = SCREENSHOTS_DIR / self.meeting_id
        self.joined_successfully = False
        self.post_processing_thread = None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Скриншоты будут сохраняться в: '{self.output_dir}'")

        self.captions_transcript: list[dict[str, str]] = []  # [{"speaker": "...", "text": "..."}, ...]
        self.participant_names: set[str] = set()  # Имена участников, только пополнение

        self.chrome_profile_path = Path(CHROME_PROFILE_DIR) / self.meeting_id
        if self.chrome_profile_path.exists():
            shutil.rmtree(self.chrome_profile_path)
        self.chrome_profile_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"[{self.meeting_id}] Временный профиль Chrome: '{self.chrome_profile_path}'")

    def _monitor_remaining_seconds(self):
        threading.current_thread().name = f'RemainingSecondsMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг оставшегося времени запущен.")
        while self.is_running.is_set() and self.remaining_seconds > 0:
            if self.remaining_seconds <= 600 and not self.notified_10_min:
                self.send_chat_message("Оставшееся время: 10 минут. Через 10 минут ассистент завершит работу.")
                logger.info(f"[{self.meeting_id}] Оставшееся время: {self.remaining_seconds} секунд. Через 10 минут ассистент завершит работу.")
                self.notified_10_min = True
            if self.remaining_seconds <= 300 and not self.notified_5_min:
                self.send_chat_message("Оставшееся время: 5 минут. Через 5 минут ассистент завершит работу.")
                logger.info(f"[{self.meeting_id}] Оставшееся время: {self.remaining_seconds} секунд. Через 5 минут ассистент завершит работу.")
                self.notified_5_min = True

            if self.remaining_seconds >= 13 * 60:
                time.sleep(60)
                self.remaining_seconds -= 60
            else:
                time.sleep(1)
                self.remaining_seconds -= 1

        if self.remaining_seconds <= 0 and self.is_running.is_set():
            logger.info(f"[{self.meeting_id}] Оставшееся время закончилось. Завершаю работу.")
            try:
                self.send_chat_message("Оставшееся время закончилось. Ассистент завершает работу.")
                time.sleep(2)
            except Exception as e:
                logger.warning(f"[{self.meeting_id}] Не удалось отправить сообщение в чат: {e}")
            finally:
                self.stop()
        else:
            logger.info(f"[{self.meeting_id}] Мониторинг оставшегося времени остановлен.")

    def _get_participant_count(self) -> int | None:
        """Пытается получить количество участников несколькими способами."""
        try:
            locator_xpath = "//button[.//i[text()='people'] and @aria-label]"
            element = self.driver.find_element(By.XPATH, locator_xpath)
            aria_label = element.get_attribute('aria-label') or ""
            numbers = ''.join(filter(str.isdigit, aria_label))
            if numbers:
                logger.debug(f"[{self.meeting_id}] Способ 1 (aria-label) нашел: {numbers}")
                return int(numbers)
        except Exception:
            logger.debug(f"[{self.meeting_id}] Способ 1 (aria-label) не сработал.")

        try:
            locator_xpath = '//span[text()="Участники" or text()="People"]/following-sibling::div//div[string-length(normalize-space(text())) > 0]'
            element = self.driver.find_element(By.XPATH, locator_xpath)
            count_text = element.text
            if count_text and count_text.isdigit():
                logger.debug(f"[{self.meeting_id}] Способ 2 (текст) нашел: {count_text}")
                return int(count_text)
        except Exception:
            logger.debug(f"[{self.meeting_id}] Способ 2 (текст) не сработал.")

        return None

    def _monitor_participants(self):
        """Отслеживает количество участников. Если бот остается один, он завершает работу."""
        threading.current_thread().name = f'ParticipantMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг участников запущен.")

        consecutive_failures = 0
        max_failures = 2

        while self.is_running.is_set():
            for _ in range(15):
                if not self.is_running.is_set():
                    logger.info(f"[{self.meeting_id}] Мониторинг участников остановлен.")
                    return
                time.sleep(1)

            count = self._get_participant_count()

            if count is not None:
                logger.info(f"[{self.meeting_id}] Текущее количество участников: {count}")
                consecutive_failures = 0
                if count <= 1:
                    logger.warning(f"[{self.meeting_id}] Встреча пуста. Завершаю работу...")
                    self.stop()
                    return
            else:
                consecutive_failures += 1
                logger.warning(f"[{self.meeting_id}] Не удалось найти счетчик участников. Попытка {consecutive_failures}/{max_failures}.")

            if consecutive_failures >= max_failures:
                logger.error(f"[{self.meeting_id}] Не удалось найти счетчик участников {max_failures} раз подряд. Предполагаю, что встреча завершена.")
                self.stop()
                return

    def _monitor_participant_names(self):
        """
        Каждые 20 секунд открывает панель участников, получает имена (полный список),
        закрывает панель. Только пополняет self.participant_names, не удаляет.
        """
        threading.current_thread().name = f'ParticipantNamesMonitor-{self.meeting_id}'
        logger.info(f"[{self.meeting_id}] Мониторинг имён участников запущен.")

        people_btn_xpath = "//button[.//i[text()='people'] and @aria-label]"

        def _is_not_number(s: str) -> bool:
            return not s.replace(".", "").replace("-", "").isdigit()

        while self.is_running.is_set():
            try:
                # Возвращаем фокус в окно браузера (чтобы Meet подгружал участников в фоне)
                try:
                    self.driver.switch_to.window(self.driver.current_window_handle)
                    self.driver.execute_script("window.focus();")
                except Exception:
                    pass

                try:
                    btn = self.driver.find_element(By.XPATH, people_btn_xpath)
                    btn.click()
                    time.sleep(0.8)
                except Exception:
                    pass

                elements = self.driver.find_elements(By.CSS_SELECTOR, "div[data-participant-id] span.notranslate")
                names = [
                    el.text.strip()
                    for el in elements
                    if el.text and len(el.text.strip()) > 1 and _is_not_number(el.text.strip())
                ]

                try:
                    btn = self.driver.find_element(By.XPATH, people_btn_xpath)
                    btn.click()
                except Exception:
                    pass
                logger.info(f"[{self.meeting_id}] Участники: {names}")
                if names:
                    before = len(self.participant_names)
                    self.participant_names.update(n for n in names if n)
                    added = len(self.participant_names) - before
                    if added > 0:
                        logger.info(f"[{self.meeting_id}] Участники: {sorted(self.participant_names)} (+{added})")
            except Exception as e:
                logger.debug(f"[{self.meeting_id}] Ошибка получения имён: {e}")

            for _ in range(4):
                if not self.is_running.is_set():
                    return
                time.sleep(1)

        logger.info(f"[{self.meeting_id}] Мониторинг имён участников остановлен.")

    # def _monitor_captions(self):
    #     """
    #     Мониторит субтитры каждые 3–4 сек. Берёт блоки только по иерархии от div[aria-label="Captions"].
    #     Сохраняет в self.captions_transcript список dict: [{"speaker": "...", "text": "..."}].
    #     """
    #     threading.current_thread().name = f'CaptionsMonitor-{self.meeting_id}'
    #     logger.info(f"[{self.meeting_id}] Мониторинг субтитров запущен.")

    #     container_xpath = '//div[@role="region" and @aria-label="Captions"]'
    #     # Блоки: прямые потомки контейнера, у которых 1-й div содержит span (спикер), 2-й div — текст
    #     blocks_xpath = './div[div[1]//span and div[2][normalize-space()!=""]]'

    #     while self.is_running.is_set():
    #         try:
    #             container = self.driver.find_element(By.XPATH, container_xpath)
    #             blocks = container.find_elements(By.XPATH, blocks_xpath)
    #             # Добавляем только новые блоки (по индексу), чтобы сохранять порядок и повторы
    #             for block in blocks[len(self.captions_transcript):]:
    #                 try:
    #                     speaker_el = block.find_element(By.XPATH, ".//div[1]//span")
    #                     text_el = block.find_element(By.XPATH, "./div[2]")
    #                     speaker = (speaker_el.text or "").strip()
    #                     text = (text_el.text or "").strip()
    #                     if not speaker and not text:
    #                         continue
    #                     entry = {"speaker": speaker, "text": text}
    #                     self.captions_transcript.append(entry)
    #                     logger.info(f"[{self.meeting_id}] Субтитры: {speaker}: {text}...")
    #                 except Exception:
    #                     continue
    #         except Exception:
    #             pass

    #         for _ in range(4):
    #             if not self.is_running.is_set():
    #                 return
    #             time.sleep(1)

    #     logger.info(f"[{self.meeting_id}] Мониторинг субтитров остановлен.")

    def _initialize_driver(self):
        """Инициализирует Chrome WebDriver."""
        logger.info(f"[{self.meeting_id}] Запуск Chrome...")

        driver_executable_path = None
        system_driver_path = "/usr/local/bin/chromedriver" if os.name != "nt" else None

        if system_driver_path and os.path.isfile(system_driver_path):
            try:
                driver_copy_path = self.chrome_profile_path / "chromedriver"
                shutil.copy(system_driver_path, driver_copy_path)
                driver_copy_path.chmod(0o755)
                driver_executable_path = str(driver_copy_path)
                logger.info(f"[{self.meeting_id}] Используется копия chromedriver: {driver_copy_path}")
            except Exception as e:
                logger.warning(f"[{self.meeting_id}] Не удалось создать копию chromedriver: {e}. Буду использовать auto.")
        else:
            driver_executable_path = os.environ.get("CHROMEDRIVER_PATH") or "auto"
            logger.info(f"[{self.meeting_id}] chromedriver: {driver_executable_path}")

        with CHROME_LAUNCH_LOCK:
            try:
                opt = uc.ChromeOptions()
                opt.add_argument('--no-sandbox')
                opt.add_argument('--disable-dev-shm-usage')
                opt.add_argument('--window-size=1280,720')
                opt.add_argument(f'--user-data-dir={self.chrome_profile_path}')
                # Отключаем троттлинг в фоне — Meet подгружает участников даже когда окно не в фокусе
                opt.add_argument('--disable-background-timer-throttling')


                port = random.randint(10000, 20000)
                opt.add_argument(f'--remote-debugging-port={port}')
                logger.info(f"[{self.meeting_id}] Порт отладки: {port}")

                opt.add_experimental_option("prefs", {
                    "profile.default_content_setting_values.media_stream_mic": 1,
                    "profile.default_content_setting_values.notifications": 2
                })

                driver_kwargs = dict(
                    options=opt,
                    headless=False,
                    use_subprocess=True,
                )
                if driver_executable_path and driver_executable_path != "auto":
                    driver_kwargs["driver_executable_path"] = driver_executable_path
                # На Windows: driver_executable_path="auto" или из PATH — uc сам найдёт chromedriver

                self.driver = uc.Chrome(**driver_kwargs)

                logger.info(f"[{self.meeting_id}] Chrome успешно запущен.")

                try:
                    self.driver.execute_cdp_cmd("Browser.grantPermissions", {
                        "origin": "https://meet.google.com",
                        "permissions": ["audioCapture"]
                    })
                    logger.info(f"[{self.meeting_id}] Разрешение на микрофон выдано через CDP.")
                except Exception as e_grant:
                    logger.warning(f"[{self.meeting_id}] Не удалось выдать CDP-разрешение: {e_grant}")

            except Exception as e:
                logger.critical(f"[{self.meeting_id}] Ошибка запуска Chrome: {e}", exc_info=True)
                raise

        logger.info(f"[{self.meeting_id}] Блокировка запуска Chrome освобождена.")

    def _save_screenshot(self, name: str):
        """Сохраняет скриншот для отладки."""
        path = self.output_dir / f'{datetime.now().strftime("%H%M%S")}_{name}.png'
        try:
            self.driver.save_screenshot(str(path))
            logger.info(f"[{self.meeting_id}] Скриншот сохранен: {path}")
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось сохранить скриншот '{name}': {e}")

    def _handle_mic_dialog(self) -> bool:
        logger.info(f"[{self.meeting_id}] [MicDialog] Обработка диалога микрофона")
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
        if js_scan_click(without_mic_variants, total_timeout=1):
            logger.info(f"[{self.meeting_id}] Кнопка 'без микрофона' нажата за {time.time()-t0:.2f}s")
            return True
        logger.info(f"[{self.meeting_id}] Диалог микрофона не найден за {time.time()-t0:.2f}s — продолжаю.")
        return False

    def _handle_chrome_permission_prompt(self):
        """Обрабатывает всплывающее окно разрешений Chrome."""
        allow_site_ru = ["Разрешить при нахождении на сайте"]
        allow_site_en = ["Allow on every visit", "Allow while on site", "Always allow on this site"]
        allow_once_ru = ["Разрешить в этот раз"]
        allow_once_en = ["Allow this time", "Allow once"]

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
                "return !!document.querySelector('button, div[role=\"button\"]') && Array.from(document.querySelectorAll('button, div[role=\"button\"]')).some(el => (el.innerText||'').includes('Разрешить при нахождении') || (el.innerText||'').includes('Allow'));"
            )
            if not exists:
                logger.info(f"[{self.meeting_id}] Баннер разрешений не виден — пропускаю.")
                return
        except Exception:
            pass

        if try_click_phrases(allow_site_ru, timeout_each=3) or try_click_phrases(allow_site_en, timeout_each=3):
            self._save_screenshot("02b_permission_allowed_site")
            return
        if try_click_phrases(allow_once_ru, timeout_each=2) or try_click_phrases(allow_once_en, timeout_each=2):
            self._save_screenshot("02b_permission_allowed_once")
            return
        logger.info(f"[{self.meeting_id}] Всплывающее окно разрешений не обнаружено.")

    def join_meet_as_guest(self):
        try:
            logger.info(f"[{self.meeting_id}] Подключаюсь к встрече как гость: {self.meeting_url}")
            self.driver.get(self.meeting_url)

            logger.info(f"[{self.meeting_id}] Ищу поле для ввода имени...")
            name_input_xpath = '//input[@placeholder="Your name" or @aria-label="Your name" or contains(@placeholder, "name")]'
            name_input = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, name_input_xpath))
            )

            logger.info(f"[{self.meeting_id}] Ввожу имя: {MEET_GUEST_NAME}")
            name_input.clear()
            name_input.send_keys(MEET_GUEST_NAME)

            logger.info(f"[{self.meeting_id}] Обработка диалога микрофона...")
            mic_dialog_found = self._handle_mic_dialog()
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

            success_indicators = [
                '//div[contains(@class, "control") and (contains(@class, "bar") or contains(@class, "panel"))]',
                '//button[@aria-label*="hand" or @aria-label*="рука" or @data-tooltip*="hand"]',
                '//button[contains(@aria-label, "caption") or contains(@aria-label, "субтитр")]',
                '//button[@aria-label="Start a chat with all participants"]',
            ]
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
                            logger.info(f"[{self.meeting_id}] Успешно присоединился к встрече! Селектор: {xpath}")
                            self.joined_successfully = True
                            try:
                                self.toggle_mic_hotkey()
                                self.toggle_captions()
                                time.sleep(0.5)
                                self.send_chat_message("""Инструкция по командам:
Обратитесь к Мэри по имени, чтобы она вас услышала.
Вы можете как добавить информацию ("Мэри, запиши...") так и найти информация
из вашей базы знаний ("Мэри, найди..." или "Слушай, Мэри, напомни/поищи...")
По завершению вашего созвона можете сказать "Мэри, заверши встречу", "Мэри, стоп",
либо просто выйдите из созвона, бот в скором времени выйдет сам.""")
                            except Exception as e_toggle:
                                logger.warning(f"[{self.meeting_id}] Не удалось выполнить действия после входа: {e_toggle}")

                            return True
                    except Exception:
                        continue

                for error_xpath in error_indicators:
                    try:
                        error_element = self.driver.find_element(By.XPATH, error_xpath)
                        if error_element.is_displayed():
                            logger.error(f"[{self.meeting_id}] Присоединение отклонено: {error_element.text}")
                            self._save_screenshot("98_join_denied")
                            return False
                    except Exception:
                        continue

                time.sleep(check_interval)
                elapsed_time += check_interval
                if elapsed_time % 30 == 0:
                    logger.info(f"[{self.meeting_id}] Ожидание... {elapsed_time}с прошло.")
                    self._save_screenshot(f"wait_{elapsed_time}s")

            logger.warning(f"[{self.meeting_id}] Превышено время ожидания одобрения ({max_wait_time}с).")
            self._save_screenshot("99_join_timeout")
            return False

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] Критическая ошибка при присоединении: {e}", exc_info=True)
            self._save_screenshot("99_join_fatal_error")
            return False

    def _handle_blocking_dialogs(self):
        """
        Проверяет и закрывает блокирующие диалоги (например, 'Others may see your video differently').
        Использует универсальные селекторы: role='dialog' и кнопки подтверждения.
        """
        logger.info(f"[{self.meeting_id}] Проверка блокирующих диалогов...")
        try:
            # Селекторы для кнопок подтверждения внутри диалогов
            confirm_btn_selectors = [
                '//div[@role="dialog"]//button[@data-mdc-dialog-action="ok"]',  # Стандартная кнопка OK в Material
                '//div[@role="dialog"]//button[.//span[text()="Got it"]]',      # Кнопка с текстом Got it
                '//div[@role="dialog"]//button[.//span[text()="Понятно"]]',     # Кнопка с текстом Понятно
                '//div[@role="dialog"]//button[.//span[text()="OK"]]',          # Кнопка с текстом OK
            ]

            for selector in confirm_btn_selectors:
                try:
                    # Ждём совсем немного, так как окно может и не появиться
                    btn = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    logger.info(f"[{self.meeting_id}] Найдено блокирующее окно. Нажимаю кнопку (селектор: {selector}).")
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.5)
                    return  # Нажали одну — выходим (обычно одно окно за раз)
                except Exception:
                    continue
            
            logger.info(f"[{self.meeting_id}] Блокирующих окон не обнаружено.")

        except Exception as e:
            logger.debug(f"[{self.meeting_id}] Ошибка при обработке диалогов: {e}")

    def run(self):
        logger.info(f"[{self.meeting_id}] Бот запускается...")
        try:
            self._initialize_driver()
            self.joined_successfully = self.join_meet_as_guest()

            if self.joined_successfully:
                logger.info(f"[{self.meeting_id}] Успешно вошел в конференцию, запускаю мониторинг.")

                # Обрабатываем возможные всплывающие окна после входа
                self._handle_blocking_dialogs()

                monitor_thread = threading.Thread(target=self._monitor_participants, name=f'ParticipantMonitor-{self.meeting_id}')
                remaining_seconds_thread = threading.Thread(target=self._monitor_remaining_seconds, name=f'RemainingSecondsMonitor-{self.meeting_id}')
                # captions_thread = threading.Thread(target=self._monitor_captions, name=f'CaptionsMonitor-{self.meeting_id}')
                names_thread = threading.Thread(target=self._monitor_participant_names, name=f'ParticipantNamesMonitor-{self.meeting_id}')

                monitor_thread.start()
                remaining_seconds_thread.start()
                # captions_thread.start()
                names_thread.start()

                monitor_thread.join()
                remaining_seconds_thread.join()
                # captions_thread.join()
                names_thread.join()

                logger.info(f"[{self.meeting_id}] Основные потоки завершены.")
            else:
                logger.warning(f"[{self.meeting_id}] Не удалось присоединиться к встрече.")

        except Exception as e:
            logger.critical(f"[{self.meeting_id}] Критическая ошибка: {e}", exc_info=True)
        finally:
            self.stop()
            logger.info(f"[{self.meeting_id}] Основной метод run завершен.")

    def _leave_meeting(self):
        if not self.driver or not self.joined_successfully:
            logger.info(f"[{self.meeting_id}] Пропускаю выход — драйвер не инициализирован или не был в конференции.")
            return

        try:
            logger.info(f"[{self.meeting_id}] Пытаюсь покинуть встречу...")

            leave_button_selectors = [
                '//button[@aria-label="Leave call"]',
                '//button[.//i[text()="call_end"]]',
                '//button[contains(@aria-label, "Leave") or contains(@aria-label, "Покинуть")]',
                '//div[@role="tooltip" and (contains(., "Leave a video meeting") or contains(., "Покинуть видеовстречу"))]/preceding-sibling::button',
            ]

            for selector in leave_button_selectors:
                try:
                    leave_button = WebDriverWait(self.driver, 1).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", leave_button)
                    time.sleep(0.2)
                    leave_button.click()
                    logger.info(f"[{self.meeting_id}] Кнопка 'Покинуть встречу' нажата (селектор: {selector})")
                    break
                except Exception as e:
                    logger.debug(f"[{self.meeting_id}] Селектор '{selector}' не сработал: {e}")
                    continue
            else:
                logger.warning(f"[{self.meeting_id}] Не удалось найти кнопку 'Покинуть встречу'.")

            time.sleep(1)

        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при попытке покинуть встречу: {e}")

    def stop(self):
        if not self.is_running.is_set():
            return

        logger.info(f"[{self.meeting_id}] Получена команда на завершение...")
        self.is_running.clear()

        if self.joined_successfully:
            self._leave_meeting()

        if self.captions_transcript:
            transcript_path = self.output_dir / f"{self.meeting_id}_transcript.txt"
            try:
                with open(transcript_path, "w", encoding="utf-8") as f:
                    for entry in self.captions_transcript:
                        f.write(f"{entry['speaker']}: {entry['text']}\n")
                logger.info(f"[{self.meeting_id}] Транскрипт сохранён: {transcript_path}")
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка сохранения транскрипта: {e}")

        if self.participant_names:
            names_list = sorted(self.participant_names)
            print(f"\n--- Участники встречи ({len(names_list)}) ---")
            for name in names_list:
                print(f"  • {name}")
            print("--------------------------------\n")

        if self.driver:
            try:
                logger.info(f"[{self.meeting_id}] Закрытие WebDriver...")
                self.driver.quit()
            except Exception as e:
                logger.error(f"[{self.meeting_id}] Ошибка при закрытии WebDriver: {e}")

        try:
            if self.chrome_profile_path.exists():
                logger.info(f"[{self.meeting_id}] Удаление временного профиля Chrome: {self.chrome_profile_path}")
                shutil.rmtree(self.chrome_profile_path, ignore_errors=True)
                logger.info(f"[{self.meeting_id}] Профиль Chrome удален.")
        except Exception as e:
            logger.error(f"[{self.meeting_id}] Ошибка при удалении профиля Chrome: {e}")

        logger.info(f"[{self.meeting_id}] Процедура остановки завершена.")

    def send_chat_message(self, message: str):
        if not self.driver or not self.joined_successfully:
            logger.warning(f"[{self.meeting_id}] Пропускаю отправку: бот не в конференции.")
            return

        logger.info(f"[{self.meeting_id}] Отправка сообщения в чат: '{message[:50]}...'")

        try:
            try:
                WebDriverWait(self.driver, 2).until(
                    EC.presence_of_element_located((By.XPATH, '//textarea[contains(@aria-label, "Send a message")]'))
                )
                logger.info(f"[{self.meeting_id}] Панель чата уже открыта.")
            except Exception:
                logger.info(f"[{self.meeting_id}] Панель чата закрыта, открываю...")
                chat_button_xpath = '//button[contains(@aria-label, "Chat with everyone") or contains(@aria-label, "Чат со всеми")]'
                chat_button = WebDriverWait(self.driver, 4).until(
                    EC.element_to_be_clickable((By.XPATH, chat_button_xpath))
                )
                self.driver.execute_script("arguments[0].click();", chat_button)

            textarea_xpath = '//textarea[contains(@aria-label, "Send a message") or contains(@aria-label, "Отправить сообщение")]'
            message_input = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, textarea_xpath))
            )

            message_input.clear()
            # В Meet: Enter = отправить, Shift+Enter = новая строка
            lines = message.split('\n')
            for i, line in enumerate(lines):
                message_input.send_keys(line)
                if i < len(lines) - 1:
                    ActionChains(self.driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
            time.sleep(0.3)
            message_input.send_keys(Keys.RETURN)  # Отправка
            logger.info(f"[{self.meeting_id}] Сообщение в чат отправлено.")

        except Exception as e:
            logger.error(f"[{self.meeting_id}] Не удалось отправить сообщение в чат: {e}", exc_info=True)
            self._save_screenshot("99_chat_send_error")

    def toggle_mic_hotkey(self):
        """Эмуляция Ctrl+D для переключения микрофона в Meet."""
        try:
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
            logger.info(f"[{self.meeting_id}] Отправлено Ctrl+D (toggle mic)")
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось отправить Ctrl+D: {e}")

    def toggle_captions(self):
        """
        Переключает субтитры в Meet.
        Сначала пробует горячую клавишу (c или Shift+C), затем клик по кнопке.
        """
        # 1. Горячая клавиша c или Shift+C
        try:
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
            actions.send_keys('c').perform()
            logger.info(f"[{self.meeting_id}] Отправлена горячая клавиша c (toggle captions)")
            return
        except Exception as e:
            logger.debug(f"[{self.meeting_id}] Горячая клавиша c не сработала: {e}")

        # 2. Fallback: клик по кнопке по структуре (button > i с иконкой субтитров)
        # OFF: closed_caption_off, ON: closed_caption (без _off)
        captions_xpath = '//button[.//i[contains(text(), "closed_caption")]]'
        try:
            btn = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, captions_xpath))
            )
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
            btn.click()
            logger.info(f"[{self.meeting_id}] Кнопка субтитров нажата")
        except Exception as e:
            logger.warning(f"[{self.meeting_id}] Не удалось переключить субтитры: {e}")


def main():
    """Точка входа для ручного запуска."""
    meeting_url = input("Вставьте ссылку на Meet: ").strip()
    if not meeting_url:
        return

    if not os.getenv("CHROMEDRIVER_PATH"):
        os.environ["CHROMEDRIVER_PATH"] = "auto"

    bot = MeetSeleniumBot(
        meeting_url=meeting_url,
        meeting_id="test_session",
        email="test@test.com",
        remaining_seconds=3600
    )

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nОстанавливаю...")
        bot.stop()
    except Exception as e:
        print(f"Ошибка: {e}")
        bot.stop()


if __name__ == "__main__":
    main()
