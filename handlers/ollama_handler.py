import httpx

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3:8b-instruct-q4_K_M"

OLLAMA_ASSISTANT_PROMPT = """
Ты — ассистент по имени Мэри. Тебе дали следующую команду: "{command}".
Выполни эту команду. Отвечай кратко и по делу.
"""
OLLAMA_SUMMARY_PROMPT = """
Ты — высококлассный ассистент для анализа совещаний. Твоя задача — проанализировать следующий диалог и предоставить краткое резюме.
Результат должен быть структурирован строго по следующему формату:

### Ключевые моменты
- [Основная мысль или решение 1]
- [Основная мысль или решение 2]
- [и так далее...]

### Задачи для участников
- **[Имя участника]:** [Описание задачи, которую ему поручили]
- **[Имя участника]:** [Описание другой задачи]

Вот диалог для анализа:
---
{dialogue_text}
---
"""

client = httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=45.0)

async def _call_ollama(prompt: str) -> str:
    """
    Приватная функция для отправки запроса в Ollama и обработки ответа.
    """
    data = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    try:
        response = await client.post("/api/generate", json=data)
        response.raise_for_status()
        return response.json().get('response', '')
    except httpx.RequestError as e:
        print(f"Ollama connection error: {e}")
        return ""

async def get_mary_response(command: str) -> str:
    """
    Формирует промпт для ассистента и получает ответ от Ollama.
    """
    prompt = OLLAMA_ASSISTANT_PROMPT.format(command=command)
    response_text = await _call_ollama(prompt)
    if not response_text:
        return "Извините, у меня проблемы с подключением."
    return response_text.strip()

async def get_summary_response(dialogue_text: str) -> str:
    """
    Формирует промпт для саммаризации и получает ответ от Ollama.
    """
    prompt = OLLAMA_SUMMARY_PROMPT.format(dialogue_text=dialogue_text)
    response_text = await _call_ollama(prompt)
    if not response_text:
        return "Не удалось создать резюме из-за ошибки."
    return response_text.strip()