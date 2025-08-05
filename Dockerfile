# --- ШАГ 1: БАЗОВЫЙ ОБРАЗ ---
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

# --- ШАГ 2: УСТАНОВКА СИСТЕМНЫХ ЗАВИСИМОСТЕЙ ---
# Добавлены только зависимости для Chrome/Audio, БЕЗ ИЗМЕНЕНИЯ установки Python.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Основные утилиты, необходимые для проекта
    software-properties-common build-essential wget curl git ca-certificates jq unzip dos2unix \
    # ЗАВИСИМОСТИ CHROME/AUDIO ИЗ JOIN_MEET (полный список для надежности)
    gnupg procps xvfb pulseaudio dbus-x11 x11-utils \
    fonts-liberation libnss3 libgdk-pixbuf-2.0-0 libgtk-3-0 libxss1 libgbm1 \
    libxrandr2 libpangocairo-1.0-0 libatk1.0-0 libcairo-gobject2 \
    libxcomposite1 libxcursor1 libxdamage1 libxfixes3 libxinerama1 \
    libappindicator3-1 libxshmfence1 libglu1-mesa \
    # Аудио-библиотеки (включая DEV пакеты из join_meet)
    libsndfile1 libportaudio2 portaudio19-dev libasound2-dev \
    # ОРИГИНАЛЬНАЯ УСТАНОВКА PYTHON 3.11 (НЕ ТРОНУТА)
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-dev python3.11-distutils \
    # Очистка
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ОРИГИНАЛЬНАЯ УСТАНОВКА PIP (НЕ ТРОНУТА)
RUN wget https://bootstrap.pypa.io/get-pip.py && python3.11 get-pip.py && rm get-pip.py

# --- ШАГ 3: УСТАНОВКА GOOGLE CHROME И CHROMEDRIVER (надежный метод из join_meet) ---
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN CHROME_VERSION=$(google-chrome --version | cut -d " " -f 3 | cut -d "." -f 1-3) && \
    DRIVER_URL=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" | jq -r ".versions[] | select(.version | startswith(\"$CHROME_VERSION\")) | .downloads.chromedriver[] | select(.platform==\"linux64\") | .url" | tail -n 1) && \
    wget -q --continue -P /tmp/ $DRIVER_URL && \
    unzip /tmp/chromedriver-linux64.zip -d /tmp/ && \
    mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf /tmp/chromedriver-linux64* && \
    chromedriver --version

# --- ШАГ 4: УСТАНОВКА PYTORCH (ОРИГИНАЛЬНАЯ ВЕРСИЯ, НЕ ТРОНУТА) ---
RUN python3.11 -m pip install --no-cache-dir \
    torch==2.7.1 \
    torchaudio==2.7.1 \
    torchvision==0.22.1 \
    --index-url https://download.pytorch.org/whl/cu118

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# --- ШАГ 5: УСТАНОВКА ОСТАЛЬНЫХ PYTHON-ЗАВИСИМОСТЕЙ (НЕ ТРОНУТО) ---
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
WORKDIR /app

COPY requirements.txt .
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

# --- ШАГ 6: КОПИРОВАНИЕ КОНФИГУРАЦИИ И ЗАГРУЗКА МОДЕЛЕЙ ---
# Сначала копируем только файлы, необходимые для загрузки моделей
COPY config/ /app/config/

# Настройка переменных окружения для моделей
ENV HOME=/app
ENV TORCH_HOME=/app/.cache/torch
ENV NEMO_CACHE_DIR=/app/.cache/nemo
ENV PYTHONPATH=/app

# Загружаем модели (этот слой будет кешироваться)
RUN python3 config/load_models.py

# --- ШАГ 7: КОПИРОВАНИЕ ОСТАЛЬНОГО КОДА И НАСТРОЙКА ENTRYPOINT ---
# Только потом копируем весь остальной код
COPY . /app/
RUN chmod +x /app/entrypoint.sh && dos2unix /app/entrypoint.sh
# ВАЖНО: Создаем папку профиля, как в join_meet
RUN mkdir -p /app/chrome_profile && chmod 755 /app/chrome_profile

# --- ШАГ 8: ЗАПУСК (НЕ ТРОНУТО) ---
EXPOSE 8001
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]