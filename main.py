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
                        user_comment TEXT
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
                
                # Индексы для улучшения производительности
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_requests_status 
                    ON requests(status, priority, created_at)
                ''')
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_requests_user 
                    ON requests(user_id, created_at)
                ''')
                
                conn.commit()
                logger.info("✅ Расширенная база данных успешно инициализирована")
                
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации базы данных: {e}")
            raise
    
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
                logger.info(f"✅ Заявка #{request_id} сохранена для пользователя {user_data.get('user_id')}")
                return request_id
                
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении заявки: {e}")
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
                return requests
        except Exception as e:
            logger.error(f"Ошибка при получении заявок пользователя {user_id}: {e}")
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
                elif filter_type == 'completed':
                    status_filter = "status = 'completed'"
                else:
                    status_filter = "status IN ('new', 'in_progress')"
                
                cursor.execute(f'''
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
                ''', (limit,))
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
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
                    if field in ['name', 'phone', 'plot', 'system_type', 'problem', 'photo', 'urgency']:
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

# ==================== ОПРЕДЕЛЕНИЕ ЭТАПОВ РАЗГОВОРА ====================

(
    NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, 
    EDIT_CHOICE, EDIT_FIELD, OTHER_PLOT, SELECT_REQUEST
) = range(11)

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

# ==================== ДОБАВЛЕНИЕ НЕДОСТАЮЩИХ ФУНКЦИЙ ====================

def plot(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор участка"""
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "👤 Укажите ваше имя и фамилию:",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME
    
    if update.message.text == '📋 Другой участок':
        update.message.reply_text(
            "📝 *Шаг 3 из 7*\n"
            "✏️ *Введите название вашего участка или отдела:*\n\n"
            "📋 Примеры:\n"
            "• Бухгалтерия\n"
            "• Отдел кадров\n"
            "• Производственный цех №1",
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return OTHER_PLOT
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 4 из 7*\n"
        "💻 *Выберите тип IT-проблемы:*",
        reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

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
        "📝 *Шаг 4 из 7*\n"
        "💻 *Выберите тип IT-проблемы:*",
        reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор типа системы"""
    if update.message.text == '🔙 Назад в меню':
        return show_main_menu_enhanced(update, context)
    elif update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📍 *Выберите ваш участок или отдел:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    # Проверка валидности выбора системы
    valid_systems = ['💻 Компьютеры', '🖨️ Принтеры', '🌐 Интернет', '📞 Телефония', 
                    '🔐 Программы', '📊 1С и Базы', '🎥 Оборудование', '⚡ Другое']
    if update.message.text not in valid_systems:
        update.message.reply_text(
            "❌ Пожалуйста, выберите тип проблемы из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True)
        )
        return SYSTEM_TYPE
    
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 5 из 7*\n"
        "📖 *Опишите проблему подробно:*\n\n"
        "💡 Примеры хороших описаний:\n"
        "• 'Не включается компьютер, при нажатии кнопки питания ничего не происходит'\n"
        "• 'Принтер HP LaserJet печатает пустые листы'\n"
        "• 'Не работает интернет на 3 этаже в бухгалтерии'\n\n"
        "⚠️ *Требования:* от 10 до 1000 символов",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    """Обрабатывает описание проблемы"""
    problem_text = update.message.text.strip()
    
    is_valid, message = EnhancedValidators.validate_problem(problem_text)
    
    if not is_valid:
        update.message.reply_text(
            f"❌ *{message}*\n\n"
            "📝 Пожалуйста, опишите проблему подробнее:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PROBLEM
    
    context.user_data['problem'] = message
    update.message.reply_text(
        "📝 *Шаг 6 из 7*\n"
        "⏰ *Выберите срочность выполнения:*",
        reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор срочности"""
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
        "📝 *Шаг 7 из 7*\n"
        "📸 *Хотите добавить фото к заявке?*\n\n"
        "🖼️ Фото помогает быстрее понять проблему.\n"
        "📎 Можно отправить скриншот ошибки или фото оборудования",
        reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    """Обрабатывает добавление фото"""
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
        f"📋 *Сводка заявки в IT отдел:*\n\n"
        f"👤 *Имя:* {context.user_data['name']}\n"
        f"📞 *Телефон:* `{context.user_data['phone']}`\n"
        f"📍 *Участок:* {context.user_data['plot']}\n"
        f"💻 *Тип проблемы:* {context.user_data['system_type']}\n"
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
            NotificationSystem.send_admin_notification(context, context.user_data, request_id)
            
            confirmation_text = (
                f"🎉 *Заявка #{request_id} успешно создана!*\n\n"
                f"📋 *Детали заявки:*\n"
                f"• 💻 Тип: {context.user_data['system_type']}\n"
                f"• 📍 Участок: {context.user_data['plot']}\n"
                f"• ⏰ Срочность: {context.user_data['urgency']}\n\n"
                f"👨‍💼 *Специалист IT отдела свяжется с вами в ближайшее время.*\n\n"
                f"_Спасибо за обращение в IT отдел!_ 💻"
            )
            
            if user.id in Config.ADMIN_CHAT_IDS:
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
            
            logger.info(f"Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            error_message = (
                "❌ *Произошла ошибка при создании заявки.*\n\n"
                "⚠️ Пожалуйста, попробуйте позже или обратитесь в IT отдел напрямую."
            )
            
            if user.id in Config.ADMIN_CHAT_IDS:
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
    
    return ConversationHandler.END

def cancel_request(update: Update, context: CallbackContext) -> int:
    """Отменяет создание заявки"""
    user_id = update.message.from_user.id
    logger.info(f"Пользователь {user_id} отменил создание заявки")
    
    if user_id in Config.ADMIN_CHAT_IDS:
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
        button_text = f"{status_icon} #{req['id']} - {req['system_type']}"
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
        expected_text = f"{'🆕' if req['status'] == 'new' else '🔄'} #{req['id']} - {req['system_type']}"
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
        f"📍 *Участок:* {request_data['plot']}\n"
        f"💻 *Тип проблемы:* {request_data['system_type']}\n"
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
        
    elif choice == '📍 Участок':
        update.message.reply_text(
            f"✏️ *Выберите новый участок:*\nТекущий: {context.user_data['plot']}",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '💻 Система':
        update.message.reply_text(
            f"✏️ *Выберите новую систему:*\nТекущая: {context.user_data['system_type']}",
            reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True),
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
        
    elif editing_field == '💻 Система':
        if text in ['🔙 Назад', '🔙 Главное меню']:
            if context.user_data.get('editing_existing'):
                return show_edit_summary(update, context)
            else:
                return edit_request_choice(update, context)
        context.user_data['system_type'] = text
        update.message.reply_text(
            "✅ Система обновлена!",
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
            update.message.reply_text(
                f"✅ *Заявка #{request_id} успешно обновлена!*\n\n"
                f"📋 Изменения сохранены в системе.\n"
                f"👨‍💼 Специалист IT отдела увидит обновленные данные.",
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

# ==================== АДМИН-ПАНЕЛЬ ====================

def show_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает админ-панель"""
    user_id = update.message.from_user.id
    
    if user_id not in Config.ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu_enhanced(update, context)
    
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    completed_requests = db.get_requests_by_filter('completed')
    
    admin_text = (
        "👑 *Панель администратора IT отдела*\n\n"
        f"📊 *Статистика заявок:*\n"
        f"🆕 *Новых:* {len(new_requests)}\n"
        f"🔄 *В работе:* {len(in_progress_requests)}\n"
        f"✅ *Выполненных:* {len(completed_requests)}\n"
        f"📈 *Всего активных:* {len(new_requests) + len(in_progress_requests)}\n\n"
        "🎯 *Выберите раздел для работы:*"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_requests_by_filter(update: Update, context: CallbackContext, filter_type: str) -> None:
    """Показывает заявки по фильтру"""
    user_id = update.message.from_user.id
    if user_id not in Config.ADMIN_CHAT_IDS:
        return show_main_menu_enhanced(update, context)
    
    requests = db.get_requests_by_filter(filter_type, 50)
    filter_names = {
        'new': '🆕 Новые заявки',
        'in_progress': '🔄 Заявки в работе',
        'completed': '✅ Выполненные заявки'
    }
    filter_name = f"{filter_names[filter_type]} ({len(requests)})"
    
    if not requests:
        update.message.reply_text(
            f"📭 {filter_name} отсутствуют.",
            reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True)
        )
        return
    
    update.message.reply_text(
        filter_name,
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True)
    )
    
    for req in requests:
        if req['status'] == 'completed':
            request_text = (
                f"✅ *Заявка #{req['id']} - ВЫПОЛНЕНА*\n\n"
                f"👤 *Клиент:* {req['name']}\n"
                f"📞 *Телефон:* `{req['phone']}`\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"💻 *Тип проблемы:* {req['system_type']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem']}\n"
                f"📸 *Фото:* {'✅ Есть' if req['photo'] else '❌ Нет'}\n"
                f"👨‍💼 *Исполнитель:* {req.get('assigned_admin', 'Не назначен')}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}\n"
                f"✅ *Завершена:* {req['updated_at'][:16] if req.get('updated_at') else req['created_at'][:16]}"
            )
        elif req['status'] == 'in_progress':
            request_text = (
                f"🔄 *Заявка #{req['id']} - В РАБОТЕ*\n\n"
                f"👤 *Клиент:* {req['name']}\n"
                f"📞 *Телефон:* `{req['phone']}`\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"💻 *Тип проблемы:* {req['system_type']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem']}\n"
                f"📸 *Фото:* {'✅ Есть' if req['photo'] else '❌ Нет'}\n"
                f"👨‍💼 *Исполнитель:* {req.get('assigned_admin', 'Не назначен')}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}\n"
                f"🔄 *Обновлена:* {req['updated_at'][:16] if req.get('updated_at') else req['created_at'][:16]}"
            )
        else:
            request_text = (
                f"🆕 *Заявка #{req['id']} - НОВАЯ*\n\n"
                f"👤 *Клиент:* {req['name']}\n"
                f"📞 *Телефон:* `{req['phone']}`\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"💻 *Тип проблемы:* {req['system_type']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem']}\n"
                f"📸 *Фото:* {'✅ Есть' if req['photo'] else '❌ Нет'}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}"
            )
        
        if req.get('admin_comment'):
            request_text += f"\n💬 *Комментарий администратора:* {req['admin_comment']}"
        
        # Кнопки без "Позвонить"
        if req['status'] == 'completed':
            keyboard = [[
                InlineKeyboardButton("💬 Написать", callback_data=f"message_{req['id']}")
            ]]
        elif req['status'] == 'in_progress':
            if req.get('assigned_admin') == update.message.from_user.first_name:
                keyboard = [[
                    InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{req['id']}")
                ]]
            else:
                keyboard = [[
                    InlineKeyboardButton("💬 Написать", callback_data=f"message_{req['id']}")
                ]]
        else:
            keyboard = [[
                InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{req['id']}")
            ]]
        
        if req.get('photo'):
            update.message.reply_photo(
                photo=req['photo'],
                caption=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

def handle_admin_callback(update: Update, context: CallbackContext) -> None:
    """Обработчик callback от админ-кнопок"""
    query = update.callback_query
    query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if user_id not in Config.ADMIN_CHAT_IDS:
        return
    
    if data.startswith('take_'):
        request_id = int(data.split('_')[1])
        admin_name = query.from_user.first_name
        
        db.update_request_status(
            request_id, 
            "in_progress", 
            f"Заявка взята в работу администратором {admin_name}",
            admin_name
        )
        
        request = db.get_request(request_id)
        if request and request.get('user_id'):
            try:
                context.bot.send_message(
                    chat_id=request['user_id'],
                    text=f"🔄 *Ваша заявка #{request_id} взята в работу!*\n\n"
                         f"👨‍💼 *Исполнитель:* {admin_name}\n"
                         f"📞 С вами свяжутся в ближайшее время.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {request['user_id']}: {e}")
        
        request_text = (
            f"✅ *Заявка #{request_id} взята вами в работу!*\n\n"
            f"👤 *Клиент:* {request['name']}\n"
            f"📞 *Телефон:* `{request['phone']}`\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"💻 *Тип:* {request['system_type']}\n"
            f"⏰ *Срочность:* {request['urgency']}\n"
            f"📝 *Описание:* {request['problem']}\n\n"
            f"🔄 *Статус:* В работе\n"
            f"👨‍💼 *Исполнитель:* {admin_name}"
        )
        
        keyboard = [[
            InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{request_id}")
        ]]
        
        if query.message.caption:
            query.edit_message_caption(
                caption=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            query.edit_message_text(
                text=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        
    elif data.startswith('complete_'):
        request_id = int(data.split('_')[1])
        admin_name = query.from_user.first_name
        
        db.update_request_status(
            request_id, 
            "completed", 
            f"Заявка выполнена администратором {admin_name}",
            admin_name
        )
        
        request = db.get_request(request_id)
        if request and request.get('user_id'):
            try:
                context.bot.send_message(
                    chat_id=request['user_id'],
                    text=f"✅ *Ваша заявка #{request_id} выполнена!*\n\n"
                         f"👨‍💼 *Исполнитель:* {admin_name}\n"
                         f"💬 *Комментарий:* Заявка выполнена\n\n"
                         f"_Спасибо, что воспользовались услугами IT отдела!_ 💻",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {request['user_id']}: {e}")
        
        request_text = (
            f"✅ *Заявка #{request_id} ВЫПОЛНЕНА!*\n\n"
            f"👤 *Клиент:* {request['name']}\n"
            f"📞 *Телефон:* `{request['phone']}`\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"💻 *Тип проблемы:* {request['system_type']}\n"
            f"⏰ *Срочность:* {request['urgency']}\n"
            f"📝 *Описание:* {request['problem']}\n"
            f"📸 *Фото:* {'✅ Есть' if request['photo'] else '❌ Нет'}\n\n"
            f"✅ *Статус:* Выполнено\n"
            f"👨‍💼 *Исполнитель:* {admin_name}\n"
            f"💬 *Комментарий:* Заявка выполнена\n"
            f"🕒 *Завершена:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        keyboard = [[
            InlineKeyboardButton("💬 Написать", callback_data=f"message_{request_id}")
        ]]
        
        if query.message.caption:
            query.edit_message_caption(
                caption=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            query.edit_message_text(
                text=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        
        query.answer("✅ Заявка выполнена!")
    
    elif data.startswith('message_'):
        request_id = int(data.split('_')[1])
        request = db.get_request(request_id)
        
        if request:
            phone_number = request['phone'].replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
            
            message_button = InlineKeyboardButton(
                "💬 Написать сообщение", 
                url=f"https://t.me/{phone_number}" if phone_number.startswith('+') else f"https://t.me/{phone_number}"
            )
            
            contact_text = (
                f"💬 *Контактная информация по заявке #{request_id}*\n\n"
                f"👤 *Клиент:* {request['name']}\n"
                f"📞 *Телефон:* `{request['phone']}`\n"
                f"📍 *Участок:* {request['plot']}\n"
                f"💻 *Тип проблемы:* {request['system_type']}\n"
                f"⏰ *Срочность:* {request['urgency']}\n\n"
                f"_Нажмите кнопку ниже для написания сообщения в Telegram_"
            )
            
            query.answer("💬 Открывается чат...")
            
            context.bot.send_message(
                chat_id=query.message.chat_id,
                text=contact_text,
                reply_markup=InlineKeyboardMarkup([[message_button]]),
                parse_mode=ParseMode.MARKDOWN
            )

def handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in Config.ADMIN_CHAT_IDS:
        return show_main_menu_enhanced(update, context)
    
    if text == '🆕 Новые заявки':
        return show_requests_by_filter(update, context, 'new')
    elif text == '🔄 В работе':
        return show_requests_by_filter(update, context, 'in_progress')
    elif text == '✅ Выполненные заявки':
        return show_requests_by_filter(update, context, 'completed')
    elif text == '📊 Статистика':
        return show_enhanced_statistics(update, context)
    elif text == '🔙 Главное меню':
        return show_main_menu_enhanced(update, context)

def show_enhanced_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает расширенную статистику для администраторов"""
    user_id = update.message.from_user.id
    if user_id not in Config.ADMIN_CHAT_IDS:
        return show_main_menu_enhanced(update, context)
    
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
        update.message.reply_text(
            "📋 Функция просмотра логов в разработке...",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True)
        )
        
    elif text == '🔧 Перезагрузить':
        update.message.reply_text(
            "🔄 Перезагрузка функций системы...",
            reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True)
        )
        
    elif text == '🔙 Админ-панель':
        return show_admin_panel(update, context)

# ==================== ОСНОВНЫЕ ФУНКЦИИ ====================

def show_my_requests(update: Update, context: CallbackContext) -> None:
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    
    if user_id in Config.ADMIN_CHAT_IDS:
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
                f"💻 *Тип:* {req['system_type']}\n"
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
                f"💻 *Тип проблемы:* {req['system_type']}\n"
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

def show_help(update: Update, context: CallbackContext) -> None:
    """Показывает справку"""
    help_text = (
        "💻 *Помощь по боту IT отдела*\n\n"
        "🎯 *Как создать заявку:*\n"
        "1. Нажмите 'Создать заявку'\n"
        "2. Заполните все шаги формы\n"
        "3. Проверьте данные и отправьте\n\n"
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
        "📞 *Контакты IT отдела:*\n"
        "• Телефон: +7 XXX XXX-XX-XX\n"
        "• Email: it@company.com\n"
        "• Кабинет: 3 этаж, каб. 301"
    )
    
    user_id = update.message.from_user.id
    keyboard = admin_main_menu_keyboard if user_id in Config.ADMIN_CHAT_IDS else user_main_menu_keyboard
    
    update.message.reply_text(
        help_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

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

# ==================== УЛУЧШЕННЫЕ ФУНКЦИИ ====================

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
    
    # Запускаем задачи
    job_queue.run_repeating(cleanup_job, interval=86400, first=10)  # Раз в день

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
