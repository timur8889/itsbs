import logging
import sqlite3
import os
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ParseMode,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
    CallbackQueryHandler,
    JobQueue,
)

# ==================== КОНФИГУРАЦИЯ ====================

# Безопасное получение токена из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN', "7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw")
ADMIN_CHAT_IDS = [int(x) for x in os.getenv('ADMIN_CHAT_IDS', '5024165375').split(',')]

# Включим логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Определяем этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD = range(9)

# База данных
DB_PATH = "requests.db"
BACKUP_DIR = "backups"

# Создаем директорию для бэкапов
os.makedirs(BACKUP_DIR, exist_ok=True)

# ==================== УТИЛИТЫ ====================

class Validators:
    """Класс для валидации данных"""
    
    @staticmethod
    def validate_phone(phone: str) -> bool:
        """Валидация российских номеров телефонов"""
        pattern = r'^(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}$'
        return bool(re.match(pattern, phone.strip()))
    
    @staticmethod
    def validate_name(name: str) -> bool:
        """Валидация имени (только буквы, пробелы и дефисы)"""
        pattern = r'^[a-zA-Zа-яА-ЯёЁ\s\-]{2,50}$'
        return bool(re.match(pattern, name.strip()))
    
    @staticmethod
    def format_phone(phone: str) -> str:
        """Форматирование телефона в единый формат"""
        cleaned = re.sub(r'[^\d]', '', phone)
        if cleaned.startswith('8'):
            cleaned = '7' + cleaned[1:]
        return f"+7 ({cleaned[1:4]}) {cleaned[4:7]}-{cleaned[7:9]}-{cleaned[9:11]}"

class RateLimiter:
    """Класс для ограничения частоты запросов"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_table()
    
    def init_table(self):
        """Инициализация таблицы для rate limiting"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rate_limits (
                    user_id INTEGER,
                    action_type TEXT,
                    timestamp TEXT,
                    PRIMARY KEY (user_id, action_type)
                )
            ''')
            conn.commit()
    
    def is_limited(self, user_id: int, action_type: str, limit_per_hour: int = 10) -> bool:
        """Проверяет, превышен ли лимит запросов"""
        hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) FROM rate_limits 
                WHERE user_id = ? AND action_type = ? AND timestamp > ?
            ''', (user_id, action_type, hour_ago))
            count = cursor.fetchone()[0]
            
            if count >= limit_per_hour:
                return True
            
            # Записываем текущий запрос
            cursor.execute('''
                INSERT OR REPLACE INTO rate_limits (user_id, action_type, timestamp)
                VALUES (?, ?, ?)
            ''', (user_id, action_type, datetime.now().isoformat()))
            conn.commit()
            
            return False

class BackupManager:
    """Класс для управления бэкапами базы данных"""
    
    @staticmethod
    def create_backup():
        """Создает бэкап базы данных"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
            
            import shutil
            shutil.copy2(DB_PATH, backup_path)
            
            # Удаляем старые бэкапы (оставляем последние 10)
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_')])
            for old_backup in backups[:-10]:
                os.remove(os.path.join(BACKUP_DIR, old_backup))
            
            logger.info(f"Database backed up to {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return None

# ==================== КЛАВИАТУРЫ ====================

# Главное меню пользователя
user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки']
]

# Главное меню администратора (ОБНОВЛЕНО - добавлены счетчики)
admin_main_menu_keyboard = [
    ['🆕 Новые заявки (0)', '🔄 В работе (0)'],
    ['✅ Выполненные заявки', '📊 Статистика'],
    ['🔄 Обновить', '💾 Создать бэкап']
]

# Меню создания заявки
create_request_keyboard = [
    ['📹 Видеонаблюдение', '🔐 СКУД'],
    ['🌐 Компьютерная сеть', '🚨 Пожарная сигнализация'],
    ['🔙 Назад в меню']
]

# Клавиатуры для этапов заявки
confirm_keyboard = [['✅ Подтвердить отправку', '✏️ Редактировать заявку']]
photo_keyboard = [['📷 Добавить фото', '⏭️ Пропустить фото']]
urgency_keyboard = [
    ['🔴 Срочно (2 часа)'],
    ['🟡 Средняя (сегодня)'],
    ['🟢 Не срочно (3 дня)'],
    ['🔙 Назад']
]
plot_type_keyboard = [
    ['🏭 Фрезерный участок', '⚙️ Токарный участок'],
    ['🔨 Участок штамповки', '📦 Другой участок'],
    ['🔙 Назад']
]

# Клавиатуры для редактирования
edit_choice_keyboard = [
    ['📛 Редактировать имя', '📞 Редактировать телефон'],
    ['📍 Редактировать участок', '🔧 Редактировать систему'],
    ['📝 Редактировать описание', '⏰ Редактировать срочность'],
    ['📷 Редактировать фото', '✅ Завершить редактирование']
]

edit_field_keyboard = [['🔙 Назад к редактированию']]

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Инициализация базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    name TEXT,
                    phone TEXT,
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
            conn.commit()

    def save_request(self, user_data: Dict) -> int:
        """Сохраняет заявку в базу данных"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO requests 
                    (user_id, username, name, phone, plot, system_type, problem, photo, urgency, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_data.get('user_id'),
                    user_data.get('username'),
                    user_data.get('name'),
                    user_data.get('phone'),
                    user_data.get('plot'),
                    user_data.get('system_type'),
                    user_data.get('problem'),
                    user_data.get('photo'),
                    user_data.get('urgency'),
                    datetime.now().isoformat(),
                    datetime.now().isoformat()
                ))
                request_id = cursor.lastrowid
                
                # Обновляем статистику
                today = datetime.now().strftime('%Y-%m-%d')
                cursor.execute('''
                    INSERT OR REPLACE INTO statistics (date, requests_count)
                    VALUES (?, COALESCE((SELECT requests_count FROM statistics WHERE date = ?), 0) + 1)
                ''', (today, today))
                
                # Обновляем информацию о пользователе
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
                return request_id
        except sqlite3.Error as e:
            logger.error(f"Database error in save_request: {e}")
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
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Database error in get_user_requests: {e}")
            return []

    def get_requests_by_filter(self, filter_type: str = 'all', limit: int = 50) -> List[Dict]:
        """Получает заявки по фильтру"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                if filter_type == 'new':
                    status_filter = "status = 'new'"
                elif filter_type == 'in_progress':
                    status_filter = "status = 'in_progress'"
                elif filter_type == 'urgent':
                    status_filter = "urgency LIKE '%Срочно%' AND status IN ('new', 'in_progress')"
                elif filter_type == 'completed':
                    status_filter = "status = 'completed'"
                else:  # all active
                    status_filter = "status IN ('new', 'in_progress')"
                
                cursor.execute(f'''
                    SELECT * FROM requests 
                    WHERE {status_filter}
                    ORDER BY 
                        CASE urgency 
                            WHEN '🔴 Срочно (2 часа)' THEN 1
                            WHEN '🟡 Средняя (сегодня)' THEN 2
                            ELSE 3
                        END,
                        created_at DESC
                    LIMIT ?
                ''', (limit,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Database error in get_requests_by_filter: {e}")
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
        except sqlite3.Error as e:
            logger.error(f"Database error in get_request: {e}")
            return {}

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
                
                # Обновляем статистику выполненных заявок
                if status == 'completed':
                    today = datetime.now().strftime('%Y-%m-%d')
                    cursor.execute('''
                        INSERT OR REPLACE INTO statistics (date, completed_count)
                        VALUES (?, COALESCE((SELECT completed_count FROM statistics WHERE date = ?), 0) + 1)
                    ''', (today, today))
                
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error in update_request_status: {e}")
            raise

    def get_my_in_progress_requests(self, admin_name: str, limit: int = 50) -> List[Dict]:
        """Получает заявки, которые взял в работу конкретный администратор"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE assigned_admin = ? AND status = 'in_progress'
                    ORDER BY 
                        CASE urgency 
                            WHEN '🔴 Срочно (2 часа)' THEN 1
                            WHEN '🟡 Средняя (сегодня)' THEN 2
                            ELSE 3
                        END,
                        created_at DESC
                    LIMIT ?
                ''', (admin_name, limit))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Database error in get_my_in_progress_requests: {e}")
            return []

    def get_statistics(self, days: int = 30) -> Dict:
        """Получает статистику за указанный период"""
        try:
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Общая статистика
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total_requests,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_requests,
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new_requests,
                        SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress_requests
                    FROM requests 
                    WHERE created_at > ?
                ''', (start_date,))
                
                stats = cursor.fetchone()
                total, completed, new, in_progress = stats if stats else (0, 0, 0, 0)
                
                # Статистика по дням
                cursor.execute('''
                    SELECT date, requests_count, completed_count 
                    FROM statistics 
                    WHERE date > ? 
                    ORDER BY date DESC
                ''', (start_date,))
                
                daily_stats = cursor.fetchall()
                
                return {
                    'total': total,
                    'completed': completed,
                    'new': new,
                    'in_progress': in_progress,
                    'daily': daily_stats
                }
        except sqlite3.Error as e:
            logger.error(f"Database error in get_statistics: {e}")
            return {'total': 0, 'completed': 0, 'new': 0, 'in_progress': 0, 'daily': []}

# Инициализация базы данных и утилит
db = Database(DB_PATH)
rate_limiter = RateLimiter(DB_PATH)
validators = Validators()

# ==================== СОЗДАНИЕ ЗАЯВКИ (УЛУЧШЕННАЯ) ====================

def start_request_creation(update: Update, context: CallbackContext) -> int:
    """Начинает процесс создания заявки с проверкой лимитов"""
    user_id = update.message.from_user.id
    
    # Проверка лимита запросов
    if rate_limiter.is_limited(user_id, 'create_request', 10):
        update.message.reply_text(
            "❌ *Превышен лимит запросов!*\n\n"
            "Вы можете создавать не более 10 заявок в час.\n"
            "Пожалуйста, попробуйте позже.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    context.user_data.clear()
    
    user = update.message.from_user
    context.user_data.update({
        'user_id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name
    })
    
    update.message.reply_text(
        "📝 *Создание новой заявки*\n\n"
        "Для начала укажите ваше имя:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def name(update: Update, context: CallbackContext) -> int:
    name_text = update.message.text
    
    # Валидация имени
    if not validators.validate_name(name_text):
        update.message.reply_text(
            "❌ *Неверный формат имени!*\n\n"
            "Имя должно содержать только буквы, пробелы и дефисы.\n"
            "Длина: от 2 до 50 символов.\n\n"
            "Пожалуйста, введите ваше имя еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME
    
    context.user_data['name'] = name_text
    update.message.reply_text(
        "📞 *Укажите ваш контактный телефон:*\n\n"
        "Пример: +7 999 123-45-67 или 8 999 123-45-67",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    phone_text = update.message.text
    
    # Валидация телефона
    if not validators.validate_phone(phone_text):
        update.message.reply_text(
            "❌ *Неверный формат телефона!*\n\n"
            "Пожалуйста, введите номер в формате:\n"
            "+7 999 123-45-67 или 8 999 123-45-67\n\n"
            "Попробуйте еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHONE
    
    # Форматируем телефон
    formatted_phone = validators.format_phone(phone_text)
    context.user_data['phone'] = formatted_phone
    
    update.message.reply_text(
        f"✅ Телефон сохранен: `{formatted_phone}`\n\n"
        "📍 *Выберите тип участка:*",
        reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLOT

# Остальные функции создания заявки остаются без изменений (plot, system_type, problem, urgency, photo)
def plot(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "Укажите ваше имя:",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "🔧 *Выберите тип системы:*",
        reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад в меню':
        return show_main_menu(update, context)
    elif update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📍 *Выберите тип участка:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        "📝 *Опишите проблему или необходимые работы:*\n\nПример: Не работает видеонаблюдение на фрезерном участке",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    context.user_data['problem'] = update.message.text
    update.message.reply_text(
        "⏰ *Выберите срочность выполнения работ:*",
        reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📝 *Опишите проблему или необходимые работы:*",
            reply_markup=ReplyKeyboardRemove()
        )
        return PROBLEM
    
    context.user_data['urgency'] = update.message.text
    update.message.reply_text(
        "📸 *Хотите добавить фото к заявке?*\n\nФото поможет специалисту лучше понять проблему.",
        reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "⏰ *Выберите срочность выполнения работ:*",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    elif update.message.text == '📷 Добавить фото':
        update.message.reply_text(
            "📸 *Отправьте фото:*",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHOTO
    elif update.message.text == '⏭️ Пропустить фото':
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

def update_summary(context: CallbackContext) -> None:
    """Обновляет сводку заявки в user_data"""
    photo_status = "✅ Есть" if context.user_data.get('photo') else "❌ Нет"
    
    summary = (
        f"📋 *Сводка заявки:*\n\n"
        f"📛 *Имя:* {context.user_data['name']}\n"
        f"📞 *Телефон:* `{context.user_data['phone']}`\n"
        f"📍 *Участок:* {context.user_data['plot']}\n"
        f"🔧 *Тип системы:* {context.user_data['system_type']}\n"
        f"📝 *Описание:* {context.user_data['problem']}\n"
        f"⏰ *Срочность:* {context.user_data['urgency']}\n"
        f"📸 *Фото:* {photo_status}\n"
        f"🕒 *Время:* {context.user_data.get('timestamp', 'Не указано')}"
    )
    
    context.user_data['summary'] = summary

def show_request_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку заявки перед отправкой"""
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    update_summary(context)
    
    # Определяем, откуда пришли - из создания или редактирования
    if context.user_data.get('editing_mode'):
        # Режим редактирования - показываем меню редактирования
        return edit_request_choice(update, context)
    else:
        # Режим создания - показываем подтверждение
        if context.user_data.get('photo'):
            update.message.reply_photo(
                photo=context.user_data['photo'],
                caption=f"{context.user_data['summary']}\n\n*Подтвердите отправку заявки:*",
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                f"{context.user_data['summary']}\n\n*Подтвердите отправку заявки:*",
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        return ConversationHandler.END

def confirm_request(update: Update, context: CallbackContext) -> None:
    """Подтверждает и отправляет заявку"""
    if update.message.text == '✅ Подтвердить отправку':
        user = update.message.from_user
        
        try:
            # Сохраняем заявку в базу данных
            request_id = db.save_request(context.user_data)
            
            # Отправляем уведомление администраторам
            send_admin_notification(context, context.user_data, request_id)
            
            # Подтверждение пользователю
            confirmation_text = (
                f"✅ *Заявка #{request_id} успешно создана!*\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n\n"
                f"_Спасибо за обращение в службу слаботочных систем завода Контакт!_ 🛠️"
            )
            
            # Определяем клавиатуру в зависимости от прав
            if user.id in ADMIN_CHAT_IDS:
                # Администратору показываем админ-панель
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            
            logger.info(f"Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            
            # Определяем клавиатуру в зависимости от прав
            if user.id in ADMIN_CHAT_IDS:
                update.message.reply_text(
                    "❌ *Произошла ошибка при создании заявки.*\n\nПожалуйста, попробуйте позже.",
                    reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    "❌ *Произошла ошибка при создании заявки.*\n\nПожалуйста, попробуйте позже.",
                    reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
        
        context.user_data.clear()
        
    elif update.message.text == '✏️ Редактировать заявку':
        # Включаем режим редактирования
        context.user_data['editing_mode'] = True
        return edit_request_choice(update, context)

def send_admin_notification(context: CallbackContext, user_data: Dict, request_id: int) -> None:
    """Отправляет уведомление администраторам о новой заявке"""
    notification_text = (
        f"🚨 *НОВАЯ ЗАЯВКА #{request_id}*\n\n"
        f"👤 *Пользователь:* @{user_data.get('username', 'N/A')}\n"
        f"📛 *Имя:* {user_data.get('name')}\n"
        f"📞 *Телефон:* `{user_data.get('phone')}`\n"
        f"📍 *Участок:* {user_data.get('plot')}\n"
        f"🔧 *Система:* {user_data.get('system_type')}\n"
        f"⏰ *Срочность:* {user_data.get('urgency')}\n"
        f"📸 *Фото:* {'✅ Есть' if user_data.get('photo') else '❌ Нет'}\n\n"
        f"📝 *Описание:* {user_data.get('problem')}\n\n"
        f"🕒 *Время:* {user_data.get('timestamp', 'Не указано')}"
    )
    
    for admin_id in ADMIN_CHAT_IDS:
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
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")

def cancel_request(update: Update, context: CallbackContext) -> int:
    """Отменяет создание заявки"""
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        # Администратору показываем админ-панель
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True)
        )
    else:
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
    
    context.user_data.clear()
    return ConversationHandler.END

# ==================== АДМИН-ПАНЕЛЬ (УЛУЧШЕННАЯ) ====================

def get_admin_panel_with_counters():
    """Возвращает админ-панель с актуальными счетчиками"""
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    
    return [
        [f'🆕 Новые заявки ({len(new_requests)})', f'🔄 В работе ({len(in_progress_requests)})'],
        ['✅ Выполненные заявки', '📊 Статистика'],
        ['🔄 Обновить', '💾 Создать бэкап']
    ]

def show_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает админ-панель с новыми, в работе и выполненными заявками"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu(update, context)
    
    # Получаем количество заявок по статусам
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    completed_requests = db.get_requests_by_filter('completed')
    
    admin_text = (
        "👑 *Админ-панель завода Контакт*\n\n"
        f"🆕 *Новых заявок:* {len(new_requests)}\n"
        f"🔄 *Заявок в работе:* {len(in_progress_requests)}\n"
        f"✅ *Выполненных заявок:* {len(completed_requests)}\n\n"
        "Выберите раздел для просмотра:"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает статистику заявок"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    stats = db.get_statistics(30)  # Статистика за 30 дней
    
    # Формируем текст статистики
    stats_text = (
        "📊 *Статистика заявок за 30 дней*\n\n"
        f"📈 *Всего заявок:* {stats['total']}\n"
        f"✅ *Выполнено:* {stats['completed']}\n"
        f"🆕 *Новых:* {stats['new']}\n"
        f"🔄 *В работе:* {stats['in_progress']}\n\n"
    )
    
    # Добавляем статистику по дням (последние 7 дней)
    if stats['daily']:
        stats_text += "*Статистика по дням (последние 7 дней):*\n"
        for date, requests_count, completed_count in stats['daily'][:7]:
            formatted_date = datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m')
            stats_text += f"📅 {formatted_date}: {requests_count} заявок ({completed_count} выполнено)\n"
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def create_backup_command(update: Update, context: CallbackContext) -> None:
    """Создает бэкап базы данных"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    backup_path = BackupManager.create_backup()
    
    if backup_path:
        update.message.reply_text(
            f"✅ *Бэкап создан успешно!*\n\n"
            f"Файл: `{os.path.basename(backup_path)}`\n"
            f"Размер: {os.path.getsize(backup_path) / 1024:.1f} КБ",
            reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        update.message.reply_text(
            "❌ *Ошибка создания бэкапа!*\n\nПожалуйста, проверьте логи.",
            reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ (УЛУЧШЕННЫЕ) ====================

def handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в главном меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Для администраторов показываем только админ-панель
    if user_id in ADMIN_CHAT_IDS:
        return show_admin_panel(update, context)
    
    # Для обычных пользователей
    if text == '📝 Создать заявку':
        return start_request_creation(update, context)
    elif text == '📋 Мои заявки':
        return show_my_requests(update, context)
    else:
        update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )

def handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    # ОБНОВЛЕНО: Обработка кнопок с счетчиками
    if text.startswith('🆕 Новые заявки'):
        return show_requests_by_filter(update, context, 'new')
    elif text.startswith('🔄 В работе'):
        return show_requests_by_filter(update, context, 'in_progress')
    elif text == '✅ Выполненные заявки':
        return show_requests_by_filter(update, context, 'completed')
    elif text == '📊 Статистика':
        return show_statistics(update, context)
    elif text == '🔄 Обновить':
        return show_admin_panel(update, context)
    elif text == '💾 Создать бэкап':
        return create_backup_command(update, context)

# ==================== ОСНОВНЫЕ ФУНКЦИИ ====================

def error_handler(update: Update, context: CallbackContext) -> None:
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    try:
        if update and update.effective_message:
            update.effective_message.reply_text(
                "❌ *Произошла непредвиденная ошибка!*\n\n"
                "Пожалуйста, попробуйте еще раз или обратитесь к администратору.",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")

def backup_job(context: CallbackContext) -> None:
    """Ежедневное автоматическое создание бэкапа"""
    backup_path = BackupManager.create_backup()
    if backup_path:
        logger.info(f"Автоматический бэкап создан: {backup_path}")
    else:
        logger.error("Ошибка автоматического бэкапа")

def main() -> None:
    """Запускаем бота"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Добавляем обработчик ошибок
        dispatcher.add_error_handler(error_handler)

        # Настраиваем ежедневное создание бэкапов в 3:00
        job_queue = updater.job_queue
        if job_queue:
            job_queue.run_daily(backup_job, time=datetime.time(hour=3, minute=0))

        # Обработчик создания заявки
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(📝 Создать заявку)$'), start_request_creation),
            ],
            states={
                NAME: [MessageHandler(Filters.text & ~Filters.command, name)],
                PHONE: [MessageHandler(Filters.text & ~Filters.command, phone)],
                PLOT: [MessageHandler(Filters.text & ~Filters.command, plot)],
                SYSTEM_TYPE: [MessageHandler(Filters.text & ~Filters.command, system_type)],
                PROBLEM: [MessageHandler(Filters.text & ~Filters.command, problem)],
                URGENCY: [MessageHandler(Filters.text & ~Filters.command, urgency)],
                PHOTO: [
                    MessageHandler(Filters.text & ~Filters.command, photo),
                    MessageHandler(Filters.photo, photo)
                ],
                EDIT_CHOICE: [MessageHandler(Filters.text & ~Filters.command, handle_edit_choice)],
                EDIT_FIELD: [
                    MessageHandler(Filters.text & ~Filters.command, handle_edit_field),
                    MessageHandler(Filters.photo, handle_edit_field)
                ],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(Filters.regex('^(🔙 Назад в меню)$'), cancel_request),
                MessageHandler(Filters.regex('^(✅ Завершить редактирование)$'), show_request_summary)
            ],
            allow_reentry=True
        )

        # Отдельный обработчик для кнопки редактирования заявки
        edit_handler = MessageHandler(
            Filters.regex('^(✏️ Редактировать заявку)$'), 
            confirm_request
        )

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        dispatcher.add_handler(CommandHandler('stats', show_statistics))
        dispatcher.add_handler(CommandHandler('backup', create_backup_command))
        
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(edit_handler)
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить отправку)$'), confirm_request))
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📋 Мои заявки)$'), handle_main_menu))
        
        # Обработчики админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки|🔄 В работе|✅ Выполненные заявки|📊 Статистика|🔄 Обновить|💾 Создать бэкап)'), 
            handle_admin_menu
        ))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(
            handle_admin_callback, 
            pattern='^(take_|complete_|message_|confirm_take_|cancel_take_|confirm_complete_|cancel_complete_)'
        ))

        # Запускаем бота
        logger.info("🤖 Бот запущен с улучшенной функциональностью!")
        logger.info(f"👑 Администраторы: {ADMIN_CHAT_IDS}")
        logger.info(f"💾 Автоматические бэкапы: включены")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
