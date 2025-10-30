import logging
import sqlite3
import os
import json
import re
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackContext,
    CallbackQueryHandler,
    ContextTypes,
)

# Загружаем переменные окружения из .env файла
from dotenv import load_dotenv
load_dotenv()

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Конфигурация приложения"""
    # Получаем токен из переменных окружения
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не установлен! Добавьте его в .env файл или переменные окружения")
    
    # Супер-админ (имеет доступ ко всем отделам + массовая рассылка)
    SUPER_ADMIN_IDS = [5024165375]
    
    # Админы по отделам
    ADMIN_CHAT_IDS = {
        '💻 IT отдел': [5024165375, 123456789],  # Супер-админ + админ IT
        '🔧 Механика': [5024165375, 987654321],  # Супер-админ + админ механики
        '⚡ Электрика': [5024165375, 555555555]  # Супер-админ + админ электрики
    }
    
    DB_PATH = "requests.db"
    LOG_LEVEL = logging.INFO
    REQUEST_TIMEOUT_HOURS = 48  # Таймер выполнения заявки
    BACKUP_INTERVAL_HOURS = 24  # Интервал автоматического бэкапа

    # Настройки уведомлений
    ENABLE_EMAIL_NOTIFICATIONS = False
    EMAIL_CONFIG = {
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'email': 'your_email@gmail.com',
        'password': 'your_password'
    }

    @classmethod
    def get_admins_for_department(cls, department: str) -> List[int]:
        """Получает список админов для отдела"""
        return cls.ADMIN_CHAT_IDS.get(department, [])
    
    @classmethod
    def get_all_admins(cls) -> List[int]:
        """Получает всех уникальных админов"""
        all_admins = set()
        for admins in cls.ADMIN_CHAT_IDS.values():
            all_admins.update(admins)
        return list(all_admins)
    
    @classmethod
    def get_all_users(cls) -> List[int]:
        """Получает всех пользователей из базы"""
        try:
            with sqlite3.connect(cls.DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT DISTINCT user_id FROM users')
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка получения пользователей: {e}")
            return []
    
    @classmethod
    def is_super_admin(cls, user_id: int) -> bool:
        """Проверяет является ли пользователь супер-админом"""
        return user_id in cls.SUPER_ADMIN_IDS
    
    @classmethod
    def is_admin(cls, user_id: int, department: str = None) -> bool:
        """Проверяет является ли пользователь админом (отдела или супер-админом)"""
        if cls.is_super_admin(user_id):
            return True
        if department:
            return user_id in cls.ADMIN_CHAT_IDS.get(department, [])
        # Проверка на админа любого отдела
        for dept_admins in cls.ADMIN_CHAT_IDS.values():
            if user_id in dept_admins:
                return True
        return False

    @classmethod
    def add_admin(cls, department: str, admin_id: int) -> bool:
        """Добавляет админа в отдел"""
        try:
            if department in cls.ADMIN_CHAT_IDS:
                if admin_id not in cls.ADMIN_CHAT_IDS[department]:
                    cls.ADMIN_CHAT_IDS[department].append(admin_id)
                    # Сохраняем изменения в файл
                    cls.save_admins_to_file()
                    return True
            return False
        except Exception as e:
            logger.error(f"Ошибка добавления админа: {e}")
            return False

    @classmethod
    def remove_admin(cls, department: str, admin_id: int) -> bool:
        """Удаляет админа из отдела"""
        try:
            if department in cls.ADMIN_CHAT_IDS:
                if admin_id in cls.ADMIN_CHAT_IDS[department] and admin_id not in cls.SUPER_ADMIN_IDS:
                    cls.ADMIN_CHAT_IDS[department].remove(admin_id)
                    # Сохраняем изменения в файл
                    cls.save_admins_to_file()
                    return True
            return False
        except Exception as e:
            logger.error(f"Ошибка удаления админа: {e}")
            return False

    @classmethod
    def save_admins_to_file(cls):
        """Сохраняет список админов в файл"""
        try:
            with open('admins_backup.json', 'w', encoding='utf-8') as f:
                json.dump(cls.ADMIN_CHAT_IDS, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения админов в файл: {e}")

    @classmethod
    def load_admins_from_file(cls):
        """Загружает список админов из файла"""
        try:
            if os.path.exists('admins_backup.json'):
                with open('admins_backup.json', 'r', encoding='utf-8') as f:
                    cls.ADMIN_CHAT_IDS.update(json.load(f))
        except Exception as e:
            logger.error(f"Ошибка загрузки админов из файла: {e}")

# Загружаем админов из файла при старте
Config.load_admins_from_file()

# ==================== ЛОГИРОВАНИЕ ====================

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=Config.LOG_LEVEL,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Определяем этапы разговора
NAME, PHONE, DEPARTMENT, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD, OTHER_PLOT, SELECT_REQUEST = range(12)

# Состояния для массовой рассылки
BROADCAST_AUDIENCE, BROADCAST_MESSAGE, BROADCAST_CONFIRM = range(12, 15)

# Состояния для управления админами
ADD_ADMIN_DEPARTMENT, ADD_ADMIN_ID, REMOVE_ADMIN_DEPARTMENT, REMOVE_ADMIN_ID = range(15, 19)

# Состояния для поиска заявок
SEARCH_REQUEST = range(19, 20)

# ==================== ВАЛИДАЦИЯ ====================

class Validators:
    """Класс для валидации данных"""
    
    @staticmethod
    def validate_phone(phone: str) -> bool:
        """Проверяет валидность номера телефона"""
        pattern = r'^(\+7|7|8)?[\s\-]?\(?[489][0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}$'
        return bool(re.match(pattern, phone.strip()))
    
    @staticmethod
    def validate_name(name: str) -> bool:
        """Проверяет валидность имени"""
        return len(name.strip()) >= 2 and len(name.strip()) <= 50 and name.replace(' ', '').isalpha()
    
    @staticmethod
    def validate_problem(problem: str) -> bool:
        """Проверяет валидность описания проблемы"""
        return 10 <= len(problem.strip()) <= 1000

    @staticmethod
    def validate_user_id(user_id: str) -> bool:
        """Проверяет валидность ID пользователя"""
        try:
            return user_id.isdigit() and len(user_id) >= 8
        except:
            return False

    @staticmethod
    def validate_email(email: str) -> bool:
        """Проверяет валидность email"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email.strip()))

# ==================== УЛУЧШЕННЫЕ КЛАВИАТУРЫ ====================

# 🎯 Главное меню пользователя
user_main_menu_keyboard = [
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', 'ℹ️ Помощь']
]

# 👑 Главное меню администратора
admin_main_menu_keyboard = [
    ['👑 Админ-панель', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', '🔄 Заявки в работе']
]

# 👑 Главное меню супер-администратора
super_admin_main_menu_keyboard = [
    ['👑 Супер-админ', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', '🔄 Заявки в работе']
]

# 👑 Панель супер-администратора
super_admin_panel_keyboard = [
    ['📢 Массовая рассылка', '👥 Управление админами'],
    ['🏢 Все заявки', '📈 Общая статистика'],
    ['💾 Создать бэкап', '🔄 Автоматизация'],
    ['🔙 Главное меню']
]

# 📢 Клавиатура массовой рассылки
broadcast_keyboard = [
    ['📢 Всем пользователям', '👥 Всем админам'],
    ['💻 IT отдел', '🔧 Механика', '⚡ Электрика'],
    ['🔙 В админ-панель']
]

# 👥 Клавиатура управления админами
admin_management_keyboard = [
    ['➕ Добавить админа', '➖ Удалить админа'],
    ['📋 Список админов', '🔄 Обновить права'],
    ['🔙 В админ-панель']
]

# 🏢 Админ-панели по отделам
it_admin_panel_keyboard = [
    ['🆕 Новые заявки IT', '🔄 В работе IT'],
    ['✅ Выполненные IT', '📊 Статистика IT'],
    ['👥 Админы IT', '🔙 Главное меню']
]

mechanics_admin_panel_keyboard = [
    ['🆕 Новые заявки механики', '🔄 В работе механики'],
    ['✅ Выполненные механики', '📊 Статистика механики'],
    ['👥 Админы механики', '🔙 Главное меню']
]

electricity_admin_panel_keyboard = [
    ['🆕 Новые заявки электрики', '🔄 В работе электрики'],
    ['✅ Выполненные электрики', '📊 Статистика электрики'],
    ['👥 Админы электрики', '🔙 Главное меню']
]

# 🏢 Общая админ-панель для обычных админов
admin_department_select_keyboard = [
    ['💻 IT админ-панель', '🔧 Механика админ-панель'],
    ['⚡ Электрика админ-панель', '🔙 Главное меню']
]

# 🏢 Выбор отдела
department_keyboard = [
    ['💻 IT отдел', '🔧 Механика'],
    ['⚡ Электрика', '🔙 Назад в меню']
]

# 💻 Типы IT систем
it_systems_keyboard = [
    ['💻 Компьютеры', '🖨️ Принтеры'],
    ['🌐 Интернет', '📞 Телефония'],
    ['🔐 Программы', '📊 1С и Базы'],
    ['🎥 Оборудование', '⚡ Другое'],
    ['🔙 Назад к выбору отдела']
]

# 🔧 Типы проблем для механики
mechanics_keyboard = [
    ['🔩 Станки и оборудование', '🛠️ Ручной инструмент'],
    ['⚙️ Гидравлика/Пневматика', '🔧 Техническое обслуживание'],
    ['🚗 Транспортные средства', '🏗️ Производственные линии'],
    ['⚡ Другое (механика)', '🔙 Назад к выбору отдела']
]

# ⚡ Типы проблем для электрики
electricity_keyboard = [
    ['💡 Освещение', '🔌 Электропроводка'],
    ['⚡ Электрощитовое', '🔋 Источники питания'],
    ['🎛️ Автоматика и КИП', '🛑 Аварийные системы'],
    ['🔧 Другое (электрика)', '🔙 Назад к выбору отдела']
]

# ✅ Клавиатура подтверждения
confirm_keyboard = [
    ['🚀 Отправить заявку', '✏️ Исправить'],
    ['🔙 Отменить']
]

# 📸 Клавиатура для фото
photo_keyboard = [
    ['📷 Добавить фото', '⏭️ Без фото'],
    ['🔙 Назад']
]

# ⏰ Клавиатура срочности
urgency_keyboard = [
    ['🔥 СРОЧНО (1-2 часа)'],
    ['⚠️ СЕГОДНЯ (до конца дня)'],
    ['💤 НЕ СРОЧНО (1-3 дня)'],
    ['🔙 Назад']
]

# 🏢 Типы участков
plot_type_keyboard = [
    ['🏢 Центральный офис', '🏭 Производство'],
    ['📦 Складской комплекс', '🛒 Торговый зал'],
    ['💻 Удаленные рабочие места', '📋 Другой участок'],
    ['🔙 Назад']
]

# ✏️ Клавиатура редактирования
edit_choice_keyboard = [
    ['👤 Имя', '📞 Телефон', '🏢 Отдел'],
    ['📍 Участок', '🔧 Тип проблемы', '📝 Описание'],
    ['⏰ Срочность', '📷 Фото', '✅ Готово'],
    ['🔙 Отменить']
]

# ◀️ Клавиатура назад
back_keyboard = [['🔙 Назад']]

# 🔍 Клавиатура поиска
search_keyboard = [
    ['🔍 Поиск по ID', '📅 Поиск по дате'],
    ['🏢 Поиск по отделу', '🔙 Главное меню']
]

# ==================== РАСШИРЕННАЯ БАЗА ДАННЫХ ====================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Инициализация базы данных с расширенными таблицами"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Основная таблица заявок
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        username TEXT,
                        name TEXT,
                        phone TEXT,
                        department TEXT,
                        plot TEXT,
                        system_type TEXT,
                        problem TEXT,
                        photo TEXT,
                        urgency TEXT,
                        status TEXT DEFAULT 'new',
                        created_at TEXT,
                        updated_at TEXT,
                        admin_comment TEXT,
                        assigned_admin TEXT,
                        assigned_at TEXT,
                        completed_at TEXT,
                        rating INTEGER,
                        user_comment TEXT
                    )
                ''')
                
                # Таблица статистики
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS statistics (
                        date TEXT PRIMARY KEY,
                        requests_count INTEGER DEFAULT 0,
                        completed_count INTEGER DEFAULT 0,
                        avg_completion_time REAL DEFAULT 0
                    )
                ''')
                
                # Таблица пользователей
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        created_at TEXT,
                        request_count INTEGER DEFAULT 0,
                        last_activity TEXT,
                        is_active INTEGER DEFAULT 1
                    )
                ''')
                
                # Таблица настроек
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                
                # Таблица логов действий
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS action_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        action TEXT,
                        details TEXT,
                        timestamp TEXT
                    )
                ''')
                
                # Добавляем индексы для производительности
                indexes = [
                    'CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id)',
                    'CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status)',
                    'CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at)',
                    'CREATE INDEX IF NOT EXISTS idx_requests_department ON requests(department)',
                    'CREATE INDEX IF NOT EXISTS idx_requests_assigned_at ON requests(assigned_at)',
                    'CREATE INDEX IF NOT EXISTS idx_users_activity ON users(last_activity)',
                    'CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON action_logs(timestamp)'
                ]
                
                for index_sql in indexes:
                    cursor.execute(index_sql)
                
                # Добавляем начальные настройки
                cursor.execute('''
                    INSERT OR IGNORE INTO settings (key, value) 
                    VALUES ('auto_backup', '1'), ('notify_overdue', '1')
                ''')
                
                conn.commit()
                logger.info("База данных успешно инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}")
            raise

    def save_request(self, user_data: Dict) -> int:
        """Сохраняет заявку в базу данных"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO requests 
                    (user_id, username, name, phone, department, plot, system_type, problem, photo, urgency, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_data.get('user_id'),
                    user_data.get('username'),
                    user_data.get('name'),
                    user_data.get('phone'),
                    user_data.get('department'),
                    user_data.get('plot'),
                    user_data.get('system_type'),
                    user_data.get('problem'),
                    user_data.get('photo'),
                    user_data.get('urgency'),
                    datetime.now().isoformat(),
                    datetime.now().isoformat()
                ))
                request_id = cursor.lastrowid
                
                today = datetime.now().strftime('%Y-%m-%d')
                cursor.execute('''
                    INSERT OR REPLACE INTO statistics (date, requests_count)
                    VALUES (?, COALESCE((SELECT requests_count FROM statistics WHERE date = ?), 0) + 1)
                ''', (today, today))
                
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at, request_count, last_activity)
                    VALUES (?, ?, ?, ?, ?, COALESCE((SELECT request_count FROM users WHERE user_id = ?), 0) + 1, ?)
                ''', (
                    user_data.get('user_id'),
                    user_data.get('username'),
                    user_data.get('first_name', ''),
                    user_data.get('last_name', ''),
                    datetime.now().isoformat(),
                    user_data.get('user_id'),
                    datetime.now().isoformat()
                ))
                
                # Логируем действие
                self.log_action(
                    user_data.get('user_id'),
                    'create_request',
                    f'Создана заявка #{request_id}'
                )
                
                conn.commit()
                logger.info(f"Заявка #{request_id} успешно сохранена для пользователя {user_data.get('user_id')}")
                return request_id
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            raise

    def get_user_requests(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Получает заявки пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE user_id = ? 
                    ORDER BY created_at DESC 
                    LIMIT ?
                ''', (user_id, limit))
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
                logger.debug(f"Получено {len(requests)} заявок для пользователя {user_id}")
                return requests
        except Exception as e:
            logger.error(f"Ошибка при получении заявок пользователя {user_id}: {e}")
            return []

    def get_requests_by_filter(self, department: str = None, status: str = 'all', limit: int = 50) -> List[Dict]:
        """Получает заявки по фильтру отдела и статуса"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Безопасное формирование запроса
                status_conditions = {
                    'new': "status = 'new'",
                    'in_progress': "status = 'in_progress'", 
                    'completed': "status = 'completed'",
                    'all': "status IN ('new', 'in_progress', 'completed')"
                }
                
                status_filter = status_conditions.get(status, "status IN ('new', 'in_progress')")
                
                if department:
                    query = f'''
                        SELECT * FROM requests 
                        WHERE department = ? AND {status_filter}
                        ORDER BY 
                            CASE urgency 
                                WHEN '🔥 СРОЧНО (1-2 часа)' THEN 1
                                WHEN '⚠️ СЕГОДНЯ (до конца дня)' THEN 2
                                ELSE 3
                            END,
                            created_at DESC
                        LIMIT ?
                    '''
                    cursor.execute(query, (department, limit))
                else:
                    query = f'''
                        SELECT * FROM requests 
                        WHERE {status_filter}
                        ORDER BY 
                            CASE urgency 
                                WHEN '🔥 СРОЧНО (1-2 часа)' THEN 1
                                WHEN '⚠️ СЕГОДНЯ (до конца дня)' THEN 2
                                ELSE 3
                            END,
                            created_at DESC
                        LIMIT ?
                    '''
                    cursor.execute(query, (limit,))
                
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
                logger.debug(f"Получено {len(requests)} заявок с фильтром '{status}' для отдела '{department}'")
                return requests
        except Exception as e:
            logger.error(f"Ошибка при получении заявок: {e}")
            return []

    def get_request(self, request_id: int) -> Dict:
        """Получает заявку по ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
                row = cursor.fetchone()
                if row:
                    columns = [column[0] for column in cursor.description]
                    return dict(zip(columns, row))
                return {}
        except Exception as e:
            logger.error(f"Ошибка при получении заявки #{request_id}: {e}")
            return {}

    def update_request(self, request_id: int, update_data: Dict) -> bool:
        """Обновляет данные заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                set_parts = []
                parameters = []
                
                for field, value in update_data.items():
                    if field in ['name', 'phone', 'department', 'plot', 'system_type', 'problem', 'photo', 'urgency']:
                        set_parts.append(f"{field} = ?")
                        parameters.append(value)
                
                set_parts.append("updated_at = ?")
                parameters.append(datetime.now().isoformat())
                parameters.append(request_id)
                
                if set_parts:
                    sql = f"UPDATE requests SET {', '.join(set_parts)} WHERE id = ?"
                    cursor.execute(sql, parameters)
                    
                    # Логируем действие
                    self.log_action(
                        update_data.get('user_id', 0),
                        'update_request',
                        f'Обновлена заявка #{request_id}'
                    )
                    
                    conn.commit()
                    logger.info(f"Заявка #{request_id} успешно обновлена")
                    return True
                return False
        except Exception as e:
            logger.error(f"Ошибка при обновлении заявки #{request_id}: {e}")
            return False

    def update_request_status(self, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
        """Обновляет статус заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                update_data = {
                    'status': status,
                    'updated_at': datetime.now().isoformat()
                }
                
                if admin_comment:
                    update_data['admin_comment'] = admin_comment
                
                if assigned_admin:
                    update_data['assigned_admin'] = assigned_admin
                    update_data['assigned_at'] = datetime.now().isoformat()
                
                if status == 'completed':
                    update_data['completed_at'] = datetime.now().isoformat()
                    
                    # Обновляем статистику времени выполнения
                    request = self.get_request(request_id)
                    if request and request.get('assigned_at'):
                        start_time = datetime.fromisoformat(request['assigned_at'])
                        completion_time = datetime.now()
                        time_diff = (completion_time - start_time).total_seconds() / 3600  # в часах
                        
                        today = datetime.now().strftime('%Y-%m-%d')
                        cursor.execute('''
                            UPDATE statistics 
                            SET avg_completion_time = (
                                SELECT AVG((julianday(completed_at) - julianday(assigned_at)) * 24) 
                                FROM requests 
                                WHERE date(completed_at) = ? AND status = 'completed'
                            )
                            WHERE date = ?
                        ''', (today, today))
                
                set_parts = []
                parameters = []
                for field, value in update_data.items():
                    set_parts.append(f"{field} = ?")
                    parameters.append(value)
                
                parameters.append(request_id)
                sql = f"UPDATE requests SET {', '.join(set_parts)} WHERE id = ?"
                cursor.execute(sql, parameters)
                
                # Логируем действие
                self.log_action(
                    0,  # system action
                    'update_status',
                    f'Статус заявки #{request_id} изменен на {status}'
                )
                
                conn.commit()
                logger.info(f"Статус заявки #{request_id} изменен на '{status}'")
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса заявки #{request_id}: {e}")
            raise

    def get_all_requests(self, limit: int = 100) -> List[Dict]:
        """Получает все заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    ORDER BY created_at DESC 
                    LIMIT ?
                ''', (limit,))
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
                return requests
        except Exception as e:
            logger.error(f"Ошибка при получении всех заявок: {e}")
            return []

    def get_statistics(self, days: int = 30) -> Dict:
        """Получает статистику за указанный период"""
        try:
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Общее количество заявок
                cursor.execute('SELECT COUNT(*) FROM requests WHERE created_at >= ?', (start_date,))
                total_requests = cursor.fetchone()[0]
                
                # Заявки по статусам
                cursor.execute('SELECT status, COUNT(*) FROM requests WHERE created_at >= ? GROUP BY status', (start_date,))
                status_stats = dict(cursor.fetchall())
                
                # Заявки по отделам
                cursor.execute('SELECT department, COUNT(*) FROM requests WHERE created_at >= ? GROUP BY department', (start_date,))
                department_stats = dict(cursor.fetchall())
                
                # Среднее время выполнения
                cursor.execute('''
                    SELECT AVG((julianday(completed_at) - julianday(assigned_at)) * 24) 
                    FROM requests 
                    WHERE completed_at >= ? AND status = 'completed'
                ''', (start_date,))
                avg_completion_time = cursor.fetchone()[0] or 0
                
                # Активные пользователи
                cursor.execute('SELECT COUNT(*) FROM users WHERE last_activity >= ?', (start_date,))
                active_users = cursor.fetchone()[0]
                
                return {
                    'total_requests': total_requests,
                    'status_stats': status_stats,
                    'department_stats': department_stats,
                    'avg_completion_time': round(avg_completion_time, 2),
                    'active_users': active_users,
                    'period_days': days
                }
        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {e}")
            return {}

    def get_overdue_requests(self) -> List[Dict]:
        """Получает просроченные заявки (более 48 часов в работе)"""
        try:
            deadline = (datetime.now() - timedelta(hours=Config.REQUEST_TIMEOUT_HOURS)).isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE status = 'in_progress' 
                    AND assigned_at < ?
                    ORDER BY assigned_at ASC
                ''', (deadline,))
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
                return requests
        except Exception as e:
            logger.error(f"Ошибка при получении просроченных заявок: {e}")
            return []

    def search_requests(self, search_term: str, search_type: str = 'id') -> List[Dict]:
        """Поиск заявок по различным критериям"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                if search_type == 'id':
                    cursor.execute('''
                        SELECT * FROM requests 
                        WHERE id = ? 
                        ORDER BY created_at DESC
                    ''', (search_term,))
                elif search_type == 'date':
                    cursor.execute('''
                        SELECT * FROM requests 
                        WHERE date(created_at) = ? 
                        ORDER BY created_at DESC
                    ''', (search_term,))
                elif search_type == 'department':
                    cursor.execute('''
                        SELECT * FROM requests 
                        WHERE department LIKE ? 
                        ORDER BY created_at DESC
                    ''', (f'%{search_term}%',))
                elif search_type == 'user':
                    cursor.execute('''
                        SELECT * FROM requests 
                        WHERE name LIKE ? OR username LIKE ?
                        ORDER BY created_at DESC
                    ''', (f'%{search_term}%', f'%{search_term}%'))
                
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
                return requests
        except Exception as e:
            logger.error(f"Ошибка при поиске заявок: {e}")
            return []

    def log_action(self, user_id: int, action: str, details: str):
        """Логирует действия пользователей"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO action_logs (user_id, action, details, timestamp)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, action, details, datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logger.error(f"Ошибка логирования действия: {e}")

    def create_backup(self) -> str:
        """Создает резервную копию базы данных"""
        try:
            backup_dir = 'backups'
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(backup_dir, f'backup_{timestamp}.db')
            
            with sqlite3.connect(self.db_path) as source:
                with sqlite3.connect(backup_path) as target:
                    source.backup(target)
            
            logger.info(f"Создан бэкап базы данных: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Ошибка создания бэкапа: {e}")
            return ""

    def get_user_stats(self, user_id: int) -> Dict:
        """Получает статистику пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('SELECT COUNT(*) FROM requests WHERE user_id = ?', (user_id,))
                total_requests = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(*) FROM requests WHERE user_id = ? AND status = "completed"', (user_id,))
                completed_requests = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(*) FROM requests WHERE user_id = ? AND status = "in_progress"', (user_id,))
                in_progress_requests = cursor.fetchone()[0]
                
                return {
                    'total_requests': total_requests,
                    'completed_requests': completed_requests,
                    'in_progress_requests': in_progress_requests,
                    'completion_rate': round((completed_requests / total_requests * 100) if total_requests > 0 else 0, 1)
                }
        except Exception as e:
            logger.error(f"Ошибка получения статистики пользователя: {e}")
            return {}

# Инициализация базы данных
db = Database(Config.DB_PATH)

# ==================== НОВЫЕ ФУНКЦИОНАЛЬНЫЕ ВОЗМОЖНОСТИ ====================

class NotificationSystem:
    """Система уведомлений"""
    
    @staticmethod
    async def send_urgent_notification(context: ContextTypes.DEFAULT_TYPE, request_id: int, department: str):
        """Отправляет срочное уведомление"""
        try:
            admins = Config.get_admins_for_department(department)
            request = db.get_request(request_id)
            
            if not request:
                return
            
            urgent_text = (
                f"🚨 *СРОЧНАЯ ЗАЯВКА!* 🚨\n\n"
                f"📋 *Заявка #{request_id}*\n"
                f"👤 *Пользователь:* {request['name']}\n"
                f"📞 *Телефон:* {request['phone']}\n"
                f"🏢 *Отдел:* {department}\n"
                f"📍 *Участок:* {request['plot']}\n"
                f"⏰ *Срочность:* {request['urgency']}\n"
                f"📝 *Проблема:* {request['problem'][:200]}...\n\n"
                f"⚠️ *Требуется немедленное внимание!*"
            )
            
            for admin_id in admins:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=urgent_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки срочного уведомления админу {admin_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка в системе срочных уведомлений: {e}")

    @staticmethod
    async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
        """Отправляет ежедневный отчет супер-админам"""
        try:
            stats = db.get_statistics(days=1)
            overdue_requests = db.get_overdue_requests()
            
            report_text = (
                f"📊 *ЕЖЕДНЕВНЫЙ ОТЧЕТ*\n\n"
                f"📅 *Дата:* {datetime.now().strftime('%d.%m.%Y')}\n"
                f"📈 *Новых заявок:* {stats.get('total_requests', 0)}\n"
                f"✅ *Выполнено:* {stats.get('status_stats', {}).get('completed', 0)}\n"
                f"⏰ *Среднее время выполнения:* {stats.get('avg_completion_time', 0)} ч.\n"
                f"👥 *Активных пользователей:* {stats.get('active_users', 0)}\n\n"
            )
            
            if overdue_requests:
                report_text += f"🚨 *Просроченных заявок:* {len(overdue_requests)}\n"
            else:
                report_text += "✅ *Просроченных заявок нет*\n"
            
            for admin_id in Config.SUPER_ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=report_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки отчета супер-админу {admin_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка отправки ежедневного отчета: {e}")

class BackupSystem:
    """Система резервного копирования"""
    
    @staticmethod
    async def auto_backup(context: ContextTypes.DEFAULT_TYPE):
        """Автоматическое создание бэкапа"""
        try:
            backup_path = db.create_backup()
            if backup_path:
                backup_size = os.path.getsize(backup_path) / 1024 / 1024  # MB
                
                for admin_id in Config.SUPER_ADMIN_IDS:
                    try:
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=f"💾 *Автоматический бэкап создан*\n\n📁 Файл: `{backup_path}`\n📊 Размер: {backup_size:.2f} MB",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception as e:
                        logger.error(f"Ошибка уведомления о бэкапе: {e}")
                        
        except Exception as e:
            logger.error(f"Ошибка автоматического бэкапа: {e}")

# ==================== ОБРАБОТЧИКИ СОЗДАНИЯ ЗАЯВКИ (ОСТАЮТСЯ БЕЗ ИЗМЕНЕНИЙ) ====================

async def start_request_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс создания заявки"""
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} начал создание заявки")
    
    # Инициализируем данные пользователя
    context.user_data['user_id'] = user.id
    context.user_data['username'] = user.username
    context.user_data['first_name'] = user.first_name
    context.user_data['last_name'] = user.last_name
    
    await update.message.reply_text(
        "🎯 *Создание новой заявки*\n\n"
        "👤 *Шаг 1 из 8: Введите ваше ФИО*\n\n"
        "💡 Пример: *Иванов Иван Иванович*",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

# ... (остальные обработчики создания заявки остаются без изменений)

# ==================== НОВЫЕ КОМАНДЫ И ФУНКЦИОНАЛ ====================

async def search_requests_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает меню поиска заявок"""
    user_id = update.message.from_user.id
    
    await update.message.reply_text(
        "🔍 *Поиск заявок*\n\n"
        "Выберите тип поиска:",
        reply_markup=ReplyKeyboardMarkup(search_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SEARCH_REQUEST

async def search_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Поиск заявки по ID"""
    await update.message.reply_text(
        "🔍 *Поиск по ID*\n\n"
        "Введите номер заявки:",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['search_type'] = 'id'
    return SEARCH_REQUEST

async def search_by_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Поиск заявок по дате"""
    await update.message.reply_text(
        "📅 *Поиск по дате*\n\n"
        "Введите дату в формате ГГГГ-ММ-ДД:\n\n"
        "💡 Пример: 2024-01-15",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['search_type'] = 'date'
    return SEARCH_REQUEST

async def search_by_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Поиск заявок по отделу"""
    await update.message.reply_text(
        "🏢 *Поиск по отделу*\n\n"
        "Введите название отдела:\n\n"
        "💡 Пример: IT, Механика, Электрика",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['search_type'] = 'department'
    return SEARCH_REQUEST

async def process_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает поисковый запрос"""
    if update.message.text == '🔙 Назад':
        await search_requests_menu(update, context)
        return SEARCH_REQUEST
    
    search_term = update.message.text.strip()
    search_type = context.user_data.get('search_type', 'id')
    
    requests = db.search_requests(search_term, search_type)
    
    if not requests:
        await update.message.reply_text(
            "❌ *Заявки не найдены*\n\n"
            "Попробуйте изменить критерии поиска",
            reply_markup=ReplyKeyboardMarkup(search_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return SEARCH_REQUEST
    
    await update.message.reply_text(
        f"✅ *Найдено заявок: {len(requests)}*\n\n"
        "Результаты поиска:",
        reply_markup=ReplyKeyboardMarkup(search_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Показываем найденные заявки
    for request in requests[:5]:  # Ограничиваем вывод
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }.get(request['status'], '❓')
        
        request_text = (
            f"📋 *Заявка #{request['id']}*\n"
            f"{status_emoji} *Статус:* {request['status']}\n"
            f"👤 *Пользователь:* {request['name']}\n"
            f"🏢 *Отдел:* {request['department']}\n"
            f"🔧 *Тип:* {request['system_type']}\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"⏰ *Срочность:* {request['urgency']}\n"
            f"📝 *Описание:* {request['problem'][:100]}...\n"
            f"🕒 *Создана:* {request['created_at'][:16]}"
        )
        
        await update.message.reply_text(
            request_text,
            parse_mode=ParseMode.MARKDOWN
        )
    
    return SEARCH_REQUEST

async def take_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды для взятия заявки в работу"""
    try:
        user_id = update.message.from_user.id
        command_text = update.message.text
        
        # Извлекаем ID заявки из команды /take_123
        request_id = int(command_text.split('_')[1])
        
        request = db.get_request(request_id)
        if not request:
            await update.message.reply_text("❌ Заявка не найдена")
            return
        
        # Проверяем права доступа к отделу
        if not Config.is_admin(user_id, request['department']):
            await update.message.reply_text("❌ У вас нет доступа к этой заявке")
            return
        
        # Проверяем что заявка еще новая
        if request['status'] != 'new':
            await update.message.reply_text("❌ Заявка уже взята в работу")
            return
        
        # Берем заявку в работу
        admin_name = update.message.from_user.first_name
        db.update_request_status(
            request_id=request_id,
            status='in_progress',
            assigned_admin=admin_name
        )
        
        await update.message.reply_text(
            f"✅ Заявка #{request_id} взята в работу!\n\n"
            f"👨‍💼 Исполнитель: {admin_name}"
        )
        
        # Уведомляем пользователя
        await notify_user_about_request_status(
            update, context, request_id, 'in_progress', 
            assigned_admin=admin_name
        )
        
        # Если заявка срочная - отправляем дополнительное уведомление
        if 'СРОЧНО' in request['urgency']:
            await NotificationSystem.send_urgent_notification(
                context, request_id, request['department']
            )
        
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Неверный формат команды. Используйте: /take_123")
    except Exception as e:
        logger.error(f"Ошибка взятия заявки: {e}")
        await update.message.reply_text("❌ Ошибка при взятии заявки")

async def complete_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды для завершения заявки"""
    try:
        user_id = update.message.from_user.id
        
        if not context.args:
            await update.message.reply_text("❌ Используйте: /complete <ID_заявки> <комментарий>")
            return
        
        request_id = int(context.args[0])
        comment = ' '.join(context.args[1:]) if len(context.args) > 1 else "Выполнено"
        
        request = db.get_request(request_id)
        if not request:
            await update.message.reply_text("❌ Заявка не найдена")
            return
        
        # Проверяем права и что заявка в работе
        if not Config.is_admin(user_id, request['department']):
            await update.message.reply_text("❌ У вас нет доступа к этой заявке")
            return
        
        if request['status'] != 'in_progress':
            await update.message.reply_text("❌ Заявка не в работе")
            return
        
        # Завершаем заявку
        admin_name = update.message.from_user.first_name
        db.update_request_status(
            request_id=request_id,
            status='completed',
            admin_comment=comment,
            assigned_admin=admin_name
        )
        
        await update.message.reply_text(
            f"✅ Заявка #{request_id} завершена!\n\n"
            f"💬 Комментарий: {comment}"
        )
        
        # Уведомляем пользователя
        await notify_user_about_request_status(
            update, context, request_id, 'completed', 
            admin_comment=comment, assigned_admin=admin_name
        )
        
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Неверный формат команды")
    except Exception as e:
        logger.error(f"Ошибка завершения заявки: {e}")
        await update.message.reply_text("❌ Ошибка при завершении заявки")

async def show_user_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает персональную статистику пользователя"""
    user_id = update.message.from_user.id
    stats = db.get_user_stats(user_id)
    
    if stats['total_requests'] == 0:
        await update.message.reply_text(
            "📊 *Ваша статистика*\n\n"
            "У вас пока нет созданных заявок.\n"
            "Создайте первую заявку! 🎯",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    stats_text = (
        f"📊 *ВАША СТАТИСТИКА*\n\n"
        f"📈 *Всего заявок:* {stats['total_requests']}\n"
        f"✅ *Выполнено:* {stats['completed_requests']}\n"
        f"🔄 *В работе:* {stats['in_progress_requests']}\n"
        f"📊 *Процент выполнения:* {stats['completion_rate']}%\n\n"
        f"💡 *Рекомендации:*\n"
    )
    
    if stats['completion_rate'] >= 80:
        stats_text += "🎉 Отличная работа! Продолжайте в том же духе!"
    elif stats['completion_rate'] >= 50:
        stats_text += "👍 Хорошие показатели! Есть куда стремиться."
    else:
        stats_text += "💪 Работайте над выполнением заявок в срок!"
    
    await update.message.reply_text(
        stats_text,
        parse_mode=ParseMode.MARKDOWN
    )

async def create_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Создает резервную копию базы данных"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    try:
        backup_path = db.create_backup()
        if backup_path:
            backup_size = os.path.getsize(backup_path) / 1024 / 1024  # MB
            
            await update.message.reply_text(
                f"💾 *Бэкап создан успешно!*\n\n"
                f"📁 *Файл:* `{backup_path}`\n"
                f"📊 *Размер:* {backup_size:.2f} MB\n"
                f"🕒 *Время:* {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("❌ Ошибка при создании бэкапа")
            
    except Exception as e:
        logger.error(f"Ошибка создания бэкапа: {e}")
        await update.message.reply_text("❌ Ошибка при создании бэкапа")

async def show_requests_in_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает заявки в работе для администратора"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    # Для обычных админов показываем только заявки их отделов
    if Config.is_super_admin(user_id):
        requests = db.get_requests_by_filter(status='in_progress', limit=20)
    else:
        # Определяем отделы, к которым есть доступ
        user_departments = []
        for department, admins in Config.ADMIN_CHAT_IDS.items():
            if user_id in admins:
                user_departments.append(department)
        
        requests = []
        for department in user_departments:
            dept_requests = db.get_requests_by_filter(
                department=department, 
                status='in_progress', 
                limit=10
            )
            requests.extend(dept_requests)
    
    if not requests:
        await update.message.reply_text(
            "📭 *Заявок в работе нет*\n\n"
            "Все заявки обработаны! 🎉",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    await update.message.reply_text(
        f"🔄 *Заявки в работе: {len(requests)}*\n\n"
        "Список заявок:",
        parse_mode=ParseMode.MARKDOWN
    )
    
    for request in requests[:10]:  # Ограничиваем вывод
        # Рассчитываем время в работе
        if request.get('assigned_at'):
            assigned_time = datetime.fromisoformat(request['assigned_at'])
            work_duration = datetime.now() - assigned_time
            hours_in_work = int(work_duration.total_seconds() / 3600)
            time_info = f"⏰ В работе: {hours_in_work} ч."
        else:
            time_info = "⏰ Время не указано"
        
        request_text = (
            f"📋 *Заявка #{request['id']}*\n"
            f"🏢 *Отдел:* {request['department']}\n"
            f"🔧 *Тип:* {request['system_type']}\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"👨‍💼 *Исполнитель:* {request.get('assigned_admin', 'Не назначен')}\n"
            f"📝 *Описание:* {request['problem'][:100]}...\n"
            f"{time_info}\n\n"
            f"💡 *Команда для завершения:* /complete_{request['id']} комментарий"
        )
        
        await update.message.reply_text(
            request_text,
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== ОБНОВЛЕННЫЕ ОСНОВНЫЕ КОМАНДЫ ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start с улучшенным приветствием"""
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} запустил бота")
    
    # Обновляем активность пользователя
    try:
        with sqlite3.connect(Config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users 
                (user_id, username, first_name, last_name, created_at, last_activity)
                VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM users WHERE user_id = ?), ?), ?)
            ''', (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                user.id,
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка обновления пользователя: {e}")
    
    welcome_text = (
        "👋 *Добро пожаловать в систему заявок завода Контакт!*\n\n"
        "🛠️ *Мы поможем с:*\n"
        "• 💻 IT проблемами - компьютеры, программы, сети\n"
        "• 🔧 Механическими неисправностями - станки, оборудование\n" 
        "• ⚡ Электрическими вопросами - проводка, освещение\n\n"
        "🎯 *Новые возможности:*\n"
        "• 🔍 Поиск заявок по различным критериям\n"
        "• 📊 Персональная статистика\n"
        "• 🚨 Срочные уведомления\n"
        "• 💾 Автоматические бэкапы\n\n"
        "💡 *Выберите действие из меню ниже:*"
    )
    
    if Config.is_super_admin(user.id):
        keyboard = super_admin_main_menu_keyboard
        welcome_text += "\n\n👑 *Ваш статус: СУПЕР-АДМИНИСТРАТОР*"
    elif Config.is_admin(user.id):
        keyboard = admin_main_menu_keyboard
        welcome_text += "\n\n👨‍💼 *Ваш статус: АДМИНИСТРАТОР*"
    else:
        keyboard = user_main_menu_keyboard
        welcome_text += "\n\n💼 *Ваш статус: ПОЛЬЗОВАТЕЛЬ*"
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ТЕКСТОВЫХ СООБЩЕНИЙ ====================

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений для меню с новыми функциями"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Обработка основных кнопок меню
    if text == '🎯 Создать заявку':
        await start_request_creation(update, context)
    elif text == '📂 Мои заявки':
        await show_my_requests(update, context)
    elif text == '🔍 Поиск заявки':
        await search_requests_menu(update, context)
    elif text == '📊 Статистика':
        await show_user_statistics(update, context)
    elif text == '🔄 Заявки в работе':
        await show_requests_in_progress(update, context)
    elif text == 'ℹ️ Помощь':
        await show_help(update, context)
    elif text == '👑 Админ-панель':
        await show_admin_panel(update, context)
    elif text == '👑 Супер-админ':
        await show_super_admin_panel(update, context)
    elif text == '🔙 Главное меню':
        await show_main_menu(update, context)
    
    # Обработка кнопок супер-админ панели
    elif text == '📢 Массовая рассылка':
        await start_broadcast(update, context)
    elif text == '👥 Управление админами':
        await show_admin_management(update, context)
    elif text == '🏢 Все заявки':
        await show_all_requests(update, context)
    elif text == '📈 Общая статистика':
        await show_general_statistics(update, context)
    elif text == '💾 Создать бэкап':
        await create_backup_command(update, context)
    elif text == '🔙 В админ-панель':
        await show_super_admin_panel(update, context)
    
    # Обработка кнопок управления админами
    elif text == '➕ Добавить админа':
        await add_admin_start(update, context)
    elif text == '➖ Удалить админа':
        await remove_admin_start(update, context)
    elif text == '📋 Список админов':
        await show_admin_list(update, context)
    elif text == '🔄 Обновить права':
        await update.message.reply_text("✅ Права администраторов обновлены")
    
    # Обработка кнопок массовой рассылки
    elif text in ['📢 Всем пользователям', '👥 Всем админам', '💻 IT отдел', '🔧 Механика', '⚡ Электрика']:
        await broadcast_audience(update, context)
    
    # Обработка кнопок админ-панелей отделов
    elif text in ['💻 IT админ-панель', '🔧 Механика админ-панель', '⚡ Электрика админ-панель']:
        await show_department_admin_panel(update, context)
    
    # Обработка кнопок новых заявок
    elif text in ['🆕 Новые заявки IT', '🆕 Новые заявки механики', '🆕 Новые заявки электрики']:
        await show_new_requests(update, context)
    
    # Обработка кнопок поиска
    elif text == '🔍 Поиск по ID':
        await search_by_id(update, context)
    elif text == '📅 Поиск по дате':
        await search_by_date(update, context)
    elif text == '🏢 Поиск по отделу':
        await search_by_department(update, context)
    
    else:
        # Проверяем команды вида /take_123 и /complete_123
        if text.startswith('/take_'):
            await take_request_command(update, context)
        elif text.startswith('/complete_'):
            # Извлекаем ID из команды /complete_123
            try:
                request_id = int(text.split('_')[1])
                context.args = [str(request_id), 'Заявка выполнена']
                await complete_request_command(update, context)
            except (IndexError, ValueError):
                await update.message.reply_text("❌ Неверный формат команды")
        else:
            await update.message.reply_text(
                "Используйте кнопки меню для навигации",
                reply_markup=ReplyKeyboardMarkup(
                    super_admin_main_menu_keyboard if Config.is_super_admin(user_id) else
                    admin_main_menu_keyboard if Config.is_admin(user_id) else
                    user_main_menu_keyboard, 
                    resize_keyboard=True
                )
            )

# ==================== АВТОМАТИЗИРОВАННЫЕ ЗАДАЧИ ====================

async def daily_tasks(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневные автоматические задачи"""
    try:
        # Отправка ежедневного отчета
        await NotificationSystem.send_daily_report(context)
        
        # Автоматический бэкап (раз в сутки)
        await BackupSystem.auto_backup(context)
        
        # Проверка просроченных заявок
        await check_overdue_requests(context)
        
        logger.info("Ежедневные задачи выполнены успешно")
    except Exception as e:
        logger.error(f"Ошибка выполнения ежедневных задач: {e}")

async def hourly_tasks(context: ContextTypes.DEFAULT_TYPE):
    """Ежечасные автоматические задачи"""
    try:
        # Проверяем срочные заявки каждые 2 часа
        overdue_requests = db.get_overdue_requests()
        if overdue_requests:
            for request in overdue_requests:
                await NotificationSystem.send_urgent_notification(
                    context, request['id'], request['department']
                )
    except Exception as e:
        logger.error(f"Ошибка выполнения ежечасных задач: {e}")

# ==================== ЗАПУСК БОТА С УЛУЧШЕНИЯМИ ====================

def main() -> None:
    """Запускаем бота с улучшенным функционалом"""
    try:
        # Проверяем, что токен загружен
        if not Config.BOT_TOKEN:
            logger.error("❌ Токен бота не загружен из .env файла!")
            print("❌ Токен бота не загружен!")
            print("💡 Проверьте что:")
            print("1. Файл .env существует в той же папке что и бот")
            print("2. В файле .env есть строка: BOT_TOKEN=ваш_токен")
            print("3. Установлена библиотека: pip install python-dotenv")
            return
        
        logger.info(f"✅ Токен бота успешно загружен, длина: {len(Config.BOT_TOKEN)} символов")
        
        # Создаем Application
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Добавляем job для автоматических задач
        job_queue = application.job_queue
        if job_queue:
            # Ежедневные задачи в 9:00
            job_queue.run_daily(daily_tasks, time=time(hour=9, minute=0))
            
            # Ежечасные задачи
            job_queue.run_repeating(hourly_tasks, interval=7200, first=10)  # 2 часа
            
            # Проверка просроченных заявок каждые 6 часов
            job_queue.run_repeating(check_overdue_requests, interval=21600, first=10)
        
        # Базовые команды
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("menu", show_main_menu))
        application.add_handler(CommandHandler("help", show_help))
        application.add_handler(CommandHandler("stats", show_user_statistics))
        application.add_handler(CommandHandler("backup", create_backup_command))
        application.add_handler(CommandHandler("complete", complete_request_command))
        
        # Обработчики команд взятия заявок
        application.add_handler(MessageHandler(
            filters.Regex(r'^/take_\d+$'), take_request_command
        ))
        
        # Обработчики админ-панелей отделов
        application.add_handler(MessageHandler(
            filters.Regex('^(💻 IT админ-панель|🔧 Механика админ-панель|⚡ Электрика админ-панель)$'),
            show_department_admin_panel
        ))
        
        # Обработчики новых заявок
        application.add_handler(MessageHandler(
            filters.Regex('^(🆕 Новые заявки IT|🆕 Новые заявки механики|🆕 Новые заявки электрики)$'),
            show_new_requests
        ))
        
        # Обработчики поиска
        application.add_handler(MessageHandler(
            filters.Regex('^(🔍 Поиск по ID|📅 Поиск по дате|🏢 Поиск по отделу)$'),
            handle_text_messages
        ))
        
        # Обработчик создания заявки
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(🎯 Создать заявку)$'), start_request_creation),
            ],
            states={
                NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name)],
                PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone)],
                DEPARTMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, department)],
                SYSTEM_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, system_type)],
                PLOT: [MessageHandler(filters.TEXT & ~filters.COMMAND, plot)],
                OTHER_PLOT: [MessageHandler(filters.TEXT & ~filters.COMMAND, other_plot)],
                PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, problem)],
                URGENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, urgency)],
                PHOTO: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, photo),
                    MessageHandler(filters.PHOTO, photo)
                ],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(filters.Regex('^(🔙 Главное меню|🔙 Отменить)$'), cancel_request),
            ],
            allow_reentry=True
        )

        # Обработчик поиска заявок
        search_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(🔍 Поиск заявки)$'), search_requests_menu),
            ],
            states={
                SEARCH_REQUEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_search)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(filters.Regex('^(🔙 Главное меню)$'), cancel_request),
            ],
        )

        # Обработчики для кнопок подтверждения и редактирования
        application.add_handler(MessageHandler(filters.Regex('^(🚀 Отправить заявку)$'), confirm_request))
        application.add_handler(MessageHandler(filters.Regex('^(✏️ Исправить)$'), confirm_request))
        
        # Обработчик массовой рассылки
        broadcast_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(📢 Массовая рассылка)$'), start_broadcast),
            ],
            states={
                BROADCAST_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_audience)],
                BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_message)],
                BROADCAST_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_confirm)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(filters.Regex('^(🔙 Главное меню|🔙 В админ-панель)$'), cancel_request),
            ],
        )
        
        # Обработчик добавления админа
        add_admin_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(➕ Добавить админа)$'), add_admin_start),
            ],
            states={
                ADD_ADMIN_DEPARTMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_department)],
                ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_id)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(filters.Regex('^(🔙 Назад)$'), cancel_request),
            ],
        )
        
        # Обработчик удаления админа
        remove_admin_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(➖ Удалить админа)$'), remove_admin_start),
            ],
            states={
                REMOVE_ADMIN_DEPARTMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_admin_department)],
                REMOVE_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_admin_id)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(filters.Regex('^(🔙 Назад)$'), cancel_request),
            ],
        )
        
        application.add_handler(conv_handler)
        application.add_handler(search_handler)
        application.add_handler(broadcast_handler)
        application.add_handler(add_admin_handler)
        application.add_handler(remove_admin_handler)
        
        # Обработчик текстовых сообщений для меню
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
        
        logger.info("🤖 Бот заявок завода Контакт запущен с улучшениями!")
        logger.info(f"👑 Супер-администраторы: {Config.SUPER_ADMIN_IDS}")
        logger.info(f"👥 Администраторы по отделам: {Config.ADMIN_CHAT_IDS}")
        
        print("✅ Бот успешно запущен с улучшениями!")
        print("🎯 Новые функции:")
        print("   • 🔍 Расширенный поиск заявок")
        print("   • 📊 Персональная статистика") 
        print("   • 💾 Автоматические бэкапы")
        print("   • 🚨 Система срочных уведомлений")
        print("   • 📈 Ежедневные отчеты")
        print("🤖 Бот заявок завода Контакт работает...")
        print("⏹️ Нажмите Ctrl+C для остановки")
        
        application.run_polling()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
        print(f"❌ Критическая ошибка: {e}")

if __name__ == '__main__':
    main()
