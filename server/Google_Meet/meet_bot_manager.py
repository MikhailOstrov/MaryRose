import logging
import subprocess
import os
import signal
import sys
from typing import Dict

logger = logging.getLogger(__name__)

# Словарь для хранения активных ботов: {meeting_id: process_pid}
active_bots: Dict[str, int] = {}

def start_bot_process(meeting_id: str, meet_url: str) -> bool:
    """
    Запускает бота в отдельном, полностью изолированном процессе.
    Возвращает True в случае успеха, False в случае неудачи.
    """
    if get_bot_status(meeting_id) == "active":
        logger.warning(f"Попытка запустить уже работающего бота для meeting_id: {meeting_id}")
        return False
    
    # Команда для запуска нашего нового скрипта
    command = [
        sys.executable,  # Используем тот же интерпретатор Python, что и у FastAPI
        "bot_runner.py",
        "--meeting-id", meeting_id,
        "--meet-url", meet_url
    ]
    
    logger.info(f"Запуск дочернего процесса командой: {' '.join(command)}")
    
    try:
        # subprocess.Popen не блокирует выполнение, он запускает процесс и идет дальше
        process = subprocess.Popen(command)
        active_bots[meeting_id] = process.pid
        logger.info(f"Бот для встречи {meeting_id} успешно запущен в процессе с PID: {process.pid}")
        return True
    except Exception as e:
        logger.error(f"Не удалось запустить процесс бота для {meeting_id}: {e}", exc_info=True)
        return False

def stop_bot_process(meeting_id: str) -> bool:
    """
    Останавливает процесс бота, отправляя ему сигнал SIGTERM для корректного завершения.
    """
    pid = active_bots.get(meeting_id)
    if not pid:
        logger.warning(f"Не найден PID для встречи {meeting_id}. Невозможно остановить.")
        return False
        
    try:
        # Отправляем сигнал SIGTERM, который будет пойман в bot_runner.py
        os.kill(pid, signal.SIGTERM)
        logger.info(f"Отправлен сигнал SIGTERM процессу {pid} (meeting_id: {meeting_id}).")
        # Удаляем из активных, так как команда на остановку дана
        del active_bots[meeting_id]
        return True
    except ProcessLookupError:
        logger.warning(f"Процесс с PID {pid} не найден. Вероятно, он уже завершился самостоятельно.")
        if meeting_id in active_bots:
            del active_bots[meeting_id]
        return False
    except Exception as e:
        logger.error(f"Ошибка при попытке остановить процесс {pid}: {e}", exc_info=True)
        return False

def get_bot_status(meeting_id: str) -> str:
    """
    Проверяет, активен ли процесс бота.
    """
    pid = active_bots.get(meeting_id)
    if not pid:
        return "inactive"
    
    # Проверяем, существует ли такой процесс в системе.
    # Для Linux/macOS это надежный способ.
    try:
        # os.kill с сигналом 0 не убивает процесс, а проверяет его существование
        os.kill(pid, 0)
        return "active"
    except OSError:
        # Процесс умер, но не был удален из словаря. Очищаем.
        logger.warning(f"Процесс {pid} для {meeting_id} не найден. Удаляем запись.")
        del active_bots[meeting_id]
        return "inactive"