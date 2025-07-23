# AI-ассистент для Google Meet

Этот проект представляет собой бэкенд для AI-ассистента, который может подключаться к встречам в Google Meet, записывать аудио, выполнять диаризацию (разделение по спикерам), транскрибацию и формировать краткое резюме диалога.

## Основные технологии

- **Бэкенд:** FastAPI
- **Бот для встреч:** Selenium, ChromeDriver
- **Обработка аудио:** FFmpeg, PulseAudio
- **Распознавание речи (STT):** NVIDIA NeMo (FastConformer)
- **Диаризация:** NVIDIA NeMo (ClusteringDiarizer)
- **Синтез речи (TTS):** Silero Models
- **Верификация спикера:** NVIDIA NeMo (TitaNet)
- **Генерация текста (LLM):** Ollama (Llama3)
- **Оркестрация:** Docker, Docker Compose

## Установка и запуск (локально)

1.  **Установите Docker и Docker Compose.**
2.  **Установите NVIDIA Container Toolkit** (если у вас есть GPU от NVIDIA).
3.  Создайте файл `server.py` и замените `"ваш_секретный_ключ"` на ваш собственный ключ API.
4.  Соберите и запустите контейнеры:
    ```bash
    docker compose up --build
    ```
5.  При первом запуске скачайте модель для Ollama:
    ```bash
    docker exec -it <имя_контейнера_ollama> ollama pull llama3:8b-instruct-q4_K_M
    ```
6.  Сервер будет доступен по адресу `http://localhost:8000`.

## Развертывание на RunPod

Проект настроен для развертывания на RunPod Serverless. Для этого используется `Dockerfile` и `runpod_handler.py`.

