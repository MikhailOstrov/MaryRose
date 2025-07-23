# --- ШАГ 1: БАЗОВЫЙ ОБРАЗ (ВАШ ШАБЛОН, БЕЗ ИЗМЕНЕНИЙ) ---
# Используем официальный образ NVIDIA CUDA 12.8 с cuDNN на Ubuntu 22.04
FROM nvidia/cuda:12.8.0-cudnn-devel-ubuntu22.04

# Устанавливаем неинтерактивный режим для apt и базовые зависимости
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common build-essential wget curl git ca-certificates && \
    add-apt-repository ppa:deadsnakes/ppa && apt-get update && \
    apt-get install -y --no-install-recommends python3.11 python3.11-dev python3.11-distutils && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Устанавливаем pip для Python 3.11
RUN wget https://bootstrap.pypa.io/get-pip.py && python3.11 get-pip.py && rm get-pip.py


# --- ШАГ 2: СИСТЕМНЫЕ ЗАВИСИМОСТИ ДЛЯ ВАШЕГО ПРОЕКТА ---
# Устанавливаем все, что нужно для meet_bot (Xvfb, PulseAudio, Chrome) и обработки аудио (ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    pulseaudio \
    xvfb \
    libsndfile1 \
    sox \
    gnupg \
    unzip \
    jq \
    && apt-get clean && rm -rf /var/lib/apt/lists/*


# --- ШАГ 3: УСТАНОВКА GOOGLE CHROME И CHROMEDRIVER ---
# Взял эту логику из вашего старого Dockerfile, она рабочая и надежная
# Устанавливаем Chrome
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Устанавливаем ChromeDriver, автоматически подбирая нужную версию
RUN CHROME_VERSION=$(google-chrome --version | cut -d " " -f3 | cut -d "." -f1-3) && \
    DRIVER_URL=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" | jq -r ".versions[] | select(.version | startswith(\"$CHROME_VERSION\")) | .downloads.chromedriver[] | select(.platform==\"linux64\") | .url" | tail -n 1) && \
    wget -q --continue -P /tmp/ $DRIVER_URL && \
    unzip /tmp/chromedriver-linux64.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/chromedriver-linux64/chromedriver && \
    ln -sf /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    rm /tmp/chromedriver-linux64.zip


# --- ШАГ 4: УСТАНОВКА PYTORCH (ВАШ ШАБЛОН, БЕЗ ИЗМЕНЕНИЙ) ---
ENV TORCH_VERSION=2.7.0
ENV CUDA_VERSION=12.8
RUN python3.11 -m pip install --no-cache-dir torch==${TORCH_VERSION}+cu128 torchvision==0.18.0+cu128 torchaudio==2.7.0+cu128 \
    -f https://download.pytorch.org/whl/torch_stable.html && \
    python3.11 -m pip install --no-cache-dir transformers==4.34.0


# --- ШАГ 5: УСТАНОВКА PYTHON-ЗАВИСИМОСТЕЙ ПРОЕКТА ---
# Устанавливаем Python3.11 как основной
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Создаем рабочую директорию
WORKDIR /workspace

# Копируем наш новый, чистый requirements.txt
COPY requirements.txt .

# Устанавливаем все зависимости одной командой. Это чисто и предсказуемо.
RUN python3 -m pip install --no-cache-dir -r requirements.txt


# --- ШАГ 6: КОПИРОВАНИЕ КОДА ПРОЕКТА ---
# Копируем весь код проекта ПОСЛЕ установки зависимостей для лучшего кэширования
COPY . /workspace/


# --- ШАГ 7: НАСТРОЙКА КЭША ДЛЯ МОДЕЛЕЙ ---
# Это гарантирует, что модели будут скачиваться и кэшироваться внутри рабочей директории
ENV HOME=/workspace
ENV TORCH_HOME=/workspace/.cache/torch
ENV NEMO_CACHE_DIR=/workspace/.cache/nemo
ENV PYTHONPATH=/workspace


# --- ШАГ 8: ПРЕДЗАГРУЗКА МОДЕЛЕЙ ---
# "Запекаем" модели прямо в образ, чтобы не скачивать их при каждом запуске контейнера
# Это может занять много времени при первой сборке, но сильно ускорит последующие запуски.
RUN python3 download_models.py


# --- ШАГ 9: ЗАПУСК КОНТЕЙНЕРА ---
# Открываем порт, который использует ваш server.py
EXPOSE 8001

# Команда по умолчанию, чтобы контейнер просто работал (как вы и просили в шаблоне).
# Это идеально для разработки: вы запускаете контейнер и подключаетесь к нему через 'docker exec'.
# CMD ["sleep", "infinity"]

# АЛЬТЕРНАТИВНАЯ КОМАНДА ДЛЯ ПРОДАКШЕНА:
# Если вы хотите, чтобы сервер FastAPI запускался сразу при старте контейнера,
# закомментируйте CMD выше и раскомментируйте эту:
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]