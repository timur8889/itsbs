import logging
import sqlite3
import os
import json
import re
import time
import asyncio
import shutil
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

# ==================== УЛУЧШЕННАЯ КОНФИГУРАЦИЯ ====================

class Config:
    """⚙️ Конфигурация бота"""
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

# ==================== МИГРАЦИИ БАЗЫ ДАННЫХ ====================

class DatabaseMigrator:
    """🔄 Управление миграциями базы данных"""
    
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
        """🚀 Выполняет все pending миграции"""
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
    """🗃️ Улучшенный класс для работы с базой данных"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_enhanced_db()
        # Запускаем миграции после инициализации
        DatabaseMigrator.run_migrations(db_path)
    
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
                ('💻 IT отдел', '🔄 Перезагрузка', '🖥️ Попробуйте перезагрузить компьютер. Если проблема сохранится, сообщите нам.', datetime.now().isoformat()),
                ('💻 IT отдел', '🌐 Проверка сети', '📡 Проверьте подключение к сети. Убедитесь, что кабель подключен.', datetime.now().isoformat()),
                ('🔧 Механика', '🔍 Диагностика', '🛠️ Проводим диагностику оборудования. Ожидайте специалиста.', datetime.now().isoformat()),
                ('⚡ Электрика', '⚡ Проверка питания', '🔌 Проверяем подачу питания. Специалист выезжает к вам.', datetime.now().isoformat()),
            ]
            
            cursor.executemany('''
                INSERT OR REPLACE INTO response_templates (department, title, template_text, created_at)
                VALUES (?, ?, ?, ?)
            ''', initial_templates)
            
            conn.commit()
    
    def add_request(self, user_id: int, username: str, phone: str, department: str, 
                   problem: str, photo_id: str = None, urgency: str = '💤 НЕ СРОЧНО') -> int:
        """📝 Добавляет новую заявку"""
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
        """📋 Получает список заявок"""
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
        """⚡ Получает заявку по ID с кэшированием"""
        return self.get_request(request_id)
    
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
        """📂 Получает заявки пользователя"""
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
        """📊 Получает расширенную статистику"""
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
        """📈 Получает базовую статистика заявок"""
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
    """⚡ Кэшированная статистика для производительности"""
    
    def __init__(self, db):
        self.db = db
        self._cache = {}
        self._cache_time = {}
    
    @lru_cache(maxsize=1)
    def get_statistics_cached(self, force_refresh: bool = False) -> Dict:
        """🔄 Получает статистику с кэшированием на 5 минут"""
        cache_key = "statistics"
        
        if not force_refresh and cache_key in self._cache:
            if datetime.now() - self._cache_time[cache_key] < timedelta(minutes=5):
                return self._cache[cache_key]
        
        stats = self.db.get_advanced_statistics()
        self._cache[cache_key] = stats
        self._cache_time[cache_key] = datetime.now()
        
        return stats
    
    def clear_cache(self):
        """🧹 Очищает кэш статистики"""
        self._cache.clear()
        self._cache_time.clear()
        self.get_statistics_cached.cache_clear()

# ==================== ТЕКСТОВАЯ ВИЗУАЛИЗАЦИЯ СТАТИСТИКИ ====================

class TextVisualizer:
    """📊 Текстовая визуализация статистики"""
    
    @staticmethod
    def create_progress_bar(percentage: float, width: int = 20) -> str:
        """📏 Создает текстовый прогресс-бар"""
        filled = int(width * percentage / 100)
        empty = width - filled
        return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"
    
    @staticmethod
    def create_statistics_text(stats: Dict[str, Any]) -> str:
        """🎨 Создает текстовое представление статистики"""
        stats_text = "📊 *РАСШИРЕННАЯ СТАТИСТИКА СИСТЕМЫ*\n\n"
        
        # Общая статистика
        stats_text += "📈 *ОБЩАЯ СТАТИСТИКА*\n"
        stats_text += f"• 📦 Всего заявок: {stats['total']}\n"
        stats_text += f"• 🆕 Новые: {stats['new']} | 🔄 В работе: {stats['in_progress']} | ✅ Выполнено: {stats['completed']}\n"
        stats_text += f"• 🎯 Эффективность: {TextVisualizer.create_progress_bar(stats['efficiency'])}\n"
        stats_text += f"• ⏱️ Среднее время выполнения: {stats['avg_completion_time_minutes']:.1f} мин.\n\n"
        
        # Статистика по отделам
        stats_text += "🏢 *СТАТИСТИКА ПО ОТДЕЛАМ*\n"
        for dept, dept_stats in stats['by_department'].items():
            total = dept_stats['total']
            completed = dept_stats['completed']
            efficiency = (completed / total * 100) if total > 0 else 0
            stats_text += f"• {dept}: {completed}/{total} {TextVisualizer.create_progress_bar(efficiency, 10)}\n"
        
        # Статистика по срочности
        if stats.get('urgency_stats'):
            stats_text += "\n⏰ *СТАТИСТИКА ПО СРОЧНОСТИ*\n"
            for urgency, urgency_stats in stats['urgency_stats'].items():
                total = urgency_stats['total']
                completed = urgency_stats['completed']
                efficiency = (completed / total * 100) if total > 0 else 0
                stats_text += f"• {urgency}: {completed}/{total} {TextVisualizer.create_progress_bar(efficiency, 10)}\n"
        
        # Статистика администраторов
        if stats.get('admin_stats'):
            stats_text += "\n👨‍💼 *ЭФФЕКТИВНОСТЬ АДМИНИСТРАТОРОВ*\n"
            for admin, admin_stats in stats['admin_stats'].items():
                completed = admin_stats['completed_requests']
                avg_time = admin_stats['avg_completion_time']
                stats_text += f"• {admin}: {completed} заявок, ⏱️ {avg_time:.1f} мин.\n"
        
        return stats_text

# ==================== РЕЙТИНГИ И АНАЛИТИКА ====================

class EnhancedRatingSystem:
    """⭐ Улучшенная система рейтингов и отзывов"""
    
    @staticmethod
    def save_rating(db_path: str, request_id: int, user_id: int, admin_id: int, admin_name: str, rating: int, comment: str = ""):
        """💾 Сохраняет оценку заявки"""
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO request_ratings (request_id, user_id, admin_id, admin_name, rating, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (request_id, user_id, admin_id, admin_name, rating, comment, datetime.now().isoformat()))
            conn.commit()

    @staticmethod
    def get_admin_rating(db_path: str, admin_id: int) -> Dict[str, Any]:
        """📈 Получает рейтинг администратора"""
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
        """📊 Получает статистику рейтингов за период"""
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
    """🔔 Расширенный менеджер умных уведомлений"""
    
    def __init__(self, bot):
        self.bot = bot
        self.user_preferences = {}  # В реальности хранить в БД
        
    async def send_smart_notification(self, user_id: int, message: str, priority: str = "normal"):
        """📨 Отправляет умное уведомление с учетом предпочтений пользователя"""
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
            logger.error(f"❌ Ошибка отправки уведомления: {e}")
            return False

    async def send_department_notification(self, department: str, message: str, exclude_admin: int = None):
        """👥 Отправляет уведомление всем администраторам отдела"""
        admin_ids = Config.ADMIN_CHAT_IDS.get(department, [])
        
        for admin_id in admin_ids:
            if admin_id != exclude_admin:  # Исключаем отправителя
                await self.send_smart_notification(admin_id, message, "normal")
    
    async def notify_new_request(self, context: ContextTypes.DEFAULT_TYPE, request: Dict):
        """🆕 Уведомляет о новой заявке"""
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
    """🤖 AI анализ текста заявок для автоматической категоризации"""
    
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
        """⚡ Возвращает анализ по умолчанию"""
        return {
            'suggested_department': '🏢 Общие',
            'suggested_urgency': '💤 НЕ СРОЧНО',
            'confidence_score': 0.0,
            'department_scores': {},
            'urgency_scores': {}
        }
    
    @classmethod
    def analyze_problem_text(cls, text: str) -> Dict[str, Any]:
        """🔍 Анализирует текст проблемы и предлагает категории"""
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
            logger.error(f"❌ Ошибка AI анализа: {e}")
            return cls.get_default_analysis()

# ==================== МЕНЕДЖЕР ШАБЛОНОВ ОТВЕТОВ ====================

class ResponseTemplateManager:
    """📝 Менеджер шаблонов ответов для администраторов"""
    
    @staticmethod
    def get_templates(department: str) -> List[Dict]:
        """📂 Получает шаблоны ответов для отдела"""
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
        """➕ Создает новый шаблон ответа"""
        with sqlite3.connect(Config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO response_templates (department, title, template_text, created_at)
                VALUES (?, ?, ?, ?)
            ''', (department, title, template_text, datetime.now().isoformat()))
            conn.commit()

    @staticmethod
    def get_template_buttons(department: str) -> List[List[InlineKeyboardButton]]:
        """⌨️ Создает кнопки шаблонов ответов"""
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
    """⚙️ Автоматизация рабочих процессов"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    async def check_timeout_requests(self, bot):
        """⏰ Проверяет просроченные заявки"""
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
            logger.error(f"❌ Ошибка проверки таймаутов: {e}")
    
    async def notify_timeout(self, bot, request):
        """🔔 Уведомляет о просроченной заявке"""
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
            logger.error(f"❌ Ошибка уведомления о таймауте: {e}")

# ==================== МЕНЕДЖЕР БЭКАПОВ ====================

class BackupManager:
    """💾 Менеджер резервного копирования"""
    
    @staticmethod
    def create_backup() -> str:
        """🔄 Создает резервную копию базы данных"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"backup/requests_backup_{timestamp}.db"
            
            # Создаем папку для бэкапов если нет
            os.makedirs("backup", exist_ok=True)
            
            # Копируем файл базы данных
            shutil.copy2(Config.DB_PATH, backup_file)
            
            logger.info(f"💾 Создан бэкап: {backup_file}")
            return backup_file
        except Exception as e:
            logger.error(f"❌ Ошибка создания бэкапа: {e}")
            return None
    
    @staticmethod
    def cleanup_old_backups(max_backups: int = 10):
        """🧹 Удаляет старые бэкапы"""
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
                logger.info(f"🗑️ Удален старый бэкап: {backup_path}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка очистки бэкапов: {e}")

    @staticmethod
    def list_backups() -> List[str]:
        """📂 Возвращает список доступных бэкапов"""
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
            logger.error(f"❌ Ошибка получения списка бэкапов: {e}")
            return []

# ==================== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ====================

# Инициализация улучшенной базы данных
db = EnhancedDatabase(Config.DB_PATH)
cached_stats = CachedStatistics(db)
notification_manager = EnhancedNotificationManager(None)  # Инициализируем позже

# ==================== ОСНОВНЫЕ КОМАНДЫ БОТА ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🚀 Обработчик команды /start"""
    user = update.message.from_user
    
    welcome_text = (
        "🎉 *Добро пожаловать в улучшенную систему заявок!*\n\n"
        "✨ *Новые возможности:*\n"
        "• 🤖 AI анализ текста заявок\n"
        "• 📊 Визуальная статистика с прогресс-барами\n"
        "• ⭐ Система рейтингов и отзывов\n"
        "• 🔔 Автоматические уведомления\n"
        "• 💾 Авто-бэкапы данных\n"
        "• 📝 Шаблоны ответов\n"
        "• 🗃️ История изменений\n\n"
        "🎯 *Выберите действие из меню ниже:*"
    )
    
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🏠 Показывает главное меню"""
    keyboard = [
        ["📝 Создать заявку", "📂 Мои заявки"],
        ["📊 Статистика", "🤖 AI Анализ"],
        ["⭐ Рейтинги", "🆘 Помощь"]
    ]
    
    # Добавляем админские кнопки для администраторов
    if Config.is_admin(update.message.from_user.id):
        keyboard.insert(1, ["👨‍💼 Админ панель", "📋 Все заявки"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🎯 *Главное меню улучшенной системы заявок*",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ПРОЦЕСС СОЗДАНИЯ ЗАЯВКИ ====================

# Состояния для создания заявки
REQUEST_PHONE, REQUEST_DEPARTMENT, REQUEST_PROBLEM, REQUEST_PHOTO = range(4)

async def new_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """📝 Начинает процесс создания новой заявки"""
    user = update.message.from_user
    
    context.user_data['request'] = {
        'user_id': user.id,
        'username': user.username or user.full_name
    }
    
    await update.message.reply_text(
        "📋 *Создание новой заявки*\n\n"
        "📞 Пожалуйста, введите ваш номер телефона:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_PHONE

async def request_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """📞 Обрабатывает номер телефона"""
    phone = update.message.text
    context.user_data['request']['phone'] = phone
    
    # Клавиатура выбора отдела
    keyboard = [
        ["💻 IT отдел", "🔧 Механика"],
        ["⚡ Электрика", "🏢 Общие"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🏢 Выберите отдел для заявки:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_DEPARTMENT

async def request_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """🏢 Обрабатывает выбор отдела"""
    department = update.message.text
    context.user_data['request']['department'] = department
    
    await update.message.reply_text(
        "🔧 Опишите вашу проблему подробно:\n\n"
        "💡 *Совет:* Опишите проблему как можно подробнее, "
        "это поможет AI автоматически определить срочность и категорию.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_PROBLEM

async def request_problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """🔧 Обрабатывает описание проблемы"""
    problem = update.message.text
    context.user_data['request']['problem'] = problem
    
    # AI анализ текста проблемы
    if Config.ENABLE_AI_ANALYSIS:
        analysis = AIAnalyzer.analyze_problem_text(problem)
        context.user_data['request']['ai_analysis'] = analysis
        
        # Предлагаем AI рекомендации
        if analysis['confidence_score'] > 0.3:
            suggestion_text = (
                f"🤖 *AI РЕКОМЕНДАЦИЯ:*\n\n"
                f"🏢 Отдел: {analysis['suggested_department']}\n"
                f"⏰ Срочность: {analysis['suggested_urgency']}\n"
                f"🎯 Уверенность: {analysis['confidence_score']:.1%}\n\n"
                f"Использовать рекомендации AI?"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Да", callback_data="use_ai_yes"),
                    InlineKeyboardButton("❌ Нет", callback_data="use_ai_no")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                suggestion_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            return REQUEST_PHOTO
    
    # Если AI отключен или низкая уверенность, продолжаем
    return await create_request_final(update, context)

async def use_ai_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """🤖 Обрабатывает выбор использования AI рекомендаций"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "use_ai_yes":
        analysis = context.user_data['request']['ai_analysis']
        context.user_data['request']['department'] = analysis['suggested_department']
        context.user_data['request']['urgency'] = analysis['suggested_urgency']
        
        await query.edit_message_text(
            f"✅ Использую AI рекомендации:\n"
            f"🏢 Отдел: {analysis['suggested_department']}\n"
            f"⏰ Срочность: {analysis['suggested_urgency']}"
        )
    
    return await create_request_final(update, context)

async def create_request_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """✅ Завершает создание заявки"""
    try:
        request_data = context.user_data['request']
        
        # Создаем заявку в базе данных
        request_id = db.add_request(
            user_id=request_data['user_id'],
            username=request_data['username'],
            phone=request_data['phone'],
            department=request_data['department'],
            problem=request_data['problem'],
            urgency=request_data.get('urgency', '💤 НЕ СРОЧНО')
        )
        
        # Отправляем уведомление администраторам
        notification_manager.bot = context.bot
        await notification_manager.notify_new_request(context, {
            'id': request_id,
            'username': request_data['username'],
            'phone': request_data['phone'],
            'department': request_data['department'],
            'problem': request_data['problem'],
            'urgency': request_data.get('urgency', '💤 НЕ СРОЧНО'),
            'created_at': datetime.now().isoformat()
        })
        
        success_text = (
            f"🎉 *Заявка #{request_id} успешно создана!*\n\n"
            f"🏢 *Отдел:* {request_data['department']}\n"
            f"⏰ *Срочность:* {request_data.get('urgency', '💤 НЕ СРОЧНО')}\n"
            f"📞 *Ваш телефон:* {request_data['phone']}\n\n"
            f"🔧 *Проблема:* {request_data['problem']}\n\n"
            f"📊 Вы можете отслеживать статус заявки в разделе \"📂 Мои заявки\""
        )
        
        await context.bot.send_message(
            chat_id=request_data['user_id'],
            text=success_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Очищаем данные
        context.user_data.clear()
        
        await show_main_menu(update, context)
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания заявки: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при создании заявки. Пожалуйста, попробуйте позже."
        )
        return ConversationHandler.END

async def cancel_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """❌ Отменяет создание заявки"""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=ReplyKeyboardMarkup([["📝 Создать заявку"]], resize_keyboard=True)
    )
    return ConversationHandler.END

# ==================== УЛУЧШЕННЫЕ КОМАНДЫ АДМИНИСТРИРОВАНИЯ ====================

async def enhanced_statistics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📊 Показывает расширенную статистику с текстовой визуализацией"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой статистике.")
        return
    
    try:
        # Показываем сообщение о загрузке
        loading_msg = await update.message.reply_text("📊 *Загружаем статистику...*", parse_mode=ParseMode.MARKDOWN)
        
        stats = cached_stats.get_statistics_cached()
        
        # Создаем текстовую статистику
        stats_text = TextVisualizer.create_statistics_text(stats)
        
        await update.message.reply_text(
            stats_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Удаляем сообщение о загрузке
        await context.bot.delete_message(
            chat_id=update.message.chat_id,
            message_id=loading_msg.message_id
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка показа статистики: {e}")
        await update.message.reply_text("❌ Ошибка при генерации статистики")

async def ai_analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🤖 AI анализ текста проблемы"""
    if not context.args:
        await update.message.reply_text(
            "🤖 *AI АНАЛИЗ ТЕКСТА*\n\n"
            "📝 Использование: `/ai_analysis ваш текст проблемы`\n\n"
            "💡 Пример: `/ai_analysis не работает компьютер и принтер, срочно нужно починить`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = ' '.join(context.args)
    analysis = AIAnalyzer.analyze_problem_text(text)
    
    analysis_text = (
        f"🤖 *AI АНАЛИЗ ТЕКСТА*\n\n"
        f"📝 *Исходный текст:* {text}\n\n"
        f"💡 *Рекомендации:*\n"
    )
    
    if analysis['suggested_department']:
        analysis_text += f"🏢 *Отдел:* {analysis['suggested_department']}\n"
        analysis_text += f"🎯 *Уверенность:* {analysis['confidence_score']:.1%}\n\n"
    
    analysis_text += f"⏰ *Срочность:* {analysis['suggested_urgency']}\n\n"
    
    if analysis['department_scores']:
        analysis_text += "🔍 *Ключевые слова:*\n"
        for dept, score in analysis['department_scores'].items():
            if score > 0:
                analysis_text += f"• {dept}: {score} совпадений\n"
    
    await update.message.reply_text(
        analysis_text,
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== СИСТЕМА РЕЙТИНГОВ ====================

async def request_rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⭐ Обработка оценки заявки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith('rate_'):
        _, request_id, rating = data.split('_')
        request_id = int(request_id)
        rating = int(rating)
        
        # Сохраняем оценку
        request = db.get_request_cached(request_id)
        if request and request['user_id'] == user_id:
            EnhancedRatingSystem.save_rating(
                Config.DB_PATH, request_id, user_id, 
                request.get('assigned_admin', 'Unknown'),
                request.get('assigned_admin', 'Unknown'), 
                rating
            )
            
            await query.edit_message_text(
                f"⭐ *Спасибо за оценку!*\n\n"
                f"📋 Заявка #{request_id}\n"
                f"⭐ Оценка: {'★' * rating}{'☆' * (5 - rating)}\n\n"
                f"💼 Ваш отзыв помогает нам улучшать сервис!",
                parse_mode=ParseMode.MARKDOWN
            )

async def ratings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📈 Показывает рейтинги администраторов"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой информации.")
        return
    
    try:
        rating_stats = EnhancedRatingSystem.get_rating_stats(Config.DB_PATH)
        
        if not rating_stats['period_ratings']:
            await update.message.reply_text("📊 Рейтингов пока нет.")
            return
        
        ratings_text = "⭐ *РЕЙТИНГИ АДМИНИСТРАТОРОВ* (за 30 дней)\n\n"
        
        for admin in rating_stats['period_ratings']:
            stars = "★" * int(admin['avg_rating']) + "☆" * (5 - int(admin['avg_rating']))
            ratings_text += (
                f"👤 *{admin['admin_name']}*\n"
                f"⭐ {stars} ({admin['avg_rating']}/5)\n"
                f"📊 Оценок: {admin['total_ratings']}\n\n"
            )
        
        ratings_text += f"📈 *Средний рейтинг:* {rating_stats['overall_avg']}/5"
        
        await update.message.reply_text(
            ratings_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка показа рейтингов: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке рейтингов")

def create_rating_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """⭐ Создает клавиатуру для оценки заявки"""
    keyboard = []
    for i in range(1, 6):
        keyboard.append([
            InlineKeyboardButton(
                "★" * i + "☆" * (5 - i), 
                callback_data=f"rate_{request_id}_{i}"
            )
        ])
    return InlineKeyboardMarkup(keyboard)

# ==================== УЛУЧШЕННЫЕ УВЕДОМЛЕНИЯ ====================

async def send_enhanced_notification(context: ContextTypes.DEFAULT_TYPE, user_id: int, 
                                   request_id: int, status: str, admin_name: str = None):
    """🔔 Отправляет улучшенное уведомление"""
    
    status_messages = {
        'in_progress': {
            'title': '🔄 Заявка взята в работу',
            'message': f'👨‍💼 Исполнитель: {admin_name}',
            'emoji': '👨‍💼'
        },
        'completed': {
            'title': '✅ Заявка выполнена',
            'message': '⭐ Пожалуйста, оцените качество работы',
            'emoji': '⭐'
        }
    }
    
    if status not in status_messages:
        return
    
    msg_info = status_messages[status]
    
    message_text = (
        f"{msg_info['emoji']} *{msg_info['title']}*\n\n"
        f"📋 *Заявка #{request_id}*\n"
        f"{msg_info['message']}\n\n"
    )
    
    if status == 'completed':
        message_text += "⭐ *Оцените работу исполнителя:*"
    
    try:
        if status == 'completed':
            await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
                reply_markup=create_rating_keyboard(request_id),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления: {e}")

# ==================== АВТОМАТИЧЕСКИЕ ЗАДАЧИ ====================

async def scheduled_backup(context: ContextTypes.DEFAULT_TYPE):
    """💾 Автоматическое создание бэкапов"""
    try:
        backup_file = BackupManager.create_backup()
        if backup_file:
            # Уведомляем супер-админов
            for admin_id in Config.SUPER_ADMIN_IDS:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🔄 *Автоматический бэкап создан*\n\n📁 Файл: `{backup_file}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            BackupManager.cleanup_old_backups()
    except Exception as e:
        logger.error(f"❌ Ошибка автоматического бэкапа: {e}")

async def check_timeouts(context: ContextTypes.DEFAULT_TYPE):
    """⏰ Проверка просроченных заявок"""
    try:
        automator = WorkflowAutomator(Config.DB_PATH)
        await automator.check_timeout_requests(context.bot)
    except Exception as e:
        logger.error(f"❌ Ошибка проверки таймаутов: {e}")

# ==================== КОМАНДА ВОССТАНОВЛЕНИЯ ИЗ БЭКАПА ====================

async def restore_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🔄 Восстановление из бэкапа (только для супер-админов)"""
    user_id = update.message.from_user.id
    
    if user_id not in Config.SUPER_ADMIN_IDS:
        await update.message.reply_text("❌ Только для супер-администраторов.")
        return
    
    backups = BackupManager.list_backups()
    
    if not backups:
        await update.message.reply_text("📭 Бэкапы не найдены.")
        return
    
    keyboard = []
    for backup in backups[:5]:  # Показываем последние 5 бэкапов
        backup_name = os.path.basename(backup)
        keyboard.append([
            InlineKeyboardButton(
                f"📁 {backup_name}",
                callback_data=f"restore_{backup}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="restore_cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔄 *Восстановление из бэкапа*\n\n"
        "📂 Выберите бэкап для восстановления:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def restore_backup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🔄 Обработка выбора бэкапа для восстановления"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "restore_cancel":
        await query.edit_message_text("❌ Восстановление отменено.")
        return
    
    if query.data.startswith('restore_'):
        backup_path = query.data.replace('restore_', '')
        
        try:
            # Создаем резервную копию текущей базы
            current_backup = BackupManager.create_backup()
            
            # Восстанавливаем из выбранного бэкапа
            shutil.copy2(backup_path, Config.DB_PATH)
            
            # Очищаем кэш
            cached_stats.clear_cache()
            
            await query.edit_message_text(
                f"✅ *База данных восстановлена!*\n\n"
                f"📁 Восстановлено из: `{os.path.basename(backup_path)}`\n"
                f"💾 Текущая база сохранена как: `{os.path.basename(current_backup)}`\n\n"
                f"🔄 Кэш статистики очищен.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"❌ Ошибка восстановления бэкапа: {e}")
            await query.edit_message_text(
                f"❌ Ошибка восстановления: {str(e)}"
            )

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================

async def show_user_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📂 Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    requests = db.get_user_requests(user_id)
    
    if not requests:
        await update.message.reply_text("📭 У вас пока нет заявок.")
        return
    
    requests_text = "📂 *ВАШИ ЗАЯВКИ*\n\n"
    
    for req in requests[:10]:  # Показываем последние 10 заявок
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }.get(req['status'], '❓')
        
        requests_text += (
            f"{status_emoji} *Заявка #{req['id']}*\n"
            f"🏢 {req['department']}\n"
            f"📝 {req['problem'][:50]}...\n"
            f"⏰ {req['created_at'][:10]}\n"
            f"🔸 Статус: {req['status']}\n\n"
        )
    
    await update.message.reply_text(requests_text, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🆘 Показывает справку"""
    help_text = (
        "🆘 *ПОМОЩЬ ПО КОМАНДАМ*\n\n"
        "🎯 *Основные команды:*\n"
        "• /start - 🏠 Главное меню\n"
        "• /new_request - 📝 Создать заявку\n"
        "• /my_requests - 📂 Мои заявки\n"
        "• /help - 🆘 Помощь\n\n"
        "🤖 *Улучшенные функции:*\n"
        "• /ai_analysis [текст] - 🤖 AI анализ проблемы\n"
        "• /advanced_stats - 📊 Расширенная статистика\n"
        "• /ratings - ⭐ Рейтинги администраторов\n\n"
        "📊 *Для администраторов:*\n"
        "• /stats - 📈 Статистика заявок\n"
        "• /requests - 📋 Список заявок\n"
        "• /assign [id] - 👨‍💼 Взять заявку\n"
        "• /complete [id] - ✅ Завершить заявку\n"
        "• /restore_backup - 🔄 Восстановить из бэкапа\n"
        "• /clear_cache - 🧹 Очистить кэш\n\n"
        "💡 *Совет:* Используйте кнопки меню для быстрого доступа к функциям!"
    )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """💬 Обрабатывает текстовые сообщения из меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Основные кнопки для всех пользователей
    if text == "📊 Статистика":
        await enhanced_statistics_command(update, context)
    elif text == "🤖 AI Анализ":
        await ai_analysis_menu(update, context)
    elif text == "⭐ Рейтинги":
        await ratings_command(update, context)
    elif text == "📂 Мои заявки":
        await show_user_requests(update, context)
    elif text == "📝 Создать заявку":
        await new_request_command(update, context)
    elif text == "🆘 Помощь":
        await help_command(update, context)
    
    # Админские кнопки
    elif text == "👨‍💼 Админ панель" and Config.is_admin(user_id):
        await admin_panel_command(update, context)
    elif text == "📋 Все заявки" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "📊 Обновить статистику" and Config.is_admin(user_id):
        await enhanced_statistics_command(update, context)
    elif text == "📋 Новые заявки" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "🔄 Бэкап" and Config.is_admin(user_id):
        await create_backup_command(update, context)
    elif text == "🎯 Главное меню":
        await show_main_menu(update, context)
    else:
        await update.message.reply_text("🤔 Пожалуйста, используйте кнопки меню или команды.")

async def ai_analysis_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🤖 Меню AI анализа"""
    await update.message.reply_text(
        "🤖 *AI АНАЛИЗ ТЕКСТА*\n\n"
        "📝 Использование: `/ai_analysis ваш текст проблемы`\n\n"
        "💡 Пример: `/ai_analysis не работает компьютер и принтер, срочно нужно починить`\n\n"
        "🎯 Или просто напишите текст проблемы для анализа:",
        parse_mode=ParseMode.MARKDOWN
    )

async def create_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """💾 Создание бэкапа вручную"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    try:
        backup_file = BackupManager.create_backup()
        if backup_file:
            await update.message.reply_text(
                f"✅ *Бэкап создан успешно!*\n\n"
                f"📁 Файл: `{backup_file}`\n\n"
                f"💾 Для восстановления используйте /restore_backup",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("❌ Не удалось создать бэкап.")
    except Exception as e:
        logger.error(f"❌ Ошибка создания бэкапа: {e}")
        await update.message.reply_text("❌ Ошибка при создании бэкапа.")

# ==================== АДМИНСКИЕ КОМАНДЫ ====================

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👨‍💼 Показывает админскую панель"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    admin_text = (
        "👨‍💼 *АДМИН ПАНЕЛЬ*\n\n"
        "📊 *Статистика:*\n"
        "• /stats - 📈 Расширенная статистика\n"
        "• /ratings - ⭐ Рейтинги администраторов\n\n"
        "📋 *Управление заявками:*\n"
        "• /requests - 📋 Список новых заявок\n"
        "• /assign [id] - 👨‍💼 Взять заявку в работу\n"
        "• /complete [id] - ✅ Завершить заявку\n\n"
        "⚙️ *Система:*\n"
        "• /restore_backup - 🔄 Восстановление из бэкапа\n"
        "• /clear_cache - 🧹 Очистка кэша\n\n"
        "💡 *Быстрые действия:*"
    )
    
    keyboard = [
        ["📊 Обновить статистику", "📋 Новые заявки"],
        ["⭐ Рейтинги", "🔄 Бэкап"],
        ["🎯 Главное меню"]
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
    
    requests = db.get_requests(status='new')
    if not requests:
        await update.message.reply_text("📭 Новых заявок нет.")
        return
    
    requests_text = "🆕 *НОВЫЕ ЗАЯВКИ*\n\n"
    
    for req in requests[:10]:
        requests_text += (
            f"📋 *Заявка #{req['id']}*\n"
            f"👤 {req['username']} | 📞 {req['phone']}\n"
            f"🏢 {req['department']}\n"
            f"🔧 {req['problem'][:80]}...\n"
            f"⏰ {req['urgency']}\n"
            f"🕒 {req['created_at'][:16]}\n\n"
        )
    
    await update.message.reply_text(requests_text, parse_mode=ParseMode.MARKDOWN)

async def assign_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👨‍💼 Взять заявку в работу"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    if not context.args:
        await update.message.reply_text("📝 Использование: /assign <id заявки>")
        return
    
    try:
        request_id = int(context.args[0])
        request = db.get_request(request_id)
        
        if not request:
            await update.message.reply_text("❌ Заявка не найдена.")
            return
        
        if request['status'] != 'new':
            await update.message.reply_text("❌ Заявка уже в работе или завершена.")
            return
        
        # Обновляем статус заявки
        admin_name = update.message.from_user.full_name
        db.update_request_status(request_id, 'in_progress', admin_name)
        
        # Отправляем уведомление пользователю
        await send_enhanced_notification(
            context, request['user_id'], request_id, 'in_progress', admin_name
        )
        
        await update.message.reply_text(
            f"✅ Заявка #{request_id} взята в работу!\n"
            f"👨‍💼 Исполнитель: {admin_name}"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Неверный ID заявки.")
    except Exception as e:
        logger.error(f"❌ Ошибка взятия заявки: {e}")
        await update.message.reply_text("❌ Ошибка при взятии заявки.")

async def complete_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """✅ Завершить заявку"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    if not context.args:
        await update.message.reply_text("📝 Использование: /complete <id заявки>")
        return
    
    try:
        request_id = int(context.args[0])
        request = db.get_request(request_id)
        
        if not request:
            await update.message.reply_text("❌ Заявка не найдена.")
            return
        
        if request['status'] != 'in_progress':
            await update.message.reply_text("❌ Заявка не в работе.")
            return
        
        # Обновляем статус заявки
        db.update_request_status(request_id, 'completed')
        
        # Отправляем уведомление с оценкой пользователю
        await send_enhanced_notification(
            context, request['user_id'], request_id, 'completed'
        )
        
        await update.message.reply_text(f"✅ Заявка #{request_id} завершена!")
        
    except ValueError:
        await update.message.reply_text("❌ Неверный ID заявки.")
    except Exception as e:
        logger.error(f"❌ Ошибка завершения заявки: {e}")
        await update.message.reply_text("❌ Ошибка при завершении заявки.")

async def clear_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🧹 Очищает кэш статистики"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    try:
        cached_stats.clear_cache()
        await update.message.reply_text("✅ Кэш статистики очищен!")
    except Exception as e:
        logger.error(f"❌ Ошибка очистки кэша: {e}")
        await update.message.reply_text("❌ Ошибка при очистке кэша.")

# ==================== НАСТРОЙКА ОБРАБОТЧИКОВ ====================

def setup_handlers(application: Application):
    """🔧 Настройка всех обработчиков"""
    
    # Обработчик создания заявки (ConversationHandler)
    request_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new_request", new_request_command),
                     MessageHandler(filters.Text("📝 Создать заявку"), new_request_command)],
        states={
            REQUEST_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_phone)],
            REQUEST_DEPARTMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_department)],
            REQUEST_PROBLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_problem)],
            REQUEST_PHOTO: [CallbackQueryHandler(use_ai_recommendation, pattern="^use_ai_")]
        },
        fallbacks=[CommandHandler("cancel", cancel_request)]
    )
    
    # Основные команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_requests", show_user_requests))
    application.add_handler(request_conv_handler)
    
    # AI и аналитика
    application.add_handler(CommandHandler("ai_analysis", ai_analysis_command))
    application.add_handler(CommandHandler("advanced_stats", enhanced_statistics_command))
    application.add_handler(CommandHandler("ratings", ratings_command))
    
    # Админские команды
    application.add_handler(CommandHandler("stats", enhanced_statistics_command))
    application.add_handler(CommandHandler("requests", admin_requests_command))
    application.add_handler(CommandHandler("assign", assign_request_command))
    application.add_handler(CommandHandler("complete", complete_request_command))
    application.add_handler(CommandHandler("restore_backup", restore_backup_command))
    application.add_handler(CommandHandler("clear_cache", clear_cache_command))
    
    # Обработчики callback
    application.add_handler(CallbackQueryHandler(request_rating_callback, pattern="^rate_"))
    application.add_handler(CallbackQueryHandler(restore_backup_callback, pattern="^restore_"))
    application.add_handler(CallbackQueryHandler(use_ai_recommendation, pattern="^use_ai_"))
    
    # Обработчики текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

def setup_automated_tasks(application: Application):
    """⏰ Настройка автоматических задач"""
    job_queue = application.job_queue
    
    if job_queue:
        # Ежедневный бэкап в 2:00
        job_queue.run_daily(
            scheduled_backup,
            time=time(hour=2, minute=0),
            name="daily_backup"
        )
        
        # Проверка таймаутов каждые 6 часов
        job_queue.run_repeating(
            check_timeouts,
            interval=timedelta(hours=6),
            first=10
        )

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================

def enhanced_main() -> None:
    """🚀 Улучшенный запуск бота"""
    try:
        print("🔄 Запуск улучшенного бота...")
        
        # Проверка конфигурации
        Config.validate_config()
        print("✅ Конфигурация проверена")
        
        if not Config.BOT_TOKEN:
            logger.error("❌ Токен бота не загружен!")
            print("❌ Токен бота не найден!")
            return
        
        # Инициализация базы данных
        print("🗄️ Инициализация базы данных...")
        db = EnhancedDatabase(Config.DB_PATH)
        print("✅ База данных готова")
        
        # Создание приложения
        print("🤖 Создание приложения...")
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Инициализируем менеджер уведомлений
        global notification_manager
        notification_manager = EnhancedNotificationManager(application.bot)
        
        # Настройка задач и обработчиков
        print("🔧 Настройка обработчиков...")
        setup_automated_tasks(application)
        setup_handlers(application)
        print("✅ Все компоненты настроены")
        
        logger.info("🚀 Улучшенный бот успешно запущен!")
        print("🎉 УЛУЧШЕННЫЙ бот успешно запущен!")
        print("✨ ДОБАВЛЕННЫЕ ВОЗМОЖНОСТИ:")
        print("   • 🤖 AI анализ текста заявок")
        print("   • 📊 Текстовая статистика с прогресс-барами") 
        print("   • ⭐ Система рейтингов и отзывов")
        print("   • 🔔 Автоматические задачи и уведомления")
        print("   • 💾 Расписание авто-бэкапов")
        print("   • ⏰ Умные уведомления")
        print("   • 📈 Расширенная аналитика")
        print("   • ⚙️ Автоматизация рабочих процессов")
        print("   • 🗃️ Кэширование для производительности")
        print("   • 👨‍💼 Полный набор админских команд")
        print("   • 📝 Многошаговое создание заявок")
        print("   • 🗄️ Система миграций базы данных")
        print("   • 🔄 Восстановление из бэкапов")
        print("   • 📋 Шаблоны ответов")
        print("\n🚀 Бот готов к работе!")
        
        # Запуск бота
        print("🔄 Запуск опроса...")
        application.run_polling()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска улучшенного бота: {e}")
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    enhanced_main()
