# Шаг 1: Правильный базовый образ с GPU-поддержкой
# Используем официальный образ RunPod с Python 3.11, CUDA и PyTorch
FROM runpod/pytorch:2.3.0-py3.11-cuda12.1.1-devel-2024-05-13

# Шаг 2: Переменные окружения
ENV DEBIAN_FRONTEND=noninteractive
ENV NEMO_CACHE_DIR=/app/.cache/nemo
ENV TORCH_HOME=/app/.cache/torch
ENV HOME=/app

# Шаг 3: Установка системных зависимостей
# Добавляем libsndfile1 - критически важно для работы с аудио!
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git \
    wget gnupg unzip jq \
    ffmpeg pulseaudio xvfb \
    libsndfile1 \
  && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
  && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
  && apt-get update && apt-get install -y --no-install-recommends \
    google-chrome-stable \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Шаг 4: Установка ChromeDriver (ваш код здесь отличный)
RUN CHROME_VERSION=$(google-chrome --version | cut -d " " -f3 | cut -d "." -f1-3) && \
    DRIVER_URL=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" | jq -r ".versions[] | select(.version | startswith(\"$CHROME_VERSION\")) | .downloads.chromedriver[] | select(.platform==\"linux64\") | .url" | tail -n 1) && \
    wget -q --continue -P /tmp/ $DRIVER_URL && \
    unzip /tmp/chromedriver-linux64.zip -d /usr/local/bin && \
    rm /tmp/chromedriver-linux64.zip

# Шаг 5: Установка Python зависимостей
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Шаг 6: Предзагрузка и кэширование ML-моделей ("запекание")
COPY config.py .
COPY api/ /app/api/
COPY download_models.py .
RUN python download_models.py

# Шаг 7: Копирование остального кода приложения
COPY . .

# Шаг 8: Команда для запуска вашего сервера при старте Пода
# Мы запускаем server.py, так как он содержит API для управления ботом
CMD ["python", "server.py"]