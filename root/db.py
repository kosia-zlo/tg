import sqlite3
import logging
from datetime import datetime

class Database:
    def __init__(self, db_file):
        self.db_file = db_file
        self.conn = None
        self.cursor = None
        self.connect()
        self.create_tables()

    def connect(self):
        try:
            self.conn = sqlite3.connect(self.db_file)
            self.cursor = self.conn.cursor()
            logging.info("Connected to database.")
        except sqlite3.Error as e:
            logging.error(f"Database connection error: {e}")

    def close(self):
        if self.conn:
            self.conn.close()
            logging.info("Database connection closed.")

    def create_tables(self):
        try:
            # Таблица пользователей с полями для будущих подписок
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    is_admin BOOLEAN DEFAULT FALSE,
                    subscribed BOOLEAN DEFAULT TRUE, -- По умолчанию все подписаны, пока нет оплаты
                    subscription_end_date TEXT -- Дата окончания подписки, пока не используется активно
                )
            ''')
            # Таблица клиентов (конфигураций)
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    client_name TEXT UNIQUE, -- Имя, которое вводит пользователь (например, "Мой телефон")
                    common_name TEXT UNIQUE, -- Common Name OpenVPN (используется для OpenVPN)
                    is_active BOOLEAN DEFAULT TRUE, -- Активен ли конфиг (можно удалить)
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, -- Дата создания
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')
            self.conn.commit()
            logging.info("Tables checked/created successfully.")
        except sqlite3.Error as e:
            logging.error(f"Error creating tables: {e}")

    def add_user(self, user_id, username):
        """Добавляет нового пользователя в базу данных или обновляет, если он уже существует."""
        try:
            self.cursor.execute("INSERT OR IGNORE INTO users (id, username, subscribed) VALUES (?, ?, ?)", (user_id, username, True))
            self.conn.commit()
            logging.info(f"User {user_id} added or already exists.")
            return True
        except sqlite3.Error as e:
            logging.error(f"Error adding user {user_id}: {e}")
            return False

    def get_user(self, user_id):
        """Получает информацию о пользователе по его ID."""
        try:
            self.cursor.execute("SELECT id, username, is_admin, subscribed, subscription_end_date FROM users WHERE id = ?", (user_id,))
            user_data = self.cursor.fetchone()
            if user_data:
                return {
                    'id': user_data[0],
                    'username': user_data[1],
                    'is_admin': bool(user_data[2]),
                    'subscribed': bool(user_data[3]),
                    'subscription_end_date': user_data[4]
                }
            return None
        except sqlite3.Error as e:
            logging.error(f"Error getting user {user_id}: {e}")
            return None

    def update_user_subscription(self, user_id, subscribed: bool, subscription_end_date: datetime = None):
        """Обновляет статус подписки пользователя. Пока не используется активно."""
        try:
            end_date_str = subscription_end_date.isoformat() if subscription_end_date else None
            self.cursor.execute(
                "UPDATE users SET subscribed = ?, subscription_end_date = ? WHERE id = ?",
                (subscribed, end_date_str, user_id)
            )
            self.conn.commit()
            logging.info(f"Subscription updated for user {user_id} to {subscribed}, ends {end_date_str}.")
            return True
        except sqlite3.Error as e:
            logging.error(f"Error updating user subscription for user {user_id}: {e}")
            return False

    def get_all_users(self):
        """Получает список всех пользователей."""
        try:
            self.cursor.execute("SELECT id, username, is_admin, subscribed, subscription_end_date FROM users")
            users = self.cursor.fetchall()
            return [{
                'id': u[0], 'username': u[1], 'is_admin': bool(u[2]),
                'subscribed': bool(u[3]), 'subscription_end_date': u[4]
            } for u in users]
        except sqlite3.Error as e:
            logging.error(f"Error getting all users: {e}")
            return []

    def add_client(self, user_id, client_name, common_name): # Изменено для common_name OpenVPN
        """Добавляет новую VPN-конфигурацию для пользователя."""
        try:
            self.cursor.execute(
                "INSERT INTO clients (user_id, client_name, common_name, is_active) VALUES (?, ?, ?, ?)",
                (user_id, client_name, common_name, True) # По умолчанию активен
            )
            self.conn.commit()
            logging.info(f"Client {client_name} (CN: {common_name}) added for user {user_id}.")
            return True
        except sqlite3.Error as e:
            logging.error(f"Error adding client {client_name} for user {user_id}: {e}")
            return False

    def get_user_configs_count(self, user_id):
        """Возвращает количество активных конфигураций для пользователя."""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM clients WHERE user_id = ? AND is_active = TRUE", (user_id,))
            count = self.cursor.fetchone()[0]
            logging.debug(f"User {user_id} has {count} active configs.")
            return count
        except sqlite3.Error as e:
            logging.error(f"Error getting user configs count for user {user_id}: {e}")
            return 0

    def get_user_clients(self, user_id):
        """Возвращает список клиентов пользователя (конфигураций), включая неактивные."""
        try:
            self.cursor.execute("SELECT id, client_name, common_name, is_active FROM clients WHERE user_id = ?", (user_id,))
            clients = self.cursor.fetchall()
            return [{'id': c[0], 'client_name': c[1], 'common_name': c[2], 'is_active': bool(c[3])} for c in clients]
        except sqlite3.Error as e:
            logging.error(f"Error getting user clients for user {user_id}: {e}")
            return []

    def set_client_inactive(self, client_id):
        """Деактивирует клиента (помечает как удаленный)."""
        try:
            self.cursor.execute("UPDATE clients SET is_active = FALSE WHERE id = ?", (client_id,))
            self.conn.commit()
            logging.info(f"Client {client_id} set to inactive.")
            return True
        except sqlite3.Error as e:
            logging.error(f"Error setting client {client_id} inactive: {e}")
            return False

    def get_client_by_id(self, client_id):
        """Получает данные клиента по его ID."""
        try:
            self.cursor.execute("SELECT id, user_id, client_name, common_name, is_active FROM clients WHERE id = ?", (client_id,))
            client_data = self.cursor.fetchone()
            if client_data:
                return {
                    'id': client_data[0],
                    'user_id': client_data[1],
                    'client_name': client_data[2],
                    'common_name': client_data[3],
                    'is_active': bool(client_data[4])
                }
            return None
        except sqlite3.Error as e:
            logging.error(f"Error getting client by ID {client_id}: {e}")
            return None

    def get_client_by_common_name(self, common_name): # Новый метод для OpenVPN
        """Получает данные клиента по его Common Name."""
        try:
            self.cursor.execute("SELECT id, user_id, client_name, common_name, is_active FROM clients WHERE common_name = ?", (common_name,))
            client_data = self.cursor.fetchone()
            if client_data:
                return {
                    'id': client_data[0],
                    'user_id': client_data[1],
                    'client_name': client_data[2],
                    'common_name': client_data[3],
                    'is_active': bool(client_data[4])
                }
            return None
        except sqlite3.Error as e:
            logging.error(f"Error getting client by common name {common_name}: {e}")
            return None
