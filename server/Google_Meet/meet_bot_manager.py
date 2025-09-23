import logging
import subprocess
import os
import signal
import sys
from typing import Dict

logger = logging.getLogger(__name__)

# Словарь для хранения активных ботов: {meeting_id: process_pid}
active_bots: Dict[str, int] = {}

def start_bot_process(meeting_id: str, meet_url: str, email: str, remaining_seconds: int) -> bool:
    """
    Запускает бота в отдельном процессе с собственным виртуальным дисплеем.
    """
    if get_bot_status(meeting_id) == "active":
        logger.warning(f"Попытка запустить уже работающего бота для meeting_id: {meeting_id}")
        return False
    
    # --- ИЗМЕНЕНИЕ: Формируем команду с xvfb-run ---
    command = [
        "xvfb-run",
        # Эта опция автоматически найдет свободный номер дисплея
        "--auto-servernum",
        # Задаем параметры экрана, как было в entrypoint
        "--server-args=-screen 0 1280x720x16 -nolisten tcp",
        
        # Далее идет наша оригинальная команда
        sys.executable,
        "bot_runner.py",
        "--meeting-id", meeting_id,
        "--meet-url", meet_url,
        "--email", email,
        "--remaining-seconds", str(remaining_seconds)
    ]
    
    logger.info(f"Запуск дочернего процесса командой: {' '.join(command)}")
    
    try:
        process = subprocess.Popen(command)
        active_bots[meeting_id] = process.pid
        logger.info(f"Бот для встречи {meeting_id} успешно запущен в процессе с PID: {process.pid}")
        return True
    except FileNotFoundError:
        logger.critical("❌ КОМАНДА 'xvfb-run' НЕ НАЙДЕНА! Установите пакет 'xvfb' в ваш Dockerfile.")
        return False
    except Exception as e:
        logger.error(f"Не удалось запустить процесс бота для {meeting_id}: {e}", exc_info=True)
        return False

# Функции stop_bot_process и get_bot_status остаются без изменений

def stop_bot_process(meeting_id: str) -> bool:
    """
    Останавливает процесс бота, отправляя ему сигнал SIGTERM для корректного завершения.
    """
    pid = active_bots.get(meeting_id)
    if not pid:
        logger.warning(f"Не найден PID для встречи {meeting_id}. Невозможно остановить.")
        return False
        
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info(f"Отправлен сигнал SIGTERM процессу {pid} (meeting_id: {meeting_id}).")
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
    
    try:
        os.kill(pid, 0)
        return "active"
    except OSError:
        logger.warning(f"Процесс {pid} для {meeting_id} не найден. Удаляем запись.")
        del active_bots[meeting_id]
        return "inactive"