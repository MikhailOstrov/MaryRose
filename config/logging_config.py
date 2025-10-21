import logging
import sys
from logging.handlers import TimedRotatingFileHandler

LOGS_DIR = "/workspace/logs"
FORMATTER = logging.Formatter("%(asctime)s — %(name)s — %(levelname)s — %(message)s")


def get_console_handler():
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(FORMATTER)
    return console_handler


def get_file_handler():
    file_handler = TimedRotatingFileHandler(
        f"{LOGS_DIR}/app.log", when="midnight", backupCount=10, encoding="utf-8"
    )
    file_handler.setFormatter(FORMATTER)
    return file_handler


def get_logger(logger_name):
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)  # Устанавливаем минимальный уровень логирования

    # Предотвращаем двойное логирование
    if not logger.handlers:
        logger.addHandler(get_console_handler())
        logger.addHandler(get_file_handler())

    logger.propagate = False
    return logger
