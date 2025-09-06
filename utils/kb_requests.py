import httpx
import logging
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.INFO)

async def save_info_in_kb(text: str, email: str):

    url = "https://maryrose.by/knowledge/add-text"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, json={"text": text, "chat_id": email}, timeout=30.0
        )
        response.raise_for_status()
        result = response.json()

    logging.info(f"Текст '{text}' успешно отправлен в БЗ")

async def get_info_from_kb(query: str, text: str, email: str):

    url = "https://maryrose.by/knowledge/search"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, json={"query": query, "chat_id": email}, timeout=30.0
        )
        response.raise_for_status()
        result = response.json()
    logging.info(f"Ответ от БЗ: {result}")

    if not result.get("success") or "results" not in result:
        return None

    results = result["results"]

    if not results:
        return None

    # Формируем красивый список
    message = "Результаты поиска:\n\n"
    for idx, r in enumerate(results, start=1):
        message += (
            f"📌 --- {idx}. {r['title']} ---\n"
            f"   {r['content_preview']}\n\n"
        )
    return message.strip()