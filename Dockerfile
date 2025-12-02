# --- ШАГ 1: БАЗОВЫЙ ОБРАЗ ---
FROM nvidia/cuda:12.3.2-cudnn9-devel-ubuntu22.04

# --- ШАГ 2: УСТАНОВКА СИСТЕМНЫХ ЗАВИСИМОСТЕЙ ---
# Добавлены только зависимости для Chrome/Audio, БЕЗ ИЗМЕНЕНИЯ установки Python.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Основные утилиты, необходимые для проекта
    software-properties-common build-essential wget curl git ca-certificates jq unzip dos2unix gosu \
    # ЗАВИСИМОСТИ CHROME/AUDIO ИЗ JOIN_MEET (полный список для надежности)
    gnupg procps xvfb pulseaudio dbus-x11 x11-utils pulseaudio-utils \
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

# --- ШАГ 3: УСТАНОВКА GOOGLE CHROME И CHROMEDRIVER (Фиксированная версия 140) ---
# Устанавливаем Chrome v140.0.7339.185 из официального репозитория для тестирования
RUN mkdir -p /opt/google/chrome && \
    wget -q --continue -P /tmp/ "https://storage.googleapis.com/chrome-for-testing-public/140.0.7339.185/linux64/chrome-linux64.zip" && \
    unzip -q /tmp/chrome-linux64.zip -d /tmp/ && \
    mv /tmp/chrome-linux64/* /opt/google/chrome/ && \
    ln -s /opt/google/chrome/chrome /usr/bin/google-chrome-stable && \
    rm /tmp/chrome-linux64.zip

# Устанавливаем соответствующий ChromeDriver v140.0.7339.185
RUN wget -q --continue -P /tmp/ "https://storage.googleapis.com/chrome-for-testing-public/140.0.7339.185/linux64/chromedriver-linux64.zip" && \
    unzip -q /tmp/chromedriver-linux64.zip -d /tmp/ && \
    mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf /tmp/chromedriver-linux64* && \
    \
    # Проверяем, что версии установлены корректно
    echo "Chrome version:" && google-chrome-stable --version && \
    echo "ChromeDriver version:" && chromedriver --version

# --- ШАГ 4: УСТАНОВКА PYTORCH (ОРИГИНАЛЬНАЯ ВЕРСИЯ, НЕ ТРОНУТА) ---
RUN python3.11 -m pip install --no-cache-dir \
    torch==2.3.1+cu121 torchaudio==2.3.1+cu121 torchvision==0.18.1+cu121 \
--index-url https://download.pytorch.org/whl/cu121

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# --- ШАГ 5: УСТАНОВКА ОСТАЛЬНЫХ PYTHON-ЗАВИСИМОСТЕЙ (НЕ ТРОНУТО) ---
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
WORKDIR /app


COPY requirements.txt .
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
    coreutils less nano
RUN apt-get update && apt-get install -y openssh-server && apt-get clean
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
RUN sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
RUN mkdir -p /var/run/sshd

# --- ШАГ 6: КОПИРОВАНИЕ КОДА И НАСТРОЙКА ПРАВ ---
# Копируем ВЕСЬ код приложения ОДИН РАЗ
COPY . /app/

# Создаем пользователя 'appuser'
RUN groupadd -r appuser && useradd --no-log-init -r -g appuser appuser

# Выполняем действия, требующие прав root
RUN dos2unix /app/entrypoint.sh && \
    chmod +x /app/entrypoint.sh && \
    mkdir -p /workspace && \
    # Рекурсивно меняем владельца всех файлов приложения и workspace на 'appuser'
    chown -R appuser:appuser /app /workspace

# --- ШАГ 7: ПЕРЕКЛЮЧЕНИЕ НА НЕПРИВИЛЕГИРОВАННОГО ПОЛЬЗОВАТЕЛЯ ---
# ЭТА КОМАНДА ДОЛЖНА БЫТЬ!
USER appuser

# Настройка переменных окружения, которые понадобятся appuser
ENV HOME=/home/appuser 
ENV XDG_RUNTIME_DIR=/tmp/runtime-appuser

# Настройка переменных окружения (можно делать и до USER, но так логичнее)
ENV HOME=/app
ENV TORCH_HOME=/workspace/.cache/torch
ENV NEMO_CACHE_DIR=/workspace/.cache/nemo
ENV HF_HOME=/workspace/.cache/huggingface
ENV LOGS_DIR=/workspace/logs
ENV PYTHONPATH=/app

# --- ШАГ 8: ЗАПУСК ---
EXPOSE 8000 8001
ENTRYPOINT ["/app/entrypoint.sh"]
# Основной сервер запускаем на 8000, так как 8001 занят инференсом
CMD ["uvicorn", "server.server:app", "--host", "0.0.0.0", "--port", "8000"]
