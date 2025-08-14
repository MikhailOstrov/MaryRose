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

# --- 1. Настройка Display и Chrome ---
export DISPLAY=:99
# Очищаем кэш и сессии Chrome от предыдущих запусков для чистого старта
echo "[Entrypoint] Очистка старых сессий Chrome..."
rm -rf /app/chrome_profile/Default/Service* 2>/dev/null || true
rm -rf /app/chrome_profile/Default/Session* 2>/dev/null || true

# --- 2. Запуск служб ---
echo "[Entrypoint] Запуск виртуального дисплея Xvfb..."
# Удаляем lock-файл на случай некорректного завершения предыдущей сессии
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x720x16 &
# Даем время на запуск
sleep 3

echo "[Entrypoint] Проверка Xvfb..."
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: Xvfb не запустился. Прерывание."
    exit 1
fi
echo "✅ [Entrypoint] Xvfb готов!"


echo "[Entrypoint] Запуск аудиосервера PulseAudio..."
# Устанавливаем путь для сокета PulseAudio
export PULSE_RUNTIME_PATH=/tmp/pulse-runtime
mkdir -p $PULSE_RUNTIME_PATH
# Запускаем PulseAudio как демон, который не будет завершаться при бездействии
pulseaudio --start --exit-idle-time=-1 --daemonize
# Даем время на запуск
sleep 3

# Проверяем, что сервер PulseAudio действительно запустился и отвечает
echo "[Entrypoint] Проверка PulseAudio..."
if ! pactl info >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: PulseAudio не запустился. Захват звука не будет работать."
    exit 1
fi
echo "✅ [Entrypoint] PulseAudio сервер готов. Управление аудиоустройствами будет выполняться приложением динамически."

# --- БЛОК СОЗДАНИЯ ГЛОБАЛЬНЫХ АУДИОУСТРОЙСТВ ПОЛНОСТЬЮ УДАЛЕН ---
# Приложение само будет создавать уникальные устройства для каждого бота.
# -----------------------------------------------------------------


# --- 3. Предзагрузка кэша моделей (опционально) ---
# Эта логика остается без изменений
if [ "${PREWARM:-off}" = "download_only" ]; then
  echo "=== [Entrypoint] Предзагрузка весов моделей (pre-cache) ==="
  python3 - <<'PY'
import os
import sys
from huggingface_hub import snapshot_download

HF_TOKEN = os.getenv("HUGGING_FACE_HUB_TOKEN") or os.getenv("HF_TOKEN")

targets = [
    # Убедитесь, что здесь актуальный список моделей, если он изменится
    "deepdml/faster-whisper-large-v3-turbo-ct2",
]

for repo in targets:
    try:
        print(f"[PreCache] Скачивание {repo} в локальный кэш...")
        snapshot_download(
            repo_id=repo,
            cache_dir="/workspace/.cache/huggingface",
            local_files_only=False,
            token=HF_TOKEN,
            # ignore_patterns=["*.safetensors.index.json", "*.h5", "*.gguf"], # Можно добавить игнорирование ненужных файлов
        )
    except Exception as e:
        print(f"⚠️ [PreCache] Не удалось скачать {repo}: {e}")
print("[PreCache] ✅ Завершено")
PY
fi

# --- 4. Проверки системы ---
echo "=== [Entrypoint] Проверка системы ==="
echo "DISPLAY=$DISPLAY"
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
echo "ChromeDriver: $(chromedriver --version 2>/dev/null || echo 'ChromeDriver не найден')"
echo "Python version: $(python3 --version)"
echo "Available memory: $(free -h | grep Mem)"
echo "--- Доступные аудиоустройства (на старте должны быть только системные) ---"
pactl list sources short
pactl list sinks short
echo "---------------------------------------------------------------------"


# --- НОВЫЙ БЛОК ДИАГНОСТИКИ ---
echo "--- [DIAG] Устройства, видимые для Python/SoundDevice на старте ---"
python3 -c "import sounddevice as sd; print(sd.query_devices())"
echo "------------------------------------------------------------------"
# --- КОНЕЦ БЛОКА ДИАГНОСТИКИ ---

echo "=== [Entrypoint] Запуск основного приложения ==="
echo "[Entrypoint] Передача управления команде: $@"
# Выполняем команду, переданную из Dockerfile (например, uvicorn ...)
exec "$@"