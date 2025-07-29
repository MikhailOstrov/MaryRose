#!/bin/bash
set -e

echo "===== [Entrypoint] - STARTING ENVIRONMENT SETUP ====="

# --- 1. Xvfb (Virtual Display) ---
# УДАЛЯЕМ LOCK-ФАЙЛ, если он остался от предыдущего запуска.
# Это делает запуск более надежным, особенно после сбоев.
echo "[Entrypoint] Removing old Xvfb lock file if it exists..."
rm -f /tmp/.X99-lock

# Запускаем виртуальный X-сервер на дисплее :99
# Это необходимо, чтобы Chrome мог работать в headless-режиме, но при этом "думать", что у него есть экран.
echo "[Entrypoint] Starting Xvfb on display :99..."
export DISPLAY=:99
Xvfb :99 -screen 0 1280x720x16 &
sleep 3 # Даем время на запуск

# Проверяем, что Xvfb запустился успешно
if ! xdpyinfo -display $DISPLAY >/dev/null 2>&1; then
  echo "[Entrypoint] CRITICAL: Xvfb failed to start. Aborting."
  exit 1
fi
echo "[Entrypoint] Xvfb started successfully."


# --- 2. PulseAudio (Virtual Sound Card) ---
# Запускаем PulseAudio для создания виртуальных аудио-устройств.
# Это позволяет нам перехватывать звук, который воспроизводит Chrome.
echo "[Entrypoint] Starting PulseAudio server..."
pulseaudio --start --exit-idle-time=-1 --daemonize
sleep 3 # Даем время на запуск

# Проверяем, что PulseAudio отвечает
if ! pactl info >/dev/null 2>&1; then
    echo "[Entrypoint] WARNING: PulseAudio server not responding. Bot will not hear audio."
else
    echo "[Entrypoint] PulseAudio started. Setting up virtual audio devices..."
    # Создаем виртуальное устройство вывода (sink), куда Chrome будет направлять звук.
    pactl load-module module-null-sink sink_name=meet_sink sink_properties=device.description="MeetSink"
    # Создаем виртуальный источник (микрофон), который слушает монитор нашего sink
    # УБИРАЕМ явное указание формата, чтобы PulseAudio сам занимался преобразованием, как в оригинальном скрипте
    pactl load-module module-virtual-source source_name=meet_mic master=meet_sink.monitor

    # Устанавливаем наши виртуальные устройства по умолчанию
    pactl set-default-sink meet_sink
    pactl set-default-source meet_mic

    echo "[Entrypoint] Virtual audio devices 'meet_sink' and 'meet_mic' created."
    echo "--- Audio Setup Diagnostics ---"
    echo "--- pactl info ---"
    pactl info
    echo "--- Sinks ---"
    pactl list sinks short
    echo "--- Sources ---"
    pactl list sources short
    echo "-------------------------------"
fi

# --- 3. Chrome Profile ---
# Создаем директорию для профиля Chrome, если ее нет.
# Это нужно для сохранения сессии и куки.
echo "[Entrypoint] Ensuring Chrome profile directory exists at /app/chrome_profile"
mkdir -p /app/chrome_profile

# Очищаем кэш и сессии Chrome от предыдущих запусков (взято из join_meet)
echo "[Entrypoint] Cleaning up old Chrome session and cache files..."
rm -rf /app/chrome_profile/Default/Service* 2>/dev/null || true
rm -rf /app/chrome_profile/Default/Session* 2>/dev/null || true


chmod -R 777 /app/chrome_profile

echo "===== [Entrypoint] - ENVIRONMENT READY ====="
echo "[Entrypoint] Handing over to CMD: $@"
echo "==============================================="

# Выполняем команду, переданную в Dockerfile (например, uvicorn)
exec "$@"