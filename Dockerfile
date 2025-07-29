# --- ШАГ 1: БАЗОВЫЙ ОБРАЗ ---
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

# --- ШАГ 2: УСТАНОВКА СИСТЕМНЫХ ЗАВИСИМОСТЕЙ ---
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Основные утилиты
    software-properties-common \
    build-essential \
    wget \
    curl \
    git \
    ca-certificates \
    jq \
    unzip \
    dos2unix \
    # Зависимости для Chrome, Xvfb и PulseAudio (взято из join_meet)
    xvfb \
    pulseaudio \
    dbus-x11 \
    fonts-liberation \
    libnss3 \
    libgdk-pixbuf-2.0-0 \
    libgtk-3-0 \
    libxss1 \
    libgbm1 \
    libasound2 \
    # Аудио-библиотеки для Python
    libsndfile1 \
    portaudio19-dev \
    libasound2-dev \
    # Python 3.11
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-dev python3.11-distutils \
    # Очистка
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем pip для Python 3.11
RUN wget https://bootstrap.pypa.io/get-pip.py && python3.11 get-pip.py && rm get-pip.py


# --- ШАГ 3: УСТАНОВКА GOOGLE CHROME И CHROMEDRIVER (надежный метод из join_meet) ---
# Устанавливаем Chrome
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем ChromeDriver, совместимый с установленной версией Chrome
RUN CHROME_VERSION=$(google-chrome --version | cut -d " " -f 3 | cut -d "." -f 1-3) && \
    DRIVER_URL=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" | jq -r ".versions[] | select(.version | startswith(\"$CHROME_VERSION\")) | .downloads.chromedriver[] | select(.platform==\"linux64\") | .url" | tail -n 1) && \
    wget -q --continue -P /tmp/ $DRIVER_URL && \
    unzip /tmp/chromedriver-linux64.zip -d /usr/local/bin && \
    # Важно: даем права на исполнение самому драйверу
    chmod +x /usr/local/bin/chromedriver-linux64/chromedriver && \
    # Создаем символическую ссылку для удобства доступа
    ln -sf /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    rm /tmp/chromedriver-linux64.zip


# --- ШАГ 4: УСТАНОВКА PYTORCH ---
RUN python3.11 -m pip install --no-cache-dir \
    torch==2.7.1 \
    torchaudio==2.7.1 \
    torchvision==0.22.1 \
    --index-url https://download.pytorch.org/whl/cu118


# --- ШАГ 5: УСТАНОВКА ОСТАЛЬНЫХ PYTHON-ЗАВИСИМОСТЕЙ ---
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
WORKDIR /app

COPY requirements.txt .
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt


# --- ШАГ 6: КОПИРОВАНИЕ КОДА И НАСТРОЙКА ENTRYPOINT ---
COPY . /app/

# Даем права на исполнение entrypoint'у и конвертируем формат строк на всякий случай
RUN chmod +x /app/entrypoint.sh && dos2unix /app/entrypoint.sh

# --- ШАГ 7: ЗАГРУЗКА МОДЕЛЕЙ И НАСТРОЙКА ПЕРЕМЕННЫХ ---
ENV HOME=/app
ENV TORCH_HOME=/app/.cache/torch
ENV NEMO_CACHE_DIR=/app/.cache/nemo
ENV PYTHONPATH=/app

# Этот шаг может занять много времени, он загружает гигабайты моделей
RUN python3 download_models.py


# --- ШАГ 8: ЗАПУСК ---
EXPOSE 8001
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]