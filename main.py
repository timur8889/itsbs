import logging
import sqlite3
import os
import json
import re
import threading
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

# ==================== ПРОВЕРКА ДОПОЛНИТЕЛЬНЫХ БИБЛИОТЕК ====================
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logging.warning("psutil не установлен - метрики системы будут ограничены")

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logging.warning("matplotlib не установлен - графики отключены")

try:
    from telegram import (
        ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, 
        InlineKeyboardMarkup, InlineKeyboardButton
    )
    from telegram.ext import (
        Updater, CommandHandler, MessageHandler, Filters, 
        ConversationHandler, CallbackContext, CallbackQueryHandler, JobQueue
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logging.error("python-telegram-bot не установлен")

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    GOOGLE_SHEETS_AVAILABLE = False

try:
    from flask import Flask, jsonify, request
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# ==================== КОНФИГУРАЦИЯ ====================
class Config:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN', '').strip()
        self.admin_chat_ids = self._parse_admin_ids()
        self.max_requests_per_hour = int(os.getenv('MAX_REQUESTS_PER_HOUR', '15'))
        self.backup_retention_days = int(os.getenv('BACKUP_RETENTION_DAYS', '30'))
        self.auto_backup_hour = int(os.getenv('AUTO_BACKUP_HOUR', '3'))
        self.auto_backup_minute = int(os.getenv('AUTO_BACKUP_MINUTE', '0'))
        self.request_timeout_hours = int(os.getenv('REQUEST_TIMEOUT_HOURS', '24'))
        self.db_path = os.getenv('DB_PATH', 'requests.db')
        self.backup_dir = os.getenv('BACKUP_DIR', 'backups')
        
        # Google Sheets настройки
        self.google_sheets_credentials = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
        self.google_sheet_id = os.getenv('GOOGLE_SHEET_ID')
        self.google_sheet_name = os.getenv('GOOGLE_SHEET_NAME', 'Заявки')
        self.sync_to_sheets = bool(self.google_sheets_credentials and self.google_sheet_id and GOOGLE_SHEETS_AVAILABLE)
        
        # Дополнительные настройки
        self.openai_api_key = os.getenv('OPENAI_API_KEY', '')
        self.web_dashboard_port = int(os.getenv('WEB_DASHBOARD_PORT', '5000'))
    
    def _parse_admin_ids(self):
        """Безопасный парсинг ID администраторов"""
        try:
            admin_ids = os.getenv('ADMIN_CHAT_IDS', '')
            if not admin_ids:
                return []
            return [int(x.strip()) for x in admin_ids.split(',') if x.strip()]
        except (ValueError, AttributeError) as e:
            logging.error(f"❌ Неверный формат ADMIN_CHAT_IDS: {e}")
            return []
    
    def validate(self) -> bool:
        if not self.bot_token or self.bot_token == 'your_bot_token':
            logging.error("❌ BOT_TOKEN не установлен или имеет значение по умолчанию")
            return False
        if not self.admin_chat_ids:
            logging.warning("⚠️ ADMIN_CHAT_IDS не установлены - некоторые функции будут ограничены")
        return True

# Инициализация конфигурации
config = Config()

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
db = None
analytics_engine = None
ai_assistant = None
security_manager = None
gamification_engine = None
rate_limiter = None

# ==================== КОНСТАНТЫ ДЛЯ CONVERSATIONHANDLER ====================
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, URGENCY = range(6)

# ==================== КЛАВИАТУРЫ ====================
user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['📊 Моя статистика', '🆘 Срочная помощь'],
    ['🎮 Игровая статистика', 'ℹ️ О боте']
]

def get_admin_panel():
    """Получает клавиатуру админ-панели"""
    new_count = len(db.get_requests_by_filter('new')) if db else 0
    in_progress_count = len(db.get_requests_by_filter('in_progress')) if db else 0
    
    return [
        [f'🆕 Новые ({new_count})', f'🔄 В работе ({in_progress_count})'],
        ['📊 Статистика', '📈 Аналитика'],
        ['💾 Бэкапы', '🔄 Обновить'],
        ['🎮 Геймификация', '📊 Метрики']
    ]

def create_request_actions_keyboard(request_id: int):
    """Создает клавиатуру действий для заявки"""
    keyboard = [
        [
            InlineKeyboardButton("✅ В работу", callback_data=f"assign_{request_id}"),
            InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{request_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ДЕКОРАТОР БЕЗОПАСНОГО ВЫПОЛНЕНИЯ ====================
def safe_execute(default_response="Произошла ошибка"):
    """Декоратор для безопасного выполнения функций"""
    def decorator(func):
        def wrapper(update, context, *args, **kwargs):
            try:
                return func(update, context, *args, **kwargs)
            except Exception as e:
                logging.error(f"Ошибка в {func.__name__}: {e}")
                
                if update and hasattr(update, 'message') and update.message:
                    update.message.reply_text(
                        default_response,
                        reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
                    )
                
                return ConversationHandler.END
        return wrapper
    return decorator

# ==================== ВАЛИДАТОРЫ ====================
class Validators:
    @staticmethod
    def validate_phone(phone: str) -> bool:
        return bool(re.match(r'^[\d\s\-\+\(\)]{7,20}$', phone.strip()))
    
    @staticmethod
    def validate_name(name: str) -> bool:
        return bool(re.match(r'^[А-Яа-яA-Za-z\s]{2,50}$', name.strip()))
    
    @staticmethod
    def validate_plot(plot: str) -> bool:
        return bool(re.match(r'^[А-Яа-яA-Za-z0-9\s\-]{2,20}$', plot.strip()))

# ==================== СИСТЕМА БЕЗОПАСНОСТИ ====================
class SecurityManager:
    def __init__(self):
        self.suspicious_activities = {}
        self.blocked_users = set()
    
    def check_suspicious_activity(self, user_id, action):
        """Проверяет подозрительную активность"""
        now = datetime.now()
        hour_key = now.strftime("%Y%m%d%H")
        
        if user_id not in self.suspicious_activities:
            self.suspicious_activities[user_id] = {}
        
        if hour_key not in self.suspicious_activities[user_id]:
            self.suspicious_activities[user_id][hour_key] = 0
        
        self.suspicious_activities[user_id][hour_key] += 1
        
        if self.suspicious_activities[user_id][hour_key] > 50:
            self.blocked_users.add(user_id)
            return False
        
        return True
    
    def is_user_blocked(self, user_id):
        return user_id in self.blocked_users

# ==================== RATE LIMITER ====================
class RateLimiter:
    def __init__(self):
        self.requests = {}
    
    def check_rate_limit(self, user_id: int, action: str = "default") -> Tuple[bool, str]:
        """Проверяет лимиты и возвращает статус + сообщение"""
        now = datetime.now()
        hour_key = now.strftime("%Y%m%d%H")
        
        if user_id not in self.requests:
            self.requests[user_id] = {}
        
        if hour_key not in self.requests[user_id]:
            self.requests[user_id][hour_key] = 0
        
        self.requests[user_id][hour_key] += 1
        
        if self.requests[user_id][hour_key] > config.max_requests_per_hour:
            return False, "🚫 Превышен лимит запросов. Попробуйте через час."
        
        if security_manager and security_manager.is_user_blocked(user_id):
            return False, "🚫 Ваш аккаунт временно заблокирован."
        
        return True, ""

# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
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
                        urgency TEXT,
                        status TEXT DEFAULT 'new',
                        created_at TEXT,
                        updated_at TEXT,
                        assigned_to TEXT,
                        completed_at TEXT
                    )
                ''')
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
                conn.commit()
                logging.info("✅ База данных инициализирована")
        except Exception as e:
            logging.error(f"Ошибка инициализации базы данных: {e}")
    
    def save_request(self, data: Dict) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Обновляем пользователя
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
                     problem, urgency, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data['user_id'], data.get('username'), data.get('first_name'), data.get('last_name'),
                    data.get('name'), data.get('phone'), data.get('plot'), data.get('system_type'),
                    data.get('problem'), data.get('urgency'), 'new',
                    datetime.now().isoformat(), datetime.now().isoformat()
                ))
                
                request_id = cursor.lastrowid
                conn.commit()
                logging.info(f"✅ Заявка #{request_id} сохранена")
                return request_id
                
        except sqlite3.Error as e:
            logging.error(f"Ошибка сохранения заявки: {e}")
            raise
    
    def get_requests_by_filter(self, status: str) -> List[Dict]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests WHERE status = ? ORDER BY created_at DESC
                ''', (status,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logging.error(f"Ошибка получения заявок: {e}")
            return []
    
    def get_user_statistics(self, user_id: int) -> Dict:
        """Получает статистику пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total_requests,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new
                    FROM requests 
                    WHERE user_id = ?
                ''', (user_id,))
                
                result = cursor.fetchone()
                if result:
                    columns = ['total_requests', 'completed', 'in_progress', 'new']
                    return dict(zip(columns, result))
                return {}
        except sqlite3.Error as e:
            logging.error(f"Ошибка получения статистики пользователя: {e}")
            return {}
    
    def update_request(self, request_id: int, updates: Dict):
        """Обновляет заявку"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
                values = list(updates.values())
                
                query = f"UPDATE requests SET {set_clause}, updated_at = ? WHERE id = ?"
                cursor.execute(query, values + [datetime.now().isoformat(), request_id])
                conn.commit()
                logging.info(f"✅ Заявка #{request_id} обновлена")
                return True
        except sqlite3.Error as e:
            logging.error(f"Ошибка обновления заявки: {e}")
            return False
    
    def get_request_by_id(self, request_id: int) -> Optional[Dict]:
        """Получает заявку по ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
                row = cursor.fetchone()
                
                if row:
                    columns = [column[0] for column in cursor.description]
                    return dict(zip(columns, row))
                return None
        except sqlite3.Error as e:
            logging.error(f"Ошибка получения заявки: {e}")
            return None

    def get_statistics(self, days: int = 7) -> Dict:
        """Получает общую статистику"""
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
            logging.error(f"Ошибка получения статистики: {e}")
            return {'total': 0, 'completed': 0, 'new': 0, 'in_progress': 0}

# ==================== BACKUP MANAGER ====================
class BackupManager:
    @staticmethod
    def create_backup():
        try:
            if not os.path.exists(config.db_path):
                logging.error(f"Файл базы данных {config.db_path} не существует")
                return None
                
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(config.backup_dir, f"backup_{timestamp}.db")
            os.makedirs(config.backup_dir, exist_ok=True)
            shutil.copy2(config.db_path, backup_path)
            logging.info(f"✅ Бэкап создан: {backup_path}")
            return backup_path
        except Exception as e:
            logging.error(f"Ошибка создания бэкапа: {e}")
            return None

# ==================== ГЕЙМИФИКАЦИЯ ====================
class GamificationEngine:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_gamification()
    
    def init_gamification(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_points (
                        user_id INTEGER PRIMARY KEY,
                        points INTEGER DEFAULT 0,
                        level INTEGER DEFAULT 1,
                        last_activity TEXT
                    )
                ''')
                conn.commit()
                logging.info("✅ Система геймификации инициализирована")
        except Exception as e:
            logging.error(f"Ошибка инициализации геймификации: {e}")
    
    def award_points(self, user_id, action):
        """Начисляет очки за действие"""
        point_values = {
            'create_request': 10,
            'request_completed': 5
        }
        
        points_to_award = point_values.get(action, 0)
        
        if points_to_award > 0:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT OR REPLACE INTO user_points 
                        (user_id, points, level, last_activity)
                        VALUES (?, 
                            COALESCE((SELECT points FROM user_points WHERE user_id = ?), 0) + ?,
                            COALESCE((SELECT level FROM user_points WHERE user_id = ?), 1),
                            ?
                        )
                    ''', (user_id, user_id, points_to_award, user_id, datetime.now().isoformat()))
                    conn.commit()
                    logging.info(f"✅ Начислено {points_to_award} очков пользователю {user_id}")
            except Exception as e:
                logging.error(f"Ошибка начисления очков: {e}")
    
    def get_user_stats(self, user_id):
        """Возвращает статистику пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT points, level FROM user_points WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                return {'points': result[0] if result else 0, 'level': result[1] if result else 1}
        except Exception as e:
            logging.error(f"Ошибка получения статистики пользователя: {e}")
            return {'points': 0, 'level': 1}

# ==================== ОСНОВНЫЕ ФУНКЦИИ БОТА ====================
@safe_execute("Ошибка при отображении меню")
def show_main_menu(update: Update, context: CallbackContext):
    """Показывает главное меню"""
    user = update.message.from_user
    
    # Проверка лимитов
    allowed, message = rate_limiter.check_rate_limit(user.id, "main_menu")
    if not allowed:
        update.message.reply_text(message)
        return
    
    if user.id in config.admin_chat_ids:
        reply_markup = ReplyKeyboardMarkup(get_admin_panel(), resize_keyboard=True)
    else:
        reply_markup = ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        "🏠 Главное меню\n\nВыберите действие:",
        reply_markup=reply_markup
    )

@safe_execute("Ошибка при создании заявки")
def start_request_creation(update: Update, context: CallbackContext):
    """Начало создания заявки"""
    user = update.message.from_user
    
    # Проверка лимитов
    allowed, message = rate_limiter.check_rate_limit(user.id, "create_request")
    if not allowed:
        update.message.reply_text(message)
        return ConversationHandler.END
    
    update.message.reply_text(
        "📝 Создание новой заявки\n\nВведите ваше ФИО:",
        reply_markup=ReplyKeyboardRemove()
    )
    return NAME

@safe_execute("Ошибка при обработке имени")
def process_name(update: Update, context: CallbackContext):
    """Обработка имени"""
    name_text = update.message.text
    if not Validators.validate_name(name_text):
        update.message.reply_text("❌ Неверный формат имени. Используйте только буквы и пробелы.")
        return NAME
    
    context.user_data['name'] = name_text
    update.message.reply_text("📞 Введите ваш номер телефона:")
    return PHONE

@safe_execute("Ошибка при обработке телефона")
def process_phone(update: Update, context: CallbackContext):
    """Обработка телефона"""
    phone_text = update.message.text
    if not Validators.validate_phone(phone_text):
        update.message.reply_text("❌ Неверный формат телефона. Пример: +7 123 456-78-90")
        return PHONE
    
    context.user_data['phone'] = phone_text
    
    keyboard = [['Участок 1', 'Участок 2', 'Участок 3'], ['Другой участок']]
    update.message.reply_text(
        "📍 Выберите или введите номер участка:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return PLOT

@safe_execute("Ошибка при обработке участка")
def process_plot(update: Update, context: CallbackContext):
    """Обработка участка"""
    plot_text = update.message.text
    if not Validators.validate_plot(plot_text):
        update.message.reply_text("❌ Неверный формат участка. Используйте только буквы, цифры и дефисы.")
        return PLOT
    
    context.user_data['plot'] = plot_text
    
    keyboard = [
        ['🔌 Электрика', '📶 Интернет'],
        ['📞 Телефония', '🎥 Видеонаблюдение']
    ]
    update.message.reply_text(
        "⚙️ Выберите тип системы:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return SYSTEM_TYPE

@safe_execute("Ошибка при обработке типа системы")
def process_system_type(update: Update, context: CallbackContext):
    """Обработка типа системы"""
    context.user_data['system_type'] = update.message.text
    
    update.message.reply_text(
        "📝 Опишите проблему подробно:",
        reply_markup=ReplyKeyboardRemove()
    )
    return PROBLEM

@safe_execute("Ошибка при обработке проблемы")
def process_problem(update: Update, context: CallbackContext):
    """Обработка описания проблемы"""
    problem_text = update.message.text
    
    if len(problem_text) < 5:
        update.message.reply_text("❌ Слишком короткое описание проблемы")
        return PROBLEM
    
    context.user_data['problem'] = problem_text
    
    keyboard = [['🔴 Срочно', '🟡 Средняя', '🟢 Не срочно']]
    update.message.reply_text(
        "⏱️ Выберите срочность заявки:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return URGENCY

@safe_execute("Ошибка при обработке срочности")
def process_urgency(update: Update, context: CallbackContext):
    """Обработка срочности"""
    context.user_data['urgency'] = update.message.text
    context.user_data['user_id'] = update.message.from_user.id
    context.user_data['username'] = update.message.from_user.username
    context.user_data['first_name'] = update.message.from_user.first_name
    context.user_data['last_name'] = update.message.from_user.last_name
    
    # Показываем подтверждение
    request_text = (
        "📋 Проверьте данные заявки:\n\n"
        f"👤 ФИО: {context.user_data['name']}\n"
        f"📞 Телефон: {context.user_data['phone']}\n"
        f"📍 Участок: {context.user_data['plot']}\n"
        f"⚙️ Система: {context.user_data['system_type']}\n"
        f"📝 Проблема: {context.user_data['problem']}\n"
        f"⏱️ Срочность: {context.user_data['urgency']}\n\n"
        "Всё верно?"
    )
    
    keyboard = [['✅ Подтвердить отправку', '❌ Отменить']]
    update.message.reply_text(
        request_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    
    return ConversationHandler.END

@safe_execute("Ошибка при подтверждении заявки")
def confirm_request(update: Update, context: CallbackContext) -> None:
    """Подтверждение заявки"""
    if update.message.text == '✅ Подтвердить отправку':
        user = update.message.from_user
        
        try:
            required_fields = ['name', 'phone', 'plot', 'system_type', 'problem', 'urgency']
            for field in required_fields:
                if field not in context.user_data or not context.user_data[field]:
                    update.message.reply_text(
                        f"❌ Отсутствует обязательное поле: {field}",
                        reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
                    )
                    return
            
            request_id = db.save_request(context.user_data)
            
            # Начисляем очки за создание заявки
            gamification_engine.award_points(user.id, 'create_request')
            
            confirmation_text = (
                f"✅ Заявка #{request_id} успешно создана!\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ Срочность: {context.user_data['urgency']}\n"
                f"📍 Участок: {context.user_data['plot']}\n\n"
                f"Спасибо за обращение! 🛠️"
            )
            
            if user.id in config.admin_chat_ids:
                reply_markup = ReplyKeyboardMarkup(get_admin_panel(), resize_keyboard=True)
            else:
                reply_markup = ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
            
            update.message.reply_text(
                confirmation_text,
                reply_markup=reply_markup
            )
            
            # Уведомление админам
            admin_message = (
                f"🆕 Новая заявка #{request_id}\n\n"
                f"👤 Клиент: {context.user_data['name']}\n"
                f"📞 Телефон: {context.user_data['phone']}\n"
                f"📍 Участок: {context.user_data['plot']}\n"
                f"⚙️ Система: {context.user_data['system_type']}\n"
                f"⏱️ Срочность: {context.user_data['urgency']}\n"
                f"📝 Проблема: {context.user_data['problem'][:100]}...\n"
            )
            
            for admin_id in config.admin_chat_ids:
                try:
                    context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_message,
                        reply_markup=create_request_actions_keyboard(request_id)
                    )
                except Exception as e:
                    logging.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
            
            logging.info(f"✅ Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logging.error(f"Ошибка при сохранении заявки: {e}")
            update.message.reply_text(
                "❌ Произошла ошибка при создании заявки.\n\nПожалуйста, попробуйте позже."
            )
        
        context.user_data.clear()
    else:
        update.message.reply_text(
            "Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )

def cancel_request(update: Update, context: CallbackContext):
    """Отмена создания заявки"""
    context.user_data.clear()
    update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
    )
    return ConversationHandler.END

@safe_execute("Ошибка при обработке меню")
def handle_main_menu(update: Update, context: CallbackContext):
    """Обработка основного меню пользователя"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    # Проверка лимитов
    allowed, message = rate_limiter.check_rate_limit(user_id, "main_menu")
    if not allowed:
        update.message.reply_text(message)
        return
    
    if text == '📋 Мои заявки':
        show_user_requests(update, context)
    
    elif text == '📊 Моя статистика':
        user_stats = db.get_user_statistics(user_id) if db else {}
        stats_text = (
            "📊 Ваша статистика\n\n"
            f"📨 Всего заявок: {user_stats.get('total_requests', 0)}\n"
            f"✅ Выполнено: {user_stats.get('completed', 0)}\n"
            f"🔄 В работе: {user_stats.get('in_progress', 0)}\n"
            f"🆕 Новых: {user_stats.get('new', 0)}\n"
        )
        update.message.reply_text(stats_text)
    
    elif text == '🎮 Игровая статистика':
        show_gamification_stats(update, context)

@safe_execute("Ошибка при обработке админ-меню")
def handle_admin_menu(update: Update, context: CallbackContext):
    """Обработка админ-меню"""
    user_id = update.message.from_user.id
    if user_id not in config.admin_chat_ids:
        update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    text = update.message.text
    
    if text.startswith('🆕 Новые'):
        new_requests = db.get_requests_by_filter('new') if db else []
        if not new_requests:
            update.message.reply_text("✅ Новых заявок нет")
        else:
            update.message.reply_text(f"🆕 Найдено {len(new_requests)} новых заявок:")
            for request in new_requests[:3]:
                request_text = (
                    f"🆕 Заявка #{request['id']}\n\n"
                    f"👤 {request['name']}\n"
                    f"📞 {request['phone']}\n"
                    f"📍 {request['plot']}\n"
                    f"📝 {request['problem'][:100]}...\n"
                )
                update.message.reply_text(
                    request_text,
                    reply_markup=create_request_actions_keyboard(request['id'])
                )
    
    elif text == '📊 Статистика':
        show_statistics(update, context)
    
    elif text == '🎮 Геймификация':
        show_gamification_stats(update, context)
    
    elif text == '🔄 Обновить':
        show_admin_panel(update, context)

@safe_execute("Ошибка при обработке кнопок")
def button_handler(update: Update, context: CallbackContext):
    """Обработчик inline кнопок"""
    query = update.callback_query
    query.answer()
    
    data = query.data
    
    if data.startswith('assign_'):
        request_id = int(data.split('_')[1])
        if db.update_request(request_id, {'status': 'in_progress'}):
            query.edit_message_text(f"✅ Заявка #{request_id} взята в работу")
            
            # Уведомляем пользователя
            try:
                request_data = db.get_request_by_id(request_id)
                if request_data:
                    context.bot.send_message(
                        chat_id=request_data['user_id'],
                        text=f"🔄 Ваша заявка #{request_id} взята в работу! Специалист уже выехал."
                    )
            except Exception as e:
                logging.error(f"Ошибка уведомления пользователя: {e}")
        else:
            query.edit_message_text(f"❌ Ошибка обновления заявки #{request_id}")
    
    elif data.startswith('complete_'):
        request_id = int(data.split('_')[1])
        if db.update_request(request_id, {'status': 'completed', 'completed_at': datetime.now().isoformat()}):
            query.edit_message_text(f"✅ Заявка #{request_id} выполнена")
            
            # Уведомляем пользователя
            try:
                request_data = db.get_request_by_id(request_id)
                if request_data:
                    context.bot.send_message(
                        chat_id=request_data['user_id'],
                        text=f"✅ Ваша заявка #{request_id} выполнена! Спасибо за обращение!"
                    )
                    # Начисляем очки за выполнение заявки
                    gamification_engine.award_points(request_data['user_id'], 'request_completed')
            except Exception as e:
                logging.error(f"Ошибка уведомления пользователя: {e}")
        else:
            query.edit_message_text(f"❌ Ошибка завершения заявки #{request_id}")

def show_user_requests(update: Update, context: CallbackContext):
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    
    try:
        with sqlite3.connect(config.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, problem, status, created_at, urgency 
                FROM requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT 5
            ''', (user_id,))
            
            requests = cursor.fetchall()
            
        if not requests:
            update.message.reply_text("📭 У вас пока нет заявок")
            return
        
        text = "📋 Ваши последние заявки:\n\n"
        for req_id, problem, status, created_at, urgency in requests:
            status_icons = {
                'new': '🆕',
                'in_progress': '🔄', 
                'completed': '✅'
            }
            date = datetime.fromisoformat(created_at).strftime("%d.%m.%Y %H:%M")
            text += f"{status_icons.get(status, '📄')} Заявка #{req_id}\n"
            text += f"📝 {problem[:50]}...\n"
            text += f"⏱️ {urgency} | {date}\n"
            text += f"📊 Статус: {status}\n\n"
        
        update.message.reply_text(text)
        
    except Exception as e:
        logging.error(f"Ошибка получения заявок пользователя: {e}")
        update.message.reply_text("❌ Ошибка при получении заявок")

def show_gamification_stats(update: Update, context: CallbackContext):
    """Показывает статистику геймификации"""
    user_id = update.message.from_user.id
    user_stats = gamification_engine.get_user_stats(user_id)
    
    text = "🎮 Ваша статистика\n\n"
    text += f"🏆 Уровень: {user_stats['level']}\n"
    text += f"⭐ Очки: {user_stats['points']}\n"
    
    update.message.reply_text(text)

def show_statistics(update: Update, context: CallbackContext):
    """Показывает статистику"""
    stats = db.get_statistics(7) if db else {}
    
    text = (
        "📊 Статистика за 7 дней\n\n"
        f"📨 Всего заявок: {stats.get('total', 0)}\n"
        f"✅ Выполнено: {stats.get('completed', 0)}\n"
        f"🔄 В работе: {stats.get('in_progress', 0)}\n"
        f"🆕 Новых: {stats.get('new', 0)}\n"
    )
    
    update.message.reply_text(text)

def show_admin_panel(update: Update, context: CallbackContext):
    """Показывает админ-панель"""
    user_id = update.message.from_user.id
    if user_id not in config.admin_chat_ids:
        update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    reply_markup = ReplyKeyboardMarkup(get_admin_panel(), resize_keyboard=True)
    update.message.reply_text(
        "👑 Панель администратора\n\nВыберите действие:",
        reply_markup=reply_markup
    )

# ==================== ЗАДАНИЯ ПО РАСПИСАНИЮ ====================
def backup_job(context: CallbackContext):
    """Задание для автоматического бэкапа"""
    backup_path = BackupManager.create_backup()
    if backup_path:
        logging.info(f"✅ Автоматический бэкап создан: {backup_path}")

def error_handler(update: Update, context: CallbackContext):
    """Обработчик ошибок"""
    logging.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.message:
        update.message.reply_text(
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )

# ==================== ИНИЦИАЛИЗАЦИЯ СИСТЕМ ====================
def initialize_basic_systems():
    """Базовая инициализация систем"""
    global db, rate_limiter, security_manager, gamification_engine
    
    try:
        # Инициализация базы данных
        db = Database(config.db_path)
        
        # Базовые системы
        rate_limiter = RateLimiter()
        security_manager = SecurityManager()
        
        # Геймификация
        gamification_engine = GamificationEngine(config.db_path)
        
        logging.info("✅ Все системы инициализированы")
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка инициализации систем: {e}")
        return False

# ==================== ЗАПУСК БОТА ====================
def main() -> None:
    """Основная функция запуска бота"""
    # Настройка логирования
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
        level=logging.INFO
    )
    
    logging.info("=" * 50)
    logging.info("🤖 ЗАПУСК СИСТЕМЫ УПРАВЛЕНИЯ ЗАЯВКАМИ")
    logging.info("=" * 50)
    
    if not config.validate():
        logging.error("❌ Неверная конфигурация бота!")
        return
    
    if not TELEGRAM_AVAILABLE:
        logging.error("❌ python-telegram-bot не установлен!")
        return
    
    # Инициализация базовых систем
    if not initialize_basic_systems():
        logging.error("❌ Не удалось инициализировать базовые системы!")
        return
    
    try:
        updater = Updater(config.bot_token)
        dispatcher = updater.dispatcher

        # Обработчик ошибок
        dispatcher.add_error_handler(error_handler)

        # Задания по расписанию
        job_queue = updater.job_queue
        if job_queue:
            try:
                # Ежедневное резервное копирование
                from datetime import time as dt_time
                backup_time = dt_time(hour=config.auto_backup_hour, minute=config.auto_backup_minute)
                job_queue.run_daily(backup_job, time=backup_time)
                logging.info("✅ Задания планировщика зарегистрированы")
            except Exception as e:
                logging.error(f"❌ Ошибка регистрации заданий планировщика: {e}")

        # Обработчик создания заявки
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(📝 Создать заявку)$'), start_request_creation),
            ],
            states={
                NAME: [MessageHandler(Filters.text & ~Filters.command, process_name)],
                PHONE: [MessageHandler(Filters.text & ~Filters.command, process_phone)],
                PLOT: [MessageHandler(Filters.text & ~Filters.command, process_plot)],
                SYSTEM_TYPE: [MessageHandler(Filters.text & ~Filters.command, process_system_type)],
                PROBLEM: [MessageHandler(Filters.text & ~Filters.command, process_problem)],
                URGENCY: [MessageHandler(Filters.text & ~Filters.command, process_urgency)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(Filters.regex('^(❌ Отменить)$'), cancel_request),
            ],
        )

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        dispatcher.add_handler(CommandHandler('stats', show_statistics))
        dispatcher.add_handler(CommandHandler('gamification', show_gamification_stats))
        dispatcher.add_handler(CommandHandler('cancel', cancel_request))
        
        dispatcher.add_handler(conv_handler)
        
        # Обработчики сообщений
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(✅ Подтвердить отправку)$'), 
            confirm_request
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📝 Создать заявку|📋 Мои заявки|📊 Моя статистика|🎮 Игровая статистика|ℹ️ О боте)$'), 
            handle_main_menu
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые|🔄 В работе|📊 Статистика|💾 Бэкапы|🔄 Обновить|🎮 Геймификация|📊 Метрики)$'), 
            handle_admin_menu
        ))
        
        # Обработчики inline кнопок
        dispatcher.add_handler(CallbackQueryHandler(button_handler))

        # Запускаем бота
        logging.info("🤖 Бот запущен!")
        logging.info(f"👑 Администраторы: {len(config.admin_chat_ids)}")
        logging.info(f"📊 Лимит запросов: {config.max_requests_per_hour}/час")
        logging.info("=" * 50)
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logging.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
