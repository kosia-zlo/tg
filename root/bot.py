# Импорты (оставьте их как есть, здесь только пример, что они должны быть)
import logging
import sqlite3
import asyncio
import os
import subprocess
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.deep_linking import create_start_link
from aiogram.utils.formatting import (
    Bold,
    Text,
)


# Загрузка переменных окружения (это часть вашего install.sh)
from dotenv import load_dotenv
load_dotenv(dotenv_path="/root/.env")

# Настройки бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
FILEVPN_NAME = os.getenv("FILEVPN_NAME")
MAX_USER_CONFIGS = int(os.getenv("MAX_USER_CONFIGS", 3))

# YOUR_SITE должен быть просто доменным именем без "https://"
YOUR_SITE = "kosia-zlo.github.io/mysite/index.html"


# Константы
DB_PATH = "/root/antizapret/db.sqlite" # Путь к вашей базе данных
CONFIGS_DIR = "/root/antizapret/client/openvpn/vpn"
EASYRSA_PATH = "/etc/openvpn/easyrsa3" # Путь к Easy-RSA
CLIENT_SH_PATH = "/root/antizapret/client.sh" # Путь к client.sh
SERVER_OPENVPN_CONF = "/etc/openvpn/server/server.conf" # Основной конфиг OpenVPN сервера

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()

# ... (Остальная часть файла bot.py остается без изменений) ...

# Определение состояний FSM
class UserStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_config_name = State()
    waiting_for_admin_config_name = State()
    waiting_for_invoice_amount = State()

# Функция для подключения к БД
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Инициализация базы данных
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            chat_id INTEGER UNIQUE NOT NULL,
            reg_date TEXT,
            configs_count INTEGER DEFAULT 0,
            admin_status INTEGER DEFAULT 0,
            ban_status INTEGER DEFAULT 0,
            balance REAL DEFAULT 0.0,
            last_activity TEXT,
            inviter_id INTEGER,
            last_payment TEXT,
            next_payment TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            generation_date TEXT,
            expiry_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    conn.commit()
    conn.close()

# Выполнение команды оболочки
async def execute_command(command, *args):
    full_command = [command] + list(args)
    logger.info(f"===[DEBUG EXEC]===")
    logger.info(f"COMMAND: {' '.join(full_command)}")
    process = await asyncio.create_subprocess_exec(
        *full_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=EASYRSA_PATH # Убедитесь, что эта директория существует и корректна
    )
    stdout, stderr = await process.communicate()
    logger.info(f"RET: {process.returncode}")
    logger.info(f"STDOUT: {stdout.decode().strip()}")
    logger.info(f"STDERR: {stderr.decode().strip()}")
    logger.info(f"===[END DEBUG]===")
    return process.returncode, stdout.decode().strip(), stderr.decode().strip()

# =========================================================================
# Раздел для пользовательских функций (User)
# =========================================================================

# Функция для получения количества конфигураций пользователя
def get_user_configs_count(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM configs WHERE user_id = ?", (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

# Функция создания главного меню для пользователя
def get_user_main_menu(user_id):
    configs_count = get_user_configs_count(user_id) # Получаем количество конфигураций
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔑 Мои VPN-конфигурации", callback_data="my_configs"),
            ],
            [
                # Добавляем кнопку с количеством конфигураций
                InlineKeyboardButton(text=f"📊 Конфигураций: {configs_count}/{MAX_USER_CONFIGS}", callback_data="view_config_count"),
            ],
            [
                InlineKeyboardButton(text="➕ Создать VPN-конфигурацию", callback_data="create_config"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Управление VPN", callback_data="manage_vpn"),
            ],
            [
                InlineKeyboardButton(text="💰 Баланс и пополнить", callback_data="balance_topup"),
            ],
            [
                InlineKeyboardButton(text="🔗 Наш сайт", url=f"https://{YOUR_SITE}"),
            ],
            [
                # Изменяем ссылку на поддержку
                InlineKeyboardButton(text="🙋‍♀️ Поддержка", url="https://t.me/krackqw"), 
            ],
        ]
    )
    return keyboard


# ... (Все остальные хендлеры и функции остаются без изменений) ...

# Пример хендлера /start
@router.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"id{user_id}"
    chat_id = message.chat.id
    reg_date = datetime.now(timezone.utc).isoformat()
    last_activity = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
    user_data = cursor.fetchone()

    if user_data is None:
        # Новый пользователь
        cursor.execute(
            """
            INSERT INTO users (id, username, chat_id, reg_date, configs_count, admin_status, ban_status, balance, last_activity, inviter_id, last_payment, next_payment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, chat_id, reg_date, 0, 0, 0, 0.0, last_activity, None, None, None)
        )
        conn.commit()
        await message.answer(
            f"Добро пожаловать в VPN-бот, {username}!\n\n"
            "Я помогу вам управлять вашими VPN-конфигурациями.\n"
            "Для начала создайте свою первую конфигурацию.",
            reply_markup=get_user_main_menu(user_id) # Теперь передаем user_id
        )
        logger.info(f"Новый пользователь зарегистрирован: {username} ({user_id})")
    else:
        # Существующий пользователь
        cursor.execute(
            "UPDATE users SET username = ?, last_activity = ? WHERE chat_id = ?",
            (username, last_activity, chat_id)
        )
        conn.commit()
        await message.answer(
            f"Снова здравствуйте, {username}!\n\n"
            "Ваше главное меню:",
            reply_markup=get_user_main_menu(user_id) # Теперь передаем user_id
        )
        logger.info(f"Пользователь вернулся: {username} ({user_id})")

    conn.close()

# Хендлер для новой кнопки "Конфигураций: X/Y"
@router.callback_query(F.data == "view_config_count")
async def handle_view_config_count(callback_query: Message):
    user_id = callback_query.from_user.id
    configs_count = get_user_configs_count(user_id)
    await callback_query.answer(f"У вас {configs_count} из {MAX_USER_CONFIGS} конфигураций.", show_alert=True)
    # Если вы хотите обновить сообщение с клавиатурой, используйте edit_message_reply_markup
    # await callback_query.message.edit_reply_markup(reply_markup=get_user_main_menu(user_id))


# ... (Все остальные хендлеры и функции) ...


# Запуск бота
async def main() -> None:
    init_db()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
