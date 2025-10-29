import logging
import sqlite3
import os
import json
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackContext,
    CallbackQueryHandler,
    JobQueue,
)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Конфигурация приложения"""
    # Безопасное получение токена из переменных окружения
    BOT_TOKEN = os.getenv('BOT_TOKEN', '7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw')
    
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

# ==================== УЛУЧШЕННЫЕ КЛАВИАТУРЫ ====================

# 🎯 Главное меню пользователя
user_main_menu_keyboard = [
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['✏️ Редактировать заявку', 'ℹ️ Помощь']
]

# 👑 Главное меню администратора
admin_main_menu_keyboard = [
    ['👑 Админ-панель', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки']
]

# 👑 Главное меню супер-администратора
super_admin_main_menu_keyboard = [
    ['👑 Супер-админ', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки']
]

# 👑 Панель супер-администратора
super_admin_panel_keyboard = [
    ['📢 Массовая рассылка', '👥 Управление админами'],
    ['🏢 Все заявки', '📈 Общая статистика'],
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
    ['📋 Список админов', '🔙 В админ-панель']
]

# 🏢 Админ-панели по отделам
it_admin_panel_keyboard = [
    ['🆕 Новые заявки IT', '🔄 В работе IT'],
    ['✅ Выполненные IT', '📊 Статистика IT'],
    ['🔙 Главное меню']
]

mechanics_admin_panel_keyboard = [
    ['🆕 Новые заявки механики', '🔄 В работе механики'],
    ['✅ Выполненные механики', '📊 Статистика механики'],
    ['🔙 Главное меню']
]

electricity_admin_panel_keyboard = [
    ['🆕 Новые заявки электрики', '🔄 В работе электрики'],
    ['✅ Выполненные электрики', '📊 Статистика электрики'],
    ['🔙 Главное меню']
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
edit_field_keyboard = [['◀️ Назад к редактированию']]

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Инициализация базы данных"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
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
                        assigned_admin TEXT
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS statistics (
                        date TEXT PRIMARY KEY,
                        requests_count INTEGER DEFAULT 0,
                        completed_count INTEGER DEFAULT 0
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        created_at TEXT,
                        request_count INTEGER DEFAULT 0
                    )
                ''')
                
                # Добавляем индексы для производительности
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_department ON requests(department)')
                
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
                    INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at, request_count)
                    VALUES (?, ?, ?, ?, ?, COALESCE((SELECT request_count FROM users WHERE user_id = ?), 0) + 1)
                ''', (
                    user_data.get('user_id'),
                    user_data.get('username'),
                    user_data.get('first_name', ''),
                    user_data.get('last_name', ''),
                    datetime.now().isoformat(),
                    user_data.get('user_id')
                ))
                
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

    def get_requests_by_filter(self, filter_type: str = 'all', limit: int = 50) -> List[Dict]:
        """Получает заявки по фильтру"""
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
                
                status_filter = status_conditions.get(filter_type, "status IN ('new', 'in_progress')")
                
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
                logger.debug(f"Получено {len(requests)} заявок с фильтром '{filter_type}'")
                return requests
        except Exception as e:
            logger.error(f"Ошибка при получении заявок с фильтром '{filter_type}': {e}")
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
                
                if admin_comment and assigned_admin:
                    cursor.execute('''
                        UPDATE requests SET status = ?, admin_comment = ?, assigned_admin = ?, updated_at = ?
                        WHERE id = ?
                    ''', (status, admin_comment, assigned_admin, datetime.now().isoformat(), request_id))
                elif admin_comment:
                    cursor.execute('''
                        UPDATE requests SET status = ?, admin_comment = ?, updated_at = ?
                        WHERE id = ?
                    ''', (status, admin_comment, datetime.now().isoformat(), request_id))
                elif assigned_admin:
                    cursor.execute('''
                        UPDATE requests SET status = ?, assigned_admin = ?, updated_at = ?
                        WHERE id = ?
                    ''', (status, assigned_admin, datetime.now().isoformat(), request_id))
                else:
                    cursor.execute('''
                        UPDATE requests SET status = ?, updated_at = ? WHERE id = ?
                    ''', (status, datetime.now().isoformat(), request_id))
                
                conn.commit()
                logger.info(f"Статус заявки #{request_id} изменен на '{status}'")
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса заявки #{request_id}: {e}")
            raise

# Инициализация базы данных
db = Database(Config.DB_PATH)

# ==================== СОЗДАНИЕ ЗАЯВКИ ====================

def start_request_creation(update: Update, context: CallbackContext) -> int:
    """Начинает процесс создания заявки"""
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} начал создание заявки")
    
    context.user_data.clear()
    context.user_data.update({
        'user_id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name
    })
    
    update.message.reply_text(
        "🎯 *Создание новой заявки*\n\n"
        "📝 *Шаг 1 из 8*\n"
        "👤 Для начала укажите ваше *имя и фамилию*:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def name(update: Update, context: CallbackContext) -> int:
    name_text = update.message.text.strip()
    
    if not Validators.validate_name(name_text):
        update.message.reply_text(
            "❌ *Неверный формат имени!*\n\n"
            "👤 Имя должно содержать только буквы и быть от 2 до 50 символов.\n"
            "Пожалуйста, введите ваше имя еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME
    
    context.user_data['name'] = name_text
    update.message.reply_text(
        "📝 *Шаг 2 из 8*\n"
        "📞 *Укажите ваш контактный телефон:*\n\n"
        "📋 Примеры:\n"
        "• +7 999 123-45-67\n"
        "• 8 999 123-45-67\n"
        "• 79991234567",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    phone_text = update.message.text.strip()
    
    if not Validators.validate_phone(phone_text):
        update.message.reply_text(
            "❌ *Неверный формат телефона!*\n\n"
            "📞 Пожалуйста, введите номер в одном из форматов:\n"
            "• +7 999 123-45-67\n"
            "• 8 999 123-45-67\n"
            "• 79991234567\n\n"
            "Попробуйте еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHONE
    
    context.user_data['phone'] = phone_text
    update.message.reply_text(
        "📝 *Шаг 3 из 8*\n"
        "🏢 *Выберите отдел для заявки:*\n\n"
        "💻 *IT отдел* - компьютеры, программы, сети\n"
        "🔧 *Механика* - станки, оборудование, инструмент\n"
        "⚡ *Электрика* - проводка, освещение, автоматика",
        reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return DEPARTMENT

def department(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад в меню':
        return show_main_menu(update, context)
    
    valid_departments = ['💻 IT отдел', '🔧 Механика', '⚡ Электрика']
    if update.message.text not in valid_departments:
        update.message.reply_text(
            "❌ Пожалуйста, выберите отдел из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True)
        )
        return DEPARTMENT
    
    context.user_data['department'] = update.message.text
    
    # Выбираем соответствующую клавиатуру для типа проблем
    if update.message.text == '💻 IT отдел':
        problem_keyboard = it_systems_keyboard
        problem_description = "💻 *Выберите тип IT-проблемы:*"
    elif update.message.text == '🔧 Механика':
        problem_keyboard = mechanics_keyboard
        problem_description = "🔧 *Выберите тип механической проблемы:*"
    elif update.message.text == '⚡ Электрика':
        problem_keyboard = electricity_keyboard
        problem_description = "⚡ *Выберите тип электрической проблемы:*"
    
    update.message.reply_text(
        f"📝 *Шаг 4 из 8*\n{problem_description}",
        reply_markup=ReplyKeyboardMarkup(problem_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад к выбору отдела':
        update.message.reply_text(
            "🏢 *Выберите отдел для заявки:*",
            reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True)
        )
        return DEPARTMENT
    
    # Проверяем валидность выбора в зависимости от отдела
    department = context.user_data.get('department')
    if department == '💻 IT отдел':
        valid_systems = ['💻 Компьютеры', '🖨️ Принтеры', '🌐 Интернет', '📞 Телефония', 
                        '🔐 Программы', '📊 1С и Базы', '🎥 Оборудование', '⚡ Другое']
    elif department == '🔧 Механика':
        valid_systems = ['🔩 Станки и оборудование', '🛠️ Ручной инструмент', '⚙️ Гидравлика/Пневматика',
                        '🔧 Техническое обслуживание', '🚗 Транспортные средства', '🏗️ Производственные линии', '⚡ Другое (механика)']
    elif department == '⚡ Электрика':
        valid_systems = ['💡 Освещение', '🔌 Электропроводка', '⚡ Электрощитовое', '🔋 Источники питания',
                        '🎛️ Автоматика и КИП', '🛑 Аварийные системы', '🔧 Другое (электрика)']
    
    if update.message.text not in valid_systems:
        # Возвращаем соответствующую клавиатуру
        if department == '💻 IT отдел':
            keyboard = it_systems_keyboard
        elif department == '🔧 Механика':
            keyboard = mechanics_keyboard
        elif department == '⚡ Электрика':
            keyboard = electricity_keyboard
        
        update.message.reply_text(
            "❌ Пожалуйста, выберите тип проблемы из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return SYSTEM_TYPE
    
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 5 из 8*\n"
        "📍 *Выберите ваш участок или отдел:*",
        reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLOT

def plot(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        # Возвращаем к выбору типа проблемы с соответствующей клавиатурой
        department = context.user_data.get('department')
        if department == '💻 IT отдел':
            keyboard = it_systems_keyboard
            description = "💻 *Выберите тип IT-проблемы:*"
        elif department == '🔧 Механика':
            keyboard = mechanics_keyboard
            description = "🔧 *Выберите тип механической проблемы:*"
        elif department == '⚡ Электрика':
            keyboard = electricity_keyboard
            description = "⚡ *Выберите тип электрической проблемы:*"
        
        update.message.reply_text(
            description,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return SYSTEM_TYPE
    
    if update.message.text == '📋 Другой участок':
        update.message.reply_text(
            "📝 *Шаг 5 из 8*\n"
            "✏️ *Введите название вашего участка или отдела:*\n\n"
            "📋 Примеры:\n"
            "• Бухгалтерия\n"
            "• Отдел кадров\n"
            "• Производственный цех №1\n"
            "• Склад готовой продукции",
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return OTHER_PLOT
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 6 из 8*\n"
        "📖 *Опишите проблему подробно:*\n\n"
        "💡 *Примеры хороших описаний:*\n"
        "• 'Не включается компьютер, при нажатии кнопки питания ничего не происходит'\n"
        "• 'Станок ЧПУ издает нехарактерный шум при работе'\n"
        "• 'На участке мигает свет, периодически пропадает напряжение'\n\n"
        "⚠️ *Требования:* от 10 до 1000 символов",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def other_plot(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод пользовательского участка"""
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📍 *Выберите ваш участок или отдел:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 6 из 8*\n"
        "📖 *Опишите проблему подробно:*\n\n"
        "💡 Примеры хороших описаний:\n"
        "• 'Не включается компьютер, при нажатии кнопки питания ничего не происходит'\n"
        "• 'Станок ЧПУ издает нехарактерный шум при работе'\n"
        "• 'На участке мигает свет, периодически пропадает напряжение'\n\n"
        "⚠️ *Требования:* от 10 до 1000 символов",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    problem_text = update.message.text.strip()
    
    if not Validators.validate_problem(problem_text):
        update.message.reply_text(
            "❌ *Описание проблемы слишком короткое или длинное!*\n\n"
            "📝 Пожалуйста, опишите проблему подробнее (от 10 до 1000 символов):",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PROBLEM
    
    context.user_data['problem'] = problem_text
    update.message.reply_text(
        "📝 *Шаг 7 из 8*\n"
        "⏰ *Выберите срочность выполнения:*",
        reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📖 *Опишите проблему подробно:*",
            reply_markup=ReplyKeyboardRemove()
        )
        return PROBLEM
    
    # Проверка валидности выбора срочности
    valid_urgency = ['🔥 СРОЧНО (1-2 часа)', '⚠️ СЕГОДНЯ (до конца дня)', '💤 НЕ СРОЧНО (1-3 дня)']
    if update.message.text not in valid_urgency:
        update.message.reply_text(
            "❌ Пожалуйста, выберите срочность из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    
    context.user_data['urgency'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 8 из 8*\n"
        "📸 *Хотите добавить фото к заявке?*\n\n"
        "🖼️ Фото помогает быстрее понять проблему.\n"
        "📎 Можно отправить скриншот ошибки или фото оборудования",
        reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "⏰ *Выберите срочность выполнения:*",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    elif update.message.text == '📷 Добавить фото':
        update.message.reply_text(
            "📸 *Отправьте фото или скриншот:*\n\n"
            "📎 Можно отправить несколько фото по очереди",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHOTO
    elif update.message.text == '⏭️ Без фото':
        context.user_data['photo'] = None
        return show_request_summary(update, context)
    elif update.message.photo:
        context.user_data['photo'] = update.message.photo[-1].file_id
        update.message.reply_text(
            "✅ Фото добавлено!",
            reply_markup=ReplyKeyboardRemove()
        )
        return show_request_summary(update, context)
    else:
        update.message.reply_text(
            "❌ Пожалуйста, отправьте фото или используйте кнопки.",
            reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True)
        )
        return PHOTO

def show_request_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку заявки перед отправкой"""
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    update_summary(context)
    
    if context.user_data.get('editing_mode'):
        return edit_request_choice(update, context)
    else:
        summary_text = (
            f"{context.user_data['summary']}\n\n"
            "🎯 *Проверьте данные заявки:*\n"
            "✅ Все верно - отправляем заявку\n"
            "✏️ Нужно что-то исправить\n"
            "🔙 Можно начать заново"
        )
        
        if context.user_data.get('photo'):
            update.message.reply_photo(
                photo=context.user_data['photo'],
                caption=summary_text,
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                summary_text,
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        return ConversationHandler.END

def update_summary(context: CallbackContext) -> None:
    """Обновляет сводку заявки в user_data"""
    photo_status = "✅ Добавлено" if context.user_data.get('photo') else "❌ Отсутствует"
    
    summary = (
        f"📋 *Сводка заявки:*\n\n"
        f"👤 *Имя:* {context.user_data['name']}\n"
        f"📞 *Телефон:* `{context.user_data['phone']}`\n"
        f"🏢 *Отдел:* {context.user_data['department']}\n"
        f"🔧 *Тип проблемы:* {context.user_data['system_type']}\n"
        f"📍 *Участок:* {context.user_data['plot']}\n"
        f"📝 *Описание:* {context.user_data['problem']}\n"
        f"⏰ *Срочность:* {context.user_data['urgency']}\n"
        f"📸 *Фото/скриншот:* {photo_status}\n"
        f"🕒 *Время создания:* {context.user_data['timestamp']}"
    )
    
    context.user_data['summary'] = summary

def confirm_request(update: Update, context: CallbackContext) -> int:
    """Подтверждает и отправляет заявку"""
    if update.message.text == '🚀 Отправить заявку':
        user = update.message.from_user
        
        try:
            request_id = db.save_request(context.user_data)
            send_admin_notification(context, context.user_data, request_id)
            
            department_contacts = {
                '💻 IT отдел': '👨‍💼 *Специалист IT отдела свяжется с вами в ближайшее время.*',
                '🔧 Механика': '🔧 *Механик свяжется с вами для уточнения деталей.*',
                '⚡ Электрика': '⚡ *Электрик свяжется с вами для осмотра оборудования.*'
            }
            
            contact_text = department_contacts.get(context.user_data['department'], '👨‍💼 *Специалист свяжется с вами в ближайшее время.*')
            
            confirmation_text = (
                f"🎉 *Заявка #{request_id} успешно создана!*\n\n"
                f"📋 *Детали заявки:*\n"
                f"• 🏢 Отдел: {context.user_data['department']}\n"
                f"• 🔧 Тип: {context.user_data['system_type']}\n"
                f"• 📍 Участок: {context.user_data['plot']}\n"
                f"• ⏰ Срочность: {context.user_data['urgency']}\n\n"
                f"{contact_text}\n\n"
                f"_Спасибо за обращение!_ 💼"
            )
            
            if Config.is_super_admin(user.id):
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            elif Config.is_admin(user.id):
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            
            logger.info(f"Новая заявка #{request_id} от {user.username} в отдел {context.user_data['department']}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            error_message = (
                "❌ *Произошла ошибка при создании заявки.*\n\n"
                "⚠️ Пожалуйста, попробуйте позже или обратитесь в соответствующий отдел напрямую."
            )
            
            if Config.is_super_admin(user.id):
                update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            elif Config.is_admin(user.id):
                update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
        
        context.user_data.clear()
        return ConversationHandler.END
        
    elif update.message.text == '✏️ Исправить':
        context.user_data['editing_mode'] = True
        return edit_request_choice(update, context)
    
    elif update.message.text == '🔙 Отменить':
        return cancel_request(update, context)
    
    # На случай, если пришло неожиданное сообщение
    return ConversationHandler.END

def send_admin_notification(context: CallbackContext, user_data: Dict, request_id: int) -> None:
    """Отправляет уведомление администраторам о новой заявке"""
    notification_text = (
        f"🚨 *НОВАЯ ЗАЯВКА #{request_id}*\n\n"
        f"🏢 *Отдел:* {user_data.get('department')}\n"
        f"👤 *Пользователь:* @{user_data.get('username', 'N/A')}\n"
        f"📛 *Имя:* {user_data.get('name')}\n"
        f"📞 *Телефон:* `{user_data.get('phone')}`\n"
        f"🔧 *Тип проблемы:* {user_data.get('system_type')}\n"
        f"📍 *Участок:* {user_data.get('plot')}\n"
        f"⏰ *Срочность:* {user_data.get('urgency')}\n"
        f"📸 *Фото:* {'✅ Добавлено' if user_data.get('photo') else '❌ Отсутствует'}\n\n"
        f"📝 *Описание:* {user_data.get('problem')}\n\n"
        f"🕒 *Время создания:* {user_data.get('timestamp')}"
    )
    
    department = user_data.get('department')
    admin_ids = Config.get_admins_for_department(department)
    
    for admin_id in admin_ids:
        try:
            if user_data.get('photo'):
                context.bot.send_photo(
                    chat_id=admin_id,
                    photo=user_data['photo'],
                    caption=notification_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            logger.info(f"Уведомление отправлено администратору {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")
            # Попробуем отправить без форматирования
            try:
                context.bot.send_message(
                    chat_id=admin_id,
                    text=f"Новая заявка #{request_id} в отдел {department}"
                )
            except Exception as e2:
                logger.error(f"Критическая ошибка отправки админу {admin_id}: {e2}")

def cancel_request(update: Update, context: CallbackContext) -> int:
    """Отменяет создание заявки"""
    user_id = update.message.from_user.id
    logger.info(f"Пользователь {user_id} отменил создание заявки")
    
    if Config.is_super_admin(user_id):
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True)
        )
    elif Config.is_admin(user_id):
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True)
        )
    else:
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
    
    context.user_data.clear()
    return ConversationHandler.END

# ==================== РЕДАКТИРОВАНИЕ ЗАЯВОК ====================

def start_edit_request(update: Update, context: CallbackContext) -> int:
    """Начинает процесс редактирования заявки"""
    user_id = update.message.from_user.id
    logger.info(f"Пользователь {user_id} начал редактирование заявки")
    
    requests = db.get_user_requests(user_id, 20)
    
    if not requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок для редактирования.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    active_requests = [req for req in requests if req['status'] != 'completed']
    
    if not active_requests:
        update.message.reply_text(
            "✅ У вас нет активных заявок для редактирования.\n\n"
            "📋 Можно редактировать только заявки со статусом:\n"
            "• 🆕 Новая\n"
            "• 🔄 В работе",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    context.user_data['editable_requests'] = active_requests
    
    keyboard = []
    for req in active_requests:
        status_icon = '🆕' if req['status'] == 'new' else '🔄'
        button_text = f"{status_icon} #{req['id']} - {req['system_type']} ({req['department']})"
        keyboard.append([button_text])
    
    keyboard.append(['🔙 Главное меню'])
    
    update.message.reply_text(
        "✏️ *Редактирование заявки*\n\n"
        "📋 *Выберите заявку для редактирования:*\n"
        "Доступны только активные заявки:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return SELECT_REQUEST

def select_request_for_edit(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор заявки для редактирования"""
    text = update.message.text
    
    if text == '🔙 Главное меню':
        return cancel_edit(update, context)
    
    # Ищем выбранную заявку
    editable_requests = context.user_data.get('editable_requests', [])
    selected_request = None
    
    for req in editable_requests:
        expected_text = f"{'🆕' if req['status'] == 'new' else '🔄'} #{req['id']} - {req['system_type']} ({req['department']})"
        if text == expected_text:
            selected_request = req
            break
    
    if not selected_request:
        update.message.reply_text(
            "❌ Заявка не найдена. Пожалуйста, выберите заявку из списка:",
            reply_markup=ReplyKeyboardMarkup([['🔙 Главное меню']], resize_keyboard=True)
        )
        return SELECT_REQUEST
    
    # Сохраняем выбранную заявку в context
    context.user_data['editing_request_id'] = selected_request['id']
    context.user_data['editing_request_data'] = selected_request
    
    # Загружаем данные заявки в user_data для редактирования
    context.user_data.update({
        'name': selected_request['name'],
        'phone': selected_request['phone'],
        'department': selected_request['department'],
        'plot': selected_request['plot'],
        'system_type': selected_request['system_type'],
        'problem': selected_request['problem'],
        'urgency': selected_request['urgency'],
        'photo': selected_request['photo'],
        'user_id': selected_request['user_id'],
        'username': selected_request['username'],
        'editing_existing': True  # Флаг что редактируем существующую заявку
    })
    
    # Показываем сводку заявки и меню редактирования
    return show_edit_summary(update, context)

def show_edit_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку редактируемой заявки"""
    request_data = context.user_data
    request_id = context.user_data.get('editing_request_id')
    
    photo_status = "✅ Добавлено" if request_data.get('photo') else "❌ Отсутствует"
    
    summary = (
        f"✏️ *Редактирование заявки #{request_id}*\n\n"
        f"👤 *Имя:* {request_data['name']}\n"
        f"📞 *Телефон:* `{request_data['phone']}`\n"
        f"🏢 *Отдел:* {request_data['department']}\n"
        f"🔧 *Тип проблемы:* {request_data['system_type']}\n"
        f"📍 *Участок:* {request_data['plot']}\n"
        f"📝 *Описание:* {request_data['problem']}\n"
        f"⏰ *Срочность:* {request_data['urgency']}\n"
        f"📸 *Фото:* {photo_status}\n"
        f"🕒 *Последнее обновление:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    update.message.reply_text(
        f"{summary}\n\n"
        "🎯 *Выберите поле для редактирования:*",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return EDIT_CHOICE

def edit_request_choice(update: Update, context: CallbackContext) -> int:
    """Показывает меню выбора поля для редактирования (для создания заявки)"""
    summary = context.user_data.get('summary', '')
    
    update.message.reply_text(
        f"{summary}\n\n"
        "🎯 *Выберите поле для редактирования:*",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return EDIT_CHOICE

def handle_edit_choice(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор поля для редактирования"""
    choice = update.message.text
    context.user_data['editing_field'] = choice
    
    if choice == '👤 Имя':
        update.message.reply_text(
            f"✏️ *Введите новое имя:*\nТекущее: {context.user_data['name']}",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📞 Телефон':
        update.message.reply_text(
            f"✏️ *Введите новый телефон:*\nТекущий: {context.user_data['phone']}\n\n"
            "📋 Пример: +7 999 123-45-67",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '🏢 Отдел':
        update.message.reply_text(
            f"✏️ *Выберите новый отдел:*\nТекущий: {context.user_data['department']}",
            reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📍 Участок':
        update.message.reply_text(
            f"✏️ *Выберите новый участок:*\nТекущий: {context.user_data['plot']}",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '🔧 Тип проблемы':
        # Показываем соответствующую клавиатуру для текущего отдела
        department = context.user_data.get('department')
        if department == '💻 IT отдел':
            keyboard = it_systems_keyboard
        elif department == '🔧 Механика':
            keyboard = mechanics_keyboard
        elif department == '⚡ Электрика':
            keyboard = electricity_keyboard
        else:
            keyboard = it_systems_keyboard  # fallback
            
        update.message.reply_text(
            f"✏️ *Выберите новый тип проблемы:*\nТекущий: {context.user_data['system_type']}",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📝 Описание':
        update.message.reply_text(
            f"✏️ *Введите новое описание проблемы:*\nТекущее: {context.user_data['problem']}",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '⏰ Срочность':
        update.message.reply_text(
            f"✏️ *Выберите новую срочность:*\nТекущая: {context.user_data['urgency']}",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📷 Фото':
        photo_status = "есть фото" if context.user_data.get('photo') else "нет фото"
        update.message.reply_text(
            f"✏️ *Работа с фото:*\nТекущее: {photo_status}",
            reply_markup=ReplyKeyboardMarkup([
                ['📷 Добавить новое фото', '🗑️ Удалить фото'],
                ['◀️ Назад к редактированию']
            ], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '✅ Готово':
        if context.user_data.get('editing_existing'):
            return save_edited_request(update, context)
        else:
            return show_request_summary(update, context)
    
    else:
        update.message.reply_text(
            "❌ Пожалуйста, выберите поле для редактирования из меню.",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        return EDIT_CHOICE

def handle_edit_field(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод новых данных для поля"""
    editing_field = context.user_data.get('editing_field')
    text = update.message.text
    
    # Обработка кнопки "Назад"
    if text == '◀️ Назад к редактированию':
        if context.user_data.get('editing_existing'):
            return show_edit_summary(update, context)
        else:
            return edit_request_choice(update, context)
    
    # Обработка фото
    if update.message.photo:
        context.user_data['photo'] = update.message.photo[-1].file_id
        update.message.reply_text(
            "✅ Фото обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        if context.user_data.get('editing_existing'):
            return show_edit_summary(update, context)
        else:
            return edit_request_choice(update, context)
    
    # Обработка текстовых полей
    if editing_field == '👤 Имя':
        if not Validators.validate_name(text):
            update.message.reply_text(
                "❌ Неверный формат имени! Должно быть от 2 до 50 букв.",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        context.user_data['name'] = text
        update.message.reply_text(
            "✅ Имя обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📞 Телефон':
        if not Validators.validate_phone(text):
            update.message.reply_text(
                "❌ Неверный формат телефона! Попробуйте еще раз.",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        context.user_data['phone'] = text
        update.message.reply_text(
            "✅ Телефон обновлен!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '🏢 Отдел':
        if text in ['🔙 Назад', '🔙 Назад в меню']:
            if context.user_data.get('editing_existing'):
                return show_edit_summary(update, context)
            else:
                return edit_request_choice(update, context)
        
        valid_departments = ['💻 IT отдел', '🔧 Механика', '⚡ Электрика']
        if text not in valid_departments:
            update.message.reply_text(
                "❌ Пожалуйста, выберите отдел из предложенных вариантов:",
                reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        
        context.user_data['department'] = text
        update.message.reply_text(
            "✅ Отдел обновлен!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📍 Участок':
        if text in ['🔙 Назад', '🔙 Главное меню']:
            if context.user_data.get('editing_existing'):
                return show_edit_summary(update, context)
            else:
                return edit_request_choice(update, context)
        
        if text == '📋 Другой участок':
            update.message.reply_text(
                "✏️ *Введите название вашего участка:*",
                reply_markup=ReplyKeyboardMarkup([['◀️ Назад к редактированию']], resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['editing_other_plot'] = True
            return OTHER_PLOT
        
        context.user_data['plot'] = text
        update.message.reply_text(
            "✅ Участок обновлен!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '🔧 Тип проблемы':
        if text in ['🔙 Назад', '🔙 Назад к выбору отдела']:
            if context.user_data.get('editing_existing'):
                return show_edit_summary(update, context)
            else:
                return edit_request_choice(update, context)
        
        # Проверяем валидность выбора в зависимости от отдела
        department = context.user_data.get('department')
        if department == '💻 IT отдел':
            valid_systems = ['💻 Компьютеры', '🖨️ Принтеры', '🌐 Интернет', '📞 Телефония', 
                            '🔐 Программы', '📊 1С и Базы', '🎥 Оборудование', '⚡ Другое']
        elif department == '🔧 Механика':
            valid_systems = ['🔩 Станки и оборудование', '🛠️ Ручной инструмент', '⚙️ Гидравлика/Пневматика',
                            '🔧 Техническое обслуживание', '🚗 Транспортные средства', '🏗️ Производственные линии', '⚡ Другое (механика)']
        elif department == '⚡ Электрика':
            valid_systems = ['💡 Освещение', '🔌 Электропроводка', '⚡ Электрощитовое', '🔋 Источники питания',
                            '🎛️ Автоматика и КИП', '🛑 Аварийные системы', '🔧 Другое (электрика)']
        
        if text not in valid_systems:
            # Возвращаем соответствующую клавиатуру
            if department == '💻 IT отдел':
                keyboard = it_systems_keyboard
            elif department == '🔧 Механика':
                keyboard = mechanics_keyboard
            elif department == '⚡ Электрика':
                keyboard = electricity_keyboard
            
            update.message.reply_text(
                "❌ Пожалуйста, выберите тип проблемы из предложенных вариантов:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        
        context.user_data['system_type'] = text
        update.message.reply_text(
            "✅ Тип проблемы обновлен!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📝 Описание':
        if not Validators.validate_problem(text):
            update.message.reply_text(
                "❌ Описание должно быть от 10 до 1000 символов!",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        context.user_data['problem'] = text
        update.message.reply_text(
            "✅ Описание обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '⏰ Срочность':
        if text == '🔙 Назад':
            if context.user_data.get('editing_existing'):
                return show_edit_summary(update, context)
            else:
                return edit_request_choice(update, context)
        context.user_data['urgency'] = text
        update.message.reply_text(
            "✅ Срочность обновлена!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📷 Фото':
        if text == '📷 Добавить новое фото':
            update.message.reply_text(
                "📸 Отправьте новое фото:",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        elif text == '🗑️ Удалить фото':
            context.user_data['photo'] = None
            update.message.reply_text(
                "✅ Фото удалено!",
                reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
            )
        else:
            update.message.reply_text(
                "❌ Пожалуйста, выберите действие из меню.",
                reply_markup=ReplyKeyboardMarkup([
                    ['📷 Добавить новое фото', '🗑️ Удалить фото'],
                    ['◀️ Назад к редактированию']
                ], resize_keyboard=True)
            )
            return EDIT_FIELD
    
    if context.user_data.get('editing_existing'):
        return show_edit_summary(update, context)
    else:
        return edit_request_choice(update, context)

def save_edited_request(update: Update, context: CallbackContext) -> int:
    """Сохраняет отредактированную заявку"""
    request_id = context.user_data.get('editing_request_id')
    
    if not request_id:
        update.message.reply_text(
            "❌ Ошибка: не найдена заявка для сохранения.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    # Подготавливаем данные для обновления
    update_data = {
        'name': context.user_data.get('name'),
        'phone': context.user_data.get('phone'),
        'department': context.user_data.get('department'),
        'plot': context.user_data.get('plot'),
        'system_type': context.user_data.get('system_type'),
        'problem': context.user_data.get('problem'),
        'urgency': context.user_data.get('urgency'),
        'photo': context.user_data.get('photo')
    }
    
    try:
        # Обновляем заявку в базе данных
        success = db.update_request(request_id, update_data)
        
        if success:
            # Отправляем уведомление администраторам об изменении заявки
            send_edit_notification(context, request_id, update_data)
            
            update.message.reply_text(
                f"✅ *Заявка #{request_id} успешно обновлена!*\n\n"
                f"📋 Изменения сохранены в системе.\n"
                f"👨‍💼 Специалист увидит обновленные данные.",
                reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Заявка #{request_id} отредактирована пользователем {context.user_data.get('user_id')}")
        else:
            update.message.reply_text(
                "❌ *Произошла ошибка при сохранении изменений.*\n\nПожалуйста, попробуйте позже.",
                reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
    
    except Exception as e:
        logger.error(f"Ошибка при сохранении отредактированной заявки #{request_id}: {e}")
        update.message.reply_text(
            "❌ *Произошла ошибка при сохранении изменений.*\n\nПожалуйста, попробуйте позже.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    # Очищаем данные редактирования
    context.user_data.pop('editing_request_id', None)
    context.user_data.pop('editing_request_data', None)
    context.user_data.pop('editing_existing', None)
    context.user_data.pop('editing_field', None)
    
    return ConversationHandler.END

def send_edit_notification(context: CallbackContext, request_id: int, update_data: Dict) -> None:
    """Отправляет уведомление администраторам об изменении заявки"""
    notification_text = (
        f"✏️ *ЗАЯВКА #{request_id} ОБНОВЛЕНА*\n\n"
        f"👤 *Пользователь:* @{context.user_data.get('username', 'N/A')}\n"
        f"🏢 *Отдел:* {update_data['department']}\n"
        f"📛 *Имя:* {update_data['name']}\n"
        f"📞 *Телефон:* `{update_data['phone']}`\n"
        f"🔧 *Тип проблемы:* {update_data['system_type']}\n"
        f"📍 *Участок:* {update_data['plot']}\n"
        f"⏰ *Срочность:* {update_data['urgency']}\n"
        f"📸 *Фото:* {'✅ Добавлено' if update_data.get('photo') else '❌ Отсутствует'}\n\n"
        f"📝 *Описание:* {update_data['problem']}\n\n"
        f"🕒 *Время обновления:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    department = update_data['department']
    admin_ids = Config.get_admins_for_department(department)
    
    for admin_id in admin_ids:
        try:
            if update_data.get('photo'):
                context.bot.send_photo(
                    chat_id=admin_id,
                    photo=update_data['photo'],
                    caption=notification_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об изменении администратору {admin_id}: {e}")

def cancel_edit(update: Update, context: CallbackContext) -> int:
    """Отменяет редактирование заявки"""
    # Очищаем данные редактирования
    context.user_data.pop('editing_request_id', None)
    context.user_data.pop('editing_request_data', None)
    context.user_data.pop('editing_existing', None)
    context.user_data.pop('editing_field', None)
    context.user_data.pop('editable_requests', None)
    
    update.message.reply_text(
        "❌ Редактирование заявки отменено.",
        reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
    )
    
    return ConversationHandler.END

def other_plot_edit(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод пользовательского участка в режиме редактирования"""
    if update.message.text == '◀️ Назад к редактированию':
        if context.user_data.get('editing_existing'):
            return show_edit_summary(update, context)
        else:
            return edit_request_choice(update, context)
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "✅ Участок обновлен!",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
    )
    
    if context.user_data.get('editing_existing'):
        return show_edit_summary(update, context)
    else:
        return edit_request_choice(update, context)

# ==================== ОСНОВНЫЕ ФУНКЦИИ ====================

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Показывает главное меню в зависимости от роли пользователя"""
    user = update.message.from_user
    user_id = user.id
    
    if Config.is_super_admin(user_id):
        keyboard = super_admin_main_menu_keyboard
        welcome_text = (
            "👑 *Добро пожаловать, СУПЕР-АДМИНИСТРАТОР!*\n\n"
            "🎯 *Ваши возможности:*\n"
            "• 📢 Массовая рассылка всем отделам\n"
            "• 👥 Управление администраторами\n"
            "• 🏢 Просмотр всех заявок системы\n"
            "• 📊 Полная статистика по всем отделам\n\n"
            "🎯 *Выберите действие из меню:*"
        )
    elif Config.is_admin(user_id):
        # Получаем отделы, в которых пользователь является админом
        user_departments = []
        for department, admins in Config.ADMIN_CHAT_IDS.items():
            if user_id in admins:
                user_departments.append(department)
        
        keyboard = admin_main_menu_keyboard
        welcome_text = (
            f"👨‍💼 *Добро пожаловать, АДМИНИСТРАТОР!*\n\n"
            f"🏢 *Ваши отделы:* {', '.join(user_departments)}\n\n"
            f"🎯 *Ваши возможности:*\n"
            f"• 🏢 Доступ к админ-панелям ваших отделов\n"
            f"• 📊 Просмотр статистики по вашим отделам\n"
            f"• 🔄 Управление заявками ваших отделов\n\n"
            f"🎯 *Выберите действие из меню:*"
        )
    else:
        keyboard = user_main_menu_keyboard
        welcome_text = (
            "💼 *Добро пожаловать в сервис заявок!*\n\n"
            "🛠️ *Мы поможем с:*\n"
            "• 💻 IT проблемами - компьютеры, программы, сети\n"
            "• 🔧 Механическими неисправностями - станки, оборудование\n"
            "• ⚡ Электрическими вопросами - проводка, освещение\n\n"
            "🎯 *Выберите действие из меню ниже:*"
        )
    
    update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_my_requests(update: Update, context: CallbackContext) -> None:
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    
    if Config.is_super_admin(user_id):
        keyboard = super_admin_main_menu_keyboard
    elif Config.is_admin(user_id):
        keyboard = admin_main_menu_keyboard
    else:
        keyboard = user_main_menu_keyboard
    
    requests = db.get_user_requests(user_id, 50)
    
    if not requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок.\n\n"
            "🎯 Хотите создать первую заявку?",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return
    
    active_requests = [req for req in requests if req['status'] != 'completed']
    completed_requests = [req for req in requests if req['status'] == 'completed']
    
    if not active_requests and not completed_requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок.\n\n"
            "🎯 Хотите создать первую заявку?",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return
    
    if active_requests:
        update.message.reply_text(
            f"📋 *Ваши активные заявки ({len(active_requests)}):*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        for req in active_requests:
            status_icons = {
                'new': '🆕 НОВАЯ',
                'in_progress': '🔄 В РАБОТЕ', 
                'completed': '✅ ВЫПОЛНЕНА'
            }
            
            status_text = status_icons.get(req['status'], req['status'])
            
            request_text = (
                f"{status_icons.get(req['status'], '📋')} *Заявка #{req['id']}*\n"
                f"🏢 *Отдел:* {req['department']}\n"
                f"🔧 *Тип:* {req['system_type']}\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"🔄 *Статус:* {status_text}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}\n"
            )
            
            if req.get('assigned_admin') and req['status'] == 'in_progress':
                request_text += f"👨‍💼 *Исполнитель:* {req['assigned_admin']}\n"
            
            if req.get('admin_comment'):
                request_text += f"💬 *Комментарий:* {req['admin_comment']}\n"
            
            update.message.reply_text(request_text, parse_mode=ParseMode.MARKDOWN)
    
    if completed_requests:
        update.message.reply_text(
            f"✅ *История выполненных заявок ({len(completed_requests)}):*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        for req in completed_requests:
            request_text = (
                f"✅ *Заявка #{req['id']} - ВЫПОЛНЕНА*\n"
                f"🏢 *Отдел:* {req['department']}\n"
                f"🔧 *Тип проблемы:* {req['system_type']}\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem'][:100]}{'...' if len(req['problem']) > 100 else ''}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}\n"
                f"✅ *Завершена:* {req['updated_at'][:16] if req.get('updated_at') else req['created_at'][:16]}\n"
            )
            
            if req.get('assigned_admin'):
                request_text += f"👨‍💼 *Исполнитель:* {req['assigned_admin']}\n"
            
            if req.get('admin_comment'):
                request_text += f"💬 *Комментарий:* {req['admin_comment']}\n"
            
            update.message.reply_text(request_text, parse_mode=ParseMode.MARKDOWN)
    
    total_text = f"📊 *Итого:* {len(active_requests)} активных, {len(completed_requests)} выполненных заявок"
    update.message.reply_text(
        total_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в главном меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if Config.is_super_admin(user_id):
        if text == '👑 Супер-админ':
            return show_super_admin_panel(update, context)
        elif text == '📊 Статистика':
            return show_statistics(update, context)
        elif text == '🎯 Создать заявку':
            return start_request_creation(update, context)
        elif text == '📂 Мои заявки':
            return show_my_requests(update, context)
    elif Config.is_admin(user_id):
        if text == '👑 Админ-панель':
            return show_admin_department_select(update, context)
        elif text == '📊 Статистика':
            return show_statistics(update, context)
        elif text == '🎯 Создать заявку':
            return start_request_creation(update, context)
        elif text == '📂 Мои заявки':
            return show_my_requests(update, context)
    else:
        if text == '🎯 Создать заявку':
            return start_request_creation(update, context)
        elif text == '📂 Мои заявки':
            return show_my_requests(update, context)
        elif text == '✏️ Редактировать заявку':
            return start_edit_request(update, context)
        elif text == 'ℹ️ Помощь':
            return show_help(update, context)
    
    update.message.reply_text(
        "🎯 Пожалуйста, выберите действие из меню:",
        reply_markup=ReplyKeyboardMarkup(
            super_admin_main_menu_keyboard if Config.is_super_admin(user_id) else
            admin_main_menu_keyboard if Config.is_admin(user_id) else
            user_main_menu_keyboard, 
            resize_keyboard=True
        )
    )

def show_help(update: Update, context: CallbackContext) -> None:
    """Показывает справку"""
    help_text = (
        "💼 *Помощь по боту заявок*\n\n"
        "🎯 *Как создать заявку:*\n"
        "1. Нажмите 'Создать заявку'\n"
        "2. Выберите отдел (IT, Механика, Электрика)\n"
        "3. Заполните все шаги формы\n"
        "4. Проверьте данные и отправьте\n\n"
        "🏢 *Отделы:*\n"
        "• 💻 IT отдел - компьютеры, программы, сети\n"
        "• 🔧 Механика - станки, оборудование, инструмент\n"
        "• ⚡ Электрика - проводка, освещение, автоматика\n\n"
        "📋 *Просмотр заявок:*\n"
        "• 'Мои заявки' - все ваши заявки\n"
        "• Активные и выполненные раздельно\n\n"
        "✏️ *Редактирование:*\n"
        "• Можно редактировать активные заявки\n"
        "• Изменить любые данные до выполнения\n\n"
        "⏰ *Срочность:*\n"
        "• 🔥 СРОЧНО - 1-2 часа\n"
        "• ⚠️ СЕГОДНЯ - до конца дня\n"
        "• 💤 НЕ СРОЧНО - 1-3 дня\n\n"
        "📞 *Контакты отделов:*\n"
        "• IT отдел: +7 XXX XXX-XX-XX\n"
        "• Механика: +7 XXX XXX-XX-XX\n"
        "• Электрика: +7 XXX XXX-XX-XX"
    )
    
    user_id = update.message.from_user.id
    if Config.is_super_admin(user_id):
        keyboard = super_admin_main_menu_keyboard
    elif Config.is_admin(user_id):
        keyboard = admin_main_menu_keyboard
    else:
        keyboard = user_main_menu_keyboard
    
    update.message.reply_text(
        help_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== СУПЕР-АДМИН ПАНЕЛЬ ====================

def show_super_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает панель супер-администратора"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        update.message.reply_text("❌ У вас нет доступа к панели супер-администратора.")
        return show_main_menu(update, context)
    
    # Статистика для супер-админа
    all_requests = db.get_requests_by_filter('all', 1000)  # Получаем все заявки
    
    # Статистика по отделам
    department_stats = {}
    status_stats = {'new': 0, 'in_progress': 0, 'completed': 0}
    
    for req in all_requests:
        dept = req['department']
        status = req['status']
        
        department_stats[dept] = department_stats.get(dept, 0) + 1
        status_stats[status] = status_stats.get(status, 0) + 1
    
    # Статистика по админам
    admin_stats = {}
    for department, admins in Config.ADMIN_CHAT_IDS.items():
        for admin_id in admins:
            if admin_id not in Config.SUPER_ADMIN_IDS:
                admin_stats[admin_id] = admin_stats.get(admin_id, []) + [department]
    
    super_admin_text = (
        "👑 *ПАНЕЛЬ СУПЕР-АДМИНИСТРАТОРА*\n\n"
        f"📊 *Общая статистика системы:*\n"
        f"🆕 Новых: {status_stats['new']}\n"
        f"🔄 В работе: {status_stats['in_progress']}\n"
        f"✅ Выполненных: {status_stats['completed']}\n"
        f"📈 Всего заявок: {len(all_requests)}\n\n"
        f"🏢 *По отделам:*\n"
    )
    
    for dept, count in sorted(department_stats.items()):
        super_admin_text += f"• {dept}: {count}\n"
    
    super_admin_text += f"\n👥 *Администраторы системы:* {len(admin_stats)}\n"
    
    update.message.reply_text(
        super_admin_text,
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_super_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает меню супер-администратора"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        return show_main_menu(update, context)
    
    if text == '📢 Массовая рассылка':
        return start_broadcast(update, context)
    elif text == '👥 Управление админами':
        return show_admin_management(update, context)
    elif text == '🏢 Все заявки':
        return show_all_requests(update, context)
    elif text == '📈 Общая статистика':
        return show_complete_statistics(update, context)
    elif text == '🔙 Главное меню':
        return show_main_menu(update, context)

def show_admin_management(update: Update, context: CallbackContext) -> None:
    """Показывает управление администраторами"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        update.message.reply_text("❌ Только супер-администратор может управлять админами.")
        return
    
    admin_list_text = "👥 *СПИСОК АДМИНИСТРАТОРОВ*\n\n"
    
    for department, admins in Config.ADMIN_CHAT_IDS.items():
        admin_list_text += f"🏢 *{department}:*\n"
        for admin_id in admins:
            if admin_id in Config.SUPER_ADMIN_IDS:
                admin_list_text += f"  👑 Супер-админ: {admin_id}\n"
            else:
                admin_list_text += f"  👨‍💼 Админ: {admin_id}\n"
        admin_list_text += "\n"
    
    update.message.reply_text(
        admin_list_text,
        reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_all_requests(update: Update, context: CallbackContext) -> None:
    """Показывает все заявки системы"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        update.message.reply_text("❌ Только супер-администратор может просматривать все заявки.")
        return
    
    all_requests = db.get_requests_by_filter('all', 100)
    
    if not all_requests:
        update.message.reply_text(
            "📭 В системе пока нет заявок.",
            reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True)
        )
        return
    
    # Группируем по отделам
    departments = {}
    for req in all_requests:
        dept = req['department']
        if dept not in departments:
            departments[dept] = []
        departments[dept].append(req)
    
    for department, requests in departments.items():
        update.message.reply_text(
            f"🏢 *{department} - {len(requests)} заявок:*",
            parse_mode=ParseMode.MARKDOWN
        )
        
        for req in requests:
            show_request_for_admin(update, context, req)
    
    update.message.reply_text(
        f"📊 *Всего заявок в системе: {len(all_requests)}*",
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_complete_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает полную статистику системы"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        update.message.reply_text("❌ Только супер-администратор может просматривать полную статистику.")
        return
    
    all_requests = db.get_requests_by_filter('all', 1000)
    
    # Расширенная статистика
    department_stats = {}
    status_stats = {'new': 0, 'in_progress': 0, 'completed': 0}
    urgency_stats = {}
    system_type_stats = {}
    
    for req in all_requests:
        dept = req['department']
        status = req['status']
        urgency = req['urgency']
        system_type = req['system_type']
        
        department_stats[dept] = department_stats.get(dept, 0) + 1
        status_stats[status] = status_stats.get(status, 0) + 1
        urgency_stats[urgency] = urgency_stats.get(urgency, 0) + 1
        system_type_stats[system_type] = system_type_stats.get(system_type, 0) + 1
    
    stats_text = (
        "📈 *ПОЛНАЯ СТАТИСТИКА СИСТЕМЫ*\n\n"
        f"📊 *Общая статистика:*\n"
        f"• 🆕 Новых: {status_stats['new']}\n"
        f"• 🔄 В работе: {status_stats['in_progress']}\n"
        f"• ✅ Выполненных: {status_stats['completed']}\n"
        f"• 📈 Всего заявок: {len(all_requests)}\n\n"
    )
    
    stats_text += "🏢 *По отделам:*\n"
    for dept, count in sorted(department_stats.items()):
        stats_text += f"• {dept}: {count}\n"
    
    stats_text += "\n⏰ *По срочности:*\n"
    for urgency, count in sorted(urgency_stats.items()):
        stats_text += f"• {urgency}: {count}\n"
    
    stats_text += "\n🔧 *Популярные типы проблем:*\n"
    for system_type, count in sorted(system_type_stats.items(), key=lambda x: x[1], reverse=True)[:10]:
        stats_text += f"• {system_type}: {count}\n"
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_request_for_admin(update: Update, context: CallbackContext, req: Dict) -> None:
    """Показывает заявку для администратора"""
    status_icons = {
        'new': '🆕',
        'in_progress': '🔄',
        'completed': '✅'
    }
    
    request_text = (
        f"{status_icons.get(req['status'])} *Заявка #{req['id']}*\n"
        f"🏢 *Отдел:* {req['department']}\n"
        f"👤 *Клиент:* {req['name']}\n"
        f"📞 *Телефон:* `{req['phone']}`\n"
        f"📍 *Участок:* {req['plot']}\n"
        f"🔧 *Тип проблемы:* {req['system_type']}\n"
        f"⏰ *Срочность:* {req['urgency']}\n"
        f"📝 *Описание:* {req['problem']}\n"
        f"🔄 *Статус:* {req['status']}\n"
        f"🕒 *Создана:* {req['created_at'][:16]}"
    )
    
    if req.get('assigned_admin'):
        request_text += f"\n👨‍💼 *Исполнитель:* {req['assigned_admin']}"
    
    # Отправляем сообщение
    if req.get('photo'):
        update.message.reply_photo(
            photo=req['photo'],
            caption=request_text,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        update.message.reply_text(
            request_text,
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== МАССОВАЯ РАССЫЛКА ====================

def start_broadcast(update: Update, context: CallbackContext) -> int:
    """Начинает процесс массовой рассылки"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        update.message.reply_text("❌ Только супер-администратор может делать рассылку.")
        return ConversationHandler.END
    
    context.user_data['broadcast_data'] = {}
    
    update.message.reply_text(
        "📢 *МАССОВАЯ РАССЫЛКА*\n\n"
        "🎯 *Выберите аудиторию для рассылки:*\n"
        "• 👥 Все пользователи - всем кто пользовался ботом\n"
        "• 👨‍💼 Все админы - администраторам всех отделов\n"
        "• 🏢 Конкретный отдел - всем пользователям отдела\n\n"
        "_Можно отправить текст, фото или документ_",
        reply_markup=ReplyKeyboardMarkup(broadcast_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return BROADCAST_AUDIENCE

def handle_broadcast_audience(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор аудитории для рассылки"""
    audience = update.message.text
    context.user_data['broadcast_data']['audience'] = audience
    
    audience_names = {
        '📢 Всем пользователям': 'ВСЕМ ПОЛЬЗОВАТЕЛЯМ системы',
        '👥 Всем админам': 'ВСЕМ АДМИНИСТРАТОРАМ', 
        '💻 IT отдел': 'пользователям IT ОТДЕЛА',
        '🔧 Механика': 'пользователям МЕХАНИКИ',
        '⚡ Электрика': 'пользователям ЭЛЕКТРИКИ'
    }
    
    audience_name = audience_names.get(audience, audience)
    
    update.message.reply_text(
        f"📢 *Расслылка для: {audience_name}*\n\n"
        "💬 *Введите сообщение для рассылки:*\n\n"
        "📝 Можно использовать форматирование Markdown\n"
        "🖼️ Можно отправить фото с подписью\n"
        "📎 Можно отправить документ\n\n"
        "❌ *Отмена:* /cancel",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return BROADCAST_MESSAGE

def handle_broadcast_message(update: Update, context: CallbackContext) -> int:
    """Обрабатывает сообщение для рассылки"""
    broadcast_data = context.user_data['broadcast_data']
    
    if update.message.text:
        broadcast_data['message_type'] = 'text'
        broadcast_data['text'] = update.message.text
        broadcast_data['parse_mode'] = ParseMode.MARKDOWN
    elif update.message.photo:
        broadcast_data['message_type'] = 'photo'
        broadcast_data['photo'] = update.message.photo[-1].file_id
        broadcast_data['caption'] = update.message.caption
        broadcast_data['parse_mode'] = ParseMode.MARKDOWN
    elif update.message.document:
        broadcast_data['message_type'] = 'document'
        broadcast_data['document'] = update.message.document.file_id
        broadcast_data['caption'] = update.message.caption
        broadcast_data['parse_mode'] = ParseMode.MARKDOWN
    else:
        update.message.reply_text("❌ Поддерживаются только текст, фото или документы.")
        return BROADCAST_MESSAGE
    
    # Подсчет получателей
    recipients_count = calculate_recipients_count(broadcast_data['audience'])
    
    # Показ предпросмотра
    preview_text = (
        f"📢 *ПРЕДПРОСМОТР РАССЫЛКИ*\n\n"
        f"👥 *Аудитория:* {broadcast_data['audience']}\n"
        f"📊 *Получателей:* {recipients_count}\n\n"
        f"💬 *Сообщение:*\n"
    )
    
    if broadcast_data['message_type'] == 'text':
        preview_text += f"{broadcast_data['text']}\n\n"
        update.message.reply_text(
            preview_text,
            reply_markup=ReplyKeyboardMarkup([
                ['🚀 Начать рассылку', '✏️ Исправить'],
                ['❌ Отменить']
            ], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    elif broadcast_data['message_type'] == 'photo':
        preview_text += f"🖼️ Фото с подписью: {broadcast_data['caption'] or 'Без подписи'}\n\n"
        update.message.reply_photo(
            photo=broadcast_data['photo'],
            caption=preview_text,
            reply_markup=ReplyKeyboardMarkup([
                ['🚀 Начать рассылку', '✏️ Исправить'],
                ['❌ Отменить']
            ], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    elif broadcast_data['message_type'] == 'document':
        preview_text += f"📎 Документ: {broadcast_data['caption'] or 'Без описания'}\n\n"
        update.message.reply_document(
            document=broadcast_data['document'],
            caption=preview_text,
            reply_markup=ReplyKeyboardMarkup([
                ['🚀 Начать рассылку', '✏️ Исправить'],
                ['❌ Отменить']
            ], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    return BROADCAST_CONFIRM

def calculate_recipients_count(audience: str) -> int:
    """Подсчитывает количество получателей рассылки"""
    if audience == '📢 Всем пользователям':
        # Получаем всех уникальных пользователей из базы
        try:
            with sqlite3.connect(Config.DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(DISTINCT user_id) FROM requests')
                return cursor.fetchone()[0] or 0
        except:
            return 0
    elif audience == '👥 Всем админам':
        return len(Config.get_all_admins())
    elif audience == '💻 IT отдел':
        # Получаем пользователей IT отдела
        return get_users_count_by_department('💻 IT отдел')
    elif audience == '🔧 Механика':
        return get_users_count_by_department('🔧 Механика')
    elif audience == '⚡ Электрика':
        return get_users_count_by_department('⚡ Электрика')
    return 0

def get_users_count_by_department(department: str) -> int:
    """Подсчитывает пользователей по отделу"""
    try:
        with sqlite3.connect(Config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COUNT(DISTINCT user_id) FROM requests WHERE department = ?',
                (department,)
            )
            return cursor.fetchone()[0] or 0
    except:
        return 0

def get_users_by_department(department: str) -> List[int]:
    """Получает список пользователей по отделу"""
    try:
        with sqlite3.connect(Config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT DISTINCT user_id FROM requests WHERE department = ?',
                (department,)
            )
            return [row[0] for row in cursor.fetchall()]
    except:
        return []

def confirm_broadcast(update: Update, context: CallbackContext) -> int:
    """Подтверждает и выполняет рассылку"""
    if update.message.text == '🚀 Начать рассылку':
        return execute_broadcast(update, context)
    elif update.message.text == '✏️ Исправить':
        return start_broadcast(update, context)
    else:
        return cancel_broadcast(update, context)

def execute_broadcast(update: Update, context: CallbackContext) -> int:
    """Выполняет массовую рассылку"""
    broadcast_data = context.user_data['broadcast_data']
    audience = broadcast_data['audience']
    
    # Определяем получателей
    if audience == '📢 Всем пользователям':
        recipients = get_all_users()
    elif audience == '👥 Всем админам':
        recipients = Config.get_all_admins()
    elif audience == '💻 IT отдел':
        recipients = get_users_by_department('💻 IT отдел')
    elif audience == '🔧 Механика':
        recipients = get_users_by_department('🔧 Механика')
    elif audience == '⚡ Электрика':
        recipients = get_users_by_department('⚡ Электрика')
    else:
        recipients = []
    
    successful = 0
    failed = 0
    
    update.message.reply_text(
        f"🔄 *Начинаю рассылку...*\n\n"
        f"👥 Получателей: {len(recipients)}\n"
        f"⏳ Это может занять несколько минут...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Отправка сообщений
    for user_id in recipients:
        try:
            if broadcast_data['message_type'] == 'text':
                context.bot.send_message(
                    chat_id=user_id,
                    text=broadcast_data['text'],
                    parse_mode=broadcast_data.get('parse_mode')
                )
            elif broadcast_data['message_type'] == 'photo':
                context.bot.send_photo(
                    chat_id=user_id,
                    photo=broadcast_data['photo'],
                    caption=broadcast_data.get('caption'),
                    parse_mode=broadcast_data.get('parse_mode')
                )
            elif broadcast_data['message_type'] == 'document':
                context.bot.send_document(
                    chat_id=user_id,
                    document=broadcast_data['document'],
                    caption=broadcast_data.get('caption'),
                    parse_mode=broadcast_data.get('parse_mode')
                )
            successful += 1
            # Небольшая задержка чтобы не превысить лимиты Telegram
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
            failed += 1
    
    # Отчет о рассылке
    report_text = (
        f"✅ *РАССЫЛКА ЗАВЕРШЕНА!*\n\n"
        f"👥 *Аудитория:* {audience}\n"
        f"✅ *Успешно:* {successful}\n"
        f"❌ *Не удалось:* {failed}\n"
        f"📊 *Эффективность:* {successful/(successful+failed)*100:.1f}%\n\n"
        f"_Рассылка выполнена_ 📢"
    )
    
    update.message.reply_text(
        report_text,
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Логируем рассылку
    logger.info(f"Супер-админ {update.message.from_user.id} выполнил рассылку: {audience}, успешно: {successful}, ошибок: {failed}")
    
    context.user_data.clear()
    return ConversationHandler.END

def get_all_users() -> List[int]:
    """Получает список всех пользователей бота"""
    try:
        with sqlite3.connect(Config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT DISTINCT user_id FROM requests')
            return [row[0] for row in cursor.fetchall()]
    except:
        return []

def cancel_broadcast(update: Update, context: CallbackContext) -> int:
    """Отменяет рассылку"""
    context.user_data.clear()
    update.message.reply_text(
        "❌ Рассылка отменена.",
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True)
    )
    return ConversationHandler.END

# ==================== РАЗДЕЛЬНЫЕ АДМИН-ПАНЕЛИ ====================

def show_admin_department_select(update: Update, context: CallbackContext) -> None:
    """Показывает выбор админ-панели по отделам"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        update.message.reply_text("❌ У вас нет доступа к админ-панелям.")
        return show_main_menu(update, context)
    
    # Определяем к каким отделам есть доступ
    available_departments = []
    keyboard = []
    
    for department in ['💻 IT отдел', '🔧 Механика', '⚡ Электрика']:
        if Config.is_admin(user_id, department):
            available_departments.append(department)
            if department == '💻 IT отдел':
                keyboard.append(['💻 IT админ-панель'])
            elif department == '🔧 Механика':
                keyboard.append(['🔧 Механика админ-панель'])
            elif department == '⚡ Электрика':
                keyboard.append(['⚡ Электрика админ-панель'])
    
    keyboard.append(['🔙 Главное меню'])
    
    if not available_departments:
        update.message.reply_text("❌ У вас нет доступа ни к одной админ-панели.")
        return show_main_menu(update, context)
    
    update.message.reply_text(
        f"👨‍💼 *ВЫБОР АДМИН-ПАНЕЛИ*\n\n"
        f"🏢 *Доступные отделы:* {', '.join(available_departments)}\n\n"
        f"🎯 *Выберите админ-панель для работы:*",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_department_admin_panel(update: Update, context: CallbackContext, department: str) -> None:
    """Показывает админ-панель для конкретного отдела"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id, department):
        update.message.reply_text(f"❌ У вас нет доступа к админ-панели {department}.")
        return show_admin_department_select(update, context)
    
    # Получаем заявки для отдела
    all_requests = db.get_requests_by_filter('all', 1000)
    department_requests = [req for req in all_requests if req['department'] == department]
    
    status_stats = {'new': 0, 'in_progress': 0, 'completed': 0}
    for req in department_requests:
        status_stats[req['status']] += 1
    
    # Выбираем клавиатуру в зависимости от отдела
    if department == '💻 IT отдел':
        keyboard = it_admin_panel_keyboard
        dept_icon = '💻'
    elif department == '🔧 Механика':
        keyboard = mechanics_admin_panel_keyboard
        dept_icon = '🔧'
    elif department == '⚡ Электрика':
        keyboard = electricity_admin_panel_keyboard
        dept_icon = '⚡'
    
    admin_text = (
        f"{dept_icon} *АДМИН-ПАНЕЛЬ {department.upper()}*\n\n"
        f"📊 *Статистика отдела:*\n"
        f"🆕 Новых: {status_stats['new']}\n"
        f"🔄 В работе: {status_stats['in_progress']}\n"
        f"✅ Выполненных: {status_stats['completed']}\n"
        f"📈 Всего заявок: {len(department_requests)}\n\n"
        f"🎯 *Выберите раздел для работы:*"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_department_admin_panel(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в админ-панели отдела"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if text == '💻 IT админ-панель':
        return show_department_admin_panel(update, context, '💻 IT отдел')
    elif text == '🔧 Механика админ-панель':
        return show_department_admin_panel(update, context, '🔧 Механика')
    elif text == '⚡ Электрика админ-панель':
        return show_department_admin_panel(update, context, '⚡ Электрика')
    elif text.endswith('IT'):
        return handle_it_admin_requests(update, context, text)
    elif text.endswith('механики'):
        return handle_mechanics_admin_requests(update, context, text)
    elif text.endswith('электрики'):
        return handle_electricity_admin_requests(update, context, text)
    elif text == '🔙 Главное меню':
        return show_main_menu(update, context)

def handle_it_admin_requests(update: Update, context: CallbackContext, filter_type: str) -> None:
    """Обрабатывает заявки IT отдела"""
    return show_department_requests_by_filter(update, context, '💻 IT отдел', filter_type)

def handle_mechanics_admin_requests(update: Update, context: CallbackContext, filter_type: str) -> None:
    """Обрабатывает заявки механики"""
    return show_department_requests_by_filter(update, context, '🔧 Механика', filter_type)

def handle_electricity_admin_requests(update: Update, context: CallbackContext, filter_type: str) -> None:
    """Обрабатывает заявки электрики"""
    return show_department_requests_by_filter(update, context, '⚡ Электрика', filter_type)

def show_department_requests_by_filter(update: Update, context: CallbackContext, department: str, filter_text: str) -> None:
    """Показывает заявки отдела по фильтру"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id, department):
        update.message.reply_text(f"❌ У вас нет доступа к заявкам {department}.")
        return
    
    filter_map = {
        '🆕 Новые заявки IT': 'new',
        '🔄 В работе IT': 'in_progress', 
        '✅ Выполненные IT': 'completed',
        '🆕 Новые заявки механики': 'new',
        '🔄 В работе механики': 'in_progress',
        '✅ Выполненные механики': 'completed',
        '🆕 Новые заявки электрики': 'new',
        '🔄 В работе электрики': 'in_progress',
        '✅ Выполненные электрики': 'completed'
    }
    
    filter_type = filter_map.get(filter_text, 'new')
    all_requests = db.get_requests_by_filter(filter_type, 100)
    department_requests = [req for req in all_requests if req['department'] == department]
    
    if not department_requests:
        update.message.reply_text(
            f"📭 Заявки {department} с фильтром '{filter_text}' отсутствуют.",
            reply_markup=ReplyKeyboardMarkup(
                it_admin_panel_keyboard if department == '💻 IT отдел' else
                mechanics_admin_panel_keyboard if department == '🔧 Механика' else
                electricity_admin_panel_keyboard
            , resize_keyboard=True)
        )
        return
    
    update.message.reply_text(
        f"📋 {filter_text} ({len(department_requests)})",
        reply_markup=ReplyKeyboardMarkup(
            it_admin_panel_keyboard if department == '💻 IT отдел' else
            mechanics_admin_panel_keyboard if department == '🔧 Механика' else
            electricity_admin_panel_keyboard
        , resize_keyboard=True)
    )
    
    # Показываем заявки
    for req in department_requests:
        show_request_for_admin(update, context, req)

def show_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает статистику"""
    user_id = update.message.from_user.id
    
    if Config.is_super_admin(user_id):
        return show_complete_statistics(update, context)
    
    all_requests = db.get_requests_by_filter('all', 1000)
    
    # Если обычный админ - показываем только его отделы
    if Config.is_admin(user_id):
        user_departments = []
        for department, admins in Config.ADMIN_CHAT_IDS.items():
            if user_id in admins:
                user_departments.append(department)
        
        department_requests = [req for req in all_requests if req['department'] in user_departments]
        
        department_stats = {}
        status_stats = {'new': 0, 'in_progress': 0, 'completed': 0}
        
        for req in department_requests:
            dept = req['department']
            status = req['status']
            
            department_stats[dept] = department_stats.get(dept, 0) + 1
            status_stats[status] = status_stats.get(status, 0) + 1
        
        stats_text = (
            f"📊 *СТАТИСТИКА ВАШИХ ОТДЕЛОВ*\n\n"
            f"🏢 *Ваши отделы:* {', '.join(user_departments)}\n\n"
            f"📈 *Общая статистика:*\n"
            f"🆕 Новых: {status_stats['new']}\n"
            f"🔄 В работе: {status_stats['in_progress']}\n"
            f"✅ Выполненных: {status_stats['completed']}\n"
            f"📊 Всего заявок: {len(department_requests)}\n\n"
        )
        
        stats_text += "🏢 *По отделам:*\n"
        for dept, count in sorted(department_stats.items()):
            stats_text += f"• {dept}: {count}\n"
        
        if Config.is_super_admin(user_id):
            keyboard = super_admin_main_menu_keyboard
        elif Config.is_admin(user_id):
            keyboard = admin_main_menu_keyboard
        else:
            keyboard = user_main_menu_keyboard
            
        update.message.reply_text(
            stats_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        # Для обычных пользователей - простая статистика
        user_requests = db.get_user_requests(user_id)
        active_count = len([req for req in user_requests if req['status'] != 'completed'])
        completed_count = len([req for req in user_requests if req['status'] == 'completed'])
        
        stats_text = (
            f"📊 *ВАША СТАТИСТИКА*\n\n"
            f"📈 *Ваши заявки:*\n"
            f"🔄 Активных: {active_count}\n"
            f"✅ Выполненных: {completed_count}\n"
            f"📊 Всего: {len(user_requests)}\n\n"
            f"_Спасибо за использование нашего сервиса!_ 💼"
        )
        
        update.message.reply_text(
            stats_text,
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== ОСНОВНЫЕ ФУНКЦИИ БОТА ====================

def main() -> None:
    """Запускаем бота"""
    if Config.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        # Используем Application вместо Updater для новой версии
        from telegram.ext import Application
        
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
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
                EDIT_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_choice)],
                EDIT_FIELD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_field),
                    MessageHandler(filters.PHOTO, handle_edit_field)
                ],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(filters.Regex('^(🔙 Главное меню|🔙 Отменить)$'), cancel_request),
            ],
            allow_reentry=True
        )

        # Обработчик редактирования заявки
        edit_conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(✏️ Редактировать заявку)$'), start_edit_request),
            ],
            states={
                SELECT_REQUEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_request_for_edit)],
                EDIT_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_choice)],
                EDIT_FIELD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_field),
                    MessageHandler(filters.PHOTO, handle_edit_field)
                ],
                OTHER_PLOT: [MessageHandler(filters.TEXT & ~filters.COMMAND, other_plot_edit)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_edit),
                MessageHandler(filters.Regex('^(🔙 Главное меню)$'), cancel_edit),
            ],
            allow_reentry=True
        )

        # Обработчик массовой рассылки
        broadcast_conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(📢 Массовая рассылка)$'), start_broadcast),
            ],
            states={
                BROADCAST_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_audience)],
                BROADCAST_MESSAGE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_message),
                    MessageHandler(filters.PHOTO, handle_broadcast_message),
                    MessageHandler(filters.Document.ALL, handle_broadcast_message)
                ],
                BROADCAST_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_broadcast)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_broadcast),
                MessageHandler(filters.Regex('^(❌ Отменить|🔙 В админ-панель)$'), cancel_broadcast),
            ],
            allow_reentry=True
        )

        # Регистрируем обработчики
        application.add_handler(CommandHandler('start', show_main_menu))
        application.add_handler(CommandHandler('menu', show_main_menu))
        application.add_handler(CommandHandler('help', show_help))
        application.add_handler(CommandHandler('statistics', show_statistics))
        
        application.add_handler(conv_handler)
        application.add_handler(edit_conv_handler)
        application.add_handler(broadcast_conv_handler)
        
        # Обработчики для кнопок подтверждения и редактирования
        application.add_handler(MessageHandler(filters.Regex('^(🚀 Отправить заявку)$'), confirm_request))
        application.add_handler(MessageHandler(filters.Regex('^(✏️ Исправить)$'), confirm_request))
        
        # Обработчики главного меню
        application.add_handler(MessageHandler(filters.Regex(
            '^(📂 Мои заявки|👑 Админ-панель|📊 Статистика|ℹ️ Помощь|👑 Супер-админ)$'), 
            handle_main_menu
        ))
        
        # Обработчики супер-админ панели
        application.add_handler(MessageHandler(
            filters.Regex('^(📢 Массовая рассылка|👥 Управление админами|🏢 Все заявки|📈 Общая статистика)$'), 
            handle_super_admin_menu
        ))
        
        # Обработчики админ-панелей по отделам
        application.add_handler(MessageHandler(
            filters.Regex('^(💻 IT админ-панель|🔧 Механика админ-панель|⚡ Электрика админ-панель)$'), 
            handle_department_admin_panel
        ))
        
        application.add_handler(MessageHandler(
            filters.Regex('^(🆕 Новые заявки IT|🔄 В работе IT|✅ Выполненные IT|📊 Статистика IT)$'), 
            handle_it_admin_requests
        ))
        
        application.add_handler(MessageHandler(
            filters.Regex('^(🆕 Новые заявки механики|🔄 В работе механики|✅ Выполненные механики|📊 Статистика механики)$'), 
            handle_mechanics_admin_requests
        ))
        
        application.add_handler(MessageHandler(
            filters.Regex('^(🆕 Новые заявки электрики|🔄 В работе электрики|✅ Выполненные электрики|📊 Статистика электрики)$'), 
            handle_electricity_admin_requests
        ))

        # Запускаем бота
        logger.info("🤖 Бот заявок запущен с системой раздельных админ-панелей!")
        logger.info(f"👑 Супер-администраторы: {Config.SUPER_ADMIN_IDS}")
        logger.info(f"👥 Администраторы по отделам: {Config.ADMIN_CHAT_IDS}")
        
        application.run_polling()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
