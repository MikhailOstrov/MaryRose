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

# Чистим старые сокеты и конфиги
rm -rf /tmp/runtime-appuser/pulse
rm -rf /app/.config/pulse
mkdir -p /app/.config/pulse
chown -R appuser:appuser /app/.config

# ХАК: Настраиваем client.conf и daemon.conf
cat > /app/.config/pulse/client.conf <<EOF
default-server = unix:/tmp/runtime-appuser/pulse/native
autospawn = no
EOF

cat > /app/.config/pulse/daemon.conf <<EOF
exit-idle-time = -1
enable-shm = no
allow-module-loading = yes
flat-volumes = no
EOF
chown -R appuser:appuser /app/.config/pulse

log "Конфигурация PulseAudio обновлена (force null-sink, no-shm)."

# Запускаем PA через gosu с ЯВНОЙ загрузкой модулей
# Мы не надеемся на авто-обнаружение железа. Мы создаем виртуальный Null Sink.
log "Попытка запуска pulseaudio с модулем null-sink..."

# --start здесь часто вреден, запускаем как обычный процесс в фоне
# Используем dbus-run-session, чтобы у PA была своя шина D-Bus
gosu appuser dbus-run-session -- bash -c "pulseaudio --daemonize=yes --verbose --log-target=stderr --disallow-exit --exit-idle-time=-1 --system=false \
    --load='module-null-sink sink_name=Virtual_Speaker sink_properties=device.description=Virtual_Speaker' \
    --load='module-native-protocol-unix socket=/tmp/runtime-appuser/pulse/native auth-anonymous=1'"

sleep 2

# Проверка
if gosu appuser pactl info >/dev/null 2>&1; then
    log "✅ PulseAudio работает (Virtual Speaker создан)."
    # Проверим, есть ли sink
    gosu appuser pactl list sinks short
else
    log "❌ ОШИБКА: PulseAudio не отвечает. Логи выше."
    log "⚠️ Продолжаем загрузку без звука..."
fi



# --- 4. Запуск Inference Service (от имени appuser) ---
log "Запуск Inference Service..."
touch /workspace/logs/inference_service.log
chown appuser:appuser /workspace/logs/inference_service.log

# Запускаем в фоне через gosu
cd /app
gosu appuser uvicorn server.inference_service:app --host 0.0.0.0 --port 8000 --log-level info 2>&1 | tee /workspace/logs/inference_service.log &
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
