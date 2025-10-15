import logging
import sqlite3
import os
import json
import re
import threading
import shutil
import tempfile
import io
import base64
import time
import sys
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
        InlineKeyboardMarkup, InlineKeyboardButton, ParseMode, InputFile
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
# Эти переменные будут инициализированы позже
db = None
sheets_manager = None
analytics_engine = None
ai_assistant = None
security_manager = None
performance_monitor = None
template_manager = None
i18n = None
gamification_engine = None
web_dashboard = None
rate_limiter = None

# ==================== КОНСТАНТЫ ДЛЯ CONVERSATIONHANDLER ====================
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD = range(9)

# ==================== КЛАВИАТУРЫ ====================
enhanced_user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['📊 Моя статистика', '🆘 Срочная помощь'],
    ['🎮 Игровая статистика', '📈 Аналитика'],
    ['ℹ️ О боте', '⚙️ Настройки']
]

def get_enhanced_admin_panel():
    """Получает клавиатуру админ-панели с актуальными счетчиками"""
    new_count = len(db.get_requests_by_filter('new')) if db else 0
    in_progress_count = len(db.get_requests_by_filter('in_progress')) if db else 0
    urgent_count = len(db.get_urgent_requests()) if db else 0
    stuck_count = len(db.get_stuck_requests(config.request_timeout_hours)) if db else 0
    
    return [
        [f'🆕 Новые ({new_count})', f'🔄 В работе ({in_progress_count})'],
        [f'⏰ Срочные ({urgent_count})', f'🚨 Зависшие ({stuck_count})'],
        ['📊 Статистика', '📈 Аналитика'],
        ['👥 Пользователи', '⚙️ Настройки'],
        ['💾 Бэкапы', '🔄 Обновить'],
        ['📊 Google Sheets', '🔄 Синхронизация'],
        ['🎮 Геймификация', '📊 Метрики']
    ]

def create_request_actions_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру действий для заявки"""
    keyboard = [
        [
            InlineKeyboardButton("✅ В работу", callback_data=f"assign_{request_id}"),
            InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{request_id}")
        ],
        [
            InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{request_id}"),
            InlineKeyboardButton("✏️ Комментарий", callback_data=f"comment_{request_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ДЕКОРАТОР БЕЗОПАСНОГО ВЫПОЛНЕНИЯ ====================
def safe_execute(default_response="Произошла ошибка"):
    """Декоратор для безопасного выполнения функций"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logging.error(f"Ошибка в {func.__name__}: {e}", exc_info=True)
                
                # Ищем update в аргументах
                update = None
                context = None
                
                for arg in args:
                    if isinstance(arg, Update):
                        update = arg
                    elif isinstance(arg, CallbackContext):
                        context = arg
                
                if 'update' in kwargs:
                    update = kwargs['update']
                if 'context' in kwargs:
                    context = kwargs['context']
                
                if update and hasattr(update, 'message') and update.message:
                    try:
                        update.message.reply_text(
                            default_response,
                            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
                        )
                    except Exception as msg_error:
                        logging.error(f"Ошибка отправки сообщения: {msg_error}")
                
                # Уведомление админам об критической ошибке
                if context and config.admin_chat_ids:
                    for admin_id in config.admin_chat_ids[:3]:
                        try:
                            context.bot.send_message(
                                admin_id,
                                f"🚨 Критическая ошибка в {func.__name__}: {str(e)[:100]}..."
                            )
                        except Exception as admin_error:
                            logging.error(f"Ошибка уведомления админа: {admin_error}")
                
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

class EnhancedValidators:
    @staticmethod
    def validate_phone(phone: str) -> bool:
        return bool(re.match(r'^[\d\s\-\+\(\)]{7,20}$', phone.strip()))
    
    @staticmethod
    def validate_name(name: str) -> bool:
        return bool(re.match(r'^[А-Яа-яA-Za-z\s]{2,50}$', name.strip()))
    
    @staticmethod
    def validate_plot(plot: str) -> bool:
        return bool(re.match(r'^[А-Яа-яA-Za-z0-9\s\-]{2,20}$', plot.strip()))
    
    @staticmethod
    def sanitize_input(text: str, max_length: int = 500) -> str:
        """Очищает пользовательский ввод"""
        if not text:
            return ""
        
        # Удаляем опасные символы и ограничиваем длину
        sanitized = re.sub(r'[<>{}\[\]]', '', text)
        return sanitized[:max_length]
    
    @staticmethod
    def validate_problem_text(text: str) -> Tuple[bool, str]:
        """Проверяет текст проблемы"""
        if len(text) < 5:
            return False, "Слишком короткое описание проблемы"
        
        if len(text) > 1000:
            return False, "Слишком длинное описание проблемы"
        
        # Проверка на спам
        spam_keywords = ['http://', 'https://', '[url]', 'купить', 'цена']
        if any(keyword in text.lower() for keyword in spam_keywords):
            return False, "Обнаружены запрещенные слова в описании"
        
        return True, ""

# ==================== СИСТЕМА БЕЗОПАСНОСТИ ====================
class SecurityManager:
    def __init__(self):
        self.suspicious_activities = {}
        self.blocked_users = set()
        self.lock = threading.Lock()
    
    def check_suspicious_activity(self, user_id, action):
        """Проверяет подозрительную активность"""
        with self.lock:
            now = datetime.now()
            hour_key = now.strftime("%Y%m%d%H")
            
            if user_id not in self.suspicious_activities:
                self.suspicious_activities[user_id] = {}
            
            if hour_key not in self.suspicious_activities[user_id]:
                self.suspicious_activities[user_id][hour_key] = 0
            
            self.suspicious_activities[user_id][hour_key] += 1
            
            # Если больше 50 действий в час - блокируем
            if self.suspicious_activities[user_id][hour_key] > 50:
                self.blocked_users.add(user_id)
                logging.warning(f"Пользователь {user_id} заблокирован за подозрительную активность")
                return False
            
            return True
    
    def is_user_blocked(self, user_id):
        return user_id in self.blocked_users

# ==================== RATE LIMITER ====================
class EnhancedRateLimiter:
    def __init__(self):
        self.requests = {}
        self.lock = threading.Lock()
    
    def is_limited(self, user_id, action, max_requests):
        with self.lock:
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

    def check_rate_limit(self, user_id: int, action: str = "default") -> Tuple[bool, str]:
        """Проверяет лимиты и возвращает статус + сообщение"""
        if self.is_limited(user_id, action, config.max_requests_per_hour):
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
                        photo TEXT,
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
                logging.info(f"✅ Заявка #{request_id} сохранена для пользователя {data['user_id']}")
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
    
    def get_urgent_requests(self, hours: int = 2) -> List[Dict]:
        """Получает срочные заявки за последние N часов"""
        try:
            time_threshold = (datetime.now() - timedelta(hours=hours)).isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE urgency LIKE '%Срочно%' OR urgency LIKE '%КРИТИЧЕСКАЯ%'
                    AND status IN ('new', 'in_progress')
                    AND created_at > ?
                    ORDER BY created_at ASC
                ''', (time_threshold,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logging.error(f"Ошибка получения срочных заявок: {e}")
            return []
    
    def get_stuck_requests(self, hours: int = 24) -> List[Dict]:
        """Получает зависшие заявки"""
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
            logging.error(f"Ошибка получения зависших заявок: {e}")
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
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                        MIN(created_at) as first_request,
                        MAX(created_at) as last_request
                    FROM requests 
                    WHERE user_id = ?
                ''', (user_id,))
                
                result = cursor.fetchone()
                if result:
                    columns = ['total_requests', 'completed', 'in_progress', 'new', 'first_request', 'last_request']
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
                logging.info(f"✅ Заявка #{request_id} обновлена: {updates}")
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

# ==================== AI ПОМОЩНИК ====================
class AIAssistant:
    def __init__(self, api_key):
        self.api_key = api_key
    
    def analyze_problem_text(self, problem_description):
        """Анализирует текст проблемы и предлагает категорию"""
        try:
            keywords = {
                'internet': ['интернет', 'сеть', 'wi-fi', 'интернет', 'подключение'],
                'electricity': ['свет', 'электричество', 'розетка', 'напряжение', 'выключатель'],
                'phone': ['телефон', 'звонок', 'связь', 'атас', 'трубка'],
                'camera': ['камера', 'видео', 'наблюдение', 'cctv', 'объектив']
            }
            
            problem_lower = problem_description.lower()
            for category, words in keywords.items():
                if any(word in problem_lower for word in words):
                    return category
            
            return 'other'
        except Exception as e:
            logging.error(f"AI analysis error: {e}")
            return 'other'
    
    def suggest_solutions(self, problem_text, system_type):
        """Предлагает возможные решения проблемы"""
        solution_templates = {
            'electricity': "🔌 Проверьте автоматы в щитке, убедитесь в наличии напряжения",
            'internet': "📶 Перезагрузите оборудование, проверьте кабели соединения",
            'phone': "📞 Проверьте трубку, линию связи, переподключите кабель",
            'camera': "🎥 Проверьте питание, подключение к сети, чистоту объектива",
            'other': "🔧 Требуется диагностика специалистом на месте"
        }
        return solution_templates.get(system_type, "Требуется диагностика специалистом")

# ==================== ГЕЙМИФИКАЦИЯ ====================
class GamificationEngine:
    def __init__(self, db_path):
        self.db_path = db_path
        if db_path:
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
                        achievements TEXT DEFAULT '[]',
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
            'request_completed': 5,
            'first_request': 25
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
                    logging.info(f"✅ Начислено {points_to_award} очков пользователю {user_id} за {action}")
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
    
    def get_leaderboard(self, limit=10):
        """Возвращает таблицу лидеров"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_id, points, level 
                    FROM user_points 
                    ORDER BY points DESC 
                    LIMIT ?
                ''', (limit,))
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Ошибка получения таблицы лидеров: {e}")
            return []

# ==================== АНАЛИТИКА ====================
class AnalyticsEngine:
    def __init__(self):
        pass
    
    def get_advanced_analytics(self, days=30):
        """Расширенная аналитика"""
        try:
            if not hasattr(config, 'db_path') or not config.db_path:
                return {}
                
            since_date = (datetime.now() - timedelta(days=days)).isoformat()
            
            with sqlite3.connect(config.db_path) as conn:
                cursor = conn.cursor()
                
                # Аналитика по типам систем
                cursor.execute('''
                    SELECT system_type, COUNT(*) as count 
                    FROM requests 
                    WHERE created_at > ?
                    GROUP BY system_type
                ''', (since_date,))
                system_stats = dict(cursor.fetchall())
                
                # Время выполнения заявок
                cursor.execute('''
                    SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24)
                    FROM requests 
                    WHERE status = 'completed' AND completed_at IS NOT NULL
                ''')
                avg_completion_time = cursor.fetchone()[0] or 0
                
                return {
                    'system_distribution': system_stats,
                    'avg_completion_hours': round(avg_completion_time, 2),
                    'total_requests': sum(system_stats.values())
                }
        except Exception as e:
            logging.error(f"Analytics error: {e}")
            return {}

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def safe_get_ai_suggestion(problem_text: str, system_type: str) -> str:
    """Безопасное получение AI рекомендации"""
    try:
        if ai_assistant:
            return ai_assistant.suggest_solutions(problem_text, system_type)
    except Exception as e:
        logging.error(f"AI suggestion error: {e}")
    return "Требуется диагностика специалистом"

def safe_award_points(user_id: int, action: str):
    """Безопасное начисление очков"""
    try:
        if gamification_engine:
            gamification_engine.award_points(user_id, action)
    except Exception as e:
        logging.error(f"Gamification error: {e}")

def safe_get_analytics(days: int = 30) -> Dict:
    """Безопасное получение аналитики"""
    try:
        if analytics_engine:
            return analytics_engine.get_advanced_analytics(days)
    except Exception as e:
        logging.error(f"Analytics error: {e}")
    return {}

# ==================== ОСНОВНЫЕ ФУНКЦИИ БОТА ====================
@safe_execute("Ошибка при отображении меню")
def show_main_menu(update: Update, context: CallbackContext):
    """Показывает главное меню"""
    if not update or not update.message:
        return
    
    user = update.message.from_user
    
    # Проверка лимитов
    allowed, message = rate_limiter.check_rate_limit(user.id, "main_menu")
    if not allowed:
        update.message.reply_text(message)
        return
    
    if user.id in config.admin_chat_ids:
        reply_markup = ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
    else:
        reply_markup = ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        "🏠 *Главное меню*\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

@safe_execute("Ошибка при создании заявки")
def enhanced_start_request_creation(update: Update, context: CallbackContext):
    """Начало создания заявки"""
    if not update or not update.message:
        return ConversationHandler.END
    
    user = update.message.from_user
    
    # Проверка лимитов
    allowed, message = rate_limiter.check_rate_limit(user.id, "create_request")
    if not allowed:
        update.message.reply_text(message)
        return ConversationHandler.END
    
    update.message.reply_text(
        "📝 *Создание новой заявки*\n\nВведите ваше ФИО:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

@safe_execute("Ошибка при обработке имени")
def name(update: Update, context: CallbackContext):
    """Обработка имени"""
    if not update or not update.message:
        return NAME
    
    name_text = update.message.text
    if not Validators.validate_name(name_text):
        update.message.reply_text("❌ Неверный формат имени. Используйте только буквы и пробелы.")
        return NAME
    
    # Санитизация ввода
    context.user_data['name'] = EnhancedValidators.sanitize_input(name_text)
    update.message.reply_text("📞 Введите ваш номер телефона:")
    return PHONE

@safe_execute("Ошибка при обработке телефона")
def phone(update: Update, context: CallbackContext):
    """Обработка телефона"""
    if not update or not update.message:
        return PHONE
    
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
def plot(update: Update, context: CallbackContext):
    """Обработка участка"""
    if not update or not update.message:
        return PLOT
    
    plot_text = update.message.text
    if not Validators.validate_plot(plot_text):
        update.message.reply_text("❌ Неверный формат участка. Используйте только буквы, цифры и дефисы.")
        return PLOT
    
    context.user_data['plot'] = plot_text
    
    keyboard = [
        ['🔌 Электрика', '📶 Интернет'],
        ['📞 Телефония', '🎥 Видеонаблюдение'],
        ['🚿 Сантехника', '🔧 Обслуживание']
    ]
    update.message.reply_text(
        "⚙️ Выберите тип системы:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return SYSTEM_TYPE

@safe_execute("Ошибка при обработке типа системы")
def system_type(update: Update, context: CallbackContext):
    """Обработка типа системы"""
    if not update or not update.message:
        return SYSTEM_TYPE
    
    context.user_data['system_type'] = update.message.text
    
    update.message.reply_text(
        "📝 Опишите проблему подробно:\n\n*Пример:* Не работает розетка в комнате 101, нет напряжения",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

@safe_execute("Ошибка при обработке проблемы")
def problem(update: Update, context: CallbackContext):
    """Обработка описания проблемы"""
    if not update or not update.message:
        return PROBLEM
    
    problem_text = update.message.text
    
    # Валидация текста проблемы
    is_valid, error_message = EnhancedValidators.validate_problem_text(problem_text)
    if not is_valid:
        update.message.reply_text(f"❌ {error_message}")
        return PROBLEM
    
    # Санитизация ввода
    context.user_data['problem'] = EnhancedValidators.sanitize_input(problem_text)
    
    keyboard = [['🔴 Срочно', '🟡 Средняя', '🟢 Не срочно']]
    update.message.reply_text(
        "⏱️ Выберите срочность заявки:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return URGENCY

@safe_execute("Ошибка при обработке срочности")
def urgency(update: Update, context: CallbackContext):
    """Обработка срочности"""
    if not update or not update.message:
        return URGENCY
    
    # Для срочной помощи автоматически устанавливаем высокий приоритет
    if context.user_data.get('is_emergency'):
        context.user_data['urgency'] = '🔴 КРИТИЧЕСКАЯ'
    else:
        context.user_data['urgency'] = update.message.text
    
    context.user_data['user_id'] = update.message.from_user.id
    context.user_data['username'] = update.message.from_user.username
    context.user_data['first_name'] = update.message.from_user.first_name
    context.user_data['last_name'] = update.message.from_user.last_name
    
    # Показываем подтверждение
    request_text = (
        "📋 *Проверьте данные заявки:*\n\n"
        f"👤 *ФИО:* {context.user_data['name']}\n"
        f"📞 *Телефон:* {context.user_data['phone']}\n"
        f"📍 *Участок:* {context.user_data['plot']}\n"
        f"⚙️ *Система:* {context.user_data['system_type']}\n"
        f"📝 *Проблема:* {context.user_data['problem']}\n"
        f"⏱️ *Срочность:* {context.user_data['urgency']}\n\n"
    )
    
    # Добавляем пометку для срочных заявок
    if context.user_data.get('is_emergency'):
        request_text += "🚨 *ЭКСТРЕННАЯ ЗАЯВКА - ВЫСОКИЙ ПРИОРИТЕТ* 🚨\n\n"
    
    request_text += "Всё верно?"
    
    keyboard = [['✅ Подтвердить отправку', '❌ Отменить']]
    update.message.reply_text(
        request_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return ConversationHandler.END

@safe_execute("Ошибка при подтверждении заявки")
def enhanced_confirm_request(update: Update, context: CallbackContext) -> None:
    """Подтверждение заявки с AI-рекомендациями"""
    if not update or not update.message or not context:
        return
    
    if update.message.text == '✅ Подтвердить отправку':
        user = update.message.from_user
        
        # Проверка наличия базы данных
        if not db:
            update.message.reply_text("❌ Система временно недоступна")
            return
            
        try:
            required_fields = ['name', 'phone', 'plot', 'system_type', 'problem', 'urgency']
            for field in required_fields:
                if field not in context.user_data or not context.user_data[field]:
                    update.message.reply_text(
                        f"❌ Отсутствует обязательное поле: {field}",
                        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
                    )
                    return
            
            # AI анализ проблемы (безопасно)
            problem_text = context.user_data['problem']
            suggested_solution = safe_get_ai_suggestion(problem_text, context.user_data['system_type'])
            context.user_data['ai_suggestion'] = suggested_solution
            
            request_id = db.save_request(context.user_data)
            
            # Начисляем очки за создание заявки
            safe_award_points(user.id, 'create_request')
            
            # Добавляем AI рекомендацию в подтверждение
            confirmation_text = (
                f"✅ *Заявка #{request_id} успешно создана!*\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n"
                f"📍 *Участок:* {context.user_data['plot']}\n"
            )
            
            if 'ai_suggestion' in context.user_data:
                confirmation_text += f"\n💡 *Рекомендация AI:* {context.user_data['ai_suggestion']}\n"
            
            confirmation_text += f"\n_Спасибо за обращение!_ 🛠️"
            
            if user.id in config.admin_chat_ids:
                reply_markup = ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
            else:
                reply_markup = ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
            
            update.message.reply_text(
                confirmation_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Уведомление админам
            admin_message = (
                f"🆕 *Новая заявка #{request_id}*\n\n"
                f"👤 *Клиент:* {context.user_data['name']}\n"
                f"📞 *Телефон:* {context.user_data['phone']}\n"
                f"📍 *Участок:* {context.user_data['plot']}\n"
                f"⚙️ *Система:* {context.user_data['system_type']}\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n"
                f"📝 *Проблема:* {context.user_data['problem'][:100]}...\n"
            )
            
            if 'ai_suggestion' in context.user_data:
                admin_message += f"💡 *AI рекомендация:* {context.user_data['ai_suggestion']}\n"
            
            for admin_id in config.admin_chat_ids:
                try:
                    context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_message,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=create_request_actions_keyboard(request_id)
                    )
                except Exception as e:
                    logging.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
            
            logging.info(f"✅ Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logging.error(f"Ошибка при сохранении заявки: {e}")
            update.message.reply_text(
                "❌ *Произошла ошибка при создании заявки.*\n\nПожалуйста, попробуйте позже.",
                parse_mode=ParseMode.MARKDOWN
            )
        
        # Очищаем флаг срочности после создания заявки
        if 'is_emergency' in context.user_data:
            del context.user_data['is_emergency']
            
        context.user_data.clear()
    else:
        update.message.reply_text(
            "Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

@safe_execute("Ошибка при отмене заявки")
def cancel_request(update: Update, context: CallbackContext):
    """Отмена создания заявки"""
    if not update or not update.message:
        return ConversationHandler.END
        
    # Очищаем флаг срочности при отмене
    if 'is_emergency' in context.user_data:
        del context.user_data['is_emergency']
        
    context.user_data.clear()
    update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
    )
    return ConversationHandler.END

@safe_execute("Ошибка при обработке меню")
def enhanced_handle_main_menu(update: Update, context: CallbackContext):
    """Обработка основного меню пользователя"""
    if not update or not update.message:
        return
    
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
            "📊 *Ваша статистика*\n\n"
            f"📨 Всего заявок: {user_stats.get('total_requests', 0)}\n"
            f"✅ Выполнено: {user_stats.get('completed', 0)}\n"
            f"🔄 В работе: {user_stats.get('in_progress', 0)}\n"
            f"🆕 Новых: {user_stats.get('new', 0)}\n"
        )
        update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
    
    elif text == '🎮 Игровая статистика':
        show_gamification_stats(update, context)
    
    elif text == '📈 Аналитика':
        show_advanced_analytics(update, context)
    
    elif text == '🆘 Срочная помощь':
        emergency_help(update, context)

@safe_execute("Ошибка при обработке админ-меню")
def enhanced_handle_admin_menu(update: Update, context: CallbackContext):
    """Обработка админ-меню"""
    if not update or not update.message:
        return
    
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
            for request in new_requests[:5]:  # Показываем первые 5
                request_text = (
                    f"🆕 *Заявка #{request['id']}*\n\n"
                    f"👤 {request['name']}\n"
                    f"📞 {request['phone']}\n"
                    f"📍 {request['plot']}\n"
                    f"📝 {request['problem'][:100]}...\n"
                )
                update.message.reply_text(
                    request_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=create_request_actions_keyboard(request['id'])
                )
    
    elif text == '📊 Статистика':
        show_statistics(update, context)
    
    elif text == '📈 Аналитика':
        show_advanced_analytics(update, context)
    
    elif text == '🎮 Геймификация':
        show_gamification_stats(update, context)
    
    elif text == '🔄 Обновить':
        show_enhanced_admin_panel(update, context)

@safe_execute("Ошибка при обработке кнопок")
def button_handler(update: Update, context: CallbackContext):
    """Обработчик inline кнопок"""
    if not update or not update.callback_query:
        return
        
    query = update.callback_query
    query.answer()
    
    data = query.data
    
    if data and data.startswith('assign_'):
        request_id = int(data.split('_')[1])
        if db and db.update_request(request_id, {'status': 'in_progress'}):
            query.edit_message_text(f"✅ Заявка #{request_id} взята в работу")
            
            # Находим заявку для уведомления пользователя
            request_data = db.get_request_by_id(request_id)
            if request_data and 'user_id' in request_data:
                try:
                    context.bot.send_message(
                        chat_id=request_data['user_id'],
                        text=f"🔄 Ваша заявка #{request_id} взята в работу! Специалист уже выехал."
                    )
                except Exception as e:
                    logging.error(f"Ошибка уведомления пользователя: {e}")
        else:
            query.edit_message_text(f"❌ Ошибка обновления заявки #{request_id}")
    
    elif data and data.startswith('complete_'):
        request_id = int(data.split('_')[1])
        if db and db.update_request(request_id, {'status': 'completed', 'completed_at': datetime.now().isoformat()}):
            query.edit_message_text(f"✅ Заявка #{request_id} выполнена")
            
            # Уведомляем пользователя
            try:
                request_data = db.get_request_by_id(request_id)
                if request_data and 'user_id' in request_data:
                    context.bot.send_message(
                        chat_id=request_data['user_id'],
                        text=f"✅ Ваша заявка #{request_id} выполнена! Спасибо за обращение!"
                    )
                    # Начисляем очки за выполнение заявки
                    safe_award_points(request_data['user_id'], 'request_completed')
            except Exception as e:
                logging.error(f"Ошибка уведомления пользователя: {e}")
        else:
            query.edit_message_text(f"❌ Ошибка завершения заявки #{request_id}")

@safe_execute("Ошибка при получении заявок")
def show_user_requests(update: Update, context: CallbackContext):
    """Показывает заявки пользователя"""
    if not update or not update.message:
        return
        
    user_id = update.message.from_user.id
    
    try:
        if not db:
            update.message.reply_text("❌ Система временно недоступна")
            return
            
        with sqlite3.connect(config.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, problem, status, created_at, urgency 
                FROM requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT 10
            ''', (user_id,))
            
            requests = cursor.fetchall()
            
        if not requests:
            update.message.reply_text("📭 У вас пока нет заявок")
            return
        
        text = "📋 *Ваши последние заявки:*\n\n"
        for req_id, problem, status, created_at, urgency in requests:
            status_icons = {
                'new': '🆕',
                'in_progress': '🔄', 
                'completed': '✅'
            }
            date = datetime.fromisoformat(created_at).strftime("%d.%m.%Y %H:%M")
            text += f"{status_icons.get(status, '📄')} *Заявка #{req_id}*\n"
            text += f"📝 {problem[:50]}...\n"
            text += f"⏱️ {urgency} | {date}\n"
            text += f"📊 Статус: {status}\n\n"
        
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logging.error(f"Ошибка получения заявок пользователя: {e}")
        update.message.reply_text("❌ Ошибка при получении заявок")

@safe_execute("Ошибка при обработке срочной помощи")
def emergency_help(update: Update, context: CallbackContext):
    """Обработка срочной помощи"""
    if not update or not update.message:
        return ConversationHandler.END
    
    user_id = update.message.from_user.id
    
    # Проверяем лимиты
    allowed, message = rate_limiter.check_rate_limit(user_id, "emergency")
    if not allowed:
        update.message.reply_text(message)
        return ConversationHandler.END
    
    # Сохраняем контекст для срочной заявки
    context.user_data['is_emergency'] = True
    
    update.message.reply_text(
        "🚨 *СРОЧНАЯ ПОМОЩЬ*\n\n"
        "Введите ваше ФИО для экстренной связи:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove()
    )
    
    return NAME

def show_advanced_analytics(update: Update, context: CallbackContext):
    """Показывает расширенную аналитику"""
    if not update or not update.message:
        return
        
    try:
        analytics = safe_get_analytics(30)
        
        text = "📈 *Расширенная аналитика*\n\n"
        text += f"⏱️ *Среднее время выполнения:* {analytics.get('avg_completion_hours', 0)}ч\n\n"
        
        system_distribution = analytics.get('system_distribution', {})
        if system_distribution:
            text += "🔧 *Распределение по системам:*\n"
            for system, count in system_distribution.items():
                text += f"• {system}: {count} заявок\n"
        else:
            text += "📊 Данные о распределении по системам отсутствуют\n"
        
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logging.error(f"Ошибка в show_advanced_analytics: {e}")
        update.message.reply_text("❌ Ошибка при получении аналитики")

def show_gamification_stats(update: Update, context: CallbackContext):
    """Показывает статистику геймификации"""
    if not update or not update.message:
        return
        
    user_id = update.message.from_user.id
    user_stats = gamification_engine.get_user_stats(user_id) if gamification_engine else {'points': 0, 'level': 1}
    
    text = "🎮 *Ваша статистика*\n\n"
    text += f"🏆 Уровень: {user_stats['level']}\n"
    text += f"⭐ Очки: {user_stats['points']}\n\n"
    
    if gamification_engine:
        leaderboard = gamification_engine.get_leaderboard(5)
        if leaderboard:
            text += "🏅 *Топ игроков:*\n"
            for i, (user_id, points, level) in enumerate(leaderboard, 1):
                text += f"{i}. Уровень {level} 1):
                text += f"{i}. Уровень {level} - {points} очков\n"
    
    update.message.reply_text(text, parse_mode=Parse - {points} очков\n"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show_statistics(Mode.MARKDOWN)

def show_statistics(update:update: Update, context: CallbackContext):
    """Показывает статистику"""
 Update, context: CallbackContext):
    """Показывает статистику"""
    if not update or not update.message:
           if not update or not update.message:
        return
        
    stats = db.get_stat return
        
    stats = db.get_statistics(7) if db else {}
istics(7) if db else {}
    
    text = (
        "📊 *Статисти    
    text = (
        "📊 *ка за 7 дней*\n\n"
        f"📨 Всего заявСтатистика за 7 дней*\n\n"
        f"📨 Всего заок: {statsявок: {stats.get('total',.get('total', 0)}\n"
        f"✅ Выполнено: {stats.get('completed', 0)}\n"
        f"✅ Выполнено: {stats.get('completed', 0)}\n"
        f"🔄 В работе: {stats.get(' 0)}\n"
        f"🔄 В работе: {stats.get('in_progress', 0)}\n"
        f"🆕 Новых: {stats.get('newin_progress', 0)}\n"
        f"🆕 Новых: {stats.get('new', 0)}\n"
', 0)}\n"
    )
    
    update.message.reply_text(text    )
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show, parse_mode=ParseMode.MARKDOWN)

def show_enhanced_admin_panel_enhanced_admin_panel(update: Update, context: CallbackContext(update: Update, context: CallbackContext):
   ):
    """Показывает улучшенную админ-панель"""
    if not update or not """Показывает улучшенную админ-панель"""
    if not update or not update.message:
        return
        
 update.message:
        return
        
    user_id = update.message.from_user.id
    if    user_id = update.message.from_user.id
    if user_id not in config.admin_chat_ids:
 user_id not in config.admin_chat_ids:
        update.message.reply_text        update.message.reply_text("❌ У("❌ У вас нет доступа к этой команде.")
        return
    
    reply_markup = ReplyKeyboard вас нет доступа к этой команде.")
        return
    
    reply_markup = ReplyKeyboardMarkup(get_enhanced_admin_panel(),Markup(get_enhanced_admin_panel(), resize_keyboard=True)
    update.message resize_keyboard=True)
    update.message.reply_text.reply_text(
        "👑 *Панель администратора*\n\nВыберите действие:",
(
        "👑 *Панель администратора*\n\nВыберите        reply_markup=reply_mark действие:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKup,
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ЗАDOWN
    )

# ==================== ЗАДАНИЯ ПО РАСПИСАДАНИЯ ПО РАСПИСАНИЮ ===================НИЮ ====================
def backup_job(context: CallbackContext):
    """Задание для=
def backup_job(context: CallbackContext):
    """Задание для автоматического бэкапа"""
    backup_path = BackupManager.create_backup()
    автоматического бэкапа"""
    backup_path = BackupManager.create_backup()
    if backup_path:
 if backup_path:
        logging.info(f"✅ Автоматический бэка        logging.info(f"✅ Автоматический бэкап создан: {backup_path}")
        
        # Уведомлениеп создан: {backup_path}")
        
        # Уведомление админам
        for admin_id in config.admin_chat_ids:
 админам
        for admin_id in config.admin_chat_ids:
            try:
                context.b            try:
                context.bot.send_message(
                    admin_id,
                    f"✅ Автоматический бэкап созданot.send_message(
                    admin_id,
                    f"✅ Автоматический бэкап создан: `{os.path.basename(backup_path: `{os.path.basename(backup_path)}`",
                    parse_mode)}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception=ParseMode.MARKDOWN
                )
            except Exception as e:
                logging as e:
                logging.error(f"Ошибка отправки уведом.error(f"Ошибка отправки уведомления о бэкапеления о бэкапе: {e}")
    else:
        logging.error("❌ Ошибка: {e}")
    else:
        logging.error("❌ Ошибка автоматического бэкапа")

def check_urgent_requests(context: CallbackContext):
 автоматического бэкапа")

def check_urgent_requests(context: CallbackContext):
    """Проверяет срочные заявки"""
    try    """Проверяет срочные заявки"""
    try:
        urgent_requests = db.get_:
        urgent_requests = db.get_urgent_requests() if db else []
        if urgent_urgent_requests() if db else []
        if urgent_requests:
            for admin_id in config.admin_chat_ids:
requests:
            for admin_id in config.admin_chat_ids:
                try:
                try:
                    context.bot.send_message(
                        admin_id,
                                           context.bot.send_message(
                        admin_id,
                        f"🔴 f"🔴 Внима Внимание! Есть {len(urgent_requests)} срочных заявок, требующих обработкиние! Есть {len(urgent_requests)} срочных заявок, требующих обработ",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    loggingки",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logging.error(f"Ошибка отправки уведомления о срочных за.error(f"Ошибка отправки уведомления о срочных заявках: {e}")
явках: {e}")
    except Exception as e:
        logging.error(f"Ошибка проверки срочных заявок: {e}")

def error    except Exception as e:
        logging.error(f"Ошибка проверки срочных заявок: {e}")

def error_handler(update: Update, context: CallbackContext):
    """Обра_handler(update: Update, context: CallbackContext):
    """Обработчик ошибок"""
    logging.error(f"Ошибка:ботчик ошибок"""
    logging.error(f"Ошибка: {context.error}", {context.error}", exc_info=context.error)
    
    if update and update.message:
        update.message.reply_text(
            exc_info=context.error)
    
    if update and update.message:
        update.message.reply_text(
            "❌ Произошла ошибка. Пожа "❌ Произошла ошибка. Пожалуйста, попробуйте позже.",
            replyлуйста, попробуйте позже.",
            reply_markup=ReplyKeyboard_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

# ==================== ИНИboard=True)
        )

# ==================== ИНИЦИАЦИАЛИЗАЦИЯ СИСТЕМ ====================
ЛИЗАЦИЯ СИСТЕМ ====================
def initializedef initialize_basic_systems():
    """Базовая инициализация систем"""
    global db, rate_basic_systems():
    """Базовая инициализация систем"""
   _limiter, security_manager, analytics_engine, ai_assistant, gamification_engine
    
    try:
 global db, rate_limiter, security_manager, analytics_engine, ai_assistant, gamification_engine
    
    try:
        # Инициализация базы        # Инициализация базы данных
        db = Database(config.db данных
        db = Database(config.db_path)
        
        # Базовые системы
        rate_limiter =_path)
        
        # Базовые системы
        rate_limiter = EnhancedRateLimiter()
        security_manager EnhancedRateLimiter()
        security_manager = SecurityManager()
        
        # AnalyticsEngine требует = SecurityManager()
        
        # AnalyticsEngine требует db_path db_path из config
        analytics_engine из config
        analytics_engine = AnalyticsEngine()
        
        # AI помощник (если есть API ключ)
        if config = AnalyticsEngine()
        
        # AI помощник (если есть API ключ)
       .openai_api_key and config.openai_api_key != "your_openai_api_key":
            ai_assistant = AIAssistant(config.openai if config.openai_api_key and config.openai_api_key != "your_openai_api_key":
            ai_assistant = AIAssistant(config.openai_api_key)
            logging.info("_api_key)
            logging.info("✅ AI помощник инициализирован")
        else:
            ai_assistant✅ AI помощник инициализирован")
        else:
            ai_assistant = None
            logging.info = None
            logging.info("("❌ AI помощник отключен (нет API ключа)")
        
        #❌ AI помощник отключен (нет API ключа)")
        
        # Г Геймификация
        gamification_engine = GamificationEngine(config.db_path)
        
        logging.info("✅ Все системы инициализиеймификация
        gamification_engine = GamificationEngine(config.db_path)
        
        logging.info("✅ Все системы инициализированы")
        return True
        
    exceptрованы")
        return True
        
    except Exception as e:
        logging.error Exception as e:
        logging.error(f"❌ Ошибка инициализации систем: {e}")
        return False

# ====================(f"❌ Ошибка инициализации систем: {e}")
        return False

# ==================== ЗАПУСК БОТА ====================
def main() -> None:
    """Основная функция ЗАПУСК БОТА ====================
def main() -> None:
    """Основная функция запуска бота"""
    # На запуска бота"""
    # Настройка логирования
    logging.bстройка логирования
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %asicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
        level=logging.INFO,
       (levelname)s - %(message)s', 
        level=logging.INFO,
        handlers=[
            logging.FileHandler('bot.log handlers=[
            logging.FileHandler('bot.log', encoding='utf-8'),
           ', encoding='utf-8'),
            logging.StreamHandler logging.StreamHandler()
        ]
    )
    
    logging.info("=" * 50)
    logging.info("()
        ]
    )
    
    logging.info("=" * 50)
    logging.info("🤖 ЗАПУСК СИ🤖 ЗАПУСК СИСТЕМЫ УПРАВЛЕНИСТЕМЫ УПРАВЛЕНИЯ ЗАЯВКАЯ ЗАЯВКАМИ")
    logging.info("=" * 50)
    
   МИ")
    logging.info("=" * 50)
    
    if not config.validate():
        logging.error("❌ if not config.validate():
        logging.error("❌ Неверная конфигурация бота!")
        Неверная конфигурация бота!")
        return
    
    if not TELEGRAM_AVAILABLE:
        logging.error return
    
    if not TELEGRAM_AVAILABLE:
        logging.error("❌ python-telegram-bot не установлен!")
       ("❌ python-telegram-bot не установлен!")
        return
    
    # Инициализация базовых систем
 return
    
    # Инициализация базовых систем
    if not initialize_basic_systems():
           if not initialize_basic_systems():
        logging.error logging.error("❌ Не удалось инициализировать базовые системы!")
        return
    
    try("❌ Не удалось инициализировать базовые системы!")
        return
    
    try:
        updater = Updater(config.bot:
        updater = Updater(config.bot_token)
        dispatcher = updater.dispatcher

        # О_token)
        dispatcher = updater.dispatcherбработчик ошибок
        dispatcher.add_error_handler(error_handler)

       

        # Обработчик ошибок
        dispatcher.add_error_handler(error_handler)

        # Задания по расписанию
        job_queue = updater.job # Задания по расписанию
        job_queue = updater.job_queue
        if_queue
        if job_queue:
            try:
                # Ежедневное резервное копирование job_queue:
            try:
                # Ежедневное резервное копирование
                from datetime import time
                from datetime import time as dt_time
                backup_time = dt_time as dt_time
                backup_time = dt_time(hour=config.auto_backup_hour, minute(hour=config.auto_backup_hour, minute=config.auto_backup_minute)
               =config.auto_backup_minute)
                job_queue.run_daily(backup_job, time= job_queue.run_daily(backup_jobbackup_time)
                
                # Ежечасная проверка срочных заявок, time=backup_time)
                
                # Ежечасная проверка срочных заявок
                job_queue.run_repeating(check_urgent_requests
                job_queue.run_repeating(check_urgent_requests, interval=, interval=3600, first=10)
                
                logging.info("✅ Задания планировщика зарегистрированы")
                
3600, first=10)
                
                logging.info("✅ Задания планировщика зарегистрированы")
                
            except Exception as e:
                logging.error(f"            except Exception as e:
                logging.error(f"❌ Ошибка регистрации заданий планировщика: {e}")

❌ Ошибка регистрации заданий планировщика: {e}")

        # Обработчик создания за        # Обработчик создания заявки
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.reявки
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(📝 Создать заявкуgex('^(📝 Создать заявку)$'), enhanced_start_request_creation)$'), enhanced_start_request_creation),
                MessageHandler(Filters.regex('^(),
                MessageHandler(Filters.regex('^(🆘 Срочная помощь)$'), emergency🆘 Срочная помощь)$'), emergency_help),
            ],
            states={
                NAME_help),
            ],
            states={
                NAME: [MessageHandler(Filters.text & ~Filters.command,: [MessageHandler(Filters.text & ~Filters.command, name)],
                PHONE: [MessageHandler(Filters.text & ~ name)],
                PHONE: [MessageHandler(Filters.text & ~Filters.command, phone)],
                PLOTFilters.command, phone)],
                PLOT: [MessageHandler(Filters.text &: [MessageHandler(F ~Filters.command, plot)],
                SYSTEM_TYPE: [MessageHandler(Filters.text & ~Filters.command, system_typeilters.text & ~Filters.command, plot)],
                SYSTEM_TYPE: [MessageHandler(Filters.text & ~Filters.command, system_type)],
)],
                PROBLEM: [MessageHandler(Filters.text & ~Filters.command, problem)],
                URGENCY: [MessageHandler(F                PROBLEM: [MessageHandler(Filters.text & ~Filters.command, problem)],
                URGENCY:ilters.text & ~Filters.command, urgency)],
            },
            fallbacks=[
                CommandHandler [MessageHandler(Filters.text & ~Filters.command, urgency)],
            },
            fallbacks=[
                CommandHandler('cancel('cancel', cancel_request),
                MessageHandler(Filters.regex('^(❌ Отменить)$'), cancel', cancel_request),
                MessageHandler(Filters.regex('^(❌ Отменить)$'), cancel_request),
            ],
        )

        # Регистрируем обработчики_request),
            ],
        )

        # Регистрируем обработчики
        dispatcher
        dispatcher.add_handler.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_enhanced_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_enhanced_admin_panel))
        dispatcher.add_handler(CommandHandler('stats', show_statistics))
       _admin_panel))
        dispatcher.add_handler(CommandHandler('stats', show_statistics))
        dispatcher.add_handler(CommandHandler('analytics dispatcher.add_handler(CommandHandler('analytics', show_advanced_analytics))
        dispatcher.add', show_advanced_analytics))
        dispatcher_handler(CommandHandler('gamification', show_gamification_stats))
        dispatcher.add_handler(CommandHandler('gamification', show_gamification_stats))
        dispatcher.add_handler(CommandHandler('cancel', cancel_request))
        
        dispatcher.add_handler.add_handler(CommandHandler('cancel', cancel_request))
        
        dispatcher.add_handler(conv_handler)
        
        # Обработчики сообщений
(conv_handler)
        
        # Обработчики сообщений
        dispatcher.add_handler(Message        dispatcher.add_handler(MessageHandler(
            FiltersHandler(
            Filters.regex('^(✅ Подтвердить отправку)$'), 
            enhanced_confirm_request
       .regex('^(✅ Подтвердить отправку)$'), 
            enhanced_confirm_request
        ))
        
        dispatcher.add_handler(MessageHandler(
            ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📝 Создать заяв Filters.regex('^(📝 Создать заявку|📋ку|📋 Мои заявки|📊 Моя статисти Мои заявки|📊 Моя статистика|🆘 Ска|🆘 Срочная помощь|🎮 Игровая статистика|📈 Аналитика|ℹ️ О боте|⚙️ Настройки)$'), 
рочная помощь|🎮 Игровая статистика|📈 Аналитика|ℹ️ О боте|⚙️ Настройки)$'), 
            enhanced_handle_main_menu
        ))
        
        dispatcher.add            enhanced_handle_main_menu
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex_handler(MessageHandler(
            Filters.regex('^(🆕 Новые|🔄 В('^(🆕 Новые|🔄 В работе|⏰ Срочные| работе|⏰ Срочные|🚨🚨 Зави Зависшие|📊 Статистика|📈 Аналитика|👥 Пользователи|⚙️ Настройки|💾 Бсшие|📊 Статистика|📈 Аналитика|👥 Пользователи|⚙️ Настройки|💾 Бэкапы|экапы|🔄 Обновить|📊 Google Sheets|🔄 Синхронизация|🔄 Обновить|📊 Google Sheets|🔄 Синхронизация|🎮 Геймификация|📊 Метрики)$'), 
            enhanced_handle_admin_menu
        ))
        
🎮 Геймификация|📊 Метрики)$'), 
            enhanced_handle_admin_menu
        ))
        
        # Обработчики inline кнопок
        # Обработчики inline кнопок
        dispatcher        dispatcher.add_handler(CallbackQueryHandler(button_handler))

        # Запускаем бота
        logging.add_handler(CallbackQueryHandler(button_handler))

        # Запускаем бота
        logging.info("🤖 Бот запущен!")
        logging.info(f"📍.info("🤖 Бот запущен!")
        logging.info(f"📍 База данных База данных: {: {config.db_path}")
        logging.info(f"👑 Администраторы: {len(config.admin_chat_ids)}")
        logging.info(f"📊 Лимит запросов: {config.max_requests_per_hour}/часconfig.db_path}")
        logging.info(f"👑 Администраторы: {len(config.admin_chat_ids)}")
        logging.info(f"📊 Лимит запросов: {config.max_requests_per_hour}/час")
        logging.info(f")
        logging.info(f"🤖 AI помощник: {'✅ Активен' if ai_assistant else '❌ Отключен'}")
"🤖 AI помощник: {'✅ Активен' if ai_assistant else '❌ Отключен'        logging.info(f"🎮 Геймификация: {'✅ Активна' if gamification_engine else '❌ Отключена'}")
        logging.info(f}")
        logging.info(f"🎮 Геймификация: {'✅ Активна' if gamification_engine else '❌ Отключена'}")
        logging"📈 Google Sheets: {'✅ Активна' if config.sync_to_sheets else '❌ Отключена'}")
        logging.info("=" * 50.info(f"📈 Google Sheets: {'✅ Активна' if config.sync_to_sheets else '❌ Отключена'}")
        logging.info("=" *)
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logging.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
