import logging
import sqlite3
import os
import json
import re
import threading
import shutil
import tempfile
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

# ==================== ДОБАВЛЕННЫЕ ИМПОРТЫ ДЛЯ GOOGLE SHEETS ====================
try:
    import gspread
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import Request
    import pandas as pd
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    GOOGLE_SHEETS_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("⚠️ Библиотеки Google Sheets не установлены")

# ==================== КОНФИГУРАЦИЯ ====================

# БЕЗОПАСНОЕ получение токена из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_IDS = [int(x) for x in os.getenv('ADMIN_CHAT_IDS', '').split(',') if x]

# Новые переменные для Google Sheets
GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')  # JSON credentials
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')  # ID таблицы
GOOGLE_SHEET_NAME = 'Заявки'  # Название листа

# Проверка обязательных переменных
if not BOT_TOKEN:
    logging.error("❌ BOT_TOKEN не установлен! Установите переменную окружения BOT_TOKEN")
    exit(1)
if not ADMIN_CHAT_IDS:
    logging.error("❌ ADMIN_CHAT_IDS не установлены! Установите переменную окружения ADMIN_CHAT_IDS")
    exit(1)

# Расширенные настройки
MAX_REQUESTS_PER_HOUR = 15
BACKUP_RETENTION_DAYS = 30
AUTO_BACKUP_HOUR = 3
AUTO_BACKUP_MINUTE = 0
REQUEST_TIMEOUT_HOURS = 24
SYNC_TO_SHEETS = bool(GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SHEET_ID and GOOGLE_SHEETS_AVAILABLE)  # Автосинхронизация

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

# ==================== БАЗОВЫЕ КЛАССЫ (ДОБАВЛЕНО ДЛЯ СОВМЕСТИМОСТИ) ====================

class Validators:
    """Базовый класс валидации"""
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
    """Базовый менеджер бэкапов"""
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
    """Система ограничения запросов"""
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
    """Базовая база данных"""
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных"""
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
        """Сохраняет заявку в базу данных"""
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
        """Получает заявки по статусу"""
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
        """Получает статистику за указанный период"""
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

# ==================== КЛАСС ДЛЯ GOOGLE SHEETS ====================

class GoogleSheetsManager:
    """Менеджер для работы с Google Sheets"""
    
    def __init__(self, credentials_json: str, sheet_id: str, sheet_name: str = 'Заявки'):
        self.credentials_json = credentials_json
        self.sheet_id = sheet_id
        self.sheet_name = sheet_name
        self.client = None
        self.sheet = None
        self.is_connected = False
        self._connect()
    
    def _connect(self):
        """Подключение к Google Sheets"""
        try:
            if not self.credentials_json or not self.sheet_id:
                logger.warning("⚠️ Google Sheets не настроен: отсутствуют credentials или sheet_id")
                return
            
            if not GOOGLE_SHEETS_AVAILABLE:
                logger.warning("⚠️ Библиотеки Google Sheets не установлены")
                return
            
            # Парсим JSON credentials из переменной окружения
            creds_dict = json.loads(self.credentials_json)
            creds = Credentials.from_service_account_info(creds_dict)
            
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_key(self.sheet_id).worksheet(self.sheet_name)
            self.is_connected = True
            logger.info("✅ Успешное подключение к Google Sheets")
            
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Google Sheets: {e}")
            self.is_connected = False
    
    def _ensure_headers(self):
        """Создает заголовки таблицы если их нет"""
        if not self.is_connected:
            return False
        
        try:
            current_data = self.sheet.get_all_records()
            if not current_data:
                headers = [
                    'ID', 'Статус', 'Имя', 'Телефон', 'Участок', 'Тип системы',
                    'Проблема', 'Срочность', 'Фото', 'ID пользователя', 
                    'Username', 'Создано', 'Обновлено', 'Исполнитель', 'Завершено'
                ]
                self.sheet.append_row(headers)
                logger.info("✅ Заголовки таблицы созданы")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка создания заголовков: {e}")
            return False
    
    def add_request(self, request_data: Dict) -> bool:
        """Добавляет заявку в таблицу"""
        if not self.is_connected:
            return False
        
        try:
            if not self._ensure_headers():
                return False
            
            row_data = [
                request_data.get('id', ''),
                request_data.get('status', 'new'),
                request_data.get('name', ''),
                request_data.get('phone', ''),
                request_data.get('plot', ''),
                request_data.get('system_type', ''),
                request_data.get('problem', ''),
                request_data.get('urgency', ''),
                '✅' if request_data.get('photo') else '❌',
                request_data.get('user_id', ''),
                request_data.get('username', ''),
                request_data.get('created_at', ''),
                request_data.get('updated_at', ''),
                request_data.get('assigned_to', ''),
                request_data.get('completed_at', '')
            ]
            
            self.sheet.append_row(row_data)
            logger.info(f"✅ Заявка #{request_data.get('id')} добавлена в Google Sheets")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка добавления заявки в Google Sheets: {e}")
            return False
    
    def update_request(self, request_id: int, updates: Dict) -> bool:
        """Обновляет заявку в таблице"""
        if not self.is_connected:
            return False
        
        try:
            # Находим строку с заявкой
            records = self.sheet.get_all_records()
            for i, record in enumerate(records, start=2):  # start=2 т.к. первая строка - заголовки
                if record.get('ID') == request_id:
                    # Обновляем поля
                    for key, value in updates.items():
                        column_map = {
                            'status': 'Статус',
                            'assigned_to': 'Исполнитель', 
                            'completed_at': 'Завершено',
                            'updated_at': 'Обновлено'
                        }
                        if key in column_map:
                            col_name = column_map[key]
                            col_index = list(records[0].keys()).index(col_name) + 1
                            self.sheet.update_cell(i, col_index, value)
                    
                    logger.info(f"✅ Заявка #{request_id} обновлена в Google Sheets")
                    return True
            
            logger.warning(f"⚠️ Заявка #{request_id} не найдена в Google Sheets для обновления")
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка обновления заявки в Google Sheets: {e}")
            return False
    
    def get_all_requests(self) -> List[Dict]:
        """Получает все заявки из таблицы"""
        if not self.is_connected:
            return []
        
        try:
            return self.sheet.get_all_records()
        except Exception as e:
            logger.error(f"❌ Ошибка получения данных из Google Sheets: {e}")
            return []
    
    def sync_from_sheets(self, db_manager) -> Tuple[int, int]:
        """Синхронизация из Google Sheets в базу данных"""
        if not self.is_connected:
            return 0, 0
        
        try:
            sheet_requests = self.get_all_requests()
            if not sheet_requests:
                return 0, 0
            
            updated = 0
            added = 0
            
            for sheet_req in sheet_requests:
                if not sheet_req.get('ID'):
                    continue
                
                # Проверяем существование заявки в базе
                existing = db_manager.get_request_by_id(sheet_req['ID'])
                
                if existing:
                    # Обновляем существующую
                    updates = {}
                    if sheet_req.get('Статус') and sheet_req['Статус'] != existing.get('status'):
                        updates['status'] = sheet_req['Статус']
                    if sheet_req.get('Исполнитель') and sheet_req['Исполнитель'] != existing.get('assigned_to'):
                        updates['assigned_to'] = sheet_req['Исполнитель']
                    
                    if updates:
                        db_manager.update_request(sheet_req['ID'], updates)
                        updated += 1
                else:
                    # Добавляем новую заявку
                    request_data = {
                        'id': sheet_req['ID'],
                        'status': sheet_req.get('Статус', 'new'),
                        'name': sheet_req.get('Имя', ''),
                        'phone': sheet_req.get('Телефон', ''),
                        'plot': sheet_req.get('Участок', ''),
                        'system_type': sheet_req.get('Тип системы', ''),
                        'problem': sheet_req.get('Проблема', ''),
                        'urgency': sheet_req.get('Срочность', ''),
                        'user_id': sheet_req.get('ID пользователя', ''),
                        'username': sheet_req.get('Username', ''),
                        'created_at': sheet_req.get('Создано', ''),
                        'updated_at': sheet_req.get('Обновлено', ''),
                        'assigned_to': sheet_req.get('Исполнитель', ''),
                        'completed_at': sheet_req.get('Завершено', '')
                    }
                    
                    if db_manager.save_external_request(request_data):
                        added += 1
            
            logger.info(f"✅ Синхронизация из Google Sheets: {added} добавлено, {updated} обновлено")
            return added, updated
            
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации из Google Sheets: {e}")
            return 0, 0

# ==================== ОБНОВЛЕННЫЙ КОНФИГУРАЦИОННЫЙ КЛАСС ====================

class Config:
    """Централизованное управление конфигурацией"""
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
    
    def validate(self) -> bool:
        """Проверяет корректность конфигурации"""
        if not self.bot_token:
            logger.error("❌ BOT_TOKEN не установлен")
            return False
        if not self.admin_chat_ids:
            logger.error("❌ ADMIN_CHAT_IDS не установлены")
            return False
        if self.sync_to_sheets and (not self.google_sheets_credentials or not self.google_sheet_id):
            logger.warning("⚠️ Google Sheets настроен не полностью - синхронизация отключена")
            self.sync_to_sheets = False
        return True

config = Config()

# ==================== ОБНОВЛЕННАЯ БАЗА ДАННЫХ ====================

class EnhancedDatabase(Database):
    """Расширенная база данных с поддержкой Google Sheets"""
    
    def __init__(self, db_path, sheets_manager=None):
        super().__init__(db_path)
        self.sheets_manager = sheets_manager
    
    def save_request(self, data: Dict) -> int:
        """Сохраняет заявку в базу данных и Google Sheets"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Обновляем или создаем пользователя
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
                
                # Синхронизация с Google Sheets
                if self.sheets_manager and self.sheets_manager.is_connected:
                    sheet_data = data.copy()
                    sheet_data['id'] = request_id
                    sheet_data['created_at'] = datetime.now().isoformat()
                    sheet_data['updated_at'] = datetime.now().isoformat()
                    self.sheets_manager.add_request(sheet_data)
                
                return request_id
                
        except sqlite3.Error as e:
            logger.error(f"Ошибка сохранения заявки: {e}")
            raise
    
    def save_external_request(self, data: Dict) -> bool:
        """Сохраняет внешнюю заявку (из Google Sheets)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT OR REPLACE INTO requests 
                    (id, user_id, username, name, phone, plot, system_type, problem, 
                     photo, urgency, status, created_at, updated_at, assigned_to, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data['id'], data.get('user_id'), data.get('username'), data.get('name'),
                    data.get('phone'), data.get('plot'), data.get('system_type'), data.get('problem'),
                    data.get('photo'), data.get('urgency'), data.get('status', 'new'),
                    data.get('created_at'), data.get('updated_at'), data.get('assigned_to'),
                    data.get('completed_at')
                ))
                
                conn.commit()
                return True
                
        except sqlite3.Error as e:
            logger.error(f"Ошибка сохранения внешней заявки: {e}")
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
            logger.error(f"Ошибка получения заявки: {e}")
            return None
    
    def update_request(self, request_id: int, updates: Dict) -> bool:
        """Обновляет заявку"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
                values = list(updates.values())
                
                cursor.execute(f'''
                    UPDATE requests 
                    SET {set_clause}, updated_at = ?
                    WHERE id = ?
                ''', values + [datetime.now().isoformat(), request_id])
                
                conn.commit()
                
                # Синхронизация с Google Sheets
                if self.sheets_manager and self.sheets_manager.is_connected:
                    self.sheets_manager.update_request(request_id, updates)
                
                return True
                
        except sqlite3.Error as e:
            logger.error(f"Ошибка обновления заявки: {e}")
            return False

    # ДОБАВЛЕННЫЕ МЕТОДЫ ДЛЯ СОВМЕСТИМОСТИ
    def get_urgent_requests(self, hours: int = 2) -> List[Dict]:
        """Получает срочные заявки"""
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
            logger.error(f"Ошибка получения зависших заявок: {e}")
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
                
                stats = cursor.fetchone()
                if stats:
                    columns = [column[0] for column in cursor.description]
                    result = dict(zip(columns, stats))
                    
                    cursor.execute('''
                        SELECT AVG(
                            (julianday(updated_at) - julianday(created_at)) * 24
                        ) as avg_hours
                        FROM requests 
                        WHERE user_id = ? AND status = 'completed'
                    ''', (user_id,))
                    
                    avg_hours = cursor.fetchone()[0]
                    result['avg_completion_hours'] = round(avg_hours, 2) if avg_hours else 0
                    
                    return result
                return {}
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения статистики пользователя: {e}")
            return {}

# ==================== ДОПОЛНИТЕЛЬНЫЕ КЛАССЫ ====================

class CacheManager:
    """Менеджер кэширования"""
    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        self._stats_cache = {}
    
    def get_cached_stats(self, key: str) -> Dict:
        """Получает кэшированную статистику"""
        with self._lock:
            if key in self._stats_cache:
                cached_data, timestamp = self._stats_cache[key]
                if datetime.now() - timestamp < timedelta(minutes=5):
                    return cached_data
        return None
    
    def set_cached_stats(self, key: str, data: Dict):
        """Сохраняет статистику в кэш"""
        with self._lock:
            self._stats_cache[key] = (data, datetime.now())
    
    def clear_cache(self):
        """Очищает кэш"""
        with self._lock:
            self._cache.clear()
            self._stats_cache.clear()

class NotificationManager:
    """Менеджер уведомлений"""
    def __init__(self, bot):
        self.bot = bot
        self.notification_queue = []
        self.lock = threading.Lock()
    
    def send_priority_notification(self, chat_ids: List[int], text: str, parse_mode: str = ParseMode.MARKDOWN):
        """Отправляет приоритетное уведомление"""
        for chat_id in chat_ids:
            try:
                self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления {chat_id}: {e}")
    
    def process_queue(self):
        """Обрабатывает очередь уведомлений"""
        # Базовая реализация
        return 0

class MediaManager:
    """Менеджер медиафайлов"""
    @staticmethod
    def validate_photo_size(file_size: int) -> bool:
        return file_size <= 10 * 1024 * 1024
    
    @staticmethod
    def get_photo_info(photo_file):
        return {
            'file_id': photo_file.file_id,
            'file_size': photo_file.file_size,
            'width': photo_file.width,
            'height': photo_file.height
        }

# ==================== НОВЫЕ КОМАНДЫ ДЛЯ GOOGLE SHEETS ====================

def sync_sheets_command(update: Update, context: CallbackContext) -> None:
    """Команда принудительной синхронизации с Google Sheets"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    if not sheets_manager or not sheets_manager.is_connected:
        update.message.reply_text(
            "❌ Google Sheets не настроен или не подключен",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
        )
        return
    
    try:
        update.message.reply_text("🔄 Начинаю синхронизацию с Google Sheets...")
        
        # Синхронизация из Sheets в базу
        added, updated = sheets_manager.sync_from_sheets(db)
        
        # Синхронизация из базы в Sheets (для новых заявок)
        new_requests = db.get_requests_by_filter('new')
        synced_to_sheets = 0
        
        for request in new_requests:
            if sheets_manager.add_request(request):
                synced_to_sheets += 1
        
        result_text = (
            f"✅ *Синхронизация завершена*\n\n"
            f"📥 *Из Sheets в базу:*\n"
            f"• Добавлено: {added}\n"
            f"• Обновлено: {updated}\n\n"
            f"📤 *Из базы в Sheets:*\n"
            f"• Синхронизировано: {synced_to_sheets}\n\n"
            f"💾 *Всего заявок в базе:* {len(new_requests) + updated}"
        )
        
        update.message.reply_text(
            result_text,
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"Ошибка синхронизации: {e}")
        update.message.reply_text(
            f"❌ Ошибка синхронизации: {str(e)}",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
        )

def show_sheets_status(update: Update, context: CallbackContext) -> None:
    """Показывает статус подключения к Google Sheets"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    if sheets_manager and sheets_manager.is_connected:
        try:
            records = sheets_manager.get_all_requests()
            total_in_sheets = len(records) - 1 if records else 0
            
            status_text = (
                "📊 *Статус Google Sheets*\n\n"
                f"🔗 *Подключение:* ✅ Активно\n"
                f"📁 *Таблица:* {GOOGLE_SHEET_NAME}\n"
                f"📈 *Записей в таблице:* {total_in_sheets}\n"
                f"🔄 *Автосинхронизация:* {'✅ Вкл' if SYNC_TO_SHEETS else '❌ Выкл'}\n\n"
            )
            
            if records and len(records) > 1:
                status_text += "*Последние 5 заявок из таблицы:*\n"
                for i, record in enumerate(records[-5:], 1):
                    if record.get('ID'):
                        status_text += f"\n{i}. #{record.get('ID', 'N/A')} - {record.get('Статус', 'N/A')} - {record.get('Имя', 'N/A')}"
            else:
                status_text += "\nЗаявок не найдено"
            
        except Exception as e:
            status_text = f"❌ *Ошибка получения данных из таблицы:* {str(e)}"
    else:
        status_text = (
            "📊 *Статус Google Sheets*\n\n"
            "🔗 *Подключение:* ❌ Не активно\n\n"
            "Для подключения необходимо:\n"
            "1. Создать сервисный аккаунт в Google Cloud\n"
            "2. Выдать доступ к таблице\n"
            "3. Установить переменные окружения:\n"
            "   • GOOGLE_SHEETS_CREDENTIALS (JSON)\n"
            "   • GOOGLE_SHEET_ID (ID таблицы)"
        )
    
    update.message.reply_text(
        status_text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def export_to_sheets(update: Update, context: CallbackContext) -> None:
    """Экспорт всех заявок в Google Sheets"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    if not sheets_manager or not sheets_manager.is_connected:
        update.message.reply_text("❌ Google Sheets не настроен")
        return
    
    try:
        update.message.reply_text("📤 Начинаю экспорт всех заявок...")
        
        all_requests = []
        for status in ['new', 'in_progress', 'completed']:
            all_requests.extend(db.get_requests_by_filter(status))
        
        exported = 0
        for request in all_requests:
            if sheets_manager.add_request(request):
                exported += 1
        
        update.message.reply_text(
            f"✅ Экспорт завершен!\n"
            f"📊 Заявок экспортировано: {exported} из {len(all_requests)}",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
        )
        
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        update.message.reply_text(f"❌ Ошибка экспорта: {str(e)}")

# ==================== ОБНОВЛЕННЫЕ КЛАВИАТУРЫ ====================

# Расширенное админ-меню с Google Sheets
enhanced_admin_main_menu_keyboard = [
    ['🆕 Новые заявки', '🔄 В работе'],
    ['⏰ Срочные заявки', '📊 Статистика'],
    ['👥 Пользователи', '⚙️ Настройки'],
    ['💾 Бэкапы', '🔄 Обновить'],
    ['🚨 Зависшие заявки', '📈 Аналитика'],
    ['📊 Google Sheets', '🔄 Синхронизация']
]

# Меню Google Sheets
sheets_keyboard = [
    ['📊 Статус Sheets', '🔄 Синхронизировать'],
    ['📤 Экспорт в Sheets', '📥 Импорт из Sheets'],
    ['🔗 Тест подключения', '📋 Данные таблицы'],
    ['🔙 Назад в админ-панель']
]

# Пользовательское меню
enhanced_user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['📊 Моя статистика', '🆘 Срочная помощь'],
    ['ℹ️ О боте', '🔔 Настройки уведомлений']
]

# ==================== БАЗОВЫЕ ФУНКЦИИ ДЛЯ СОВМЕСТИМОСТИ ====================

def get_enhanced_admin_panel():
    """Возвращает расширенную админ-панель"""
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
        ['📊 Google Sheets', '🔄 Синхронизация']
    ]

def show_enhanced_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает расширенную админ-панель"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu(update, context)
    
    stats = db.get_statistics(7)
    urgent_requests = db.get_urgent_requests()
    stuck_requests = db.get_stuck_requests(REQUEST_TIMEOUT_HOURS)
    
    admin_text = (
        "👑 *Расширенная админ-панель завода Контакт*\n\n"
        f"📊 *За последние 7 дней:*\n"
        f"• Всего заявок: {stats['total']}\n"
        f"• Выполнено: {stats['completed']}\n"
        f"• Новых: {stats['new']}\n"
        f"• В работе: {stats['in_progress']}\n\n"
        f"⚠️ *Требуют внимания:*\n"
        f"• Срочные заявки: {len(urgent_requests)}\n"
        f"• Зависшие заявки: {len(stuck_requests)}\n\n"
        "Выберите раздел для управления:"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Главное меню"""
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        return show_enhanced_admin_panel(update, context)
    else:
        update.message.reply_text(
            "Добро пожаловать в службу слаботочных систем завода Контакт!\n\nВыберите действие:",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

# ==================== ОБРАБОТЧИКИ РАЗГОВОРА ====================

def name(update: Update, context: CallbackContext) -> int:
    """Обработка имени"""
    context.user_data['name'] = update.message.text
    update.message.reply_text("📞 Теперь введите ваш номер телефона:", reply_markup=ReplyKeyboardRemove())
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    """Обработка телефона"""
    context.user_data['phone'] = update.message.text
    update.message.reply_text("📍 Введите номер участка:", reply_markup=ReplyKeyboardRemove())
    return PLOT

def plot(update: Update, context: CallbackContext) -> int:
    """Обработка участка"""
    context.user_data['plot'] = update.message.text
    
    keyboard = [['🔌 Электрика', '📶 Сети'], ['📞 Телефония', '🎥 Видеонаблюдение']]
    update.message.reply_text("🔧 Выберите тип системы:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    """Обработка типа системы"""
    context.user_data['system_type'] = update.message.text
    update.message.reply_text("📝 Опишите проблему:", reply_markup=ReplyKeyboardRemove())
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    """Обработка проблемы"""
    context.user_data['problem'] = update.message.text
    
    keyboard = [['🔴 Срочно', '🟡 Средняя'], ['🟢 Не срочно']]
    update.message.reply_text("⏰ Выберите срочность:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    """Обработка срочности"""
    context.user_data['urgency'] = update.message.text
    update.message.reply_text(
        "📸 Пришлите фото проблемы (или нажмите 'Пропустить'):",
        reply_markup=ReplyKeyboardMarkup([['📷 Пропустить']], resize_keyboard=True)
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    """Обработка фото"""
    if update.message.photo:
        photo_file = update.message.photo[-1].file_id
        context.user_data['photo'] = photo_file
    else:
        context.user_data['photo'] = None
    
    return show_request_summary(update, context)

def show_request_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку заявки"""
    user_data = context.user_data
    
    summary_text = (
        "📋 *Сводка заявки:*\n\n"
        f"👤 *Имя:* {user_data.get('name', 'Не указано')}\n"
        f"📞 *Телефон:* {user_data.get('phone', 'Не указано')}\n"
        f"📍 *Участок:* {user_data.get('plot', 'Не указано')}\n"
        f"🔧 *Система:* {user_data.get('system_type', 'Не указано')}\n"
        f"⏰ *Срочность:* {user_data.get('urgency', 'Не указано')}\n"
        f"📝 *Проблема:* {user_data.get('problem', 'Не указано')}\n"
        f"📸 *Фото:* {'✅ Есть' if user_data.get('photo') else '❌ Нет'}\n\n"
        "Подтвердите отправку заявки:"
    )
    
    keyboard = [['✅ Подтвердить отправку', '✏️ Редактировать заявку']]
    
    update.message.reply_text(
        summary_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return ConversationHandler.END

def cancel_request(update: Update, context: CallbackContext) -> int:
    """Отмена заявки"""
    update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
    )
    context.user_data.clear()
    return ConversationHandler.END

def enhanced_start_request_creation(update: Update, context: CallbackContext) -> int:
    """Начало создания заявки"""
    user_id = update.message.from_user.id
    
    if rate_limiter.is_limited(user_id, 'create_request', MAX_REQUESTS_PER_HOUR):
        update.message.reply_text(
            "❌ *Превышен лимит запросов!*\n\nВы можете создавать не более 15 заявок в час.\nПожалуйста, попробуйте позже.",
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

def enhanced_confirm_request(update: Update, context: CallbackContext) -> None:
    """Подтверждение заявки"""
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
            
            request_id = db.save_request(context.user_data)
            
            confirmation_text = (
                f"✅ *Заявка #{request_id} успешно создана!*\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n"
                f"📍 *Участок:* {context.user_data['plot']}\n\n"
                f"_Спасибо за обращение в службу слаботочных систем завода Контакт!_ 🛠️"
            )
            
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

# ==================== ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ ====================

def show_sheets_management(update: Update, context: CallbackContext) -> None:
    """Показывает меню управления Google Sheets"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    sheets_text = (
        "📊 *Управление Google Sheets*\n\n"
        "Здесь вы можете управлять синхронизацией с онлайн-таблицей:\n\n"
        "• 📊 Статус - информация о подключении\n"
        "• 🔄 Синхронизировать - двусторонняя синхронизация\n"
        "• 📤 Экспорт - выгрузка всех заявок в таблицу\n"
        "• 📥 Импорт - загрузка данных из таблицы\n"
        "• 🔗 Тест - проверка подключения\n"
        "• 📋 Данные - просмотр данных таблицы\n"
    )
    
    update.message.reply_text(
        sheets_text,
        reply_markup=ReplyKeyboardMarkup(sheets_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_sheets_commands(update: Update, context: CallbackContext) -> None:
    """Обрабатывает команды Google Sheets"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    if text == '🔙 Назад в админ-панель':
        return show_enhanced_admin_panel(update, context)
    elif text == '📊 Статус Sheets':
        return show_sheets_status(update, context)
    elif text == '🔄 Синхронизировать':
        return sync_sheets_command(update, context)
    elif text == '📤 Экспорт в Sheets':
        return export_to_sheets(update, context)
    elif text == '🔗 Тест подключения':
        return test_sheets_connection(update, context)
    elif text == '📋 Данные таблицы':
        return show_sheets_data(update, context)
    else:
        update.message.reply_text(
            "Выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(sheets_keyboard, resize_keyboard=True)
        )

def test_sheets_connection(update: Update, context: CallbackContext) -> None:
    """Тестирует подключение к Google Sheets"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    if sheets_manager and sheets_manager.is_connected:
        try:
            records = sheets_manager.get_all_requests()
            update.message.reply_text(
                f"✅ *Подключение активно*\n\n"
                f"📊 Записей в таблице: {len(records) - 1 if records else 0}\n"
                f"🔗 Соединение стабильное",
                reply_markup=ReplyKeyboardMarkup(sheets_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            update.message.reply_text(
                f"❌ *Ошибка подключения:* {str(e)}",
                reply_markup=ReplyKeyboardMarkup(sheets_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
    else:
        update.message.reply_text(
            "❌ Подключение не настроено",
            reply_markup=ReplyKeyboardMarkup(sheets_keyboard, resize_keyboard=True)
        )

def show_sheets_data(update: Update, context: CallbackContext) -> None:
    """Показывает данные из Google Sheets"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    if not sheets_manager or not sheets_manager.is_connected:
        update.message.reply_text("❌ Google Sheets не подключен")
        return
    
    try:
        records = sheets_manager.get_all_requests()
        if not records or len(records) <= 1:
            update.message.reply_text("📋 Таблица пуста или содержит только заголовки")
            return
        
        recent_records = records[-10:] if len(records) > 10 else records
        
        text = "📋 *Последние заявки из Google Sheets:*\n\n"
        for i, record in enumerate(recent_records, 1):
            if record.get('ID'):
                text += (
                    f"*#{record.get('ID', 'N/A')}* - {record.get('Статус', 'N/A')}\n"
                    f"👤 {record.get('Имя', 'N/A')} | 📞 {record.get('Телефон', 'N/A')}\n"
                    f"📍 {record.get('Участок', 'N/A')} | 🔧 {record.get('Тип системы', 'N/A')}\n"
                    f"⏰ {record.get('Срочность', 'N/A')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                )
        
        update.message.reply_text(
            text,
            reply_markup=ReplyKeyboardMarkup(sheets_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения данных: {e}")
        update.message.reply_text(f"❌ Ошибка: {str(e)}")

def enhanced_handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обработчик админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if text.startswith('🆕 Новые'):
        return show_requests_by_filter(update, context, 'new')
    elif text.startswith('🔄 В работе'):
        return show_requests_by_filter(update, context, 'in_progress')
    elif text.startswith('⏰ Срочные'):
        return show_urgent_requests(update, context)
    elif text.startswith('🚨 Зависшие'):
        return show_stuck_requests(update, context)
    elif text == '📊 Статистика':
        return show_statistics(update, context)
    elif text == '📈 Аналитика':
        return show_analytics(update, context)
    elif text == '👥 Пользователи':
        return show_users_management(update, context)
    elif text == '⚙️ Настройки':
        return show_settings(update, context)
    elif text == '💾 Бэкапы':
        return show_backup_management(update, context)
    elif text == '🔄 Обновить':
        cache_manager.clear_cache()
        return show_enhanced_admin_panel(update, context)
    elif text == '📊 Google Sheets':
        return show_sheets_management(update, context)
    elif text == '🔄 Синхронизация':
        return sync_sheets_command(update, context)

def show_requests_by_filter(update: Update, context: CallbackContext, status: str):
    """Показывает заявки по фильтру"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    requests = db.get_requests_by_filter(status)
    
    if not requests:
        update.message.reply_text(
            f"📋 *Заявки со статусом '{status}'*\n\nЗаявок не найдено.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = f"📋 *Заявки ({status}):*\n\n"
    
    for req in requests[:5]:
        created_time = datetime.fromisoformat(req['created_at'])
        time_str = created_time.strftime('%d.%m.%Y %H:%M')
        
        text += (
            f"📄 *Заявка #{req['id']}*\n"
            f"📍 {req['plot']} | {req['system_type']}\n"
            f"👤 {req['name']} | 📞 {req['phone']}\n"
            f"🕒 {time_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
    
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_urgent_requests(update: Update, context: CallbackContext) -> None:
    """Показывает срочные заявки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    urgent_requests = db.get_urgent_requests()
    
    if not urgent_requests:
        update.message.reply_text(
            "⏰ *Срочные заявки*\n\nСрочных заявок не найдено.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = f"⏰ *Срочные заявки ({len(urgent_requests)}):*\n\n"
    
    for req in urgent_requests[:10]:
        created_time = datetime.fromisoformat(req['created_at'])
        time_diff = datetime.now() - created_time
        hours_passed = time_diff.total_seconds() / 3600
        
        text += (
            f"🔴 *Заявка #{req['id']}*\n"
            f"📍 {req['plot']} | {req['system_type']}\n"
            f"⏰ Прошло: {hours_passed:.1f} ч.\n"
            f"👤 {req['name']} | 📞 {req['phone']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
    
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_stuck_requests(update: Update, context: CallbackContext) -> None:
    """Показывает зависшие заявки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    stuck_requests = db.get_stuck_requests(REQUEST_TIMEOUT_HOURS)
    
    if not stuck_requests:
        update.message.reply_text(
            "🚨 *Зависшие заявки*\n\nЗависших заявок не найдено.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = f"🚨 *Зависшие заявки ({len(stuck_requests)}):*\n\n"
    
    for req in stuck_requests[:10]:
        created_time = datetime.fromisoformat(req['created_at'])
        time_diff = datetime.now() - created_time
        hours_passed = time_diff.total_seconds() / 3600
        
        text += (
            f"⚠️ *Заявка #{req['id']}*\n"
            f"📍 {req['plot']} | {req['system_type']}\n"
            f"⏰ Висит: {hours_passed:.1f} ч.\n"
            f"👤 {req['name']} | 📞 {req['phone']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
    
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_statistics(update: Update, context: CallbackContext):
    """Показывает статистику"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    stats = db.get_statistics(7)
    
    update.message.reply_text(
        f"📊 Статистика за 7 дней:\n\n"
        f"• Всего заявок: {stats['total']}\n"
        f"• Выполнено: {stats['completed']}\n"
        f"• Новых: {stats['new']}\n"
        f"• В работе: {stats['in_progress']}",
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_analytics(update: Update, context: CallbackContext):
    """Показывает аналитику"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    stats_7_days = db.get_statistics(7)
    stats_30_days = db.get_statistics(30)
    
    analytics_text = (
        "📈 *Аналитика системы*\n\n"
        "📊 *За последние 7 дней:*\n"
        f"• Всего заявок: {stats_7_days['total']}\n"
        f"• Выполнено: {stats_7_days['completed']}\n"
        f"• В работе: {stats_7_days['in_progress']}\n"
        f"• Новых: {stats_7_days['new']}\n\n"
        "📅 *За последние 30 дней:*\n"
        f"• Всего заявок: {stats_30_days['total']}\n"
        f"• Выполнено: {stats_30_days['completed']}\n"
        f"• В работе: {stats_30_days['in_progress']}\n"
        f"• Новых: {stats_30_days['new']}\n\n"
    )
    
    update.message.reply_text(
        analytics_text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_users_management(update: Update, context: CallbackContext):
    """Управление пользователями"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    update.message.reply_text(
        "👥 *Управление пользователями*\n\nФункция в разработке.",
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_settings(update: Update, context: CallbackContext):
    """Настройки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    update.message.reply_text(
        "⚙️ *Настройки*\n\nФункция в разработке.",
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_backup_management(update: Update, context: CallbackContext):
    """Управление бэкапами"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    update.message.reply_text(
        "💾 *Управление бэкапами*\n\nФункция в разработке.",
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def enhanced_handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Обработчик главного меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        return show_enhanced_admin_panel(update, context)
    
    if text == '📝 Создать заявку':
        return enhanced_start_request_creation(update, context)
    elif text == '📋 Мои заявки':
        return show_my_requests(update, context)
    elif text == '📊 Моя статистика':
        return show_user_statistics(update, context)
    elif text == '🆘 Срочная помощь':
        return emergency_help(update, context)
    elif text == 'ℹ️ О боте':
        return show_bot_info(update, context)
    elif text == '🔔 Настройки уведомлений':
        return notification_settings(update, context)
    else:
        update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

def show_my_requests(update: Update, context: CallbackContext):
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM requests WHERE user_id = ? ORDER BY created_at DESC LIMIT 10',
                (user_id,)
            )
            requests = cursor.fetchall()
        
        if not requests:
            update.message.reply_text(
                "📋 *Мои заявки*\n\nУ вас пока нет заявок.",
                reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        text = "📋 *Мои последние заявки:*\n\n"
        
        for req in requests:
            req_dict = {
                'id': req[0],
                'status': req[12],
                'plot': req[7],
                'system_type': req[8],
                'problem': req[9],
                'created_at': req[13]
            }
            created_time = datetime.fromisoformat(req_dict['created_at'])
            time_str = created_time.strftime('%d.%m.%Y %H:%M')
            
            status_emoji = {
                'new': '🆕',
                'in_progress': '🔄', 
                'completed': '✅'
            }.get(req_dict['status'], '📄')
            
            text += (
                f"{status_emoji} *Заявка #{req_dict['id']}*\n"
                f"📍 {req_dict['plot']} | {req_dict['system_type']}\n"
                f"📝 {req_dict['problem'][:50]}...\n"
                f"🕒 {time_str} | {req_dict['status']}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )
        
        update.message.reply_text(
            text,
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения заявок пользователя: {e}")
        update.message.reply_text(
            "❌ Ошибка получения заявок",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

def show_user_statistics(update: Update, context: CallbackContext):
    """Показывает статистику пользователя"""
    user_id = update.message.from_user.id
    user_stats = db.get_user_statistics(user_id)
    
    if not user_stats or user_stats.get('total_requests', 0) == 0:
        update.message.reply_text(
            "📊 *Ваша статистика*\n\nУ вас пока нет созданных заявок.",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    completion_rate = (user_stats['completed'] / user_stats['total_requests']) * 100 if user_stats['total_requests'] > 0 else 0
    avg_hours = user_stats.get('avg_completion_hours', 0)
    
    stats_text = (
        "📊 *Ваша статистика заявок*\n\n"
        f"📈 *Всего заявок:* {user_stats['total_requests']}\n"
        f"✅ *Выполнено:* {user_stats['completed']}\n"
        f"🔄 *В работе:* {user_stats.get('in_progress', 0)}\n"
        f"🆕 *Новых:* {user_stats.get('new', 0)}\n\n"
        f"📊 *Эффективность:*\n"
        f"• Процент выполнения: {completion_rate:.1f}%\n"
        f"• Среднее время выполнения: {avg_hours} часов\n"
    )
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def emergency_help(update: Update, context: CallbackContext):
    """Экстренная помощь"""
    emergency_text = (
        "🆘 *Экстренная помощь*\n\n"
        "Для срочных вопросов и аварийных ситуаций:\n\n"
        "📞 *Телефон службы поддержки:*\n"
        "+7 (XXX) XXX-XX-XX\n\n"
        "👨‍💼 *Ответственный:*\n"
        "Иванов Иван Иванович\n\n"
        "📍 *Местоположение службы:*\n"
        "Главный корпус, кабинет 101\n\n"
        "⏰ *Режим работы:*\n"
        "Пн-Пт: 8:00-17:00\n"
        "Сб: 9:00-15:00\n"
        "Вс: выходной"
    )
    
    update.message.reply_text(
        emergency_text,
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_bot_info(update: Update, context: CallbackContext):
    """Информация о боте"""
    info_text = (
        "ℹ️ *Информация о боте*\n\n"
        "🤖 *Бот службы слаботочных систем*\n"
        "Завод Контакт\n\n"
        "📊 *Возможности:*\n"
        "• Создание заявок на обслуживание\n"
        "• Отслеживание статуса заявок\n"
        "• Статистика и аналитика\n"
        "• Уведомления о статусах\n\n"
        "🛠️ *Техническая информация:*\n"
        f"• Версия: 2.0 (расширенная)\n"
        f"• База данных: SQLite\n"
        f"• Лимит заявок: {MAX_REQUESTS_PER_HOUR}/час\n"
    )
    
    update.message.reply_text(
        info_text,
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def notification_settings(update: Update, context: CallbackContext):
    """Настройки уведомлений"""
    update.message.reply_text(
        "🔔 *Настройки уведомлений*\n\nФункция в разработке.",
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def error_handler(update: Update, context: CallbackContext):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.message:
        update.message.reply_text(
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже.",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

def backup_job(context: CallbackContext):
    """Задание для автоматического бэкапа"""
    try:
        backup_path = BackupManager.create_backup()
        if backup_path:
            logger.info(f"Автоматический бэкап создан: {backup_path}")
    except Exception as e:
        logger.error(f"Ошибка автоматического бэкапа: {e}")

def auto_sync_job(context: CallbackContext):
    """Автоматическая синхронизация с Google Sheets"""
    if not sheets_manager or not sheets_manager.is_connected:
        return
    
    try:
        logger.info("🔄 Запуск автоматической синхронизации с Google Sheets")
        added, updated = sheets_manager.sync_from_sheets(db)
        if added > 0 or updated > 0:
            logger.info(f"✅ Автосинхронизация: {added} добавлено, {updated} обновлено")
    except Exception as e:
        logger.error(f"❌ Ошибка автосинхронизации: {e}")

def check_urgent_requests(context: CallbackContext):
    """Проверяет срочные заявки"""
    try:
        urgent_requests = db.get_urgent_requests()
        if urgent_requests:
            logger.info(f"⚠️ Найдено {len(urgent_requests)} срочных заявок")
    except Exception as e:
        logger.error(f"Ошибка проверки срочных заявок: {e}")

# ==================== ЗАПУСК БОТА ====================

# Глобальные объекты
rate_limiter = RateLimiter()
db = None
sheets_manager = None
notification_manager = None
cache_manager = CacheManager()

def enhanced_main() -> None:
    """Улучшенный запуск бота с поддержкой Google Sheets"""
    global db, sheets_manager, notification_manager
    
    if not config.validate():
        logger.error("❌ Неверная конфигурация бота!")
        return
    
    try:
        # Инициализация Google Sheets
        if config.sync_to_sheets:
            sheets_manager = GoogleSheetsManager(
                config.google_sheets_credentials,
                config.google_sheet_id,
                config.google_sheet_name
            )
        else:
            logger.info("⚠️ Google Sheets отключен в конфигурации")
        
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Инициализация компонентов
        db = EnhancedDatabase(DB_PATH, sheets_manager)
        notification_manager = NotificationManager(updater.bot)

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
                
                # Автоматическая синхронизация с Google Sheets
                if config.sync_to_sheets:
                    job_queue.run_repeating(auto_sync_job, interval=1800, first=60)
                    logger.info("✅ Автосинхронизация с Google Sheets включена")
                
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
        dispatcher.add_handler(CommandHandler('sync_sheets', sync_sheets_command))
        dispatcher.add_handler(CommandHandler('sheets_status', show_sheets_status))
        
        dispatcher.add_handler(conv_handler)
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(✅ Подтвердить отправку)$'), 
            enhanced_confirm_request
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📝 Создать заявку|📋 Мои заявки|📊 Моя статистика|🆘 Срочная помощь|ℹ️ О боте|🔔 Настройки уведомлений)$'), 
            enhanced_handle_main_menu
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые|🔄 В работе|⏰ Срочные|🚨 Зависшие|📊 Статистика|📈 Аналитика|👥 Пользователи|⚙️ Настройки|💾 Бэкапы|🔄 Обновить|📊 Google Sheets|🔄 Синхронизация)$'), 
            enhanced_handle_admin_menu
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📊 Статус Sheets|🔄 Синхронизировать|📤 Экспорт в Sheets|📥 Импорт из Sheets|🔗 Тест подключения|📋 Данные таблицы|🔙 Назад в админ-панель)$'),
            handle_sheets_commands
        ))

        # Запускаем бота
        logger.info("🤖 Улучшенный бот запущен с поддержкой Google Sheets!")
        logger.info(f"👑 Администраторы: {len(ADMIN_CHAT_IDS)}")
        logger.info(f"📊 Google Sheets: {'✅ Подключен' if sheets_manager and sheets_manager.is_connected else '❌ Отключен'}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска улучшенного бота: {e}")

if __name__ == '__main__':
    enhanced_main()
