import logging
import os
import sys


def setup_logging():
    """
    Настраивает двойное логирование: в консоль (уровень INFO) и в файл (уровень DEBUG).
    Автоматически создает директорию для логов, если она не существует.
    """
    # 1. Получаем корневой логгер. Настраивая его, мы настраиваем логирование для всего приложения.
    logger = logging.getLogger()
    # Устанавливаем самый низкий уровень логирования. 
    # Это позволяет обработчикам самим решать, какие сообщения фильтровать.
    logger.setLevel(logging.DEBUG)

    # Убираем все предыдущие обработчики, чтобы избежать дублирования логов
    # при повторном вызове функции (например, в тестах или при перезагрузке модуля).
    if logger.hasHandlers():
        logger.handlers.clear()

    # 2. Создаем форматтер, используя ваш детальный формат из первого примера.
    # Он будет одинаковым для консоли и файла.
    log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(threadName)s] - %(message)s'
    formatter = logging.Formatter(log_format)

    # 3. Настраиваем обработчик для вывода логов в КОНСОЛЬ (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    # Для консоли ставим уровень INFO, чтобы не засорять вывод отладочными сообщениями.
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # 4. Настраиваем обработчик для записи логов в ФАЙЛ
    # Определяем путь к директории логов относительно текущего файла,
    # чтобы он был независим от операционной системы и рабочего каталога.
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base_dir, 'logs')

    # Создаем директорию для логов, если она еще не существует.
    os.makedirs(log_dir, exist_ok=True)
    
    log_file_path = os.path.join(log_dir, 'app.log')
    file_handler = logging.FileHandler(
        log_file_path,
        mode='a', # 'a' - append, дозапись в конец файла
        encoding='utf-8' # Важно для поддержки кириллицы и других символов
    )
    # В файл пишем всё, начиная с уровня DEBUG, для максимальной детализации при разборе проблем.
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # 5. Добавляем настроенные обработчики к корневому логгеру
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    logging.info(f"Logging setup is complete. Logs will be sent to console and file: {log_file_path}")

