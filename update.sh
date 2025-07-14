#!/bin/bash

REPO_DIR="/root"  # Папка, где клонирован репозиторий

echo "🔄 Обновление кода Telegram-бота..."

cd "$REPO_DIR" || exit 1

# Сохраняем .env, если он есть
if [ -f .env ]; then
    cp .env .env.bak
    echo "✅ Резервная копия .env создана"
fi

# Обновляем репозиторий
git pull origin main || {
    echo "❌ Ошибка при обновлении из Git"
    exit 1
}

# Восстанавливаем .env, если был перезаписан
if [ -f .env.bak ]; then
    mv .env.bak .env
    echo "✅ .env восстановлен"
fi

# Перезапускаем сервис
echo "🔁 Перезапуск systemd-сервиса..."
systemctl restart vpnbot.service

echo "✅ Обновление завершено"
