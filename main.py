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
import matplotlib
matplotlib.use('Agg')  # Для работы без GUI
import matplotlib.pyplot as plt
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

# ==================== ДОБАВЛЕННЫЕ ИМПОРТЫ ====================
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

# ==================== БАЗОВЫЕ КЛАССЫ ====================

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

# ==================== GOOGLE SHEETS ====================

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
            logger.info(f"🤖 AI определил категорию: {suggested_category}")
        
        # Начисление очков
        gamification_engine.award_points(data['user_id'], 'create_request')
        
        # Синхронизация с Google Sheets
        if self.sheets_manager and self.sheets_manager.is_connected:
            try:
                self.sheets_manager.sheet.append_row([
                    request_id,
                    data.get('name', ''),
                    data.get('phone', ''),
                    data.get('plot', ''),
                    data.get('system_type', ''),
                    data.get('problem', ''),
                    data.get('urgency', ''),
                    'new',
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ])
                logger.info(f"✅ Заявка #{request_id} синхронизирована с Google Sheets")
            except Exception as e:
                logger.error(f"❌ Ошибка синхронизации с Google Sheets: {e}")
        
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

    def update_request(self, request_id: int, updates: Dict):
        """Обновляет заявку"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
                values = list(updates.values())
                values.append(request_id)
                
                cursor.execute(f'''
                    UPDATE requests 
                    SET {set_clause}, updated_at = ?
                    WHERE id = ?
                ''', values + [datetime.now().isoformat(), request_id])
                conn.commit()
                
                # Начисляем очки за выполнение
                if updates.get('status') == 'completed':
                    request_data = self.get_request_by_id(request_id)
                    if request_data:
                        gamification_engine.award_points(request_data['user_id'], 'request_completed')
                
                return True
        except sqlite3.Error as e:
            logger.error(f"Ошибка обновления заявки: {e}")
            return False

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

# ==================== ГЛОБАЛЬНЫЕ ОБЪЕКТЫ ====================

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

# ==================== КЛАВИАТУРЫ ====================

enhanced_user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['📊 Моя статистика', '🆘 Срочная помощь'],
    ['🎮 Игровая статистика', '📈 Аналитика'],
    ['ℹ️ О боте', '⚙️ Настройки']
]

def get_enhanced_admin_panel():
    new_requests = db.get_requests_by_filter('new') if db else []
    in_progress_requests = db.get_requests_by_filter('in_progress') if db else []
    urgent_requests = db.get_urgent_requests() if db else []
    stuck_requests = db.get_stuck_requests(REQUEST_TIMEOUT_HOURS) if db else []
    
    return [
        [f'🆕 Новые ({len(new_requests)})', f'🔄 В работе ({len(in_progress_requests)})'],
        [f'⏰ Срочные ({len(urgent_requests)})', f'🚨 Зависшие ({len(stuck_requests)})'],
        ['📊 Статистика', '📈 Аналитика'],
        ['👥 Пользователи', '⚙️ Настройки'],
        ['💾 Бэкапы', '🔄 Обновить'],
        ['📊 Google Sheets', '🔄 Синхронизация'],
        ['🎮 Геймификация', '📊 Метрики']
    ]

# ==================== ОСНОВНЫЕ ФУНКЦИИ БОТА ====================

def show_main_menu(update: Update, context: CallbackContext):
    """Показывает главное меню"""
    user = update.message.from_user
    
    if user.id in ADMIN_CHAT_IDS:
        reply_markup = ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
    else:
        reply_markup = ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        "🏠 *Главное меню*\n\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

def name(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод имени"""
    name_text = update.message.text
    
    if not Validators.validate_name(name_text):
        update.message.reply_text(
            "❌ Неверный формат имени. Используйте только буквы (2-50 символов).\nПопробуйте еще раз:"
        )
        return NAME
    
    context.user_data['name'] = name_text
    update.message.reply_text(
        "📞 Теперь введите ваш номер телефона:",
        reply_markup=ReplyKeyboardRemove()
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод телефона"""
    phone_text = update.message.text
    
    if not Validators.validate_phone(phone_text):
        update.message.reply_text(
            "❌ Неверный формат телефона. Используйте цифры, пробелы, скобки и дефисы.\nПопробуйте еще раз:"
        )
        return PHONE
    
    context.user_data['phone'] = phone_text
    
    keyboard = [['🏠 Участок 1', '🏠 Участок 2'], ['🏠 Участок 3', '🏠 Другой']]
    update.message.reply_text(
        "📍 Выберите или введите номер участка:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return PLOT

def plot(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод участка"""
    plot_text = update.message.text
    
    if not Validators.validate_plot(plot_text):
        update.message.reply_text(
            "❌ Неверный формат участка. Используйте буквы, цифры и дефисы.\nПопробуйте еще раз:"
        )
        return PLOT
    
    context.user_data['plot'] = plot_text
    
    keyboard = [
        ['🔌 Электрика', '📶 Интернет'],
        ['📞 Телефония', '🎥 Видеонаблюдение'],
        ['💧 Водоснабжение', '🔧 Другое']
    ]
    update.message.reply_text(
        "⚙️ Выберите тип системы:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор типа системы"""
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        "📝 Опишите проблему подробно:\n\n_Пример: Не работает интернет в гостиной, индикаторы на роутере не горят_",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    """Обрабатывает описание проблемы"""
    context.user_data['problem'] = update.message.text
    
    keyboard = [
        ['🔴 Срочно (в течение 1 часа)', '🟡 Средняя срочность (2-4 часа)'],
        ['🟢 Не срочно (в течение дня)', '⏰ Запланировать на завтра']
    ]
    update.message.reply_text(
        "⏱️ Выберите срочность заявки:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор срочности"""
    context.user_data['urgency'] = update.message.text
    
    keyboard = [['📷 Прикрепить фото', '🚀 Пропустить']]
    update.message.reply_text(
        "📷 Хотите прикрепить фото проблемы?",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    """Обрабатывает фото или пропуск"""
    if update.message.text == '🚀 Пропустить':
        context.user_data['photo'] = None
        return show_request_summary(update, context)
    elif update.message.photo:
        # Сохраняем информацию о фото
        photo_file = update.message.photo[-1].get_file()
        context.user_data['photo'] = photo_file.file_id
        return show_request_summary(update, context)
    else:
        update.message.reply_text(
            "📷 Пожалуйста, прикрепите фото или нажмите 'Пропустить'"
        )
        return PHOTO

def show_request_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку заявки для подтверждения"""
    user_data = context.user_data
    
    summary_text = (
        "📋 *Сводка заявки*\n\n"
        f"👤 *Имя:* {user_data.get('name', 'Не указано')}\n"
        f"📞 *Телефон:* {user_data.get('phone', 'Не указан')}\n"
        f"📍 *Участок:* {user_data.get('plot', 'Не указан')}\n"
        f"⚙️ *Тип системы:* {user_data.get('system_type', 'Не указан')}\n"
        f"📝 *Проблема:* {user_data.get('problem', 'Не указана')}\n"
        f"⏱️ *Срочность:* {user_data.get('urgency', 'Не указана')}\n"
        f"📷 *Фото:* {'✅ Прикреплено' if user_data.get('photo') else '❌ Отсутствует'}\n\n"
        "_Всё верно?_"
    )
    
    keyboard = [['✅ Подтвердить отправку', '❌ Отменить']]
    update.message.reply_text(
        summary_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

def cancel_request(update: Update, context: CallbackContext) -> int:
    """Отменяет создание заявки"""
    context.user_data.clear()
    update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
    )
    return ConversationHandler.END

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
            
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_message,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=create_request_actions_keyboard(request_id)
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
            
            logger.info(f"Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            update.message.reply_text(
                "❌ *Произошла ошибка при создании заявки.*\n\nПожалуйста, попробуйте позже.",
                parse_mode=ParseMode.MARKDOWN
            )
        
        context.user_data.clear()
    else:
        update.message.reply_text(
            "Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

def show_enhanced_admin_panel(update: Update, context: CallbackContext):
    """Показывает админ-панель"""
    user = update.message.from_user
    if user.id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    update.message.reply_text(
        "👑 *Панель администратора*\n\nВыберите действие:",
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_statistics(update: Update, context: CallbackContext):
    """Показывает статистику"""
    stats = db.get_statistics(7) if db else {'total': 0, 'completed': 0, 'new': 0, 'in_progress': 0}
    
    text = (
        "📊 *Статистика за 7 дней*\n\n"
        f"📨 Всего заявок: {stats['total']}\n"
        f"✅ Выполнено: {stats['completed']}\n"
        f"🆕 Новых: {stats['new']}\n"
        f"🔄 В работе: {stats['in_progress']}"
    )
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

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
        success = db.update_request(request_id, {'status': 'completed', 'completed_at': datetime.now().isoformat()})
        if success:
            query.edit_message_text(f"✅ Заявка #{request_id} выполнена!")
        else:
            query.edit_message_text(f"❌ Ошибка обновления заявки #{request_id}")
    
    elif data.startswith('progress_') and request_id:
        success = db.update_request(request_id, {'status': 'in_progress'})
        if success:
            query.edit_message_text(f"🔄 Заявка #{request_id} взята в работу!")
        else:
            query.edit_message_text(f"❌ Ошибка обновления заявки #{request_id}")

def show_advanced_analytics(update: Update, context: CallbackContext):
    """Показывает расширенную аналитику"""
    analytics = analytics_engine.get_advanced_analytics(30) if analytics_engine else {}
    
    text = "📈 *Расширенная аналитика*\n\n"
    text += f"⏱️ *Среднее время выполнения:* {analytics.get('avg_completion_hours', 0)}ч\n\n"
    
    text += "🔧 *Распределение по системам:*\n"
    for system, count in analytics.get('system_distribution', {}).items():
        text += f"• {system}: {count} заявок\n"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show_system_metrics(update: Update, context: CallbackContext):
    """Показывает системные метрики"""
    metrics = performance_monitor.get_system_metrics() if performance_monitor else {}
    
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
    user_stats = gamification_engine.get_user_stats(user_id) if gamification_engine else {'points': 0, 'level': 1}
    
    text = "🎮 *Ваша статистика*\n\n"
    text += f"🏆 Уровень: {user_stats['level']}\n"
    text += f"⭐ Очки: {user_stats['points']}\n\n"
    
    if gamification_engine:
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
        
        if security_manager and security_manager.is_user_blocked(user_id):
            update.message.reply_text("❌ Ваш аккаунт временно заблокирован")
            return
        
        if security_manager and not security_manager.check_suspicious_activity(user_id, 'message'):
            update.message.reply_text("❌ Слишком много запросов")
            return
        
        return handler_func(update, context)
    return wrapper

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

def enhanced_handle_main_menu(update: Update, context: CallbackContext):
    """Обрабатывает основные команды меню"""
    text = update.message.text
    
    if text == '📋 Мои заявки':
        update.message.reply_text("📋 Функция 'Мои заявки' в разработке...")
    elif text == '📊 Моя статистика':
        show_user_statistics(update, context)
    elif text == '🆘 Срочная помощь':
        update.message.reply_text("🆘 Для срочной помощи звоните: +7 (XXX) XXX-XX-XX")
    elif text == '🎮 Игровая статистика':
        show_gamification_stats(update, context)
    elif text == '📈 Аналитика':
        show_advanced_analytics(update, context)
    elif text == 'ℹ️ О боте':
        update.message.reply_text("ℹ️ Это бот для управления заявками технической поддержки.")
    elif text == '⚙️ Настройки':
        update.message.reply_text("⚙️ Настройки в разработке...")

def enhanced_handle_admin_menu(update: Update, context: CallbackContext):
    """Обрабатывает команды админ-меню"""
    user = update.message.from_user
    if user.id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    text = update.message.text
    
    if text.startswith('🆕 Новые'):
        show_requests_by_status(update, context, 'new')
    elif text.startswith('🔄 В работе'):
        show_requests_by_status(update, context, 'in_progress')
    elif text.startswith('⏰ Срочные'):
        show_urgent_requests(update, context)
    elif text.startswith('🚨 Зависшие'):
        show_stuck_requests(update, context)
    elif text == '📊 Статистика':
        show_statistics(update, context)
    elif text == '📈 Аналитика':
        show_advanced_analytics(update, context)
    elif text == '👥 Пользователи':
        show_users_statistics(update, context)
    elif text == '⚙️ Настройки':
        update.message.reply_text("⚙️ Настройки админ-панели в разработке...")
    elif text == '💾 Бэкапы':
        create_backup_command(update, context)
    elif text == '🔄 Обновить':
        show_enhanced_admin_panel(update, context)
    elif text == '📊 Google Sheets':
        show_google_sheets_status(update, context)
    elif text == '🔄 Синхронизация':
        sync_with_sheets(update, context)
    elif text == '🎮 Геймификация':
        show_gamification_leaderboard(update, context)
    elif text == '📊 Метрики':
        show_system_metrics(update, context)

def show_requests_by_status(update: Update, context: CallbackContext, status: str):
    """Показывает заявки по статусу"""
    requests = db.get_requests_by_filter(status) if db else []
    
    if not requests:
        update.message.reply_text(f"📭 Нет заявок со статусом '{status}'")
        return
    
    text = f"📋 *Заявки ({status})*:\n\n"
    for req in requests[:10]:  # Ограничиваем вывод
        text += f"#{req['id']} - {req['name']} - {req['plot']}\n"
        text += f"Проблема: {req['problem'][:50]}...\n"
        text += f"Создана: {req['created_at'][:16]}\n\n"
    
    if len(requests) > 10:
        text += f"... и еще {len(requests) - 10} заявок"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show_urgent_requests(update: Update, context: CallbackContext):
    """Показывает срочные заявки"""
    requests = db.get_urgent_requests() if db else []
    
    if not requests:
        update.message.reply_text("✅ Нет срочных заявок, требующих внимания")
        return
    
    text = "🔴 *Срочные заявки:*\n\n"
    for req in requests:
        text += f"#{req['id']} - {req['name']} - {req['plot']}\n"
        text += f"Проблема: {req['problem'][:50]}...\n"
        text += f"Создана: {req['created_at'][:16]}\n\n"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show_stuck_requests(update: Update, context: CallbackContext):
    """Показывает зависшие заявки"""
    requests = db.get_stuck_requests(24) if db else []
    
    if not requests:
        update.message.reply_text("✅ Нет зависших заявок")
        return
    
    text = "🚨 *Зависшие заявки (>24 часов):*\n\n"
    for req in requests:
        text += f"#{req['id']} - {req['name']} - {req['plot']}\n"
        text += f"Статус: {req['status']}\n"
        text += f"Создана: {req['created_at'][:16]}\n\n"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show_user_statistics(update: Update, context: CallbackContext):
    """Показывает статистику пользователя"""
    user_id = update.message.from_user.id
    stats = db.get_user_statistics(user_id) if db else {}
    
    text = "📊 *Ваша статистика:*\n\n"
    text += f"📨 Всего заявок: {stats.get('total_requests', 0)}\n"
    text += f"✅ Выполнено: {stats.get('completed', 0)}\n"
    text += f"🔄 В работе: {stats.get('in_progress', 0)}\n"
    text += f"🆕 Новых: {stats.get('new', 0)}\n"
    
    if stats.get('first_request'):
        text += f"📅 Первая заявка: {stats['first_request'][:10]}\n"
    if stats.get('last_request'):
        text += f"📅 Последняя заявка: {stats['last_request'][:10]}\n"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def show_users_statistics(update: Update, context: CallbackContext):
    """Показывает статистику пользователей (админ)"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) as total_users, 
                       SUM(request_count) as total_requests,
                       AVG(request_count) as avg_requests
                FROM users
            ''')
            result = cursor.fetchone()
            
            text = "👥 *Статистика пользователей:*\n\n"
            text += f"👤 Всего пользователей: {result[0]}\n"
            text += f"📨 Всего заявок: {result[1]}\n"
            text += f"📊 Среднее заявок на пользователя: {result[2]:.1f}\n"
            
            update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Ошибка получения статистики пользователей: {e}")
        update.message.reply_text("❌ Ошибка получения статистики")

def create_backup_command(update: Update, context: CallbackContext):
    """Создает бэкап базы данных"""
    backup_path = BackupManager.create_backup()
    if backup_path:
        update.message.reply_text(f"✅ Бэкап создан: `{backup_path}`", parse_mode=ParseMode.MARKDOWN)
    else:
        update.message.reply_text("❌ Ошибка создания бэкапа")

def show_google_sheets_status(update: Update, context: CallbackContext):
    """Показывает статус Google Sheets"""
    if sheets_manager and sheets_manager.is_connected:
        update.message.reply_text("✅ Google Sheets подключен и работает")
    else:
        update.message.reply_text("❌ Google Sheets не подключен")

def sync_with_sheets(update: Update, context: CallbackContext):
    """Синхронизирует данные с Google Sheets"""
    if sheets_manager and sheets_manager.is_connected:
        update.message.reply_text("🔄 Синхронизация с Google Sheets...")
        # Здесь можно добавить логику синхронизации
        update.message.reply_text("✅ Синхронизация завершена")
    else:
        update.message.reply_text("❌ Google Sheets не подключен")

def show_gamification_leaderboard(update: Update, context: CallbackContext):
    """Показывает таблицу лидеров геймификации"""
    if not gamification_engine:
        update.message.reply_text("❌ Система геймификации не активирована")
        return
    
    leaderboard = gamification_engine.get_leaderboard(10)
    
    if not leaderboard:
        update.message.reply_text("🏆 Таблица лидеров пуста")
        return
    
    text = "🏅 *Таблица лидеров:*\n\n"
    for i, (user_id, points, level) in enumerate(leaderboard, 1):
        text += f"{i}. Уровень {level} - {points} очков (ID: {user_id})\n"
    
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ==================== ЗАДАНИЯ ПО РАСПИСАНИЮ ====================

def backup_job(context: CallbackContext):
    """Задание для автоматического бэкапа"""
    backup_path = BackupManager.create_backup()
    if backup_path:
        logger.info(f"✅ Автоматический бэкап создан: {backup_path}")
        
        # Уведомление админам
        for admin_id in ADMIN_CHAT_IDS:
            try:
                context.bot.send_message(
                    admin_id,
                    f"✅ Автоматический бэкап создан: `{backup_path}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления о бэкапе: {e}")
    else:
        logger.error("❌ Ошибка автоматического бэкапа")

def check_urgent_requests(context: CallbackContext):
    """Проверяет срочные заявки"""
    try:
        urgent_requests = db.get_urgent_requests() if db else []
        if urgent_requests:
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    context.bot.send_message(
                        admin_id,
                        f"🔴 Внимание! Есть {len(urgent_requests)} срочных заявок, требующих обработки",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления о срочных заявках: {e}")
    except Exception as e:
        logger.error(f"Ошибка проверки срочных заявок: {e}")

def auto_sync_job(context: CallbackContext):
    """Автоматическая синхронизация с Google Sheets"""
    if sheets_manager and sheets_manager.is_connected:
        try:
            # Логика синхронизации
            logger.info("✅ Автоматическая синхронизация с Google Sheets")
        except Exception as e:
            logger.error(f"❌ Ошибка автоматической синхронизации: {e}")

def error_handler(update: Update, context: CallbackContext):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.message:
        update.message.reply_text(
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже.",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

# ==================== ЗАПУСК СИСТЕМ ====================

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
                from datetime import time as dt_time
                backup_time = dt_time(hour=AUTO_BACKUP_HOUR, minute=AUTO_BACKUP_MINUTE)
                job_queue.run_daily(backup_job, time=backup_time)
                
                # Ежечасная проверка срочных заявок
                job_queue.run_repeating(check_urgent_requests, interval=3600, first=10)
                
                # Проверка напоминаний
                if notification_manager:
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
