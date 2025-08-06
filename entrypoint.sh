#!/bin/bash
set -e

echo "=== [Entrypoint] Настройка окружения для RunPod (версия из join_meet) ==="

# --- 0. Проверка и настройка /workspace ---
echo "[Entrypoint] Проверка Volume диска /workspace..."
if [ ! -d "/workspace" ]; then
    echo "❌ [Entrypoint] CRITICAL: /workspace не найден. Убедитесь, что Volume диск подключен."
    exit 1
fi

echo "[Entrypoint] Создание структуры папок в /workspace..."
mkdir -p /workspace/.cache/torch
mkdir -p /workspace/.cache/nemo
mkdir -p /workspace/.cache/huggingface  
mkdir -p /workspace/models
echo "✅ [Entrypoint] Структура /workspace создана."

# Настройка переменных окружения для моделей
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
echo "[Entrypoint] Переменные окружения для моделей настроены."

# --- 1. Настройка Display и Chrome ---
export DISPLAY=:99
# Переменные Chrome (как в рабочем join_meet)
export CHROME_DEVEL_SANDBOX=/usr/lib/chromium-browser/chrome-sandbox
export CHROME_FLAGS="--memory-pressure-off --max_old_space_size=4096"
# Очищаем кэш и сессии Chrome от предыдущих запусков
echo "[Entrypoint] Очистка старых сессий Chrome..."
rm -rf /app/chrome_profile/Default/Service* 2>/dev/null || true
rm -rf /app/chrome_profile/Default/Session* 2>/dev/null || true

# --- 2. Запуск служб ---
echo "[Entrypoint] Запуск Xvfb..."
# Удаляем lock-файл на всякий случай
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x720x16 &
sleep 3

echo "[Entrypoint] Проверка Xvfb..."
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "❌ [Entrypoint] CRITICAL: Xvfb не запустился. Прерывание."
    exit 1
fi
echo "✅ [Entrypoint] Xvfb готов!"


echo "[Entrypoint] Запуск PulseAudio в USER режиме (как в рабочем join_meet)..."
# ИСПОЛЬЗУЕМ ТОЧНО ТАКУЮ ЖЕ НАСТРОЙКУ КАК В РАБОЧЕМ join_meet/entrypoint.sh
export PULSE_RUNTIME_PATH=/tmp/pulse-runtime
mkdir -p $PULSE_RUNTIME_PATH
pulseaudio --start --exit-idle-time=-1 --daemonize
sleep 3

echo "[Entrypoint] Настройка виртуального аудио..."
if pactl info >/dev/null 2>&1; then
    echo "✅ [Entrypoint] PulseAudio работает. Создание устройств..."
    # Создаем виртуальную "раковину" (колонки) для вывода звука из Chrome
    pactl load-module module-null-sink sink_name=meet_sink sink_properties=device.description="Virtual_Sink_for_Meet"
    # Устанавливаем эту раковину как устройство вывода по умолчанию
    pactl set-default-sink meet_sink
    # Создаем виртуальный "микрофон", который слушает "раковину".
    pactl load-module module-virtual-source source_name=meet_mic master=meet_sink.monitor
    # Устанавливаем этот микрофон как устройство ввода по умолчанию
    pactl set-default-source meet_mic

    echo "--- [Entrypoint] Диагностика аудио ---"
    echo "Default Sink (Output): $(pactl get-default-sink)"
    echo "Default Source (Input): $(pactl get-default-source)"
    echo "--- Доступные источники (микрофоны) ---"
    pactl list sources short
    echo "----------------------------------------"
else
    echo "⚠️ [Entrypoint] PulseAudio не отвечает. Захват звука не будет работать."
fi

# --- 3. Загрузка моделей (ПЕРВЫМ ДЕЛОМ!) ---
echo "=== [Entrypoint] Загрузка моделей в /workspace ==="
echo "[Entrypoint] Загрузка моделей при первом запуске может занять несколько минут..."

python3 -c "
import sys
sys.path.append('/app')
try:
    print('[Model Load] Начинаем проверку и загрузку моделей...')
    from config.load_models import llm_model, asr_model, vad_model, tts_model, diarizer_config
    print('[Model Load] ✅ Все модели успешно загружены и готовы к использованию')
except Exception as e:
    print(f'[Model Load] ❌ Ошибка загрузки моделей: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    echo "❌ [Entrypoint] CRITICAL: Загрузка моделей не удалась. Прерывание."
    exit 1
fi

echo "✅ [Entrypoint] Все модели загружены и готовы!"

# --- 4. Проверки системы ---
echo "=== [Entrypoint] Проверка системы ==="
echo "DISPLAY=$DISPLAY"
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
echo "ChromeDriver: $(chromedriver --version 2>/dev/null || echo 'ChromeDriver не найден')"
echo "Python version: $(python3 --version)"
echo "Available memory: $(free -h | grep Mem)"

# Ollama не используется - приложение работает с HuggingFace моделями напрямую

echo "=== [Entrypoint] Запуск основного приложения ==="
echo "[Entrypoint] Передача управления команде: $@"
# Выполняем команду, переданную из Dockerfile (uvicorn)
exec "$@"