#!/bin/bash
set -e

echo "=== Настройка окружения ==="

# Устанавливаем DISPLAY в самом начале
export DISPLAY=:99

# УВЕЛИЧИВАЕМ ПАМЯТЬ ДЛЯ CHROME
export CHROME_DEVEL_SANDBOX=/usr/lib/chromium-browser/chrome-sandbox
export CHROME_FLAGS="--memory-pressure-off --max_old_space_size=4096"

echo "Creating Chrome profile directory with proper permissions..."
mkdir -p /app/chrome_profile
chmod 755 /app/chrome_profile

# Очищаем кэш Chrome на всякий случай
rm -rf /app/chrome_profile/Default/Service* 2>/dev/null || true
rm -rf /app/chrome_profile/Default/Session* 2>/dev/null || true

echo "=== Запуск служб ==="

echo "Starting Xvfb..."
Xvfb :99 -screen 0 1280x720x16 &
sleep 3

echo "Checking Xvfb..."
if xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "✅ Xvfb is ready!"
else
    echo "❌ Xvfb failed to start"
fi

echo "Starting PulseAudio in USER mode (simpler)..."
# Пробуем самый простой способ - пользовательский режим
export PULSE_RUNTIME_PATH=/tmp/pulse-runtime
mkdir -p $PULSE_RUNTIME_PATH
pulseaudio --start --exit-idle-time=-1 --daemonize
sleep 3

echo "Setting up audio (if PulseAudio responds)..."
if pactl info >/dev/null 2>&1; then
    echo "✅ PulseAudio is working!"
    # ПРИНУДИТЕЛЬНО создаем sink с форматом, который нужен Python-коду (16000Hz, 1 канал, s16le)
    pactl load-module module-null-sink sink_name=meet_sink format=s16le channels=1 rate=16000 sink_properties=device.description="Virtual_Sink_for_Meet"
    # Устанавливаем эту раковину как устройство вывода по умолчанию
    pactl set-default-sink meet_sink
    # Создаем "микрофон", который слушает монитор раковины и наследует ее формат.
    pactl load-module module-virtual-source source_name=meet_mic master=meet_sink.monitor
    # Устанавливаем этот микрофон как устройство ввода по умолчанию
    pactl set-default-source meet_mic

    echo "--- Audio Setup Complete ---"
    echo "Default Sink (Output):"
    pactl get-default-sink
    echo "Default Source (Input):"
    pactl get-default-source
    echo "Available Sources:"
    pactl list sources short
    echo "----------------------------"
else
    echo "⚠️ PulseAudio not responding - continuing without audio setup"
fi

echo "=== Проверка системы ==="
echo "DISPLAY=$DISPLAY"
echo "Chrome version: $(google-chrome --version 2>/dev/null || echo 'Chrome not found')"
echo "ChromeDriver: $(chromedriver --version 2>/dev/null || echo 'ChromeDriver not found')"
echo "Available memory: $(free -h | grep Mem)"

echo "=== [Entrypoint] Запуск основного приложения ==="
echo "[Entrypoint] Передача управления команде: $@"
# Выполняем команду, переданную из Dockerfile (uvicorn)
exec "$@"