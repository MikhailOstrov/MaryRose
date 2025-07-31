#!/bin/bash
set -e

echo "=== [Entrypoint] Настройка окружения (версия из join_meet) ==="

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

# --- 3. Проверки и запуск приложения ---
echo "=== [Entrypoint] Проверка системы ==="
echo "DISPLAY=$DISPLAY"
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
echo "ChromeDriver: $(chromedriver --version 2>/dev/null || echo 'ChromeDriver не найден')"
echo "Python version: $(python3 --version)"
echo "Available memory: $(free -h | grep Mem)"

# Запускаем сервер Ollama в фоновом режиме
/usr/local/bin/ollama serve &

echo "[Entrypoint] Ожидание готовности сервера Ollama..."
# Используем цикл while с curl для проверки, что сервер отвечает, вместо жесткого sleep
# -s - silent
# -f - fail silently (return non-zero on server errors)
# -o /dev/null - discard output
timeout 60 bash -c 'until curl -sf -o /dev/null http://localhost:11434; do echo "Сервер Ollama еще не готов, ждем..."; sleep 2; done'

if [ $? -ne 0 ]; then
    echo "❌ [Entrypoint] CRITICAL: Сервер Ollama не запустился за 60 секунд. Прерывание."
    exit 1
fi

echo "✅ [Entrypoint] Сервер Ollama готов и отвечает."

echo "=== [Entrypoint] Запуск основного приложения ==="
echo "[Entrypoint] Передача управления команде: $@"
# Выполняем команду, переданную из Dockerfile (uvicorn)
exec "$@"