import logging
import sqlite3
import os
import json
import re
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
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
import threading
import time
from collections import defaultdict

# ==================== УЛУЧШЕННАЯ КОНФИГУРАЦИЯ ====================

class Config:
    """Улучшенная конфигурация приложения"""
    # Безопасное получение токена только из переменных окружения
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не установлен в переменных окружения!")
    
    ADMIN_CHAT_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '5024165375').split(',') if x.strip()]
    DB_PATH = os.getenv('DB_PATH', "requests.db")
    LOG_LEVEL = getattr(logging, os.getenv('LOG_LEVEL', 'INFO'))
    
    # Настройки ограничений
    MAX_REQUESTS_PER_USER = 50
    RATE_LIMIT_REQUESTS = 10  # макс запросов в минуту
    RATE_LIMIT_WINDOW = 60    # окно в секундах
    
    # Настройки уведомлений
    NOTIFICATION_RETRY_COUNT = 3
    NOTIFICATION_RETRY_DELAY = 5
    
    # Авто-закрытие старых заявок (в днях)
    AUTO_CLOSE_DAYS = 30

# ==================== РАСШИРЕННОЕ ЛОГИРОВАНИЕ ====================

class CustomFormatter(logging.Formatter):
    """Кастомный форматтер для логирования"""
    
    COLORS = {
        'DEBUG': '\033[36m',     # CYAN
        'INFO': '\033[32m',      # GREEN  
        'WARNING': '\033[33m',   # YELLOW
        'ERROR': '\033[31m',     # RED
        'CRITICAL': '\033[41m',  # RED BACKGROUND
        'RESET': '\033[0m'       # RESET
    }
    
    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        message = super().format(record)
        return f"{log_color}{message}{self.COLORS['RESET']}"

# Настройка расширенного логирования
def setup_logging():
    """Настраивает расширенное логирование"""
    logger = logging.getLogger()
    logger.setLevel(Config.LOG_LEVEL)
    
    # Файловый обработчик
    file_handler = logging.FileHandler('bot.log', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # Консольный обработчик с цветами
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(CustomFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

setup_logging()
logger = logging.getLogger(__name__)

# ==================== СИСТЕМА ЛИМИТОВ ====================

class RateLimiter:
    """Система ограничения запросов"""
    
    def __init__(self):
        self.user_requests = defaultdict(list)
        self.lock = threading.Lock()
    
    def is_rate_limited(self, user_id: int) -> Tuple[bool, int]:
        """Проверяет лимит запросов для пользователя"""
        with self.lock:
            now = time.time()
            user_requests = self.user_requests[user_id]
            
            # Очищаем старые запросы
            user_requests = [req_time for req_time in user_requests 
                           if now - req_time < Config.RATE_LIMIT_WINDOW]
            self.user_requests[user_id] = user_requests
            
            if len(user_requests) >= Config.RATE_LIMIT_REQUESTS:
                wait_time = int(Config.RATE_LIMIT_WINDOW - (now - user_requests[0]))
                return True, wait_time
            
            user_requests.append(now)
            return False, 0

rate_limiter = RateLimiter()

# ==================== КЭШИРОВАНИЕ ====================

class Cache:
    """Простая система кэширования"""
    
    def __init__(self, ttl=300):  # 5 минут по умолчанию
        self._cache = {}
        self._ttl = ttl
        self._lock = threading.Lock()
    
    def get(self, key):
        """Получает значение из кэша"""
        with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if time.time() - timestamp < self._ttl:
                    return value
                else:
                    del self._cache[key]
            return None
    
    def set(self, key, value):
        """Устанавливает значение в кэш"""
        with self._lock:
            self._cache[key] = (value, time.time())
    
    def clear(self, key=None):
        """Очищает кэш"""
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

cache = Cache()

# ==================== РАСШИРЕННАЯ ВАЛИДАЦИЯ ====================

class EnhancedValidators:
    """Расширенная валидация данных"""
    
    @staticmethod
    def validate_phone(phone: str) -> Tuple[bool, str]:
        """Проверяет и нормализует номер телефона"""
        # Очистка номера
        cleaned_phone = re.sub(r'[^\d+]', '', phone.strip())
        
        # Проверка формата
        pattern = r'^(\+7|7|8)?[489][0-9]{9}$'
        if not re.match(pattern, cleaned_phone.lstrip('+')):
            return False, "Неверный формат номера"
        
        # Нормализация к формату +7
        if cleaned_phone.startswith('8'):
            normalized = '+7' + cleaned_phone[1:]
        elif cleaned_phone.startswith('7'):
            normalized = '+' + cleaned_phone
        elif cleaned_phone.startswith('+7'):
            normalized = cleaned_phone
        else:
            normalized = '+7' + cleaned_phone
        
        return True, normalized
    
    @staticmethod
    def validate_name(name: str) -> Tuple[bool, str]:
        """Проверяет валидность имени"""
        name = name.strip()
        
        if len(name) < 2:
            return False, "Имя слишком короткое (минимум 2 символа)"
        if len(name) > 50:
            return False, "Имя слишком длинное (максимум 50 символов)"
        if not re.match(r'^[a-zA-Zа-яА-ЯёЁ\s\-]+$', name):
            return False, "Имя может содержать только буквы, пробелы и дефисы"
        
        # Капитализация имени
        normalized = ' '.join(word.capitalize() for word in name.split())
        return True, normalized
    
    @staticmethod
    def validate_problem(problem: str) -> Tuple[bool, str]:
        """Проверяет валидность описания проблемы"""
        problem = problem.strip()
        
        if len(problem) < 10:
            return False, "Описание слишком короткое (минимум 10 символов)"
        if len(problem) > 2000:
            return False, "Описание слишком длинное (максимум 2000 символов)"
        
        # Проверка на спам
        spam_words = ['http://', 'https://', '.com', '.ru', 'купить', 'цена']
        if any(spam_word in problem.lower() for spam_word in spam_words):
            return False, "Описание содержит запрещенные слова"
        
        return True, problem
    
    @staticmethod
    def validate_email(email: str) -> Tuple[bool, str]:
        """Проверяет валидность email (опционально)"""
        if not email:
            return True, ""
        
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if re.match(pattern, email.strip()):
            return True, email.strip().lower()
        return False, "Неверный формат email"

# ==================== РАСШИРЕННАЯ БАЗА ДАННЫХ ====================

class EnhancedDatabase:
    """Расширенная база данных с улучшенной функциональностью"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Инициализация расширенной базы данных"""
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
                        plot TEXT,
                        system_type TEXT,
                        problem TEXT,
                        photo TEXT,
                        urgency TEXT,
                        status TEXT DEFAULT 'new',
                        priority INTEGER DEFAULT 1,
                        created_at TEXT,
                        updated_at TEXT,
                        admin_comment TEXT,
                        assigned_admin TEXT,
                        rating INTEGER,
                        user_comment TEXT,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    )
                ''')
                
                # Таблица статистики
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS statistics (
                        date TEXT PRIMARY KEY,
                        requests_count INTEGER DEFAULT 0,
                        completed_count INTEGER DEFAULT 0,
                        avg_response_time REAL DEFAULT 0
                    )
                ''')
                
                # Расширенная таблица пользователей
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        phone TEXT,
                        department TEXT,
                        created_at TEXT,
                        last_activity TEXT,
                        request_count INTEGER DEFAULT 0,
                        is_blocked BOOLEAN DEFAULT FALSE,
                        block_reason TEXT
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
                
                # Индексы для улучшения производительности
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_requests_status 
                    ON requests(status, priority, created_at)
                ''')
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_requests_user 
                    ON requests(user_id, created_at)
                ''')
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_users_activity 
                    ON users(last_activity)
                ''')
                
                conn.commit()
                logger.info("✅ Расширенная база данных успешно инициализирована")
                
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации базы данных: {e}")
            raise
    
    def log_action(self, user_id: int, action: str, details: str = ""):
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
    
    def save_request(self, user_data: Dict) -> int:
        """Сохраняет заявку с расширенной логикой"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Определяем приоритет на основе срочности
                priority_map = {
                    '🔥 СРОЧНО (1-2 часа)': 1,
                    '⚠️ СЕГОДНЯ (до конца дня)': 2,
                    '💤 НЕ СРОЧНО (1-3 дня)': 3
                }
                priority = priority_map.get(user_data.get('urgency', ''), 3)
                
                cursor.execute('''
                    INSERT INTO requests 
                    (user_id, username, name, phone, plot, system_type, problem, 
                     photo, urgency, priority, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    priority,
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
                    INSERT OR REPLACE INTO users 
                    (user_id, username, first_name, last_name, created_at, last_activity, request_count)
                    VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT request_count FROM users WHERE user_id = ?), 0) + 1)
                ''', (
                    user_data.get('user_id'),
                    user_data.get('username'),
                    user_data.get('first_name', ''),
                    user_data.get('last_name', ''),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                    user_data.get('user_id')
                ))
                
                conn.commit()
                
                # Логируем создание заявки
                self.log_action(
                    user_data.get('user_id'), 
                    'REQUEST_CREATED', 
                    f'Request #{request_id}'
                )
                
                logger.info(f"✅ Заявка #{request_id} сохранена для пользователя {user_data.get('user_id')}")
                return request_id
                
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении заявки: {e}")
            raise
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Получает статистику пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total_requests,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_requests,
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new_requests,
                        SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress_requests
                    FROM requests 
                    WHERE user_id = ?
                ''', (user_id,))
                
                result = cursor.fetchone()
                return {
                    'total_requests': result[0] if result else 0,
                    'completed_requests': result[1] if result else 0,
                    'new_requests': result[2] if result else 0,
                    'in_progress_requests': result[3] if result else 0
                }
                
        except Exception as e:
            logger.error(f"Ошибка получения статистики пользователя {user_id}: {e}")
            return {}
    
    def get_system_stats(self) -> Dict:
        """Получает системную статистику"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Общая статистика
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total_requests,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                        SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress
                    FROM requests
                    WHERE created_at >= date('now', '-30 days')
                ''')
                total_stats = cursor.fetchone()
                
                # Статистика по типам проблем
                cursor.execute('''
                    SELECT system_type, COUNT(*) as count
                    FROM requests 
                    WHERE created_at >= date('now', '-30 days')
                    GROUP BY system_type 
                    ORDER BY count DESC
                ''')
                system_stats = {row[0]: row[1] for row in cursor.fetchall()}
                
                # Активные пользователи
                cursor.execute('''
                    SELECT COUNT(DISTINCT user_id) as active_users
                    FROM requests 
                    WHERE created_at >= date('now', '-7 days')
                ''')
                active_users = cursor.fetchone()[0]
                
                return {
                    'total_requests': total_stats[0] if total_stats else 0,
                    'completed_requests': total_stats[1] if total_stats else 0,
                    'new_requests': total_stats[2] if total_stats else 0,
                    'in_progress_requests': total_stats[3] if total_stats else 0,
                    'system_stats': system_stats,
                    'active_users': active_users
                }
                
        except Exception as e:
            logger.error(f"Ошибка получения системной статистики: {e}")
            return {}
    
    def cleanup_old_requests(self):
        """Очищает старые выполненные заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cutoff_date = (datetime.now() - timedelta(days=Config.AUTO_CLOSE_DAYS)).isoformat()
                
                cursor.execute('''
                    DELETE FROM requests 
                    WHERE status = 'completed' AND created_at < ?
                ''', (cutoff_date,))
                
                deleted_count = cursor.rowcount
                conn.commit()
                
                if deleted_count > 0:
                    logger.info(f"🗑️ Удалено {deleted_count} старых заявок")
                
                return deleted_count
                
        except Exception as e:
            logger.error(f"Ошибка очистки старых заявок: {e}")
            return 0

# Инициализация расширенной базы данных
db = EnhancedDatabase(Config.DB_PATH)

# ==================== СИСТЕМА УВЕДОМЛЕНИЙ ====================

class NotificationSystem:
    """Система умных уведомлений"""
    
    @staticmethod
    def send_admin_notification(context: CallbackContext, user_data: Dict, request_id: int) -> bool:
        """Отправляет уведомление администраторам с повторными попытками"""
        notification_text = (
            f"🚨 *НОВАЯ ЗАЯВКА В IT ОТДЕЛ #{request_id}*\n\n"
            f"👤 *Пользователь:* @{user_data.get('username', 'N/A')}\n"
            f"📛 *Имя:* {user_data.get('name')}\n"
            f"📞 *Телефон:* `{user_data.get('phone')}`\n"
            f"📍 *Участок:* {user_data.get('plot')}\n"
            f"💻 *Тип проблемы:* {user_data.get('system_type')}\n"
            f"⏰ *Срочность:* {user_data.get('urgency')}\n"
            f"📸 *Фото:* {'✅ Добавлено' if user_data.get('photo') else '❌ Отсутствует'}\n\n"
            f"📝 *Описание:* {user_data.get('problem')}\n\n"
            f"🕒 *Время создания:* {user_data.get('timestamp')}"
        )
        
        success_count = 0
        for admin_id in Config.ADMIN_CHAT_IDS:
            for attempt in range(Config.NOTIFICATION_RETRY_COUNT):
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
                    success_count += 1
                    logger.info(f"✅ Уведомление отправлено администратору {admin_id}")
                    break
                except Exception as e:
                    logger.warning(f"⚠️ Попытка {attempt + 1} не удалась для admin {admin_id}: {e}")
                    if attempt < Config.NOTIFICATION_RETRY_COUNT - 1:
                        time.sleep(Config.NOTIFICATION_RETRY_DELAY)
                    else:
                        logger.error(f"❌ Не удалось отправить уведомление admin {admin_id}")
        
        return success_count > 0
    
    @staticmethod
    def send_user_notification(context: CallbackContext, user_id: int, message: str, parse_mode=None):
        """Отправляет уведомление пользователю"""
        try:
            context.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=parse_mode or ParseMode.MARKDOWN
            )
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления пользователю {user_id}: {e}")
            return False

# ==================== ОПРЕДЕЛЕНИЕ ЭТАПОВ РАЗГОВОРА ====================

(
    NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, 
    EDIT_CHOICE, EDIT_FIELD, OTHER_PLOT, SELECT_REQUEST, EMAIL
) = range(12)

# ==================== РАСШИРЕННЫЕ КЛАВИАТУРЫ ====================

# 🎯 Главное меню пользователя - улучшенный дизайн
user_main_menu_keyboard = [
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['✏️ Редактировать заявку', '📊 Моя статистика'],
    ['ℹ️ Помощь', '⚙️ Настройки']
]

# 👑 Главное меню администратора
admin_main_menu_keyboard = [
    ['👑 Админ-панель', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['⚙️ Управление', '🔄 Обслуживание']
]

# 💻 Типы IT систем - обновленные категории
create_request_keyboard = [
    ['💻 Компьютеры', '🖨️ Принтеры'],
    ['🌐 Интернет', '📞 Телефония'],
    ['🔐 Программы', '📊 1С и Базы'],
    ['🎥 Оборудование', '⚡ Другое'],
    ['🔙 Назад в меню']
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

# ⏰ Клавиатура срочности - улучшенный дизайн
urgency_keyboard = [
    ['🔥 СРОЧНО (1-2 часа)'],
    ['⚠️ СЕГОДНЯ (до конца дня)'],
    ['💤 НЕ СРОЧНО (1-3 дня)'],
    ['🔙 Назад']
]

# 🏢 Типы участков - обновленные для IT
plot_type_keyboard = [
    ['🏢 Центральный офис', '🏭 Производство'],
    ['📦 Складской комплекс', '🛒 Торговый зал'],
    ['💻 Удаленные рабочие места', '📋 Другой участок'],
    ['🔙 Назад']
]

# ✏️ Клавиатура редактирования
edit_choice_keyboard = [
    ['👤 Имя', '📞 Телефон', '📍 Участок'],
    ['💻 Система', '📝 Описание', '⏰ Срочность'],
    ['📷 Фото', '✅ Готово'],
    ['🔙 Отменить']
]

# ◀️ Клавиатура назад
edit_field_keyboard = [['◀️ Назад к редактированию']]

# 👑 Панель администратора
admin_panel_keyboard = [
    ['🆕 Новые заявки', '🔄 В работе'],
    ['✅ Выполненные', '📊 Статистика'],
    ['👥 Пользователи', '⚙️ Настройки'],
    ['🔙 Главное меню']
]

# ⚙️ Клавиатура управления
admin_management_keyboard = [
    ['🔄 Очистить кэш', '🗑️ Очистить старые заявки'],
    ['📋 Логи действий', '🔧 Перезагрузить'],
    ['🔙 Админ-панель']
]

# ==================== РАСШИРЕННЫЕ ФУНКЦИИ ====================

def rate_limit_check(update: Update, context: CallbackContext) -> bool:
    """Проверяет лимит запросов для пользователя"""
    user_id = update.effective_user.id
    is_limited, wait_time = rate_limiter.is_rate_limited(user_id)
    
    if is_limited:
        update.message.reply_text(
            f"⏳ *Превышен лимит запросов!*\n\n"
            f"Пожалуйста, подождите {wait_time} секунд перед следующим запросом.",
            parse_mode=ParseMode.MARKDOWN
        )
        return True
    return False

def start_enhanced(update: Update, context: CallbackContext) -> None:
    """Улучшенная команда start"""
    user = update.message.from_user
    
    # Логируем начало работы
    db.log_action(user.id, 'BOT_START', f'User {user.username} started bot')
    
    welcome_text = (
        f"👋 *Добро пожаловать, {user.first_name}!*\n\n"
        f"💻 *IT Сервис поддержки готов помочь вам!*\n\n"
        f"🛠️ *Доступные функции:*\n"
        f"• 🎯 Создание заявок в IT отдел\n"
        f"• 📂 Просмотр истории заявок\n"
        f"• ✏️ Редактирование активных заявок\n"
        f"• 📊 Статистика и отслеживание\n"
        f"• ⚡ Быстрая техподдержка\n\n"
        f"🚀 *Начните с создания заявки или выберите действие из меню:*"
    )
    
    update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(
            admin_main_menu_keyboard if user.id in Config.ADMIN_CHAT_IDS 
            else user_main_menu_keyboard, 
            resize_keyboard=True
        ),
        parse_mode=ParseMode.MARKDOWN
    )

def show_user_stats(update: Update, context: CallbackContext) -> None:
    """Показывает статистику пользователя"""
    if rate_limit_check(update, context):
        return
    
    user_id = update.message.from_user.id
    stats = db.get_user_stats(user_id)
    
    if not stats:
        update.message.reply_text(
            "📊 Статистика временно недоступна.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return
    
    stats_text = (
        f"📊 *Ваша статистика заявок:*\n\n"
        f"📈 *Всего заявок:* {stats['total_requests']}\n"
        f"✅ *Выполнено:* {stats['completed_requests']}\n"
        f"🆕 *Новых:* {stats['new_requests']}\n"
        f"🔄 *В работе:* {stats['in_progress_requests']}\n\n"
    )
    
    if stats['total_requests'] > 0:
        completion_rate = (stats['completed_requests'] / stats['total_requests']) * 100
        stats_text += f"📊 *Процент выполнения:* {completion_rate:.1f}%\n"
    
    stats_text += f"\n_Статистика обновляется автоматически_"
    
    keyboard = admin_main_menu_keyboard if user_id in Config.ADMIN_CHAT_IDS else user_main_menu_keyboard
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_enhanced_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает расширенную статистику для администраторов"""
    user_id = update.message.from_user.id
    if user_id not in Config.ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    stats = db.get_system_stats()
    
    stats_text = "📊 *Расширенная статистика системы*\n\n"
    stats_text += f"📈 *За последние 30 дней:*\n"
    stats_text += f"• 📋 Всего заявок: {stats.get('total_requests', 0)}\n"
    stats_text += f"• ✅ Выполнено: {stats.get('completed_requests', 0)}\n"
    stats_text += f"• 🆕 Новых: {stats.get('new_requests', 0)}\n"
    stats_text += f"• 🔄 В работе: {stats.get('in_progress_requests', 0)}\n"
    stats_text += f"• 👥 Активных пользователей: {stats.get('active_users', 0)}\n\n"
    
    stats_text += "💻 *Распределение по типам проблем:*\n"
    for system_type, count in stats.get('system_stats', {}).items():
        stats_text += f"• {system_type}: {count}\n"
    
    # Добавляем информацию о производительности
    cache_info = f"Кэш: {len(cache._cache)} записей"
    stats_text += f"\n⚙️ *Система:* {cache_info}"
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_admin_management(update: Update, context: CallbackContext) -> None:
    """Обрабатывает управление системой для администраторов"""
    user_id = update.message.from_user.id
    if user_id not in Config.ADMIN_CHAT_IDS:
        return
    
    text = update.message.text
    
    if text == '🔄 Очистить кэш':
        cache.clear()
        update.message.reply_text(
            "✅ Кэш успешно очищен!",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True)
        )
        
    elif text == '🗑️ Очистить старые заявки':
        deleted_count = db.cleanup_old_requests()
        update.message.reply_text(
            f"✅ Удалено {deleted_count} старых заявок!",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True)
        )
        
    elif text == '📋 Логи действий':
        # Здесь можно добавить вывод логов
        update.message.reply_text(
            "📋 Функция просмотра логов в разработке...",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True)
        )
        
    elif text == '🔧 Перезагрузить':
        update.message.reply_text(
            "🔄 Перезагрузка функций системы...",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True)
        )
        # Можно добавить перезагрузку определенных компонентов
        
    elif text == '🔙 Админ-панель':
        return show_admin_panel(update, context)

def show_settings(update: Update, context: CallbackContext) -> None:
    """Показывает настройки пользователя"""
    user_id = update.message.from_user.id
    
    settings_text = (
        "⚙️ *Настройки*\n\n"
        "🔔 *Уведомления:* Включены\n"
        "🌐 *Язык:* Русский\n"
        "📱 *Тема:* Авто\n\n"
        "_Дополнительные настройки в разработке..._"
    )
    
    keyboard = admin_main_menu_keyboard if user_id in Config.ADMIN_CHAT_IDS else user_main_menu_keyboard
    update.message.reply_text(
        settings_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== УЛУЧШЕННАЯ ОБРАБОТКА ЗАЯВОК ====================

def start_request_creation_enhanced(update: Update, context: CallbackContext) -> int:
    """Улучшенное начало создания заявки"""
    if rate_limit_check(update, context):
        return ConversationHandler.END
    
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} начал создание заявки")
    
    # Проверяем лимит заявок пользователя
    user_stats = db.get_user_stats(user.id)
    if user_stats.get('total_requests', 0) >= Config.MAX_REQUESTS_PER_USER:
        update.message.reply_text(
            "❌ *Достигнут лимит заявок!*\n\n"
            "Вы создали максимальное количество заявок. "
            "Пожалуйста, дождитесь обработки существующих заявок.",
            reply_markup=ReplyKeyboardMarkup(
                admin_main_menu_keyboard if user.id in Config.ADMIN_CHAT_IDS 
                else user_main_menu_keyboard, 
                resize_keyboard=True
            ),
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END
    
    context.user_data.clear()
    context.user_data.update({
        'user_id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'start_time': datetime.now().isoformat()
    })
    
    update.message.reply_text(
        "🎯 *Создание новой заявки в IT отдел*\n\n"
        "📝 *Шаг 1 из 7*\n"
        "👤 Для начала укажите ваше *имя и фамилию*:\n\n"
        "💡 Пример: Иван Иванов",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def name_enhanced(update: Update, context: CallbackContext) -> int:
    """Улучшенная обработка имени"""
    name_text = update.message.text.strip()
    
    is_valid, message = EnhancedValidators.validate_name(name_text)
    
    if not is_valid:
        update.message.reply_text(
            f"❌ *{message}*\n\n"
            "👤 Пожалуйста, введите ваше имя еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME
    
    context.user_data['name'] = message  # Используем нормализованное имя
    update.message.reply_text(
        "📝 *Шаг 2 из 7*\n"
        "📞 *Укажите ваш контактный телефон:*\n\n"
        "📋 Примеры:\n"
        "• +7 999 123-45-67\n"
        "• 8 999 123-45-67\n"
        "• 79991234567\n\n"
        "💡 Номер будет использован для связи",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

def phone_enhanced(update: Update, context: CallbackContext) -> int:
    """Улучшенная обработка телефона"""
    phone_text = update.message.text.strip()
    
    is_valid, normalized_phone = EnhancedValidators.validate_phone(phone_text)
    
    if not is_valid:
        update.message.reply_text(
            f"❌ *{normalized_phone}*\n\n"  # В этом случае normalized_phone содержит сообщение об ошибке
            "📞 Пожалуйста, введите номер в одном из форматов:\n"
            "• +7 999 123-45-67\n"
            "• 8 999 123-45-67\n"
            "• 79991234567\n\n"
            "Попробуйте еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHONE
    
    context.user_data['phone'] = normalized_phone
    update.message.reply_text(
        "📝 *Шаг 3 из 7*\n"
        "📍 *Выберите ваш участок или отдел:*",
        reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLOT

# ==================== ФУНКЦИИ ОБСЛУЖИВАНИЯ ====================

def setup_maintenance_jobs(job_queue: JobQueue):
    """Настраивает фоновые задачи обслуживания"""
    
    def cleanup_job(context: CallbackContext):
        """Очистка старых данных"""
        try:
            deleted_count = db.cleanup_old_requests()
            if deleted_count > 0:
                logger.info(f"🔄 Автоочистка: удалено {deleted_count} старых заявок")
        except Exception as e:
            logger.error(f"❌ Ошибка в задаче очистки: {e}")
    
    def cache_cleanup_job(context: CallbackContext):
        """Очистка устаревшего кэша"""
        try:
            # Здесь можно добавить логику очистки старых записей кэша
            logger.debug("🔄 Проверка кэша...")
        except Exception as e:
            logger.error(f"❌ Ошибка очистки кэша: {e}")
    
    # Запускаем задачи
    job_queue.run_repeating(cleanup_job, interval=86400, first=10)  # Раз в день
    job_queue.run_repeating(cache_cleanup_job, interval=3600, first=30)  # Раз в час

# ==================== ОБНОВЛЕННЫЕ ОСНОВНЫЕ ФУНКЦИИ ====================

def show_main_menu_enhanced(update: Update, context: CallbackContext) -> None:
    """Улучшенное главное меню"""
    user = update.message.from_user
    user_id = user.id
    
    if user_id in Config.ADMIN_CHAT_IDS:
        keyboard = admin_main_menu_keyboard
        welcome_text = (
            f"👑 *Добро пожаловать, {user.first_name}!*\n\n"
            f"💻 *Панель администратора IT отдела*\n\n"
            f"📊 *Сегодняшняя статистика:*\n"
            f"• 🆕 Новые заявки: {len(db.get_requests_by_filter('new'))}\n"
            f"• 🔄 В работе: {len(db.get_requests_by_filter('in_progress'))}\n"
            f"• ✅ Выполненные: {len(db.get_requests_by_filter('completed'))}\n\n"
            f"🎯 *Выберите действие из меню:*"
        )
    else:
        keyboard = user_main_menu_keyboard
        user_stats = db.get_user_stats(user_id)
        welcome_text = (
            f"👋 *Добро пожаловать, {user.first_name}!*\n\n"
            f"💻 *Ваша статистика:*\n"
            f"• 📋 Всего заявок: {user_stats.get('total_requests', 0)}\n"
            f"• ✅ Выполнено: {user_stats.get('completed_requests', 0)}\n"
            f"• 🔄 Активных: {user_stats.get('in_progress_requests', 0) + user_stats.get('new_requests', 0)}\n\n"
            f"🛠️ *Сервис IT поддержки к вашим услугам!*"
        )
    
    update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_enhanced_main_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает улучшенное главное меню"""
    if rate_limit_check(update, context):
        return
    
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id in Config.ADMIN_CHAT_IDS:
        if text == '👑 Админ-панель':
            return show_admin_panel(update, context)
        elif text == '📊 Статистика':
            return show_enhanced_statistics(update, context)
        elif text == '🎯 Создать заявку':
            return start_request_creation_enhanced(update, context)
        elif text == '📂 Мои заявки':
            return show_my_requests(update, context)
        elif text == '⚙️ Управление':
            update.message.reply_text(
                "⚙️ *Управление системой*",
                reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        elif text == '🔄 Обслуживание':
            return handle_admin_management(update, context)
    else:
        if text == '🎯 Создать заявку':
            return start_request_creation_enhanced(update, context)
        elif text == '📂 Мои заявки':
            return show_my_requests(update, context)
        elif text == '✏️ Редактировать заявку':
            return start_edit_request(update, context)
        elif text == '📊 Моя статистика':
            return show_user_stats(update, context)
        elif text == 'ℹ️ Помощь':
            return show_help(update, context)
        elif text == '⚙️ Настройки':
            return show_settings(update, context)

# ==================== ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА ====================

def main_enhanced() -> None:
    """Улучшенный запуск бота"""
    try:
        updater = Updater(Config.BOT_TOKEN)
        dispatcher = updater.dispatcher
        job_queue = updater.job_queue

        # Настраиваем фоновые задачи
        setup_maintenance_jobs(job_queue)

        # Обработчик создания заявки
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(🎯 Создать заявку)$'), start_request_creation_enhanced),
            ],
            states={
                NAME: [MessageHandler(Filters.text & ~Filters.command, name_enhanced)],
                PHONE: [MessageHandler(Filters.text & ~Filters.command, phone_enhanced)],
                PLOT: [MessageHandler(Filters.text & ~Filters.command, plot)],
                OTHER_PLOT: [MessageHandler(Filters.text & ~Filters.command, other_plot)],
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
                MessageHandler(Filters.regex('^(🔙 Главное меню|🔙 Отменить)$'), cancel_request),
            ],
            allow_reentry=True
        )

        # Обработчик редактирования заявки
        edit_conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(✏️ Редактировать заявку)$'), start_edit_request),
            ],
            states={
                SELECT_REQUEST: [MessageHandler(Filters.text & ~Filters.command, select_request_for_edit)],
                EDIT_CHOICE: [MessageHandler(Filters.text & ~Filters.command, handle_edit_choice)],
                EDIT_FIELD: [
                    MessageHandler(Filters.text & ~Filters.command, handle_edit_field),
                    MessageHandler(Filters.photo, handle_edit_field)
                ],
                OTHER_PLOT: [MessageHandler(Filters.text & ~Filters.command, other_plot_edit)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_edit),
                MessageHandler(Filters.regex('^(🔙 Главное меню)$'), cancel_edit),
            ],
            allow_reentry=True
        )

        # Регистрируем улучшенные обработчики
        dispatcher.add_handler(CommandHandler('start', start_enhanced))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu_enhanced))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        dispatcher.add_handler(CommandHandler('help', show_help))
        dispatcher.add_handler(CommandHandler('stats', show_user_stats))
        dispatcher.add_handler(CommandHandler('statistics', show_enhanced_statistics))
        
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(edit_conv_handler)
        
        # Обработчики для кнопок подтверждения и редактирования
        dispatcher.add_handler(MessageHandler(Filters.regex('^(🚀 Отправить заявку)$'), confirm_request))
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✏️ Исправить)$'), confirm_request))
        
        # Обработчики улучшенного главного меню
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📂 Мои заявки|👑 Админ-панель|📊 Статистика|ℹ️ Помощь|📊 Моя статистика|⚙️ Настройки|⚙️ Управление|🔄 Обслуживание)$'), 
            handle_enhanced_main_menu
        ))
        
        # Обработчики админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки|🔄 В работе|✅ Выполненные|👥 Пользователи|⚙️ Настройки)$'), 
            handle_admin_menu
        ))
        
        # Обработчики управления
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🔄 Очистить кэш|🗑️ Очистить старые заявки|📋 Логи действий|🔧 Перезагрузить|🔙 Админ-панель)$'),
            handle_admin_management
        ))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(handle_admin_callback, pattern='^(take_|complete_|message_)'))

        # Запускаем бота
        logger.info("🚀 Улучшенный бот IT отдела запущен!")
        logger.info(f"👑 Администраторы: {Config.ADMIN_CHAT_IDS}")
        logger.info(f"📊 Лимит запросов: {Config.RATE_LIMIT_REQUESTS} в {Config.RATE_LIMIT_WINDOW} сек")
        logger.info(f"💾 Автоочистка: каждые {Config.AUTO_CLOSE_DAYS} дней")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Критическая ошибка запуска бота: {e}")
        raise

if __name__ == '__main__':
    main_enhanced()
