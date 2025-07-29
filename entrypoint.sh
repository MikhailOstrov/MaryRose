#!/bin/bash
set -e

echo "=== Настройка окружения (ТОЧНАЯ КОПИЯ join_meet) ==="

# --- 1. Переменные окружения и Display ---
export DISPLAY=:99
# Эти переменные могут быть критичны для стабильности Chrome в Docker
export CHROME_DEVEL_SANDBOX=/usr/lib/chromium-browser/chrome-sandbox
export CHROME_FLAGS="--memory-pressure-off --max_old_space_size=4096"

echo "[Entrypoint] Создание профиля Chrome с правильными правами..."
mkdir -p /app/chrome_profile
chmod 755 /app/chrome_profile

echo "[Entrypoint] Очистка кэша Chrome..."
rm -rf /app/chrome_profile/Default/Service* 2>/dev/null || true
rm -rf /app/chrome_profile/Default/Session* 2>/dev/null || true

# --- 2. Запуск служб ---
echo "[Entrypoint] Запуск Xvfb..."
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x720x16 &
sleep 3

if xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "✅ [Entrypoint] Xvfb готов!"
else
    echo "❌ [Entrypoint] Xvfb не запустился."
    exit 1
fi

echo "[Entrypoint] Запуск PulseAudio..."
# Используем метод запуска из join_meet
export PULSE_RUNTIME_PATH=/tmp/pulse-runtime
mkdir -p $PULSE_RUNTIME_PATH
pulseaudio --start --exit-idle-time=-1 --daemonize
sleep 3

echo "[Entrypoint] Настройка аудио..."
if pactl info >/dev/null 2>&1; then
    echo "✅ [Entrypoint] PulseAudio работает!"
    pactl load-module module-null-sink sink_name=meet_sink sink_properties=device.description="Virtual_Sink_for_Meet"
    pactl set-default-sink meet_sink
    pactl load-module module-virtual-source source_name=meet_mic master=meet_sink.monitor
    pactl set-default-source meet_mic

    echo "--- [Entrypoint] Диагностика аудио ---"
    echo "Default Sink (Output): $(pactl get-default-sink)"
    echo "Default Source (Input): $(pactl get-default-source)"
    echo "--- Доступные источники (микрофоны) ---"
    pactl list sources short
    echo "----------------------------------------"
else
    echo "⚠️ [Entrypoint] PulseAudio не отвечает. Аудио не будет работать."
fi

# --- 3. Финальные проверки и запуск ---
echo "=== [Entrypoint] Проверка системы ==="
echo "DISPLAY=$DISPLAY"
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome не найден')"
echo "ChromeDriver: $(chromedriver --version 2>/dev/null || echo 'ChromeDriver не найден')"
echo "Python version: $(python3 --version)"
echo "Available memory: $(free -h | grep Mem)"

echo "=== [Entrypoint] Запуск основного приложения ==="
echo "[Entrypoint] Передача управления команде: $@"
# Выполняем команду, переданную из Dockerfile (uvicorn)
exec "$@"