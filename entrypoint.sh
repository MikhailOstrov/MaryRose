#!/bin/bash
set -e

echo "=== [Entrypoint] Start (User: $(whoami)) ==="

# 1. Запуск SSH
echo "[Entrypoint] Starting SSH..."
mkdir -p /run/sshd
ssh-keygen -A
/usr/sbin/sshd
echo "✅ SSH started."

# 2. Настройка прав (на всякий случай, хоть мы и root)
# Chrome не любит работать от root без флага --no-sandbox, но он у нас есть в коде.
# Также нужно убедиться, что Chrome может писать в свои папки.
mkdir -p /app/chrome_profile
chmod 777 /app/chrome_profile

export TORCH_HOME=/workspace/.cache/torch
export NEMO_CACHE_DIR=/workspace/.cache/nemo
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs
export PYTHONPATH=/app
# Для системного PulseAudio
export PULSE_SERVER=unix:/var/run/pulse/native

# 3. Запуск PulseAudio (System Mode)
echo "[Entrypoint] Starting PulseAudio (System Mode)..."
# Удаляем старые сокеты, если есть
rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse
pulseaudio --system --daemonize --log-target=stderr --disallow-exit --disallow-module-loading=0
sleep 2

if ! pgrep pulseaudio >/dev/null; then
    echo "❌ PulseAudio failed to start."
    exit 1
fi
echo "✅ PulseAudio is running (PID: $(pgrep pulseaudio))."

# Даем всем доступ к сокету PulseAudio (на всякий случай)
chmod -R 777 /var/run/pulse

# 4. Запуск Inference Service (от ROOT)
echo "[Entrypoint] Starting Inference Service..."
mkdir -p /workspace/logs
touch /workspace/logs/inference_service.log

# Запускаем в фоне
cd /app
python3.11 -m uvicorn server.inference_service:app --host 0.0.0.0 --port 8000 --log-level info &
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

# 5. Запуск основного приложения (от ROOT)
echo "=== [Entrypoint] Starting Main App ==="
# Просто запускаем команду как есть (от root)
exec "$@"
