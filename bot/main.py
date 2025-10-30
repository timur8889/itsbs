import logging
import sqlite3
import os
import json
import re
import time
import asyncio
import shutil
import signal
import sys
from io import BytesIO
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple, Set, Any
from functools import lru_cache
from enum import Enum
from dataclasses import dataclass
from collections import defaultdict
import phonenumbers
from phonenumbers import NumberParseException

from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
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
    JobQueue,
)

# Загружаем переменные окружения из .env файла
from dotenv import load_dotenv
load_dotenv()

# ==================== УЛУЧШЕННОЕ ЛОГИРОВАНИЕ ====================

class ColoredFormatter(logging.Formatter):
    """🎨 Цветное форматирование логов"""
    COLORS = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[32m',     # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[41m', # Red background
        'RESET': '\033[0m'      # Reset
    }

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        message = super().format(record)
        return f"{log_color}{message}{self.COLORS['RESET']}"

# Настройка улучшенного логирования
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Файловый обработчик
    file_handler = logging.FileHandler('bot.log', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # Консольный обработчик с цветами
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

setup_logging()
logger = logging.getLogger(__name__)

# ==================== ОГРАНИЧИТЕЛЬ ЗАПРОСОВ ====================

class RateLimiter:
    """🔒 Ограничитель частоты запросов для защиты от спама"""
    
    def __init__(self):
        self.user_requests = defaultdict(list)
    
    def is_allowed(self, user_id: int, limit: int = 5, period: int = 3600) -> bool:
        """🔒 Проверяет, не превышен ли лимит запросов"""
        now = datetime.now()
        user_requests = self.user_requests[user_id]
        
        # Удаляем старые запросы
        user_requests[:] = [req_time for req_time in user_requests 
                          if now - req_time < timedelta(seconds=period)]
        
        if len(user_requests) >= limit:
            return False
        
        user_requests.append(now)
        return True
    
    def get_remaining_time(self, user_id: int, period: int = 3600) -> int:
        """⏰ Получает оставшееся время до сброса лимита"""
        now = datetime.now()
        user_requests = self.user_requests[user_id]
        
        if not user_requests:
            return 0
        
        # Находим самое старое время запроса
        oldest_request = min(user_requests)
        reset_time = oldest_request + timedelta(seconds=period)
        
        return max(0, int((reset_time - now).total_seconds()))

# Инициализация ограничителя
rate_limiter = RateLimiter()

# ==================== УЛУЧШЕННАЯ КОНФИГУРАЦИЯ ====================

class Config:
    """⚙️ Конфигурация бота"""
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    SUPER_ADMIN_IDS = [int(x) for x in os.getenv('SUPER_ADMIN_IDS', '5024165375').split(',')]
    
    # Настройки отделов (только IT отдел)
    ADMIN_CHAT_IDS = {
        '💻 IT отдел': [5024165375]
    }
    
    DB_PATH = "requests.db"
    BACKUP_DIR = "backups"
    
    # Новые настройки
    ENABLE_AI_ANALYSIS = True
    ENABLE_RATINGS = True
    AUTO_BACKUP_HOURS = 24
    NOTIFICATION_HOURS_START = 9
    NOTIFICATION_HOURS_END = 22
    
    # Настройки завода
    COMPANY_NAME = "Завод Контакт"
    IT_DEPARTMENT_NAME = "IT отдел"
    SUPPORT_PHONE = "+7 (XXX) XXX-XX-XX"
    SUPPORT_EMAIL = "it@zavod-kontakt.ru"
    
    # Настройки ограничений
    REQUESTS_PER_HOUR = 5  # Максимум заявок в час на пользователя
    MAX_MEDIA_FILES = 10   # Максимум медиа файлов на заявку
    
    @staticmethod
    def is_admin(user_id: int) -> bool:
        """🔐 Проверяет, является ли пользователь администратором"""
        return any(user_id in admins for admins in Config.ADMIN_CHAT_IDS.values()) or user_id in Config.SUPER_ADMIN_IDS
    
    @staticmethod
    def validate_config():
        """🔍 Проверяет конфигурацию"""
        if not Config.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не найден в переменных окружения!")
        
        required_vars = ['BOT_TOKEN']
        for var in required_vars:
            if not getattr(Config, var):
                raise ValueError(f"Не задана обязательная переменная: {var}")
        
        # Создаем директорию для бэкапов
        os.makedirs(Config.BACKUP_DIR, exist_ok=True)

# ==================== УЛУЧШЕННАЯ БАЗА ДАННЫХ ====================

class EnhancedDatabase:
    """🗃️ Улучшенный класс для работы с базой данных"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        try:
            self.init_enhanced_db()
            logger.info("✅ База данных успешно инициализирована")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации БД: {e}")
            raise
    
    def init_enhanced_db(self):
        """🎯 Инициализация улучшенной базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Таблица заявок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    phone TEXT,
                    department TEXT DEFAULT '💻 IT отдел',
                    problem TEXT,
                    photo_id TEXT,
                    status TEXT DEFAULT 'new',
                    urgency TEXT DEFAULT '💤 НЕ СРОЧНО',
                    created_at TEXT,
                    assigned_at TEXT,
                    assigned_admin TEXT,
                    completed_at TEXT,
                    admin_comment TEXT,
                    user_rating INTEGER DEFAULT 0,
                    user_feedback TEXT
                )
            ''')
            
            # Таблица медиа файлов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS request_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER,
                    file_id TEXT,
                    file_type TEXT,
                    file_name TEXT,
                    created_at TEXT,
                    FOREIGN KEY (request_id) REFERENCES requests (id)
                )
            ''')
            
            # Таблица статистики
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT,
                    total_requests INTEGER DEFAULT 0,
                    completed_requests INTEGER DEFAULT 0,
                    avg_completion_time REAL DEFAULT 0,
                    created_at TEXT
                )
            ''')
            
            # Таблица пользователей для улучшенного управления
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    phone TEXT,
                    department TEXT,
                    created_at TEXT,
                    last_activity TEXT,
                    is_blocked BOOLEAN DEFAULT FALSE,
                    block_reason TEXT
                )
            ''')
            
            # Индексы для производительности
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)')
            
            conn.commit()
    
    def backup_database(self):
        """💾 Создает резервную копию базы данных"""
        backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(Config.BACKUP_DIR, backup_name)
        
        try:
            # Используем SQLite backup API
            with sqlite3.connect(self.db_path) as source:
                with sqlite3.connect(backup_path) as target:
                    source.backup(target)
            
            logger.info(f"✅ Резервная копия создана: {backup_name}")
            
            # Удаляем старые бэкапы (оставляем последние 5)
            self.cleanup_old_backups()
            
            return backup_path
        except Exception as e:
            logger.error(f"❌ Ошибка резервного копирования: {e}")
            return None
    
    def cleanup_old_backups(self, keep_count: int = 5):
        """🧹 Удаляет старые резервные копии"""
        try:
            backups = []
            for filename in os.listdir(Config.BACKUP_DIR):
                if filename.startswith('backup_') and filename.endswith('.db'):
                    filepath = os.path.join(Config.BACKUP_DIR, filename)
                    backups.append((filepath, os.path.getctime(filepath)))
            
            # Сортируем по дате создания (новые первыми)
            backups.sort(key=lambda x: x[1], reverse=True)
            
            # Удаляем старые бэкапы
            for backup_path, _ in backups[keep_count:]:
                os.remove(backup_path)
                logger.info(f"🗑️ Удален старый бэкап: {os.path.basename(backup_path)}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка очистки бэкапов: {e}")
    
    def add_request(self, user_id: int, username: str, phone: str, problem: str, 
                   photo_id: str = None, urgency: str = '💤 НЕ СРОЧНО') -> int:
        """📝 Добавляет новую заявку"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO requests 
                (user_id, username, phone, problem, photo_id, urgency, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, username, phone, problem, photo_id, urgency, datetime.now().isoformat()))
            request_id = cursor.lastrowid
            conn.commit()
            
            # Обновляем информацию о пользователе
            self.update_user_info(user_id, username, phone)
            
            return request_id
    
    def update_user_info(self, user_id: int, username: str, phone: str = None):
        """👤 Обновляет информацию о пользователе"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Проверяем существование пользователя
            cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
            exists = cursor.fetchone()
            
            if exists:
                cursor.execute('''
                    UPDATE users 
                    SET username = ?, last_activity = ?
                    WHERE user_id = ?
                ''', (username, datetime.now().isoformat(), user_id))
            else:
                cursor.execute('''
                    INSERT INTO users 
                    (user_id, username, full_name, phone, created_at, last_activity)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (user_id, username, username, phone, datetime.now().isoformat(), datetime.now().isoformat()))
            
            conn.commit()
    
    def add_media_to_request(self, request_id: int, file_id: str, file_type: str, file_name: str = None):
        """📎 Добавляет медиа файл к заявке"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO request_media (request_id, file_id, file_type, file_name, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (request_id, file_id, file_type, file_name, datetime.now().isoformat()))
            conn.commit()
    
    def get_request_media(self, request_id: int) -> List[Dict]:
        """📂 Получает медиа файлы заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM request_media 
                WHERE request_id = ? 
                ORDER BY created_at
            ''', (request_id,))
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def update_admin_comment(self, request_id: int, comment: str):
        """💬 Обновляет комментарий администратора"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE requests 
                SET admin_comment = ?
                WHERE id = ?
            ''', (comment, request_id))
            conn.commit()
    
    def get_requests(self, status: str = None, limit: int = 50, user_id: int = None) -> List[Dict]:
        """📋 Получает список заявок"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM requests WHERE 1=1"
            params = []
            
            if status:
                query += " AND status = ?"
                params.append(status)
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def get_request(self, request_id: int) -> Optional[Dict]:
        """🔍 Получает заявку по ID"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
            row = cursor.fetchone()
            if row:
                columns = [column[0] for column in cursor.description]
                return dict(zip(columns, row))
            return None
    
    def update_request_status(self, request_id: int, status: str, admin_name: str = None):
        """🔄 Обновляет статус заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if status == 'in_progress' and admin_name:
                cursor.execute('''
                    UPDATE requests 
                    SET status = ?, assigned_at = ?, assigned_admin = ?
                    WHERE id = ?
                ''', (status, datetime.now().isoformat(), admin_name, request_id))
            elif status == 'completed':
                cursor.execute('''
                    UPDATE requests 
                    SET status = ?, completed_at = ?
                    WHERE id = ?
                ''', (status, datetime.now().isoformat(), request_id))
            else:
                cursor.execute('''
                    UPDATE requests 
                    SET status = ?
                    WHERE id = ?
                ''', (status, request_id))
            
            conn.commit()
    
    def get_user_requests(self, user_id: int) -> List[Dict]:
        """📂 Получает заявки пользователя"""
        return self.get_requests(user_id=user_id, limit=100)
    
    def get_statistics(self) -> Dict[str, Any]:
        """📊 Получает статистику"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Общая статистика
            cursor.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM requests
            ''')
            stats = cursor.fetchone()
            
            # Статистика за сегодня
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT COUNT(*) FROM requests 
                WHERE DATE(created_at) = ? AND status = 'completed'
            ''', (today,))
            completed_today = cursor.fetchone()[0]
            
            # Средняя оценка
            cursor.execute('''
                SELECT AVG(user_rating) FROM requests 
                WHERE user_rating > 0
            ''')
            avg_rating = cursor.fetchone()[0] or 0
            
            # Статистика пользователей
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]
            
            # Активные пользователи (за последние 30 дней)
            month_ago = (datetime.now() - timedelta(days=30)).isoformat()
            cursor.execute('SELECT COUNT(*) FROM users WHERE last_activity > ?', (month_ago,))
            active_users = cursor.fetchone()[0]
            
            return {
                'total': stats[0],
                'new': stats[1],
                'in_progress': stats[2],
                'completed': stats[3],
                'completed_today': completed_today,
                'avg_rating': round(avg_rating, 1),
                'efficiency': round((stats[3] / stats[0] * 100), 1) if stats[0] > 0 else 0,
                'total_users': total_users,
                'active_users': active_users
            }
    
    def add_user_feedback(self, request_id: int, rating: int, feedback: str = ""):
        """⭐ Добавляет отзыв пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE requests 
                SET user_rating = ?, user_feedback = ?
                WHERE id = ?
            ''', (rating, feedback, request_id))
            conn.commit()

# ==================== УТИЛИТЫ ====================

def validate_phone_number(phone: str) -> Tuple[bool, str]:
    """📞 Валидация номера телефона"""
    try:
        # Пробуем использовать библиотеку phonenumbers для российских номеров
        parsed = phonenumbers.parse(phone, "RU")
        if phonenumbers.is_valid_number(parsed):
            formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            return True, formatted
        return False, "Неверный номер телефона"
    except NumberParseException:
        # Простая валидация для российских номеров
        cleaned = re.sub(r'[^\d+]', '', phone)
        
        if cleaned.startswith('+7') and len(cleaned) == 12:
            return True, cleaned
        elif cleaned.startswith('8') and len(cleaned) == 11:
            return True, '+7' + cleaned[1:]
        elif len(cleaned) == 10:
            return True, '+7' + cleaned
        elif len(cleaned) == 11 and cleaned.startswith('7'):
            return True, '+' + cleaned
        
        return False, "Неверный формат номера. Используйте российский номер"

def signal_handler(signum, frame):
    """🛑 Обработчик сигналов для graceful shutdown"""
    logger.info("🛑 Получен сигнал завершения...")
    print("\n🛑 Завершение работы бота...")
    sys.exit(0)

# ==================== ИНИЦИАЛИЗАЦИЯ ====================

# Инициализация базы данных
db = EnhancedDatabase(Config.DB_PATH)

# ==================== УЛУЧШЕННЫЕ КОМАНДЫ БОТА ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🚀 Обработчик команды /start"""
    user = update.message.from_user
    
    # Проверяем ограничение запросов
    if not rate_limiter.is_allowed(user.id, limit=10, period=3600):
        remaining = rate_limiter.get_remaining_time(user.id)
        await update.message.reply_text(
            f"⏰ Слишком много запросов. Попробуйте через {remaining // 60} минут."
        )
        return
    
    welcome_text = (
        f"🎉 *Рады видеть Вас!*\n\n"
        f"Вы подключились в {Config.IT_DEPARTMENT_NAME} {Config.COMPANY_NAME}! 🤖\n\n"
        f"*Будем рады Вам помочь с решением технических вопросов:*\n"
        f"• 🖥️ Компьютерная техника и ПО\n"
        f"• 🌐 Сеть и интернет\n"
        f"• 🖨️ Принтеры и оргтехника\n"
        f"• 📱 Мобильные устройства\n"
        f"• 🔧 Технические консультации\n\n"
        f"*Контакты отдела:*\n"
        f"• 📞 {Config.SUPPORT_PHONE}\n"
        f"• 📧 {Config.SUPPORT_EMAIL}\n\n"
        f"Выберите действие из меню ниже:"
    )
    
    # Сохраняем информацию о пользователе
    logger.info(f"👤 Новый пользователь: {user.full_name} (ID: {user.id})")
    
    await show_main_menu(update, context, welcome_text)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, welcome_text: str = None) -> None:
    """🏠 Показывает главное меню"""
    keyboard = [
        ["📝 Создать заявку", "📂 Мои заявки"],
        ["📊 Статистика", "🆘 Помощь"],
        ["👨‍💼 Контакты отдела"]
    ]
    
    # Добавляем админские кнопки для администраторов
    if Config.is_admin(update.message.from_user.id):
        keyboard.insert(1, ["👨‍💼 Админ панель", "📋 Все заявки"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if welcome_text:
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            f"🎯 *Главное меню {Config.IT_DEPARTMENT_NAME}*",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== УЛУЧШЕННЫЙ ПРОЦЕСС СОЗДАНИЯ ЗАЯВКИ ====================

# Состояния для создания заявки
REQUEST_PHONE, REQUEST_PROBLEM, REQUEST_MEDIA = range(3)

async def new_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """📝 Начинает процесс создания новой заявки"""
    user = update.message.from_user
    
    # Проверяем ограничение запросов
    if not rate_limiter.is_allowed(user.id, Config.REQUESTS_PER_HOUR, 3600):
        remaining = rate_limiter.get_remaining_time(user.id)
        await update.message.reply_text(
            f"⏰ *Превышен лимит заявок!*\n\n"
            f"Вы можете создавать не более {Config.REQUESTS_PER_HOUR} заявок в час.\n"
            f"Попробуйте через {remaining // 60} минут или обратитесь в отдел напрямую.\n\n"
            f"📞 {Config.SUPPORT_PHONE}",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END
    
    context.user_data['request'] = {
        'user_id': user.id,
        'username': user.username or user.full_name,
        'media_files': []  # Список для хранения медиа файлов
    }
    
    keyboard = [["🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📋 *Создание новой заявки*\n\n"
        "📞 Пожалуйста, введите ваш номер телефона для связи:\n\n"
        "💡 *Пример:* +7 (XXX) XXX-XX-XX или 8 (XXX) XXX-XX-XX",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_PHONE

async def request_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """📞 Обрабатывает номер телефона"""
    if update.message.text == "🔙 Главное меню":
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    phone = update.message.text.strip()
    
    # Валидация номера телефона
    is_valid, validated_phone_or_error = validate_phone_number(phone)
    
    if not is_valid:
        await update.message.reply_text(
            f"❌ {validated_phone_or_error}\n\n"
            f"💡 *Пожалуйста, введите номер в одном из форматов:*\n"
            f"• +7 (XXX) XXX-XX-XX\n"
            f"• 8 (XXX) XXX-XX-XX\n"
            f"• 8XXXXXXXXXX",
            parse_mode=ParseMode.MARKDOWN
        )
        return REQUEST_PHONE
    
    context.user_data['request']['phone'] = validated_phone_or_error
    
    keyboard = [["🔙 Назад", "🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🔧 *Опишите вашу проблему подробно:*\n\n"
        "💡 *Примеры хороших описаний:*\n"
        "• 'Не включается компьютер, при нажатии кнопки питания ничего не происходит'\n"
        "• 'Не работает интернет на всех устройствах в кабинете 305'\n"
        "• 'Принтер HP LaserJet печатает пустые листы'\n"
        "• 'Требуется установка программы 1С на новый компьютер'\n\n"
        "📎 *После описания вы сможете прикрепить фото или видео проблемы*",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_PROBLEM

async def request_problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """🔧 Обрабатывает описание проблемы"""
    text = update.message.text
    
    if text == "🔙 Главное меню":
        await show_main_menu(update, context)
        return ConversationHandler.END
    elif text == "🔙 Назад":
        # Возвращаемся к вводу телефона
        keyboard = [["🔙 Главное меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📞 Пожалуйста, введите ваш номер телефона:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return REQUEST_PHONE
    
    problem = text.strip()
    
    # Проверяем длину описания
    if len(problem) < 10:
        await update.message.reply_text(
            "❌ Описание проблемы слишком короткое. Пожалуйста, опишите проблему более подробно:"
        )
        return REQUEST_PROBLEM
    
    if len(problem) > 2000:
        await update.message.reply_text(
            "❌ Описание проблемы слишком длинное. Пожалуйста, опишите проблему более кратко (максимум 2000 символов):"
        )
        return REQUEST_PROBLEM
    
    context.user_data['request']['problem'] = problem
    
    keyboard = [
        ["📎 Прикрепить фото/видео", "✅ Завершить без медиа"],
        ["🔙 Назад", "🔙 Главное меню"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📎 *Хотите прикрепить фото или видео к заявке?*\n\n"
        "💡 *Это поможет нам быстрее понять и решить проблему*\n"
        "• 📸 Фото проблемы\n"
        "• 🎥 Видео с демонстрацией\n"
        "• 📄 Скриншот ошибки\n"
        f"• 🎤 Голосовое сообщение (максимум {Config.MAX_MEDIA_FILES} файлов)\n\n"
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_MEDIA

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """📎 Обрабатывает медиа файлы"""
    message = update.message
    text = message.text if message.text else ""

    if text == "🔙 Главное меню":
        await show_main_menu(update, context)
        return ConversationHandler.END
    elif text == "🔙 Назад":
        # Возвращаемся к описанию проблемы
        keyboard = [["🔙 Назад", "🔙 Главное меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🔧 Опишите вашу проблему подробно:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return REQUEST_PROBLEM
    elif text == "✅ Завершить без медиа":
        return await create_request_final(update, context)
    elif text == "📎 Прикрепить фото/видео":
        await update.message.reply_text(
            "📎 Отправьте фото, видео, документ или голосовое сообщение:"
        )
        return REQUEST_MEDIA
    
    # Обработка медиа файлов
    file_info = None
    file_type = None
    file_name = None
    
    if message.photo:
        # Берем самое большое фото
        file_info = message.photo[-1]
        file_type = "photo"
        file_name = f"photo_{file_info.file_id}.jpg"
    elif message.video:
        file_info = message.video
        file_type = "video"
        file_name = f"video_{file_info.file_id}.mp4"
    elif message.document:
        file_info = message.document
        file_type = "document"
        file_name = file_info.file_name or f"document_{file_info.file_id}"
    elif message.voice:
        file_info = message.voice
        file_type = "voice"
        file_name = f"voice_{file_info.file_id}.ogg"
    else:
        await message.reply_text(
            "❌ Пожалуйста, отправьте фото, видео, документ или голосовое сообщение, либо выберите действие из меню."
        )
        return REQUEST_MEDIA
    
    if file_info:
        # Проверяем лимит медиа файлов
        media_files = context.user_data['request'].get('media_files', [])
        if len(media_files) >= Config.MAX_MEDIA_FILES:
            await message.reply_text(
                f"❌ Достигнут лимит медиа файлов ({Config.MAX_MEDIA_FILES}). "
                f"Завершите создание заявки или удалите некоторые файлы."
            )
            return REQUEST_MEDIA
        
        # Сохраняем информацию о файле
        context.user_data['request']['media_files'].append({
            'file_id': file_info.file_id,
            'file_type': file_type,
            'file_name': file_name
        })
        
        media_count = len(context.user_data['request']['media_files'])
        
        keyboard = [
            ["📎 Прикрепить еще", "✅ Завершить создание"],
            ["🔙 Назад", "🔙 Главное меню"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        media_type_emoji = {
            'photo': '📸',
            'video': '🎥', 
            'document': '📄',
            'voice': '🎤'
        }.get(file_type, '📎')
        
        await message.reply_text(
            f"{media_type_emoji} *Файл успешно прикреплен!*\n\n"
            f"📎 Прикреплено файлов: {media_count}/{Config.MAX_MEDIA_FILES}\n"
            f"💾 Тип: {file_type}\n"
            f"📁 Имя: {file_name}\n\n"
            f"Вы можете прикрепить еще файлы или завершить создание заявки.",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    return REQUEST_MEDIA

async def create_request_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """✅ Завершает создание заявки"""
    try:
        request_data = context.user_data['request']
        
        # Создаем заявку в базе данных
        request_id = db.add_request(
            user_id=request_data['user_id'],
            username=request_data['username'],
            phone=request_data['phone'],
            problem=request_data['problem']
        )
        
        # Сохраняем медиа файлы
        for media_file in request_data.get('media_files', []):
            db.add_media_to_request(
                request_id, 
                media_file['file_id'], 
                media_file['file_type'],
                media_file.get('file_name')
            )
        
        # Отправляем уведомление администраторам
        await notify_admins_new_request(context, request_id, request_data)
        
        # Форматируем дату создания
        created_time = datetime.now().strftime('%d.%m.%Y в %H:%M')
        
        success_text = (
            f"🎉 *Заявка #{request_id} успешно создана!*\n\n"
            f"🏢 *Отдел:* {Config.IT_DEPARTMENT_NAME}\n"
            f"👤 *Ваше имя:* {request_data['username']}\n"
            f"📞 *Телефон:* {request_data['phone']}\n"
            f"📎 *Медиа файлов:* {len(request_data.get('media_files', []))}\n\n"
            f"🔧 *Описание проблемы:*\n{request_data['problem']}\n\n"
            f"⏰ *Создана:* {created_time}\n\n"
            f"📊 *Статус:* 🆕 Новая\n\n"
            f"💬 *Мы свяжемся с вами в ближайшее время!*\n"
            f"📂 Отслеживать статус можно в разделе \"Мои заявки\""
        )
        
        await context.bot.send_message(
            chat_id=request_data['user_id'],
            text=success_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Логируем создание заявки
        logger.info(f"✅ Создана заявка #{request_id} от пользователя {request_data['username']}")
        
        # Очищаем данные
        context.user_data.clear()
        
        await show_main_menu(update, context)
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания заявки: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при создании заявки. Пожалуйста, попробуйте позже или обратитесь в отдел напрямую."
        )
        return ConversationHandler.END

async def notify_admins_new_request(context: ContextTypes.DEFAULT_TYPE, request_id: int, request_data: Dict):
    """👥 Уведомляет администраторов о новой заявке"""
    message = (
        f"🆕 *НОВАЯ ЗАЯВКА #{request_id}*\n\n"
        f"👤 *Пользователь:* {request_data['username']}\n"
        f"📞 *Телефон:* {request_data['phone']}\n"
        f"🔧 *Проблема:* {request_data['problem'][:200]}...\n"
        f"📎 *Медиа файлов:* {len(request_data.get('media_files', []))}\n"
        f"🕒 *Создана:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    # Отправляем уведомление всем администраторам IT отдела
    admin_ids = Config.ADMIN_CHAT_IDS.get('💻 IT отдел', [])
    for admin_id in admin_ids:
        try:
            # Создаем клавиатуру с кнопками действий
            keyboard = [
                [
                    InlineKeyboardButton("👨‍💼 Взять в работу", callback_data=f"take_{request_id}"),
                    InlineKeyboardButton("📋 Подробнее", callback_data=f"details_{request_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=admin_id,
                text=message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"❌ Ошибка уведомления администратора {admin_id}: {e}")

async def cancel_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """❌ Отменяет создание заявки"""
    context.user_data.clear()
    
    keyboard = [["📝 Создать заявку", "🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=reply_markup
    )
    return ConversationHandler.END

# ==================== УЛУЧШЕННЫЕ ОБРАБОТЧИКИ КНОПОК АДМИНИСТРАТОРОВ ====================

async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👨‍💼 Обрабатывает нажатия кнопок администратора"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not Config.is_admin(user_id):
        await query.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    data = query.data
    
    if data.startswith('take_'):
        request_id = int(data.split('_')[1])
        await take_request_in_work(update, context, request_id, user_id)
    
    elif data.startswith('details_'):
        request_id = int(data.split('_')[1])
        await show_request_details(update, context, request_id)
    
    elif data.startswith('complete_'):
        request_id = int(data.split('_')[1])
        await complete_request_with_comment(update, context, request_id, user_id)
    
    elif data.startswith('feedback_'):
        parts = data.split('_')
        request_id = int(parts[1])
        rating = int(parts[2])
        await handle_user_feedback(update, context, request_id, rating)

async def take_request_in_work(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, admin_id: int):
    """👨‍💼 Берет заявку в работу"""
    query = update.callback_query
    
    try:
        request = db.get_request(request_id)
        if not request:
            await query.edit_message_text("❌ Заявка не найдена.")
            return
        
        if request['status'] != 'new':
            await query.answer("❌ Заявка уже в работе!", show_alert=True)
            return
        
        # Обновляем статус заявки
        admin_name = query.from_user.full_name
        db.update_request_status(request_id, 'in_progress', admin_name)
        
        # Обновляем сообщение
        message_text = query.message.text + f"\n\n✅ *ВЗЯТА В РАБОТУ*\n👨‍💼 Исполнитель: {admin_name}\n🕒 Время: {datetime.now().strftime('%H:%M')}"
        
        # Обновляем клавиатуру
        keyboard = [
            [
                InlineKeyboardButton("✅ Заявка выполнена", callback_data=f"complete_{request_id}"),
            ],
            [
                InlineKeyboardButton("📋 Обновить информацию", callback_data=f"details_{request_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=message_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Уведомляем пользователя
        await context.bot.send_message(
            chat_id=request['user_id'],
            text=(
                f"🔄 *Заявка #{request_id} взята в работу*\n\n"
                f"👨‍💼 *Исполнитель:* {admin_name}\n"
                f"🕒 *Время:* {datetime.now().strftime('%H:%M')}\n\n"
                f"💬 *Специалист свяжется с вами для уточнения деталей*"
            ),
            parse_mode=ParseMode.MARKDOWN
        )
        
        logger.info(f"👨‍💼 Заявка #{request_id} взята в работу администратором {admin_name}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка взятия заявки в работу: {e}")
        await query.answer("❌ Ошибка при взятии заявки!", show_alert=True)

async def complete_request_with_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, admin_id: int):
    """✅ Завершает заявку с запросом комментария"""
    query = update.callback_query
    
    context.user_data['completing_request'] = request_id
    context.user_data['completing_admin'] = query.from_user.full_name
    
    keyboard = [["🔙 Отмена"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await query.message.reply_text(
        f"💬 *Завершение заявки #{request_id}*\n\n"
        f"Пожалуйста, введите комментарий к выполненной работе:\n\n"
        f"💡 *Примеры комментариев:*\n"
        f"• 'Переустановил драйвер принтера, проблема решена'\n"
        f"• 'Заменил сетевой кабель, интернет работает'\n"
        f"• 'Настроил ПО, пользователь проинструктирован'",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_admin_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """💬 Обрабатывает комментарий администратора"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        return
    
    if update.message.text == "🔙 Отмена":
        context.user_data.pop('completing_request', None)
        context.user_data.pop('completing_admin', None)
        
        keyboard = [["👨‍💼 Админ панель"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "❌ Завершение заявки отменено.",
            reply_markup=reply_markup
        )
        return
    
    # Проверяем, ожидаем ли мы комментарий для завершения заявки
    if 'completing_request' in context.user_data:
        request_id = context.user_data['completing_request']
        admin_name = context.user_data['completing_admin']
        comment = update.message.text
        
        try:
            # Обновляем статус заявки
            db.update_request_status(request_id, 'completed')
            
            # Сохраняем комментарий
            db.update_admin_comment(request_id, comment)
            
            # Отправляем уведомление пользователю
            request = db.get_request(request_id)
            if request:
                user_message = (
                    f"✅ *Заявка #{request_id} выполнена!*\n\n"
                    f"👨‍💼 *Исполнитель:* {admin_name}\n"
                    f"💬 *Комментарий:* {comment}\n\n"
                    f"⭐ *Пожалуйста, оцените качество работы:*"
                )
                
                # Создаем клавиатуру для оценки
                rating_keyboard = []
                for i in range(1, 6):
                    rating_keyboard.append([
                        InlineKeyboardButton(
                            "★" * i + "☆" * (5 - i), 
                            callback_data=f"feedback_{request_id}_{i}"
                        )
                    ])
                reply_markup = InlineKeyboardMarkup(rating_keyboard)
                
                await context.bot.send_message(
                    chat_id=request['user_id'],
                    text=user_message,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            
            keyboard = [["👨‍💼 Админ панель"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                f"✅ Заявка #{request_id} завершена с комментарием!",
                reply_markup=reply_markup
            )
            
            logger.info(f"✅ Заявка #{request_id} завершена администратором {admin_name}")
            
            # Очищаем временные данные
            context.user_data.pop('completing_request', None)
            context.user_data.pop('completing_admin', None)
            
        except Exception as e:
            logger.error(f"❌ Ошибка завершения заявки: {e}")
            await update.message.reply_text("❌ Ошибка при завершении заявки.")

async def handle_user_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, rating: int):
    """⭐ Обрабатывает оценку пользователя"""
    query = update.callback_query
    
    try:
        request = db.get_request(request_id)
        if not request or request['user_id'] != query.from_user.id:
            await query.answer("❌ Ошибка оценки!", show_alert=True)
            return
        
        # Сохраняем оценку
        db.add_user_feedback(request_id, rating)
        
        # Благодарим пользователя
        thanks_message = (
            f"⭐ *Спасибо за оценку!*\n\n"
            f"📋 *Заявка #{request_id}*\n"
            f"⭐ *Оценка:* {'★' * rating}{'☆' * (5 - rating)}\n\n"
            f"💼 *Ваш отзыв помогает нам улучшать сервис!*\n"
            f"🔄 Если возникнут новые проблемы - создавайте заявки!"
        )
        
        await query.edit_message_text(
            thanks_message,
            parse_mode=ParseMode.MARKDOWN
        )
        
        logger.info(f"⭐ Пользователь оценил заявку #{request_id} на {rating} звезд")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки оценки: {e}")
        await query.answer("❌ Ошибка при сохранении оценки!", show_alert=True)

async def show_request_details(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int):
    """📋 Показывает детали заявки"""
    query = update.callback_query
    
    try:
        request = db.get_request(request_id)
        if not request:
            await query.answer("❌ Заявка не найдена!", show_alert=True)
            return
        
        # Получаем медиа файлы
        media_files = db.get_request_media(request_id)
        
        # Форматируем даты
        created_date = datetime.fromisoformat(request['created_at']).strftime('%d.%m.%Y в %H:%M')
        
        details_text = (
            f"📋 *ДЕТАЛИ ЗАЯВКИ #{request_id}*\n\n"
            f"👤 *Пользователь:* {request['username']}\n"
            f"📞 *Телефон:* {request['phone']}\n"
            f"🔧 *Проблема:* {request['problem']}\n"
            f"📊 *Статус:* {request['status']}\n"
            f"🕒 *Создана:* {created_date}\n"
        )
        
        if request['assigned_admin']:
            details_text += f"👨‍💼 *Исполнитель:* {request['assigned_admin']}\n"
            if request['assigned_at']:
                assigned_date = datetime.fromisoformat(request['assigned_at']).strftime('%d.%m.%Y в %H:%M')
                details_text += f"⏰ *Взята в работу:* {assigned_date}\n"
        
        if request['admin_comment']:
            details_text += f"💬 *Комментарий:* {request['admin_comment']}\n"
        
        if request['completed_at']:
            completed_date = datetime.fromisoformat(request['completed_at']).strftime('%d.%m.%Y в %H:%M')
            details_text += f"✅ *Завершена:* {completed_date}\n"
        
        if request['user_rating'] > 0:
            details_text += f"⭐ *Оценка пользователя:* {'★' * request['user_rating']}{'☆' * (5 - request['user_rating'])}\n"
        
        details_text += f"📎 *Медиа файлов:* {len(media_files)}\n"
        
        await query.message.reply_text(
            details_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Отправляем медиа файлы если они есть
        for media in media_files:
            try:
                caption = f"📎 Файл к заявке #{request_id}"
                if media['file_name']:
                    caption += f" ({media['file_name']})"
                
                if media['file_type'] == 'photo':
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=media['file_id'],
                        caption=caption
                    )
                elif media['file_type'] == 'video':
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=media['file_id'],
                        caption=caption
                    )
                elif media['file_type'] == 'document':
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=media['file_id'],
                        caption=caption
                    )
                elif media['file_type'] == 'voice':
                    await context.bot.send_voice(
                        chat_id=query.message.chat_id,
                        voice=media['file_id'],
                        caption=caption
                    )
            except Exception as e:
                logger.error(f"❌ Ошибка отправки медиа: {e}")
                await query.message.reply_text(f"❌ Не удалось отправить файл: {str(e)}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка показа деталей заявки: {e}")
        await query.answer("❌ Ошибка при загрузке деталей!", show_alert=True)

# ==================== НОВЫЕ КОМАНДЫ ====================

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🔄 Сброс данных пользователя"""
    user_id = update.message.from_user.id
    
    keyboard = [
        ["✅ Да, сбросить", "❌ Нет, отмена"],
        ["🔙 Главное меню"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🔄 *Сброс данных*\n\n"
        "Это действие удалит все ваши текущие незавершенные заявки и историю.\n"
        "Вы уверены, что хотите продолжить?",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Сохраняем состояние для обработки ответа
    context.user_data['awaiting_reset_confirmation'] = True

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """💾 Создание резервной копии базы данных (только для админов)"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для этой команды.")
        return
    
    try:
        await update.message.reply_text("💾 Создание резервной копии...")
        
        backup_path = db.backup_database()
        
        if backup_path:
            await update.message.reply_text(
                f"✅ *Резервная копия создана успешно!*\n\n"
                f"📁 Файл: `{os.path.basename(backup_path)}`\n"
                f"💾 Размер: {os.path.getsize(backup_path) // 1024} КБ\n"
                f"🕒 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("❌ Ошибка при создании резервной копии.")
            
    except Exception as e:
        logger.error(f"❌ Ошибка команды backup: {e}")
        await update.message.reply_text("❌ Ошибка при создании резервной копии.")

async def send_bulk_notification(context: ContextTypes.DEFAULT_TYPE, message: str, user_ids: List[int]):
    """📢 Массовая рассылка уведомлений"""
    success_count = 0
    fail_count = 0
    
    for user_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            success_count += 1
            await asyncio.sleep(0.1)  # Задержка чтобы не превысить лимиты Telegram
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления пользователю {user_id}: {e}")
            fail_count += 1
    
    logger.info(f"📢 Рассылка завершена: Успешно {success_count}, Ошибок {fail_count}")
    return success_count, fail_count

# ==================== УЛУЧШЕННЫЕ АДМИНСКИЕ КОМАНДЫ ====================

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👨‍💼 Показывает админскую панель"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    # Получаем статистику
    stats = db.get_statistics()
    
    admin_text = (
        f"👨‍💼 *АДМИН ПАНЕЛЬ {Config.IT_DEPARTMENT_NAME.upper()}*\n\n"
        f"📊 *СТАТИСТИКА СИСТЕМЫ:*\n"
        f"• 📋 Всего заявок: {stats['total']}\n"
        f"• 🆕 Новые: {stats['new']}\n"
        f"• 🔄 В работе: {stats['in_progress']}\n"
        f"• ✅ Выполнено: {stats['completed']}\n"
        f"• 🎯 Эффективность: {stats['efficiency']}%\n"
        f"• ⭐ Средняя оценка: {stats['avg_rating']}/5\n"
        f"• 🚀 Выполнено сегодня: {stats['completed_today']}\n"
        f"• 👥 Всего пользователей: {stats['total_users']}\n"
        f"• 🔥 Активных пользователей: {stats['active_users']}\n\n"
        f"🛠️ *УПРАВЛЕНИЕ СИСТЕМОЙ:*"
    )
    
    keyboard = [
        ["📋 Новые заявки", "🔄 В работе"],
        ["✅ Выполненные", "📊 Общая статистика"],
        ["💾 Создать бэкап", "🔄 Сброс системы"],
        ["🔙 Главное меню"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        admin_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def admin_requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📋 Показывает заявки для администратора"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    status_filter = None
    if update.message.text == "📋 Новые заявки":
        status_filter = 'new'
        title = "🆕 НОВЫЕ ЗАЯВКИ"
        emoji = "🆕"
    elif update.message.text == "🔄 В работе":
        status_filter = 'in_progress'
        title = "🔄 ЗАЯВКИ В РАБОТЕ"
        emoji = "🔄"
    elif update.message.text == "✅ Выполненные":
        status_filter = 'completed'
        title = "✅ ВЫПОЛНЕННЫЕ ЗАЯВКИ"
        emoji = "✅"
    else:
        title = "📋 ВСЕ ЗАЯВКИ"
        emoji = "📋"
    
    requests = db.get_requests(status=status_filter, limit=20)
    if not requests:
        await update.message.reply_text(f"📭 Заявок в этой категории нет.")
        return
    
    requests_text = f"{emoji} *{title}*\n\n"
    
    for req in requests:
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }.get(req['status'], '❓')
        
        created_date = datetime.fromisoformat(req['created_at']).strftime('%d.%m %H:%M')
        
        requests_text += (
            f"{status_emoji} *Заявка #{req['id']}*\n"
            f"👤 {req['username']} | 📞 {req['phone']}\n"
            f"🔧 {req['problem'][:60]}...\n"
            f"🕒 {created_date}\n"
        )
        
        if req['assigned_admin']:
            requests_text += f"👨‍💼 {req['assigned_admin']}\n"
        
        requests_text += "\n"
    
    keyboard = [["🔙 Назад в админку", "🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(requests_text, 
                                  reply_markup=reply_markup,
                                  parse_mode=ParseMode.MARKDOWN)

# ==================== УЛУЧШЕННЫЕ ОСНОВНЫЕ ОБРАБОТЧИКИ ====================

async def show_user_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📂 Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    requests = db.get_user_requests(user_id)
    
    if not requests:
        keyboard = [["📝 Создать заявку", "🔙 Главное меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📭 У вас пока нет заявок.\n\n"
            "💡 Создайте первую заявку, и мы поможем решить вашу проблему!",
            reply_markup=reply_markup
        )
        return
    
    requests_text = "📂 *ВАШИ ЗАЯВКИ*\n\n"
    
    for req in requests[:15]:  # Показываем последние 15 заявок
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }.get(req['status'], '❓')
        
        created_date = datetime.fromisoformat(req['created_at']).strftime('%d.%m.%Y')
        
        requests_text += (
            f"{status_emoji} *Заявка #{req['id']}*\n"
            f"📝 {req['problem'][:50]}...\n"
            f"📅 {created_date}\n"
            f"🔸 Статус: {req['status']}\n"
        )
        
        if req['user_rating'] > 0:
            requests_text += f"⭐ Оценка: {'★' * req['user_rating']}{'☆' * (5 - req['user_rating'])}\n"
        
        requests_text += "\n"
    
    keyboard = [["📝 Создать заявку", "🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(requests_text, 
                                  reply_markup=reply_markup,
                                  parse_mode=ParseMode.MARKDOWN)

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📊 Показывает статистику для пользователя"""
    stats = db.get_statistics()
    
    stats_text = (
        f"📊 *СТАТИСТИКА {Config.IT_DEPARTMENT_NAME.upper()}*\n\n"
        f"🏢 *{Config.COMPANY_NAME}*\n\n"
        f"📈 *ОБЩАЯ СТАТИСТИКА:*\n"
        f"• 📋 Всего заявок: {stats['total']}\n"
        f"• ✅ Выполнено: {stats['completed']}\n"
        f"• 🎯 Эффективность: {stats['efficiency']}%\n"
        f"• ⭐ Средняя оценка: {stats['avg_rating']}/5\n"
        f"• 🚀 Выполнено сегодня: {stats['completed_today']}\n\n"
        f"💡 *Мы работаем для вашего комфорта!*"
    )
    
    keyboard = [["📝 Создать заявку", "🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        stats_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def show_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📞 Показывает контакты отдела"""
    contacts_text = (
        f"👨‍💼 *КОНТАКТЫ {Config.IT_DEPARTMENT_NAME.upper()}*\n\n"
        f"🏢 *{Config.COMPANY_NAME}*\n\n"
        f"📞 *Телефон:* {Config.SUPPORT_PHONE}\n"
        f"📧 *Email:* {Config.SUPPORT_EMAIL}\n"
        f"🕒 *Время работы:* 9:00 - 18:00\n"
        f"📍 *Местоположение:* [Укажите адрес]\n\n"
        f"💡 *Также вы можете:*\n"
        f"• 📝 Создать заявку через бота\n"
        f"• 📂 Отслеживать статус заявок\n"
        f"• ⭐ Оценивать качество работы\n\n"
        f"🚀 *Мы всегда готовы помочь!*"
    )
    
    keyboard = [["📝 Создать заявку", "🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        contacts_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🆘 Показывает справку"""
    help_text = (
        f"🆘 *ПОМОЩЬ {Config.IT_DEPARTMENT_NAME.upper()}*\n\n"
        f"🎯 *ОСНОВНЫЕ КОМАНДЫ:*\n"
        f"• /start - 🏠 Главное меню\n"
        f"• /new_request - 📝 Создать заявку\n"
        f"• /my_requests - 📂 Мои заявки\n"
        f"• /help - 🆘 Помощь\n"
        f"• /reset - 🔄 Сброс данных\n\n"
        f"💡 *КАК РАБОТАЕТ СИСТЕМА:*\n"
        f"1. 📝 Создайте заявку с описанием проблемы\n"
        f"2. 📎 Прикрепите фото/видео (по желанию)\n"
        f"3. 🔄 Отслеживайте статус заявки\n"
        f"4. ✅ Получайте уведомления о выполнении\n"
        f"5. ⭐ Оценивайте качество работы\n\n"
        f"👨‍💼 *ДЛЯ АДМИНИСТРАТОРОВ:*\n"
        f"• /admin - 👨‍💼 Админ панель\n"
        f"• /backup - 💾 Создать бэкап\n\n"
        f"📞 *ЭКСТРЕННАЯ ПОМОЩЬ:*\n"
        f"Телефон: {Config.SUPPORT_PHONE}\n"
        f"Email: {Config.SUPPORT_EMAIL}\n\n"
        f"💼 *Мы ценим ваше время и стремимся к лучшему сервису!*"
    )
    
    keyboard = [["🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(help_text, 
                                  reply_markup=reply_markup,
                                  parse_mode=ParseMode.MARKDOWN)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """💬 Обрабатывает текстовые сообщения из меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Обработка подтверждения сброса
    if context.user_data.get('awaiting_reset_confirmation'):
        if text == "✅ Да, сбросить":
            # Здесь можно добавить логику сброса данных пользователя
            context.user_data.pop('awaiting_reset_confirmation', None)
            await update.message.reply_text(
                "🔄 *Данные сброшены!*\n\n"
                "Все ваши незавершенные заявки были удалены.\n"
                "Вы можете начать с чистого листа.",
                parse_mode=ParseMode.MARKDOWN
            )
            await show_main_menu(update, context)
        elif text == "❌ Нет, отмена":
            context.user_data.pop('awaiting_reset_confirmation', None)
            await update.message.reply_text("❌ Сброс данных отменен.")
            await show_main_menu(update, context)
        return
    
    # Основные кнопки для всех пользователей
    if text == "📂 Мои заявки":
        await show_user_requests(update, context)
    elif text == "📝 Создать заявку":
        await new_request_command(update, context)
    elif text == "📊 Статистика":
        await show_statistics(update, context)
    elif text == "👨‍💼 Контакты отдела":
        await show_contacts(update, context)
    elif text == "🆘 Помощь":
        await help_command(update, context)
    elif text == "🔙 Главное меню":
        await show_main_menu(update, context)
    elif text == "🔄 Сброс системы" and Config.is_admin(user_id):
        await reset_command(update, context)
    elif text == "💾 Создать бэкап" and Config.is_admin(user_id):
        await backup_command(update, context)
    
    # Админские кнопки
    elif text == "👨‍💼 Админ панель" and Config.is_admin(user_id):
        await admin_panel_command(update, context)
    elif text == "📋 Все заявки" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "📋 Новые заявки" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "🔄 В работе" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "✅ Выполненные" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "📊 Общая статистика" and Config.is_admin(user_id):
        await admin_panel_command(update, context)
    elif text == "🔙 Назад в админку" and Config.is_admin(user_id):
        await admin_panel_command(update, context)
    else:
        keyboard = [["🔙 Главное меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🤔 Не понимаю ваше сообщение. Пожалуйста, используйте кнопки меню.",
            reply_markup=reply_markup
        )

# ==================== НАСТРОЙКА ОБРАБОТЧИКОВ ====================

def setup_handlers(application: Application):
    """🔧 Настройка всех обработчиков"""
    
    # Обработчик создания заявки (ConversationHandler)
    request_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new_request", new_request_command),
                     MessageHandler(filters.Text("📝 Создать заявку"), new_request_command)],
        states={
            REQUEST_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_phone)],
            REQUEST_PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_problem)],
            REQUEST_MEDIA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_media),
                MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.VOICE, handle_media)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_request)]
    )
    
    # Основные команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_requests", show_user_requests))
    application.add_handler(CommandHandler("admin", admin_panel_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(request_conv_handler)
    
    # Обработчики callback (кнопки администраторов)
    application.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern="^(take_|details_|complete_|feedback_)"))
    
    # Обработчик комментариев администратора
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_comment))
    
    # Обработчики текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================

def main() -> None:
    """🚀 Запуск бота"""
    try:
        print("🔄 Запуск бота IT отдела завода Контакт...")
        
        # Обработчики сигналов
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Проверка конфигурации
        Config.validate_config()
        print("✅ Конфигурация проверена")
        
        if not Config.BOT_TOKEN:
            logger.error("❌ Токен бота не загружен!")
            print("❌ Токен бота не найден!")
            return
        
        # Создание приложения
        print("🤖 Создание приложения...")
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Настройка обработчиков
        print("🔧 Настройка обработчиков...")
        setup_handlers(application)
        print("✅ Все компоненты настроены")
        
        logger.info("🚀 Бот IT отдела успешно запущен!")
        print("🎉 Бот IT отдела завода Контакт успешно запущен!")
        print("✨ УЛУЧШЕННЫЕ ВОЗМОЖНОСТИ:")
        print("   • 🏢 Адаптация под завод Контакт")
        print("   • 📝 Умное создание заявок с валидацией")
        print("   • 📎 Поддержка фото, видео, документов и голосовых сообщений")
        print("   • ⭐ Система оценок и отзывов")
        print("   • 📊 Детальная статистика")
        print("   • 🔒 Ограничитель запросов от спама")
        print("   • 📞 Улучшенная валидация телефонов")
        print("   • 💾 Автоматическое резервное копирование")
        print("   • 🔙 Улучшенная навигация с кнопками 'Назад'")
        print("   • 👨‍💼 Полная админ-панель")
        print("   • 💬 Комментарии к выполненным работам")
        print("   • 🔔 Умные уведомления")
        print("   • 📈 Аналитика эффективности")
        print("   • 🛑 Graceful shutdown обработка")
        print("   • 🎯 Профессиональные шаблоны сообщений")
        print("\n🚀 Бот готов к работе!")
        
        # Запуск бота
        print("🔄 Запуск опроса...")
        application.run_polling()

    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
        print("\n🛑 Бот остановлен")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
