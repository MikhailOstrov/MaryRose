# План миграции MaryRose/api/meet_listener.py на Playwright

## 1. Создание нового файла
Мы создадим новый файл `MaryRose/api/meet_listener_pw.py`, чтобы сохранить оригинальную реализацию на Selenium нетронутой до полной проверки работоспособности Playwright версии.

## 2. Основные изменения в логике

### A. Инициализация и запуск (Браузер)
*   **Selenium**: Использовал `undetected_chromedriver` и патчинг бинарника.
*   **Playwright**: Будем использовать `playwright.chromium.launch_persistent_context`. Это позволит сохранить сессию (профиль пользователя) и при этом контролировать процесс запуска.
*   **Аудио**: Продолжим использовать передачу переменных окружения `PULSE_SINK` при запуске процесса Playwright (через аргумент `env`), чтобы сохранить интеграцию с `parec`.
*   **GPU**: Используем аргументы запуска Chrome (`args`) в Playwright для включения аппаратного ускорения, аналогично тому, как мы сделали в Selenium (`--enable-gpu-rasterization` и т.д.).

### B. Взаимодействие с сетью (Network Interception) - **Главная фича**
*   Вместо CSS-инъекций для скрытия видео, мы будем использовать `page.route` для **полной блокировки загрузки** медиа-ресурсов.
*   Правило: `route.abort()` для всех запросов типа `image`, `media` (видео/аудио стримы не от Google Meet, если такие есть), и, возможно, фильтрация WebRTC трафика (хотя Meet использует WebSocket + UDP, мы можем блокировать подгрузку "тяжелых" ассетов).

### C. Логика входа (Google Meet)
*   Перепишем селекторы `By.XPATH` на Playwright Locators (`page.locator(...)`).
*   Логика ожидания (`WebDriverWait`) заменится на встроенные в Playwright `expect` и `await locator.wait_for()`.
*   Обработка диалогов (микрофон, разрешение) останется похожей, но через API Playwright.

### D. Взаимодействие с чатом и элементами
*   `send_chat_message`: Перепишем на `await page.fill(...)` и `await page.click(...)`.
*   `_monitor_participants`: Перепишем на `await locator.text_content()`.

## 3. Структура нового класса `MeetListenerBotPW`

```python
class MeetListenerBotPW:
    def __init__(self, ...):
        # ... инициализация переменных (как было) ...
        self.playwright = None
        self.browser_context = None
        self.page = None

    async def _initialize_browser(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        
        # Настройка аргументов (GPU, PulseAudio)
        args = [
            "--window-size=800,600",
            "--disable-animations",
            "--enable-gpu-rasterization",
            "--ignore-gpu-blocklist",
            "--use-gl=desktop",
            # ... остальные флаги ...
        ]
        
        # Запуск с сохранением профиля
        self.browser_context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.chrome_profile_path,
            headless=False, # Или True
            args=args,
            env={**os.environ, "PULSE_SINK": self.sink_name} # Проброс PulseAudio
        )
        
        self.page = self.browser_context.pages[0]
        
        # БЛОКИРОВКА РЕСУРСОВ (СЕТЬ)
        await self.page.route("**/*", self._handle_route)

    async def _handle_route(self, route):
        if route.request.resource_type in ["image", "media", "font"]:
            await route.abort()
        else:
            await route.continue_()

    # ... остальные методы (join, leave, send_message) переписанные под async/await ...
```

## 4. Синхронность vs Асинхронность
Playwright лучше всего работает в `async` режиме.
*   Текущий код `MeetListenerBot` работает в потоках (`threading`).
*   **Вариант 1 (Простой)**: Использовать `sync_playwright`. Тогда структура кода останется почти такой же (потоки + синхронные вызовы). Это проще для миграции.
*   **Вариант 2 (Правильный)**: Переписать бота на `asyncio`. Это потребует изменения `main.py` или того места, откуда вызывается бот.

**Решение для начала:** Используем **Sync API** (`from playwright.sync_api import sync_playwright`). Это позволит оставить архитектуру на потоках (`threading`), но получить преимущества Playwright в управлении браузером.

## 5. План реализации
1.  Установить Playwright: `pip install playwright` и `playwright install chromium`.
2.  Создать `MaryRose/api/meet_listener_pw.py`.
3.  Скопировать логику из `meet_listener.py`, заменяя Selenium вызовы на Playwright Sync API.
4.  Реализовать жесткую блокировку медиа-ресурсов через `page.route`.
5.  Протестировать подключение и аудио (звук должен работать так же, через PulseAudio).

**Вы готовы начать создание файла `meet_listener_pw.py` с использованием Sync API?**
