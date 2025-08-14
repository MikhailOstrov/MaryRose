#!/bin/bash
set -e

echo "=== [Entrypoint] Настройка окружения для RunPod ==="

# --- 0. Проверка и настройка /workspace ---
echo "[Entrypoint] Проверка Volume диска /workspace..."
if [ ! -d "/workspace" ]; then
    echo "❌ [Entrypoint] CRITICAL: /workspace не найден. Убедитесь, что Volume диск подключен."
    exit 1
fi

echo "[Entrypoint] Создание структуры папок в /workspace..."
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models
echo "✅ [Entrypoint] Структура /workspace создана."

# Настройка переменных окружения для кэширования моделей
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
echo "[Entrypoint] Переменные окружения для моделей настроены."

# --- 1. Настройка Display ---
export DISPLAY=:99
echo "[Entrypoint] Запуск виртуального дисплея Xvfb..."
rm -f /tmp/.X99-lock # Удаляем lock-файл на случай некорректного завершения
Xvfb :99 -screen 0 1280x720x16 &
sleep 3

echo "[Entrypoint] Проверка Xvfb..."
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: Xvfb не запустился. Прерывание."
    exit 1
fi
echo "✅ [Entrypoint] Xvfb готов!"


# --- 2. Запуск служб ---
echo "[Entrypoint] Запуск системного аудиосервера PulseAudio..."
# Запускаем в системном режиме (--system), чтобы избежать проблем с правами доступа в Docker.
# Это создаст общедоступный сокет, который увидят все приложения.
# --disallow-exit и --exit-idle-time=-1 гарантируют, что сервер не завершится.
# --log-target=stderr направляет логи PA в стандартный поток ошибок контейнера.
pulseaudio --system --disallow-exit --exit-idle-time=-1 --log-target=stderr --daemonize
sleep 3 # Даем время на полный запуск сервера

# Проверяем, что сервер действительно запустился и отвечает
echo "[Entrypoint] Проверка PulseAudio..."
if ! pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: PulseAudio не запустился. Захват звука не будет работать."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio сервер готов. Управление аудиоустройствами будет выполняться приложением динамически."


# --- 3. Предзагрузка кэша моделей (опционально) ---
# Эта логика остается без изменений
if [ "${PREWARM:-off}" = "download_only" ]; then
  echo "=== [Entrypoint] Предзагрузка весов моделей (pre-cache) ==="
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

# --- 4. Проверки системы и диагностика ---
echo "=== [Entrypoint] Проверка системы ==="
echo "DISPLAY=$DISPLAY"
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
echo "ChromeDriver: $(chromedriver --version 2>/dev/null || echo 'ChromeDriver не найден')"
echo "Python version: $(python3 --version)"
echo "Available memory: $(free -h | grep Mem)"
echo "--- [DIAG] Доступные аудиоустройства (на старте должны быть только системные) ---"
pactl list sources short
pactl list sinks short
echo "---------------------------------------------------------------------"
echo "--- [DIAG] Устройства, видимые для Python/SoundDevice на старте ---"
python3 -c "import sounddevice as sd; print(sd.query_devices())"
echo "------------------------------------------------------------------"


echo "=== [Entrypoint] Запуск основного приложения ==="
echo "[Entrypoint] Передача управления команде: $@"
# Выполняем команду, переданную из Dockerfile (например, uvicorn ...)
exec "$@"