# --- ШАГ 1: БАЗОВЫЙ ОБРАЗ ---
# ИЗМЕНЕНО: Используем CUDA 11.8.0, так как torch==2.7.1 собран именно под него.
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

# --- ШАГ 2: УСТАНОВКА СИСТЕМНЫХ ЗАВИСИМОСТЕЙ ---
# Устанавливаем все системные пакеты и Python 3.11 в одном слое для эффективности
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Основные утилиты
    software-properties-common \
    build-essential \
    wget \
    curl \
    git \
    ca-certificates \
    # Утилиты для установки Chrome/ChromeDriver и работы с аудио
    jq \
    unzip \
    gnupg \
    ffmpeg \
    pulseaudio \
    xvfb \
    libsndfile1 \
    sox \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-dev python3.11-distutils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем pip для Python 3.11
RUN wget https://bootstrap.pypa.io/get-pip.py && python3.11 get-pip.py && rm get-pip.py


# --- ШАГ 3: УСТАНОВКА GOOGLE CHROME И CHROMEDRIVER ---
# Устанавливаем Chrome
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Устанавливаем ChromeDriver
RUN CHROME_VERSION=$(google-chrome --version | cut -d " " -f3 | cut -d "." -f1-3) && \
    DRIVER_URL=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" | jq -r ".versions[] | select(.version | startswith(\"$CHROME_VERSION\")) | .downloads.chromedriver[] | select(.platform==\"linux64\") | .url" | tail -n 1) && \
    wget -q --continue -P /tmp/ $DRIVER_URL && \
    unzip /tmp/chromedriver-linux64.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/chromedriver-linux64/chromedriver && \
    ln -sf /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    rm /tmp/chromedriver-linux64.zip


# --- ШАГ 4: УСТАНОВКА PYTORCH (ИМЕННО ВЕРСИЯ 2.7.1) ---
# ИЗМЕНЕНО: Устанавливаем torch==2.7.1 с привязкой к CUDA 11.8
RUN python3.11 -m pip install --no-cache-dir \
    torch==2.7.1 \
    torchaudio==2.7.1 \
    torchvision==0.22.1 \
    --index-url https://download.pytorch.org/whl/cu118


# --- ШАГ 5: УСТАНОВКА ОСТАЛЬНЫХ PYTHON-ЗАВИСИМОСТЕЙ ---
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
WORKDIR /workspace

# Копируем файл с остальными зависимостями
COPY requirements.txt .

# Устанавливаем всё из requirements.txt
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt


# --- ШАГИ 6-9: КОД, НАСТРОЙКА И ЗАПУСК (БЕЗ ИЗМЕНЕНИЙ) ---
COPY . /workspace/

ENV HOME=/workspace
ENV TORCH_HOME=/workspace/.cache/torch
ENV NEMO_CACHE_DIR=/workspace/.cache/nemo
ENV PYTHONPATH=/workspace

RUN python3 download_models.py

EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]