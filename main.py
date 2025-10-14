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
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
import pandas as pd

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
SYNC_TO_SHEETS = bool(GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SHEET_ID)  # Автосинхронизация

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
                values.append(request_id)
                
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
            # Получаем данные из таблицы для статистики
            records = sheets_manager.get_all_requests()
            total_in_sheets = len(records) - 1 if records else 0  # minus header
            
            status_text = (
                "📊 *Статус Google Sheets*\n\n"
                f"🔗 *Подключение:* ✅ Активно\n"
                f"📁 *Таблица:* {GOOGLE_SHEET_NAME}\n"
                f"📈 *Записей в таблице:* {total_in_sheets}\n"
                f"🔄 *Автосинхронизация:* {'✅ Вкл' if SYNC_TO_SHEETS else '❌ Выкл'}\n\n"
                f"*Последние 5 заявок из таблицы:*\n"
            )
            
            if records and len(records) > 1:
                for i, record in enumerate(records[-5:], 1):
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
        
        # Получаем все заявки из базы
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
    ['📊 Google Sheets', '🔄 Синхронизация']  # Новые кнопки
]

# Меню Google Sheets
sheets_keyboard = [
    ['📊 Статус Sheets', '🔄 Синхронизировать'],
    ['📤 Экспорт в Sheets', '📥 Импорт из Sheets'],
    ['🔗 Тест подключения', '📋 Данные таблицы'],
    ['🔙 Назад в админ-панель']
]

# ==================== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ====================

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
            # Пытаемся получить данные
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
        
        # Показываем последние 10 записей
        recent_records = records[-10:] if len(records) > 10 else records
        
        text = "📋 *Последние заявки из Google Sheets:*\n\n"
        for i, record in enumerate(recent_records, 1):
            if record.get('ID'):  # Пропускаем заголовки
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

# ==================== ОБНОВЛЕННЫЕ ФУНКЦИИ СИНХРОНИЗАЦИИ ====================

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

# ==================== ОБНОВЛЕННЫЙ ЗАПУСК ====================

# Глобальные объекты
rate_limiter = RateLimiter()
db = None
sheets_manager = None
notification_manager = None
cache_manager = CacheManager()

def enhanced_main() -> None:
    """Улучшенный запуск бота с поддержкой Google Sheets"""
    global db, sheets_manager, notification_manager
    
    # Проверка конфигурации
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

        # Инициализация расширенных компонентов
        db = EnhancedDatabase(DB_PATH, sheets_manager)
        notification_manager = NotificationManager(updater.bot)

        # Обработчик ошибок
        dispatcher.add_error_handler(error_handler)

        # Расширенные задания по расписанию
        job_queue = updater.job_queue
        if job_queue:
            try:
                # Ежедневное резервное копирование
                backup_time = time(hour=AUTO_BACKUP_HOUR, minute=AUTO_BACKUP_MINUTE)
                job_queue.run_daily(backup_job, time=backup_time)
                
                # Ежечасная проверка срочных заявок
                job_queue.run_repeating(
                    check_urgent_requests, 
                    interval=3600,
                    first=10
                )
                
                # Автоматическая синхронизация с Google Sheets каждые 30 минут
                if config.sync_to_sheets:
                    job_queue.run_repeating(
                        auto_sync_job,
                        interval=1800,  # 30 минут
                        first=60
                    )
                    logger.info("✅ Автосинхронизация с Google Sheets включена")
                
                # Обработка очереди уведомлений
                job_queue.run_repeating(
                    lambda context: notification_manager.process_queue(),
                    interval=30,
                    first=5
                )
                
                logger.info("✅ Все задания планировщика успешно зарегистрированы")
                
            except Exception as e:
                logger.error(f"❌ Ошибка регистрации заданий планировщика: {e}")

        # [СОХРАНЯЕМ ВСЕ СТАРЫЕ ОБРАБОТЧИКИ ИЗ ПРЕДЫДУЩЕГО КОДА]
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
                EDIT_CHOICE: [MessageHandler(Filters.text & ~Filters.command, handle_edit_choice)],
                EDIT_FIELD: [
                    MessageHandler(Filters.text & ~Filters.command, handle_edit_field),
                    MessageHandler(Filters.photo, handle_edit_field)
                ],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(Filters.regex('^(🔙 Назад в меню)$'), cancel_request),
            ],
            allow_reentry=True
        )

        # Регистрируем обработчики (сохраняем старые + добавляем новые)
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_enhanced_admin_panel))
        dispatcher.add_handler(CommandHandler('stats', show_statistics))
        dispatcher.add_handler(CommandHandler('backup', create_backup_command))
        dispatcher.add_handler(CommandHandler('mystats', show_user_statistics))
        dispatcher.add_handler(CommandHandler('help', emergency_help))
        dispatcher.add_handler(CommandHandler('info', show_bot_info))
        dispatcher.add_handler(CommandHandler('sync_sheets', sync_sheets_command))  # Новая команда
        dispatcher.add_handler(CommandHandler('sheets_status', show_sheets_status))  # Новая команда
        
        dispatcher.add_handler(conv_handler)
        
        # Обработчик подтверждения заявки
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(✅ Подтвердить отправку)$'), 
            enhanced_confirm_request
        ))
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📝 Создать заявку|📋 Мои заявки|📊 Моя статистика|🆘 Срочная помощь|ℹ️ О боте|🔔 Настройки уведомлений)$'), 
            enhanced_handle_main_menu
        ))
        
        # Обработчики админ-панели (обновленные)
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые|🔄 В работе|⏰ Срочные|🚨 Зависшие|📊 Статистика|📈 Аналитика|👥 Пользователи|⚙️ Настройки|💾 Бэкапы|🔄 Обновить|📊 Google Sheets|🔄 Синхронизация)$'), 
            enhanced_handle_admin_menu
        ))
        
        # Обработчики Google Sheets
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📊 Статус Sheets|🔄 Синхронизировать|📤 Экспорт в Sheets|📥 Импорт из Sheets|🔗 Тест подключения|📋 Данные таблицы|🔙 Назад в админ-панель)$'),
            lambda update, context: handle_sheets_commands(update, context)
        ))

        # [ДОБАВЛЯЕМ НОВЫЕ ОБРАБОТЧИКИ В АДМИН-МЕНЮ]
        def enhanced_handle_admin_menu(update: Update, context: CallbackContext) -> None:
            """Улучшенный обработчик админ-меню с Google Sheets"""
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

        # Запускаем бота
        logger.info("🤖 Улучшенный бот запущен с поддержкой Google Sheets!")
        logger.info(f"👑 Администраторы: {len(ADMIN_CHAT_IDS)}")
        logger.info(f"📊 Google Sheets: {'✅ Подключен' if sheets_manager and sheets_manager.is_connected else '❌ Отключен'}")
        logger.info(f"🔄 Автосинхронизация: {'✅ Вкл' if config.sync_to_sheets else '❌ Выкл'}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска улучшенного бота: {e}")

if __name__ == '__main__':
    enhanced_main()
