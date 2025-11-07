#!/bin/bash
set -e

echo "=== [Entrypoint] Запуск от пользователя: $(whoami) ==="

# --- 1. Настройка /workspace (от root) ---
# Этот скрипт выполняется один раз перед запуском supervisord
echo "[Entrypoint] Проверка и настройка /workspace..."
mkdir -p /workspace/.cache/torch /workspace/.cache/nemo /workspace/.cache/huggingface /workspace/models /workspace/logs
echo "✅ [Entrypoint] /workspace настроен."

# --- 2. Настройка окружения для appuser ---
# Создаем директорию для runtime файлов PulseAudio и D-Bus
echo "[Entrypoint] Настройка XDG_RUNTIME_DIR для appuser..."
mkdir -p -m 0700 /tmp/runtime-appuser
chown appuser:appuser /tmp/runtime-appuser
echo "✅ [Entrypoint] XDG_RUNTIME_DIR настроен."

# --- 3. Запуск Supervisord ---
# Передаем управление supervisord, который будет управлять всеми остальными процессами
echo "=== [Entrypoint] Запуск Supervisord... ==="
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf