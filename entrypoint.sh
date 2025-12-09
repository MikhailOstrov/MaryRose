#!/bin/bash
set -e

echo "=== [Entrypoint] Start (User: $(whoami)) ==="

# 1. Запуск SSH
echo "[Entrypoint] Starting SSH..."
mkdir -p /run/sshd
ssh-keygen -A
/usr/sbin/sshd
echo "✅ SSH started."

# 2. Настройка прав
mkdir -p /app/chrome_profile
chmod 777 /app/chrome_profile

export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs
export PYTHONPATH=/app
export PULSE_SERVER=unix:/var/run/pulse/native

# 3. Запуск PulseAudio (System Mode с полным доступом)
echo "[Entrypoint] Starting PulseAudio (System Mode)..."

# Удаляем старое
rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse

# Создаем кастомный конфиг, разрешающий анонимный доступ
cat > /tmp/system.pa <<EOF
load-module module-device-restore
load-module module-stream-restore
load-module module-card-restore
load-module module-augment-properties
load-module module-always-sink
load-module module-native-protocol-unix auth-anonymous=1 socket=/var/run/pulse/native
load-module module-null-sink sink_name=auto_null
set-default-sink auto_null
EOF

# Запускаем с нашим конфигом
pulseaudio --system --daemonize --log-target=stderr --disallow-exit --disallow-module-loading=0 --file=/tmp/system.pa -vvvv
sleep 2

if ! pgrep pulseaudio >/dev/null; then
    echo "❌ PulseAudio failed to start."
    exit 1
fi
echo "✅ PulseAudio is running (PID: $(pgrep pulseaudio))."

# Даем права на сокет (хотя auth-anonymous должен помочь, но для надежности)
chmod 777 /var/run/pulse/native

# 4. Запуск Inference Service
echo "[Entrypoint] Starting Inference Service..."
mkdir -p /workspace/logs
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

# 5. Запуск основного приложения
echo "=== [Entrypoint] Starting Main App ==="
exec "$@"
