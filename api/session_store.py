from typing import Dict

# Словарь для временной связи между session_id (из WebSocket)
# и meeting_id (из основной базы данных).
# В production-среде это может быть заменено на Redis или другую быструю БД.
session_to_meeting_map: Dict[str, int] = {} 