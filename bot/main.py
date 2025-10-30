import logging
import sqlite3
import os
import json
import re
import time
import asyncio
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from functools import lru_cache
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

# ==================== ЛОГИРОВАНИЕ ====================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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
        '💻 IT отдел': [5024165375, 123456789],
        '🔧 Механика': [5024165375, 987654321],
        '⚡ Электрика': [5024165375, 555555555]
    }
    
    DB_PATH = "requests.db"
    REQUEST_TIMEOUT_HOURS = 48

    @classmethod
    def is_super_admin(cls, user_id: int) -> bool:
        """Проверяет является ли пользователь супер-админом"""
        return user_id in cls.SUPER_ADMIN_IDS

    @classmethod
    def is_admin(cls, user_id: int, department: str = None) -> bool:
        """Проверяет является ли пользователь админом"""
        if cls.is_super_admin(user_id):
            return True
        if department:
            return user_id in cls.ADMIN_CHAT_IDS.get(department, [])
        for dept_admins in cls.ADMIN_CHAT_IDS.values():
            if user_id in dept_admins:
                return True
        return False

    @classmethod
    def get_admins_for_department(cls, department: str) -> List[int]:
        """Получает список админов для отдела"""
        return cls.ADMIN_CHAT_IDS.get(department, [])

    @classmethod
    def validate_config(cls):
        """Проверка конфигурации при запуске"""
        required_vars = ['BOT_TOKEN']
        missing_vars = [var for var in required_vars if not getattr(cls, var)]
        
        if missing_vars:
            raise ValueError(f"Отсутствуют обязательные переменные окружения: {missing_vars}")
        
        # Проверка структуры админов
        for dept, admins in cls.ADMIN_CHAT_IDS.items():
            if not isinstance(admins, list) or not all(isinstance(admin_id, int) for admin_id in admins):
                raise ValueError(f"Неверная структура админов для отдела: {dept}")
        
        logger.info("✅ Конфигурация успешно проверена")

# ==================== БЭКАП МЕНЕДЖЕР ====================

class BackupManager:
    """Менеджер бэкапов базы данных"""
    
    @staticmethod
    def create_backup():
        """Создает бэкап базы данных"""
        try:
            backup_dir = "backups"
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"{backup_dir}/requests_backup_{timestamp}.db"
            
            if os.path.exists(Config.DB_PATH):
                shutil.copy2(Config.DB_PATH, backup_file)
                logger.info(f"✅ Бэкап создан: {backup_file}")
                return backup_file
            else:
                logger.warning("⚠️ Файл базы данных не найден для бэкапа")
                return None
        except Exception as e:
            logger.error(f"❌ Ошибка создания бэкапа: {e}")
            return None
    
    @staticmethod
    def cleanup_old_backups(max_backups: int = 10):
        """Удаляет старые бэкапы"""
        try:
            backup_dir = "backups"
            if not os.path.exists(backup_dir):
                return
            
            backups = sorted(
                [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.db')],
                key=os.path.getctime
            )
            
            # Удаляем самые старые бэкапы
            while len(backups) > max_backups:
                old_backup = backups.pop(0)
                os.remove(old_backup)
                logger.info(f"🗑️ Удален старый бэкап: {old_backup}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка очистки старых бэкапов: {e}")

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

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.retry_count = 3
        self.retry_delay = 1
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
                # Добавляем таблицу для комментариев
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS request_comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id INTEGER,
                        admin_id INTEGER,
                        admin_name TEXT,
                        comment TEXT,
                        created_at TEXT,
                        FOREIGN KEY (request_id) REFERENCES requests (id)
                    )
                ''')
                conn.commit()
                logger.info("✅ База данных успешно инициализирована")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации базы данных: {e}")
            raise

    def execute_with_retry(self, query: str, params: tuple = ()):
        """Выполняет запрос с повторными попытками"""
        for attempt in range(self.retry_count):
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(query, params)
                    conn.commit()
                    return cursor
            except sqlite3.Error as e:
                logger.warning(f"⚠️ Попытка {attempt + 1} не удалась: {e}")
                if attempt == self.retry_count - 1:
                    raise e
                time.sleep(self.retry_delay)

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
                conn.commit()
                logger.info(f"✅ Заявка #{request_id} успешно сохранена")
                return request_id
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении заявки: {e}")
            raise

    @lru_cache(maxsize=100)
    def get_request_cached(self, request_id: int) -> Dict:
        """Получает заявку по ID с кэшированием"""
        return self.get_request(request_id)

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
            logger.error(f"❌ Ошибка при получении заявки #{request_id}: {e}")
            return {}

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
                logger.info(f"✅ Статус заявки #{request_id} изменен на '{status}'")
                
                # Инвалидируем кэш
                self.get_request_cached.cache_clear()
                
        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении статуса заявки #{request_id}: {e}")
            raise

    def add_comment_to_request(self, request_id: int, admin_id: int, admin_name: str, comment: str):
        """Добавляет комментарий к заявке"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO request_comments (request_id, admin_id, admin_name, comment, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (request_id, admin_id, admin_name, comment, datetime.now().isoformat()))
                conn.commit()
                logger.info(f"✅ Комментарий добавлен к заявке #{request_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка добавления комментария к заявке #{request_id}: {e}")
            raise

    def get_request_comments(self, request_id: int) -> List[Dict]:
        """Получает комментарии к заявке"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM request_comments 
                    WHERE request_id = ? 
                    ORDER BY created_at DESC
                ''', (request_id,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"❌ Ошибка получения комментариев заявки #{request_id}: {e}")
            return []

    def get_requests_by_filter(self, department: str = None, status: str = 'all', limit: int = 50) -> List[Dict]:
        """Получает заявки по фильтру отдела и статуса"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
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
                return requests
        except Exception as e:
            logger.error(f"❌ Ошибка при получении заявок: {e}")
            return []

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
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"❌ Ошибка при получении заявок пользователя {user_id}: {e}")
            return []

    def get_all_user_ids(self) -> List[int]:
        """Получает все уникальные ID пользователей"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT user_id FROM requests WHERE user_id IS NOT NULL")
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"❌ Ошибка получения ID пользователей: {e}")
            return []

    def get_statistics(self) -> Dict:
        """Получает статистику по заявкам"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Общая статистика
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new
                    FROM requests
                ''')
                total_stats = cursor.fetchone()
                
                # Статистика по отделам
                cursor.execute('''
                    SELECT 
                        department,
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                    FROM requests 
                    GROUP BY department
                ''')
                dept_stats = cursor.fetchall()
                
                return {
                    'total': total_stats[0] if total_stats else 0,
                    'completed': total_stats[1] if total_stats else 0,
                    'in_progress': total_stats[2] if total_stats else 0,
                    'new': total_stats[3] if total_stats else 0,
                    'by_department': {
                        dept: {'total': total, 'completed': completed} 
                        for dept, total, completed in dept_stats
                    }
                }
                
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики: {e}")
            return {}

# Инициализация базы данных
db = Database(Config.DB_PATH)

# ==================== ОПРЕДЕЛЕНИЕ ЭТАПОВ РАЗГОВОРА ====================

NAME, PHONE, DEPARTMENT, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD, OTHER_PLOT, SELECT_REQUEST = range(12)

# ==================== КЛАВИАТУРЫ ====================

# 🎯 Главное меню пользователя
user_main_menu_keyboard = [
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', 'ℹ️ Помощь'],
    ['🔙 Главное меню']
]

# 👑 Главное меню администратора
admin_main_menu_keyboard = [
    ['👑 Админ-панель', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', '🔄 Заявки в работе'],
    ['🔙 Главное меню']
]

# 👑 Главное меню супер-администратора
super_admin_main_menu_keyboard = [
    ['👑 Супер-админ', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', '🔄 Заявки в работе'],
    ['📢 Массовая рассылка', '💾 Создать бэкап'],
    ['🔙 Главное меню']
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

# 🏢 Выбор отдела
department_keyboard = [
    ['💻 IT отдел', '🔧 Механика'],
    ['⚡ Электрика', '🔙 Назад в меню']
]

# ◀️ Клавиатура назад
back_keyboard = [['🔙 Назад']]

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

# ==================== ВИЗУАЛЬНЫЕ КНОПКИ ====================

def create_request_actions_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру с действиями для заявки"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{request_id}"),
            InlineKeyboardButton("📝 Комментарий", callback_data=f"comment_{request_id}")
        ],
        [
            InlineKeyboardButton("✅ Завершить", callback_data=f"complete_{request_id}"),
            InlineKeyboardButton("📋 Подробнее", callback_data=f"details_{request_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_comment_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для комментария"""
    keyboard = [
        [
            InlineKeyboardButton("✏️ Ввести комментарий", callback_data=f"add_comment_{request_id}"),
            InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_request_{request_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ФОРМАТИРОВАНИЕ ТЕКСТА ====================

def format_request_text(request: Dict) -> str:
    """Форматирует текст заявки"""
    status_emoji = {
        'new': '🆕',
        'in_progress': '🔄',
        'completed': '✅'
    }.get(request['status'], '❓')
    
    return (
        f"📋 *Заявка #{request['id']}* {status_emoji}\n\n"
        f"👤 *ФИО:* {request['name']}\n"
        f"📞 *Телефон:* {request['phone']}\n"
        f"🏢 *Отдел:* {request['department']}\n"
        f"🔧 *Тип проблемы:* {request['system_type']}\n"
        f"📍 *Участок:* {request['plot']}\n"
        f"⏰ *Срочность:* {request['urgency']}\n"
        f"📝 *Описание:* {request['problem'][:200]}...\n"
        f"👨‍💼 *Исполнитель:* {request.get('assigned_admin', 'Не назначен')}\n"
        f"🕒 *Создана:* {request['created_at'][:16]}\n"
        f"💬 *Комментарий:* {request.get('admin_comment', 'Нет комментария')}"
    )

def format_detailed_request_text(request: Dict, comments: List[Dict]) -> str:
    """Форматирует детальный текст заявки с комментариями"""
    base_text = format_request_text(request)
    
    comments_text = "\n\n💬 *История комментариев:*\n"
    if comments:
        for comment in comments:
            comments_text += f"\n👤 {comment['admin_name']} ({comment['created_at'][:16]}):\n{comment['comment']}\n"
    else:
        comments_text += "\n📭 Комментариев пока нет"
    
    return base_text + comments_text

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} запустил бота")
    
    welcome_text = (
        "👋 *Добро пожаловать в систему заявок!*\n\n"
        "🛠️ *Мы поможем с:*\n"
        "• 💻 IT проблемами\n"
        "• 🔧 Механическими неисправностями\n"
        "• ⚡ Электрическими вопросами\n\n"
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
        welcome_text = "👑 *Добро пожаловать, СУПЕР-АДМИНИСТРАТОР!*"
    elif Config.is_admin(user_id):
        keyboard = admin_main_menu_keyboard
        welcome_text = "👨‍💼 *Добро пожаловать, АДМИНИСТРАТОР!*"
    else:
        keyboard = user_main_menu_keyboard
        welcome_text = "💼 *Добро пожаловать в сервис заявок!*"
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает справку"""
    help_text = (
        "💼 *Помощь по боту заявок*\n\n"
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

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает админ-панель"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return
    
    await update.message.reply_text(
        "👑 *АДМИН-ПАНЕЛЬ*\n\n"
        "Выберите отдел для управления:",
        reply_markup=ReplyKeyboardMarkup([
            ['💻 IT админ-панель', '🔧 Механика админ-панель'],
            ['⚡ Электрика админ-панель', '🔙 Главное меню']
        ], resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_super_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает панель супер-администратора"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к панели супер-администратора.")
        return
    
    await update.message.reply_text(
        "👑 *ПАНЕЛЬ СУПЕР-АДМИНИСТРАТОРА*\n\n"
        "Доступные функции:",
        reply_markup=ReplyKeyboardMarkup([
            ['📢 Массовая рассылка', '👥 Управление админами'],
            ['🏢 Все заявки', '📈 Общая статистика'],
            ['💾 Создать бэкап', '🔙 Главное меню']
        ], resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_user_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает персональную статистику пользователя"""
    user_id = update.message.from_user.id
    
    # Получаем реальную статистику пользователя
    user_requests = db.get_user_requests(user_id, limit=1000)
    total = len(user_requests)
    completed = len([r for r in user_requests if r['status'] == 'completed'])
    in_progress = len([r for r in user_requests if r['status'] == 'in_progress'])
    new = len([r for r in user_requests if r['status'] == 'new'])
    
    percentage = (completed / total * 100) if total > 0 else 0
    
    stats_text = (
        "📊 *ВАША СТАТИСТИКА*\n\n"
        f"📈 *Всего заявок:* {total}\n"
        f"✅ *Выполнено:* {completed}\n"
        f"🔄 *В работе:* {in_progress}\n"
        f"🆕 *Новых:* {new}\n"
        f"📊 *Процент выполнения:* {percentage:.1f}%\n\n"
    )
    
    if total == 0:
        stats_text += "💡 Создайте первую заявку! 🎯"
    
    await update.message.reply_text(
        stats_text,
        parse_mode=ParseMode.MARKDOWN
    )

async def show_detailed_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает детальную статистику"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой статистике.")
        return
    
    stats = db.get_statistics()
    
    stats_text = "📊 *ДЕТАЛЬНАЯ СТАТИСТИКА*\n\n"
    
    if stats:
        total = stats['total']
        completed = stats['completed']
        in_progress = stats['in_progress']
        new = stats['new']
        
        percentage = (completed / total * 100) if total > 0 else 0
        
        stats_text += f"📈 Всего заявок: *{total}*\n"
        stats_text += f"✅ Выполнено: *{completed}*\n"
        stats_text += f"🔄 В работе: *{in_progress}*\n"
        stats_text += f"🆕 Новых: *{new}*\n"
        stats_text += f"📊 Процент выполнения: *{percentage:.1f}%*\n\n"
        
        stats_text += "🏢 *По отделам:*\n"
        for dept, dept_stats in stats.get('by_department', {}).items():
            dept_total = dept_stats['total']
            dept_completed = dept_stats['completed']
            dept_percentage = (dept_completed / dept_total * 100) if dept_total > 0 else 0
            stats_text += f"• {dept}: {dept_completed}/{dept_total} ({dept_percentage:.1f}%)\n"
    else:
        stats_text += "📭 Нет данных для отображения"
    
    await update.message.reply_text(
        stats_text,
        parse_mode=ParseMode.MARKDOWN
    )

async def create_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Создает бэкап базы данных"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    try:
        backup_file = BackupManager.create_backup()
        if backup_file:
            await update.message.reply_text(
                f"✅ *Бэкап успешно создан!*\n\n"
                f"📁 Файл: `{backup_file}`\n\n"
                f"💾 Бэкапы хранятся в папке `backups/`",
                parse_mode=ParseMode.MARKDOWN
            )
            # Очищаем старые бэкапы
            BackupManager.cleanup_old_backups()
        else:
            await update.message.reply_text("❌ Не удалось создать бэкап")
            
    except Exception as e:
        logger.error(f"❌ Ошибка создания бэкапа: {e}")
        await update.message.reply_text("❌ Ошибка при создании бэкапа")

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
    
    await update.message.reply_text(
        f"📂 *Ваши заявки ({len(requests)}):*",
        parse_mode=ParseMode.MARKDOWN
    )
    
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

async def search_requests_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает меню поиска заявок"""
    await update.message.reply_text(
        "🔍 *Поиск заявок*\n\n"
        "Функция поиска в разработке...",
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== МАССОВАЯ РАССЫЛКА ====================

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Массовая рассылка для супер-админа"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой команде")
        return
    
    # Сохраняем состояние рассылки
    context.user_data['broadcasting'] = True
    
    await update.message.reply_text(
        "📢 *МАССОВАЯ РАССЫЛКА*\n\n"
        "Введите сообщение для рассылки всем пользователям:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup([['❌ Отменить рассылку']], resize_keyboard=True)
    )

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает массовую рассылку"""
    user_id = update.message.from_user.id
    
    # Проверяем отмену
    if update.message.text == '❌ Отменить рассылку':
        context.user_data.pop('broadcasting', None)
        await show_main_menu(update, context)
        return
    
    if not context.user_data.get('broadcasting'):
        return
    
    if not Config.is_super_admin(user_id):
        return
    
    message_text = update.message.text
    
    # Получаем всех уникальных пользователей из заявок
    try:
        user_ids = db.get_all_user_ids()
        
        if not user_ids:
            await update.message.reply_text(
                "❌ Нет пользователей для рассылки",
                reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True)
            )
            context.user_data.pop('broadcasting', None)
            return
        
        await update.message.reply_text(
            f"📤 *Начинаю рассылку...*\n\n"
            f"👥 Получателей: {len(user_ids)}\n"
            f"💬 Сообщение: {message_text[:100]}...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        success_count = 0
        fail_count = 0
        failed_users = []
        
        for i, uid in enumerate(user_ids):
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"📢 *ОБЪЯВЛЕНИЕ*\n\n{message_text}",
                    parse_mode=ParseMode.MARKDOWN
                )
                success_count += 1
                
                # Обновляем прогресс каждые 10 сообщений
                if (i + 1) % 10 == 0:
                    await update.message.reply_text(
                        f"📤 Отправлено {i + 1}/{len(user_ids)} сообщений..."
                    )
                
                await asyncio.sleep(0.1)  # Задержка чтобы не превысить лимиты
                
            except Exception as e:
                logger.error(f"❌ Ошибка отправки пользователю {uid}: {e}")
                fail_count += 1
                failed_users.append(uid)
        
        # Формируем итоговый отчет
        result_text = (
            f"📊 *Результаты рассылки:*\n\n"
            f"✅ Успешно: *{success_count}*\n"
            f"❌ Ошибок: *{fail_count}*\n"
            f"📊 Эффективность: *{success_count/len(user_ids)*100:.1f}%*"
        )
        
        if failed_users:
            result_text += f"\n\n⚠️ *Не удалось отправить:* {len(failed_users)} пользователей"
        
        await update.message.reply_text(
            result_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True)
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка массовой рассылки: {e}")
        await update.message.reply_text(
            "❌ Ошибка при рассылке",
            reply_markup=ReplyKeyboardMarkup(super_admin_main_menu_keyboard, resize_keyboard=True)
        )
    
    finally:
        context.user_data.pop('broadcasting', None)

# ==================== ОБРАБОТКА СОЗДАНИЯ ЗАЯВКИ ====================

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
        await cancel_request(update, context)
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
            "💻 *Шаг 4 из 8: Выберите тип IT проблемы*",
            reply_markup=ReplyKeyboardMarkup(it_systems_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    elif update.message.text == '🔧 Механика':
        await update.message.reply_text(
            "🔧 *Шаг 4 из 8: Выберите тип механической проблемы*",
            reply_markup=ReplyKeyboardMarkup(mechanics_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    elif update.message.text == '⚡ Электрика':
        await update.message.reply_text(
            "⚡ *Шаг 4 из 8: Выберите тип электрической проблемы*",
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
        "📍 *Шаг 5 из 8: Выберите участок*",
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
    
    valid_plots = ['🏢 Центральный офис', '🏭 Проduction', '📦 Складской комплекс', '🛒 Торговый зал', '💻 Удаленные рабочие места']
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
            "📝 Пожалуйста, опишите проблему подробно (от 10 до 1000 символов):",
            reply_markup=ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return PROBLEM
    
    context.user_data['problem'] = problem_text
    
    await update.message.reply_text(
        "⏰ *Шаг 7 из 8: Выберите срочность*",
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
        "📸 *Шаг 8 из 8: Добавить фото*",
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
            "📸 *Отправьте фото проблемы:*",
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
                            reply_markup=create_request_actions_keyboard(request_id),
                            parse_mode=ParseMode.MARKDOWN
                        )
                else:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=request_text,
                        reply_markup=create_request_actions_keyboard(request_id),
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
            f"💡 *Статус заявки можно отслеживать в разделе '📂 Мои заявки'*"
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

# ==================== ОБРАБОТЧИКИ INLINE КНОПОК ====================

async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатий на inline кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"Обработка inline кнопки: {data} пользователем {user_id}")
    
    if data.startswith('take_'):
        await take_request_inline(update, context)
    elif data.startswith('complete_'):
        await complete_request_inline(update, context)
    elif data.startswith('comment_'):
        await add_comment_inline(update, context)
    elif data.startswith('add_comment_'):
        await start_comment_input(update, context)
    elif data.startswith('details_'):
        await show_request_details(update, context)
    elif data.startswith('back_to_request_'):
        await show_request_with_actions(update, context)

async def take_request_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик взятия заявки в работу через inline кнопку"""
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        request_id = int(query.data.split('_')[1])
        request = db.get_request_cached(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        # Проверяем права доступа
        if not Config.is_admin(user_id, request['department']):
            await query.edit_message_text("❌ У вас нет доступа к этой заявке")
            return
        
        # Проверяем статус
        if request['status'] != 'new':
            await query.edit_message_text("❌ Заявка уже взята в работу")
            return
        
        # Берем в работу
        admin_name = query.from_user.first_name
        db.update_request_status(
            request_id=request_id,
            status='in_progress',
            assigned_admin=admin_name
        )
        
        # Обновляем сообщение
        updated_request = db.get_request_cached(request_id)
        request_text = format_request_text(updated_request)
        keyboard = create_request_actions_keyboard(request_id)
        
        await query.edit_message_text(
            f"🔄 *Взято в работу!*\n\n{request_text}",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Уведомляем пользователя
        await notify_user_about_request_status(
            update, context, request_id, 'in_progress', 
            assigned_admin=admin_name
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка взятия заявки: {e}")
        await query.edit_message_text("❌ Ошибка при взятии заявки")

async def complete_request_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик завершения заявки через inline кнопку"""
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        request_id = int(query.data.split('_')[1])
        request = db.get_request_cached(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        # Проверяем права и статус
        if not Config.is_admin(user_id, request['department']):
            await query.edit_message_text("❌ У вас нет доступа к этой заявке")
            return
        
        if request['status'] != 'in_progress':
            await query.edit_message_text("❌ Заявка не в работе")
            return
        
        # Сохраняем ID заявки для комментария
        context.user_data['completing_request_id'] = request_id
        context.user_data['completing_message_id'] = query.message.message_id
        
        await query.edit_message_text(
            f"📝 *Завершение заявки #{request_id}*\n\n"
            "💬 *Введите комментарий к выполнению:*\n\n"
            "💡 Опишите что было сделано, какие детали заменены и т.д.",
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка завершения заявки: {e}")
        await query.edit_message_text("❌ Ошибка при завершении заявки")

async def add_comment_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик добавления комментария через inline кнопку"""
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        request_id = int(query.data.split('_')[1])
        request = db.get_request_cached(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        # Проверяем права
        if not Config.is_admin(user_id, request['department']):
            await query.edit_message_text("❌ У вас нет доступа к этой заявке")
            return
        
        # Сохраняем ID заявки для комментария
        context.user_data['commenting_request_id'] = request_id
        context.user_data['commenting_message_id'] = query.message.message_id
        
        await query.edit_message_text(
            f"💬 *Добавление комментария к заявке #{request_id}*\n\n"
            "✍️ *Введите ваш комментарий:*\n\n"
            "💡 Комментарий будет виден всем администраторам отдела",
            reply_markup=create_comment_keyboard(request_id),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления комментария: {e}")
        await query.edit_message_text("❌ Ошибка при добавлении комментария")

async def start_comment_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начинает ввод комментария"""
    query = update.callback_query
    
    await query.edit_message_text(
        "✍️ *Введите ваш комментарий:*\n\n"
        "📝 Напишите сообщение и отправьте его как обычное текстовое сообщение",
        parse_mode=ParseMode.MARKDOWN
    )

async def show_request_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает детальную информацию о заявке"""
    query = update.callback_query
    
    try:
        request_id = int(query.data.split('_')[1])
        request = db.get_request_cached(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        # Получаем комментарии
        comments = db.get_request_comments(request_id)
        
        details_text = format_detailed_request_text(request, comments)
        
        await query.edit_message_text(
            details_text,
            reply_markup=create_request_actions_keyboard(request_id),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка показа деталей заявки: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке деталей")

async def show_request_with_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает заявку с кнопками действий"""
    query = update.callback_query
    
    try:
        request_id = int(query.data.split('_')[2])  # back_to_request_123
        request = db.get_request_cached(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        request_text = format_request_text(request)
        keyboard = create_request_actions_keyboard(request_id)
        
        await query.edit_message_text(
            request_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка возврата к заявке: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке заявки")

# ==================== ОБРАБОТКА КОММЕНТАРИЕВ ====================

async def handle_admin_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает комментарии администраторов"""
    user_id = update.message.from_user.id
    comment_text = update.message.text.strip()
    
    # Проверяем отмену массовой рассылки
    if context.user_data.get('broadcasting'):
        await process_broadcast(update, context)
        return
    
    # Проверяем, есть ли активный процесс комментария
    commenting_request_id = context.user_data.get('commenting_request_id')
    completing_request_id = context.user_data.get('completing_request_id')
    
    if commenting_request_id:
        # Обработка обычного комментария
        await process_comment(update, context, commenting_request_id, user_id, comment_text, is_completion=False)
    
    elif completing_request_id:
        # Обработка комментария при завершении заявки
        await process_comment(update, context, completing_request_id, user_id, comment_text, is_completion=True)
    
    else:
        # Если нет активных процессов, обрабатываем как обычное сообщение
        await handle_text_messages(update, context)

async def process_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, admin_id: int, comment: str, is_completion: bool = False):
    """Обрабатывает комментарий администратора"""
    try:
        request = db.get_request_cached(request_id)
        if not request:
            await update.message.reply_text("❌ Заявка не найдена")
            return
        
        admin_name = update.message.from_user.first_name
        
        if is_completion:
            # Завершаем заявку с комментарием
            db.update_request_status(
                request_id=request_id,
                status='completed',
                admin_comment=comment,
                assigned_admin=admin_name
            )
            
            # Добавляем в историю комментариев
            db.add_comment_to_request(request_id, admin_id, admin_name, f"✅ Завершено: {comment}")
            
            success_message = f"✅ *Заявка #{request_id} завершена!*\n\n💬 *Комментарий:* {comment}"
            
            # Уведомляем пользователя
            await notify_user_about_request_status(
                update, context, request_id, 'completed', 
                admin_comment=comment, assigned_admin=admin_name
            )
            
        else:
            # Добавляем обычный комментарий
            db.add_comment_to_request(request_id, admin_id, admin_name, comment)
            success_message = f"💬 *Комментарий добавлен к заявке #{request_id}*\n\n📝 *Текст:* {comment}"
            
            # Уведомляем других админов отдела
            await notify_admins_about_comment(update, context, request_id, admin_name, comment)
        
        # Очищаем данные контекста
        context.user_data.pop('commenting_request_id', None)
        context.user_data.pop('completing_request_id', None)
        context.user_data.pop('commenting_message_id', None)
        context.user_data.pop('completing_message_id', None)
        
        await update.message.reply_text(
            success_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(
                super_admin_main_menu_keyboard if Config.is_super_admin(admin_id) else
                admin_main_menu_keyboard if Config.is_admin(admin_id) else
                user_main_menu_keyboard,
                resize_keyboard=True
            )
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки комментария: {e}")
        await update.message.reply_text("❌ Ошибка при обработке комментария")

async def notify_admins_about_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, admin_name: str, comment: str):
    """Уведомляет админов отдела о новом комментарии"""
    try:
        request = db.get_request_cached(request_id)
        if not request:
            return
        
        department = request['department']
        admin_ids = Config.get_admins_for_department(department)
        
        notification_text = (
            f"💬 *Новый комментарий к заявке #{request_id}*\n\n"
            f"🏢 *Отдел:* {department}\n"
            f"👤 *Администратор:* {admin_name}\n"
            f"📝 *Комментарий:* {comment}\n\n"
            f"🔧 *Тип проблемы:* {request['system_type']}\n"
            f"📍 *Участок:* {request['plot']}"
        )
        
        for admin_id in admin_ids:
            if admin_id != update.message.from_user.id:  # Не уведомляем автора комментария
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=notification_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки уведомления админу {admin_id}: {e}")
                    
    except Exception as e:
        logger.error(f"❌ Ошибка уведомления админов о комментарии: {e}")

# ==================== ПОКАЗ ЗАЯВОК С INLINE КНОПКАМИ ====================

async def show_new_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает новые заявки отдела с inline кнопками"""
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
    
    await update.message.reply_text(
        f"🆕 *Новые заявки {department}: {len(requests)}*\n\n"
        "Используйте кнопки ниже для управления заявками:",
        parse_mode=ParseMode.MARKDOWN
    )
    
    for request in requests:
        request_text = format_request_text(request)
        keyboard = create_request_actions_keyboard(request['id'])
        
        if request.get('photo'):
            try:
                with open(request['photo'], 'rb') as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=request_text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.MARKDOWN
                    )
            except:
                await update.message.reply_text(
                    request_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            await update.message.reply_text(
                request_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )

async def show_requests_in_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает заявки в работе с inline кнопками"""
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
        "Используйте кнопки для управления заявками:",
        parse_mode=ParseMode.MARKDOWN
    )
    
    for request in requests[:10]:
        request_text = format_request_text(request)
        keyboard = create_request_actions_keyboard(request['id'])
        
        await update.message.reply_text(
            request_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== УВЕДОМЛЕНИЯ ====================

async def notify_user_about_request_status(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
    """Уведомляет пользователя об изменении статуса заявки"""
    try:
        request = db.get_request_cached(request_id)
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
        logger.info(f"✅ Уведомление отправлено пользователю {user_id} о заявке #{request_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления пользователю о заявке #{request_id}: {e}")

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
    elif text == '🔍 Поиск заявки':
        await search_requests_menu(update, context)
    elif text == '📊 Статистика':
        await show_user_statistics(update, context)
    elif text == '📈 Общая статистика':
        await show_detailed_statistics(update, context)
    elif text == '🔄 Заявки в работе':
        await show_requests_in_progress(update, context)
    elif text == 'ℹ️ Помощь':
        await show_help(update, context)
    elif text == '👑 Админ-панель':
        await show_admin_panel(update, context)
    elif text == '👑 Супер-админ':
        await show_super_admin_panel(update, context)
    elif text == '📢 Массовая рассылка':
        await broadcast_message(update, context)
    elif text == '💾 Создать бэкап':
        await create_backup_command(update, context)
    elif text == '🔙 Главное меню':
        await show_main_menu(update, context)
    
    # Обработка кнопок админ-панелей отделов
    elif text in ['💻 IT админ-панель', '🔧 Механика админ-панель', '⚡ Электрика админ-панель']:
        await show_department_admin_panel(update, context)
    
    # Обработка кнопок новых заявок
    elif text in ['🆕 Новые заявки IT', '🆕 Новые заявки механики', '🆕 Новые заявки электрики']:
        await show_new_requests(update, context)
    
    # Обработка кнопок статистики отделов
    elif text in ['📊 Статистика IT', '📊 Статистика механики', '📊 Статистика электрики']:
        await show_detailed_statistics(update, context)
    
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

# ==================== ЗАПУСК БОТА ====================

def main() -> None:
    """Запускаем бота"""
    try:
        # Проверка конфигурации
        Config.validate_config()
        
        # Создание бэкапа при запуске
        BackupManager.create_backup()
        BackupManager.cleanup_old_backups()
        
        if not Config.BOT_TOKEN:
            logger.error("❌ Токен бота не загружен!")
            return
        
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("menu", show_main_menu))
        application.add_handler(CommandHandler("help", show_help))
        application.add_handler(CommandHandler("statistics", show_detailed_statistics))
        application.add_handler(CommandHandler("backup", create_backup_command))
        
        # Обработчик inline кнопок
        application.add_handler(CallbackQueryHandler(handle_inline_buttons))
        
        # Обработчик комментариев админов и массовой рассылки
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_admin_comment
        ))
        
        # Обработчики текстовых сообщений для меню
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
        
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
        )
        
        application.add_handler(conv_handler)
        
        logger.info("🤖 Бот заявок успешно запущен!")
        print("✅ Бот успешно запущен!")
        print("🎯 Улучшенные возможности:")
        print("   • 🔒 Проверка конфигурации при запуске")
        print("   • 💾 Автоматические бэкапы базы данных") 
        print("   • 📊 Детальная статистика по отделам")
        print("   • 📢 Массовая рассылка для супер-админов")
        print("   • 🔄 Повторные попытки при ошибках БД")
        print("   • 🚀 Кэширование для улучшения производительности")
        print("   • 💬 Улучшенная система комментариев")
        
        application.run_polling()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
        print(f"❌ Критическая ошибка: {e}")

if __name__ == '__main__':
    main()
