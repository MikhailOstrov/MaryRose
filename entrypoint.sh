#!/bin/bash
set -e

echo "=== [Entrypoint] Start (User: $(whoami)) ==="

# 1. Запуск SSH (как root)
echo "[Entrypoint] Starting SSH..."
mkdir -p /run/sshd
ssh-keygen -A
/usr/sbin/sshd
echo "✅ SSH started."

# 2. Подготовка папок и прав
echo "[Entrypoint] Fixing permissions..."
mkdir -p /app/chrome_profile /workspace/logs /tmp/runtime-appuser
chmod 777 /app/chrome_profile /workspace/logs
chmod 0700 /tmp/runtime-appuser
chown appuser:appuser /tmp/runtime-appuser

# Чистим старые сокеты
rm -rf /tmp/pulse-socket

# 3. Запуск PulseAudio (USER MODE, as appuser)
# Мы не используем системный режим. Мы запускаем как юзер, но с кастомным сокетом в /tmp.
echo "[Entrypoint] Starting PulseAudio (User Mode as appuser)..."

# Создаем конфиг для анонимного доступа (unix socket в /tmp/pulse-socket)
# Это позволит и руту, и appuser'у подключаться к нему.
cat > /tmp/default.pa <<EOF
load-module module-native-protocol-unix auth-anonymous=1 socket=/tmp/pulse-socket
load-module module-null-sink sink_name=auto_null
set-default-sink auto_null
EOF

chown appuser:appuser /tmp/default.pa

# Запускаем от имени appuser
runuser -u appuser -- pulseaudio --start --log-target=stderr --exit-idle-time=-1 --file=/tmp/default.pa -vvvv
sleep 2

# 4. Настройка окружения для ВСЕХ (и root, и appuser)
# Теперь любой процесс будет знать, где искать PulseAudio
export PULSE_SERVER=unix:/tmp/pulse-socket
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs
export PYTHONPATH=/app

# Проверяем (от root)
echo "Testing PulseAudio connection..."
if ! pactl info >/dev/null 2>&1; then
    echo "❌ PulseAudio check failed. Logs:"
    cat /workspace/logs/pulseaudio.log 2>/dev/null || true
    exit 1
fi
echo "✅ PulseAudio is active (Socket: /tmp/pulse-socket)"

# Даем всем права на сокет (на всякий случай)
chmod 777 /tmp/pulse-socket

# 5. Запуск Inference Service (от ROOT)
# Ему не нужен звук, ему нужна GPU. Root - это ок.
echo "[Entrypoint] Starting Inference Service..."
touch /workspace/logs/inference_service.log
cd /app
python3.11 -m uvicorn server.inference_service:app --host 0.0.0.0 --port 8000 --log-level info &
INFERENCE_PID=$!

MAX_RETRIES=150
echo "Waiting for Inference Service..."
for ((i=1;i<=MAX_RETRIES;i++)); do
    if curl -s http://127.0.0.1:8000/health | grep -q "ok"; then
        echo "✅ Inference Service is ready!"
        break
    fi
    if ! kill -0 $INFERENCE_PID 2>/dev/null; then
        echo "❌ Inference Service died."
        exit 1
    fi
    sleep 2
done

# 6. Запуск основного приложения (от ROOT)
# Chrome с флагом --no-sandbox работает от рута.
echo "=== [Entrypoint] Starting Main App ==="
exec "$@"
