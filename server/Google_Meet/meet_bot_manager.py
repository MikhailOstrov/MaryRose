# server/Google_Meet/meet_bot_manager.py (ФИНАЛЬНАЯ ВЕРСИЯ ДЛЯ ПАРАЛЛЕЛЬНОЙ РАБОТЫ)

import logging
import queue
import threading
import time
from typing import Dict

from api.meet_listener import MeetListenerBot

logger = logging.getLogger(__name__)

# --- Глобальные объекты для управления ---

# Словарь для отслеживания активных, работающих ботов.
active_bots: Dict[str, MeetListenerBot] = {}

# Потокобезопасная очередь для "заявок" на запуск ботов.
# Хранит кортежи: (meeting_id, meet_url)
launch_queue = queue.Queue()

# -------------------------------------------------

def run_bot_in_thread(meeting_id: str, meet_url: str):
    """
    Эта функция-обертка выполняется в отдельном потоке для каждого бота.
    Она управляет полным жизненным циклом одного бота.
    """
    bot = None
    try:
        logger.info(f"[{meeting_id}] Поток для бота запущен. Начинаю инициализацию...")
        bot = MeetListenerBot(meeting_url=meet_url, meeting_id=meeting_id)
        active_bots[meeting_id] = bot
        
        # bot.run() - это долгая, блокирующая операция.
        # Но так как она запущена в своем потоке, она не мешает другим ботам.
        bot.run()

    except Exception as e:
        logger.error(f"[{meeting_id}] Ошибка в потоке бота: {e}", exc_info=True)
    finally:
        # Очистка после завершения работы бота
        if meeting_id in active_bots:
            del active_bots[meeting_id]
            logger.info(f"[{meeting_id}] Бот завершил работу и удален из активных.")

def launch_worker():
    """
    Функция-воркер, работающая в главном фоновом потоке.
    Берет задачи из очереди и ПОСЛЕДОВАТЕЛЬНО запускает их в НОВЫХ потоках.
    """
    logger.info("✅ Воркер-диспетчер запуска ботов запущен и готов к работе.")
    
    while True:
        try:
            # Ждем появления задачи в очереди
            meeting_id, meet_url = launch_queue.get()
            
            logger.info(f"[Воркер] Получена задача на запуск бота для встречи {meeting_id}.")
            
            if meeting_id in active_bots:
                logger.warning(f"[Воркер] Бот для {meeting_id} уже активен. Пропускаю задачу.")
                launch_queue.task_done()
                continue

            # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ ---
            # Мы не вызываем bot.run() напрямую.
            # Мы создаем НОВЫЙ поток для КАЖДОГО бота и запускаем его.
            bot_thread = threading.Thread(target=run_bot_in_thread, args=(meeting_id, meet_url))
            bot_thread.daemon = True # Поток завершится, если основной процесс умрет
            bot_thread.name = f"BotThread-{meeting_id}"
            bot_thread.start()
            
            logger.info(f"[Воркер] Новый поток для бота {meeting_id} запущен. Бот начнет инициализацию.")

            # Сообщаем очереди, что задача "взята в работу".
            launch_queue.task_done()
            
            # !!! ВАЖНАЯ ПАУЗА !!!
            # Ждем N секунд ПОСЛЕ запуска одного бота, ПЕРЕД тем как взять следующую задачу.
            # Это и есть наш механизм защиты от одновременного "холодного старта".
            launch_interval = 25 # секунд
            logger.info(f"[Воркер] Ожидание {launch_interval} секунд перед обработкой следующей задачи в очереди...")
            time.sleep(launch_interval)

        except Exception as e:
            logger.critical(f"[Воркер] ❌ КРИТИЧЕСКАЯ ОШИБКА в цикле воркера: {e}", exc_info=True)
            time.sleep(30)