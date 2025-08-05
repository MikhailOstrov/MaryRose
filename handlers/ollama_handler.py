import requests
import os
import time

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = "llama3:8b-instruct-q4_K_M"

def ensure_model_available():
    """Проверяет наличие модели и загружает её если нужно"""
    try:
        # Проверяем доступные модели
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        response.raise_for_status()
        models = response.json().get('models', [])
        
        # Проверяем, есть ли нужная модель
        model_exists = any(OLLAMA_MODEL in model.get('name', '') for model in models)
        
        if model_exists:
            print(f"✅ Модель {OLLAMA_MODEL} готова к использованию")
            return True
            
        print(f"📥 Загружаю модель {OLLAMA_MODEL}...")
        
        # Загружаем модель
        pull_response = requests.post(
            f"{OLLAMA_BASE_URL}/api/pull",
            json={"name": OLLAMA_MODEL},
            timeout=600  # 10 минут на загрузку
        )
        pull_response.raise_for_status()
        print(f"✅ Модель {OLLAMA_MODEL} успешно загружена!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при работе с моделью: {e}")
        return False

# Автоматически проверяем и загружаем модель при импорте модуля
print("🚀 Инициализация Ollama...")
ensure_model_available()



OLLAMA_ASSISTANT_PROMPT = """
Ты — умный русскоязычный помощник по имени Мэри. Отвечай только на русском языке, кратко и по существу.

Команда пользователя: "{command}"

Дай четкий и полезный ответ:
"""

OLLAMA_SUMMARY_PROMPT = """
Ты — русскоязычный ИИ-ассистент, профессиональный аналитик встреч. Твоя задача — проанализировать предоставленный диалог и создать краткое резюме на русском языке.

ВАЖНЫЕ ПРАВИЛА:
1.  ЯЗЫК ОТВЕТА: Всегда и без исключений отвечай ТОЛЬКО на РУССКОМ языке.
2.  СТРОГИЙ ФОРМАТ: Неукоснительно следуй предложенной структуре с использованием Markdown-заголовков (###) и списков (-). Не добавляй ничего лишнего.
3.  ТОЧНОСТЬ: Основывай резюме только на информации из предоставленного диалога.

### Пример желаемого результата:

Резюме:
### Ключевые моменты
- Обсуждение запуска новой функции.
- Принято решение о проведении A/B теста на следующей неделе.

### Поручения и задачи
- Иван: Подготовить сегменты пользователей для A/B теста к среде.
- Анна: Подготовить дизайн и тексты для новой фичи к среде.

### Решения
- Запустить A/B тестирование новой функции на следующей неделе.

---
Теперь проанализируй следующий диалог и создай резюме по тому же формату.

Диалог для анализа:
---
{dialogue_text}
---

Резюме:
"""

def _call_ollama(prompt: str) -> str:
    """Отправляет запрос в Ollama и получает ответ"""
    data = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    
    try:
        response = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=data, timeout=30.0)
        response.raise_for_status()
        return response.json().get('response', '')
    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка Ollama: {e}")
        return ""

def get_mary_response(command: str) -> str:
    """Получает ответ от Мэри на команду пользователя"""
    prompt = OLLAMA_ASSISTANT_PROMPT.format(command=command)
    response_text = _call_ollama(prompt)
    if not response_text:
        return "Извините, у меня проблемы с подключением."
    return response_text.strip()

def get_summary_response(dialogue_text: str) -> str:
    """Создает резюме диалога с помощью Ollama"""
    prompt = OLLAMA_SUMMARY_PROMPT.format(dialogue_text=dialogue_text)
    response_text = _call_ollama(prompt)
    if not response_text:
        return "Не удалось создать резюме из-за ошибки."
    return response_text.strip()