# server/Google_Meet/meet_bot_manager.py (НОВАЯ ВЕРСИЯ БЕЗ ВОРКЕРА)

import logging
import queue
import threading
from typing import Dict

from api.meet_listener import MeetListenerBot

logger = logging.getLogger(__name__)

# --- Глобальные объекты для управления ---

# Словарь для отслеживания активных, работающих ботов.
active_bots: Dict[str, MeetListenerBot] = {}

# Потокобезопасная очередь для "заявок" на запуск ботов.
launch_queue = queue.Queue()

# Блокировка, которая гарантирует, что только один фоновый процесс
# в данный момент времени обрабатывает очередь.
queue_processor_lock = threading.Lock()

# -----------------------------------------

def process_launch_queue():
    """
    Эта функция запускается как BackgroundTask.
    Она пытается "захватить" блокировку. Если успешно - она становится
    единственным обработчиком очереди, пока та не опустеет.
    """
    # Пытаемся захватить блокировку в неблокирующем режиме.
    if not queue_processor_lock.acquire(blocking=False):
        # Если не удалось, значит, другой фоновый процесс уже обрабатывает очередь.
        # Наша работа не нужна, просто выходим.
        logger.info("[QueueProcessor] Другой обработчик уже активен. Завершаю дублирующий процесс.")
        return

    logger.info("[QueueProcessor] Блокировка получена. Начинаю обработку очереди...")
    try:
        # Обрабатываем все задачи, которые есть в очереди на данный момент.
        while not launch_queue.empty():
            try:
                meeting_id, meet_url = launch_queue.get_nowait()
            except queue.Empty:
                # На случай, если в многопоточной среде очередь опустела между проверкой и извлечением.
                continue

            logger.info(f"[QueueProcessor] Обрабатываю задачу для встречи {meeting_id}.")
            
            # Проверяем, не был ли бот запущен, пока задача была в очереди.
            if meeting_id in active_bots:
                logger.warning(f"[QueueProcessor] Бот для {meeting_id} уже активен. Пропускаю.")
                continue

            # --- Основная логика запуска бота (та же, что и в воркере) ---
            bot = None
            try:
                bot = MeetListenerBot(meeting_url=meet_url, meeting_id=meeting_id)
                active_bots[meeting_id] = bot
                bot.run() # Это долгая, блокирующая операция
            except Exception as e:
                logger.error(f"[QueueProcessor] Ошибка при запуске/работе бота для {meeting_id}: {e}", exc_info=True)
            finally:
                if meeting_id in active_bots:
                    del active_bots[meeting_id]
                    logger.info(f"[QueueProcessor] Бот для {meeting_id} завершил работу и удален.")
            # -----------------------------------------------------------
    finally:
        # Гарантированно освобождаем блокировку, чтобы следующий BackgroundTask мог работать.
        queue_processor_lock.release()
        logger.info("[QueueProcessor] Очередь пуста. Блокировка освобождена.")