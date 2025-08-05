# --- ЭТАП 1: СБОРЩИК (BUILDER) ---
# Используем полный devel-образ для установки зависимостей, требующих компиляции.
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 AS builder

# Установка системных зависимостей, включая -dev пакеты и venv для Python
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common build-essential wget curl ca-certificates \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y python3.11 python3.11-dev python3.11-distutils python3.11-venv \
    portaudio19-dev libasound2-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Создаем виртуальное окружение
RUN python3.11 -m venv /opt/venv

# Добавляем venv в PATH, чтобы все последующие команды pip/python использовали его
ENV PATH="/opt/venv/bin:$PATH"

# Установка Python-зависимостей внутри venv
WORKDIR /app

# Сначала ставим тяжелые пакеты (PyTorch)
RUN pip install --no-cache-dir \
    torch==2.7.1 \
    torchaudio==2.7.1 \
    torchvision==0.22.1 \
    --index-url https://download.pytorch.org/whl/cu118

# Затем ставим остальные зависимости из requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# --- ЭТАП 2: ФИНАЛЬНЫЙ ОБРАЗ (RUNTIME) ---
# Используем более легковесный runtime-образ
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

# Установка только RUNTIME системных зависимостей
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    wget curl git ca-certificates jq unzip dos2unix gnupg procps \
    xvfb \
    pulseaudio dbus-x11 x11-utils \
    fonts-liberation libnss3 libgdk-pixbuf-2.0-0 libgtk-3-0 libxss1 libgbm1 \
    libxrandr2 libpangocairo-1.0-0 libatk1.0-0 libcairo-gobject2 \
    libxcomposite1 libxcursor1 libxdamage1 libxfixes3 libxinerama1 \
    libappindicator3-1 libxshmfence1 libglu1-mesa \
    libsndfile1 libportaudio2 \
    ffmpeg \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y python3.11 python3.11-distutils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Установка Google Chrome и Chromedriver
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

# Устанавливаем Ollama CLI
RUN curl -L https://github.com/ollama/ollama/releases/download/v0.1.48/ollama-linux-amd64 -o /usr/local/bin/ollama && \
    chmod +x /usr/local/bin/ollama

# Рабочая директория
WORKDIR /app

# Копируем целиком виртуальное окружение из этапа сборщика
COPY --from=builder /opt/venv /opt/venv

# Указываем, что нужно использовать Python и pip из нашего venv
ENV PATH="/opt/venv/bin:$PATH"

# Копируем код приложения
COPY . /app/

# Настройка переменных окружения
ENV HOME=/app
ENV PYTHONPATH=/app

# Настройка entrypoint и профиля Chrome
RUN chmod +x /app/entrypoint.sh && dos2unix /app/entrypoint.sh
RUN mkdir -p /app/chrome_profile && chmod 755 /app/chrome_profile

# Запуск
EXPOSE 8001
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
