#!/bin/bash
set -e

echo "=== [Entrypoint] Настройка окружения для RunPod (Пользовательский режим + TCP) ==="

# --- 0. Проверка и настройка /workspace ---
echo "[Entrypoint] Проверка Volume диска /workspace..."
if [ ! -d "/workspace" ]; then
    echo "❌ [Entrypoint] CRITICAL: /workspace не найден. Убедитесь, что Volume диск подключен."
    exit 1
fi
echo "[Entrypoint] Создание структуры папок в /workspace..."
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models
echo "✅ [Entrypoint] Структура /workspace создана."


# --- 1. Настройка Display и служб ---
export DISPLAY=:99
echo "[Entrypoint] Запуск виртуального дисплея Xvfb..."
rm -f /tmp/.X99-lock # Удаляем lock-файл на случай некорректного завершения
Xvfb :99 -screen 0 1280x720x16 &
sleep 3
echo "✅ [Entrypoint] Xvfb готов!"

echo "[Entrypoint] Запуск PulseAudio в пользовательском режиме с TCP-сокетом..."

# Запускаем сервер в фоновом режиме
pulseaudio --start --exit-idle-time=-1 --daemonize
sleep 1 # Даем ему секунду на базовую инициализацию

# Загружаем модуль для публикации сервера по TCP на localhost.
# Это самый надежный способ сделать его доступным для всех процессов внутри контейнера.
pactl load-module module-native-protocol-tcp auth-ip-acl=127.0.0.1
sleep 1

# Устанавливаем переменную окружения, которая скажет всем клиентам (Chrome, sounddevice)
# подключаться к нашему TCP-серверу, а не искать файловый сокет.
export PULSE_SERVER=127.0.0.1

echo "[Entrypoint] Проверка PulseAudio через TCP..."
if ! pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: PulseAudio не запустился или недоступен по TCP."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio сервер готов и доступен по TCP."


# --- 2. Предзагрузка моделей (без изменений) ---
if [ "${PREWARM:-off}" = "download_only" ]; then
  echo "=== [Entrypoint] Предзагрузка весов моделей... ==="
  python3 - <<'PY'
import os
import sys
from huggingface_hub import snapshot_download

HF_TOKEN = os.getenv("HUGGING_FACE_HUB_TOKEN") or os.getenv("HF_TOKEN")
targets = ["deepdml/faster-whisper-large-v3-turbo-ct2"]

for repo in targets:
    try:
        print(f"[PreCache] Скачивание {repo} в локальный кэш...")
        snapshot_download(
            repo_id=repo,
            cache_dir="/workspace/.cache/huggingface",
            local_files_only=False,
            token=HF_TOKEN,
        )
    except Exception as e:
        print(f"⚠️ [PreCache] Не удалось скачать {repo}: {e}")
print("[PreCache] ✅ Завершено")
PY
fi


# --- 3. Финальная диагностика ---
echo "=== [Entrypoint] Проверка системы ==="
echo "DISPLAY=$DISPLAY"
echo "PULSE_SERVER=$PULSE_SERVER"
echo "Python version: $(python3 --version)"
echo "--- [DIAG] Устройства, видимые для Python/SoundDevice на старте ---"
python3 -c "import sounddevice as sd; print(sd.query_devices())"
echo "---------------------