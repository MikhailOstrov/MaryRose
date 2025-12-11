#!/bin/bash
set -e

# Функция для логирования
log() {
    echo "[Entrypoint] $1"
}

log "Старт контейнера от пользователя: $(whoami)"

# --- 1. Настройка прав доступа (работаем как root) ---
log "Настройка прав доступа..."

# --- РАННИЙ ЗАПУСК SSH (ДЛЯ VAST.AI) ---
# Запускаем SSH сразу же, чтобы иметь доступ в случае проблем с остальным скриптом
log "Ранний запуск SSH сервера..."
mkdir -p /root/.ssh
chmod 700 /root/.ssh
# Если Vast.ai прокинул ключи через ENV, можно их добавить (обычно он делает это сам через volume или docker copy)
if [ ! -d "/var/run/sshd" ]; then
    mkdir -p /var/run/sshd
fi
/usr/sbin/sshd
log "SSH сервер запущен."

# Генерируем machine-id, если нет (нужен для D-Bus и PulseAudio)
if [ ! -f /etc/machine-id ]; then
    log "Генерация /etc/machine-id..."
    dbus-uuidgen > /etc/machine-id
fi

# Убедимся, что рабочие директории принадлежат appuser
# Это критично, если папки смонтированы как volumes
chown -R appuser:appuser /app /workspace

# Создаем и настраиваем XDG_RUNTIME_DIR для звука
export XDG_RUNTIME_DIR=/tmp/runtime-appuser
mkdir -p -m 0700 "$XDG_RUNTIME_DIR"
chown appuser:appuser "$XDG_RUNTIME_DIR"
log "XDG_RUNTIME_DIR настроен: $XDG_RUNTIME_DIR"

# Настройка кэшей
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models /workspace/logs
chown -R appuser:appuser /workspace/.cache /workspace/models /workspace/logs

# Экспортируем переменные, чтобы gosu их подхватил
export HOME=/app
export TORCH_HOME=/workspace/.cache/torch
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs

# --- 2. Запуск D-Bus (нужен для PulseAudio) ---
log "Запуск D-Bus..."
mkdir -p /var/run/dbus
# Очищаем старый pid файл если есть
rm -f /var/run/dbus/pid
dbus-daemon --system --fork
sleep 1

# --- 2.5 Запуск SSH сервера ---
# log "Запуск SSH сервера..."
# /usr/sbin/sshd

# --- 3. Запуск PulseAudio (от имени appuser) ---
log "Запуск PulseAudio от пользователя appuser..."

# 1. Полная зачистка и ПОДГОТОВКА HOMEDIR
# PulseAudio берет домашнюю директорию из /etc/passwd, даже если HOME переопределен.
# Создаем /home/appuser, чтобы он не падал.
mkdir -p /home/appuser/.config/pulse
chown -R appuser:appuser /home/appuser

# Чистим runtime
rm -rf /tmp/runtime-appuser/pulse
rm -rf /app/.config/pulse
mkdir -p /app/.config/pulse
chown -R appuser:appuser /app/.config
chown -R appuser:appuser /tmp/runtime-appuser

# 2. Создаем минимальный скрипт запуска PA (default.pa)
# Кладем конфиг и в /app, и в /home/appuser на всякий случай
CONF_CONTENT="load-module module-native-protocol-unix socket=/tmp/runtime-appuser/pulse/native auth-anonymous=1
load-module module-null-sink sink_name=Virtual_Speaker sink_properties=device.description=Virtual_Speaker
load-module module-always-sink"

echo "$CONF_CONTENT" > /app/.config/pulse/default.pa
echo "$CONF_CONTENT" > /home/appuser/.config/pulse/default.pa
chown -R appuser:appuser /app/.config /home/appuser/.config

# 3. Настраиваем client.conf
CLIENT_CONF="default-server = unix:/tmp/runtime-appuser/pulse/native
autospawn = no"
echo "$CLIENT_CONF" > /app/.config/pulse/client.conf
echo "$CLIENT_CONF" > /home/appuser/.config/pulse/client.conf

# 4. Настраиваем daemon.conf
DAEMON_CONF="exit-idle-time = -1
enable-shm = no
allow-module-loading = yes
flat-volumes = no
use-pid-file = no"
echo "$DAEMON_CONF" > /app/.config/pulse/daemon.conf
echo "$DAEMON_CONF" > /home/appuser/.config/pulse/daemon.conf

log "Конфигурация PulseAudio создана (minimal, no hardware)."

# 5. Запуск
# Используем конфиг из /home/appuser, раз он так хочет туда лезть
log "Попытка запуска pulseaudio..."

# Запускаем в фоне, но пишем stdout/stderr в файл для анализа
gosu appuser dbus-run-session -- pulseaudio --verbose --log-target=file:/workspace/logs/pulseaudio.log --file=/home/appuser/.config/pulse/default.pa --exit-idle-time=-1 &
PA_PID=$!

sleep 5

# Проверяем, жив ли процесс
if kill -0 $PA_PID 2>/dev/null; then
    log "✅ PulseAudio процесс жив (PID $PA_PID)."
    if gosu appuser pactl info >/dev/null 2>&1; then
        log "✅ pactl info работает."
    else
        log "⚠️ Процесс жив, но pactl не отвечает. Проверьте логи."
    fi
else
    log "❌ ОШИБКА: PulseAudio процесс умер сразу."
    log "=== ПОСЛЕДНИЕ ЛОГИ PULSEAUDIO ==="
    cat /workspace/logs/pulseaudio.log || echo "Логов нет"
    log "================================="
    log "⚠️ Продолжаем загрузку без звука..."
fi




# --- 4. Запуск Inference Service (от имени appuser) ---
log "Запуск Inference Service..."
touch /workspace/logs/inference_service.log
chown appuser:appuser /workspace/logs/inference_service.log

# Запускаем в фоне через gosu
cd /app
gosu appuser uvicorn server.inference_service:app --host 0.0.0.0 --port 8000 --log-level info > /workspace/logs/inference_service.log 2>&1 &
INFERENCE_PID=$!

log "Ожидание запуска Inference Service (порт 8000)..."
MAX_RETRIES=150 
for ((i=1;i<=MAX_RETRIES;i++)); do
    # Проверка health endpoint
    if curl -s http://127.0.0.1:8000/health | grep -q "ok"; then
        log "✅ Inference Service готов (попытка $i)!"
        break
    fi
    
    # Проверка, жив ли процесс
    if ! kill -0 $INFERENCE_PID 2>/dev/null; then
        log "❌ Inference Service упал! Логи:"
        cat /workspace/logs/inference_service.log
        exit 1
    fi
    
    if [ $((i % 5)) -eq 0 ]; then
        log "⏳ Ожидание... ($i/$MAX_RETRIES)"
    fi
    sleep 2
done

if (( i > MAX_RETRIES )); then
    log "❌ Таймаут ожидания Inference Service."
    kill $INFERENCE_PID
    exit 1
fi

# --- 5. Запуск основного приложения ---
log "=== Запуск основного приложения (appuser) ==="
log "Команда: $@"

# Передаем управление appuser для выполнения CMD из Dockerfile
exec gosu appuser "$@"
