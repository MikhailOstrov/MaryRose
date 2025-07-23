# Шаг 1: Базовый образ
# Используем Python 3.11 как современный и стабильный вариант.
# RunPod сможет использовать эту версию для сборки.
ARG PYTHON_VERSION=3.11.9
FROM python:${PYTHON_VERSION}-slim

# Шаг 2: Установка переменных окружения
# DEBIAN_FRONTEND=noninteractive - для автоматической установки пакетов без диалогов.
# Указываем пути для кэша, чтобы они были внутри рабочей директории /app.
# ENV WORKER_START - специальная переменная RunPod, указывающая, какой файл запускать.
ENV DEBIAN_FRONTEND=noninteractive
ENV NEMO_CACHE_DIR=/app/.cache/nemo
ENV TORCH_HOME=/app/.cache/torch
ENV HOME=/app
ENV WORKER_START="/app/runpod_handler.py"

# Шаг 3: Установка системных зависимостей
# Устанавливаем всё необходимое для Selenium, FFmpeg и обработки аудио.
# - build-essential, git, python3-dev: для сборки некоторых python-пакетов.
# - wget, gnupg, unzip, jq: утилиты для установки Chrome и драйвера.
# - google-chrome-stable: сам браузер для Selenium.
# - ffmpeg: для обработки аудио.
# - pulseaudio, xvfb: для создания виртуального аудио/видео окружения для бота.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git python3-dev \
    wget gnupg unzip jq \
    ffmpeg pulseaudio xvfb \
  && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
  && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
  && apt-get update && apt-get install -y --no-install-recommends \
    google-chrome-stable \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Шаг 4: Установка ChromeDriver
# Этот блок автоматически находит и скачивает версию драйвера,
# совместимую с установленной версией Google Chrome.
RUN CHROME_VERSION=$(google-chrome --version | cut -d " " -f3 | cut -d "." -f1-3) && \
    DRIVER_URL=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" | jq -r ".versions[] | select(.version | startswith(\"$CHROME_VERSION\")) | .downloads.chromedriver[] | select(.platform==\"linux64\") | .url" | tail -n 1) && \
    wget -q --continue -P /tmp/ $DRIVER_URL && \
    unzip /tmp/chromedriver-linux64.zip -d /usr/local/bin && \
    rm /tmp/chromedriver-linux64.zip

# Шаг 5: Установка Python зависимостей
# Копируем requirements.txt и добавляем runpod SDK для работы на платформе.
WORKDIR /app
COPY requirements.txt .
RUN echo "runpod" >> requirements.txt
RUN pip install -r requirements.txt

# Шаг 6: Предзагрузка и кэширование ML-моделей ("запекание" в образ)
# Это самый долгий, но самый важный шаг для быстрого старта воркеров на RunPod.
# Копируем только те файлы, что нужны для скрипта загрузки, для оптимизации кэша Docker.
COPY config.py .
COPY api/ /app/api/
COPY download_models.py .
RUN python download_models.py

# Шаг 7: Копирование остального кода приложения
# Этот шаг выполняется быстро, так как все "тяжелое" уже в кэше.
COPY . .
