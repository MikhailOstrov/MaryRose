#!/bin/bash
set -e

# Этот скрипт ожидает, что он будет запущен от имени непривилегированного пользователя ('appuser'),
# как это настроено в Dockerfile с помощью команды 'USER appuser'.

echo "=== [Entrypoint] Запуск от пользователя: $(whoami) ==="

# --- 0. Проверка и настройка /workspace ---
echo "[Entrypoint] Проверка Volume диска /workspace..."
if [ ! -d "/workspace" ]; then
    echo "❌ [Entrypoint] CRITICAL: /workspace не найден. Убедитесь, что Volume диск подключен."
    exit 1
fi
echo "[Entrypoint] Создание структуры папок в /workspace..."
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models
echo "✅ [Entrypoint] Структура /workspace создана."

# Настройка переменных окружения для моделей
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
echo "[Entrypoint] Переменные окружения для моделей настроены."


# --- 1. Настройка окружения и PulseAudio (от appuser) ---
# Для PulseAudio нужен этот каталог с правильными правами.
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-"/tmp/runtime-$(whoami)"}
mkdir -p -m 0700 "$XDG_RUNTIME_DIR"

# --- ГЛОБАЛЬНЫЙ XVFB БОЛЬШЕ НЕ ЗАПУСКАЕТСЯ ЗДЕСЬ ---
# Python-код будет запускать xvfb-run для каждого экземпляра Chrome.
echo "[Entrypoint] Глобальный Xvfb пропущен. Будет использоваться xvfb-run."

# Запускаем PulseAudio в обычном, пользовательском режиме.
echo "[Entrypoint] Запуск PulseAudio в пользовательском режиме..."
pulseaudio --start --log-target=stderr --exit-idle-time=-1
sleep 2 # Пауза для инициализации

# Проверяем, что PulseAudio запустился
if ! pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: PulseAudio не запустился. Проверьте права доступа."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio готов."

# Находим путь к сокету PulseAudio и экспортируем его.
# Это нужно для paplay в Python-коде, чтобы он точно нашел сервер.
export PULSE_SERVER=$(pactl info | grep 'Server String' | awk '{print $3}')
echo "[Entrypoint] PulseAudio сокет экспортирован: $PULSE_SERVER"


# --- 2. Предзагрузка моделей (опционально, от appuser) ---
if [ "${PREWARM:-off}" = "download_only" ]; then
  echo "=== [Entrypoint] Предзагрузка весов моделей... ==="
  python3 - <<'PY'
import os, sys
from huggingface_hub import snapshot_download
HF_TOKEN = os.getenv("HUGGING_FACE_HUB_TOKEN") or os.getenv("HF_TOKEN")
targets = ["deepdml/faster-whisper-large-v3-turbo-ct2"]
for repo in targets:
    try:
        print(f"[PreCache] Скачивание {repo}...")
        snapshot_download(repo_id=repo, cache_dir="/workspace/.cache/huggingface", token=HF_TOKEN)
    except Exception as e:
        print(f"⚠️ [PreCache] Не удалось скачать {repo}: {e}")
print("[PreCache] ✅ Завершено")
PY
fi


# --- 3. Финальная диагностика и проверки ---
echo "=== [Entrypoint] Проверка системы ==="
echo "Python version: $(python3 --version)"
echo "Available memory: $(free -h | grep Mem)"
echo "--- [DIAG] Доступные аудиоустройства (pactl) ---"
pactl list sources short
pactl list sinks short
echo "------------------------------------------------"


# --- 4. Запуск основного приложения (от appuser) ---
echo "=== [Entrypoint] Запуск приложения... ==="
# Просто выполняем команду, так как мы уже являемся пользователем 'appuser'.
exec "$@"