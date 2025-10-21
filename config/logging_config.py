import logging
import sys
from logging.handlers import TimedRotatingFileHandler

LOGS_DIR = "/workspace/logs"
FORMATTER = logging.Formatter(
    "%(asctime)s — %(name)s — %(levelname)s — %(message)s"
)


def setup_logging():
    """
    Настраивает корневой логгер для захвата всех логов в приложении.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)  # Устанавливаем глобальный уровень

    # Очищаем существующих обработчиков, чтобы избежать дублирования,
    # особенно от uvicorn.
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Обработчик для вывода в консоль (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(FORMATTER)
    root_logger.addHandler(console_handler)

    # Обработчик для записи в файл с ротацией
    file_handler = TimedRotatingFileHandler(
        f"{LOGS_DIR}/app.log", when="midnight", backupCount=10, encoding="utf-8"
    )
    file_handler.setFormatter(FORMATTER)
    root_logger.addHandler(file_handler)

    logging.info("Root logger has been configured successfully.")
