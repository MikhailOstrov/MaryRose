#!/bin/bash
set -e

# Этот скрипт должен запускаться от имени root.
# Он настроит системные службы и затем передаст управление
# непривилегированному пользователю 'appuser'.

echo "=== [Entrypoint] Запуск от пользователя: $(whoami) ==="

# --- 1. Настройка системных служб (от ROOT) ---
echo "[Entrypoint] Настройка служб от имени ROOT..."
export DISPLAY=:99

# Запускаем Xvfb (виртуальный дисплей)
echo "[Entrypoint] Запуск Xvfb..."
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x720x16 &
sleep 2 # Пауза для инициализации

# Запускаем PulseAudio в системном режиме.
# Он создан для таких сценариев: запуск от root для обслуживания всех пользователей.
echo "[Entrypoint] Запуск PulseAudio в системном режиме..."
pulseaudio --system --disallow-exit --exit-idle-time=-1 --log-target=stderr --daemonize
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


# --- 3. Предзагрузка моделей (опционально, от root) ---
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


# --- 4. Финальная диагностика ---
# Выполняем диагностику от имени 'appuser', чтобы среда была идентична
# той, в которой будет работать приложение.
echo "--- [DIAG] Устройства, видимые для Python/SoundDevice (от appuser) ---"
gosu appuser python3 -c "import sounddevice as sd; print(sd.query_devices())"
echo "------------------------------------------------------------------"


# --- 5. Запуск основного приложения (от appuser) ---
echo "=== [Entrypoint] Переключение на пользователя 'appuser' и запуск приложения... ==="
# Используем gosu для передачи управления основному процессу.
# gosu appuser - выполянет команду от имени пользователя appuser
# "$@" - это команда, переданная из CMD Dockerfile (т.е. uvicorn ...).
exec gosu appuser "$@"