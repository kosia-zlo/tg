#!/bin/bash
#
# Установочный скрипт для VPN-бота (TG-Bot-OpenVPN-Antizapret)

set -e

### 0) Проверка, что скрипт запущен от root
if [ "$EUID" -ne 0 ]; then
  echo "Ошибка: скрипт нужно запускать от root."
  exit 1
fi

echo "=============================================="
echo "Установка VPN-бота (TG-Bot-OpenVPN-Antizapret) v2.8.6 (для OpenVPN)"
echo "=============================================="
echo

### 1) Установка системных пакетов (git, wget, curl, python3-venv, python3-pip, easy-rsa)
echo "=== Шаг 1: Установка системных пакетов ==="
apt update -qq

REQUIRED_PKG=("git" "wget" "curl" "python3-venv" "python3-pip" "easy-rsa" "openvpn") # Добавляем openvpn
for pkg in "${REQUIRED_PKG[@]}"; do
  if ! dpkg -s "$pkg" &>/dev/null; then
    echo "  • Устанавливаем: $pkg"
    apt install -y "$pkg"
  else
    echo "  • $pkg уже установлен — пропуск."
  fi
done

echo

### 2) Копирование easy-rsa в /etc/openvpn/easyrsa3
echo "=== Шаг 2: Настройка easy-rsa → /etc/openvpn/easyrsa3 ==="
EASY_SRC="/usr/share/easy-rsa"
EASY_DST="/etc/openvpn/easyrsa3"

if [ -d "$EASY_SRC" ]; then
  echo "  Копируем '$EASY_SRC' → '$EASY_DST'"
  mkdir -p "$EASY_DST"
  cp -r "$EASY_SRC/"* "$EASY_DST/"
  chmod -R 755 "$EASY_DST"
  echo "  easy-rsa скопирован."
else
  echo "  ⚠️  Папка '$EASY_SRC' не найдена, easy-rsa не установлен?"
fi

# Инициализация PKI для OpenVPN
echo "  Инициализация PKI для OpenVPN (если не инициализировано)..."
cd "$EASY_DST"
# Удаляем старый PKI, если есть, чтобы гарантировать чистую инициализацию
if [ -d "pki" ]; then
  rm -rf pki
fi
./easyrsa init-pki
# Строим CA - без пароля для удобства автоматизации (в продакшене лучше с паролем)
echo "  Генерация CA (Certificate Authority)..."
./easyrsa --batch build-ca nopass

# Генерируем ключ сервера (без пароля)
echo "  Генерация ключа сервера OpenVPN..."
./easyrsa gen-req server nopass
./easyrsa sign-req server server

# Генерируем параметры Диффи-Хеллмана
echo "  Генерация параметров Диффи-Хеллмана (может занять время)..."
./easyrsa gen-dh

# Копируем сгенерированные ключи и сертификаты в /etc/openvpn/server/
echo "  Копируем ключи и сертификаты OpenVPN в /etc/openvpn/server/..."
mkdir -p /etc/openvpn/server/
cp pki/ca.crt /etc/openvpn/server/
cp pki/private/server.key /etc/openvpn/server/
cp pki/issued/server.crt /etc/openvpn/server/
cp pki/dh.pem /etc/openvpn/server/dh.pem

# Создаем файл ta.key (HMAC Firewall)
echo "  Создание ta.key..."
openvpn --genkey secret /etc/openvpn/server/ta.key

echo "  Настройка easy-rsa завершена."
echo

### 3) Запрос BOT_TOKEN, ADMIN_ID, FILEVPN_NAME и MAX_USER_CONFIGS
echo "=== Шаг 3: Настройка параметров бота ==-"
read -p "Введите BOT_TOKEN (токен из BotFather): " BOT_TOKEN
BOT_TOKEN="$(echo "$BOT_TOKEN" | xargs)"
if [ -z "$BOT_TOKEN" ]; then
  echo "Ошибка: BOT_TOKEN не может быть пустым."
  exit 1
fi

read -p "Введите ADMIN_ID (Telegram User ID): " ADMIN_ID
ADMIN_ID="$(echo "$ADMIN_ID" | xargs)"
if [ -z "$ADMIN_ID" ]; then
  echo "Ошибка: ADMIN_ID не может быть пустым."
  exit 1
fi

echo
read -p "Введите название для VPN-файлов (FILEVPN_NAME), например: MyOVPN: " FILEVPN_NAME
FILEVPN_NAME="$(echo "$FILEVPN_NAME" | xargs)"
if [ -z "$FILEVPN_NAME" ]; then
  echo "Ошибка: FILEVPN_NAME не может быть пустым."
  exit 1
fi

echo
read -p "Введите максимальное количество конфигураций на одного пользователя (например, 3): " MAX_USER_CONFIGS
MAX_USER_CONFIGS="$(echo "$MAX_USER_CONFIGS" | xargs)"
if ! [[ "$MAX_USER_CONFIGS" =~ ^[0-9]+$ ]] || [ -z "$MAX_USER_CONFIGS" ]; then
  echo "Ошибка: MAX_USER_CONFIGS должен быть числом и не может быть пустым."
  exit 1
fi

# YOUR_SERVER_IP, YOUR_SERVER_OVPN_PORT, DNS_SERVERS больше не запрашиваются
# и будут управляться скриптами AntiZapret-VPN или использоваться из client.sh напрямую

echo
echo "Вы ввели:"
echo "  BOT_TOKEN          = \"$BOT_TOKEN\""
echo "  ADMIN_ID           = \"$ADMIN_ID\""
echo "  FILEVPN_NAME       = \"$FILEVPN_NAME\""
echo "  MAX_USER_CONFIGS   = \"$MAX_USER_CONFIGS\""
# YOUR_SERVER_IP, YOUR_SERVER_OVPN_PORT, DNS_SERVERS больше не выводятся

echo

### 4) Сохранение переменных в /root/.env (UTF-8 без BOM)
echo "=== Шаг 4: Запись переменных в /root/.env ==="
cat > "/root/.env" <<EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_ID=$ADMIN_ID
FILEVPN_NAME=$FILEVPN_NAME
MAX_USER_CONFIGS=$MAX_USER_CONFIGS
EOF
# Убедимся, что файл UTF-8:
iconv -f utf-8 -t utf-8 "/root/.env" -o "/root/.env.tmp" && mv "/root/.env.tmp" "/root/.env"
echo "  Файл /root/.env записан (UTF-8)."
echo

### 5) Клонирование репозитория во временную папку
TMP_DIR="/tmp/antizapret-install"
GIT_URL="https://github.com/VATAKATru61/TG-Bot-OpenVPN-Antizapret.git" # Ваш репозиторий
GIT_URL_CLIENT_SH="https://github.com/GubernievS/AntiZapret-VPN.git" # Репозиторий client.sh
BRANCH="main"

if [ -d "$TMP_DIR" ]; then
  echo "Удаляем старую временную папку $TMP_DIR"
  rm -rf "$TMP_DIR"
fi

echo "=== Шаг 5: Клонируем основной репозиторий в $TMP_DIR ==="
git clone "$GIT_URL" "$TMP_DIR"
cd "$TMP_DIR"
git checkout "$BRANCH"

echo "Сбрасываем локальные изменения и подтягиваем origin/$BRANCH..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

# Клонируем репозиторий с client.sh в подпапку
echo "Клонируем репозиторий client.sh в $TMP_DIR/client_sh_repo..."
mkdir -p "$TMP_DIR/client_sh_repo"
git clone "$GIT_URL_CLIENT_SH" "$TMP_DIR/client_sh_repo"
echo

### 6) Копирование подпапок в целевые директории (перезапись без удаления остального)
echo "=== Шаг 6: Копирование файлов из временного клона ==-"

# 6.1) antizapret → /root/antizapret
SRC_ANTIZAPRET="$TMP_DIR/antizapret"
DST_ANTIZAPRET="/root/antizapret"
if [ -d "$SRC_ANTIZAPRET" ]; then
  echo "  Копируем '$SRC_ANTIZAPRET' → '$DST_ANTIZAPRET'"
  mkdir -p "$DST_ANTIZAPRET"
  cp -r "$SRC_ANTIZAPRET/"* "$DST_ANTIZAPRET/"
  # Удаляем wg_manager.py, так как он не используется с client.sh
  rm -f "$DST_ANTIZAPRET/wg_manager.py"
else
  echo "  ⚠️  Папка '$SRC_ANTIZAPRET' не найдена."
fi

# 6.2) etc/openvpn → /etc/openvpn
SRC_OPENVPN_REPO="$TMP_DIR/etc/openvpn"
DST_OPENVPN="/etc/openvpn"
if [ -d "$SRC_OPENVPN_REPO" ]; then
  echo "  Копируем конфиги OpenVPN из репо '$SRC_OPENVPN_REPO' → '$DST_OPENVPN'"
  mkdir -p "$DST_OPENVPN"
  # Копируем только те файлы, которые нужны из репо, чтобы не перезатереть ключи easy-rsa
  cp -r "$SRC_OPENVPN_REPO/"* "$DST_OPENVPN/"

  # 6.3) Копирование пользовательских серверных конфигов OpenVPN → /etc/openvpn/server
  echo "  Копируем серверные конфиги OpenVPN из репо → /etc/openvpn/server"
  mkdir -p /etc/openvpn/server
  # защита на случай отсутствия конфигов
  shopt -s nullglob
  for src in "$TMP_DIR/etc/openvpn/server/"*.conf; do
    cp "$src" /etc/openvpn/server/
  done
  shopt -u nullglob

  # Подставляем FILEVPN_NAME и выставляем права
  for f in /etc/openvpn/server/*.conf; do
    sed -i "s|\${FILEVPN_NAME}|${FILEVPN_NAME}|g" "$f" || true
    chmod 644 "$f"
    echo "    Настроен и права 644: $f"
  done
else
  echo "  ⚠️  Папка '$SRC_OPENVPN_REPO' не найдена."
fi

# 6.4) root → /root
SRC_ROOT="$TMP_DIR/root"
DST_ROOT="/root"
if [ -d "$SRC_ROOT" ]; then
  echo "  Копируем '$SRC_ROOT' → '$DST_ROOT'"
  # Используем rsync для более умного копирования и обновления существующих файлов
  rsync -av --exclude 'venv/' --exclude '.env' "$SRC_ROOT/" "$DST_ROOT/"
  # Удаляем bot.py и db.py из папки antizapret, если они там есть,
  # так как мы хотим использовать их из /root/ напрямую
  rm -f "$DST_ANTIZAPRET/bot.py" "$DST_ANTIZAPRET/db.py"
else
  echo "  ⚠️  Папка '$SRC_ROOT' не найдена."
fi

# 6.5) Копирование client.sh из GubernievS/AntiZapret-VPN
SRC_CLIENT_SH="$TMP_DIR/client_sh_repo/client.sh"
DST_CLIENT_SH="/root/client.sh"
if [ -f "$SRC_CLIENT_SH" ]; then
  echo "  Копируем client.sh → '$DST_CLIENT_SH'"
  cp "$SRC_CLIENT_SH" "$DST_CLIENT_SH"
  chmod +x "$DST_CLIENT_SH"
else
  echo "  ⚠️  client.sh не найден в '$SRC_CLIENT_SH'."
fi

echo "Копирование завершено."
echo

### 7) Замена переменных в файлах и приведение к UTF-8
echo "=== Шаг 7: Замена переменных и приведение к UTF-8 ==-"

# Функция для перекодирования в UTF-8
recode_to_utf8() {
  local file="$1"
  if [ -f "$file" ]; then
    iconv -f utf-8 -t utf-8 "$file" -o "${file}.tmp" && mv "${file}.tmp" "$file"
  fi
}

# 7.1) Заменяем в /root/antizapret (кроме подпапки client/openvpn/vpn, где лежат шаблоны конфигов)
grep -RIl --exclude-dir="client/openvpn/vpn" '\${FILEVPN_NAME}' /root/antizapret 2>/dev/null | while IFS= read -r f; do
  sed -i "s|\${FILEVPN_NAME}|${FILEVPN_NAME}|g" "$f"
  recode_to_utf8 "$f"
  echo "  Заменено \${FILEVPN_NAME} и UTF-8: $f"
done || true

grep -RIl --exclude-dir="client/openvpn/vpn" '\$FILEVPN_NAME' /root/antizapret 2>/dev/null | while IFS= read -r f; do
  sed -i "s|\$FILEVPN_NAME|${FILEVPN_NAME}|g" "$f"
  recode_to_utf8 "$f"
  echo "  Заменено \$FILEVPN_NAME и UTF-8: $f"
done || true

# 7.2) В /etc/openvpn
grep -RIl '\${FILEVPN_NAME}' /etc/openvpn 2>/dev/null | while IFS= read -r f; do
  sed -i "s|\${FILEVPN_NAME}|${FILEVPN_NAME}|g" "$f"
  recode_to_utf8 "$f"
  echo "  Заменено \${FILEVPN_NAME} и UTF-8: $f"
done || true

grep -RIl '\$FILEVPN_NAME' /etc/openvpn 2>/dev/null | while IFS= read -r f; do
  sed -i "s|\$FILEVPN_NAME|${FILEVPN_NAME}|g" "$f"
  recode_to_utf8 "$f"
  echo "  Заменено \$FILEVPN_NAME и UTF-8: $f"
done || true


# 7.3) В /root/bot.py и /root/client.sh
for f in /root/bot.py /root/client.sh; do
  if [ -f "$f" ]; then
    # Удаляем замену IP, порта и DNS в client.sh, так как это будет управляться AntiZapret-VPN
    if [ "$f" == "/root/client.sh" ]; then
        # Удаляем строки, которые пытаются заменить IP, PORT, DNS_SERVERS
        sed -i '/sed -i "s|YOUR_SERVER_IP|/d' "$f"
        sed -i '/sed -i "s|YOUR_SERVER_PORT|/d' "$f"
        sed -i '/sed -i "s|DNS_SERVERS|/d' "$f"
        sed -i '/^remote \$VPN_SERVER_IP \$VPN_SERVER_PORT/d' "$f" # Удаляем строку с remote, она должна быть в template
        sed -i '/^dhcp-option DNS/d' "$f" # Удаляем строки с dhcp-option, они должны быть в template или push'ится сервером
        echo "  Удалены замены IP, порта, DNS из $f"
    fi

    # Замена FILEVPN_NAME (только если шаблон есть в файле)
    if grep -q '\${FILEVPN_NAME}' "$f"; then
      sed -i "s|\${FILEVPN_NAME}|${FILEVPN_NAME}|g" "$f"
      recode_to_utf8 "$f"
      echo "  Заменено \${FILEVPN_NAME} и UTF-8: $f"
    fi
    if grep -q '\$FILEVPN_NAME' "$f"; then
      sed -i "s|\$FILEVPN_NAME|${FILEVPN_NAME}|g" "$f"
      recode_to_utf8 "$f"
      echo "  Заменено \$FILEVPN_NAME и UTF-8: $f"
    fi
  fi
done


echo

### 8) Принудительное пересоздание виртуального окружения и установка зависимостей
echo "=== Шаг 8: Пересоздание виртуального окружения и установка зависимостей ==="
VENV_DIR="/root/venv"

if [ -d "$VENV_DIR" ]; then
  echo "  Удаляем существующее виртуальное окружение: rm -rf $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

echo "  Создаём виртуальное окружение: python3 -m venv $VENV_DIR"
python3 -m venv "$VENV_DIR"

echo "  Активируем venv и устанавливаем зависимости из /root/requirements.txt"
source "$VENV_DIR/bin/activate"
if [ -f "/root/requirements.txt" ]; then
  pip install --upgrade pip
  pip install -r /root/requirements.txt
else
  echo "  ⚠️  /root/requirements.txt не найден — зависимости не установлены!"
fi
deactivate

echo

### 9) Даем всем скопированным файлам права 777
echo "=== Шаг 9: Полные права (777) всем скопированным файлам ==="
if [ -d "/root/antizapret" ]; then
  chmod -R 777 "/root/antizapret"
  echo "  Права 777 выставлены на /root/antizapret"
fi

if [ -d "/etc/openvpn" ]; then
  chmod -R 777 "/etc/openvpn"
  echo "  Права 777 выставлены на /etc/openvpn"
fi

if [ -d "$DST_ROOT" ]; then
  # Рекурсивно выставляем права для всех файлов, скопированных в /root/
  find "$DST_ROOT" -type f \( -path "$DST_ROOT/bot.py" -o -path "$DST_ROOT/requirements.txt" -o -path "$DST_ROOT/client.sh" -o -path "$DST_ROOT/db.py" \) -exec chmod 777 {} +
  echo "  Права 777 выставлены на основные файлы в /root/"
fi


echo

### 10) Создание systemd-юнита vpnbot.service
echo "=== Шаг 10: Создание systemd-юнита /etc/systemd/system/vpnbot.service ==-"
cat > /etc/systemd/system/vpnbot.service <<EOF
[Unit]
Description=VPN Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
EnvironmentFile=/root/.env
ExecStart=/root/venv/bin/python /root/bot.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo "  Юнит записан: /etc/systemd/system/vpnbot.service"
echo

### 11) Перезагрузка systemd, автозапуск, запуск службы
echo "=== Шаг 11: Перезагрузка systemd и запуск vpnbot.service ==-"
systemctl daemon-reload
systemctl enable vpnbot.service
systemctl restart vpnbot.service

echo

### 12) Итоговое сообщение и инструкции
echo "=============================================="
echo "Установка завершена! Бот запущен как vpnbot.service."
echo
echo "Команды для управления ботом:"
echo "  ● Проверить статус:     systemctl status vpnbot.service"
echo "  ● Перезапустить бота:   systemctl restart vpnbot.service"
echo "  ● Смотреть логи:        journalctl -u vpnbot -f"
echo
echo "Основные пути и параметры:"
echo "  ● /root/antizapret       — скопировано из репозитория antizapret/"
echo "  ● /etc/openvpn           — скопировано из репозитория etc/openvpn/"
echo "  ● /etc/openvpn/easyrsa3  — скопирован easy-rsa и инициализирован PKI"
echo "  ● /root                  — скопировано из репозитория root/ (bot.py, requirements.txt и т. д.)"
echo "  ● /root/client.sh        — скрипт для управления OpenVPN-клиентами"
echo "  ● Виртуальное окружение: /root/venv"
echo "  ● Файл с переменными:    /root/.env"
echo "        • BOT_TOKEN          = $BOT_TOKEN"
echo "        • ADMIN_ID           = $ADMIN_ID"
echo "        • FILEVPN_NAME        = $FILEVPN_NAME"
echo "        • MAX_USER_CONFIGS   = $MAX_USER_CONFIGS"
# YOUR_SERVER_IP, YOUR_SERVER_OVPN_PORT, DNS_SERVERS больше не выводятся
echo "=============================================="
