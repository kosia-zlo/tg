import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from antizapret.db import Database
from antizapret.wg_manager import WireGuardManager # Ваш wg_manager

# --- Загрузка переменных окружения ---
# Убедитесь, что переменные окружения загружены до этого момента (например, через systemd Unit)
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
FILEVPN_NAME = os.getenv('FILEVPN_NAME')
MAX_USER_CONFIGS = int(os.getenv('MAX_USER_CONFIGS', 3)) # По умолчанию 3, если не установлено

# --- Инициализация ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

db = Database('vpn_bot.db') # Инициализируем объект базы данных. Файл vpn_bot.db будет создан в /root/

# Инициализация wg_manager с путями и именем файла VPN из .env
# Если у вас OpenVPN, пути будут другими и нужен другой менеджер
wg_manager = WireGuardManager(
    easyrsa_path='/etc/openvpn/easyrsa3', # Для OpenVPN
    server_config_dir='/etc/openvpn/server', # Для OpenVPN
    filevpn_name=FILEVPN_NAME # Используется для генерации имени клиента
)

# --- FSM States ---
class ConfigCreationStates(StatesGroup):
    waiting_for_config_name = State() # Состояние для ожидания имени нового конфига

# --- Вспомогательные функции ---
def generate_client_name(username, user_id):
    """
    Генерирует уникальное имя для клиента WireGuard.
    Использует FILEVPN_NAME из переменных окружения, имя пользователя и часть timestamp.
    Это имя будет использоваться внутренне (для ключей WireGuard и в базе данных).
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    # Ограничиваем длину имени пользователя, если оно слишком длинное для имени файла/ключа
    clean_username = "".join(c for c in username if c.isalnum() or c in ('-', '_')).strip()
    if len(clean_username) > 15:
        clean_username = clean_username[:15]
    return f"{FILEVPN_NAME}_{clean_username}_{user_id}_{timestamp}"[:50] # Ограничим общую длину

async def send_config_to_user(message: types.Message, client_custom_name: str, config_content: str):
    """
    Отправляет сгенерированный конфиг пользователю.
    client_custom_name - это имя, которое пользователь дал конфигу.
    """
    file_path = f"/tmp/{client_custom_name}.conf"
    try:
        with open(file_path, "w") as f:
            f.write(config_content)

        with open(file_path, "rb") as f:
            await message.answer_document(f, caption=f"Ваш новый конфигурационный файл для устройства **{client_custom_name}**:\n\n", parse_mode="Markdown")
            await message.answer("Скопируйте содержимое файла в приложение OpenVPN (или WireGuard, если вы его используете) или скачайте его.")

        os.remove(file_path)
        logging.info(f"Config {client_custom_name} sent and temp file removed for user {message.from_user.id}")
        return True
    except Exception as e:
        logging.error(f"Error sending config {client_custom_name} to user {message.from_user.id}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return False

# --- Обработчики команд ---

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else message.from_user.first_name
    db.add_user(user_id, username) # Добавляем пользователя в БД, если его нет

    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("Создать VPN-конфиг", callback_data="generate_config"),
        InlineKeyboardButton("Мои конфиги", callback_data="my_configs"),
        InlineKeyboardButton("Как подключиться?", callback_data="how_to_connect"),
        InlineKeyboardButton("Поддержать проект", callback_data="donate")
    )
    await message.answer(
        f"Привет, **{username}**!\n"
        "Этот бот поможет вам создать и управлять VPN-конфигурациями.",
        reply_markup=keyboard, parse_mode="Markdown"
    )
    logging.info(f"User {user_id} started bot.")

@dp.callback_query_handler(text="generate_config")
async def generate_config_entry_point(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    
    # Проверяем лимит активных конфигов
    user_configs_count = db.get_user_configs_count(user_id)
    if user_configs_count >= MAX_USER_CONFIGS: # Используем MAX_USER_CONFIGS из .env
        await call.message.answer(
            f"Вы достигли лимита в {MAX_USER_CONFIGS} активных конфигураций. "
            "Для создания новой, пожалуйста, удалите одну из существующих через 'Мои конфиги'."
        )
        await call.answer()
        return
    
    # Если лимит не превышен, запрашиваем имя для конфига
    await call.message.answer("Введите название для нового VPN-конфига (например, 'Мой телефон', 'Ноутбук'):")
    await ConfigCreationStates.waiting_for_config_name.set() # Переходим в состояние ожидания имени
    await call.answer()
    logging.info(f"User {user_id} prompted for config name.")


@dp.message_handler(state=ConfigCreationStates.waiting_for_config_name)
async def process_config_name(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else message.from_user.first_name
    client_custom_name = message.text.strip() # Пользовательское имя для конфига

    if not client_custom_name:
        await message.answer("Название конфига не может быть пустым. Пожалуйста, введите название:")
        return

    # Проверяем, существует ли уже конфиг с таким ПОЛЬЗОВАТЕЛЬСКИМ именем у этого пользователя
    existing_clients = db.get_user_clients(user_id)
    if any(c['client_name'].lower() == client_custom_name.lower() for c in existing_clients if c['is_active']):
        await message.answer("Конфиг с таким названием уже существует. Пожалуйста, выберите другое название:")
        await state.reset_state(with_data=False) # Сбрасываем состояние, чтобы пользователь мог начать заново
        return

    # Проверяем лимит еще раз на случай гонки
    user_configs_count = db.get_user_configs_count(user_id)
    if user_configs_count >= MAX_USER_CONFIGS:
        await message.answer(
            f"Вы достигли лимита в {MAX_USER_CONFIGS} активных конфигураций. "
            "Для создания новой, пожалуйста, удалите одну из существующих через 'Мои конфиги'."
        )
        await state.finish()
        return

    # Генерируем уникальное имя для WireGuard на основе пользовательского имени и ID
    # Это имя будет использоваться в файлах OpenVPN и в базе данных для WireGuard
    wg_client_name = generate_client_name(username, user_id)
    
    await message.answer(f"Создаю VPN-конфиг с именем: **{client_custom_name}**...", parse_mode="Markdown")

    try:
        private_key, public_key = wg_manager.generate_key_pair()
        
        # Добавляем клиента в WireGuard (используем wg_client_name как идентификатор)
        # client_ip будет возвращен и использован в конфиге
        client_ip = wg_manager.add_client(wg_client_name, public_key)
        
        # Сохраняем в базу данных пользовательское имя и публичный ключ (который используется в WG)
        db.add_client(user_id, client_custom_name, public_key) 

        config_content = wg_manager.generate_client_config(private_key, public_key, client_ip)

        success = await send_config_to_user(message, client_custom_name, config_content)
        if success:
            await message.answer("Готово! Ваш новый VPN-конфиг создан.")
        else:
            await message.answer("Произошла ошибка при отправке конфига.")
    except Exception as e:
        logging.error(f"Error generating or sending config for user {user_id} with name {client_custom_name}: {e}")
        await message.answer("Произошла ошибка при создании конфига. Пожалуйста, попробуйте позже.")

    await state.finish()
    logging.info(f"Config creation finished for user {user_id}.")


@dp.callback_query_handler(text="my_configs")
async def my_configs_command(call: CallbackQuery):
    user_id = call.from_user.id
    clients = db.get_user_clients(user_id)

    active_clients = [c for c in clients if c['is_active']]

    if not active_clients:
        await call.message.answer("У вас пока нет активных конфигурационных файлов.")
        await call.answer()
        return

    text = "Ваши активные конфигурационные файлы:\n\n"
    keyboard = InlineKeyboardMarkup(row_width=1)

    for client in active_clients:
        # client['client_name'] - это то, что ввёл пользователь
        text += f"▪️ **{client['client_name']}**\n"
        keyboard.add(InlineKeyboardButton(f"Удалить {client['client_name']}", callback_data=f"delete_config_{client['id']}"))

    await call.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    await call.answer()
    logging.info(f"User {user_id} requested my_configs.")


@dp.callback_query_handler(text_startswith="delete_config_")
async def delete_config_callback(call: CallbackQuery):
    client_id = int(call.data.split('_')[2])
    user_id = call.from_user.id

    client_data = db.get_client_by_id(client_id)

    if not client_data or client_data['user_id'] != user_id:
        await call.answer("Ошибка: Конфигурация не найдена или принадлежит другому пользователю.", show_alert=True)
        logging.warning(f"User {user_id} tried to delete config {client_id} belonging to another user or non-existent.")
        return

    if not client_data['is_active']:
        await call.answer("Эта конфигурация уже неактивна.", show_alert=True)
        logging.warning(f"User {user_id} tried to delete already inactive config {client_id}.")
        return

    # Деактивируем конфиг в базе данных
    if db.set_client_inactive(client_id):
        # Удаляем клиента из WireGuard по его public_key
        try:
            wg_manager.remove_client(client_data['public_key'])
            logging.info(f"Client {client_data['client_name']} (WG: {client_data['public_key']}) removed from WireGuard.")
        except Exception as e:
            logging.error(f"Error removing client {client_data['client_name']} from WireGuard: {e}")
            await call.message.answer("Внимание: Конфигурация удалена из базы, но не удалось удалить из WireGuard. Обратитесь к администратору.")


        await call.message.answer(f"Конфигурация **{client_data['client_name']}** успешно удалена.", parse_mode="Markdown")
    else:
        await call.message.answer("Произошла ошибка при удалении конфигурации.")
        logging.error(f"Error setting client {client_id} inactive in DB for user {user_id}.")
    
    await call.answer()
    
    # Обновим сообщение с конфигами
    active_clients_after_deletion = [c for c in db.get_user_clients(user_id) if c['is_active']]
    if active_clients_after_deletion:
        # Редактируем предыдущее сообщение, если возможно, или отправляем новое
        try:
            text = "Ваши активные конфигурационные файлы:\n\n"
            keyboard = InlineKeyboardMarkup(row_width=1)
            for client in active_clients_after_deletion:
                text += f"▪️ **{client['client_name']}**\n"
                keyboard.add(InlineKeyboardButton(f"Удалить {client['client_name']}", callback_data=f"delete_config_{client['id']}"))
            await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        except Exception as e:
            # Если сообщение не может быть отредактировано (например, слишком старое), отправляем новое
            logging.warning(f"Could not edit message for user {user_id} after deletion: {e}. Sending new one.")
            await call.message.answer("Ваши активные конфигурационные файлы (обновлено):", reply_markup=InlineKeyboardMarkup(row_width=1).add(
                *[InlineKeyboardButton(f"Удалить {c['client_name']}", callback_data=f"delete_config_{c['id']}") for c in active_clients_after_deletion]
            ), parse_mode="Markdown")
    else:
        await call.message.answer("У вас больше нет активных конфигурационных файлов.")


@dp.callback_query_handler(text="how_to_connect")
async def how_to_connect_handler(call: CallbackQuery):
    instructions = (
        "**Как подключиться к VPN:**\n\n"
        "1. **Загрузите приложение WireGuard:**\n" # Изменено на WireGuard
        "   - [Android](https://play.google.com/store/apps/details?id=com.wireguard.android)\n"
        "   - [iOS](https://apps.apple.com/us/app/wireguard/id1441195295)\n"
        "   - [Windows](https://download.wireguard.com/windows-client/wireguard-installer.exe)\n"
        "   - [macOS](https://itunes.apple.com/us/app/wireguard/id1451895079)\n"
        "2. **Импортируйте конфигурационный файл:**\n"
        "   - **На телефоне:** Откройте WireGuard, нажмите '+' или 'Добавить туннель', выберите 'Импортировать из файла' и укажите файл, который прислал бот.\n"
        "   - **На компьютере:** Откройте WireGuard, выберите 'Импорт файла' и укажите скачанный файл.\n"
        "3. **Подключитесь:** После импорта активируйте туннель в приложении WireGuard."
    )
    await call.message.answer(instructions, parse_mode="Markdown", disable_web_page_preview=True)
    await call.answer()

@dp.callback_query_handler(text="donate")
async def donate_handler(call: CallbackQuery):
    await call.message.answer(
        "Спасибо за вашу поддержку! Вы можете отправить донат в TON на адрес:\n"
        "`ВАШ_TON_АДРЕС`\n" # Замените на ваш реальный TON-адрес
    )
    await call.answer()

# --- Запуск бота ---
if __name__ == '__main__':
    logging.info("Bot started.")
    executor.start_polling(dp, skip_updates=True)
