# Шаг 1: Используем официальный образ RunPod, он уже содержит все драйверы
FROM runpod/pytorch:2.3.0-py3.11-cuda12.1.1-devel-2024-05-13

# Шаг 2: Установка системных зависимостей (ваш код здесь отличный)
# Устанавливаем переменные окружения и необходимые библиотеки, включая Chrome
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git wget gnupg unzip jq ffmpeg pulseaudio xvfb libsndfile1 \
  && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
  && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
  && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Шаг 3: Установка ChromeDriver (ваш код здесь отличный)
RUN CHROME_VERSION=$(google-chrome --version | cut -d " " -f3 | cut -d "." -f1-3) && \
    DRIVER_URL=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" | jq -r ".versions[] | select(.version | startswith(\"$CHROME_VERSION\")) | .downloads.chromedriver[] | select(.platform==\"linux64\") | .url" | tail -n 1) && \
    wget -q --continue -P /tmp/ $DRIVER_URL && \
    unzip /tmp/chromedriver-linux64.zip -d /usr/local/bin && \
    rm /tmp/chromedriver-linux64.zip

# Шаг 4: Установка Python зависимостей с оптимизацией кэширования
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Шаг 5: Копирование кода и предзагрузка моделей
# Копируем все остальное
COPY . .
# Запускаем скрипт скачивания моделей. Они "запекаются" в образ.
RUN python download_models.py

# Шаг 6: Настройка сети и запуск
# Сообщаем Docker, что контейнер будет слушать порт 8001
EXPOSE 8001

# Команда для запуска Uvicorn сервера при старте контейнера
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]