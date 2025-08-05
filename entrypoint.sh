#!/bin/bash
set -e

echo "=== [Entrypoint] v2.0: Настройка окружения и моделей ==="

# --- 1. Настройка персистентного хранилища для моделей ---
PERSISTENT_DIR="/workspace"
OLLAMA_MODELS_DIR="$PERSISTENT_DIR/ollama_models"
TORCH_CACHE_DIR="$PERSISTENT_DIR/.cache/torch"
NEMO_CACHE_DIR="$PERSISTENT_DIR/.cache/nemo"
OTHER_MODELS_DONE_FILE="$PERSISTENT_DIR/.models_loaded"

echo "[Entrypoint] Персистентная папка: $PERSISTENT_DIR"
echo "[Entrypoint] Папка для Ollama моделей: $OLLAMA_MODELS_DIR"
echo "[Entrypoint] Папка для Torch кэша: $TORCH_CACHE_DIR"
echo "[Entrypoint] Папка для Nemo кэша: $NEMO_CACHE_DIR"

# Создаем директории, если их нет
mkdir -p "$OLLAMA_MODELS_DIR"
mkdir -p "$TORCH_CACHE_DIR"
mkdir -p "$NEMO_CACHE_DIR"

# Устанавливаем переменные окружения, чтобы приложение знало, где искать модели
export OLLAMA_MODELS="$OLLAMA_MODELS_DIR"
export TORCH_HOME="$TORCH_CACHE_DIR"
export NEMO_CACHE_DIR="$NEMO_CACHE_DIR"
export HOME=/app # HOME все еще указывает на /app

# --- 2. Загрузка моделей (только если они еще не загружены) ---

# 2.1. Загрузка модели Ollama
# Проверяем, есть ли уже модель. Мы ищем конкретный файл манифеста.
# Это надежнее, чем просто проверять папку.
OLLAMA_MODEL_NAME="llama3:8b-instruct-q4_K_M"
OLLAMA_MODEL_MANIFEST="$OLLAMA_MODELS_DIR/manifests/registry.ollama.ai/library/llama3/8b-instruct-q4_K_M"

if [ ! -f "$OLLAMA_MODEL_MANIFEST" ]; then
    echo "[Entrypoint] Модель Ollama '$OLLAMA_MODEL_NAME' не найдена. Начинаю загрузку..."
    # Временно запускаем сервер Ollama в фоне ТОЛЬКО для скачивания модели
    /usr/local/bin/ollama serve &
    OLLAMA_PID=$!
    
    # Ждем, пока сервер будет готов
    timeout 60 bash -c 'until curl -sf -o /dev/null http://localhost:11434; do echo "Ожидание временного сервера Ollama..."; sleep 2; done'
    
    if [ $? -ne 0 ]; then
      echo "❌ [Entrypoint] CRITICAL: Временный сервер Ollama не запустился. Не могу скачать модель."
      exit 1
    fi

    # Качаем модель
    echo "[Entrypoint] Скачиваю модель $OLLAMA_MODEL_NAME..."
    /usr/local/bin/ollama pull "$OLLAMA_MODEL_NAME"
    
    # Останавливаем временный сервер
    echo "[Entrypoint] Загрузка модели Ollama завершена. Останавливаю временный сервер."
    kill $OLLAMA_PID
    wait $OLLAMA_PID
else
    echo "✅ [Entrypoint] Модель Ollama '$OLLAMA_MODEL_NAME' уже на месте."
fi

# 2.2. Загрузка моделей PyTorch/Nemo
if [ ! -f "$OTHER_MODELS_DONE_FILE" ]; then
    echo "[Entrypoint] Модели PyTorch/Nemo еще не загружены. Запускаю скрипт загрузки..."
    # Запускаем ваш скрипт. Он будет использовать переменные TORCH_HOME и NEMO_CACHE_DIR.
    python3 config/load_models.py
    
    # Создаем "файл-флаг", чтобы не делать это снова
    echo "✅ [Entrypoint] Загрузка моделей PyTorch/Nemo завершена."
    touch "$OTHER_MODELS_DONE_FILE"
else
    echo "✅ [Entrypoint] Модели PyTorch/Nemo уже на месте."
fi

# --- 3. Настройка Display и Audio (логика из старого entrypoint) ---
export DISPLAY=:99
rm -rf /app/chrome_profile/Default/Service* 2>/dev/null || true
rm -rf /app/chrome_profile/Default/Session* 2>/dev/null || true

echo "[Entrypoint] Запуск Xvfb..."
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x720x16 &
sleep 3
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: Xvfb не запустился. Прерывание."
    exit 1
fi
echo "✅ [Entrypoint] Xvfb готов!"

echo "[Entrypoint] Запуск PulseAudio..."
export PULSE_RUNTIME_PATH=/tmp/pulse-runtime
mkdir -p $PULSE_RUNTIME_PATH
pulseaudio --start --exit-idle-time=-1 --daemonize
sleep 3
if pactl info >/dev/null 2>&1; then
    echo "✅ [Entrypoint] PulseAudio работает. Создание виртуальных аудио-устройств..."
    pactl load-module module-null-sink sink_name=meet_sink sink_properties=device.description="Virtual_Sink_for_Meet"
    pactl set-default-sink meet_sink
    pactl load-module module-virtual-source source_name=meet_mic master=meet_sink.monitor
    pactl set-default-source meet_mic
else
    echo "⚠️ [Entrypoint] PulseAudio не отвечает. Захват звука не будет работать."
fi

# --- 4. Запуск служб и основного приложения ---
echo "=== [Entrypoint] Запуск служб для приложения ==="

# Запускаем основной сервер Ollama в фоновом режиме
echo "[Entrypoint] Запуск основного сервера Ollama..."
/usr/local/bin/ollama serve &

echo "[Entrypoint] Ожидание готовности основного сервера Ollama..."
timeout 60 bash -c 'until curl -sf -o /dev/null http://localhost:11434; do echo "Сервер Ollama еще не готов, ждем..."; sleep 2; done'
if [ $? -ne 0 ]; then
    echo "❌ [Entrypoint] CRITICAL: Основной сервер Ollama не запустился за 60 секунд. Прерывание."
    exit 1
fi
echo "✅ [Entrypoint] Основной сервер Ollama готов и отвечает."

echo "=== [Entrypoint] Запуск основного приложения ==="
echo "[Entrypoint] Передача управления команде: $@"
exec "$@"
