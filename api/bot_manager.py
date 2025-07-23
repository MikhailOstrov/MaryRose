# api/bot_manager.py
from api.meet_bot import MeetBot

active_bots = {}

def start_bot_session(meeting_id: str, meet_url: str):
    if meeting_id in active_bots:
        return {"status": "already_running"}
    
    bot = MeetBot(meet_url=meet_url, meeting_id=meeting_id)
    active_bots[meeting_id] = bot
    bot.start()
    
    return {"status": "started"}

def stop_bot_session(meeting_id: str):
    if meeting_id not in active_bots:
        return {"status": "not_found"}

    bot = active_bots[meeting_id]
    bot.stop()
    # Бот сам удалит себя из словаря после остановки
    return {"status": "stopping_initiated"}
