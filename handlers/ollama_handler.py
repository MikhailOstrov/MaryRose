import requests
import os

# Получаем URL Ollama из переменной окружения
# Для RunPod нужно указать правильный адрес вашего Ollama контейнера
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
print(f"🔗 Ollama URL: {OLLAMA_BASE_URL}")  # Для отладки
print(f"🌍 Все переменные окружения с OLLAMA: {[k for k in os.environ.keys() if 'OLLAMA' in k.upper()]}")  # Диагностика
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

def test_ollama_connection() -> bool:
    """
    Проверяет подключение к Ollama.
    """
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        response.raise_for_status()
        print(f"✅ Ollama доступна по адресу: {OLLAMA_BASE_URL}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка подключения к Ollama ({OLLAMA_BASE_URL}): {e}")
        return False

def _call_ollama(prompt: str) -> str:
    """
    Приватная функция для отправки запроса в Ollama и обработки ответа (синхронная).
    """
    data = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    try:
        # Вызов requests.post блокирующий
        response = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=data,  timeout=30.0)
        response.raise_for_status() # Вызовет исключение для статусов 4xx/5xx
        return response.json().get('response', '')
    except requests.exceptions.RequestException as e: # Общая ошибка для requests
        print(f"Ollama connection error: {e}")
        return ""

def get_mary_response(command: str) -> str:
    """
    Формирует промпт для ассистента и получает ответ от Ollama (синхронная).
    """
    prompt = OLLAMA_ASSISTANT_PROMPT.format(command=command)
    response_text = _call_ollama(prompt)
    if not response_text:
        return "Извините, у меня проблемы с подключением."
    return response_text.strip()

def get_summary_response(dialogue_text: str) -> str:
    """
    Формирует промпт для саммаризации и получает ответ от Ollama (синхронная).
    """
    prompt = OLLAMA_SUMMARY_PROMPT.format(dialogue_text=dialogue_text)
    response_text = _call_ollama(prompt)
    if not response_text:
        return "Не удалось создать резюме из-за ошибки."
    return response_text.strip()

# Проверяем подключение к Ollama при загрузке модуля
if __name__ != "__main__":
    print("🔄 Проверяю подключение к Ollama...")
    test_ollama_connection()