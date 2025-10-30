import logging
import sqlite3
import os
import json
import re
import time
import asyncio
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
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackContext,
    CallbackQueryHandler,
    ContextTypes,
)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Конфигурация приложения"""
    # Безопасное получение токена из переменных окружения
    BOT_TOKEN = os.getenv("7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnH")
    
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
                    return True
            return False
        except Exception as e:
            logger.error(f"Ошибка удаления админа: {e}")
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

# Состояния для управления админами
ADD_ADMIN_DEPARTMENT, ADD_ADMIN_ID, REMOVE_ADMIN_DEPARTMENT, REMOVE_ADMIN_ID = range(15, 19)

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
back_keyboard = [['🔙 Назад']]

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
                        assigned_admin TEXT,
                        assigned_at TEXT,
                        completed_at TEXT
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
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_assigned_at ON requests(assigned_at)')
                
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
                
                set_parts = []
                parameters = []
                for field, value in update_data.items():
                    set_parts.append(f"{field} = ?")
                    parameters.append(value)
                
                parameters.append(request_id)
                sql = f"UPDATE requests SET {', '.join(set_parts)} WHERE id = ?"
                cursor.execute(sql, parameters)
                
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
                
                return {
                    'total_requests': total_requests,
                    'status_stats': status_stats,
                    'department_stats': department_stats,
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

# Инициализация базы данных
db = Database(Config.DB_PATH)

# ==================== ОБРАБОТЧИКИ СОЗДАНИЯ ЗАЯВКИ ====================

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

async def name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод имени"""
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "❌ Создание заявки отменено",
            reply_markup=ReplyKeyboardMarkup(
                super_admin_main_menu_keyboard if Config.is_super_admin(update.message.from_user.id) else
                admin_main_menu_keyboard if Config.is_admin(update.message.from_user.id) else
                user_main_menu_keyboard, 
                resize_keyboard=True
            )
        )
        return ConversationHandler.END
    
    name = update.message.text.strip()
    
    if not Validators.validate_name(name):
        await update.message.reply_text(
            "❌ *Неверный формат имени!*\n\n"
            "👤 Пожалуйста, введите ваше ФИО (только буквы и пробелы, от 2 до 50 символов):\n\n"
            "💡 Пример: *Иванов Иван Иванович*",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME
    
    context.user_data['name'] = name
    
    await update.message.reply_text(
        "📞 *Шаг 2 из 8: Введите ваш номер телефона*\n\n"
        "💡 Пример: *+7 999 123-45-67* или *89991234567*",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

async def phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод телефона"""
    if update.message.text == '🔙 Назад':
        context.user_data.pop('name', None)
        await update.message.reply_text(
            "👤 Введите ваше ФИО:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True)
        )
        return NAME
    
    phone = update.message.text.strip()
    
    if not Validators.validate_phone(phone):
        await update.message.reply_text(
            "❌ *Неверный формат телефона!*\n\n"
            "📞 Пожалуйста, введите корректный номер телефона:\n\n"
            "💡 Пример: *+7 999 123-45-67* или *89991234567*",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHONE
    
    context.user_data['phone'] = phone
    
    await update.message.reply_text(
        "🏢 *Шаг 3 из 8: Выберите отдел*\n\n"
        "💻 *IT отдел* - компьютеры, программы, сети\n"
        "🔧 *Механика* - станки, оборудование, инструмент\n"
        "⚡ *Электрика* - проводка, освещение, электрощиты",
        reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return DEPARTMENT

async def department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор отдела"""
    if update.message.text == '🔙 Назад':
        context.user_data.pop('phone', None)
        await update.message.reply_text(
            "📞 Введите ваш номер телефона:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True)
        )
        return PHONE
    
    if update.message.text == '🔙 Назад в меню':
        await cancel_request(update, context)
        return ConversationHandler.END
    
    valid_departments = ['💻 IT отдел', '🔧 Механика', '⚡ Электрика']
    if update.message.text not in valid_departments:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите отдел из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True)
        )
        return DEPARTMENT
    
    context.user_data['department'] = update.message.text
    
    # Показываем соответствующую клавиатуру для типа проблемы
    if update.message.text == '💻 IT отдел':
        await update.message.reply_text(
            "💻 *Шаг 4 из 8: Выберите тип IT проблемы*\n\n"
            "💻 *Компьютеры* - ПК, ноутбуки, рабочие места\n"
            "🖨️ *Принтеры* - Печать, сканирование, МФУ\n"
            "🌐 *Интернет* - Сеть, Wi-Fi, подключение\n"
            "📞 *Телефония* - Телефоны, IP-телефония\n"
            "🔐 *Программы* - ПО, лицензии, установка\n"
            "📊 *1С и Базы* - 1С, базы данных\n"
            "🎥 *Оборудование* - Камеры, серверы\n"
            "⚡ *Другое* - Другие IT вопросы",
            reply_markup=ReplyKeyboardMarkup(it_systems_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    elif update.message.text == '🔧 Механика':
        await update.message.reply_text(
            "🔧 *Шаг 4 из 8: Выберите тип механической проблемы*\n\n"
            "🔩 *Станки и оборудование* - Производственные станки\n"
            "🛠️ *Ручной инструмент* - Инструменты, дрели, шуруповерты\n"
            "⚙️ *Гидравлика/Пневматика* - Насосы, компрессоры\n"
            "🔧 *Техническое обслуживание* - Плановый ремонт\n"
            "🚗 *Транспортные средства* - Погрузчики, тележки\n"
            "🏗️ *Производственные линии* - Конвейеры, линии\n"
            "⚡ *Другое (механика)* - Другие механические вопросы",
            reply_markup=ReplyKeyboardMarkup(mechanics_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    elif update.message.text == '⚡ Электрика':
        await update.message.reply_text(
            "⚡ *Шаг 4 из 8: Выберите тип электрической проблемы*\n\n"
            "💡 *Освещение* - Лампы, светильники, выключатели\n"
            "🔌 *Электропроводка* - Кабели, розетки, проводка\n"
            "⚡ *Электрощитовое* - Щиты, автоматы, УЗО\n"
            "🔋 *Источники питания* - ИБП, стабилизаторы\n"
            "🎛️ *Автоматика и КИП* - Датчики, контроллеры\n"
            "🛑 *Аварийные системы* - Аварийное освещение\n"
            "🔧 *Другое (электрика)* - Другие электрические вопросы",
            reply_markup=ReplyKeyboardMarkup(electricity_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    return SYSTEM_TYPE

async def system_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор типа проблемы"""
    if update.message.text == '🔙 Назад':
        context.user_data.pop('department', None)
        await update.message.reply_text(
            "🏢 Выберите отдел:",
            reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True)
        )
        return DEPARTMENT
    
    if update.message.text == '🔙 Назад к выбору отдела':
        await department(update, context)
        return DEPARTMENT
    
    context.user_data['system_type'] = update.message.text
    
    await update.message.reply_text(
        "📍 *Шаг 5 из 8: Выберите участок*\n\n"
        "🏢 *Центральный офис* - Административное здание\n"
        "🏭 *Производство* - Производственные цеха\n"
        "📦 *Складской комплекс* - Склады, зоны хранения\n"
        "🛒 *Торговый зал* - Торговые площади\n"
        "💻 *Удаленные рабочие места* - Домашние офисы\n"
        "📋 *Другой участок* - Другое местоположение",
        reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLOT

async def plot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор участка"""
    if update.message.text == '🔙 Назад':
        context.user_data.pop('system_type', None)
        await update.message.reply_text(
            f"🔧 Выберите тип проблемы для {context.user_data.get('department')}:",
            reply_markup=ReplyKeyboardMarkup(
                it_systems_keyboard if context.user_data.get('department') == '💻 IT отдел' else
                mechanics_keyboard if context.user_data.get('department') == '🔧 Механика' else
                electricity_keyboard,
                resize_keyboard=True
            )
        )
        return SYSTEM_TYPE
    
    if update.message.text == '📋 Другой участок':
        await update.message.reply_text(
            "📍 *Введите название вашего участка:*\n\n"
            "💡 Пример: *Цех №5, Склад запчастей, Офис бухгалтерии*",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return OTHER_PLOT
    
    valid_plots = ['🏢 Центральный офис', '🏭 Производство', '📦 Складской комплекс', '🛒 Торговый зал', '💻 Удаленные рабочие места']
    if update.message.text not in valid_plots:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите участок из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    context.user_data['plot'] = update.message.text
    
    await update.message.reply_text(
        "📝 *Шаг 6 из 8: Опишите проблему*\n\n"
        "✍️ *Подробно опишите что случилось:*\n\n"
        "💡 *Пример хорошего описания:*\n"
        "• *Что произошло?* - Не включается компьютер\n"
        "• *Когда началось?* - Сегодня утром\n"
        "• *Какие симптомы?* - Не горит индикатор, нет звука\n"
        "• *Что уже пробовали?* - Проверил розетку, кабель\n\n"
        "⚠️ *Минимум 10 символов, максимум 1000*",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

async def other_plot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод другого участка"""
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "📍 Выберите участок:",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    plot_name = update.message.text.strip()
    if len(plot_name) < 2 or len(plot_name) > 100:
        await update.message.reply_text(
            "❌ *Название участка должно быть от 2 до 100 символов*\n\n"
            "📍 Введите название участка:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return OTHER_PLOT
    
    context.user_data['plot'] = f"📋 {plot_name}"
    
    await update.message.reply_text(
        "📝 *Шаг 6 из 8: Опишите проблему*\n\n"
        "✍️ Подробно опишите что случилось:",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

async def problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод описания проблемы"""
    if update.message.text == '🔙 Назад':
        context.user_data.pop('plot', None)
        await update.message.reply_text(
            "📍 Выберите участок:",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    problem_text = update.message.text.strip()
    
    if not Validators.validate_problem(problem_text):
        await update.message.reply_text(
            "❌ *Описание проблемы слишком короткое или длинное!*\n\n"
            "📝 Пожалуйста, опишите проблему подробно (от 10 до 1000 символов):\n\n"
            "💡 *Пример:* 'Не включается компьютер в бухгалтерии. Не горит индикатор питания, "
            "проверил розетку - напряжение есть. Проблема с сегодняшнего утра.'",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return PROBLEM
    
    context.user_data['problem'] = problem_text
    
    await update.message.reply_text(
        "⏰ *Шаг 7 из 8: Выберите срочность*\n\n"
        "🔥 *СРОЧНО (1-2 часа)* - Критическая проблема, остановка производства\n"
        "⚠️ *СЕГОДНЯ (до конца дня)* - Важная проблема, влияет на работу\n"
        "💤 *НЕ СРОЧНО (1-3 дня)* - Плановая работа, не срочные вопросы",
        reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return URGENCY

async def urgency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор срочности"""
    if update.message.text == '🔙 Назад':
        context.user_data.pop('problem', None)
        await update.message.reply_text(
            "📝 Опишите проблему:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True)
        )
        return PROBLEM
    
    valid_urgency = ['🔥 СРОЧНО (1-2 часа)', '⚠️ СЕГОДНЯ (до конца дня)', '💤 НЕ СРОЧНО (1-3 дня)']
    if update.message.text not in valid_urgency:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите срочность из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    
    context.user_data['urgency'] = update.message.text
    
    await update.message.reply_text(
        "📸 *Шаг 8 из 8: Добавить фото*\n\n"
        "🖼️ *Фото поможет быстрее понять проблему*\n\n"
        "💡 Вы можете:\n"
        "• 📷 Сделать и отправить фото\n"
        "• ⏭️ Продолжить без фото",
        reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHOTO

async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает добавление фото или пропуск"""
    if update.message.text == '🔙 Назад':
        context.user_data.pop('urgency', None)
        await update.message.reply_text(
            "⏰ Выберите срочность:",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    
    if update.message.text == '⏭️ Без фото':
        context.user_data['photo'] = None
        return await show_request_summary(update, context)
    
    if update.message.text == '📷 Добавить фото':
        await update.message.reply_text(
            "📸 *Отправьте фото проблемы:*\n\n"
            "💡 Сделайте четкое фото, чтобы было видно детали",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHOTO
    
    if update.message.photo:
        # Сохраняем самое большое фото
        photo_file = await update.message.photo[-1].get_file()
        photo_path = f"photos/photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{update.message.from_user.id}.jpg"
        
        # Создаем папку если нет
        os.makedirs('photos', exist_ok=True)
        
        await photo_file.download_to_drive(photo_path)
        context.user_data['photo'] = photo_path
        return await show_request_summary(update, context)
    
    await update.message.reply_text(
        "❌ Пожалуйста, отправьте фото или выберите действие:",
        reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True)
    )
    return PHOTO

async def show_request_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает сводку заявки для подтверждения"""
    user_data = context.user_data
    
    summary_text = (
        "📋 *ПРОВЕРЬТЕ ДАННЫЕ ЗАЯВКИ*\n\n"
        f"👤 *ФИО:* {user_data.get('name')}\n"
        f"📞 *Телефон:* {user_data.get('phone')}\n"
        f"🏢 *Отдел:* {user_data.get('department')}\n"
        f"🔧 *Тип проблемы:* {user_data.get('system_type')}\n"
        f"📍 *Участок:* {user_data.get('plot')}\n"
        f"⏰ *Срочность:* {user_data.get('urgency')}\n"
        f"📝 *Описание:* {user_data.get('problem')}\n"
        f"📷 *Фото:* {'✅ Есть' if user_data.get('photo') else '❌ Нет'}\n\n"
        "✅ *Всё верно?*"
    )
    
    if user_data.get('photo'):
        try:
            with open(user_data['photo'], 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=summary_text,
                    reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}")
            await update.message.reply_text(
                summary_text,
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
    else:
        await update.message.reply_text(
            summary_text,
            reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    return ConversationHandler.END

async def confirm_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Подтверждает и сохраняет заявку"""
    if update.message.text == '✏️ Исправить':
        await update.message.reply_text(
            "✏️ *Редактирование заявки*\n\n"
            "Начните создание заявки заново",
            reply_markup=ReplyKeyboardMarkup(
                super_admin_main_menu_keyboard if Config.is_super_admin(update.message.from_user.id) else
                admin_main_menu_keyboard if Config.is_admin(update.message.from_user.id) else
                user_main_menu_keyboard, 
                resize_keyboard=True
            )
        )
        return
    
    if update.message.text != '🚀 Отправить заявку':
        return
    
    try:
        # Сохраняем заявку в базу
        request_id = db.save_request(context.user_data)
        
        # Отправляем уведомление администраторам
        department = context.user_data.get('department')
        admin_ids = Config.get_admins_for_department(department)
        
        request_text = (
            f"🆕 *НОВАЯ ЗАЯВКА #{request_id}*\n\n"
            f"👤 *ФИО:* {context.user_data.get('name')}\n"
            f"📞 *Телефон:* {context.user_data.get('phone')}\n"
            f"🏢 *Отдел:* {department}\n"
            f"🔧 *Тип проблемы:* {context.user_data.get('system_type')}\n"
            f"📍 *Участок:* {context.user_data.get('plot')}\n"
            f"⏰ *Срочность:* {context.user_data.get('urgency')}\n"
            f"📝 *Описание:* {context.user_data.get('problem')}\n\n"
            f"🕒 *Создана:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        # Отправляем администраторам
        for admin_id in admin_ids:
            try:
                if context.user_data.get('photo'):
                    with open(context.user_data['photo'], 'rb') as photo:
                        await context.bot.send_photo(
                            chat_id=admin_id,
                            photo=photo,
                            caption=request_text,
                            parse_mode=ParseMode.MARKDOWN
                        )
                else:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=request_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
        
        # Подтверждение пользователю
        success_text = (
            f"✅ *Заявка #{request_id} успешно создана!*\n\n"
            f"🏢 *Отдел:* {department}\n"
            f"⏰ *Срочность:* {context.user_data.get('urgency')}\n"
            f"📞 *Ваш телефон:* {context.user_data.get('phone')}\n\n"
            f"💡 *Статус заявки можно отслеживать в разделе '📂 Мои заявки'*\n"
            f"⏳ *Максимальное время выполнения: 48 часов*"
        )
        
        await update.message.reply_text(
            success_text,
            reply_markup=ReplyKeyboardMarkup(
                super_admin_main_menu_keyboard if Config.is_super_admin(update.message.from_user.id) else
                admin_main_menu_keyboard if Config.is_admin(update.message.from_user.id) else
                user_main_menu_keyboard, 
                resize_keyboard=True
            ),
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Очищаем данные
        context.user_data.clear()
        
    except Exception as e:
        logger.error(f"Ошибка создания заявки: {e}")
        await update.message.reply_text(
            "❌ *Произошла ошибка при создании заявки*\n\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            reply_markup=ReplyKeyboardMarkup(
                super_admin_main_menu_keyboard if Config.is_super_admin(update.message.from_user.id) else
                admin_main_menu_keyboard if Config.is_admin(update.message.from_user.id) else
                user_main_menu_keyboard, 
                resize_keyboard=True
            ),
            parse_mode=ParseMode.MARKDOWN
        )

async def cancel_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет создание заявки"""
    user_id = update.message.from_user.id
    
    # Очищаем временные данные
    context.user_data.clear()
    
    await update.message.reply_text(
        "❌ Создание заявки отменено",
        reply_markup=ReplyKeyboardMarkup(
            super_admin_main_menu_keyboard if Config.is_super_admin(user_id) else
            admin_main_menu_keyboard if Config.is_admin(user_id) else
            user_main_menu_keyboard, 
            resize_keyboard=True
        )
    )
    return ConversationHandler.END

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} запустил бота")
    
    welcome_text = (
        "👋 *Добро пожаловать в систему заявок завода Контакт!*\n\n"
        "🛠️ *Мы поможем с:*\n"
        "• 💻 IT проблемами - компьютеры, программы, сети\n"
        "• 🔧 Механическими неисправностями - станки, оборудование\n"
        "• ⚡ Электрическими вопросами - проводка, освещение\n\n"
        "🎯 *Выберите действие из меню ниже:*"
    )
    
    if Config.is_super_admin(user.id):
        keyboard = super_admin_main_menu_keyboard
    elif Config.is_admin(user.id):
        keyboard = admin_main_menu_keyboard
    else:
        keyboard = user_main_menu_keyboard
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает главное меню"""
    user = update.message.from_user
    user_id = user.id
    
    if Config.is_super_admin(user_id):
        keyboard = super_admin_main_menu_keyboard
        welcome_text = "👑 *Добро пожаловать, СУПЕР-АДМИНИСТРАТОР завода Контакт!*"
    elif Config.is_admin(user_id):
        keyboard = admin_main_menu_keyboard
        welcome_text = "👨‍💼 *Добро пожаловать, АДМИНИСТРАТОР завода Контакт!*"
    else:
        keyboard = user_main_menu_keyboard
        welcome_text = "💼 *Добро пожаловать в сервис заявок завода Контакт!*"
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает справку"""
    help_text = (
        "💼 *Помощь по боту заявок завода Контакт*\n\n"
        "🎯 *Основные команды:*\n"
        "/start - начать работу\n"
        "/menu - главное меню\n" 
        "/help - эта справка\n\n"
        "📞 *Контакты поддержки:*\n"
        "По техническим вопросам обращайтесь к администратору"
    )
    
    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ФУНКЦИИ ДЛЯ КНОПОК ====================

async def show_my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    requests = db.get_user_requests(user_id)
    
    if not requests:
        await update.message.reply_text(
            "📭 *У вас пока нет заявок*\n\n"
            "Создайте первую заявку, нажав кнопку '🎯 Создать заявку'",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    for request in requests[:5]:  # Показываем последние 5 заявок
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }.get(request['status'], '❓')
        
        request_text = (
            f"📋 *Заявка #{request['id']}*\n"
            f"{status_emoji} *Статус:* {request['status']}\n"
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

async def show_all_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает все заявки (для супер-админа)"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    requests = db.get_all_requests(limit=10)
    
    if not requests:
        await update.message.reply_text("📭 Заявок пока нет")
        return
    
    for request in requests:
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }.get(request['status'], '❓')
        
        request_text = (
            f"📋 *Заявка #{request['id']}*\n"
            f"👤 *Пользователь:* @{request['username'] or 'N/A'}\n"
            f"{status_emoji} *Статус:* {request['status']}\n"
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

async def show_general_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает общую статистику"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    stats = db.get_statistics(days=7)
    
    if not stats:
        await update.message.reply_text("📊 Статистика временно недоступна")
        return
    
    stats_text = (
        f"📊 *ОБЩАЯ СТАТИСТИКА за {stats['period_days']} дней*\n\n"
        f"📈 *Всего заявок:* {stats['total_requests']}\n\n"
        f"📋 *По статусам:*\n"
    )
    
    for status, count in stats['status_stats'].items():
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄',
            'completed': '✅'
        }.get(status, '❓')
        stats_text += f"{status_emoji} {status}: {count}\n"
    
    stats_text += f"\n🏢 *По отделам:*\n"
    for department, count in stats['department_stats'].items():
        stats_text += f"• {department}: {count}\n"
    
    await update.message.reply_text(
        stats_text,
        parse_mode=ParseMode.MARKDOWN
    )

async def show_admin_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает управление админами"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    admin_text = "👥 *Управление администраторами*\n\n"
    
    for department, admins in Config.ADMIN_CHAT_IDS.items():
        admin_text += f"*{department}:*\n"
        for admin_id in admins:
            admin_text += f"• ID: {admin_id}\n"
        admin_text += "\n"
    
    admin_text += "👑 *Супер-администраторы:*\n"
    for admin_id in Config.SUPER_ADMIN_IDS:
        admin_text += f"• ID: {admin_id}\n"
    
    await update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начинает процесс массовой рассылки"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    await update.message.reply_text(
        "📢 *Массовая рассылка*\n\n"
        "Выберите аудиторию для рассылки:",
        reply_markup=ReplyKeyboardMarkup(broadcast_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return BROADCAST_AUDIENCE

async def broadcast_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор аудитории для рассылки"""
    audience = update.message.text
    context.user_data['broadcast_audience'] = audience
    
    audiences = {
        '📢 Всем пользователям': 'all_users',
        '👥 Всем админам': 'all_admins',
        '💻 IT отдел': 'it_department',
        '🔧 Механика': 'mechanics_department',
        '⚡ Электрика': 'electricity_department'
    }
    
    if audience not in audiences:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите аудиторию из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(broadcast_keyboard, resize_keyboard=True)
        )
        return BROADCAST_AUDIENCE
    
    await update.message.reply_text(
        f"📝 *Аудитория:* {audience}\n\n"
        "✍️ *Введите сообщение для рассылки:*\n\n"
        "💡 Можно использовать Markdown разметку",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return BROADCAST_MESSAGE

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает сообщение для рассылки"""
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "📢 Выберите аудиторию для рассылки:",
            reply_markup=ReplyKeyboardMarkup(broadcast_keyboard, resize_keyboard=True)
        )
        return BROADCAST_AUDIENCE
    
    context.user_data['broadcast_message'] = update.message.text
    audience = context.user_data['broadcast_audience']
    
    await update.message.reply_text(
        f"📢 *Подтверждение рассылки*\n\n"
        f"👥 *Аудитория:* {audience}\n"
        f"📝 *Сообщение:*\n{update.message.text}\n\n"
        f"✅ *Отправить сообщение?*",
        reply_markup=ReplyKeyboardMarkup([['✅ Отправить', '❌ Отменить']], resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return BROADCAST_CONFIRM

async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждает и отправляет рассылку"""
    if update.message.text == '❌ Отменить':
        await update.message.reply_text(
            "❌ Рассылка отменена.",
            reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True)
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    audience = context.user_data['broadcast_audience']
    message = context.user_data['broadcast_message']
    
    # Определяем получателей
    if audience == '📢 Всем пользователям':
        recipients = Config.get_all_users()
    elif audience == '👥 Всем админам':
        recipients = Config.get_all_admins()
    elif audience == '💻 IT отдел':
        recipients = Config.get_admins_for_department('💻 IT отдел')
    elif audience == '🔧 Механика':
        recipients = Config.get_admins_for_department('🔧 Механика')
    elif audience == '⚡ Электрика':
        recipients = Config.get_admins_for_department('⚡ Электрика')
    else:
        recipients = []
    
    sent_count = 0
    failed_count = 0
    
    for i, recipient_id in enumerate(recipients):
        try:
            await context.bot.send_message(
                chat_id=recipient_id,
                text=f"📢 *Рассылка:*\n\n{message}",
                parse_mode=ParseMode.MARKDOWN
            )
            sent_count += 1
            # Пауза между сообщениями чтобы избежать лимитов
            if i % 20 == 0:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения {recipient_id}: {e}")
            failed_count += 1
    
    await update.message.reply_text(
        f"✅ *Рассылка завершена!*\n\n"
        f"📤 Отправлено: {sent_count}\n"
        f"❌ Не отправлено: {failed_count}",
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data.clear()
    return ConversationHandler.END

# ==================== УПРАВЛЕНИЕ АДМИНАМИ ====================

async def add_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс добавления админа"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "➕ *Добавление администратора*\n\n"
        "🏢 *Выберите отдел:*",
        reply_markup=ReplyKeyboardMarkup([
            ['💻 IT отдел', '🔧 Механика'],
            ['⚡ Электрика', '🔙 Назад']
        ], resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return ADD_ADMIN_DEPARTMENT

async def add_admin_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор отдела для добавления админа"""
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "👥 Управление администраторами:",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    valid_departments = ['💻 IT отдел', '🔧 Механика', '⚡ Электрика']
    if update.message.text not in valid_departments:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите отдел из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup([
                ['💻 IT отдел', '🔧 Механика'],
                ['⚡ Электрика', '🔙 Назад']
            ], resize_keyboard=True)
        )
        return ADD_ADMIN_DEPARTMENT
    
    context.user_data['admin_department'] = update.message.text
    await update.message.reply_text(
        f"🏢 *Отдел:* {update.message.text}\n\n"
        "👤 *Введите ID пользователя для добавления:*\n\n"
        "💡 ID можно получить с помощью бота @userinfobot",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return ADD_ADMIN_ID

async def add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод ID админа"""
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "🏢 Выберите отдел:",
            reply_markup=ReplyKeyboardMarkup([
                ['💻 IT отдел', '🔧 Механика'],
                ['⚡ Электрика', '🔙 Назад']
            ], resize_keyboard=True)
        )
        return ADD_ADMIN_DEPARTMENT
    
    if not Validators.validate_user_id(update.message.text):
        await update.message.reply_text(
            "❌ *Неверный формат ID!*\n\n"
            "👤 Пожалуйста, введите числовой ID пользователя:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return ADD_ADMIN_ID
    
    admin_id = int(update.message.text)
    department = context.user_data['admin_department']
    
    if Config.add_admin(department, admin_id):
        await update.message.reply_text(
            f"✅ *Администратор добавлен!*\n\n"
            f"🏢 *Отдел:* {department}\n"
            f"👤 *ID:* {admin_id}",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            f"❌ *Не удалось добавить администратора*\n\n"
            f"Возможно, этот пользователь уже является администратором данного отдела.",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    context.user_data.clear()
    return ConversationHandler.END

async def remove_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс удаления админа"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "➖ *Удаление администратора*\n\n"
        "🏢 *Выберите отдел:*",
        reply_markup=ReplyKeyboardMarkup([
            ['💻 IT отдел', '🔧 Механика'],
            ['⚡ Электрика', '🔙 Назад']
        ], resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return REMOVE_ADMIN_DEPARTMENT

async def remove_admin_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор отдела для удаления админа"""
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "👥 Управление администраторами:",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    valid_departments = ['💻 IT отдел', '🔧 Механика', '⚡ Электрика']
    if update.message.text not in valid_departments:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите отдел из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup([
                ['💻 IT отдел', '🔧 Механика'],
                ['⚡ Электрика', '🔙 Назад']
            ], resize_keyboard=True)
        )
        return REMOVE_ADMIN_DEPARTMENT
    
    department = update.message.text
    admins = Config.get_admins_for_department(department)
    
    if len(admins) <= 1:  # Нельзя удалить последнего админа
        await update.message.reply_text(
            f"❌ *В отделе {department} только один администратор*\n\n"
            f"Нельзя удалить последнего администратора отдела.",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END
    
    context.user_data['admin_department'] = department
    
    admin_list = "\n".join([f"• ID: {admin_id}" for admin_id in admins if admin_id not in Config.SUPER_ADMIN_IDS])
    
    await update.message.reply_text(
        f"🏢 *Отдел:* {department}\n\n"
        f"👥 *Доступные для удаления администраторы:*\n{admin_list}\n\n"
        "👤 *Введите ID пользователя для удаления:*",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return REMOVE_ADMIN_ID

async def remove_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод ID админа для удаления"""
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "🏢 Выберите отдел:",
            reply_markup=ReplyKeyboardMarkup([
                ['💻 IT отдел', '🔧 Механика'],
                ['⚡ Электрика', '🔙 Назад']
            ], resize_keyboard=True)
        )
        return REMOVE_ADMIN_DEPARTMENT
    
    if not Validators.validate_user_id(update.message.text):
        await update.message.reply_text(
            "❌ *Неверный формат ID!*\n\n"
            "👤 Пожалуйста, введите числовой ID пользователя:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return REMOVE_ADMIN_ID
    
    admin_id = int(update.message.text)
    department = context.user_data['admin_department']
    
    if Config.remove_admin(department, admin_id):
        await update.message.reply_text(
            f"✅ *Администратор удален!*\n\n"
            f"🏢 *Отдел:* {department}\n"
            f"👤 *ID:* {admin_id}",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            f"❌ *Не удалось удалить администратора*\n\n"
            f"Возможно, этот пользователь не является администратором данного отдела или является супер-администратором.",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    context.user_data.clear()
    return ConversationHandler.END

async def show_admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список всех админов"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    admin_text = "📋 *СПИСОК АДМИНИСТРАТОРОВ*\n\n"
    
    for department, admins in Config.ADMIN_CHAT_IDS.items():
        admin_text += f"*{department}:*\n"
        for admin_id in admins:
            status = "👑 СУПЕР-АДМИН" if admin_id in Config.SUPER_ADMIN_IDS else "👨‍💼 АДМИН"
            admin_text += f"• ID: {admin_id} ({status})\n"
        admin_text += "\n"
    
    await update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== УВЕДОМЛЕНИЯ ПОЛЬЗОВАТЕЛЯМ ====================

async def notify_user_about_request_status(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
    """Уведомляет пользователя об изменении статуса заявки"""
    try:
        request = db.get_request(request_id)
        if not request:
            return
        
        user_id = request['user_id']
        
        if status == 'in_progress':
            message_text = (
                f"🔄 *Ваша заявка взята в работу!*\n\n"
                f"📋 *Заявка #{request_id}*\n"
                f"🏢 *Отдел:* {request['department']}\n"
                f"🔧 *Тип:* {request['system_type']}\n"
                f"👨‍💼 *Исполнитель:* {assigned_admin or 'Администратор'}\n"
                f"💬 *Комментарий:* {admin_comment or 'Без комментария'}\n\n"
                f"_Заявка будет выполнена в течение 48 часов_"
            )
        elif status == 'completed':
            message_text = (
                f"✅ *Ваша заявка выполнена!*\n\n"
                f"📋 *Заявка #{request_id}*\n"
                f"🏢 *Отдел:* {request['department']}\n"
                f"🔧 *Тип:* {request['system_type']}\n"
                f"👨‍💼 *Исполнитель:* {assigned_admin or 'Администратор'}\n"
                f"💬 *Комментарий:* {admin_comment or 'Без комментария'}\n\n"
                f"_Спасибо за обращение!_ 💼"
            )
        else:
            return
        
        await context.bot.send_message(
            chat_id=user_id,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"Уведомление отправлено пользователю {user_id} о заявке #{request_id}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю о заявке #{request_id}: {e}")

async def check_overdue_requests(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет просроченные заявки и уведомляет супер-админов"""
    try:
        overdue_requests = db.get_overdue_requests()
        
        if not overdue_requests:
            return
        
        for request in overdue_requests:
            overdue_time = datetime.now() - datetime.fromisoformat(request['assigned_at'])
            overdue_hours = int(overdue_time.total_seconds() / 3600)
            
            notification_text = (
                f"🚨 *ПРОСРОЧЕНА ЗАЯВКА!*\n\n"
                f"📋 *Заявка #{request['id']}*\n"
                f"🏢 *Отдел:* {request['department']}\n"
                f"🔧 *Тип:* {request['system_type']}\n"
                f"👤 *Пользователь:* @{request['username'] or 'N/A'}\n"
                f"👨‍💼 *Исполнитель:* {request['assigned_admin'] or 'Не назначен'}\n"
                f"⏰ *Просрочка:* {overdue_hours} часов\n"
                f"🕒 *Взята в работу:* {request['assigned_at'][:16]}\n\n"
                f"📝 *Описание:* {request['problem'][:200]}..."
            )
            
            # Отправляем уведомление всем супер-админам
            for super_admin_id in Config.SUPER_ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=super_admin_id,
                        text=notification_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления супер-админу {super_admin_id}: {e}")
        
        logger.info(f"Проверка просроченных заявок: найдено {len(overdue_requests)}")
        
    except Exception as e:
        logger.error(f"Ошибка при проверке просроченных заявок: {e}")

# ==================== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ====================

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений для меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Обработка основных кнопок меню
    if text == '🎯 Создать заявку':
        await start_request_creation(update, context)
    elif text == '📂 Мои заявки':
        await show_my_requests(update, context)
    elif text == '✏️ Редактировать заявку':
        await update.message.reply_text(
            "✏️ *Редактирование заявок*\n\n"
            "Для редактирования заявки создайте новую или обратитесь к администратору.",
            parse_mode=ParseMode.MARKDOWN
        )
    elif text == 'ℹ️ Помощь':
        await show_help(update, context)
    elif text == '👑 Админ-панель':
        await show_admin_panel(update, context)
    elif text == '📊 Статистика':
        await show_statistics_menu(update, context)
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
    elif text == '🔙 В админ-панель':
        await show_super_admin_panel(update, context)
    
    # Обработка кнопок управления админами
    elif text == '➕ Добавить админа':
        await add_admin_start(update, context)
    elif text == '➖ Удалить админа':
        await remove_admin_start(update, context)
    elif text == '📋 Список админов':
        await show_admin_list(update, context)
    
    # Обработка кнопок массовой рассылки
    elif text in ['📢 Всем пользователям', '👥 Всем админам', '💻 IT отдел', '🔧 Механика', '⚡ Электрика']:
        await broadcast_audience(update, context)
    
    # Обработка кнопок админ-панелей отделов
    elif text in ['💻 IT админ-панель', '🔧 Механика админ-панель', '⚡ Электрика админ-панель']:
        await show_department_admin_panel(update, context)
    
    # Обработка кнопок новых заявок
    elif text in ['🆕 Новые заявки IT', '🆕 Новые заявки механики', '🆕 Новые заявки электрики']:
        await show_new_requests(update, context)
    
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

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает админ-панель"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return
    
    # Если пользователь админ, но не супер-админ
    if Config.is_admin(user_id) and not Config.is_super_admin(user_id):
        await update.message.reply_text(
            "👑 *АДМИН-ПАНЕЛЬ*\n\n"
            "Выберите отдел для управления:",
            reply_markup=ReplyKeyboardMarkup(admin_department_select_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await show_super_admin_panel(update, context)

async def show_super_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает панель супер-администратора"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к панели супер-администратора.")
        return
    
    await update.message.reply_text(
        "👑 *ПАНЕЛЬ СУПЕР-АДМИНИСТРАТОРА завода Контакт*\n\n"
        "Доступные функции:",
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_department_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает админ-панель для конкретного отдела"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    department_map = {
        '💻 IT админ-панель': '💻 IT отдел',
        '🔧 Механика админ-панель': '🔧 Механика', 
        '⚡ Электрика админ-панель': '⚡ Электрика'
    }
    
    if text not in department_map:
        await update.message.reply_text("❌ Неверный выбор отдела")
        return
    
    department = department_map[text]
    
    # Проверяем права доступа
    if not Config.is_admin(user_id, department):
        await update.message.reply_text(f"❌ У вас нет доступа к админ-панели {department}")
        return
    
    # Показываем соответствующую клавиатуру
    keyboard_map = {
        '💻 IT отдел': it_admin_panel_keyboard,
        '🔧 Механика': mechanics_admin_panel_keyboard,
        '⚡ Электрика': electricity_admin_panel_keyboard
    }
    
    await update.message.reply_text(
        f"👑 *АДМИН-ПАНЕЛЬ {department}*\n\n"
        f"Управление заявками вашего отдела:",
        reply_markup=ReplyKeyboardMarkup(keyboard_map[department], resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_new_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает новые заявки отдела"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    department_map = {
        '🆕 Новые заявки IT': '💻 IT отдел',
        '🆕 Новые заявки механики': '🔧 Механика',
        '🆕 Новые заявки электрики': '⚡ Электрика'
    }
    
    if text not in department_map:
        return
    
    department = department_map[text]
    
    if not Config.is_admin(user_id, department):
        await update.message.reply_text(f"❌ У вас нет доступа к заявкам {department}")
        return
    
    requests = db.get_requests_by_filter(department=department, status='new', limit=10)
    
    if not requests:
        await update.message.reply_text(
            f"📭 *Новых заявок в {department} нет*",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    for request in requests:
        request_text = (
            f"🆕 *НОВАЯ ЗАЯВКА #{request['id']}*\n\n"
            f"👤 *ФИО:* {request['name']}\n"
            f"📞 *Телефон:* {request['phone']}\n"
            f"🔧 *Тип:* {request['system_type']}\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"⏰ *Срочность:* {request['urgency']}\n"
            f"📝 *Описание:* {request['problem'][:200]}...\n\n"
            f"🕒 *Создана:* {request['created_at'][:16]}\n\n"
            f"💡 *Используйте команду /take_{request['id']} чтобы взять в работу*"
        )
        
        if request.get('photo'):
            try:
                with open(request['photo'], 'rb') as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=request_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
            except:
                await update.message.reply_text(
                    request_text,
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            await update.message.reply_text(
                request_text,
                parse_mode=ParseMode.MARKDOWN
            )

async def show_statistics_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает меню статистики"""
    user_id = update.message.from_user.id
    
    if Config.is_super_admin(user_id):
        await show_general_statistics(update, context)
    else:
        stats = db.get_statistics(days=7)
        
        if stats:
            stats_text = (
                f"📊 *ВАША СТАТИСТИКА за 7 дней*\n\n"
                f"📈 *Всего заявок:* {stats['total_requests']}\n\n"
                f"📋 *Статусы ваших заявок:*\n"
            )
            
            for status, count in stats['status_stats'].items():
                status_emoji = {
                    'new': '🆕',
                    'in_progress': '🔄',
                    'completed': '✅'
                }.get(status, '❓')
                stats_text += f"{status_emoji} {status}: {count}\n"
            
            await update.message.reply_text(
                stats_text,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                "📊 *Статистика*\n\n"
                "У вас пока нет заявок для отображения статистики.",
                parse_mode=ParseMode.MARKDOWN
            )

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику (для обратной совместимости)"""
    await show_statistics_menu(update, context)

# ==================== ЗАПУСК БОТА ====================

def main() -> None:
    """Запускаем бота"""
    if not Config.BOT_TOKEN or Config.BOT_TOKEN == "7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        # Создаем Application
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Добавляем job для проверки просроченных заявок (каждые 6 часов)
        job_queue = application.job_queue
        if job_queue:
            job_queue.run_repeating(check_overdue_requests, interval=21600, first=10)  # 6 часов
        
        # Базовые команды
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("menu", show_main_menu))
        application.add_handler(CommandHandler("help", show_help))
        
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
        application.add_handler(broadcast_handler)
        application.add_handler(add_admin_handler)
        application.add_handler(remove_admin_handler)
        
        # Обработчик текстовых сообщений для меню
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
        
        logger.info("🤖 Бот заявок завода Контакт запущен!")
        logger.info(f"👑 Супер-администраторы: {Config.SUPER_ADMIN_IDS}")
        logger.info(f"👥 Администраторы по отделам: {Config.ADMIN_CHAT_IDS}")
        
        print("Бот запущен! Нажмите Ctrl+C для остановки")
        application.run_polling()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
