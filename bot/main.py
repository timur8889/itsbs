import logging
import sqlite3
import os
import json
import re
import time
import asyncio
import shutil
import aiohttp
import pandas as pd
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

# ==================== УЛУЧШЕННАЯ БАЗА ДАННЫХ ====================

class EnhancedDatabase:
    """Улучшенный класс для работы с базой данных"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_enhanced_db()
    
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
            conn.commit()
            return cursor.lastrowid
    
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
        """Получает базовую статистику заявок"""
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

class NotificationManager:
    """Менеджер умных уведомлений"""
    
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
    def analyze_problem_text(cls, text: str) -> Dict[str, Any]:
        """Анализирует текст проблемы и предлагает категории"""
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

# ==================== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ====================

# Инициализация улучшенной базы данных
db = EnhancedDatabase(Config.DB_PATH)

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
        "• 💾 Авто-бэкапы данных\n\n"
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
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🎯 *Главное меню улучшенной системы заявок*",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== УЛУЧШЕННЫЕ КОМАНДЫ АДМИНИСТРИРОВАНИЯ ====================

async def enhanced_statistics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расширенную статистику с графиками"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой статистике.")
        return
    
    try:
        # Показываем сообщение о загрузке
        loading_msg = await update.message.reply_text("📊 *Загружаем статистику...*", parse_mode=ParseMode.MARKDOWN)
        
        stats = db.get_advanced_statistics()
        
        # Создаем график
        plot_buffer = DataVisualizer.create_statistics_plot(stats)
        
        if plot_buffer:
            # Отправляем график
            await update.message.reply_photo(
                photo=InputFile(plot_buffer, filename='statistics.png'),
                caption="📊 *ВИЗУАЛИЗАЦИЯ СТАТИСТИКИ*",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("❌ Не удалось создать график статистики")
        
        # Текстовая статистика
        stats_text = (
            f"📈 *РАСШИРЕННАЯ СТАТИСТИКА*\n\n"
            f"📊 *Общая эффективность:* {stats.get('efficiency', 0):.1f}%\n"
            f"⏱️ *Среднее время выполнения:* {stats.get('avg_completion_time_minutes', 0):.1f} мин.\n\n"
            f"🏢 *По отделам:*\n"
        )
        
        for dept, dept_stats in stats.get('by_department', {}).items():
            total = dept_stats.get('total', 0)
            completed = dept_stats.get('completed', 0)
            efficiency = (completed / total * 100) if total > 0 else 0
            stats_text += f"• {dept}: {completed}/{total} ({efficiency:.1f}%)\n"
        
        if stats.get('admin_stats'):
            stats_text += f"\n👨‍💼 *Эффективность администраторов:*\n"
            for admin, admin_stats in stats['admin_stats'].items():
                completed = admin_stats.get('completed_requests', 0)
                avg_time = admin_stats.get('avg_completion_time', 0)
                stats_text += f"• {admin}: {completed} заявок, {avg_time:.1f} мин.\n"
        
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
        logger.error(f"Ошибка показа статистики: {e}")
        await update.message.reply_text("❌ Ошибка при генерации статистики")

async def ai_analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """AI анализ текста проблемы"""
    if not context.args:
        await update.message.reply_text(
            "🤖 *AI АНАЛИЗ ТЕКСТА*\n\n"
            "Использование: `/ai_analysis ваш текст проблемы`\n\n"
            "Пример: `/ai_analysis не работает компьютер и принтер, срочно нужно починить`",
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
    """Обработка оценки заявки"""
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
                f"Ваш отзыв помогает нам улучшать сервис! 💼",
                parse_mode=ParseMode.MARKDOWN
            )

async def ratings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает рейтинги администраторов"""
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
        logger.error(f"Ошибка показа рейтингов: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке рейтингов")

def create_rating_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для оценки заявки"""
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
    """Отправляет улучшенное уведомление"""
    
    status_messages = {
        'in_progress': {
            'title': '🔄 Заявка взята в работу',
            'message': f'Исполнитель: {admin_name}',
            'emoji': '👨‍💼'
        },
        'completed': {
            'title': '✅ Заявка выполнена',
            'message': 'Пожалуйста, оцените качество работы',
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
        logger.error(f"Ошибка отправки уведомления: {e}")

# ==================== АВТОМАТИЧЕСКИЕ ЗАДАЧИ ====================

async def scheduled_backup(context: ContextTypes.DEFAULT_TYPE):
    """Автоматическое создание бэкапов"""
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
        logger.error(f"Ошибка автоматического бэкапа: {e}")

async def check_timeouts(context: ContextTypes.DEFAULT_TYPE):
    """Проверка просроченных заявок"""
    try:
        automator = WorkflowAutomator(Config.DB_PATH)
        await automator.check_timeout_requests(context.bot)
    except Exception as e:
        logger.error(f"Ошибка проверки таймаутов: {e}")

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================

async def show_user_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    requests = db.get_user_requests(user_id)
    
    if not requests:
        await update.message.reply_text("📭 У вас пока нет заявок.")
        return
    
    requests_text = "📋 *ВАШИ ЗАЯВКИ*\n\n"
    
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
    """Показывает справку"""
    help_text = (
        "🆘 *ПОМОЩЬ ПО КОМАНДАМ*\n\n"
        "🎯 *Основные команды:*\n"
        "• /start - Главное меню\n"
        "• /new_request - Создать заявку\n"
        "• /my_requests - Мои заявки\n"
        "• /help - Помощь\n\n"
        "🤖 *Улучшенные функции:*\n"
        "• /ai_analysis [текст] - AI анализ проблемы\n"
        "• /advanced_stats - Расширенная статистика\n"
        "• /ratings - Рейтинги администраторов\n\n"
        "📊 *Для администраторов:*\n"
        "• /stats - Статистика заявок\n"
        "• /requests - Список заявок\n"
        "• /assign [id] - Взять заявку\n"
        "• /complete [id] - Завершить заявку\n\n"
        "💡 *Совет:* Используйте кнопки меню для быстрого доступа к функциям!"
    )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения из меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if text == "📊 Статистика":
        await enhanced_statistics_command(update, context)
    elif text == "🤖 AI Анализ":
        await update.message.reply_text(
            "🤖 *AI АНАЛИЗ ТЕКСТА*\n\n"
            "Использование: `/ai_analysis ваш текст проблемы`\n\n"
            "Пример: `/ai_analysis не работает компьютер и принтер, срочно нужно починить`",
            parse_mode=ParseMode.MARKDOWN
        )
    elif text == "⭐ Рейтинги":
        await ratings_command(update, context)
    elif text == "📋 Мои заявки":
        await show_user_requests(update, context)
    elif text == "📋 Создать заявку":
        await update.message.reply_text("Для создания заявки используйте команду /new_request")
    elif text == "🆘 Помощь":
        await help_command(update, context)
    else:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню или команды.")

# ==================== АДМИНСКИЕ КОМАНДЫ ====================

async def admin_requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает заявки для администратора"""
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
    """Взять заявку в работу"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /assign <id заявки>")
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
        logger.error(f"Ошибка взятия заявки: {e}")
        await update.message.reply_text("❌ Ошибка при взятии заявки.")

async def complete_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Завершить заявку"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /complete <id заявки>")
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
        logger.error(f"Ошибка завершения заявки: {e}")
        await update.message.reply_text("❌ Ошибка при завершении заявки.")

# ==================== НАСТРОЙКА ОБРАБОТЧИКОВ ====================

def setup_handlers(application: Application):
    """Настройка всех обработчиков"""
    
    # Основные команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_requests", show_user_requests))
    
    # AI и аналитика
    application.add_handler(CommandHandler("ai_analysis", ai_analysis_command))
    application.add_handler(CommandHandler("advanced_stats", enhanced_statistics_command))
    application.add_handler(CommandHandler("ratings", ratings_command))
    
    # Админские команды
    application.add_handler(CommandHandler("stats", enhanced_statistics_command))
    application.add_handler(CommandHandler("requests", admin_requests_command))
    application.add_handler(CommandHandler("assign", assign_request_command))
    application.add_handler(CommandHandler("complete", complete_request_command))
    
    # Обработчики callback (рейтинги)
    application.add_handler(CallbackQueryHandler(request_rating_callback, pattern="^rate_"))
    
    # Обработчики текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

def setup_automated_tasks(application: Application):
    """Настройка автоматических задач"""
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
    """Улучшенный запуск бота"""
    try:
        Config.validate_config()
        
        if not Config.BOT_TOKEN:
            logger.error("❌ Токен бота не загружен!")
            return
        
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Настройка задач и обработчиков
        setup_automated_tasks(application)
        setup_handlers(application)
        
        logger.info("🚀 Улучшенный бот успешно запущен!")
        print("✅ УЛУЧШЕННЫЙ бот успешно запущен!")
        print("🎯 ДОБАВЛЕННЫЕ ВОЗМОЖНОСТИ:")
        print("   • 🤖 AI анализ текста заявок")
        print("   • 📊 Визуальная статистика с графиками") 
        print("   • ⭐ Система рейтингов и отзывов")
        print("   • 🔄 Автоматические задачи и уведомления")
        print("   • 💾 Расписание авто-бэкапов")
        print("   • ⏰ Умные уведомления")
        print("   • 📈 Расширенная аналитика")
        print("   • 🔧 Автоматизация рабочих процессов")
        print("   • 🗃️ Кэширование для производительности")
        print("   • 👨‍💼 Полный набор админских команд")
        print("\n🚀 Бот готов к работе!")
        
        application.run_polling()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска улучшенного бота: {e}")
        print(f"❌ Критическая ошибка: {e}")

if __name__ == '__main__':
    enhanced_main()
