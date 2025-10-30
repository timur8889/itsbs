import logging
import sqlite3
import os
import json
import re
import time
import asyncio
import shutil
import matplotlib
matplotlib.use('Agg')  # Для работы matplotlib в асинхронном режиме
import matplotlib.pyplot as plt
import seaborn as sns
from io import BytesIO
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple, Set, Any
from functools import lru_cache
from enum import Enum
from dataclasses import dataclass
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
    """Цветное форматирование логов"""
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

# ==================== УЛУЧШЕННАЯ КОНФИГУРАЦИЯ ====================

class Config:
    """Конфигурация бота"""
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    SUPER_ADMIN_IDS = [int(x) for x in os.getenv('SUPER_ADMIN_IDS', '5024165375').split(',')]
    
    # Настройки отделов
    ADMIN_CHAT_IDS = {
        '💻 IT отдел': [5024165375],
        '🔧 Механика': [5024165375],
        '⚡ Электрика': [5024165375],
        '🏢 Общие': [5024165375]
    }
    
    DB_PATH = "requests.db"
    
    # Новые настройки
    ENABLE_AI_ANALYSIS = True
    ENABLE_RATINGS = True
    AUTO_BACKUP_HOURS = 24
    NOTIFICATION_HOURS_START = 9
    NOTIFICATION_HOURS_END = 22
    
    @staticmethod
    def is_admin(user_id: int) -> bool:
        """Проверяет, является ли пользователь администратором"""
        return any(user_id in admins for admins in Config.ADMIN_CHAT_IDS.values()) or user_id in Config.SUPER_ADMIN_IDS
    
    @staticmethod
    def validate_config():
        """Проверяет конфигурацию"""
        if not Config.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не найден в переменных окружения!")
        
        required_vars = ['BOT_TOKEN']
        for var in required_vars:
            if not getattr(Config, var):
                raise ValueError(f"Не задана обязательная переменная: {var}")

# ==================== МИГРАЦИИ БАЗЫ ДАННЫХ ====================

class DatabaseMigrator:
    """Управление миграциями базы данных"""
    
    MIGRATIONS = [
        # Миграция 1: Добавление индексов для производительности
        '''
        CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
        CREATE INDEX IF NOT EXISTS idx_requests_department ON requests(department);
        CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at);
        CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id);
        ''',
        
        # Миграция 2: Добавление таблицы для истории изменений
        '''
        CREATE TABLE IF NOT EXISTS request_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            changed_by INTEGER,
            change_type TEXT,
            old_value TEXT,
            new_value TEXT,
            changed_at TEXT,
            FOREIGN KEY (request_id) REFERENCES requests (id)
        );
        ''',
        
        # Миграция 3: Добавление таблицы пользовательских настроек
        '''
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            notification_preferences TEXT DEFAULT 'all',
            language TEXT DEFAULT 'ru',
            created_at TEXT,
            updated_at TEXT
        );
        '''
    ]
    
    @classmethod
    def run_migrations(cls, db_path: str):
        """Выполняет все pending миграции"""
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                
                # Создаем таблицу для отслеживания миграций
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS migrations (
                        id INTEGER PRIMARY KEY,
                        migration_name TEXT,
                        applied_at TEXT
                    )
                ''')
                
                # Получаем applied миграции
                cursor.execute('SELECT id FROM migrations')
                applied_migrations = set(row[0] for row in cursor.fetchall())
                
                # Применяем новые миграции
                for i, migration_sql in enumerate(cls.MIGRATIONS, 1):
                    if i not in applied_migrations:
                        try:
                            cursor.executescript(migration_sql)
                            cursor.execute(
                                'INSERT INTO migrations (id, migration_name, applied_at) VALUES (?, ?, ?)',
                                (i, f'migration_{i}', datetime.now().isoformat())
                            )
                            logger.info(f"✅ Применена миграция #{i}")
                        except Exception as e:
                            logger.error(f"❌ Ошибка миграции #{i}: {e}")
                            raise
        except Exception as e:
            logger.error(f"❌ Ошибка выполнения миграций: {e}")

# ==================== УЛУЧШЕННАЯ БАЗА ДАННЫХ ====================

class EnhancedDatabase:
    """Улучшенный класс для работы с базой данных"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_enhanced_db()
        # Запускаем миграции после инициализации
        DatabaseMigrator.run_migrations(db_path)
    
    def init_enhanced_db(self):
        """Инициализация улучшенной базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Таблица заявок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    phone TEXT,
                    department TEXT,
                    problem TEXT,
                    photo_id TEXT,
                    status TEXT DEFAULT 'new',
                    urgency TEXT DEFAULT '💤 НЕ СРОЧНО',
                    created_at TEXT,
                    assigned_at TEXT,
                    assigned_admin TEXT,
                    completed_at TEXT
                )
            ''')
            
            # Таблица администраторов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    department TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TEXT
                )
            ''')
            
            # Таблица рейтингов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS request_ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER,
                    user_id INTEGER,
                    admin_id INTEGER,
                    admin_name TEXT,
                    rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                    comment TEXT,
                    created_at TEXT,
                    FOREIGN KEY (request_id) REFERENCES requests (id)
                )
            ''')
            
            # Таблица настроек
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    description TEXT,
                    updated_at TEXT
                )
            ''')
            
            # Таблица шаблонов ответов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS response_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department TEXT,
                    title TEXT,
                    template_text TEXT,
                    created_at TEXT
                )
            ''')
            
            # Таблица SLA метрик
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sla_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER,
                    response_time_minutes INTEGER,
                    resolution_time_minutes INTEGER,
                    met_sla BOOLEAN,
                    created_at TEXT,
                    FOREIGN KEY (request_id) REFERENCES requests (id)
                )
            ''')
            
            # Начальные настройки
            default_settings = [
                ('enable_ai_analysis', 'true', 'Включить AI анализ заявок', datetime.now().isoformat()),
                ('enable_ratings', 'true', 'Включить систему рейтингов', datetime.now().isoformat()),
                ('auto_backup_hours', '24', 'Частота авто-бэкапов (часы)', datetime.now().isoformat()),
                ('work_hours_start', '9', 'Начало рабочего дня', datetime.now().isoformat()),
                ('work_hours_end', '22', 'Конец рабочего дня', datetime.now().isoformat()),
            ]
            
            cursor.executemany('''
                INSERT OR REPLACE INTO bot_settings (key, value, description, updated_at)
                VALUES (?, ?, ?, ?)
            ''', default_settings)
            
            # Добавляем начальные шаблоны ответов
            initial_templates = [
                ('💻 IT отдел', 'Перезагрузка', 'Попробуйте перезагрузить компьютер. Если проблема сохранится, сообщите нам.', datetime.now().isoformat()),
                ('💻 IT отдел', 'Проверка сети', 'Проверьте подключение к сети. Убедитесь, что кабель подключен.', datetime.now().isoformat()),
                ('🔧 Механика', 'Диагностика', 'Проводим диагностику оборудования. Ожидайте специалиста.', datetime.now().isoformat()),
                ('⚡ Электрика', 'Проверка питания', 'Проверяем подачу питания. Специалист выезжает к вам.', datetime.now().isoformat()),
            ]
            
            cursor.executemany('''
                INSERT OR REPLACE INTO response_templates (department, title, template_text, created_at)
                VALUES (?, ?, ?, ?)
            ''', initial_templates)
            
            conn.commit()
    
    def add_request(self, user_id: int, username: str, phone: str, department: str, 
                   problem: str, photo_id: str = None, urgency: str = '💤 НЕ СРОЧНО') -> int:
        """Добавляет новую заявку"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO requests 
                (user_id, username, phone, department, problem, photo_id, urgency, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, username, phone, department, problem, photo_id, urgency, datetime.now().isoformat()))
            request_id = cursor.lastrowid
            
            # Добавляем запись в историю
            cursor.execute('''
                INSERT INTO request_history 
                (request_id, changed_by, change_type, old_value, new_value, changed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (request_id, user_id, 'created', None, 'new', datetime.now().isoformat()))
            
            conn.commit()
            return request_id
    
    def get_requests(self, status: str = None, department: str = None, limit: int = 50) -> List[Dict]:
        """Получает список заявок"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM requests WHERE 1=1"
            params = []
            
            if status:
                query += " AND status = ?"
                params.append(status)
            
            if department:
                query += " AND department = ?"
                params.append(department)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    @lru_cache(maxsize=100)
    def get_request_cached(self, request_id: int) -> Optional[Dict]:
        """Получает заявку по ID с кэшированием"""
        return self.get_request(request_id)
    
    def get_request(self, request_id: int) -> Optional[Dict]:
        """Получает заявку по ID"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
            row = cursor.fetchone()
            if row:
                columns = [column[0] for column in cursor.description]
                return dict(zip(columns, row))
            return None
    
    def update_request_status(self, request_id: int, status: str, admin_name: str = None):
        """Обновляет статус заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Получаем текущий статус для истории
            old_status = None
            cursor.execute('SELECT status FROM requests WHERE id = ?', (request_id,))
            result = cursor.fetchone()
            if result:
                old_status = result[0]
            
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
            
            # Добавляем запись в историю
            if old_status != status:
                cursor.execute('''
                    INSERT INTO request_history 
                    (request_id, changed_by, change_type, old_value, new_value, changed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (request_id, admin_name or 'system', 'status_changed', old_status, status, datetime.now().isoformat()))
            
            # Очищаем кэш для этой заявки
            self.get_request_cached.cache_clear()
            conn.commit()
    
    def get_user_requests(self, user_id: int) -> List[Dict]:
        """Получает заявки пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            ''', (user_id,))
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def get_advanced_statistics(self) -> Dict[str, Any]:
        """Получает расширенную статистику"""
        basic_stats = self.get_statistics()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Среднее время выполнения
            cursor.execute('''
                SELECT AVG(
                    (julianday(completed_at) - julianday(created_at)) * 24 * 60
                ) as avg_completion_time
                FROM requests 
                WHERE status = 'completed' AND completed_at IS NOT NULL
            ''')
            avg_time_result = cursor.fetchone()
            avg_time = avg_time_result[0] or 0 if avg_time_result else 0
            
            # Статистика по срочности
            cursor.execute('''
                SELECT urgency, COUNT(*), 
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM requests 
                GROUP BY urgency
            ''')
            urgency_stats = {}
            for row in cursor.fetchall():
                urgency_stats[row[0]] = {'total': row[1], 'completed': row[2]}
            
            # Рейтинги администраторов
            cursor.execute('''
                SELECT assigned_admin, COUNT(*), 
                       AVG((julianday(completed_at) - julianday(assigned_at)) * 24 * 60)
                FROM requests 
                WHERE status = 'completed' AND assigned_admin IS NOT NULL
                GROUP BY assigned_admin
            ''')
            admin_stats = {}
            for row in cursor.fetchall():
                admin_stats[row[0]] = {
                    'completed_requests': row[1], 
                    'avg_completion_time': row[2] or 0
                }
        
        basic_stats.update({
            'avg_completion_time_minutes': round(avg_time, 1),
            'urgency_stats': urgency_stats,
            'admin_stats': admin_stats,
            'efficiency': (basic_stats['completed'] / basic_stats['total'] * 100) if basic_stats['total'] > 0 else 0
        })
        
        return basic_stats
    
    def get_statistics(self) -> Dict[str, Any]:
        """Получает базовую статистика заявок"""
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
            total_stats = cursor.fetchone()
            
            # Статистика по отделам
            cursor.execute('''
                SELECT 
                    department,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM requests
                GROUP BY department
            ''')
            
            department_stats = {}
            for row in cursor.fetchall():
                department_stats[row[0]] = {
                    'total': row[1],
                    'new': row[2],
                    'in_progress': row[3],
                    'completed': row[4]
                }
            
            return {
                'total': total_stats[0],
                'new': total_stats[1],
                'in_progress': total_stats[2],
                'completed': total_stats[3],
                'by_department': department_stats
            }

# ==================== КЭШИРОВАННАЯ СТАТИСТИКА ====================

class CachedStatistics:
    """Кэшированная статистика для производительности"""
    
    def __init__(self, db):
        self.db = db
        self._cache = {}
        self._cache_time = {}
    
    @lru_cache(maxsize=1)
    def get_statistics_cached(self, force_refresh: bool = False) -> Dict:
        """Получает статистику с кэшированием на 5 минут"""
        cache_key = "statistics"
        
        if not force_refresh and cache_key in self._cache:
            if datetime.now() - self._cache_time[cache_key] < timedelta(minutes=5):
                return self._cache[cache_key]
        
        stats = self.db.get_advanced_statistics()
        self._cache[cache_key] = stats
        self._cache_time[cache_key] = datetime.now()
        
        return stats
    
    def clear_cache(self):
        """Очищает кэш статистики"""
        self._cache.clear()
        self._cache_time.clear()
        self.get_statistics_cached.cache_clear()

# ==================== РЕЙТИНГИ И АНАЛИТИКА ====================

class EnhancedRatingSystem:
    """Улучшенная система рейтингов и отзывов"""
    
    @staticmethod
    def save_rating(db_path: str, request_id: int, user_id: int, admin_id: int, admin_name: str, rating: int, comment: str = ""):
        """Сохраняет оценку заявки"""
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO request_ratings (request_id, user_id, admin_id, admin_name, rating, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (request_id, user_id, admin_id, admin_name, rating, comment, datetime.now().isoformat()))
            conn.commit()

    @staticmethod
    def get_admin_rating(db_path: str, admin_id: int) -> Dict[str, Any]:
        """Получает рейтинг администратора"""
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_ratings,
                    AVG(rating) as avg_rating,
                    SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) as five_stars,
                    SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END) as four_stars,
                    SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END) as three_stars,
                    SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END) as two_stars,
                    SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as one_stars
                FROM request_ratings 
                WHERE admin_id = ?
            ''', (admin_id,))
            result = cursor.fetchone()
            
            return {
                'total_ratings': result[0],
                'avg_rating': round(result[1], 2) if result[1] else 0,
                'five_stars': result[2],
                'four_stars': result[3],
                'three_stars': result[4],
                'two_stars': result[5],
                'one_stars': result[6]
            }

    @staticmethod
    def get_rating_stats(db_path: str, days: int = 30) -> Dict[str, Any]:
        """Получает статистику рейтингов за период"""
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            since_date = (datetime.now() - timedelta(days=days)).isoformat()
            
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_ratings,
                    AVG(rating) as avg_rating,
                    admin_id,
                    admin_name
                FROM request_ratings 
                WHERE created_at > ?
                GROUP BY admin_id, admin_name
                ORDER BY avg_rating DESC
            ''', (since_date,))
            
            results = cursor.fetchall()
            return {
                'period_ratings': [
                    {
                        'admin_id': row[2],
                        'admin_name': row[3] or f"Admin_{row[2]}",
                        'total_ratings': row[0],
                        'avg_rating': round(row[1], 2) if row[1] else 0
                    }
                    for row in results
                ],
                'overall_avg': round(sum(row[1] for row in results) / len(results), 2) if results else 0
            }

# ==================== УМНЫЕ УВЕДОМЛЕНИЯ ====================

class EnhancedNotificationManager:
    """Расширенный менеджер умных уведомлений"""
    
    def __init__(self, bot):
        self.bot = bot
        self.user_preferences = {}  # В реальности хранить в БД
        
    async def send_smart_notification(self, user_id: int, message: str, priority: str = "normal"):
        """Отправляет умное уведомление с учетом предпочтений пользователя"""
        try:
            # Проверяем время для ненавязчивых уведомлений
            current_hour = datetime.now().hour
            if priority == "low" and (current_hour < 9 or current_hour > 22):
                return False  # Не беспокоим в нерабочее время для низкоприоритетных уведомлений
            
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")
            return False

    async def send_department_notification(self, department: str, message: str, exclude_admin: int = None):
        """Отправляет уведомление всем администраторам отдела"""
        admin_ids = Config.ADMIN_CHAT_IDS.get(department, [])
        
        for admin_id in admin_ids:
            if admin_id != exclude_admin:  # Исключаем отправителя
                await self.send_smart_notification(admin_id, message, "normal")
    
    async def notify_new_request(self, context: ContextTypes.DEFAULT_TYPE, request: Dict):
        """Уведомляет о новой заявке"""
        message = (
            f"🆕 *НОВАЯ ЗАЯВКА #{request['id']}*\n\n"
            f"👤 {request['username']} | 📞 {request['phone']}\n"
            f"🏢 {request['department']}\n"
            f"🔧 {request['problem'][:100]}...\n"
            f"⏰ {request['urgency']}\n"
            f"🕒 {request['created_at'][:16]}"
        )
        
        await self.send_department_notification(request['department'], message)

# ==================== AI АНАЛИЗ ЗАЯВОК ====================

class AIAnalyzer:
    """AI анализ текста заявок для автоматической категоризации"""
    
    KEYWORDS = {
        '💻 IT отдел': ['компьютер', 'принтер', 'интернет', 'программа', '1с', 'база', 'сеть', 'email', 'почта', 'мышь', 'клавиатура', 'монитор'],
        '🔧 Механика': ['станок', 'инструмент', 'ремонт', 'смазка', 'гидравлика', 'пневматика', 'транспорт', 'механизм', 'подшипник'],
        '⚡ Электрика': ['свет', 'проводка', 'розетка', 'щит', 'напряжение', 'автомат', 'освещение', 'электрик', 'кабель']
    }
    
    URGENCY_KEYWORDS = {
        '🔥 СРОЧНО': ['срочно', 'авария', 'сломалось', 'не работает', 'срочная', 'аварийная', 'горящее', 'критично'],
        '⚠️ СЕГОДНЯ': ['сегодня', 'сейчас', 'быстро', 'нужно', 'требуется', 'неотложно'],
        '💤 НЕ СРОЧНО': ['не срочно', 'когда будет', 'планово', 'можно подождать', 'в ближайшее время']
    }
    
    @classmethod
    def get_default_analysis(cls) -> Dict[str, Any]:
        """Возвращает анализ по умолчанию"""
        return {
            'suggested_department': '🏢 Общие',
            'suggested_urgency': '💤 НЕ СРОЧНО',
            'confidence_score': 0.0,
            'department_scores': {},
            'urgency_scores': {}
        }
    
    @classmethod
    def analyze_problem_text(cls, text: str) -> Dict[str, Any]:
        """Анализирует текст проблемы и предлагает категории"""
        try:
            if not text or len(text.strip()) < 3:
                return cls.get_default_analysis()
            
            text_lower = text.lower()
            
            # Анализ отдела
            department_scores = {}
            for dept, keywords in cls.KEYWORDS.items():
                score = sum(1 for keyword in keywords if keyword in text_lower)
                if score > 0:
                    department_scores[dept] = score
            
            # Анализ срочности
            urgency_scores = {}
            for urgency, keywords in cls.URGENCY_KEYWORDS.items():
                score = sum(1 for keyword in keywords if keyword in text_lower)
                if score > 0:
                    urgency_scores[urgency] = score
            
            return {
                'suggested_department': max(department_scores, key=department_scores.get) if department_scores else '🏢 Общие',
                'suggested_urgency': max(urgency_scores, key=urgency_scores.get) if urgency_scores else '💤 НЕ СРОЧНО',
                'confidence_score': len([s for s in department_scores.values() if s > 0]) / len(cls.KEYWORDS),
                'department_scores': department_scores,
                'urgency_scores': urgency_scores
            }
        except Exception as e:
            logger.error(f"Ошибка AI анализа: {e}")
            return cls.get_default_analysis()

# ==================== МЕНЕДЖЕР ШАБЛОНОВ ОТВЕТОВ ====================

class ResponseTemplateManager:
    """Менеджер шаблонов ответов для администраторов"""
    
    @staticmethod
    def get_templates(department: str) -> List[Dict]:
        """Получает шаблоны ответов для отдела"""
        with sqlite3.connect(Config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM response_templates 
                WHERE department = ? OR department = 'general'
                ORDER BY department DESC, title
            ''', (department,))
            
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    @staticmethod
    def create_template(department: str, title: str, template_text: str):
        """Создает новый шаблон ответа"""
        with sqlite3.connect(Config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO response_templates (department, title, template_text, created_at)
                VALUES (?, ?, ?, ?)
            ''', (department, title, template_text, datetime.now().isoformat()))
            conn.commit()

    @staticmethod
    def get_template_buttons(department: str) -> List[List[InlineKeyboardButton]]:
        """Создает кнопки шаблонов ответов"""
        templates = ResponseTemplateManager.get_templates(department)
        keyboard = []
        
        for template in templates[:5]:  # Ограничиваем 5 шаблонами
            keyboard.append([
                InlineKeyboardButton(
                    f"📝 {template['title']}",
                    callback_data=f"template_{template['id']}"
                )
            ])
        
        return keyboard

# ==================== АВТОМАТИЗАЦИЯ РАБОЧИХ ПРОЦЕССОВ ====================

class WorkflowAutomator:
    """Автоматизация рабочих процессов"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    async def check_timeout_requests(self, bot):
        """Проверяет просроченные заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                timeout_threshold = (datetime.now() - timedelta(hours=48)).isoformat()
                
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE status = 'in_progress' AND assigned_at < ?
                ''', (timeout_threshold,))
                
                timeout_requests = cursor.fetchall()
                
                for request in timeout_requests:
                    await self.notify_timeout(bot, request)
                    
        except Exception as e:
            logger.error(f"Ошибка проверки таймаутов: {e}")
    
    async def notify_timeout(self, bot, request):
        """Уведомляет о просроченной заявке"""
        try:
            request_dict = dict(zip(['id', 'user_id', 'username', 'phone', 'department', 'problem', 'photo_id', 'status', 'urgency', 'created_at', 'assigned_at', 'assigned_admin', 'completed_at'], request))
            
            # Уведомление супер-админам
            admin_message = (
                f"⏰ *ПРОСРОЧЕНА ЗАЯВКА #{request_dict['id']}*\n\n"
                f"🕒 Находится в работе более 48 часов!\n"
                f"👤 Клиент: {request_dict['username']}\n"
                f"📞 Телефон: {request_dict['phone']}\n"
                f"🏢 Отдел: {request_dict['department']}\n"
                f"👨‍💼 Исполнитель: {request_dict['assigned_admin']}\n"
                f"🔧 Проблема: {request_dict['problem'][:100]}..."
            )
            
            for super_admin_id in Config.SUPER_ADMIN_IDS:
                await bot.send_message(
                    chat_id=super_admin_id,
                    text=admin_message,
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"Ошибка уведомления о таймауте: {e}")

# ==================== ВИЗУАЛИЗАЦИЯ ДАННЫХ ====================

class DataVisualizer:
    """Генерация графиков и отчетов"""
    
    @staticmethod
    def create_statistics_plot(stats: Dict[str, Any]) -> BytesIO:
        """Создает график статистики"""
        try:
            plt.style.use('seaborn-v0_8')
            fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 10))
            
            # График 1: Общая статистика
            status_data = [stats['new'], stats['in_progress'], stats['completed']]
            status_labels = ['Новые', 'В работе', 'Выполнено']
            colors = ['#ff6b6b', '#4ecdc4', '#45b7d1']
            ax1.pie(status_data, labels=status_labels, colors=colors, autopct='%1.1f%%')
            ax1.set_title('Статус заявок')
            
            # График 2: По отделам
            departments = list(stats['by_department'].keys())
            completed = [stats['by_department'][dept]['completed'] for dept in departments]
            total = [stats['by_department'][dept]['total'] for dept in departments]
            
            x = range(len(departments))
            ax2.bar(x, total, label='Всего', alpha=0.7)
            ax2.bar(x, completed, label='Выполнено', alpha=0.9)
            ax2.set_xticks(x)
            ax2.set_xticklabels([dept.replace(' отдел', '') for dept in departments], rotation=45)
            ax2.set_title('Заявки по отделам')
            ax2.legend()
            
            # График 3: Эффективность отделов
            efficiency = []
            for dept in departments:
                dept_stats = stats['by_department'][dept]
                eff = (dept_stats['completed'] / dept_stats['total'] * 100) if dept_stats['total'] > 0 else 0
                efficiency.append(eff)
            
            ax3.bar(range(len(departments)), efficiency, color='lightgreen')
            ax3.set_xticks(range(len(departments)))
            ax3.set_xticklabels([dept.replace(' отдел', '') for dept in departments], rotation=45)
            ax3.set_ylabel('Процент выполнения (%)')
            ax3.set_title('Эффективность отделов')
            
            # График 4: Время выполнения
            avg_time = stats.get('avg_completion_time_minutes', 0)
            ax4.text(0.5, 0.6, f'Среднее время\nвыполнения:\n{avg_time:.1f} мин.', 
                    fontsize=14, ha='center', va='center')
            ax4.axis('off')
            
            plt.tight_layout()
            
            # Сохраняем в буфер
            buffer = BytesIO()
            plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
            buffer.seek(0)
            plt.close()
            
            return buffer
            
        except Exception as e:
            logger.error(f"Ошибка создания графика: {e}")
            return None

# ==================== МЕНЕДЖЕР БЭКАПОВ ====================

class BackupManager:
    """Менеджер резервного копирования"""
    
    @staticmethod
    def create_backup() -> str:
        """Создает резервную копию базы данных"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"backup/requests_backup_{timestamp}.db"
            
            # Создаем папку для бэкапов если нет
            os.makedirs("backup", exist_ok=True)
            
            # Копируем файл базы данных
            shutil.copy2(Config.DB_PATH, backup_file)
            
            logger.info(f"Создан бэкап: {backup_file}")
            return backup_file
        except Exception as e:
            logger.error(f"Ошибка создания бэкапа: {e}")
            return None
    
    @staticmethod
    def cleanup_old_backups(max_backups: int = 10):
        """Удаляет старые бэкапы"""
        try:
            backup_dir = "backup"
            if not os.path.exists(backup_dir):
                return
            
            backups = []
            for file in os.listdir(backup_dir):
                if file.startswith("requests_backup_") and file.endswith(".db"):
                    file_path = os.path.join(backup_dir, file)
                    backups.append((file_path, os.path.getctime(file_path)))
            
            # Сортируем по дате создания (новые первыми)
            backups.sort(key=lambda x: x[1], reverse=True)
            
            # Удаляем старые бэкапы
            for backup_path, _ in backups[max_backups:]:
                os.remove(backup_path)
                logger.info(f"Удален старый бэкап: {backup_path}")
                
        except Exception as e:
            logger.error(f"Ошибка очистки бэкапов: {e}")

    @staticmethod
    def list_backups() -> List[str]:
        """Возвращает список доступных бэкапов"""
        try:
            backup_dir = "backup"
            if not os.path.exists(backup_dir):
                return []
            
            backups = []
            for file in os.listdir(backup_dir):
                if file.startswith("requests_backup_") and file.endswith(".db"):
                    file_path = os.path.join(backup_dir, file)
                    backups.append(file_path)
            
            return sorted(backups, reverse=True)
        except Exception as e:
            logger.error(f"Ошибка получения списка бэкапов: {e}")
            return []

# ==================== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ====================

# Инициализация улучшенной базы данных
db = EnhancedDatabase(Config.DB_PATH)
cached_stats = CachedStatistics(db)
notification_manager = EnhancedNotificationManager(None)  # Инициализируем позже

# ==================== ОСНОВНЫЕ КОМАНДЫ БОТА ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.message.from_user
    
    welcome_text = (
        "🚀 *Добро пожаловать в улучшенную систему заявок!*\n\n"
        "✨ *Новые возможности:*\n"
        "• 🤖 AI анализ текста заявок\n"
        "• 📊 Визуальная статистика с графиками\n"
        "• ⭐ Система рейтингов и отзывов\n"
        "• 🔄 Автоматические уведомления\n"
        "• 💾 Авто-бэкапы данных\n"
        "• 📝 Шаблоны ответов\n"
        "• 🗃️ История изменений\n\n"
        "🎯 *Выберите действие из меню ниже:*"
    )
    
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает главное меню"""
    keyboard = [
        ["📋 Создать заявку", "📊 Мои заявки"],
        ["📊 Статистика", "🤖 AI Анализ"],
        ["⭐ Рейтинги", "🆘 Помощь"]
    ]
    
    # Добавляем админские кнопки для администраторов
    if Config.is_admin(update.message.from_user.id):
        keyboard.insert(1, ["👨‍💼 Админ панель", "📋 Все заявки"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🎯 *Главное меню улучшенной системы заявки*",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ЗАПУСК И ДИАГНОСТИКА ====================

if __name__ == '__main__':
    try:
        print("🔄 Запуск бота...")
        print(f"📝 Проверка конфигурации...")
        Config.validate_config()
        print("✅ Конфигурация проверена")
        
        print("🗄️ Инициализация базы данных...")
        db = EnhancedDatabase(Config.DB_PATH)
        print("✅ База данных готова")
        
        print("🤖 Создание приложения...")
        application = Application.builder().token(Config.BOT_TOKEN).build()
        print("✅ Приложение создано")
        
        print("🔧 Настройка обработчиков...")
        setup_handlers(application)
        print("✅ Обработчики настроены")
        
        print("⏰ Настройка автоматических задач...")
        setup_automated_tasks(application)
        print("✅ Задачи настроены")
        
        print("🚀 ЗАПУСК БОТА...")
        application.run_polling()
        
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        logger.error(f"Критическая ошибка запуска: {e}")
        import traceback
        traceback.print_exc()
