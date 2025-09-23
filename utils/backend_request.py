import requests
import os

from config.config import logger

# Функция отправки результатов на внешний сервер
def send_results_to_backend(meeting_id: int, full_text: str, summary: str, title: str, meeting_elapsed_sec: int):
    try:
        meeting_id_int = int(meeting_id) if isinstance(meeting_id, str) else meeting_id
        
        payload = {
            "meeting_id": meeting_id_int,
            "full_text": full_text,
            "summary": summary,
            "title": title,
            "meeting_elapsed_sec": meeting_elapsed_sec
        }
        headers = {
            "X-Internal-Api-Key": "key",
            "Content-Type": "application/json"
        }
        backend_url = os.getenv('MAIN_BACKEND_URL', 'https://maryrose.by')
        url = f"{backend_url}/meetings/internal/result"
        
        logger.info(f"[{meeting_id}] Отправляю результаты на backend...")
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        response.raise_for_status()
        logger.info(f"[{meeting_id}] ✅ Результаты успешно отправлены на backend")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка при отправке результатов на backend: {e}")
        logger.error(f"[{meeting_id}] ❌ Ошибка при отправке результатов: {e}")
    except ValueError as e:
        print(f"❌ Ошибка преобразования meeting_id в число: {e}")
        logger.error(f"[{meeting_id}] ❌ Ошибка meeting_id: {e}")
    except Exception as e:
        print(f"❌ Неожиданная ошибка при отправке результатов: {e}")
        logger.error(f"[{meeting_id}] ❌ Неожиданная ошибка: {e}")