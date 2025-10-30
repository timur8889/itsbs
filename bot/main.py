import logging
import sqlite3
import os
import json
import re
import time
import asyncio
import shutil
import gspread
from oauth2client.service_account import ServiceAccountCredentials
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
    
    # Настройки отделов (только IT отдел)
    ADMIN_CHAT_IDS = {
        '💻 IT отдел': [5024165375]
    }
    
    # Google Sheets настройки
    GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
    GOOGLE_SHEET_NAME = os.getenv('GOOGLE_SHEET_NAME', 'IT Заявки Завод Контакт')
    
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

# ==================== GOOGLE SHEETS ИНТЕГРАЦИЯ ====================

class GoogleSheetsManager:
    """📊 Менеджер для работы с Google Sheets"""
    
    def __init__(self):
        self.credentials = None
        self.client = None
        self.sheet = None
        self.setup_sheets()
    
    def setup_sheets(self):
        """🔧 Настройка подключения к Google Sheets"""
        try:
            if not Config.GOOGLE_SHEETS_CREDENTIALS:
                logger.warning("❌ Google Sheets credentials не найдены")
                return
            
            # Парсим JSON credentials из переменной окружения
            creds_dict = json.loads(Config.GOOGLE_SHEETS_CREDENTIALS)
            
            # Настраиваем доступ
            scope = ['https://spreadsheets.google.com/feeds', 
                    'https://www.googleapis.com/auth/drive']
            
            credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(credentials)
            
            # Открываем таблицу
            self.sheet = self.client.open(Config.GOOGLE_SHEET_NAME).sheet1
            
            # Создаем заголовки если их нет
            headers = ['ID', 'Дата создания', 'Пользователь', 'Телефон', 'Проблема', 
                      'Статус', 'Исполнитель', 'Дата выполнения', 'Комментарий', 'Оценка']
            
            if not self.sheet.get('A1:J1'):
                self.sheet.append_row(headers)
            
            logger.info("✅ Google Sheets подключен успешно")
            
        except Exception as e:
            logger.error(f"❌ Ошибка настройки Google Sheets: {e}")
    
    def add_request_to_sheet(self, request_data: Dict):
        """📝 Добавляет заявку в Google Sheets"""
        try:
            if not self.sheet:
                logger.warning("Google Sheets не настроен")
                return
            
            row_data = [
                request_data['id'],
                request_data['created_at'],
                request_data['username'],
                request_data['phone'],
                request_data['problem'],
                request_data['status'],
                request_data.get('assigned_admin', ''),
                request_data.get('completed_at', ''),
                request_data.get('admin_comment', ''),
                request_data.get('rating', '')
            ]
            
            self.sheet.append_row(row_data)
            logger.info(f"✅ Заявка #{request_data['id']} добавлена в Google Sheets")
            
        except Exception as e:
            logger.error(f"❌ Ошибка добавления в Google Sheets: {e}")
    
    def update_request_in_sheet(self, request_data: Dict):
        """🔄 Обновляет заявку в Google Sheets"""
        try:
            if not self.sheet:
                return
            
            # Ищем строку с ID заявки
            cell = self.sheet.find(str(request_data['id']))
            if cell:
                row = cell.row
                
                # Обновляем данные
                update_data = [
                    request_data['id'],
                    request_data['created_at'],
                    request_data['username'],
                    request_data['phone'],
                    request_data['problem'],
                    request_data['status'],
                    request_data.get('assigned_admin', ''),
                    request_data.get('completed_at', ''),
                    request_data.get('admin_comment', ''),
                    request_data.get('rating', '')
                ]
                
                for i, value in enumerate(update_data, 1):
                    self.sheet.update_cell(row, i, value)
                
                logger.info(f"✅ Заявка #{request_data['id']} обновлена в Google Sheets")
            
        except Exception as e:
            logger.error(f"❌ Ошибка обновления в Google Sheets: {e}")

# ==================== БАЗА ДАННЫХ ====================

class EnhancedDatabase:
    """🗃️ Улучшенный класс для работы с базой данных"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_enhanced_db()
    
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
                    admin_comment TEXT
                )
            ''')
            
            # Таблица медиа файлов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS request_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER,
                    file_id TEXT,
                    file_type TEXT,
                    created_at TEXT,
                    FOREIGN KEY (request_id) REFERENCES requests (id)
                )
            ''')
            
            conn.commit()
    
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
            return request_id
    
    def add_media_to_request(self, request_id: int, file_id: str, file_type: str):
        """📎 Добавляет медиа файл к заявке"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO request_media (request_id, file_id, file_type, created_at)
                VALUES (?, ?, ?, ?)
            ''', (request_id, file_id, file_type, datetime.now().isoformat()))
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
    
    def get_requests(self, status: str = None, limit: int = 50) -> List[Dict]:
        """📋 Получает список заявок"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM requests WHERE 1=1"
            params = []
            
            if status:
                query += " AND status = ?"
                params.append(status)
            
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
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            ''', (user_id,))
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

# ==================== ИНИЦИАЛИЗАЦИЯ ====================

# Инициализация базы данных
db = EnhancedDatabase(Config.DB_PATH)

# Инициализация Google Sheets
sheets_manager = GoogleSheetsManager()

# ==================== ОСНОВНЫЕ КОМАНДЫ БОТА ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🚀 Обработчик команды /start"""
    user = update.message.from_user
    
    welcome_text = (
        "🎉 *Рады видеть Вас!*\n\n"
        "Вы подключились в IT отдел завода *Контакт*! 🤖\n\n"
        "Будем рады Вам помочь с решением технических вопросов и проблем. "
        "Наша команда готова оперативно реагировать на ваши заявки.\n\n"
        "💡 *Что мы можем сделать:*\n"
        "• 🖥️ Помощь с компьютерной техникой\n"
        "🌐 Решение проблем с сетью и интернетом\n"
        "🖨️ Настройка принтеров и оргтехники\n"
        "📱 Поддержка программного обеспечения\n"
        "🔧 Консультации по техническим вопросам\n\n"
        "Выберите действие из меню ниже:"
    )
    
    await show_main_menu(update, context, welcome_text)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, welcome_text: str = None) -> None:
    """🏠 Показывает главное меню"""
    keyboard = [
        ["📝 Создать заявку", "📂 Мои заявки"],
        ["🆘 Помощь"]
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
            "🎯 *Главное меню IT отдела завода Контакт*",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== ПРОЦЕСС СОЗДАНИЯ ЗАЯВКИ ====================

# Состояния для создания заявки
REQUEST_PHONE, REQUEST_PROBLEM, REQUEST_MEDIA = range(3)

async def new_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """📝 Начинает процесс создания новой заявки"""
    user = update.message.from_user
    
    context.user_data['request'] = {
        'user_id': user.id,
        'username': user.username or user.full_name,
        'media_files': []  # Список для хранения медиа файлов
    }
    
    keyboard = [["🔙 Назад"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📋 *Создание новой заявки*\n\n"
        "📞 Пожалуйста, введите ваш номер телефона для связи:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_PHONE

async def request_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """📞 Обрабатывает номер телефона"""
    if update.message.text == "🔙 Назад":
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    phone = update.message.text
    context.user_data['request']['phone'] = phone
    
    keyboard = [["🔙 Назад"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "🔧 Опишите вашу проблему подробно:\n\n"
        "💡 *Примеры:*\n"
        "• 'Не включается компьютер'\n"
        "• 'Не работает интернет'\n"
        "• 'Не печатает принтер'\n"
        "• 'Требуется установка программы'\n\n"
        "Вы можете прикрепить фото или видео проблемы после описания.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_PROBLEM

async def request_problem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """🔧 Обрабатывает описание проблемы"""
    if update.message.text == "🔙 Назад":
        # Возвращаемся к вводу телефона
        keyboard = [["🔙 Назад"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📞 Пожалуйста, введите ваш номер телефона:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return REQUEST_PHONE
    
    problem = update.message.text
    context.user_data['request']['problem'] = problem
    
    keyboard = [
        ["📎 Прикрепить фото/видео", "✅ Завершить без медиа"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📎 Хотите прикрепить фото или видео к заявке?\n\n"
        "💡 *Это поможет нам быстрее понять и решить проблему*",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    return REQUEST_MEDIA

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """📎 Обрабатывает медиа файлы"""
    message = update.message
    
    if message.text == "🔙 Назад":
        # Возвращаемся к описанию проблемы
        keyboard = [["🔙 Назад"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🔧 Опишите вашу проблему подробно:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return REQUEST_PROBLEM
    
    if message.text == "✅ Завершить без медиа":
        return await create_request_final(update, context)
    
    if message.photo:
        # Берем самое большое фото
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    else:
        await message.reply_text("❌ Пожалуйста, отправьте фото или видео.")
        return REQUEST_MEDIA
    
    # Сохраняем информацию о файле
    context.user_data['request']['media_files'].append({
        'file_id': file_id,
        'file_type': file_type
    })
    
    keyboard = [
        ["📎 Прикрепить еще", "✅ Завершить создание"],
        ["🔙 Назад"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await message.reply_text(
        f"✅ { 'Фото' if file_type == 'photo' else 'Видео' } прикреплено!\n"
        f"📎 Прикреплено файлов: {len(context.user_data['request']['media_files'])}",
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
                media_file['file_type']
            )
        
        # Получаем полные данные заявки для Google Sheets
        full_request_data = db.get_request(request_id)
        full_request_data['id'] = request_id
        
        # Добавляем в Google Sheets
        sheets_manager.add_request_to_sheet(full_request_data)
        
        # Отправляем уведомление администраторам
        await notify_admins_new_request(context, request_id, request_data)
        
        success_text = (
            f"🎉 *Заявка #{request_id} успешно создана!*\n\n"
            f"🏢 *Отдел:* 💻 IT отдел\n"
            f"📞 *Ваш телефон:* {request_data['phone']}\n"
            f"📎 *Медиа файлов:* {len(request_data.get('media_files', []))}\n\n"
            f"🔧 *Проблема:* {request_data['problem']}\n\n"
            f"⏰ *Заявка зарегистрирована:* {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            f"📊 Вы можете отслеживать статус заявки в разделе \"📂 Мои заявки\"\n"
            f"💬 Мы свяжемся с вами в ближайшее время!"
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

async def notify_admins_new_request(context: ContextTypes.DEFAULT_TYPE, request_id: int, request_data: Dict):
    """👥 Уведомляет администраторов о новой заявке"""
    message = (
        f"🆕 *НОВАЯ ЗАЯВКА #{request_id}*\n\n"
        f"👤 {request_data['username']} | 📞 {request_data['phone']}\n"
        f"🔧 {request_data['problem'][:100]}...\n"
        f"📎 Медиа файлов: {len(request_data.get('media_files', []))}\n"
        f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
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
    await update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=ReplyKeyboardMarkup([["📝 Создать заявку"]], resize_keyboard=True)
    )
    return ConversationHandler.END

# ==================== ОБРАБОТЧИКИ КНОПОК АДМИНИСТРАТОРОВ ====================

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
        
        # Обновляем в Google Sheets
        updated_request = db.get_request(request_id)
        updated_request['id'] = request_id
        sheets_manager.update_request_in_sheet(updated_request)
        
        # Обновляем сообщение
        message_text = query.message.text + f"\n\n✅ *ВЗЯТА В РАБОТУ*\n👨‍💼 Исполнитель: {admin_name}"
        
        # Обновляем клавиатуру
        keyboard = [
            [
                InlineKeyboardButton("✅ Заявка выполнена", callback_data=f"complete_{request_id}"),
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
            text=f"🔄 *Заявка #{request_id} взята в работу*\n\n👨‍💼 Исполнитель: {admin_name}",
            parse_mode=ParseMode.MARKDOWN
        )
        
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
        f"Пожалуйста, введите комментарий к выполненной работе:",
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
        await update.message.reply_text(
            "❌ Завершение заявки отменено.",
            reply_markup=ReplyKeyboardMarkup([["👨‍💼 Админ панель"]], resize_keyboard=True)
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
            
            # Обновляем в Google Sheets
            updated_request = db.get_request(request_id)
            updated_request['id'] = request_id
            sheets_manager.update_request_in_sheet(updated_request)
            
            # Отправляем уведомление пользователю
            request = db.get_request(request_id)
            if request:
                await context.bot.send_message(
                    chat_id=request['user_id'],
                    text=(
                        f"✅ *Заявка #{request_id} выполнена!*\n\n"
                        f"👨‍💼 Исполнитель: {admin_name}\n"
                        f"💬 Комментарий: {comment}\n\n"
                        f"⭐ Спасибо, что воспользовались нашим сервисом!\n"
                        f"🔄 Если проблема повторится, создайте новую заявку."
                    ),
                    parse_mode=ParseMode.MARKDOWN
                )
            
            await update.message.reply_text(
                f"✅ Заявка #{request_id} завершена с комментарием!",
                reply_markup=ReplyKeyboardMarkup([["👨‍💼 Админ панель"]], resize_keyboard=True)
            )
            
            # Очищаем временные данные
            context.user_data.pop('completing_request', None)
            context.user_data.pop('completing_admin', None)
            
        except Exception as e:
            logger.error(f"❌ Ошибка завершения заявки: {e}")
            await update.message.reply_text("❌ Ошибка при завершении заявки.")

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
        
        details_text = (
            f"📋 *ДЕТАЛИ ЗАЯВКИ #{request_id}*\n\n"
            f"👤 *Пользователь:* {request['username']}\n"
            f"📞 *Телефон:* {request['phone']}\n"
            f"🔧 *Проблема:* {request['problem']}\n"
            f"📊 *Статус:* {request['status']}\n"
            f"🕒 *Создана:* {request['created_at'][:16]}\n"
        )
        
        if request['assigned_admin']:
            details_text += f"👨‍💼 *Исполнитель:* {request['assigned_admin']}\n"
        
        if request['admin_comment']:
            details_text += f"💬 *Комментарий:* {request['admin_comment']}\n"
        
        if media_files:
            details_text += f"📎 *Медиа файлов:* {len(media_files)}\n"
        
        await query.message.reply_text(
            details_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Отправляем медиа файлы если они есть
        for media in media_files:
            try:
                if media['file_type'] == 'photo':
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=media['file_id'],
                        caption=f"📎 Фото к заявке #{request_id}"
                    )
                elif media['file_type'] == 'video':
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=media['file_id'],
                        caption=f"📎 Видео к заявке #{request_id}"
                    )
            except Exception as e:
                logger.error(f"❌ Ошибка отправки медиа: {e}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка показа деталей заявки: {e}")
        await query.answer("❌ Ошибка при загрузке деталей!", show_alert=True)

# ==================== АДМИНСКИЕ КОМАНДЫ ====================

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👨‍💼 Показывает админскую панель"""
    user_id = update.message.from_user.id
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    # Получаем статистику
    requests = db.get_requests()
    new_requests = len([r for r in requests if r['status'] == 'new'])
    in_progress_requests = len([r for r in requests if r['status'] == 'in_progress'])
    
    admin_text = (
        f"👨‍💼 *АДМИН ПАНЕЛЬ IT ОТДЕЛА*\n\n"
        f"📊 *Статистика:*\n"
        f"• 🆕 Новые заявки: {new_requests}\n"
        f"• 🔄 В работе: {in_progress_requests}\n"
        f"• 📋 Всего заявок: {len(requests)}\n\n"
        f"📋 *Управление заявками:*"
    )
    
    keyboard = [
        ["📋 Новые заявки", "🔄 Заявки в работе"],
        ["📊 Общая статистика", "🔙 Главное меню"]
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
    elif update.message.text == "🔄 Заявки в работе":
        status_filter = 'in_progress'
        title = "🔄 ЗАЯВКИ В РАБОТЕ"
    else:
        title = "📋 ВСЕ ЗАЯВКИ"
    
    requests = db.get_requests(status=status_filter)
    if not requests:
        await update.message.reply_text("📭 Заявок нет.")
        return
    
    requests_text = f"{title}\n\n"
    
    for req in requests[:10]:
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }.get(req['status'], '❓')
        
        requests_text += (
            f"{status_emoji} *Заявка #{req['id']}*\n"
            f"👤 {req['username']} | 📞 {req['phone']}\n"
            f"🔧 {req['problem'][:80]}...\n"
            f"🕒 {req['created_at'][:16]}\n\n"
        )
    
    keyboard = [["🔙 Назад в админку"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(requests_text, 
                                  reply_markup=reply_markup,
                                  parse_mode=ParseMode.MARKDOWN)

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================

async def show_user_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📂 Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    requests = db.get_user_requests(user_id)
    
    if not requests:
        await update.message.reply_text("📭 У вас пока нет заявок.")
        return
    
    requests_text = "📂 *ВАШИ ЗАЯВКИ*\n\n"
    
    for req in requests[:10]:
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }.get(req['status'], '❓')
        
        requests_text += (
            f"{status_emoji} *Заявка #{req['id']}*\n"
            f"📝 {req['problem'][:50]}...\n"
            f"⏰ {req['created_at'][:10]}\n"
            f"🔸 Статус: {req['status']}\n\n"
        )
    
    keyboard = [["🔙 Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(requests_text, 
                                  reply_markup=reply_markup,
                                  parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🆘 Показывает справку"""
    help_text = (
        "🆘 *ПОМОЩЬ IT ОТДЕЛА*\n\n"
        "🎯 *Основные команды:*\n"
        "• /start - 🏠 Главное меню\n"
        "• /new_request - 📝 Создать заявку\n"
        "• /my_requests - 📂 Мои заявки\n"
        "• /help - 🆘 Помощь\n\n"
        "👨‍💼 *Для администраторов:*\n"
        "• /admin - 👨‍💼 Админ панель\n\n"
        "💡 *Совет:* Используйте кнопки меню для быстрого доступа к функциям!\n\n"
        "📞 *Контакты IT отдела:*\n"
        "• Телефон: [номер телефона]\n"
        "• Email: [email адрес]\n"
        "• Кабинет: [номер кабинета]"
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
    
    # Основные кнопки для всех пользователей
    if text == "📂 Мои заявки":
        await show_user_requests(update, context)
    elif text == "📝 Создать заявку":
        await new_request_command(update, context)
    elif text == "🆘 Помощь":
        await help_command(update, context)
    elif text == "🔙 Главное меню":
        await show_main_menu(update, context)
    
    # Админские кнопки
    elif text == "👨‍💼 Админ панель" and Config.is_admin(user_id):
        await admin_panel_command(update, context)
    elif text == "📋 Все заявки" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "📋 Новые заявки" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "🔄 Заявки в работе" and Config.is_admin(user_id):
        await admin_requests_command(update, context)
    elif text == "📊 Общая статистика" and Config.is_admin(user_id):
        await admin_panel_command(update, context)
    elif text == "🔙 Назад в админку" and Config.is_admin(user_id):
        await admin_panel_command(update, context)
    else:
        await update.message.reply_text("🤔 Пожалуйста, используйте кнопки меню.")

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
                MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_request)]
    )
    
    # Основные команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_requests", show_user_requests))
    application.add_handler(CommandHandler("admin", admin_panel_command))
    application.add_handler(request_conv_handler)
    
    # Обработчики callback (кнопки администраторов)
    application.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern="^(take_|details_|complete_)"))
    
    # Обработчик комментариев администратора
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_comment))
    
    # Обработчики текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================

def main() -> None:
    """🚀 Запуск бота"""
    try:
        print("🔄 Запуск бота IT отдела завода Контакт...")
        
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
        print("✨ ВОЗМОЖНОСТИ:")
        print("   • 🏢 Только IT отдел")
        print("   • 📝 Создание заявок с медиа файлами")
        print("   • 👨‍💼 Кнопки для администраторов")
        print("   • 💬 Комментарии к выполненным заявкам")
        print("   • 📊 Интеграция с Google Sheets")
        print("   • 🔙 Кнопки 'Назад' на всех этапах")
        print("   • 🎉 Приветствие завода Контакт")
        print("\n🚀 Бот готов к работе!")
        
        # Запуск бота
        print("🔄 Запуск опроса...")
        application.run_polling()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
