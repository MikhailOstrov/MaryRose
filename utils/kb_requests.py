import httpx
import logging
from dotenv import load_dotenv
from config.config import INTERNAL_API_KEY, BACKEND_URL

load_dotenv()
logger = logging.getLogger(__name__)

async def save_info_in_kb(text: str, email: str):

    url = f"{BACKEND_URL}/knowledge/add-text"
    headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=headers, json={"text": text, "email": email}, timeout=30.0
        )
        response.raise_for_status()
        result = response.json()

    logger.info(f"Текст '{text}' успешно добавлен в БЗ.")

async def get_info_from_kb(query: str, email: str):

    url = f"{BACKEND_URL}/knowledge/search"
    headers = {"X-Internal-Api-Key": INTERNAL_API_KEY}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=headers, json={"query": query, "email": email}, timeout=30.0
        )
        response.raise_for_status()
        result = response.json()
    logger.info(f"Ответ от БЗ: {result}")

    if not result.get("success") or "results" not in result:
        return None

    results = result["results"]

    if not results:
        return None

    # Формируем красивый список
    message = "Результаты поиска:\n\n"
    for idx, r in enumerate(results, start=1):
        message += (
            f" {idx}. {r['title']}\n"
            f"   {r['content_preview']}\n\n"
        )
    return message.strip()
