import logging
import os
import subprocess
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from db import Database # Импортируем класс Database напрямую из db.py

# --- Загрузка переменных окружения ---
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

# --- FSM States ---
class ConfigCreationStates(StatesGroup):
    waiting_for_config_name = State() # Состояние для ожидания имени нового конфига

# --- Вспомогательные функции ---
def generate_common_name(username, user_id):
    """
    Генерирует уникальный Common Name для OpenVPN клиента.
    Использует FILEVPN_NAME из переменных окружения, имя пользователя и часть timestamp.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    clean_username = "".join(c for c in username if c.isalnum() or c in ('-', '_')).strip()
    if len(clean_username) > 15:
        clean_username = clean_username[:15]
    return f"{FILEVPN_NAME}-{clean_username}-{user_id}-{timestamp}"[:64] # Common Name не должен быть слишком длинным

async def execute_client_sh(action: str, client_cn: str):
    """
    Выполняет скрипт client.sh для создания или удаления клиента OpenVPN.
    Args:
        action (str): 'create' или 'revoke'.
        client_cn (str): Common Name клиента.
    Returns:
        tuple: (bool success, str output/error_message)
    """
    cmd = ['bash', '/root/client.sh', action, client_cn]
    try:
        logging.info(f"Executing client.sh: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logging.info(f"client.sh output (action: {action}, CN: {client_cn}):\n{result.stdout}")
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        logging.error(f"client.sh failed (action: {action}, CN: {client_cn}):\n{e.stderr}")
        return False, e.stderr
    except FileNotFoundError:
        logging.error("client.sh not found at /root/client.sh. Please ensure it's copied and executable.")
        return False, "Скрипт client.sh не найден или не исполняем."


async def send_config_to_user(message: types.Message, client_custom_name: str, client_cn: str):
    """
    Отправляет сгенерированный OpenVPN конфиг пользователю.
    Args:
        message (types.Message): Объект сообщения Telegram.
        client_custom_name (str): Имя конфига, данное пользователем.
        client_cn (str): Common Name клиента (используется для поиска файла).
    Returns:
        bool: True, если конфиг успешно отправлен, False в противном случае.
    """
    # Предполагаем, что client.sh создает файл .ovpn в /root/client/
    file_path = f"/root/client/{client_cn}.ovpn"
    if not os.path.exists(file_path):
        logging.error(f"OpenVPN config file not found: {file_path} for CN: {client_cn}")
        await message.answer(f"Ошибка: Не удалось найти файл конфигурации для {client_custom_name}. Возможно, скрипт client.sh не создал его.")
        return False

    try:
        with open(file_path, "rb") as f:
            await message.answer_document(f, caption=f"Ваш новый конфигурационный файл для устройства **{client_custom_name}**:\n\n", parse_mode="Markdown")
            await message.answer("Скопируйте содержимое файла в приложение OpenVPN или скачайте его.")

        # Optionally remove the .ovpn file from the server after sending
        # os.remove(file_path)
        logging.info(f"Config {client_custom_name} ({client_cn}) sent to user {message.from_user.id}")
        return True
    except Exception as e:
        logging.error(f"Error sending config {client_custom_name} ({client_cn}) to user {message.from_user.id}: {e}")
        return False

# --- Обработчики команд ---

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else message.from_user.first_name
    db.add_user(user_id, username) # Используем db.add_user

    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("Создать VPN-конфиг", callback_data="generate_config"),
        # Кнопка "Мои конфиги" теперь добавлена здесь
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
    user_configs_count = db.get_user_configs_count(user_id) # Используем db.get_user_configs_count
    if user_configs_count >= MAX_USER_CONFIGS: # Используем MAX_USER_CONFIGS из .env
        await call.message.answer(
            f"Вы достигли лимита в **{MAX_USER_CONFIGS}** активных конфигураций. "
            "Для создания новой, пожалуйста, удалите одну из существующих через 'Мои конфиги'."
        )
        await call.answer()
        return
    
    await call.message.answer("Введите название для нового VPN-конфига (например, 'Мой телефон', 'Ноутбук'):")
    await ConfigCreationStates.waiting_for_config_name.set()
    await call.answer()
    logging.info(f"User {user_id} prompted for config name.")


@dp.message_handler(state=ConfigCreationStates.waiting_for_config_name)
async def process_config_name(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else message.from_user.first_name
    client_custom_name = message.text.strip()

    if not client_custom_name:
        await message.answer("Название конфига не может быть пустым. Пожалуйста, введите название:")
        return

    # Проверяем, существует ли уже конфиг с таким ПОЛЬЗОВАТЕЛЬСКИМ именем у этого пользователя
    existing_clients = db.get_user_clients(user_id) # Используем db.get_user_clients
    if any(c['client_name'].lower() == client_custom_name.lower() for c in existing_clients if c['is_active']):
        await message.answer("Конфиг с таким названием уже существует. Пожалуйста, выберите другое название:")
        await state.reset_state(with_data=False)
        return

    # Проверяем лимит еще раз на случай гонки
    user_configs_count = db.get_user_configs_count(user_id) # Используем db.get_user_configs_count
    if user_configs_count >= MAX_USER_CONFIGS:
        await message.answer(
            f"Вы достигли лимита в **{MAX_USER_CONFIGS}** активных конфигураций. "
            "Для создания новой, пожалуйста, удалите одну из существующих через 'Мои конфиги'."
        )
        await state.finish()
        return

    # Генерируем уникальный Common Name для OpenVPN
    client_cn = generate_common_name(username, user_id)
    
    await message.answer(f"Создаю VPN-конфиг с именем: **{client_custom_name}**...\n"
                         f"Идентификатор клиента (Common Name): `{client_cn}`", parse_mode="Markdown")

    try:
        # Вызываем client.sh для создания конфига
        success, output = await execute_client_sh('create', client_cn)

        if success:
            db.add_client(user_id, client_custom_name, client_cn) # Используем db.add_client с common_name
            await send_config_to_user(message, client_custom_name, client_cn)
            await message.answer("Готово! Ваш новый VPN-конфиг создан.")
        else:
            await message.answer(f"Произошла ошибка при создании конфига:\n`{output}`")
            logging.error(f"client.sh create failed for {client_cn}: {output}")
    except Exception as e:
        logging.error(f"Error during config creation process for user {user_id} with name {client_custom_name}: {e}")
        await message.answer("Произошла ошибка при создании конфига. Пожалуйста, попробуйте позже.")

    await state.finish()
    logging.info(f"Config creation finished for user {user_id}.")


@dp.callback_query_handler(text="my_configs")
async def my_configs_command(call: CallbackQuery):
    user_id = call.from_user.id
    clients = db.get_user_clients(user_id) # Используем db.get_user_clients

    active_clients = [c for c in clients if c['is_active']]

    if not active_clients:
        await call.message.answer("У вас пока нет активных конфигурационных файлов.")
        await call.answer()
        return

    text = "Ваши активные конфигурационные файлы:\n\n"
    keyboard = InlineKeyboardMarkup(row_width=1)

    for client in active_clients:
        text += f"▪️ **{client['client_name']}** (`{client['common_name']}`)\n" # Отображаем Common Name
        keyboard.add(InlineKeyboardButton(f"Удалить {client['client_name']}", callback_data=f"delete_config_{client['id']}"))

    await call.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    await call.answer()
    logging.info(f"User {user_id} requested my_configs.")


@dp.callback_query_handler(text_startswith="delete_config_")
async def delete_config_callback(call: CallbackQuery):
    client_id = int(call.data.split('_')[2])
    user_id = call.from_user.id

    client_data = db.get_client_by_id(client_id) # Используем db.get_client_by_id

    if not client_data or client_data['user_id'] != user_id:
        await call.answer("Ошибка: Конфигурация не найдена или принадлежит другому пользователю.", show_alert=True)
        logging.warning(f"User {user_id} tried to delete config {client_id} belonging to another user or non-existent.")
        return

    if not client_data['is_active']:
        await call.answer("Эта конфигурация уже неактивна.", show_alert=True)
        logging.warning(f"User {user_id} tried to delete already inactive config {client_id}.")
        return

    common_name = client_data['common_name'] # Получаем common_name для удаления
    await call.message.answer(f"Удаляю конфигурацию **{client_data['client_name']}** (`{common_name}`)...", parse_mode="Markdown")

    # Вызываем client.sh для отзыва сертификата
    success, output = await execute_client_sh('revoke', common_name)

    if success:
        if db.set_client_inactive(client_id): # Деактивируем в БД
            await call.message.answer(f"Конфигурация **{client_data['client_name']}** успешно удалена.", parse_mode="Markdown")
        else:
            await call.message.answer("Конфигурация удалена из OpenVPN, но произошла ошибка при обновлении в базе данных.")
            logging.error(f"Error setting client {client_id} inactive in DB for user {user_id} after client.sh revoke.")
    else:
        await call.message.answer(f"Произошла ошибка при удалении конфига из OpenVPN:\n`{output}`\n"
                                 "Конфигурация в базе данных не изменена.", parse_mode="Markdown")
        logging.error(f"client.sh revoke failed for {common_name}: {output}")
    
    await call.answer()
    
    # Обновим сообщение с конфигами
    active_clients_after_deletion = [c for c in db.get_user_clients(user_id) if c['is_active']] # Используем db.get_user_clients
    if active_clients_after_deletion:
        try:
            text = "Ваши активные конфигурационные файлы:\n\n"
            keyboard = InlineKeyboardMarkup(row_width=1)
            for client in active_clients_after_deletion:
                text += f"▪️ **{client['client_name']}** (`{client['common_name']}`)\n"
                keyboard.add(InlineKeyboardButton(f"Удалить {client['client_name']}", callback_data=f"delete_config_{client['id']}"))
            await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        except Exception as e:
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
        "1. **Загрузите приложение OpenVPN Connect:**\n"
        "   - [Android](https://play.google.com/store/apps/details?id=net.openvpn.openvpn&hl=ru)\n"
        "   - [iOS](https://apps.apple.com/us/app/openvpn-connect/id590379981)\n"
        "   - [Windows](https://openvpn.net/client-connect-vpn-for-windows/)\n"
        "   - [macOS](https://openvpn.net/client-connect-vpn-for-mac-os/)\n"
        "2. **Импортируйте конфигурационный файл:**\n"
        "   - **На телефоне:** Откройте OpenVPN Connect, нажмите '+' или 'Импорт', выберите 'Импорт файла' и укажите файл, который прислал бот.\n"
        "   - **На компьютере:** Откройте OpenVPN Connect, выберите 'Импорт файла' и укажите скачанный файл.\n"
        "3. **Подключитесь:** После импорта активируйте туннель в приложении OpenVPN Connect."
    )
    await call.message.answer(instructions, parse_mode="Markdown", disable_web_page_preview=True)
    await call.answer()

@dp.callback_query_handler(text="donate")
async def donate_handler(call: CallbackQuery):
    await call.message.answer(
        "Спасибо за вашу поддержку! Вы можете узнать больше о проекте и поддержать его здесь:\n"
        "`https://kosia-zlo.github.io/mysite/index.html`\n"
    )
    await call.answer()

# --- Запуск бота ---
if __name__ == '__main__':
    logging.info("Bot started.")
    executor.start_polling(dp, skip_updates=True)
