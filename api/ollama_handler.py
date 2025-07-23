# api/ollama_handler.py
import requests
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_ASSISTANT_PROMPT, OLLAMA_SUMMARY_PROMPT

def get_mary_response(command: str) -> str:
    prompt = OLLAMA_ASSISTANT_PROMPT.format(command=command)
    data = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    try:
        response = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=data)
        response.raise_for_status()
        return response.json().get('response', 'Не удалось получить ответ.')
    except requests.RequestException as e:
        print(f"Ollama connection error: {e}")
        return "Извините, у меня проблемы с подключением."

def get_summary_response(dialogue_text: str) -> str:
    prompt = OLLAMA_SUMMARY_PROMPT.format(dialogue_text=dialogue_text)
    data = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    try:
        response = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=data)
        response.raise_for_status()
        return response.json().get('response', 'Не удалось создать резюме.')
    except requests.RequestException as e:
        print(f"Ollama connection error: {e}")
        return "Не удалось создать резюме из-за ошибки."
