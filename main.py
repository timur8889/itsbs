import logging
import sqlite3
import os
import json
import csv
import gspread
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ParseMode,
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
from oauth2client.service_account import ServiceAccountCredentials
import threading
import time

# Включим логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
ADMIN_CHAT_IDS = [5024165375]  # Замените на реальные chat_id админов
BOT_TOKEN = "7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw"  # Замените на реальный токен бота

# Настройки Google Sheets
GOOGLE_SHEETS_CONFIG = {
    'enabled': True,  # Включить автоматическую синхронизацию
    'spreadsheet_name': 'Заявки слаботочных систем',
    'credentials_file': 'credentials.json',  # Файл с учетными данными
    'worksheet_name': 'Заявки',
    'sync_interval': 30,  # Интервал синхронизации в секундах
    'auto_sync': True  # Автоматическая синхронизация при изменениях
}

# Определяем этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD = range(9)

# База данных
DB_PATH = "requests.db"

# ==================== КЛАВИАТУРЫ ====================

# Главное меню пользователя
user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки']
]

# Главное меню администратора
admin_main_menu_keyboard = [
    ['🆕 Новые заявки (0)', '🔄 В работе (0)'],
    ['✅ Выполненные заявки', '📊 Статистика'],
    ['🔧 Управление', '📊 Excel онлайн']
]

# Меню управления для админов
admin_management_keyboard = [
    ['📢 Сделать рассылку', '🔄 Обновить счетчики'],
    ['📁 Экспорт заявок', '🔄 Синхронизировать с Excel'],
    ['🔙 Назад в админ-панель']
]

# Меню Excel онлайн
excel_online_keyboard = [
    ['🔄 Обновить данные', '📊 Статистика Excel'],
    ['⚙️ Настройки Excel', '📋 Просмотреть Excel'],
    ['🔙 Назад в админ-панель']
]

# Меню создания заявки
create_request_keyboard = [
    ['📹 Видеонаблюдение', '🔐 СКУД'],
    ['🌐 Компьютерная сеть', '🚨 Пожарная сигнализация'],
    ['🔙 Назад в меню']
]

# Клавиатуры для этапов заявки
confirm_keyboard = [['✅ Подтвердить отправку', '✏️ Редактировать заявку']]
photo_keyboard = [['📷 Добавить фото', '⏭️ Пропустить фото']]
urgency_keyboard = [
    ['🔴 Срочно (2 часа)'],
    ['🟡 Средняя (сегодня)'],
    ['🟢 Не срочно (3 дня)'],
    ['🔙 Назад']
]
plot_type_keyboard = [
    ['🏭 Фрезерный участок', '⚙️ Токарный участок'],
    ['🔨 Участок штамповки', '📦 Другой участок'],
    ['🔙 Назад']
]

# Клавиатуры для редактирования
edit_choice_keyboard = [
    ['📛 Редактировать имя', '📞 Редактировать телефон'],
    ['📍 Редактировать участок', '🔧 Редактировать систему'],
    ['📝 Редактировать описание', '⏰ Редактировать срочность'],
    ['📷 Редактировать фото', '✅ Завершить редактирование']
]

edit_field_keyboard = [['🔙 Назад к редактированию']]

# ==================== GOOGLE SHEETS ИНТЕГРАЦИЯ ====================

class GoogleSheetsManager:
    def __init__(self, config: Dict):
        self.config = config
        self.sheet = None
        self.connected = False
        self.last_sync = None
        self.sync_in_progress = False
        self.init_sheets()

    def init_sheets(self):
        """Инициализация подключения к Google Sheets"""
        if not self.config['enabled']:
            logger.info("📊 Google Sheets отключен в настройках")
            return

        try:
            if not os.path.exists(self.config['credentials_file']):
                logger.error(f"❌ Файл учетных данных {self.config['credentials_file']} не найден")
                return

            # Авторизация
            scope = ['https://spreadsheets.google.com/feeds', 
                    'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                self.config['credentials_file'], scope)
            client = gspread.authorize(creds)

            # Открытие таблицы
            try:
                self.sheet = client.open(self.config['spreadsheet_name']).worksheet(
                    self.config['worksheet_name'])
            except gspread.SpreadsheetNotFound:
                # Создаем новую таблицу
                self.sheet = client.create(self.config['spreadsheet_name'])
                self.sheet = self.sheet.sheet1
                self.sheet.update_title(self.config['worksheet_name'])
                # Создаем заголовки
                self._create_headers()
            except gspread.WorksheetNotFound:
                # Создаем новый лист
                self.sheet = client.open(self.config['spreadsheet_name']).add_worksheet(
                    title=self.config['worksheet_name'], rows=1000, cols=20)
                self._create_headers()

            self.connected = True
            logger.info("✅ Google Sheets подключен успешно")
            
            # Запускаем фоновую синхронизацию
            self._start_background_sync()
            
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Google Sheets: {e}")
            self.connected = False

    def _create_headers(self):
        """Создает заголовки таблицы"""
        headers = [
            'ID', 'Статус', 'Срочность', 'Имя', 'Телефон', 'Участок',
            'Тип системы', 'Описание проблемы', 'Фото', 'Username',
            'Исполнитель', 'Комментарий', 'Дата создания', 'Дата обновления',
            'User ID'
        ]
        self.sheet.update('A1:O1', [headers])
        logger.info("✅ Заголовки Google Sheets созданы")

    def _start_background_sync(self):
        """Запускает фоновую синхронизацию"""
        if not self.config['auto_sync']:
            return
            
        def sync_worker():
            while True:
                try:
                    if self.connected:
                        self.sync_all_requests()
                    time.sleep(self.config['sync_interval'])
                except Exception as e:
                    logger.error(f"❌ Ошибка в фоновой синхронизации: {e}")
                    time.sleep(60)  # Ждем минуту при ошибке

        thread = threading.Thread(target=sync_worker, daemon=True)
        thread.start()
        logger.info("✅ Фоновая синхронизация запущена")

    def sync_all_requests(self):
        """Синхронизирует все заявки с Google Sheets"""
        if not self.connected or self.sync_in_progress:
            return

        self.sync_in_progress = True
        try:
            # Получаем все заявки из базы
            db = Database(DB_PATH)
            requests = db.get_all_requests_for_sync()
            
            if not requests:
                return

            # Подготавливаем данные
            data = []
            for req in requests:
                row = [
                    req['id'],
                    req['status'],
                    req['urgency'],
                    req['name'],
                    req['phone'],
                    req['plot'],
                    req['system_type'],
                    req['problem'],
                    '✅' if req['photo'] else '❌',
                    req.get('username', ''),
                    req.get('assigned_admin', ''),
                    req.get('admin_comment', ''),
                    req['created_at'],
                    req.get('updated_at', req['created_at']),
                    req['user_id']
                ]
                data.append(row)

            # Очищаем старые данные (кроме заголовка)
            self.sheet.clear()
            self._create_headers()
            
            # Добавляем новые данные
            if data:
                self.sheet.update(f'A2:O{len(data) + 1}', data)
            
            self.last_sync = datetime.now()
            logger.info(f"✅ Синхронизировано {len(data)} заявок с Google Sheets")
            
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации с Google Sheets: {e}")
        finally:
            self.sync_in_progress = False

    def sync_single_request(self, request_data: Dict):
        """Синхронизирует одну заявку в реальном времени"""
        if not self.connected or not self.config['auto_sync']:
            return

        try:
            # Находим строку с этой заявкой
            all_records = self.sheet.get_all_records()
            row_index = None
            
            for i, record in enumerate(all_records, start=2):  # start=2 потому что заголовок в 1 строке
                if str(record.get('ID', '')) == str(request_data['id']):
                    row_index = i
                    break

            # Подготавливаем данные строки
            row_data = [
                request_data['id'],
                request_data['status'],
                request_data['urgency'],
                request_data['name'],
                request_data['phone'],
                request_data['plot'],
                request_data['system_type'],
                request_data['problem'],
                '✅' if request_data['photo'] else '❌',
                request_data.get('username', ''),
                request_data.get('assigned_admin', ''),
                request_data.get('admin_comment', ''),
                request_data['created_at'],
                request_data.get('updated_at', request_data['created_at']),
                request_data['user_id']
            ]

            if row_index:
                # Обновляем существующую строку
                self.sheet.update(f'A{row_index}:O{row_index}', [row_data])
                logger.info(f"✅ Обновлена заявка #{request_data['id']} в Google Sheets")
            else:
                # Добавляем новую строку
                self.sheet.append_row(row_data)
                logger.info(f"✅ Добавлена заявка #{request_data['id']} в Google Sheets")
                
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации заявки #{request_data['id']}: {e}")

    def get_sheet_stats(self) -> Dict:
        """Получает статистику из Google Sheets"""
        if not self.connected:
            return {'error': 'Google Sheets не подключен'}
        
        try:
            all_records = self.sheet.get_all_records()
            
            stats = {
                'total_rows': len(all_records),
                'last_sync': self.last_sync.strftime('%d.%m.%Y %H:%M') if self.last_sync else 'Никогда',
                'new_count': len([r for r in all_records if r.get('Статус') == 'new']),
                'in_progress_count': len([r for r in all_records if r.get('Статус') == 'in_progress']),
                'completed_count': len([r for r in all_records if r.get('Статус') == 'completed']),
                'urgent_count': len([r for r in all_records if 'Срочно' in str(r.get('Срочность', ''))])
            }
            return stats
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики из Google Sheets: {e}")
            return {'error': str(e)}

    def get_sheet_url(self) -> str:
        """Возвращает URL Google Sheets"""
        if not self.connected:
            return "Google Sheets не подключен"
        return f"https://docs.google.com/spreadsheets/d/{self.sheet.spreadsheet.id}"

# Инициализация Google Sheets
sheets_manager = GoogleSheetsManager(GOOGLE_SHEETS_CONFIG)

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self, db_path: str):
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
                    admin_comment TEXT,
                    assigned_admin TEXT,
                    synced_with_sheets INTEGER DEFAULT 0
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS statistics (
                    date TEXT PRIMARY KEY,
                    requests_count INTEGER DEFAULT 0,
                    completed_count INTEGER DEFAULT 0
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TEXT,
                    request_count INTEGER DEFAULT 0,
                    last_activity TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sheets_sync (
                    last_sync_time TEXT,
                    total_synced INTEGER DEFAULT 0
                )
            ''')
            conn.commit()

    def save_request(self, user_data: Dict) -> int:
        """Сохраняет заявку в базу данных"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO requests 
                (user_id, username, name, phone, plot, system_type, problem, photo, urgency, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_data.get('user_id'),
                user_data.get('username'),
                user_data.get('name'),
                user_data.get('phone'),
                user_data.get('plot'),
                user_data.get('system_type'),
                user_data.get('problem'),
                user_data.get('photo'),
                user_data.get('urgency'),
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            request_id = cursor.lastrowid
            
            # Обновляем статистику
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('''
                INSERT OR REPLACE INTO statistics (date, requests_count)
                VALUES (?, COALESCE((SELECT requests_count FROM statistics WHERE date = ?), 0) + 1)
            ''', (today, today))
            
            # Обновляем информацию о пользователе
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at, request_count, last_activity)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT request_count FROM users WHERE user_id = ?), 0) + 1, ?)
            ''', (
                user_data.get('user_id'),
                user_data.get('username'),
                user_data.get('first_name', ''),
                user_data.get('last_name', ''),
                datetime.now().isoformat(),
                user_data.get('user_id'),
                datetime.now().isoformat()
            ))
            
            conn.commit()
            
            # Синхронизируем с Google Sheets в реальном времени
            self._sync_request_to_sheets(request_id)
            
            return request_id

    def _sync_request_to_sheets(self, request_id: int):
        """Синхронизирует заявку с Google Sheets"""
        try:
            request = self.get_request(request_id)
            if request:
                sheets_manager.sync_single_request(request)
                
                # Помечаем как синхронизированную
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE requests SET synced_with_sheets = 1 WHERE id = ?
                    ''', (request_id,))
                    conn.commit()
                    
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации заявки #{request_id}: {e}")

    def update_request_status(self, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
        """Обновляет статус заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if admin_comment and assigned_admin:
                cursor.execute('''
                    UPDATE requests SET status = ?, admin_comment = ?, assigned_admin = ?, updated_at = ?, synced_with_sheets = 0
                    WHERE id = ?
                ''', (status, admin_comment, assigned_admin, datetime.now().isoformat(), request_id))
            elif admin_comment:
                cursor.execute('''
                    UPDATE requests SET status = ?, admin_comment = ?, updated_at = ?, synced_with_sheets = 0
                    WHERE id = ?
                ''', (status, admin_comment, datetime.now().isoformat(), request_id))
            elif assigned_admin:
                cursor.execute('''
                    UPDATE requests SET status = ?, assigned_admin = ?, updated_at = ?, synced_with_sheets = 0
                    WHERE id = ?
                ''', (status, assigned_admin, datetime.now().isoformat(), request_id))
            else:
                cursor.execute('''
                    UPDATE requests SET status = ?, updated_at = ?, synced_with_sheets = 0 WHERE id = ?
                ''', (status, datetime.now().isoformat(), request_id))
            
            conn.commit()
            
            # Синхронизируем с Google Sheets в реальном времени
            self._sync_request_to_sheets(request_id)

    def get_all_requests_for_sync(self) -> List[Dict]:
        """Получает все заявки для синхронизации"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests ORDER BY created_at DESC
            ''')
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_unsynced_requests(self) -> List[Dict]:
        """Получает несинхронизированные заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests WHERE synced_with_sheets = 0 ORDER BY created_at DESC
            ''')
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # ... остальные методы базы данных остаются без изменений ...
    def get_user_requests(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Получает заявки пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (user_id, limit))
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_requests_by_filter(self, filter_type: str = 'all', limit: int = 50) -> List[Dict]:
        """Получает заявки по фильтру"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if filter_type == 'new':
                status_filter = "status = 'new'"
            elif filter_type == 'in_progress':
                status_filter = "status = 'in_progress'"
            elif filter_type == 'urgent':
                status_filter = "urgency LIKE '%Срочно%' AND status IN ('new', 'in_progress')"
            elif filter_type == 'completed':
                status_filter = "status = 'completed'"
            else:  # all active
                status_filter = "status IN ('new', 'in_progress')"
            
            cursor.execute(f'''
                SELECT * FROM requests 
                WHERE {status_filter}
                ORDER BY 
                    CASE urgency 
                        WHEN '🔴 Срочно (2 часа)' THEN 1
                        WHEN '🟡 Средняя (сегодня)' THEN 2
                        ELSE 3
                    END,
                    created_at DESC
                LIMIT ?
            ''', (limit,))
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_request(self, request_id: int) -> Dict:
        """Получает заявку по ID"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
            row = cursor.fetchone()
            if row:
                columns = [column[0] for column in cursor.description]
                return dict(zip(columns, row))
            return {}

    def get_my_in_progress_requests(self, admin_name: str, limit: int = 50) -> List[Dict]:
        """Получает заявки, которые взял в работу конкретный администратор"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests 
                WHERE assigned_admin = ? AND status = 'in_progress'
                ORDER BY 
                    CASE urgency 
                        WHEN '🔴 Срочно (2 часа)' THEN 1
                        WHEN '🟡 Средняя (сегодня)' THEN 2
                        ELSE 3
                    END,
                    created_at DESC
                LIMIT ?
            ''', (admin_name, limit))
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_statistics(self) -> Dict:
        """Получает общую статистику"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM requests')
            total_requests = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM requests WHERE status = "new"')
            new_requests = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM requests WHERE status = "in_progress"')
            in_progress_requests = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM requests WHERE status = "completed"')
            completed_requests = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(DISTINCT user_id) FROM users')
            total_users = cursor.fetchone()[0]
            
            # Статистика за сегодня
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('SELECT requests_count FROM statistics WHERE date = ?', (today,))
            today_requests = cursor.fetchone()
            today_requests = today_requests[0] if today_requests else 0
            
            # Статистика за вчера
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            cursor.execute('SELECT requests_count FROM statistics WHERE date = ?', (yesterday,))
            yesterday_requests = cursor.fetchone()
            yesterday_requests = yesterday_requests[0] if yesterday_requests else 0
            
            return {
                'total_requests': total_requests,
                'new_requests': new_requests,
                'in_progress_requests': in_progress_requests,
                'completed_requests': completed_requests,
                'total_users': total_users,
                'today_requests': today_requests,
                'yesterday_requests': yesterday_requests
            }

    def get_all_users(self) -> List[Dict]:
        """Получает всех пользователей"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM users 
                ORDER BY last_activity DESC
            ''')
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def export_requests_to_csv(self, filename: str = "export_requests.csv") -> str:
        """Экспортирует заявки в CSV файл"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, user_id, username, name, phone, plot, system_type, problem, 
                       urgency, status, created_at, updated_at, assigned_admin, admin_comment
                FROM requests
                ORDER BY created_at DESC
            ''')
            
            import csv
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                # Заголовки
                writer.writerow([
                    'ID', 'User ID', 'Username', 'Name', 'Phone', 'Plot', 
                    'System Type', 'Problem', 'Urgency', 'Status', 
                    'Created At', 'Updated At', 'Assigned Admin', 'Admin Comment'
                ])
                # Данные
                for row in cursor.fetchall():
                    writer.writerow(row)
            
            return filename

# Инициализация базы данных
db = Database(DB_PATH)

# ==================== ФУНКЦИИ ДЛЯ EXCEL ONLINE ====================

def show_excel_online(update: Update, context: CallbackContext) -> None:
    """Показывает меню Excel онлайн"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    excel_text = (
        "📊 *Excel Online - Панель управления*\n\n"
        "Здесь вы можете управлять синхронизацией с Google Sheets\n\n"
    )
    
    # Добавляем информацию о статусе
    if sheets_manager.connected:
        stats = sheets_manager.get_sheet_stats()
        if 'error' not in stats:
            excel_text += (
                f"✅ *Google Sheets подключен*\n"
                f"📊 Всего строк: {stats['total_rows']}\n"
                f"🔄 Последняя синхронизация: {stats['last_sync']}\n"
                f"📎 Ссылка: {sheets_manager.get_sheet_url()}\n\n"
            )
        else:
            excel_text += f"❌ Ошибка: {stats['error']}\n\n"
    else:
        excel_text += "❌ *Google Sheets не подключен*\n\n"
    
    excel_text += "Выберите действие:"
    
    update.message.reply_text(
        excel_text,
        reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def sync_with_excel(update: Update, context: CallbackContext) -> None:
    """Синхронизирует все данные с Excel"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if not sheets_manager.connected:
        update.message.reply_text(
            "❌ Google Sheets не подключен. Проверьте настройки.",
            reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
        )
        return
    
    update.message.reply_text(
        "🔄 Начинаю синхронизацию с Google Sheets...",
        reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
    )
    
    # Запускаем синхронизацию в отдельном потоке
    def sync_thread():
        try:
            sheets_manager.sync_all_requests()
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text="✅ Синхронизация с Google Sheets завершена успешно!",
                reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
            )
        except Exception as e:
            context.bot.send_message(
                chat_id=update.message.chat_id,
                text=f"❌ Ошибка синхронизации: {e}",
                reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
            )
    
    threading.Thread(target=sync_thread).start()

def show_excel_stats(update: Update, context: CallbackContext) -> None:
    """Показывает статистику Excel"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if not sheets_manager.connected:
        update.message.reply_text(
            "❌ Google Sheets не подключен",
            reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
        )
        return
    
    stats = sheets_manager.get_sheet_stats()
    
    if 'error' in stats:
        update.message.reply_text(
            f"❌ Ошибка: {stats['error']}",
            reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
        )
        return
    
    stats_text = (
        "📊 *Статистика Google Sheets*\n\n"
        f"📋 Всего заявок: {stats['total_rows']}\n"
        f"🆕 Новых: {stats['new_count']}\n"
        f"🔄 В работе: {stats['in_progress_count']}\n"
        f"✅ Выполнено: {stats['completed_count']}\n"
        f"🔴 Срочных: {stats['urgent_count']}\n"
        f"🕒 Последняя синхронизация: {stats['last_sync']}\n\n"
        f"📎 *Ссылка на таблицу:*\n{sheets_manager.get_sheet_url()}"
    )
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_excel_settings(update: Update, context: CallbackContext) -> None:
    """Показывает настройки Excel"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    settings_text = (
        "⚙️ *Настройки Google Sheets*\n\n"
        f"📊 Включено: {'✅ Да' if GOOGLE_SHEETS_CONFIG['enabled'] else '❌ Нет'}\n"
        f"📁 Таблица: {GOOGLE_SHEETS_CONFIG['spreadsheet_name']}\n"
        f"📄 Лист: {GOOGLE_SHEETS_CONFIG['worksheet_name']}\n"
        f"🔄 Автосинхронизация: {'✅ Включена' if GOOGLE_SHEETS_CONFIG['auto_sync'] else '❌ Выключена'}\n"
        f"⏱️ Интервал: {GOOGLE_SHEETS_CONFIG['sync_interval']} сек.\n\n"
        f"🔗 Статус подключения: {'✅ Подключено' if sheets_manager.connected else '❌ Не подключено'}\n"
    )
    
    if sheets_manager.connected:
        settings_text += f"📎 Ссылка: {sheets_manager.get_sheet_url()}"
    
    update.message.reply_text(
        settings_text,
        reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def view_excel_data(update: Update, context: CallbackContext) -> None:
    """Показывает данные из Excel"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if not sheets_manager.connected:
        update.message.reply_text(
            "❌ Google Sheets не подключен",
            reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
        )
        return
    
    try:
        # Получаем последние 5 заявок из Google Sheets
        all_records = sheets_manager.sheet.get_all_records()
        recent_records = all_records[:5]  # Последние 5 записей
        
        if not recent_records:
            update.message.reply_text(
                "📭 В Google Sheets пока нет данных",
                reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
            )
            return
        
        excel_data_text = "📊 *Последние заявки из Google Sheets:*\n\n"
        
        for i, record in enumerate(recent_records, 1):
            excel_data_text += (
                f"{i}. *#{record.get('ID', 'N/A')}* - {record.get('Статус', 'N/A')}\n"
                f"   👤 {record.get('Имя', 'N/A')} | {record.get('Телефон', 'N/A')}\n"
                f"   📍 {record.get('Участок', 'N/A')} | {record.get('Тип системы', 'N/A')}\n"
                f"   ⏰ {record.get('Срочность', 'N/A')}\n\n"
            )
        
        excel_data_text += f"📎 Полная таблица: {sheets_manager.get_sheet_url()}"
        
        update.message.reply_text(
            excel_data_text,
            reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        update.message.reply_text(
            f"❌ Ошибка получения данных: {e}",
            reply_markup=ReplyKeyboardMarkup(excel_online_keyboard, resize_keyboard=True)
        )

# ==================== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ====================

def handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    # Обработка кнопок с счетчиками
    if text.startswith('🆕 Новые заявки'):
        return show_requests_by_filter(update, context, 'new')
    elif text.startswith('🔄 В работе'):
        return show_requests_by_filter(update, context, 'in_progress')
    elif text == '✅ Выполненные заявки':
        return show_requests_by_filter(update, context, 'completed')
    elif text == '📊 Статистика':
        return show_statistics(update, context)
    elif text == '🔧 Управление':
        return show_admin_management(update, context)
    elif text == '📊 Excel онлайн':
        return show_excel_online(update, context)
    elif text == '📢 Сделать рассылку':
        return start_broadcast(update, context)
    elif text == '🔄 Обновить счетчики':
        return update_counters(update, context)
    elif text == '📁 Экспорт заявок':
        return export_requests(update, context)
    elif text == '🔄 Синхронизировать с Excel':
        return sync_with_excel(update, context)
    elif text == '🔙 Назад в админ-панель':
        return show_admin_panel(update, context)
    # Обработка меню Excel онлайн
    elif text == '🔄 Обновить данные':
        return sync_with_excel(update, context)
    elif text == '📊 Статистика Excel':
        return show_excel_stats(update, context)
    elif text == '⚙️ Настройки Excel':
        return show_excel_settings(update, context)
    elif text == '📋 Просмотреть Excel':
        return view_excel_data(update, context)
    elif text == '🔙 Назад в админ-панель':
        return show_admin_panel(update, context)

# ... остальной код остается без изменений (функции создания заявок, админ-панели и т.д.) ...

# В функции main добавьте обработчики для Excel онлайн:
def main() -> None:
    """Запускаем бота"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # ... существующие обработчики ...

        # Обработчики Excel онлайн
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📊 Excel онлайн|🔄 Обновить данные|📊 Статистика Excel|⚙️ Настройки Excel|📋 Просмотреть Excel)$'), 
            handle_admin_menu
        ))

        # ... остальные обработчики ...

        # Запускаем бота
        logger.info("🤖 Бот запущен с интеграцией Google Sheets!")
        logger.info(f"👑 Администраторы: {ADMIN_CHAT_IDS}")
        if sheets_manager.connected:
            logger.info("📊 Google Sheets подключен и готов к работе")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
