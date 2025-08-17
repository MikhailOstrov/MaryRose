# server/Google_Meet/meet_bot_manager.py (ИСПРАВЛЕННАЯ ВЕРСЯ)

import logging
import time
from typing import Dict
import multiprocessing as mp
from queue import Empty
from api.meet_listener import MeetListenerBot # Убедитесь, что этот импорт есть

logger = logging.getLogger(__name__)

# --- ШАГ 1: Объявляем переменные, но не инициализируем их ---
manager = None
launch_queue = None
active_bots = None
# -----------------------------------------------------------

def initialize_multiprocessing():
    """
    Эта функция будет вызвана ОДИН РАЗ при старте сервера FastAPI.
    Она безопасно настраивает все объекты multiprocessing.
    """
    global manager, launch_queue, active_bots

    try:
        mp.set_start_method('spawn', force=True)
        logging.info("Multiprocessing start method set to 'spawn'.")
    except RuntimeError:
        logging.warning("Multiprocessing start method already set.")

    manager = mp.Manager()
    launch_queue = manager.Queue()
    active_bots = manager.dict()
    logging.info("Multiprocessing manager, queue, and dict initialized successfully.")


def run_bot_in_process(meeting_id: str, meet_url: str, startup_complete_event: mp.Event):
    """
    Эта функция-обертка выполняется в ОТДЕЛЬНОМ ПРОЦЕССЕ для каждого бота.
    """
    bot = None
    try:
        logger.info(f"[{meeting_id}] ПРОЦЕСС для бота запущен. Начинаю инициализацию...")
        bot = MeetListenerBot(meeting_url=meet_url, meeting_id=meeting_id)
        bot.run(startup_complete_event=startup_complete_event)
    except Exception as e:
        logger.error(f"[{meeting_id}] Ошибка в ПРОЦЕССЕ бота: {e}", exc_info=True)
        if not startup_complete_event.is_set():
            startup_complete_event.set()
    finally:
        logger.info(f"[{meeting_id}] ПРОЦЕСС бота завершил работу.")

def launch_worker():
    """
    Функция-воркер, которая ПОСЛЕДОВАТЕЛЬНО запускает процессы ботов.
    """
    logger.info("✅ Воркер-диспетчер (multiprocessing, spawn) запущен и готов к работе.")
    
    while True:
        try:
            # Проверяем, что объекты инициализированы, прежде чем их использовать
            if launch_queue is None or active_bots is None:
                time.sleep(1)
                continue

            meeting_id, meet_url = launch_queue.get(timeout=1)
            logger.info(f"[Воркер] Получена задача на запуск бота для встречи {meeting_id}.")
            
            if meeting_id in active_bots:
                logger.warning(f"[Воркер] Бот для {meeting_id} уже активен. Пропускаю.")
                continue

            startup_complete_event = mp.Event()
            bot_process = mp.Process(
                target=run_bot_in_process, 
                args=(meeting_id, meet_url, startup_complete_event)
            )
            bot_process.daemon = True
            bot_process.name = f"BotProcess-{meeting_id}"
            bot_process.start()
            
            active_bots[meeting_id] = bot_process.pid
            
            logger.info(f"[Воркер] Процесс для бота {meeting_id} (PID: {bot_process.pid}) запущен. Ожидаю сигнала...")

            completed_in_time = startup_complete_event.wait(timeout=180.0)

            if completed_in_time:
                logger.info(f"[Воркер] ✅ Сигнал от бота {meeting_id} получен. Беру следующую задачу.")
            else:
                logger.error(f"[Воркер] ❌ Сигнал от бота {meeting_id} не получен за 180 секунд! Завершаю процесс принудительно.")
                if bot_process.is_alive():
                    bot_process.terminate()
                    bot_process.join()
                if meeting_id in active_bots:
                    del active_bots[meeting_id]

        except Empty:
            time.sleep(0.1)
            continue
        except Exception as e:
            logger.critical(f"[Воркер] ❌ КРИТИЧЕСКАЯ ОШИБКА в цикле воркера: {e}", exc_info=True)
            time.sleep(30)