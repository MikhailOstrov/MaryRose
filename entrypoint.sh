#!/bin/bash
set -e

# Этот скрипт ожидает, что он будет запущен от имени непривилегированного пользователя ('appuser'),
# как это настроено в Dockerfile с помощью команды 'USER appuser'.

echo "=== [Entrypoint] Запуск от пользователя: $(whoami) ==="

# --- 0. Проверка и настройка /workspace (из вашего оригинала) ---
echo "[Entrypoint] Проверка Volume диска /workspace..."
if [ ! -d "/workspace" ]; then
    echo "❌ [Entrypoint] CRITICAL: /workspace не найден. Убедитесь, что Volume диск подключен."
    exit 1
fi
echo "[Entrypoint] Создание структуры папок в /workspace..."
# Пользователь appuser должен иметь права на эти папки, что настраивается в Dockerfile.
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models
echo "✅ [Entrypoint] Структура /workspace создана."

# Настройка переменных окружения для моделей (из вашего оригинала)
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
echo "[Entrypoint] Переменные окружения для моделей настроены."


# --- 1. Настройка окружения и служб (от appuser) ---
# Для запуска служб от имени пользователя, им может потребоваться специальный каталог.
# Убедимся, что он существует и имеет правильные права.
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-"/tmp/runtime-$(whoami)"}
mkdir -p -m 0700 "$XDG_RUNTIME_DIR"

export DISPLAY=:99

# Очистка старых сессий Chrome (из вашего оригинала)
echo "[Entrypoint] Очистка старых сессий Chrome..."
rm -rf /app/chrome_profile/Default/Service* 2>/dev/null || true
rm -rf /app/chrome_profile/Default/Session* 2>/dev/null || true

# Запускаем Xvfb с опцией, не требующей прав root
echo "[Entrypoint] Запуск Xvfb..."
Xvfb :99 -screen 0 1280x720x16 -nolisten tcp &
sleep 2 # Пауза для инициализации

# Запускаем PulseAudio в обычном, пользовательском режиме.
echo "[Entrypoint] Запуск PulseAudio в пользовательском режиме..."
pulseaudio --start --log-target=stderr --exit-idle-time=-1
sleep 2 # Пауза для инициализации


# --- 2. Проверка запущенных служб ---
echo "[Entrypoint] Проверка служб..."
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "⚠️ [Entrypoint] WARNING: Xvfb не ответил, но продолжаем."
fi
echo "✅ [Entrypoint] Xvfb готов."

if ! pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: PulseAudio не запустился. Проверьте права доступа."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio готов."

# --- 3. ЗАПУСК НАШЕГО "ЧЕРНОГО ЯЩИКА" (НОВЫЙ БЛОК) ---
echo "=== [Entrypoint] Запуск скрипта мониторинга памяти в фоновом режиме... ==="
# Делаем наш скрипт исполняемым, чтобы его можно было запустить.
chmod +x /app/monitor.sh
# Запускаем его в фоновом режиме (символ '&' в конце).
# Он будет работать параллельно с нашим основным приложением.
/app/monitor.sh &
echo "✅ [Entrypoint] Мониторинг запущен. Логи будут писаться в /workspace/memory_log.txt"
# ----------------------------------------  


# --- 3. Предзагрузка моделей (опционально, от appuser) ---
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

# Выполняем диагностику sounddevice. Так как мы уже appuser, gosu не нужен.
echo "--- [DIAG] Устройства, видимые для Python/SoundDevice ---"
python3 -c "import sounddevice as sd; print(sd.query_devices())"
echo "------------------------------------------------------------------"


# --- 5. Запуск основного приложения (от appuser) ---
echo "=== [Entrypoint] Переключение на пользователя 'appuser' и запуск приложения... ==="
# Просто выполняем команду, так как мы уже являемся пользователем 'appuser'.
# gosu здесь не нужен и вызовет ошибку.
exec "$@"