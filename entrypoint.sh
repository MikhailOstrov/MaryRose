#!/bin/bash
set -e

echo "=== [Entrypoint] Start (User: $(whoami)) ==="

# 1. Запуск SSH (мы root, нам можно)
echo "[Entrypoint] Starting SSH..."
mkdir -p /run/sshd
ssh-keygen -A
/usr/sbin/sshd
echo "✅ SSH started."

# 2. Подготовка прав для appuser
echo "[Entrypoint] Fixing permissions..."
chown -R appuser:appuser /workspace /app /tmp
mkdir -p /tmp/runtime-appuser
chown appuser:appuser /tmp/runtime-appuser
chmod 0700 /tmp/runtime-appuser

# Экспорт переменных для appuser (чтобы они были видны внутри runuser)
export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs
export PYTHONPATH=/app
export XDG_RUNTIME_DIR=/tmp/runtime-appuser

# 3. Запуск PulseAudio (от имени appuser)
echo "[Entrypoint] Starting PulseAudio (as appuser)..."
runuser -u appuser -- pulseaudio --start --log-target=stderr --exit-idle-time=-1
sleep 2

# Проверка PulseAudio
if ! runuser -u appuser -- pactl info >/dev/null 2>&1; then
    echo "❌ PulseAudio failed to start."
    exit 1
fi
echo "✅ PulseAudio is ready."

# 4. Запуск Inference Service (от имени appuser)
echo "[Entrypoint] Starting Inference Service..."
mkdir -p /workspace/logs
touch /workspace/logs/inference_service.log
chown appuser:appuser /workspace/logs/inference_service.log

# Запускаем в фоне от appuser
cd /app
runuser -u appuser -- python3.11 -m uvicorn server.inference_service:app --host 0.0.0.0 --port 8000 --log-level info &
INFERENCE_PID=$!

# Ждем запуска
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

# 5. Запуск основного приложения
echo "=== [Entrypoint] Starting Main App ==="
# Если команда по умолчанию (запуск сервера)
if [ "$1" = "uvicorn" ] && [ "$2" = "server.server:app" ]; then
    # Запускаем от appuser
    exec runuser -u appuser -- "$@"
else
    # Если передана другая команда (например bash), запускаем как есть (root)
    exec "$@"
fi
