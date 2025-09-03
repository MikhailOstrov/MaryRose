import argparse
import logging
import sys
import signal
import time
from api.meet_listener import MeetListenerBot

# Настраиваем логирование для вывода в консоль, чтобы его можно было отслеживать
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - [%(threadName)s] - %(message)s'
)
logger = logging.getLogger(__name__)

# Глобальная переменная для хранения экземпляра бота, чтобы обработчик сигнала имел к нему доступ
bot_instance = None

def handle_shutdown_signal(signum, frame):
    """
    Обработчик сигналов SIGINT (Ctrl+C) и SIGTERM (команда kill) для graceful shutdown.
    """
    logger.warning(f"Получен сигнал {signal.Signals(signum).name}. Начинаю корректное завершение...")
    if bot_instance:
        # Вызываем метод stop(), который выполнит всю очистку
        bot_instance.stop()
    logger.info("Завершение процесса бота.")
    # Даем время на завершение всех операций перед выходом
    time.sleep(5) 
    sys.exit(0)

def main():
    """
    Основная функция, которая парсит аргументы, создает и запускает бота.
    """
    global bot_instance

    parser = argparse.ArgumentParser(description="Запускает изолированный экземпляр бота для Google Meet.")
    parser.add_argument("--meeting-id", required=True, help="Уникальный ID для сессии бота.")
    parser.add_argument("--meet-url", required=True, help="URL для подключения к встрече Google Meet.")
    parser.add_argument("--email", required=True, help="Email пользователя для поиска.")
    args = parser.parse_args()

    # Устанавливаем обработчики сигналов
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    
    logger.info(f"Инициализация бота для meeting_id: {args.meeting_id}")

    try:
        # Создаем экземпляр бота и сохраняем его в глобальную переменную
        bot_instance = MeetListenerBot(
            meeting_url=args.meet_url,
            meeting_id=args.meeting_id,
            email=args.email
        )
        # Запускаем основной цикл работы бота. Этот вызов блокирующий.
        bot_instance.run()
    except Exception as e:
        logger.critical(f"Критическая неперехваченная ошибка в процессе бота {args.meeting_id}: {e}", exc_info=True)
    finally:
        # Этот блок гарантирует, что stop() будет вызван, даже если run() завершится с ошибкой
        logger.info("Основной метод run() завершен. Выполняется финальная очистка...")
        if bot_instance and bot_instance.is_running.is_set():
             # Если бот все еще "работает", но run() завершился, вызываем stop()
            bot_instance.stop()
        logger.info(f"Процесс для бота {args.meeting_id} полностью завершен.")

if __name__ == "__main__":
    main()