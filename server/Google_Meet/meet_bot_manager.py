import logging
from api.meet_listener import MeetListenerBot

logger = logging.getLogger(__name__)

active_bots = {}

def run_bot_thread(meeting_id: str, meet_url: str):
    bot = None
    try:
        logger.info(f"Запуск бота в потоке для встречи {meeting_id}")
        bot = MeetListenerBot(meeting_url=meet_url, meeting_id=meeting_id)
        active_bots[meeting_id] = bot
        bot.run()
    except Exception as e:
        logger.error(f"Ошибка при запуске/работе бота для {meeting_id}: {e}", exc_info=True)
    finally:
        if meeting_id in active_bots:
            del active_bots[meeting_id]
            logger.info(f"Бот для встречи {meeting_id} завершил работу и удален из активных.")
