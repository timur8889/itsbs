import logging
import sqlite3
import os
import json
import re
import threading
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ParseMode,
    InputFile,
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

BOT_TOKEN = os.getenv('BOT_TOKEN', "7391146893:AAFDi7qQTWjscSeqNBueKlWWXbaXK99NpnHw")
ADMIN_CHAT_IDS = [int(x) for x in os.getenv('ADMIN_CHAT_IDS', '5024165375').split(',')]

# Расширенные настройки
MAX_REQUESTS_PER_HOUR = 15
BACKUP_RETENTION_DAYS = 30
AUTO_BACKUP_HOUR = 3
AUTO_BACKUP_MINUTE = 0
REQUEST_TIMEOUT_HOURS = 24  # Автоматическое закрытие старых заявок

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Этапы разговора (сохраняем старые + добавляем новые)
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD = range(9)

DB_PATH = "requests.db"
BACKUP_DIR = "backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

# ==================== БАЗОВЫЕ КЛАССЫ (для совместимости) ====================

class Validators:
    """Базовый класс валидации"""
    @staticmethod
    def validate_phone(phone: str) -> bool:
        return bool(re.match(r'^[\d\s\-\+\(\)]{7,20}$', phone.strip()))
    
    @staticmethod
    def validate_name(name: str) -> bool:
        return bool(re.match(r'^[А-Яа-яA-Za-z\s]{2,50}$', name.strip()))
    
    @staticmethod
    def validate_plot(plot: str) -> bool:
        return bool(re.match(r'^[А-Яа-яA-Za-z0-9\s\-]{2,20}$', plot.strip()))

class BackupManager:
    """Базовый менеджер бэкапов"""
    @staticmethod
    def create_backup():
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
            shutil.copy2(DB_PATH, backup_path)
            logger.info(f"Бэкап создан: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Ошибка создания бэкапа: {e}")
            return None

class RateLimiter:
    """Система ограничения запросов"""
    def __init__(self):
        self.requests = {}
    
    def is_limited(self, user_id, action, max_requests):
        now = datetime.now()
        hour_key = now.strftime("%Y%m%d%H")
        
        if user_id not in self.requests:
            self.requests[user_id] = {}
        
        if action not in self.requests[user_id]:
            self.requests[user_id][action] = {}
        
        if hour_key not in self.requests[user_id][action]:
            self.requests[user_id][action][hour_key] = 0
        
        self.requests[user_id][action][hour_key] += 1
        return self.requests[user_id][action][hour_key] > max_requests

class Database:
    """Базовая база данных с полной реализацией"""
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Основная таблица заявок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
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
                    assigned_to TEXT,
                    completed_at TEXT
                )
            ''')
            
            # Таблица пользователей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    request_count INTEGER DEFAULT 0,
                    created_at TEXT,
                    last_activity TEXT
                )
            ''')
            
            # Таблица для уведомлений
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    message TEXT,
                    sent_at TEXT,
                    is_read INTEGER DEFAULT 0
                )
            ''')
            
            # Таблица настроек
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # Таблица для истории изменений заявок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS request_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER,
                    action TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    changed_by TEXT,
                    changed_at TEXT,
                    FOREIGN KEY (request_id) REFERENCES requests (id)
                )
            ''')
            
            conn.commit()
    
    def save_request(self, data: Dict) -> int:
        """Сохраняет заявку в базу данных"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Обновляем или создаем пользователя
                cursor.execute('''
                    INSERT OR REPLACE INTO users 
                    (user_id, username, first_name, last_name, request_count, created_at, last_activity)
                    VALUES (?, ?, ?, ?, 
                        COALESCE((SELECT request_count FROM users WHERE user_id = ?), 0) + 1,
                        COALESCE((SELECT created_at FROM users WHERE user_id = ?), ?), ?)
                ''', (
                    data['user_id'], data.get('username'), data.get('first_name'), data.get('last_name'),
                    data['user_id'], data['user_id'], datetime.now().isoformat(), datetime.now().isoformat()
                ))
                
                # Сохраняем заявку
                cursor.execute('''
                    INSERT INTO requests 
                    (user_id, username, first_name, last_name, name, phone, plot, system_type, 
                     problem, photo, urgency, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data['user_id'], data.get('username'), data.get('first_name'), data.get('last_name'),
                    data.get('name'), data.get('phone'), data.get('plot'), data.get('system_type'),
                    data.get('problem'), data.get('photo'), data.get('urgency'), 'new',
                    datetime.now().isoformat(), datetime.now().isoformat()
                ))
                
                request_id = cursor.lastrowid
                conn.commit()
                return request_id
                
        except sqlite3.Error as e:
            logger.error(f"Ошибка сохранения заявки: {e}")
            raise
    
    def get_requests_by_filter(self, status: str) -> List[Dict]:
        """Получает заявки по статусу"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests WHERE status = ? ORDER BY created_at DESC
                ''', (status,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения заявок: {e}")
            return []
    
    def get_statistics(self, days: int = 7) -> Dict:
        """Получает статистику за указанный период"""
        try:
            since_date = (datetime.now() - timedelta(days=days)).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                        SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress
                    FROM requests 
                    WHERE created_at > ?
                ''', (since_date,))
                
                result = cursor.fetchone()
                return {
                    'total': result[0] if result else 0,
                    'completed': result[1] if result else 0,
                    'new': result[2] if result else 0,
                    'in_progress': result[3] if result else 0
                }
                
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return {'total': 0, 'completed': 0, 'new': 0, 'in_progress': 0}

# ==================== НОВЫЕ УТИЛИТЫ ====================

class AdvancedValidators(Validators):
    """Расширенный класс валидации с сохранением старых методов"""
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Валидация email адреса"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email.strip()))
    
    @staticmethod
    def validate_plot_number(plot: str) -> bool:
        """Валидация номера участка"""
        return bool(re.match(r'^[А-Яа-яA-Za-z0-9\s\-]{2,20}$', plot.strip()))
    
    @staticmethod
    def sanitize_text(text: str) -> str:
        """Очистка текста от потенциально опасных символов"""
        return re.sub(r'[<>&\"\']', '', text.strip())
    
    @staticmethod
    def validate_phone_extended(phone: str) -> Tuple[bool, str]:
        """Расширенная валидация телефона с нормализацией"""
        # Удаляем все нецифровые символы кроме +
        cleaned = re.sub(r'[^\d+]', '', phone)
        
        if len(cleaned) < 10:
            return False, "Слишком короткий номер"
        
        if len(cleaned) > 15:
            return False, "Слишком длинный номер"
        
        return True, cleaned

class NotificationManager:
    """Менеджер уведомлений с расширенными функциями"""
    
    def __init__(self, bot):
        self.bot = bot
        self.notification_queue = []
        self.lock = threading.Lock()
    
    def add_notification(self, chat_id: int, text: str, photo: str = None, 
                        keyboard: List[List[str]] = None, priority: int = 1):
        """Добавляет уведомление в очередь"""
        with self.lock:
            self.notification_queue.append({
                'chat_id': chat_id,
                'text': text,
                'photo': photo,
                'keyboard': keyboard,
                'priority': priority,
                'timestamp': datetime.now(),
                'attempts': 0
            })
            # Сортируем по приоритету
            self.notification_queue.sort(key=lambda x: x['priority'])
    
    def send_priority_notification(self, chat_ids: List[int], text: str, 
                                 parse_mode: str = ParseMode.MARKDOWN):
        """Отправляет приоритетное уведомление нескольким пользователям"""
        for chat_id in chat_ids:
            try:
                self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode
                )
                logger.info(f"Приоритетное уведомление отправлено {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки приоритетного уведомления {chat_id}: {e}")
    
    def process_queue(self):
        """Обрабатывает очередь уведомлений (вызывается периодически)"""
        with self.lock:
            for notification in self.notification_queue[:10]:  # Обрабатываем первые 10
                try:
                    if notification['photo']:
                        self.bot.send_photo(
                            chat_id=notification['chat_id'],
                            photo=notification['photo'],
                            caption=notification['text'],
                            reply_markup=ReplyKeyboardMarkup(
                                notification['keyboard'], 
                                resize_keyboard=True
                            ) if notification['keyboard'] else None,
                            parse_mode=ParseMode.MARKDOWN
                        )
                    else:
                        self.bot.send_message(
                            chat_id=notification['chat_id'],
                            text=notification['text'],
                            reply_markup=ReplyKeyboardMarkup(
                                notification['keyboard'], 
                                resize_keyboard=True
                            ) if notification['keyboard'] else None,
                            parse_mode=ParseMode.MARKDOWN
                        )
                    self.notification_queue.remove(notification)
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}")
                    # Удаляем проблемное уведомление после 3 попыток
                    if notification.get('attempts', 0) >= 3:
                        self.notification_queue.remove(notification)
                    else:
                        notification['attempts'] = notification.get('attempts', 0) + 1

class EnhancedBackupManager(BackupManager):
    """Расширенный менеджер бэкапов"""
    
    @staticmethod
    def create_encrypted_backup(password: str = None):
        """Создает зашифрованный бэкап (базовая реализация)"""
        backup_path = BackupManager.create_backup()
        if backup_path and password:
            # Здесь может быть реализация шифрования
            logger.info(f"Бэкап создан: {backup_path} (шифрование отключено)")
        return backup_path
    
    @staticmethod
    def get_backup_info():
        """Возвращает информацию о бэкапах"""
        try:
            backups = []
            for f in os.listdir(BACKUP_DIR):
                if f.startswith('backup_') and f.endswith('.db'):
                    path = os.path.join(BACKUP_DIR, f)
                    stats = os.stat(path)
                    backups.append({
                        'name': f,
                        'size': stats.st_size,
                        'created': datetime.fromtimestamp(stats.st_ctime),
                        'path': path
                    })
            
            # Сортируем по дате создания
            backups.sort(key=lambda x: x['created'], reverse=True)
            return backups
        except Exception as e:
            logger.error(f"Ошибка получения информации о бэкапах: {e}")
            return []
    
    @staticmethod
    def cleanup_old_backups():
        """Удаляет старые бэкапы"""
        try:
            cutoff_date = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
            backups = EnhancedBackupManager.get_backup_info()
            
            deleted_count = 0
            for backup in backups:
                if backup['created'] < cutoff_date:
                    os.remove(backup['path'])
                    deleted_count += 1
                    logger.info(f"Удален старый бэкап: {backup['name']}")
            
            return deleted_count
        except Exception as e:
            logger.error(f"Ошибка очистки бэкапов: {e}")
            return 0

# ==================== РАСШИРЕННАЯ БАЗА ДАННЫХ ====================

class EnhancedDatabase(Database):
    """Расширенная база данных с новыми функциями"""
    
    def log_request_change(self, request_id: int, action: str, old_value: str, 
                          new_value: str, changed_by: str):
        """Логирует изменения заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO request_history 
                    (request_id, action, old_value, new_value, changed_by, changed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (request_id, action, old_value, new_value, changed_by, 
                      datetime.now().isoformat()))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Ошибка логирования изменения заявки: {e}")
    
    def get_request_history(self, request_id: int) -> List[Dict]:
        """Получает историю изменений заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM request_history 
                    WHERE request_id = ? 
                    ORDER BY changed_at DESC
                ''', (request_id,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения истории заявки: {e}")
            return []
    
    def get_urgent_requests(self, hours: int = 2) -> List[Dict]:
        """Получает срочные заявки с истекающим сроком"""
        try:
            time_threshold = (datetime.now() - timedelta(hours=hours)).isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE urgency LIKE '%Срочно%' 
                    AND status IN ('new', 'in_progress')
                    AND created_at > ?
                    ORDER BY created_at ASC
                ''', (time_threshold,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения срочных заявок: {e}")
            return []
    
    def get_user_statistics(self, user_id: int) -> Dict:
        """Получает расширенную статистику пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Основная статистика
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total_requests,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                        MIN(created_at) as first_request,
                        MAX(created_at) as last_request
                    FROM requests 
                    WHERE user_id = ?
                ''', (user_id,))
                
                stats = cursor.fetchone()
                if stats:
                    columns = [column[0] for column in cursor.description]
                    result = dict(zip(columns, stats))
                    
                    # Среднее время выполнения
                    cursor.execute('''
                        SELECT AVG(
                            (julianday(updated_at) - julianday(created_at)) * 24
                        ) as avg_hours
                        FROM requests 
                        WHERE user_id = ? AND status = 'completed'
                    ''', (user_id,))
                    
                    avg_hours = cursor.fetchone()[0]
                    result['avg_completion_hours'] = round(avg_hours, 2) if avg_hours else 0
                    
                    return result
                return {}
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения статистики пользователя: {e}")
            return {}
    
    def get_stuck_requests(self, hours: int = 24) -> List[Dict]:
        """Получает заявки, которые зависли дольше указанного времени"""
        try:
            time_threshold = (datetime.now() - timedelta(hours=hours)).isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE status IN ('new', 'in_progress')
                    AND created_at < ?
                    ORDER BY created_at ASC
                ''', (time_threshold,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения зависших заявок: {e}")
            return []

# ==================== НОВЫЕ КЛАВИАТУРЫ ====================

# Расширенное главное меню пользователя
enhanced_user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['📊 Моя статистика', '🆘 Срочная помощь'],
    ['ℹ️ О боте', '🔔 Настройки уведомлений']  # Новые кнопки
]

# Расширенное админ-меню
enhanced_admin_main_menu_keyboard = [
    ['🆕 Новые заявки', '🔄 В работе'],
    ['⏰ Срочные заявки', '📊 Статистика'],
    ['👥 Пользователи', '⚙️ Настройки'],
    ['💾 Бэкапы', '🔄 Обновить'],
    ['🚨 Зависшие заявки', '📈 Аналитика']  # Новые кнопки
]

# Меню настроек
settings_keyboard = [
    ['📊 Общая статистика', '🔔 Уведомления'],
    ['🔄 Авто-обновление', '💾 Управление бэкапами'],
    ['⚡ Быстрые действия', '🔧 Расширенные настройки'],
    ['🔙 Назад в админ-панель']
]

# Меню бэкапов
backup_keyboard = [
    ['💾 Создать бэкап', '📋 Список бэкапов'],
    ['🧹 Очистить старые', '🔐 Зашифровать бэкапы'],
    ['🔙 Назад']
]

# Меню уведомлений
notification_keyboard = [
    ['🔔 Включить уведомления', '🔕 Выключить уведомления'],
    ['📢 Экстренные уведомления', '📅 Напоминания'],
    ['🔙 Назад в меню']
]

# ==================== СОВМЕСТИМОСТЬ СО СТАРЫМИ ФУНКЦИЯМИ ====================

# Старые клавиатуры (для обратной совместимости)
user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['🆘 Срочная помощь']
]

admin_main_menu_keyboard = [
    ['🆕 Новые заявки', '🔄 В работе'],
    ['📊 Статистика', '⚙️ Настройки']
]

# ==================== ОБРАБОТЧИКИ РАЗГОВОРА (совместимость) ====================

def name(update: Update, context: CallbackContext) -> int:
    """Обработка имени (старая функция)"""
    context.user_data['name'] = update.message.text
    update.message.reply_text(
        "📞 Теперь введите ваш номер телефона:",
        reply_markup=ReplyKeyboardRemove()
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    """Обработка телефона (старая функция)"""
    context.user_data['phone'] = update.message.text
    update.message.reply_text(
        "📍 Введите номер участка:",
        reply_markup=ReplyKeyboardRemove()
    )
    return PLOT

def plot(update: Update, context: CallbackContext) -> int:
    """Обработка участка (старая функция)"""
    context.user_data['plot'] = update.message.text
    
    keyboard = [['🔌 Электрика', '📶 Сети'], ['📞 Телефония', '🎥 Видеонаблюдение']]
    update.message.reply_text(
        "🔧 Выберите тип системы:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    """Обработка типа системы (старая функция)"""
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        "📝 Опишите проблему:",
        reply_markup=ReplyKeyboardRemove()
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    """Обработка проблемы (старая функция)"""
    context.user_data['problem'] = update.message.text
    
    keyboard = [['🔴 Срочно', '🟡 Средняя'], ['🟢 Не срочно']]
    update.message.reply_text(
        "⏰ Выберите срочность:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    """Обработка срочности (старая функция)"""
    context.user_data['urgency'] = update.message.text
    update.message.reply_text(
        "📸 Пришлите фото проблемы (или нажмите 'Пропустить'):",
        reply_markup=ReplyKeyboardMarkup([['📷 Пропустить']], resize_keyboard=True)
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    """Обработка фото (старая функция)"""
    if update.message.photo:
        # Сохраняем файл ID фото
        photo_file = update.message.photo[-1].file_id
        context.user_data['photo'] = photo_file
    else:
        context.user_data['photo'] = None
    
    return show_request_summary(update, context)

def show_request_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку заявки (старая функция)"""
    user_data = context.user_data
    
    summary_text = (
        "📋 *Сводка заявки:*\n\n"
        f"👤 *Имя:* {user_data.get('name', 'Не указано')}\n"
        f"📞 *Телефон:* {user_data.get('phone', 'Не указано')}\n"
        f"📍 *Участок:* {user_data.get('plot', 'Не указано')}\n"
        f"🔧 *Система:* {user_data.get('system_type', 'Не указано')}\n"
        f"⏰ *Срочность:* {user_data.get('urgency', 'Не указано')}\n"
        f"📝 *Проблема:* {user_data.get('problem', 'Не указано')}\n"
        f"📸 *Фото:* {'✅ Есть' if user_data.get('photo') else '❌ Нет'}\n\n"
        "Подтвердите отправку заявки:"
    )
    
    keyboard = [['✅ Подтвердить отправку', '✏️ Редактировать заявку']]
    
    if update.message:
        update.message.reply_text(
            summary_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        update.callback_query.message.reply_text(
            summary_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    return ConversationHandler.END

def cancel_request(update: Update, context: CallbackContext) -> int:
    """Отмена заявки (старая функция)"""
    update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
    )
    context.user_data.clear()
    return ConversationHandler.END

# ==================== РАСШИРЕННЫЕ ФУНКЦИИ СОЗДАНИЯ ЗАЯВКИ ====================

def enhanced_start_request_creation(update: Update, context: CallbackContext) -> int:
    """Улучшенное начало создания заявки с проверкой лимитов и статистики"""
    user_id = update.message.from_user.id
    
    # Проверка расширенного лимита
    if rate_limiter.is_limited(user_id, 'create_request', MAX_REQUESTS_PER_HOUR):
        user_stats = db.get_user_statistics(user_id)
        
        update.message.reply_text(
            "❌ *Превышен лимит запросов!*\n\n"
            f"📊 *Ваша статистика:*\n"
            f"• Всего заявок: {user_stats.get('total_requests', 0)}\n"
            f"• Выполнено: {user_stats.get('completed', 0)}\n"
            f"• В работе: {user_stats.get('in_progress', 0)}\n\n"
            "Вы можете создавать не более 15 заявок в час.\n"
            "Пожалуйста, попробуйте позже.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    context.user_data.clear()
    
    user = update.message.from_user
    context.user_data.update({
        'user_id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'creation_started': datetime.now().isoformat()
    })
    
    # Показываем статистику пользователя
    user_stats = db.get_user_statistics(user_id)
    if user_stats.get('total_requests', 0) > 0:
        stats_text = (
            f"📊 *Ваша статистика:*\n"
            f"• Всего заявок: {user_stats['total_requests']}\n"
            f"• Выполнено: {user_stats['completed']}\n"
            f"• Среднее время выполнения: {user_stats.get('avg_completion_hours', 0)} ч.\n\n"
        )
    else:
        stats_text = "🎉 *Это ваша первая заявка!*\n\n"
    
    update.message.reply_text(
        f"{stats_text}"
        "📝 *Создание новой заявки*\n\n"
        "Для начала укажите ваше имя:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def enhanced_confirm_request(update: Update, context: CallbackContext) -> None:
    """Улучшенное подтверждение заявки с дополнительными проверками"""
    if update.message.text == '✅ Подтвердить отправку':
        user = update.message.from_user
        
        try:
            # Дополнительная проверка данных
            required_fields = ['name', 'phone', 'plot', 'system_type', 'problem', 'urgency']
            for field in required_fields:
                if field not in context.user_data or not context.user_data[field]:
                    update.message.reply_text(
                        f"❌ Отсутствует обязательное поле: {field}",
                        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
                    )
                    return
            
            # Сохраняем заявку
            request_id = db.save_request(context.user_data)
            
            # Логируем создание
            db.log_request_change(
                request_id=request_id,
                action='created',
                old_value='',
                new_value='new',
                changed_by=f"user_{user.id}"
            )
            
            # Отправляем уведомления
            enhanced_send_admin_notification(context, context.user_data, request_id)
            
            # Расширенное подтверждение пользователю
            user_stats = db.get_user_statistics(user.id)
            
            confirmation_text = (
                f"✅ *Заявка #{request_id} успешно создана!*\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n"
                f"📍 *Участок:* {context.user_data['plot']}\n\n"
                f"📊 *Ваша статистика:* {user_stats.get('total_requests', 0)} заявок "
                f"({user_stats.get('completed', 0)} выполнено)\n\n"
                f"_Спасибо за обращение в службу слаботочных систем завода Контакт!_ 🛠️"
            )
            
            # Определяем клавиатуру
            if user.id in ADMIN_CHAT_IDS:
                reply_markup = ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
            else:
                reply_markup = ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
            
            update.message.reply_text(
                confirmation_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            
            error_text = (
                "❌ *Произошла ошибка при создании заявки.*\n\n"
                "Пожалуйста, попробуйте позже или обратитесь к администратору."
            )
            
            if user.id in ADMIN_CHAT_IDS:
                update.message.reply_text(
                    error_text,
                    reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    error_text,
                    reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
        
        context.user_data.clear()
        
    elif update.message.text == '✏️ Редактировать заявку':
        context.user_data['editing_mode'] = True
        return edit_request_choice(update, context)

def enhanced_send_admin_notification(context: CallbackContext, user_data: Dict, request_id: int) -> None:
    """Расширенное уведомление администраторов"""
    # Основное уведомление
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
    
    # Уведомление о срочности
    if '🔴 Срочно' in user_data.get('urgency', ''):
        notification_text = "🔴🔴🔴 СРОЧНАЯ ЗАЯВКА 🔴🔴🔴\n\n" + notification_text
    
    for admin_id in ADMIN_CHAT_IDS:
        try:
            if user_data.get('photo'):
                context.bot.send_photo(
                    chat_id=admin_id,
                    photo=user_data['photo'],
                    caption=notification_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{request_id}"),
                        InlineKeyboardButton("📋 Подробнее", callback_data=f"view_{request_id}")
                    ]])
                )
            else:
                context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{request_id}"),
                        InlineKeyboardButton("📋 Подробнее", callback_data=f"view_{request_id}")
                    ]])
                )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")

# ==================== НОВЫЕ КОМАНДЫ ====================

def show_user_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает статистику пользователя"""
    user_id = update.message.from_user.id
    user_stats = db.get_user_statistics(user_id)
    
    if not user_stats or user_stats.get('total_requests', 0) == 0:
        update.message.reply_text(
            "📊 *Ваша статистика*\n\n"
            "У вас пока нет созданных заявок.\n\n"
            "Создайте первую заявку, чтобы начать отслеживать статистику!",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Рассчитываем дополнительные метрики
    completion_rate = (user_stats['completed'] / user_stats['total_requests']) * 100
    avg_hours = user_stats.get('avg_completion_hours', 0)
    
    stats_text = (
        "📊 *Ваша статистика заявок*\n\n"
        f"📈 *Всего заявок:* {user_stats['total_requests']}\n"
        f"✅ *Выполнено:* {user_stats['completed']}\n"
        f"🔄 *В работе:* {user_stats.get('in_progress', 0)}\n"
        f"🆕 *Новых:* {user_stats.get('new', 0)}\n\n"
        f"📊 *Эффективность:*\n"
        f"• Процент выполнения: {completion_rate:.1f}%\n"
        f"• Среднее время выполнения: {avg_hours} часов\n\n"
    )
    
    # Добавляем информацию о первой и последней заявке
    if user_stats.get('first_request'):
        first_date = datetime.fromisoformat(user_stats['first_request']).strftime('%d.%m.%Y')
        stats_text += f"🎉 *Первая заявка:* {first_date}\n"
    
    if user_stats.get('last_request'):
        last_date = datetime.fromisoformat(user_stats['last_request']).strftime('%d.%m.%Y')
        stats_text += f"📅 *Последняя заявка:* {last_date}\n"
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def emergency_help(update: Update, context: CallbackContext) -> None:
    """Экстренная помощь"""
    user_id = update.message.from_user.id
    
    emergency_text = (
        "🆘 *Экстренная помощь*\n\n"
        "Для срочных вопросов и аварийных ситуаций:\n\n"
        "📞 *Телефон службы поддержки:*\n"
        "+7 (XXX) XXX-XX-XX\n\n"
        "👨‍💼 *Ответственный:*\n"
        "Иванов Иван Иванович\n\n"
        "📍 *Местоположение службы:*\n"
        "Главный корпус, кабинет 101\n\n"
        "⏰ *Режим работы:*\n"
        "Пн-Пт: 8:00-17:00\n"
        "Сб: 9:00-15:00\n"
        "Вс: выходной\n\n"
        "⚠️ *Для аварийных ситуаций:*\n"
        "Круглосуточный телефон: +7 (XXX) XXX-XX-XX"
    )
    
    update.message.reply_text(
        emergency_text,
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Уведомляем администраторов об обращении к экстренной помощи
    admin_notification = (
        f"🆘 Пользователь @{update.message.from_user.username or 'N/A'} "
        f"обратился к экстренной помощи"
    )
    
    for admin_id in ADMIN_CHAT_IDS:
        try:
            context.bot.send_message(admin_id, admin_notification)
        except Exception as e:
            logger.error(f"Ошибка уведомления администратора: {e}")

def show_bot_info(update: Update, context: CallbackContext) -> None:
    """Показывает информацию о боте"""
    info_text = (
        "ℹ️ *Информация о боте*\n\n"
        "🤖 *Бот службы слаботочных систем*\n"
        "Завод Контакт\n\n"
        "📊 *Возможности:*\n"
        "• Создание заявок на обслуживание\n"
        "• Отслеживание статуса заявок\n"
        "• Статистика и аналитика\n"
        "• Уведомления о статусах\n\n"
        "🛠️ *Техническая информация:*\n"
        f"• Версия: 2.0 (расширенная)\n"
        f"• База данных: SQLite\n"
        f"• Лимит заявок: {MAX_REQUESTS_PER_HOUR}/час\n\n"
        "📞 *Поддержка:*\n"
        "По техническим вопросам обращайтесь к администратору"
    )
    
    update.message.reply_text(
        info_text,
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def notification_settings(update: Update, context: CallbackContext) -> None:
    """Настройки уведомлений"""
    user_id = update.message.from_user.id
    
    settings_text = (
        "🔔 *Настройки уведомлений*\n\n"
        "Вы можете настроить получение уведомлений:\n\n"
        "• 🔔 Все уведомления\n"
        "• 🔕 Только важные\n"
        "• 📢 Только экстренные\n"
        "• 📅 Напоминания\n\n"
        "Выберите тип уведомлений:"
    )
    
    update.message.reply_text(
        settings_text,
        reply_markup=ReplyKeyboardMarkup(notification_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== РАСШИРЕННАЯ АДМИН-ПАНЕЛЬ ====================

def get_enhanced_admin_panel():
    """Возвращает расширенную админ-панель"""
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    urgent_requests = db.get_urgent_requests()
    stuck_requests = db.get_stuck_requests(REQUEST_TIMEOUT_HOURS)
    
    return [
        [f'🆕 Новые ({len(new_requests)})', f'🔄 В работе ({len(in_progress_requests)})'],
        [f'⏰ Срочные ({len(urgent_requests)})', f'🚨 Зависшие ({len(stuck_requests)})'],
        ['📊 Статистика', '📈 Аналитика'],
        ['👥 Пользователи', '⚙️ Настройки'],
        ['💾 Бэкапы', '🔄 Обновить']
    ]

def show_enhanced_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает расширенную админ-панель"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu(update, context)
    
    # Получаем расширенную статистику
    stats = db.get_statistics(7)  # За 7 дней
    urgent_requests = db.get_urgent_requests()
    stuck_requests = db.get_stuck_requests(REQUEST_TIMEOUT_HOURS)
    
    admin_text = (
        "👑 *Расширенная админ-панель завода Контакт*\n\n"
        f"📊 *За последние 7 дней:*\n"
        f"• Всего заявок: {stats['total']}\n"
        f"• Выполнено: {stats['completed']}\n"
        f"• Новых: {stats['new']}\n"
        f"• В работе: {stats['in_progress']}\n\n"
        f"⚠️ *Требуют внимания:*\n"
        f"• Срочные заявки: {len(urgent_requests)}\n"
        f"• Зависшие заявки: {len(stuck_requests)}\n\n"
        "Выберите раздел для управления:"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_users_management(update: Update, context: CallbackContext) -> None:
    """Управление пользователями"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, first_name, last_name, request_count, created_at
                FROM users 
                ORDER BY request_count DESC 
                LIMIT 50
            ''')
            users = cursor.fetchall()
        
        if not users:
            update.message.reply_text(
                "👥 *Управление пользователями*\n\n"
                "Пользователей не найдено.",
                reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        users_text = "👥 *Топ пользователей по количеству заявок:*\n\n"
        
        for i, (user_id, username, first_name, last_name, request_count, created_at) in enumerate(users[:10], 1):
            user_display = username or f"{first_name} {last_name}".strip() or f"ID: {user_id}"
            users_text += f"{i}. {user_display} - {request_count} заявок\n"
        
        users_text += f"\nВсего пользователей: {len(users)}"
        
        update.message.reply_text(
            users_text,
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        update.message.reply_text(
            "❌ Ошибка получения списка пользователей.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
        )

def show_settings(update: Update, context: CallbackContext) -> None:
    """Показывает настройки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    settings_text = (
        "⚙️ *Настройки системы*\n\n"
        f"🤖 *Бот:*\n"
        f"• Администраторов: {len(ADMIN_CHAT_IDS)}\n"
        f"• Лимит заявок: {MAX_REQUESTS_PER_HOUR}/час\n"
        f"• Хранение бэкапов: {BACKUP_RETENTION_DAYS} дней\n"
        f"• Таймаут заявок: {REQUEST_TIMEOUT_HOURS} часов\n\n"
        f"💾 *База данных:*\n"
        f"• Путь: {DB_PATH}\n"
        f"• Размер: {os.path.getsize(DB_PATH) / 1024 / 1024:.2f} МБ\n\n"
        "Выберите раздел для настройки:"
    )
    
    update.message.reply_text(
        settings_text,
        reply_markup=ReplyKeyboardMarkup(settings_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_backup_management(update: Update, context: CallbackContext) -> None:
    """Управление бэкапами"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    backups = EnhancedBackupManager.get_backup_info()
    total_size = sum(b['size'] for b in backups) / 1024 / 1024  # в МБ
    
    backup_text = (
        "💾 *Управление бэкапами*\n\n"
        f"📊 *Статистика:*\n"
        f"• Всего бэкапов: {len(backups)}\n"
        f"• Общий размер: {total_size:.2f} МБ\n"
        f"• Авто-очистка: {BACKUP_RETENTION_DAYS} дней\n\n"
        "Выберите действие:"
    )
    
    update.message.reply_text(
        backup_text,
        reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def list_backups(update: Update, context: CallbackContext) -> None:
    """Показывает список бэкапов"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    backups = EnhancedBackupManager.get_backup_info()
    
    if not backups:
        update.message.reply_text(
            "📋 *Список бэкапов*\n\n"
            "Бэкапы не найдены.",
            reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    backups_text = "📋 *Последние 10 бэкапов:*\n\n"
    
    for i, backup in enumerate(backups[:10], 1):
        size_mb = backup['size'] / 1024 / 1024
        date_str = backup['created'].strftime('%d.%m.%Y %H:%M')
        backups_text += f"{i}. {backup['name']}\n"
        backups_text += f"   📅 {date_str} | 💾 {size_mb:.1f} МБ\n\n"
    
    update.message.reply_text(
        backups_text,
        reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def cleanup_backups(update: Update, context: CallbackContext) -> None:
    """Очищает старые бэкапы"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    deleted_count = EnhancedBackupManager.cleanup_old_backups()
    
    if deleted_count > 0:
        update.message.reply_text(
            f"🧹 *Очистка бэкапов*\n\n"
            f"Удалено {deleted_count} старых бэкапов.",
            reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        update.message.reply_text(
            "🧹 *Очистка бэкапов*\n\n"
            "Старые бэкапы не найдены.",
            reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )

def show_stuck_requests(update: Update, context: CallbackContext) -> None:
    """Показывает зависшие заявки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    stuck_requests = db.get_stuck_requests(REQUEST_TIMEOUT_HOURS)
    
    if not stuck_requests:
        update.message.reply_text(
            "🚨 *Зависшие заявки*\n\n"
            "Зависших заявок не найдено.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = f"🚨 *Зависшие заявки ({len(stuck_requests)}):*\n\n"
    
    for req in stuck_requests[:10]:
        created_time = datetime.fromisoformat(req['created_at'])
        time_diff = datetime.now() - created_time
        hours_passed = time_diff.total_seconds() / 3600
        
        text += (
            f"⚠️ *Заявка #{req['id']}*\n"
            f"📍 {req['plot']} | {req['system_type']}\n"
            f"⏰ Висит: {hours_passed:.1f} ч.\n"
            f"👤 {req['name']} | 📞 {req['phone']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
    
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ====================

def enhanced_handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Улучшенный обработчик главного меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        return show_enhanced_admin_panel(update, context)
    
    if text == '📝 Создать заявку':
        return enhanced_start_request_creation(update, context)
    elif text == '📋 Мои заявки':
        return show_my_requests(update, context)
    elif text == '📊 Моя статистика':
        return show_user_statistics(update, context)
    elif text == '🆘 Срочная помощь':
        return emergency_help(update, context)
    elif text == 'ℹ️ О боте':
        return show_bot_info(update, context)
    elif text == '🔔 Настройки уведомлений':
        return notification_settings(update, context)
    else:
        update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

def enhanced_handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Улучшенный обработчик админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if text.startswith('🆕 Новые'):
        return show_requests_by_filter(update, context, 'new')
    elif text.startswith('🔄 В работе'):
        return show_requests_by_filter(update, context, 'in_progress')
    elif text.startswith('⏰ Срочные'):
        return show_urgent_requests(update, context)
    elif text.startswith('🚨 Зависшие'):
        return show_stuck_requests(update, context)
    elif text == '📊 Статистика':
        return show_statistics(update, context)
    elif text == '📈 Аналитика':
        return show_analytics(update, context)
    elif text == '👥 Пользователи':
        return show_users_management(update, context)
    elif text == '⚙️ Настройки':
        return show_settings(update, context)
    elif text == '💾 Бэкапы':
        return show_backup_management(update, context)
    elif text == '🔄 Обновить':
        return show_enhanced_admin_panel(update, context)

def show_urgent_requests(update: Update, context: CallbackContext) -> None:
    """Показывает срочные заявки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    urgent_requests = db.get_urgent_requests()
    
    if not urgent_requests:
        update.message.reply_text(
            "⏰ *Срочные заявки*\n\n"
            "Срочных заявок не найдено.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = f"⏰ *Срочные заявки ({len(urgent_requests)}):*\n\n"
    
    for req in urgent_requests[:10]:
        created_time = datetime.fromisoformat(req['created_at'])
        time_diff = datetime.now() - created_time
        hours_passed = time_diff.total_seconds() / 3600
        
        text += (
            f"🔴 *Заявка #{req['id']}*\n"
            f"📍 {req['plot']} | {req['system_type']}\n"
            f"⏰ Прошло: {hours_passed:.1f} ч.\n"
            f"👤 {req['name']} | 📞 {req['phone']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
    
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_analytics(update: Update, context: CallbackContext) -> None:
    """Показывает аналитику"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    # Статистика за разные периоды
    stats_7_days = db.get_statistics(7)
    stats_30_days = db.get_statistics(30)
    
    analytics_text = (
        "📈 *Аналитика системы*\n\n"
        "📊 *За последние 7 дней:*\n"
        f"• Всего заявок: {stats_7_days['total']}\n"
        f"• Выполнено: {stats_7_days['completed']}\n"
        f"• В работе: {stats_7_days['in_progress']}\n"
        f"• Новых: {stats_7_days['new']}\n\n"
        "📅 *За последние 30 дней:*\n"
        f"• Всего заявок: {stats_30_days['total']}\n"
        f"• Выполнено: {stats_30_days['completed']}\n"
        f"• В работе: {stats_30_days['in_progress']}\n"
        f"• Новых: {stats_30_days['new']}\n\n"
        "📈 *Эффективность:*\n"
        f"• Процент выполнения (7 дней): {(stats_7_days['completed']/stats_7_days['total']*100) if stats_7_days['total'] > 0 else 0:.1f}%\n"
        f"• Процент выполнения (30 дней): {(stats_30_days['completed']/stats_30_days['total']*100) if stats_30_days['total'] > 0 else 0:.1f}%"
    )
    
    update.message.reply_text(
        analytics_text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ФУНКЦИИ РЕДАКТИРОВАНИЯ ====================

def edit_request_choice(update: Update, context: CallbackContext):
    """Начало редактирования заявки"""
    keyboard = [
        ['👤 Имя', '📞 Телефон', '📍 Участок'],
        ['🔧 Система', '📝 Проблема', '⏰ Срочность'],
        ['📸 Фото', '✅ Завершить редактирование']
    ]
    
    update.message.reply_text(
        "✏️ *Редактирование заявки*\n\nВыберите поле для редактирования:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return EDIT_CHOICE

def handle_edit_choice(update: Update, context: CallbackContext):
    """Обработка выбора поля для редактирования"""
    choice = update.message.text
    field_map = {
        '👤 Имя': 'name',
        '📞 Телефон': 'phone', 
        '📍 Участок': 'plot',
        '🔧 Система': 'system_type',
        '📝 Проблема': 'problem',
        '⏰ Срочность': 'urgency',
        '📸 Фото': 'photo'
    }
    
    if choice == '✅ Завершить редактирование':
        return show_request_summary(update, context)
    
    field = field_map.get(choice)
    if field:
        context.user_data['editing_field'] = field
        
        if field == 'photo':
            update.message.reply_text(
                "📸 Пришлите новое фото проблемы:",
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            current_value = context.user_data.get(field, 'Не указано')
            update.message.reply_text(
                f"Введите новое значение для '{choice}':\nТекущее значение: {current_value}",
                reply_markup=ReplyKeyboardRemove()
            )
        
        return EDIT_FIELD
    
    update.message.reply_text("Пожалуйста, выберите поле из меню.")
    return EDIT_CHOICE

def handle_edit_field(update: Update, context: CallbackContext):
    """Обработка ввода нового значения поля"""
    field = context.user_data.get('editing_field')
    
    if not field:
        return edit_request_choice(update, context)
    
    if field == 'photo':
        if update.message.photo:
            context.user_data[field] = update.message.photo[-1].file_id
        else:
            context.user_data[field] = None
    else:
        context.user_data[field] = update.message.text
    
    del context.user_data['editing_field']
    update.message.reply_text(f"✅ Поле успешно обновлено!")
    
    return edit_request_choice(update, context)

# ==================== УТИЛИТЫ И ОБРАБОТЧИКИ ОШИБОК ====================

def error_handler(update: Update, context: CallbackContext):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.message:
        update.message.reply_text(
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже.",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

def backup_job(context: CallbackContext):
    """Задание для автоматического бэкапа"""
    try:
        backup_path = BackupManager.create_backup()
        if backup_path:
            logger.info(f"Автоматический бэкап создан: {backup_path}")
    except Exception as e:
        logger.error(f"Ошибка автоматического бэкапа: {e}")

def create_backup_command(update: Update, context: CallbackContext):
    """Команда создания бэкапа"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    backup_path = EnhancedBackupManager.create_encrypted_backup()
    if backup_path:
        update.message.reply_text(
            f"✅ Бэкап успешно создан!\n📁 Файл: {os.path.basename(backup_path)}",
            reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True)
        )
    else:
        update.message.reply_text(
            "❌ Ошибка создания бэкапа",
            reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True)
        )

def show_my_requests(update: Update, context: CallbackContext):
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    # Реализация показа заявок пользователя
    update.message.reply_text(
        "📋 Функция показа заявок в разработке",
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
    )

def show_statistics(update: Update, context: CallbackContext):
    """Показывает общую статистику"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    stats = db.get_statistics(7)
    update.message.reply_text(
        f"📊 Статистика за 7 дней:\n\n"
        f"• Всего заявок: {stats['total']}\n"
        f"• Выполнено: {stats['completed']}\n"
        f"• Новых: {stats['new']}\n"
        f"• В работе: {stats['in_progress']}",
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_admin_callback(update: Update, context: CallbackContext):
    """Обработчик callback для админ-панели"""
    query = update.callback_query
    query.answer()
    
    # Базовая реализация
    query.edit_message_text(
        text=f"Обработан запрос: {query.data}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Назад", callback_data="back_admin")
        ]])
    )

def check_urgent_requests(context: CallbackContext):
    """Проверяет срочные заявки и отправляет напоминания"""
    try:
        urgent_requests = db.get_urgent_requests()
        
        for request in urgent_requests:
            if request['status'] == 'new':
                # Уведомление о невзятых срочных заявках
                notification_text = (
                    f"⏰ *Напоминание о срочной заявке #{request['id']}*\n\n"
                    f"Заявка ожидает взятия в работу более 1 часа!\n"
                    f"📍 {request['plot']} | {request['system_type']}\n"
                    f"👤 {request['name']} | 📞 {request['phone']}"
                )
                
                notification_manager.send_priority_notification(
                    ADMIN_CHAT_IDS,
                    notification_text
                )
                
    except Exception as e:
        logger.error(f"Ошибка проверки срочных заявок: {e}")

def handle_settings(update: Update, context: CallbackContext):
    """Обрабатывает команды настроек"""
    text = update.message.text
    
    if text == '🔙 Назад в админ-панель':
        return show_enhanced_admin_panel(update, context)
    elif text == '💾 Управление бэкапами':
        return show_backup_management(update, context)
    # Добавьте обработку других настроек по необходимости

def handle_backup_commands(update: Update, context: CallbackContext):
    """Обрабатывает команды управления бэкапами"""
    text = update.message.text
    
    if text == '🔙 Назад':
        return show_settings(update, context)
    elif text == '💾 Создать бэкап':
        return create_backup_command(update, context)
    elif text == '📋 Список бэкапов':
        return list_backups(update, context)
    elif text == '🧹 Очистить старые':
        return cleanup_backups(update, context)

def show_requests_by_filter(update: Update, context: CallbackContext, status: str):
    """Показывает заявки по фильтру"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    requests = db.get_requests_by_filter(status)
    
    if not requests:
        update.message.reply_text(
            f"📋 *Заявки со статусом '{status}'*\n\n"
            "Заявок не найдено.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = f"📋 *Заявки ({status}):*\n\n"
    
    for req in requests[:5]:
        created_time = datetime.fromisoformat(req['created_at'])
        time_str = created_time.strftime('%d.%m.%Y %H:%M')
        
        text += (
            f"📄 *Заявка #{req['id']}*\n"
            f"📍 {req['plot']} | {req['system_type']}\n"
            f"👤 {req['name']} | 📞 {req['phone']}\n"
            f"🕒 {time_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
    
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== СОВМЕСТИМОСТЬ СО СТАРЫМИ ФУНКЦИЯМИ ====================

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Совместимость со старым кодом"""
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        # Используем старую админ-панель для совместимости
        update.message.reply_text(
            "👑 Админ-панель завода Контакт\n\nВыберите действие:",
            reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True)
        )
    else:
        # Используем старую пользовательскую панель для совместимости
        update.message.reply_text(
            "Добро пожаловать в службу слаботочных систем завода Контакт!\n\nВыберите действие:",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )

def start_request_creation(update: Update, context: CallbackContext) -> int:
    """Совместимость со старым кодом"""
    return enhanced_start_request_creation(update, context)

def confirm_request(update: Update, context: CallbackContext) -> None:
    """Совместимость со старым кодом"""
    return enhanced_confirm_request(update, context)

# ==================== ИНИЦИАЛИЗАЦИЯ И ЗАПУСК ====================

# Глобальные объекты
rate_limiter = RateLimiter()
db = None
notification_manager = None

def enhanced_main() -> None:
    """Улучшенный запуск бота"""
    global db, notification_manager
    
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Инициализация расширенных компонентов
        db = EnhancedDatabase(DB_PATH)
        notification_manager = NotificationManager(updater.bot)

        # Обработчик ошибок
        dispatcher.add_error_handler(error_handler)

        # Расширенные задания по расписанию
        job_queue = updater.job_queue
        if job_queue:
            # Ежедневное резервное копирование
            job_queue.run_daily(
                backup_job, 
                time=datetime.time(hour=AUTO_BACKUP_HOUR, minute=AUTO_BACKUP_MINUTE)
            )
            
            # Ежечасная проверка срочных заявок
            job_queue.run_repeating(
                check_urgent_requests, 
                interval=3600,  # 1 час
                first=10
            )
            
            # Обработка очереди уведомлений каждые 30 секунд
            job_queue.run_repeating(
                lambda context: notification_manager.process_queue(),
                interval=30,
                first=5
            )
            
            # Еженедельная очистка старых бэкапов
            job_queue.run_repeating(
                lambda context: EnhancedBackupManager.cleanup_old_backups(),
                interval=604800,  # 7 дней
                first=3600
            )

        # Обработчик создания заявки (сохраняем старый)
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(📝 Создать заявку)$'), enhanced_start_request_creation),
                MessageHandler(Filters.regex('^(📝 Создать заявку)$'), start_request_creation),  # Для совместимости
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

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_enhanced_admin_panel))
        dispatcher.add_handler(CommandHandler('stats', show_statistics))
        dispatcher.add_handler(CommandHandler('backup', create_backup_command))
        dispatcher.add_handler(CommandHandler('mystats', show_user_statistics))
        dispatcher.add_handler(CommandHandler('help', emergency_help))
        dispatcher.add_handler(CommandHandler('info', show_bot_info))
        
        dispatcher.add_handler(conv_handler)
        
        # Обработчик подтверждения заявки (для совместимости)
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(✅ Подтвердить отправку)$'), 
            enhanced_confirm_request
        ))
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(✅ Подтвердить отправку)$'), 
            confirm_request  # Старая функция для совместимости
        ))
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📝 Создать заявку|📋 Мои заявки|📊 Моя статистика|🆘 Срочная помощь|ℹ️ О боте|🔔 Настройки уведомлений)$'), 
            enhanced_handle_main_menu
        ))
        
        # Обработчики админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые|🔄 В работе|⏰ Срочные|🚨 Зависшие|📊 Статистика|📈 Аналитика|👥 Пользователи|⚙️ Настройки|💾 Бэкапы|🔄 Обновить)'), 
            enhanced_handle_admin_menu
        ))
        
        # Обработчики настроек
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📊 Общая статистика|🔔 Уведомления|🔄 Авто-обновление|💾 Управление бэкапами|⚡ Быстрые действия|🔧 Расширенные настройки|🔙 Назад в админ-панель)$'),
            lambda update, context: handle_settings(update, context)
        ))
        
        # Обработчики бэкапов
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(💾 Создать бэкап|📋 Список бэкапов|🧹 Очистить старые|🔐 Зашифровать бэкапы|🔙 Назад)$'),
            lambda update, context: handle_backup_commands(update, context)
        ))
        
        # Обработчики уведомлений
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🔔 Включить уведомления|🔕 Выключить уведомления|📢 Экстренные уведомления|📅 Напоминания|🔙 Назад в меню)$'),
            lambda update, context: update.message.reply_text("Настройки уведомлений в разработке")
        ))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(
            handle_admin_callback, 
            pattern='^(take_|complete_|message_|confirm_take_|cancel_take_|confirm_complete_|cancel_complete_|view_|back_)'
        ))

        # Запускаем бота
        logger.info("🤖 Улучшенный бот запущен с расширенными функциями!")
        logger.info(f"👑 Администраторы: {len(ADMIN_CHAT_IDS)}")
        logger.info(f"💾 Автоматические бэкапы: {AUTO_BACKUP_HOUR}:{AUTO_BACKUP_MINUTE:02d}")
        logger.info(f"📊 Лимит заявок: {MAX_REQUESTS_PER_HOUR}/час")
        logger.info(f"⏰ Таймаут заявок: {REQUEST_TIMEOUT_HOURS} часов")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска улучшенного бота: {e}")

if __name__ == '__main__':
    enhanced_main()
