# server/Google_Meet/meet_bot_manager.py (ФИНАЛЬНАЯ ВЕРСИЯ С СИГНАЛОМ ПОСЛЕ ВХОДА)

import logging
import queue
import threading
import time
from typing import Dict

from api.meet_listener import MeetListenerBot

logger = logging.getLogger(__name__)

active_bots: Dict[str, MeetListenerBot] = {}
launch_queue = queue.Queue()


def run_bot_in_thread(meeting_id: str, meet_url: str, startup_complete_event: threading.Event):
    """
    Эта функция-обертка выполняется в отдельном потоке для каждого бота.
    Она создает бота и передает ему "сигнальный флажок".
    """
    bot = None
    try:
        logger.info(f"[{meeting_id}] Поток для бота запущен. Начинаю инициализацию...")
        bot = MeetListenerBot(meeting_url=meet_url, meeting_id=meeting_id)
        active_bots[meeting_id] = bot
        
        # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ ---
        # Мы передаем "сигнальный флажок" в метод run().
        # Теперь сам бот будет отвечать за то, чтобы подать сигнал в нужный момент.
        bot.run(startup_complete_event=startup_complete_event)

    except Exception as e:
        logger.error(f"[{meeting_id}] Ошибка в потоке бота: {e}", exc_info=True)
        # Если инициализация упала, тоже нужно подать сигнал, чтобы не заблокировать очередь
        if not startup_complete_event.is_set():
            startup_complete_event.set()
    finally:
        if meeting_id in active_bots:
            del active_bots[meeting_id]
            logger.info(f"[{meeting_id}] Бот завершил работу и удален из активных.")

def launch_worker():
    """
    Функция-воркер, которая ПОСЛЕДОВАТЕЛЬНО запускает ботов,
    дожидаясь сигнала о ПОЛНОМ ВХОДЕ В КОНФЕРЕНЦИЮ от каждого.
    """
    logger.info("✅ Воркер-диспетчер (с Event после входа) запущен и готов к работе.")
    
    while True:
        try:
            meeting_id, meet_url = launch_queue.get()
            logger.info(f"[Воркер] Получена задача на запуск бота для встречи {meeting_id}.")
            
            if meeting_id in active_bots:
                logger.warning(f"[Воркер] Бот для {meeting_id} уже активен. Пропускаю.")
                launch_queue.task_done()
                continue

            # Создаем уникальный "сигнальный флажок" для этого запуска
            startup_complete_event = threading.Event()

            bot_thread = threading.Thread(
                target=run_bot_in_thread, 
                args=(meeting_id, meet_url, startup_complete_event) # Передаем флажок в поток
            )
            bot_thread.daemon = True
            bot_thread.name = f"BotThread-{meeting_id}"
            bot_thread.start()
            
            logger.info(f"[Воркер] Поток для бота {meeting_id} запущен. Ожидаю сигнала об успешном входе в созвон...")

            # Ждем "зеленого света", но не дольше 180 секунд (увеличиваем таймаут, т.к. он включает ожидание хоста)
            completed_in_time = startup_complete_event.wait(timeout=180.0)

            if completed_in_time:
                logger.info(f"[Воркер] ✅ Сигнал от бота {meeting_id} получен. Беру следующую задачу.")
            else:
                logger.error(f"[Воркер] ❌ Сигнал от бота {meeting_id} не получен за 180 секунд! Возможно, вход завис или не был одобрен.")
            
            launch_queue.task_done()

        except Exception as e:
            logger.critical(f"[Воркер] ❌ КРИТИЧЕСКАЯ ОШИБКА в цикле воркера: {e}", exc_info=True)
            time.sleep(30)