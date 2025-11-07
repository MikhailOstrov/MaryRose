#!/bin/bash
set -e

echo "=== [Entrypoint] Подготовка окружения... ==="

# --- Настройка /workspace ---
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models /workspace/logs
export TORCH_HOME=/workspace/.cache/torch
export HF_HOME=/workspace/.cache/huggingface
export LOGS_DIR=/workspace/logs

# --- Очистка временных файлов PulseAudio ---
# Supervisor будет запускать его, но мы подстрахуемся
rm -rf /var/run/pulse /var/lib/pulse

# --- Настройка прав (на случай монтирования /workspace) ---
# Убедимся, что appuser может писать в /workspace
chown -R appuser:appuser /workspace

echo "✅ [Entrypoint] Окружение готово. Запускаем Supervisor..."
echo "======================================================"

# Запускаем Supervisor. Он будет в фоновом режиме управлять всеми службами.
exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf