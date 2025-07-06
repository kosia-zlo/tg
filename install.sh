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
echo "Установка VPN-бота (TG-Bot-OpenVPN-Antizapret) v2.8.6.1 (для OpenVPN)"
echo "=============================================="
echo

### 1) Установка системных пакетов (git, wget, curl, python3-venv, python3-pip, easy-rsa, openvpn)
echo "=== Шаг 1: Установка системных пакетов ==="
apt update -qq

REQUIRED_PKG=("git" "wget" "curl" "python3-venv" "python3-pip" "easy-rsa" "openvpn")
for pkg in "${REQUIRED_PKG[@]}"; do
  if ! dpkg -s "$pkg" &>/dev/null; then
    echo "  • Устанавливаем: $pkg"
    apt install -y "$pkg"
  else
    echo "  • $pkg уже установлен — пропуск."
  fi
done

echo

### 2) Копирование easy-rsa в /etc/openvpn/easyrsa3 и инициализация PKI
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
  exit 1
fi

# Инициализация PKI для OpenVPN
echo "  Проверяем и, если нужно, инициализируем PKI для OpenVPN..."
cd "$EASY_DST"

# НОВОЕ: Проверяем, существует ли уже PKI, и если да, то пропускаем создание CA и ключей сервера.
# Эта проверка ПЕРЕД удалением 'pki'.
if [ -d "pki" ] && [ -f "pki/ca.crt" ] && [ -f "pki/issued/server.crt" ] && [ -f "pki/private/server.key" ]; then
  echo "  PKI уже инициализировано (найдены ca.crt, server.crt, server.key). Пропускаем генерацию CA и сервера."
  # Убедимся, что vars файл все равно создан, если его нет (хотя easy-rsa init-pki обычно его делает)
  if [ ! -f "$EASY_DST/vars" ]; then
    echo "  Создаем файл 'vars' для easy-rsa (так как он отсутствует, но PKI уже есть)..."
    cat > "$EASY_DST/vars" <<EOF
set_var EASYRSA_ALGO "rsa"
set_var EASYRSA_DIGEST "sha256"
set_var EASYRSA_CA_EXPIRE 3650
set_var EASYRSA_CERT_EXPIRE 365
set_var EASYRSA_REQ_COUNTRY    "RU"
set_var EASYRSA_REQ_PROVINCE   "Moscow"
set_var EASYRSA_REQ_CITY       "Moscow"
set_var EASYRSA_REQ_ORG        "VPN-Service"
set_var EASYRSA_REQ_EMAIL      "vpn@example.com"
set_var EASYRSA_REQ_OU         "Antizapret"
EOF
    chmod 600 "$EASY_DST/vars" # Устанавливаем права для vars
  fi
  export EASYRSA_VARS="$EASY_DST/vars" # Убедимся, что переменная установлена
else
  echo "  PKI не найдено или неполное. Выполняем инициализацию и генерацию CA/серверных ключей..."
  # Удаляем старый PKI, если есть, только если мы собираемся его создавать заново.
  if [ -d "pki" ]; then
    echo "  Обнаружен существующий PKI, удаляем (для чистой установки)..."
    rm -rf pki
  fi

  # Создаем минимальный файл vars для easy-rsa
  echo "  Создаем файл 'vars' для easy-rsa..."
  cat > "$EASY_DST/vars" <<EOF
set_var EASYRSA_ALGO "rsa"
set_var EASYRSA_DIGEST "sha256"
set_var EASYRSA_CA_EXPIRE 3650
set_var EASYRSA_CERT_EXPIRE 365
set_var EASYRSA_REQ_COUNTRY    "RU"
set_var EASYRSA_REQ_PROVINCE   "Moscow"
set_var EASYRSA_REQ_CITY       "Moscow"
set_var EASYRSA_REQ_ORG        "VPN-Service"
set_var EASYRSA_REQ_EMAIL      "vpn@example.com"
set_var EASYRSA_REQ_OU         "Antizapret"
EOF
  chmod 600 "$EASY_DST/vars" # Устанавливаем права для vars

  # Указываем easyrsa, где находится файл vars
  export EASYRSA_VARS="$EASY_DST/vars"

  # Инициализируем PKI
  ./easyrsa init-pki

  # Строим CA - используем --batch для автоматического ответа
  echo "  Генерация CA (Certificate Authority)..."
  ./easyrsa --batch build-ca nopass

  # Генерируем ключ сервера (без пароля)
  echo "  Генерация ключа сервера OpenVPN..."
  ./easyrsa --batch gen-req server nopass
  ./easyrsa --batch sign-req server server

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
fi # Конец блока if/else для инициализации PKI

echo
# ... (остальная часть вашего скрипта) ...
