#!/bin/bash
set -e

# Этот скрипт должен запускаться от имени root.
# Он настроит системные службы и затем передаст управление
# непривилегированному пользователю 'appuser'.

echo "=== [Entrypoint] Запуск от пользователя: $(whoami) ==="

# --- 0. Проверка и настройка /workspace (из вашего оригинала) ---
echo "[Entrypoint] Проверка Volume диска /workspace..."
if [ ! -d "/workspace" ]; then
    echo "❌ [Entrypoint] CRITICAL: /workspace не найден. Убедитесь, что Volume диск подключен."
    exit 1
fi
echo "[Entrypoint] Создание структуры папок в /workspace..."
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models
echo "✅ [Entrypoint] Структура /workspace создана."

# Настройка переменных окружения для моделей (из вашего оригинала)
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
echo "[Entrypoint] Переменные окружения для моделей настроены."


# --- 1. Настройка системных служб (от ROOT) ---
echo "[Entrypoint] Настройка служб от имени ROOT..."
export DISPLAY=:99

# Очистка старых сессий Chrome (из вашего оригинала)
echo "[Entrypoint] Очистка старых сессий Chrome..."
rm -rf /app/chrome_profile/Default/Service* 2>/dev/null || true
rm -rf /app/chrome_profile/Default/Session* 2>/dev/null || true

# Запускаем Xvfb (виртуальный дисплей)
echo "[Entrypoint] Запуск Xvfb..."
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x720x16 &
sleep 2 # Пауза для инициализации

# Запускаем PulseAudio в системном режиме, так как он предназначен для запуска от root
# и обслуживания всех пользователей в системе (в нашем случае - в контейнере).
echo "[Entrypoint] Запуск PulseAudio в системном режиме..."
# Добавляем --fail=1, чтобы он немедленно завершался с ошибкой, если не сможет запуститься
pulseaudio --system --disallow-exit --exit-idle-time=-1 --log-target=stderr --daemonize --fail=1
sleep 2 # Пауза для инициализации

# --- 2. Проверка запущенных служб ---
echo "[Entrypoint] Проверка служб..."
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: Xvfb не запустился."
    exit 1
fi
echo "✅ [Entrypoint] Xvfb готов."

if ! pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: PulseAudio не запустился."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio готов."


# --- 3. Предзагрузка моделей (опционально, от root, из вашего оригинала) ---
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


# --- 4. Финальная диагностика и проверки (из вашего оригинала) ---
echo "=== [Entrypoint] Проверка системы ==="
echo "DISPLAY=$DISPLAY"
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
echo "ChromeDriver: $(chromedriver --version 2>/dev/null || echo 'ChromeDriver не найден')"
echo "Python version: $(python3 --version)"
echo "Available memory: $(free -h | grep Mem)"
echo "--- [DIAG] Доступные аудиоустройства (pactl) ---"
pactl list sources short
pactl list sinks short
echo "------------------------------------------------"

# Выполняем диагностику sounddevice от имени 'appuser', чтобы среда была идентична
# той, в которой будет работать приложение.
echo "--- [DIAG] Устройства, видимые для Python/SoundDevice (от appuser) ---"
gosu appuser python3 -c "import sounddevice as sd; print(sd.query_devices())"
echo "------------------------------------------------------------------"


# --- 5. Запуск основного приложения (от appuser) ---
echo "=== [Entrypoint] Переключение на пользователя 'appuser' и запуск приложения... ==="
# Используем gosu для передачи управления основному процессу.
# gosu appuser - выполняет команду от имени пользователя appuser
# "$@" - это команда, переданная из CMD Dockerfile (т.е. uvicorn ...).
exec gosu appuser "$@"