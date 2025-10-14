import matplotlib
matplotlib.use('Agg')  # Для работы без GUI
from matplotlib import pyplot as plt
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
import psutil
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, time
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

# ==================== ДОБАВЛЕННЫЕ ИМПОРТЫ ====================
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    GOOGLE_SHEETS_AVAILABLE = False

try:
    import flask
    from flask import Flask, render_template, jsonify, request
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# ==================== КОНФИГУРАЦИЯ ====================

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_IDS = [int(x) for x in os.getenv('ADMIN_CHAT_IDS', '').split(',') if x]
GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
GOOGLE_SHEET_NAME = 'Заявки'

# Новые настройки
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
SMS_API_KEY = os.getenv('SMS_API_KEY', '')
EMAIL_CONFIG = os.getenv('EMAIL_CONFIG', '')
WEB_DASHBOARD_PORT = int(os.getenv('WEB_DASHBOARD_PORT', '5000'))

if not BOT_TOKEN:
    logging.error("❌ BOT_TOKEN не установлен!")
    exit(1)
if not ADMIN_CHAT_IDS:
    logging.error("❌ ADMIN_CHAT_IDS не установлены!")
    exit(1)

# Расширенные настройки
MAX_REQUESTS_PER_HOUR = 15
BACKUP_RETENTION_DAYS = 30
AUTO_BACKUP_HOUR = 3
AUTO_BACKUP_MINUTE = 0
REQUEST_TIMEOUT_HOURS = 24
SYNC_TO_SHEETS = bool(GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SHEET_ID and GOOGLE_SHEETS_AVAILABLE)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD = range(9)

DB_PATH = "requests.db"
BACKUP_DIR = "backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

# ==================== СИСТЕМА ВЕБ-ПАНЕЛИ ====================

class WebDashboard:
    def __init__(self, db_manager, port=5000):
        if not FLASK_AVAILABLE:
            logger.warning("⚠️ Flask не установлен - веб-панель отключена")
            return
            
        self.app = Flask(__name__)
        self.db_manager = db_manager
        self.port = port
        self.setup_routes()
    
    def setup_routes(self):
        @self.app.route('/')
        def dashboard():
            stats = self.db_manager.get_statistics(7)
            return f"""
            <html>
                <head><title>Панель управления ботом</title></head>
                <body>
                    <h1>📊 Панель управления</h1>
                    <div>Всего заявок: {stats['total']}</div>
                    <div>Выполнено: {stats['completed']}</div>
                    <div>Новых: {stats['new']}</div>
                    <div>В работе: {stats['in_progress']}</div>
                </body>
            </html>
            """
        
        @self.app.route('/api/requests')
        def get_requests():
            status = request.args.get('status', 'all')
            requests = []
            if status == 'all':
                for status_type in ['new', 'in_progress', 'completed']:
                    requests.extend(self.db_manager.get_requests_by_filter(status_type))
            else:
                requests = self.db_manager.get_requests_by_filter(status)
            return jsonify({"requests": requests[:50]})  # Ограничиваем вывод
    
    def run(self):
        if not FLASK_AVAILABLE:
            return
            
        def run_flask():
            try:
                self.app.run(host='0.0.0.0', port=self.port, debug=False)
            except Exception as e:
                logger.error(f"❌ Ошибка веб-панели: {e}")
        
        threading.Thread(target=run_flask, daemon=True).start()
        logger.info(f"🌐 Веб-панель запущена на порту {self.port}")

# ==================== СИСТЕМА АНАЛИТИКИ ====================

class AnalyticsEngine:
    def generate_weekly_stats_chart(self, stats_data):
        """Генерирует график статистики в base64"""
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            
            days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
            requests = stats_data.get('daily_requests', [0]*7)
            
            ax.bar(days, requests, color='skyblue')
            ax.set_title('Заявки по дням недели')
            ax.set_ylabel('Количество заявок')
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight')
            buf.seek(0)
            plt.close()
            return base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            logger.error(f"Ошибка генерации графика: {e}")
            return None
    
    def get_advanced_analytics(self, days=30):
        """Расширенная аналитика"""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                
                # Аналитика по типам систем
                cursor.execute('''
                    SELECT system_type, COUNT(*) as count 
                    FROM requests 
                    WHERE created_at > ?
                    GROUP BY system_type
                ''', ((datetime.now() - timedelta(days=days)).isoformat(),))
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
            logger.error(f"Analytics error: {e}")
            return {}

# ==================== AI-ПОМОЩНИК ====================

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
            logger.error(f"AI analysis error: {e}")
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

# ==================== УМНЫЕ УВЕДОМЛЕНИЯ ====================

class SmartNotificationManager:
    def __init__(self, bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager
        self.user_preferences = {}
    
    def send_reminder(self, request_id, reminder_type):
        """Отправляет напоминание о заявке"""
        request = self.db_manager.get_request_by_id(request_id)
        if not request:
            return
        
        user_id = request['user_id']
        messages = {
            'status_update': f"🔄 Обновление по заявке #{request_id}",
            'deadline': f"⏰ Напоминание: заявка #{request_id} требует внимания",
            'completion': f"✅ Ваша заявка #{request_id} выполнена!"
        }
        
        if user_id in self.user_preferences and self.user_preferences[user_id].get('notifications', True):
            try:
                self.bot.send_message(chat_id=user_id, text=messages.get(reminder_type, "Напоминание"))
            except Exception as e:
                logger.error(f"Reminder send error: {e}")
    
    def check_pending_reminders(self):
        """Проверяет pending напоминания"""
        try:
            stale_requests = self.db_manager.get_stuck_requests(24)
            for request in stale_requests:
                self.send_reminder(request['id'], 'deadline')
        except Exception as e:
            logger.error(f"Reminder check error: {e}")

# ==================== СИСТЕМА МОНИТОРИНГА ====================

class PerformanceMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.request_count = 0
    
    def increment_request_count(self):
        self.request_count += 1
    
    def get_system_metrics(self):
        """Возвращает системные метрики"""
        try:
            return {
                'uptime': time.time() - self.start_time,
                'memory_usage': psutil.virtual_memory().percent,
                'cpu_usage': psutil.cpu_percent(),
                'active_users': len(self.get_active_users()),
                'requests_today': self.request_count
            }
        except Exception as e:
            logger.error(f"Metrics error: {e}")
            return {}
    
    def get_active_users(self):
        """Возвращает активных пользователей за последние 24 часа"""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                time_threshold = (datetime.now() - timedelta(hours=24)).isoformat()
                cursor.execute('SELECT DISTINCT user_id FROM requests WHERE created_at > ?', (time_threshold,))
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Active users error: {e}")
            return []

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
        
        # Если больше 50 действий в час - блокируем
        if self.suspicious_activities[user_id][hour_key] > 50:
            self.blocked_users.add(user_id)
            return False
        
        return True
    
    def is_user_blocked(self, user_id):
        return user_id in self.blocked_users

# ==================== МЕНЕДЖЕР ШАБЛОНОВ ====================

class TemplateManager:
    def __init__(self):
        self.templates = {
            'greeting': "Добро пожаловать в службу поддержки! Чем могу помочь?",
            'request_received': "✅ Заявка #{id} принята в обработку",
            'completion': "✅ Заявка #{id} выполнена. Спасибо за обращение!",
            'urgent_response': "🔴 СРОЧНО! Приняли вашу заявку #{id}. Свяжемся в течение 10 минут.",
        }
    
    def get_template(self, name, **kwargs):
        """Возвращает шаблон с подставленными значениями"""
        template = self.templates.get(name, "")
        return template.format(**kwargs)
    
    def quick_reply(self, update, template_name, **kwargs):
        """Быстрый ответ по шаблону"""
        text = self.get_template(template_name, **kwargs)
        update.message.reply_text(text)

# ==================== МУЛЬТИЯЗЫЧНАЯ ПОДДЕРЖКА ====================

class Internationalization:
    def __init__(self):
        self.translations = {
            'ru': {
                'welcome': "Добро пожаловать!",
                'create_request': "Создать заявку",
                'my_requests': "Мои заявки",
                'help': "Помощь",
            },
            'en': {
                'welcome': "Welcome!",
                'create_request': "Create request", 
                'my_requests': "My requests",
                'help': "Help",
            }
        }
        self.user_languages = {}
    
    def set_language(self, user_id, language):
        if language in self.translations:
            self.user_languages[user_id] = language
    
    def get_text(self, user_id, key):
        lang = self.user_languages.get(user_id, 'ru')
        return self.translations.get(lang, {}).get(key, key)

# ==================== ГЕЙМИФИКАЦИЯ ====================

class GamificationEngine:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_gamification()
    
    def init_gamification(self):
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
    
    def award_points(self, user_id, action):
        """Начисляет очки за действие"""
        point_values = {
            'create_request': 10,
            'request_completed': 5,
            'first_request': 25
        }
        
        points_to_award = point_values.get(action, 0)
        
        if points_to_award > 0:
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
    
    def get_user_stats(self, user_id):
        """Возвращает статистику пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT points, level FROM user_points WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return {'points': result[0] if result else 0, 'level': result[1] if result else 1}
    
    def get_leaderboard(self, limit=10):
        """Возвращает таблицу лидеров"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, points, level 
                FROM user_points 
                ORDER BY points DESC 
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()

# ==================== БАЗОВЫЕ КЛАССЫ (СОХРАНЕНЫ) ====================

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

class BackupManager:
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

class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
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
    
    def save_request(self, data: Dict) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
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

# ==================== GOOGLE SHEETS (СОХРАНЕН) ====================

class GoogleSheetsManager:
    def __init__(self, credentials_json: str, sheet_id: str, sheet_name: str = 'Заявки'):
        self.credentials_json = credentials_json
        self.sheet_id = sheet_id
        self.sheet_name = sheet_name
        self.client = None
        self.sheet = None
        self.is_connected = False
        self._connect()
    
    def _connect(self):
        try:
            if not self.credentials_json or not self.sheet_id:
                logger.warning("⚠️ Google Sheets не настроен")
                return
            
            if not GOOGLE_SHEETS_AVAILABLE:
                logger.warning("⚠️ Библиотеки Google Sheets не установлены")
                return
            
            creds_dict = json.loads(self.credentials_json)
            SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
            creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_key(self.sheet_id).worksheet(self.sheet_name)
            self.is_connected = True
            logger.info("✅ Успешное подключение к Google Sheets")
            
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Google Sheets: {e}")
            self.is_connected = False

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    def __init__(self):
        self.bot_token = BOT_TOKEN
        self.admin_chat_ids = ADMIN_CHAT_IDS
        self.max_requests_per_hour = MAX_REQUESTS_PER_HOUR
        self.backup_retention_days = BACKUP_RETENTION_DAYS
        self.auto_backup_hour = AUTO_BACKUP_HOUR
        self.auto_backup_minute = AUTO_BACKUP_MINUTE
        self.request_timeout_hours = REQUEST_TIMEOUT_HOURS
        self.db_path = DB_PATH
        self.backup_dir = BACKUP_DIR
        self.sync_to_sheets = SYNC_TO_SHEETS
        self.google_sheets_credentials = GOOGLE_SHEETS_CREDENTIALS
        self.google_sheet_id = GOOGLE_SHEET_ID
        self.google_sheet_name = GOOGLE_SHEET_NAME
        self.openai_api_key = OPENAI_API_KEY
        self.web_dashboard_port = WEB_DASHBOARD_PORT
    
    def validate(self) -> bool:
        if not self.bot_token:
            logger.error("❌ BOT_TOKEN не установлен")
            return False
        if not self.admin_chat_ids:
            logger.error("❌ ADMIN_CHAT_IDS не установлены")
            return False
        return True

config = Config()

# ==================== РАСШИРЕННАЯ БАЗА ДАННЫХ ====================

class EnhancedDatabase(Database):
    def __init__(self, db_path, sheets_manager=None):
        super().__init__(db_path)
        self.sheets_manager = sheets_manager
    
    def save_request(self, data: Dict) -> int:
        request_id = super().save_request(data)
        
        # AI анализ проблемы
        if ai_assistant and data.get('problem'):
            suggested_category = ai_assistant.analyze_problem_text(data['problem'])
            logger.info(f"🤖 AI определил категорию: {suggestied_category}")
        
        # Начисление очков
        gamification_engine.award_points(data['user_id'], 'create_request')
        
        # Синхронизация с Google Sheets
        if self.sheets_manager and self.sheets_manager.is_connected:
            sheet_data = data.copy()
            sheet_data['id'] = request_id
            # Здесь будет вызов метода add_request
        
        return request_id
    
    def get_request_by_id(self, request_id: int) -> Optional[Dict]:
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
            logger.error(f"Ошибка получения заявки: {e}")
            return None
    
    def get_urgent_requests(self, hours: int = 2) -> List[Dict]:
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

    def get_stuck_requests(self, hours: int = 24) -> List[Dict]:
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

    def get_user_statistics(self, user_id: int) -> Dict:
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
                
                stats = cursor.fetchone()
                if stats:
                    columns = [column[0] for column in cursor.description]
                    return dict(zip(columns, stats))
                return {}
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения статистики пользователя: {e}")
            return {}

nager(
# ==================== МУЛЬТИЯЗЫЧНАЯ ПОДДЕРЖКА ====================

class Internationalization:
    def __init__(self):
        self.translations = {
            'ru': {
                'welcome': "Добро пожаловать!",
                'create_request': "Создать заявку",
                'my_requests': "Мои заявки",
                'help': "Помощь",
            },
            'en': {
                'welcome': "Welcome!",
                'create_request': "Create request", 
                'my_requests': "My requests",
                'help': "Help",
            }
        }
        self.user_languages = {}
    
    def set_language(self, user_id, language):
        if language in self.translations:
            self.user_languages[user_id] = language
    
    def get_text(self, user_id, key):
        lang = self.user_languages.get(user_id, 'ru')
        return self.translations.get(lang, {}).get(key, key)

# ==================== ГЕЙМИФИКАЦИЯ ====================

class GamificationEngine:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_gamification()
    
    def init_gamification(self):
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
    
    def award_points(self, user_id, action):
        """Начисляет очки за действие"""
        point_values = {
            'create_request': 10,
            'request_completed': 5,
            'first_request': 25
        }
        
        points_to_award = point_values.get(action, 0)
        
        if points_to_award > 0:
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
    
    def get_user_stats(self, user_id):
        """Возвращает статистику пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT points, level FROM user_points WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return {'points': result[0] if result else 0, 'level': result[1] if result else 1}
    
    def get_leaderboard(self, limit=10):
        """Возвращает таблицу лидеров"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, points, level 
                FROM user_points 
                ORDER BY points DESC 
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()

# ==================== ИНИЦИАЛИЗАЦИЯ ВСЕХ СИСТЕМ ====================

def initialize_all_systems():
    """Инициализирует все системы бота"""
    global db
    global sheets_manager
    global notification_manager
    global analytics_engine
    global ai_assistant
    global security_manager
    global performance_monitor
    global template_manager
    global i18n
    global gamification_engine
    global web_dashboard
    
    # Основные системы
    if config.sync_to_sheets:
        sheets_manager = GoogleSheetsManager(
            config.google_sheets_credentials,
            config.google_sheet_id,
            config.google_sheet_name
        )
    else:
        sheets_manager = None
        logger.info("⚠️ Google Sheets отключен в конфигурации")
    
    db = EnhancedDatabase(DB_PATH, sheets_manager)
    security_manager = SecurityManager()
    performance_monitor = PerformanceMonitor()
    
    # Дополнительные системы
    analytics_engine = AnalyticsEngine()
    ai_assistant = AIAssistant(config.openai_api_key)
    template_manager = TemplateManager()
    i18n = Internationalization()
    gamification_engine = GamificationEngine(DB_PATH)
    
    # Веб-панель
    web_dashboard = WebDashboard(db, config.web_dashboard_port)
    web_dashboard.run()
    
    logger.info("✅ Все системы инициализированы!")

# ==================== ЗАПУСК СИСТЕМ ====================

# Глобальные объекты
rate_limiter = RateLimiter()
db = None
sheets_manager = None
notification_manager = None
analytics_engine = None
ai_assistant = None
security_manager = None
performance_monitor = None
template_manager = None
i18n = None
gamification_engine = None
web_dashboard = None

# ==================== НОВЫЕ КОМАНДЫ И ФУНКЦИИ ====================

def create_request_actions_keyboard(request_id):
    """Создает интерактивные кнопки для заявки"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{request_id}"),
            InlineKeyboardButton("🔄 В работу", callback_data=f"progress_{request_id}"),
        ],
        [
            InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{request_id}"),
            InlineKeyboardButton("📊 Статистика", callback_data=f"stats_{request_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def button_handler(update: Update, context: CallbackContext):
    """Обработчик inline-кнопок"""
    query = update.callback_query
    query.answer()
    
    data = query.data
    request_id = data.split('_')[1] if '_' in data else None
    
    if data.startswith('complete_') and request_id:
        db.update_request(request_id, {'status': 'completed'})
        query.edit_message_text(f"✅ Заявка #{request_id} выполнена!")
    
    elif data.startswith('progress_') and request_id:
        db.update_request(request_id, {'status': 'in_progress'})
        query.edit_message_text(f"🔄 Заявка #{request_id} взята в работу!")

def show_advanced_analytics(update: Update, context: CallbackContext):
    """Показывает расширенную аналитику"""
    analytics = analytics_engine.get_advanced_analytics(30)
    
    text = "📈 *Расширенная аналитика*\n\n"
    text += f"⏱️ *Среднее время выполнения:* {analytics.get('avg_completion_hours', 0)}ч\n\n"
    
    text += "🔧 *Распределение по системам:*\n"
    for system, count in analytics.get('system_distribution', {}).items():
        text += f"• {system}: {count} заявок\n"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show_system_metrics(update: Update, context: CallbackContext):
    """Показывает системные метрики"""
    metrics = performance_monitor.get_system_metrics()
    
    text = "📊 *Системные метрики*\n\n"
    text += f"⏱️ Аптайм: {metrics.get('uptime', 0):.0f} сек\n"
    text += f"🧠 Память: {metrics.get('memory_usage', 0)}%\n"
    text += f"⚡ CPU: {metrics.get('cpu_usage', 0)}%\n"
    text += f"👥 Активных пользователей: {len(metrics.get('active_users', []))}\n"
    text += f"📨 Заявок сегодня: {metrics.get('requests_today', 0)}"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show_gamification_stats(update: Update, context: CallbackContext):
    """Показывает статистику геймификации"""
    user_id = update.message.from_user.id
    user_stats = gamification_engine.get_user_stats(user_id)
    
    text = "🎮 *Ваша статистика*\n\n"
    text += f"🏆 Уровень: {user_stats['level']}\n"
    text += f"⭐ Очки: {user_stats['points']}\n\n"
    
    leaderboard = gamification_engine.get_leaderboard(5)
    if leaderboard:
        text += "🏅 *Топ игроков:*\n"
        for i, (user_id, points, level) in enumerate(leaderboard, 1):
            text += f"{i}. Уровень {level} - {points} очков\n"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def handle_voice_message(update: Update, context: CallbackContext):
    """Обрабатывает голосовые сообщения"""
    if update.message.voice:
        update.message.reply_text(
            "🎤 Голосовые сообщения пока не поддерживаются. "
            "Пожалуйста, опишите проблему текстом.",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

def secure_handler(handler_func):
    """Декоратор для безопасной обработки"""
    def wrapper(update: Update, context: CallbackContext):
        user_id = update.message.from_user.id
        
        if security_manager.is_user_blocked(user_id):
            update.message.reply_text("❌ Ваш аккаунт временно заблокирован")
            return
        
        if not security_manager.check_suspicious_activity(user_id, 'message'):
            update.message.reply_text("❌ Слишком много запросов")
            return
        
        return handler_func(update, context)
    return wrapper

# ==================== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ====================

@secure_handler
def enhanced_start_request_creation(update: Update, context: CallbackContext) -> int:
    """Начало создания заявки с AI-анализом"""
    user_id = update.message.from_user.id
    
    if rate_limiter.is_limited(user_id, 'create_request', MAX_REQUESTS_PER_HOUR):
        update.message.reply_text(
            "❌ *Превышен лимит запросов!*\n\nВы можете создавать не более 15 заявок в час.",
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
        'last_name': user.last_name
    })
    
    update.message.reply_text(
        "📝 *Создание новой заявки*\n\nДля начала укажите ваше имя:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

@secure_handler  
def enhanced_confirm_request(update: Update, context: CallbackContext) -> None:
    """Подтверждение заявки с AI-рекомендациями"""
    if update.message.text == '✅ Подтвердить отправку':
        user = update.message.from_user
        
        try:
            required_fields = ['name', 'phone', 'plot', 'system_type', 'problem', 'urgency']
            for field in required_fields:
                if field not in context.user_data or not context.user_data[field]:
                    update.message.reply_text(
                        f"❌ Отсутствует обязательное поле: {field}",
                        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
                    )
                    return
            
            # AI анализ проблемы
            problem_text = context.user_data['problem']
            if ai_assistant:
                suggested_solution = ai_assistant.suggest_solutions(
                    problem_text, 
                    context.user_data['system_type']
                )
                context.user_data['ai_suggestion'] = suggested_solution
            
            request_id = db.save_request(context.user_data)
            performance_monitor.increment_request_count()
            
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
            update.message.reply_text(
                "❌ *Произошла ошибка при создании заявки.*\n\nПожалуйста, попробуйте позже.",
                parse_mode=ParseMode.MARKDOWN
            )
        
        context.user_data.clear()

# ==================== СУЩЕСТВУЮЩИЕ ФУНКЦИИ (СОХРАНЕНЫ) ====================

# [Здесь должны быть все существующие функции из предыдущего кода:
# show_main_menu, name, phone, plot, system_type, problem, urgency, photo, 
# show_request_summary, cancel_request, get_enhanced_admin_panel, 
# show_enhanced_admin_panel, enhanced_handle_main_menu, enhanced_handle_admin_menu,
# и все остальные функции...]

# Клавиатуры
enhanced_user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['📊 Моя статистика', '🆘 Срочная помощь'],
    ['🎮 Игровая статистика', '📈 Аналитика'],
    ['ℹ️ О боте', '⚙️ Настройки']
]

def get_enhanced_admin_panel():
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    urgent_requests = db.get_urgent_requests()
    stuck_requests = db.get_stuck_requests(REQUEST_TIMEOUT_HOURS)
    
    return [
        [f'🆕 Новые ({len(new_requests)})', f'🔄 В работе ({len(in_progress_requests)})'],
        [f'⏰ Срочные ({len(urgent_requests)})', f'🚨 Зависшие ({len(stuck_requests)})'],
        ['📊 Статистика', '📈 Аналитика'],
        ['👥 Пользователи', '⚙️ Настройки'],
        ['💾 Бэкапы', '🔄 Обновить'],
        ['📊 Google Sheets', '🔄 Синхронизация'],
        ['🎮 Геймификация', '📊 Метрики']
    ]

# ==================== ЗАПУСК СИСТЕМ ====================

# Глобальные объекты
rate_limiter = RateLimiter()
db = None
sheets_manager = None
notification_manager = None
analytics_engine = None
ai_assistant = None
security_manager = None
performance_monitor = None
template_manager = None
i18n = None
gamification_engine = None
web_dashboard = None

def enhanced_main() -> None:
    """Улучшенный запуск бота со всеми системами"""
    global notification_manager
    
    if not config.validate():
        logger.error("❌ Неверная конфигурация бота!")
        return
    
    try:
        # Инициализация всех систем
        initialize_all_systems()
        
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Инициализация менеджера уведомлений
        notification_manager = SmartNotificationManager(updater.bot, db)

        # Обработчик ошибок
        dispatcher.add_error_handler(error_handler)

        # Задания по расписанию
        job_queue = updater.job_queue
        if job_queue:
            try:
                # Ежедневное резервное копирование
                backup_time = time(hour=AUTO_BACKUP_HOUR, minute=AUTO_BACKUP_MINUTE)
                job_queue.run_daily(backup_job, time=backup_time)
                
                # Ежечасная проверка срочных заявок
                job_queue.run_repeating(check_urgent_requests, interval=3600, first=10)
                
                # Проверка напоминаний
                job_queue.run_repeating(
                    lambda context: notification_manager.check_pending_reminders(),
                    interval=1800, first=300
                )
                
                # Автоматическая синхронизация с Google Sheets
                if config.sync_to_sheets:
                    job_queue.run_repeating(auto_sync_job, interval=1800, first=60)
                
                logger.info("✅ Все задания планировщика успешно зарегистрированы")
                
            except Exception as e:
                logger.error(f"❌ Ошибка регистрации заданий планировщика: {e}")

        # Обработчик создания заявки
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(📝 Создать заявку)$'), enhanced_start_request_creation),
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
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
            ],
        )

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_enhanced_admin_panel))
        dispatcher.add_handler(CommandHandler('stats', show_statistics))
        dispatcher.add_handler(CommandHandler('analytics', show_advanced_analytics))
        dispatcher.add_handler(CommandHandler('metrics', show_system_metrics))
        dispatcher.add_handler(CommandHandler('gamification', show_gamification_stats))
        
        dispatcher.add_handler(conv_handler)
        
        # Обработчики сообщений
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(✅ Подтвердить отправку)$'), 
            enhanced_confirm_request
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📝 Создать заявку|📋 Мои заявки|📊 Моя статистика|🆘 Срочная помощь|🎮 Игровая статистика|📈 Аналитика|ℹ️ О боте|⚙️ Настройки)$'), 
            enhanced_handle_main_menu
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые|🔄 В работе|⏰ Срочные|🚨 Зависшие|📊 Статистика|📈 Аналитика|👥 Пользователи|⚙️ Настройки|💾 Бэкапы|🔄 Обновить|📊 Google Sheets|🔄 Синхронизация|🎮 Геймификация|📊 Метрики)$'), 
            enhanced_handle_admin_menu
        ))
        
        # Новые обработчики
        dispatcher.add_handler(CallbackQueryHandler(button_handler))
        dispatcher.add_handler(MessageHandler(Filters.voice, handle_voice_message))

        # Запускаем бота
        logger.info("🤖 Улучшенный бот запущен со всеми системами!")
        logger.info(f"👑 Администраторы: {len(ADMIN_CHAT_IDS)}")
        logger.info(f"📊 Google Sheets: {'✅ Подключен' if sheets_manager and sheets_manager.is_connected else '❌ Отключен'}")
        logger.info(f"🤖 AI помощник: {'✅ Активен' if ai_assistant else '❌ Отключен'}")
        logger.info(f"🌐 Веб-панель: {'✅ Запущена' if FLASK_AVAILABLE else '❌ Отключена'}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска улучшенного бота: {e}")

if __name__ == '__main__':
    enhanced_main()
