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

# Инициализация базы данных
db = Database(Config.DB_PATH)

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} запустил бота")
    
    # ДОБАВЛЕНА ПОДПИСЬ "завод Контакт"
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

# ==================== СОЗДАНИЕ ЗАЯВКИ ====================

async def start_request_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
    
    # ДОБАВЛЕНА КНОПКА НАЗАД
    await update.message.reply_text(
        "🎯 *Создание новой заявки*\n\n"
        "📝 *Шаг 1 из 8*\n"
        "👤 Для начала укажите ваше *имя и фамилию*:",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

async def name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ДОБАВЛЕНА ОБРАБОТКА КНОПКИ НАЗАД
    if update.message.text == '🔙 Назад':
        return await cancel_request(update, context)
    
    name_text = update.message.text.strip()
    
    if not Validators.validate_name(name_text):
        await update.message.reply_text(
            "❌ *Неверный формат имени!*\n\n"
            "👤 Имя должно содержать только буквы и быть от 2 до 50 символов.\n"
            "Пожалуйста, введите ваше имя еще раз:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME
    
    context.user_data['name'] = name_text
    await update.message.reply_text(
        "📝 *Шаг 2 из 8*\n"
        "📞 *Укажите ваш контактный телефон:*\n\n"
        "📋 Примеры:\n"
        "• +7 999 123-45-67\n"
        "• 8 999 123-45-67\n"
        "• 79991234567",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

async def phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ДОБАВЛЕНА ОБРАБОТКА КНОПКИ НАЗАД
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "👤 Введите ваше имя и фамилию:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True)
        )
        return NAME
    
    phone_text = update.message.text.strip()
    
    if not Validators.validate_phone(phone_text):
        await update.message.reply_text(
            "❌ *Неверный формат телефона!*\n\n"
            "📞 Пожалуйста, введите номер в одном из форматов:\n"
            "• +7 999 123-45-67\n"
            "• 8 999 123-45-67\n"
            "• 79991234567\n\n"
            "Попробуйте еще раз:",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHONE
    
    context.user_data['phone'] = phone_text
    await update.message.reply_text(
        "📝 *Шаг 3 из 8*\n"
        "🏢 *Выберите отдел для заявки:*\n\n"
        "💻 *IT отдел* - компьютеры, программы, сети\n"
        "🔧 *Механика* - станки, оборудование, инструмент\n"
        "⚡ *Электрика* - проводка, освещение, автоматика",
        reply_markup=ReplyKeyboardMarkup(department_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return DEPARTMENT

async def department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == '🔙 Назад в меню':
        return await cancel_request(update, context)
    
    valid_departments = ['💻 IT отдел', '🔧 Механика', '⚡ Электрика']
    if update.message.text not in valid_departments:
        await update.message.reply_text(
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
    
    await update.message.reply_text(
        f"📝 *Шаг 4 из 8*\n{problem_description}",
        reply_markup=ReplyKeyboardMarkup(problem_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

async def system_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == '🔙 Назад к выбору отдела':
        await update.message.reply_text(
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
        
        await update.message.reply_text(
            "❌ Пожалуйста, выберите тип проблемы из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return SYSTEM_TYPE
    
    context.user_data['system_type'] = update.message.text
    await update.message.reply_text(
        "📝 *Шаг 5 из 8*\n"
        "📍 *Выберите ваш участок или отдел:*",
        reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLOT

async def plot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        
        await update.message.reply_text(
            description,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return SYSTEM_TYPE
    
    if update.message.text == '📋 Другой участок':
        await update.message.reply_text(
            "📝 *Шаг 5 из 8*\n"
            "✏️ *Введите название вашего участка или отдела:*\n\n"
            "📋 Примеры:\n"
            "• Бухгалтерия\n"
            "• Отдел кадров\n"
            "• Производственный цех №1\n"
            "• Склад готовой продукции",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return OTHER_PLOT
    
    context.user_data['plot'] = update.message.text
    await update.message.reply_text(
        "📝 *Шаг 6 из 8*\n"
        "📖 *Опишите проблему подробно:*\n\n"
        "💡 *Примеры хороших описаний:*\n"
        "• 'Не включается компьютер, при нажатии кнопки питания ничего не происходит'\n"
        "• 'Станок ЧПУ издает нехарактерный шум при работе'\n"
        "• 'На участке мигает свет, периодически пропадает напряжение'\n\n"
        "⚠️ *Требования:* от 10 до 1000 символов",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

async def other_plot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод пользовательского участка"""
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "📍 *Выберите ваш участок или отдел:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    context.user_data['plot'] = update.message.text
    await update.message.reply_text(
        "📝 *Шаг 6 из 8*\n"
        "📖 *Опишите проблему подробно:*\n\n"
        "💡 Примеры хороших описаний:\n"
        "• 'Не включается компьютер, при нажатии кнопки питания ничего не происходит'\n"
        "• 'Станок ЧПУ издает нехарактерный шум при работе'\n"
        "• 'На участке мигает свет, периодически пропадает напряжение'\n\n"
        "⚠️ *Требования:* от 10 до 1000 символов",
        reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

async def problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ДОБАВЛЕНА ОБРАБОТКА КНОПКИ НАЗАД
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "📍 *Выберите ваш участок или отдел:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    problem_text = update.message.text.strip()
    
    if not Validators.validate_problem(problem_text):
        await update.message.reply_text(
            "❌ *Описание проблемы слишком короткое или длинное!*\n\n"
            "📝 Пожалуйста, опишите проблему подробнее (от 10 до 1000 символов):",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return PROBLEM
    
    context.user_data['problem'] = problem_text
    await update.message.reply_text(
        "📝 *Шаг 7 из 8*\n"
        "⏰ *Выберите срочность выполнения:*",
        reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return URGENCY

async def urgency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "📖 *Опишите проблему подробно:*",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True)
        )
        return PROBLEM
    
    # Проверка валидности выбора срочности
    valid_urgency = ['🔥 СРОЧНО (1-2 часа)', '⚠️ СЕГОДНЯ (до конца дня)', '💤 НЕ СРОЧНО (1-3 дня)']
    if update.message.text not in valid_urgency:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите срочность из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    
    context.user_data['urgency'] = update.message.text
    await update.message.reply_text(
        "📝 *Шаг 8 из 8*\n"
        "📸 *Хотите добавить фото к заявке?*\n\n"
        "🖼️ Фото помогает быстрее понять проблему.\n"
        "📎 Можно отправить скриншот ошибки или фото оборудования",
        reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHOTO

async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == '🔙 Назад':
        await update.message.reply_text(
            "⏰ *Выберите срочность выполнения:*",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    elif update.message.text == '📷 Добавить фото':
        await update.message.reply_text(
            "📸 *Отправьте фото или скриншот:*\n\n"
            "📎 Можно отправить несколько фото по очереди",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHOTO
    elif update.message.text == '⏭️ Без фото':
        context.user_data['photo'] = None
        return await show_request_summary(update, context)
    elif update.message.photo:
        context.user_data['photo'] = update.message.photo[-1].file_id
        await update.message.reply_text(
            "✅ Фото добавлено!",
            reply_markup=ReplyKeyboardRemove()
        )
        return await show_request_summary(update, context)
    else:
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте фото или используйте кнопки.",
            reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True)
        )
        return PHOTO

async def show_request_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает сводку заявки перед отправкой"""
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    await update_summary(context)
    
    if context.user_data.get('editing_mode'):
        return await edit_request_choice(update, context)
    else:
        summary_text = (
            f"{context.user_data['summary']}\n\n"
            "🎯 *Проверьте данные заявки:*\n"
            "✅ Все верно - отправляем заявку\n"
            "✏️ Нужно что-то исправить\n"
            "🔙 Можно начать заново"
        )
        
        if context.user_data.get('photo'):
            await update.message.reply_photo(
                photo=context.user_data['photo'],
                caption=summary_text,
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

async def update_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
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

async def confirm_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждает и отправляет заявку"""
    if update.message.text == '🚀 Отправить заявку':
        user = update.message.from_user
        
        try:
            request_id = db.save_request(context.user_data)
            await send_admin_notification(context, context.user_data, request_id)
            
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
                await update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            elif Config.is_admin(user.id):
                await update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
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
                await update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            elif Config.is_admin(user.id):
                await update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
        
        context.user_data.clear()
        return ConversationHandler.END
        
    elif update.message.text == '✏️ Исправить':
        context.user_data['editing_mode'] = True
        return await edit_request_choice(update, context)
    
    elif update.message.text == '🔙 Отменить':
        return await cancel_request(update, context)
    
    # На случай, если пришло неожиданное сообщение
    return ConversationHandler.END

async def send_admin_notification(context: ContextTypes.DEFAULT_TYPE, user_data: Dict, request_id: int) -> None:
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
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=user_data['photo'],
                    caption=notification_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            logger.info(f"Уведомление отправлено администратору {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")
            # Попробуем отправить без форматирования
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"Новая заявка #{request_id} в отдел {department}"
                )
            except Exception as e2:
                logger.error(f"Критическая ошибка отправки админу {admin_id}: {e2}")

async def cancel_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет создание заявки"""
    user_id = update.message.from_user.id
    logger.info(f"Пользователь {user_id} отменил создание заявки")
    
    if Config.is_super_admin(user_id):
        await update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True)
        )
    elif Config.is_admin(user_id):
        await update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True)
        )
    else:
        await update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
    
    context.user_data.clear()
    return ConversationHandler.END

async def edit_request_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Позволяет выбрать поле для редактирования"""
    await update.message.reply_text(
        "✏️ *Редактирование заявки*\n\n"
        "Выберите поле, которое хотите изменить:",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return EDIT_CHOICE

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
    if Config.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        # Создаем Application
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Базовые команды
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("menu", show_main_menu))
        application.add_handler(CommandHandler("help", show_help))
        
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
        
        application.add_handler(conv_handler)
        
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
