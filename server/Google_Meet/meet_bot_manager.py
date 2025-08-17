# server/Google_Meet/meet_bot_manager.py (ВЕРСИЯ С MULTIPROCESSING)

import logging
import time
from typing import Dict
# Меняем импорты на multiprocessing
import multiprocessing as mp

from api.meet_listener import MeetListenerBot

logger = logging.getLogger(__name__)

# --- КЛЮЧЕВЫЕ ИЗМЕНЕНИЯ ДЛЯ МЕЖПРОЦЕССНОГО ВЗАИМОДЕЙСТВИЯ ---

# 1. Создаем менеджер для общих объектов
manager = mp.Manager()

# 2. Создаем межпроцессную очередь
launch_queue = manager.Queue()

# 3. Создаем общий словарь (proxy-объект), который будет доступен из всех процессов
active_bots = manager.dict()

# --------------------------------------------------------------------


def run_bot_in_process(meeting_id: str, meet_url: str, startup_complete_event: mp.Event):
    """
    Эта функция-обертка выполняется в ОТДЕЛЬНОМ ПРОЦЕССЕ для каждого бота.
    """
    bot = None
    try:
        logger.info(f"[{meeting_id}] ПРОЦЕСС для бота запущен. Начинаю инициализацию...")
        # MeetListenerBot остается почти без изменений, он просто получает Event от multiprocessing
        bot = MeetListenerBot(meeting_url=meet_url, meeting_id=meeting_id)
        
        # ВАЖНО: Мы не можем хранить сам объект bot в общем словаре,
        # так как он несериализуем. Мы можем хранить его состояние или PID.
        # Для простоты, мы будем управлять им через API, а здесь просто запускать.
        # active_bots[meeting_id] = bot # <-- Так делать нельзя с объектами, содержащими драйвер
        
        bot.run(startup_complete_event=startup_complete_event)

    except Exception as e:
        logger.error(f"[{meeting_id}] Ошибка в ПРОЦЕССЕ бота: {e}", exc_info=True)
        if not startup_complete_event.is_set():
            startup_complete_event.set()
    finally:
        # Процесс завершился, информация о нем должна быть удалена из active_bots
        # Это будет сделано в API-хендлере stop или при проверке статуса
        logger.info(f"[{meeting_id}] ПРОЦЕСС бота завершил работу.")


def launch_worker():
    """
    Функция-воркер, которая ПОСЛЕДОВАТЕЛЬНО запускает процессы ботов.
    """
    logger.info("✅ Воркер-диспетчер (multiprocessing) запущен и готов к работе.")
    
    while True:
        try:
            meeting_id, meet_url = launch_queue.get()
            logger.info(f"[Воркер] Получена задача на запуск бота для встречи {meeting_id}.")
            
            # Проверка на активность остается, но теперь она работает с общим словарем
            if meeting_id in active_bots:
                logger.warning(f"[Воркер] Бот для {meeting_id} уже активен. Пропускаю.")
                continue

            # Создаем межпроцессный Event
            startup_complete_event = mp.Event()

            # Создаем и запускаем ПРОЦЕСС вместо потока
            bot_process = mp.Process(
                target=run_bot_in_process, 
                args=(meeting_id, meet_url, startup_complete_event)
            )
            bot_process.daemon = True
            bot_process.name = f"BotProcess-{meeting_id}"
            bot_process.start()
            
            # Сохраняем в общем словаре PID процесса, чтобы им можно было управлять
            active_bots[meeting_id] = {'pid': bot_process.pid, 'process': bot_process}
            
            logger.info(f"[Воркер] Процесс для бота {meeting_id} (PID: {bot_process.pid}) запущен. Ожидаю сигнала...")

            completed_in_time = startup_complete_event.wait(timeout=180.0)

            if completed_in_time:
                logger.info(f"[Воркер] ✅ Сигнал от бота {meeting_id} получен. Беру следующую задачу.")
            else:
                logger.error(f"[Воркер] ❌ Сигнал от бота {meeting_id} не получен за 180 секунд! Завершаю процесс принудительно.")
                if bot_process.is_alive():
                    bot_process.terminate() # Принудительно убиваем зависший процесс
                    bot_process.join()
                if meeting_id in active_bots:
                    del active_bots[meeting_id]

        except queue.Empty:
            time.sleep(1)
            continue
        except Exception as e:
            logger.critical(f"[Воркер] ❌ КРИТИЧЕСКАЯ ОШИБКА в цикле воркера: {e}", exc_info=True)
            time.sleep(30)