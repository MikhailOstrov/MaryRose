# Шаг 1: Используем базовый образ Ubuntu 22.04 с CUDA 12.1
FROM nvidia/cuda:12.1.1-devel-ubuntu22.04

# Шаг 2: Устанавливаем системные зависимости
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    curl \
    git \
    ca-certificates \
    gnupg \
    unzip \
    jq \
    ffmpeg \
    pulseaudio \
    xvfb \
    libsndfile1 \
    sox \
    software-properties-common \
    zlib1g-dev \
    libncurses5-dev \
    libgdbm-dev \
    libnss3-dev \
    libssl-dev \
    libreadline-dev \
    libffi-dev \
    libsqlite3-dev \
    libbz2-dev \
    liblzma-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Шаг 3: Компилируем и устанавливаем Python 3.11.13 из исходников
WORKDIR /tmp
RUN wget https://www.python.org/ftp/python/3.11.13/Python-3.11.13.tgz \
    && tar -xzf Python-3.11.13.tgz \
    && cd Python-3.11.13 \
    && ./configure --enable-optimizations --enable-shared --with-lto \
    && make -j$(nproc) \
    && make altinstall \
    && ldconfig \
    && cd / && rm -rf /tmp/Python-3.11.13*

# Создаем символические ссылки для python3 и pip3
RUN ln -sf /usr/local/bin/python3.11 /usr/local/bin/python3 \
    && ln -sf /usr/local/bin/python3.11 /usr/local/bin/python \
    && ln -sf /usr/local/bin/pip3.11 /usr/local/bin/pip3 \
    && ln -sf /usr/local/bin/pip3.11 /usr/local/bin/pip

# Обновляем PATH
ENV PATH="/usr/local/bin:$PATH"

# Шаг 4: Устанавливаем Google Chrome
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Шаг 5: Устанавливаем ChromeDriver
RUN CHROME_VERSION=$(google-chrome --version | cut -d " " -f3 | cut -d "." -f1-3) && \
    DRIVER_URL=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" | jq -r ".versions[] | select(.version | startswith(\"$CHROME_VERSION\")) | .downloads.chromedriver[] | select(.platform==\"linux64\") | .url" | tail -n 1) && \
    wget -q --continue -P /tmp/ $DRIVER_URL && \
    unzip /tmp/chromedriver-linux64.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/chromedriver-linux64/chromedriver && \
    ln -sf /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    rm /tmp/chromedriver-linux64.zip

# Шаг 6: Переходим в рабочую директорию и обновляем pip
WORKDIR /app
RUN python3 -m pip install --upgrade pip setuptools wheel

# Шаг 7: Копируем requirements.txt для установки зависимостей
COPY requirements.txt .

# Шаг 8: Устанавливаем зависимости поэтапно для избежания конфликтов
# Сначала устанавливаем критически важные зависимости
RUN pip3 install --no-cache-dir \
    numpy==1.26.4 \
    Cython \
    packaging

# Устанавливаем PyTorch отдельно (так как у вас специфическая версия torchvision)
RUN pip3 install --no-cache-dir \
    torch==2.7.1 \
    torchaudio==2.7.1

# Устанавливаем torchvision отдельно (версия с CUDA 12.4)
RUN pip3 install --no-cache-dir torchvision==0.21.0+cu124 --index-url https://download.pytorch.org/whl/cu124

# Устанавливаем остальные зависимости из requirements.txt
# Исключаем уже установленные пакеты, чтобы избежать конфликтов
RUN grep -v "^torch" requirements.txt | \
    grep -v "^numpy" | \
    grep -v "^Cython" | \
    grep -v "^packaging" | \
    grep -v "^#" | \
    grep -v "^$" > requirements_filtered.txt && \
    pip3 install --no-cache-dir -r requirements_filtered.txt && \
    rm requirements_filtered.txt

# Шаг 9: Копируем остальной код приложения
COPY . .

# Шаг 10: Настройка переменных окружения для кэша моделей
ENV HOME=/app
ENV TORCH_HOME=/app/.cache/torch
ENV NEMO_CACHE_DIR=/app/.cache/nemo
ENV PYTHONPATH=/app

# Шаг 11: Предзагружаем модели
RUN python3 download_models.py

# Шаг 12: Настройка сети и запуск
EXPOSE 8001
CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]