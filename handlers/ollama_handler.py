from config.load_models import llm_model
from config.config import OLLAMA_SUMMARY_PROMPT, OLLAMA_TITLE_PROMPT, OLLAMA_ASSISTANT_PROMPT

# Функиция для суммаризации
def get_summary_response(cleaned_dialogue: str) -> str:

    messages = [
    {"role": "system", "content": OLLAMA_SUMMARY_PROMPT}, # Промпт для саммари
    {"role": "user", "content": cleaned_dialogue}, # Очищенный диалог от меток спикеров
    ]

    outputs = llm_model(
        messages,
        max_new_tokens=2056, # Можно менять для размера ответа
    )
    return outputs[0]["generated_text"][-1]["content"]

# Функиция для названия встречи
def get_title_response(cleaned_dialogue: str) -> str:

    messages = [
    {"role": "system", "content": OLLAMA_TITLE_PROMPT}, # Промпт для названия встречи
    {"role": "user", "content": cleaned_dialogue}, # Очищенный диалог от меток спикеров
    ]

    outputs = llm_model(
        messages,
        max_new_tokens=32, # Можно менять для размера ответа
    )
    return outputs[0]["generated_text"][-1]["content"]

# Функция ответа от Мэри
def get_mary_response(command: str) -> str:

    messages = [
    {"role": "system", "content": OLLAMA_ASSISTANT_PROMPT}, # Промпт для ответа Мэри
    {"role": "user", "content": command}, # Запрос для Мэри
    ]

    outputs = llm_model(
        messages,
        max_new_tokens=256, # Можно менять для размера ответа
    )
    return outputs[0]["generated_text"][-1]["content"]