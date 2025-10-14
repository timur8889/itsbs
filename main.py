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
)
from dotenv import load_dotenv
load_dotenv()

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
                self._create_sample_credentials()
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

    def _create_sample_credentials(self):
        """Создает пример файла credentials.json"""
        sample_credentials = {
            "type": "service_account",
            "project_id": "your-project-id",
            "private_key_id": "your-private-key-id",
            "private_key": "-----BEGIN PRIVATE KEY-----\nYOUR_PRIVATE_KEY\n-----END PRIVATE KEY-----\n",
            "client_email": "your-service-account@your-project.iam.gserviceaccount.com",
            "client_id": "your-client-id",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/your-service-account%40your-project.iam.gserviceaccount.com"
        }
        
        with open('credentials_sample.json', 'w', encoding='utf-8') as f:
            json.dump(sample_credentials, f, indent=2)
        
        logger.info("📝 Создан пример файла credentials_sample.json. Замените его на реальные учетные данные.")

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

# ==================== ОСНОВНЫЕ ФУНКЦИИ БОТА ====================

def start_request_creation(update: Update, context: CallbackContext) -> int:
    """Начинает процесс создания заявки"""
    context.user_data.clear()
    
    user = update.message.from_user
    context.user_data.update({
        'user_id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name
    })
    
    update.message.reply_text(
        "📝 *Создание новой заявки*\n\n"
        "Для начала укажите ваше имя:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def name(update: Update, context: CallbackContext) -> int:
    context.user_data['name'] = update.message.text
    update.message.reply_text(
        "📞 *Укажите ваш контактный телефон:*\n\nПример: +7 999 123-45-67",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    context.user_data['phone'] = update.message.text
    update.message.reply_text(
        "📍 *Выберите тип участка:*",
        reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLOT

def plot(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "Укажите ваше имя:",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "🔧 *Выберите тип системы:*",
        reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад в меню':
        return show_main_menu(update, context)
    elif update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📍 *Выберите тип участка:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        "📝 *Опишите проблему или необходимые работы:*\n\nПример: Не работает видеонаблюдение на фрезерном участке",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    context.user_data['problem'] = update.message.text
    update.message.reply_text(
        "⏰ *Выберите срочность выполнения работ:*",
        reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📝 *Опишите проблему или необходимые работы:*",
            reply_markup=ReplyKeyboardRemove()
        )
        return PROBLEM
    
    context.user_data['urgency'] = update.message.text
    update.message.reply_text(
        "📸 *Хотите добавить фото к заявке?*\n\nФото поможет специалисту лучше понять проблему.",
        reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "⏰ *Выберите срочность выполнения работ:*",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    elif update.message.text == '📷 Добавить фото':
        update.message.reply_text(
            "📸 *Отправьте фото:*",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHOTO
    elif update.message.text == '⏭️ Пропустить фото':
        context.user_data['photo'] = None
        return show_request_summary(update, context)
    elif update.message.photo:
        context.user_data['photo'] = update.message.photo[-1].file_id
        update.message.reply_text(
            "✅ Фото добавлено!",
            reply_markup=ReplyKeyboardRemove()
        )
        return show_request_summary(update, context)
    else:
        update.message.reply_text(
            "❌ Пожалуйста, отправьте фото или используйте кнопки.",
            reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True)
        )
        return PHOTO

def update_summary(context: CallbackContext) -> None:
    """Обновляет сводку заявки в user_data"""
    photo_status = "✅ Есть" if context.user_data.get('photo') else "❌ Нет"
    
    summary = (
        f"📋 *Сводка заявки:*\n\n"
        f"📛 *Имя:* {context.user_data['name']}\n"
        f"📞 *Телефон:* `{context.user_data['phone']}`\n"
        f"📍 *Участок:* {context.user_data['plot']}\n"
        f"🔧 *Тип системы:* {context.user_data['system_type']}\n"
        f"📝 *Описание:* {context.user_data['problem']}\n"
        f"⏰ *Срочность:* {context.user_data['urgency']}\n"
        f"📸 *Фото:* {photo_status}\n"
        f"🕒 *Время:* {context.user_data.get('timestamp', 'Не указано')}"
    )
    
    context.user_data['summary'] = summary

def show_request_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку заявки перед отправкой"""
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    update_summary(context)
    
    # Определяем, откуда пришли - из создания или редактирования
    if context.user_data.get('editing_mode'):
        # Режим редактирования - показываем меню редактирования
        return edit_request_choice(update, context)
    else:
        # Режим создания - показываем подтверждение
        if context.user_data.get('photo'):
            update.message.reply_photo(
                photo=context.user_data['photo'],
                caption=f"{context.user_data['summary']}\n\n*Подтвердите отправку заявки:*",
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                f"{context.user_data['summary']}\n\n*Подтвердите отправку заявки:*",
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        return ConversationHandler.END

def confirm_request(update: Update, context: CallbackContext) -> None:
    """Подтверждает и отправляет заявку"""
    if update.message.text == '✅ Подтвердить отправку':
        user = update.message.from_user
        
        try:
            # Сохраняем заявку в базу данных
            request_id = db.save_request(context.user_data)
            
            # Отправляем уведомление администраторам
            send_admin_notification(context, context.user_data, request_id)
            
            # Подтверждение пользователю
            confirmation_text = (
                f"✅ *Заявка #{request_id} успешно создана!*\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n\n"
                f"_Спасибо за обращение в службу слаботочных систем завода Контакт!_ 🛠️"
            )
            
            # Определяем клавиатуру в зависимости от прав
            if user.id in ADMIN_CHAT_IDS:
                # Администратору показываем админ-панель
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            
            logger.info(f"Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            
            # Определяем клавиатуру в зависимости от прав
            if user.id in ADMIN_CHAT_IDS:
                update.message.reply_text(
                    "❌ *Произошла ошибка при создании заявки.*\n\nПожалуйста, попробуйте позже.",
                    reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    "❌ *Произошла ошибка при создании заявки.*\n\nПожалуйста, попробуйте позже.",
                    reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
        
        context.user_data.clear()
        
    elif update.message.text == '✏️ Редактировать заявку':
        # Включаем режим редактирования
        context.user_data['editing_mode'] = True
        return edit_request_choice(update, context)

def send_admin_notification(context: CallbackContext, user_data: Dict, request_id: int) -> None:
    """Отправляет уведомление администраторам о новой заявке"""
    notification_text = (
        f"🚨 *НОВАЯ ЗАЯВКА #{request_id}*\n\n"
        f"👤 *Пользователь:* @{user_data.get('username', 'N/A')}\n"
        f"📛 *Имя:* {user_data.get('name')}\n"
        f"📞 *Телефон:* `{user_data.get('phone')}`\n"
        f"📍 *Участок:* {user_data.get('plot')}\n"
        f"🔧 *Система:* {user_data.get('system_type')}\n"
        f"⏰ *Срочность:* {user_data.get('urgency')}\n"
        f"📸 *Фото:* {'✅ Есть' if user_data.get('photo') else '❌ Нет'}\n\n"
        f"📝 *Описание:* {user_data.get('problem')}\n\n"
        f"🕒 *Время:* {user_data.get('timestamp', 'Не указано')}"
    )
    
    for admin_id in ADMIN_CHAT_IDS:
        try:
            if user_data.get('photo'):
                context.bot.send_photo(
                    chat_id=admin_id,
                    photo=user_data['photo'],
                    caption=notification_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")

def cancel_request(update: Update, context: CallbackContext) -> int:
    """Отменяет создание заявки"""
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        # Администратору показываем админ-панель
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True)
        )
    else:
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
    
    context.user_data.clear()
    return ConversationHandler.END

# ==================== ФУНКЦИИ РЕДАКТИРОВАНИЯ ====================

def edit_request_choice(update: Update, context: CallbackContext) -> int:
    """Показывает меню выбора поля для редактирования"""
    summary = context.user_data.get('summary', '')
    
    update.message.reply_text(
        f"{summary}\n\n"
        "✏️ *Выберите поле для редактирования:*",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return EDIT_CHOICE

def handle_edit_choice(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор поля для редактирования"""
    choice = update.message.text
    context.user_data['editing_field'] = choice
    
    if choice == '📛 Редактировать имя':
        update.message.reply_text(
            "✏️ *Введите новое имя:*\n\nТекущее имя: " + context.user_data.get('name', 'Не указано'),
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📞 Редактировать телефон':
        update.message.reply_text(
            f"✏️ *Введите новый телефон:*\n\nПример: +7 999 123-45-67\nТекущий телефон: `{context.user_data.get('phone', 'Не указано')}`",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📍 Редактировать участок':
        update.message.reply_text(
            f"✏️ *Выберите новый участок:*\n\nТекущий участок: {context.user_data.get('plot', 'Не указан')}",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '🔧 Редактировать систему':
        update.message.reply_text(
            f"✏️ *Выберите новую систему:*\n\nТекущая система: {context.user_data.get('system_type', 'Не указана')}",
            reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📝 Редактировать описание':
        update.message.reply_text(
            f"✏️ *Введите новое описание проблемы:*\n\nТекущее описание: {context.user_data.get('problem', 'Не указано')}",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '⏰ Редактировать срочность':
        update.message.reply_text(
            f"✏️ *Выберите новую срочность:*\n\nТекущая срочность: {context.user_data.get('urgency', 'Не указана')}",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📷 Редактировать фото':
        photo_status = "✅ Есть" if context.user_data.get('photo') else "❌ Нет"
        update.message.reply_text(
            f"✏️ *Редактирование фото:*\n\nТекущий статус фото: {photo_status}",
            reply_markup=ReplyKeyboardMarkup([
                ['📷 Добавить новое фото', '🗑️ Удалить фото'],
                ['🔙 Назад к редактированию']
            ], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '✅ Завершить редактирование':
        # Завершаем редактирование и возвращаемся к сводке
        context.user_data.pop('editing_mode', None)
        context.user_data.pop('editing_field', None)
        return show_request_summary(update, context)
    
    else:
        update.message.reply_text(
            "❌ Пожалуйста, выберите поле для редактирования из меню.",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        return EDIT_CHOICE

def handle_edit_field(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод новых данных для поля"""
    editing_field = context.user_data.get('editing_field')
    text = update.message.text
    
    # Обработка кнопки "Назад"
    if text == '🔙 Назад к редактированию':
        return edit_request_choice(update, context)
    
    # Обработка фото
    if update.message.photo:
        context.user_data['photo'] = update.message.photo[-1].file_id
        update.message.reply_text(
            "✅ Фото обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        # Обновляем сводку и возвращаемся к меню редактирования
        context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
        update_summary(context)
        return edit_request_choice(update, context)
    
    # Обработка текстовых полей
    if editing_field == '📛 Редактировать имя':
        if text and text != '🔙 Назад к редактированию':
            context.user_data['name'] = text
            update.message.reply_text(
                "✅ Имя обновлено!",
                reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
            )
        else:
            update.message.reply_text(
                "❌ Имя не может быть пустым.",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        
    elif editing_field == '📞 Редактировать телефон':
        if text and text != '🔙 Назад к редактированию':
            context.user_data['phone'] = text
            update.message.reply_text(
                "✅ Телефон обновлен!",
                reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
            )
        else:
            update.message.reply_text(
                "❌ Телефон не может быть пустым.",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        
    elif editing_field == '📍 Редактировать участок':
        if text in ['🔙 Назад', '🔙 Назад в меню']:
            return edit_request_choice(update, context)
        elif text and text != '🔙 Назад к редактированию':
            context.user_data['plot'] = text
            update.message.reply_text(
                "✅ Участок обновлен!",
                reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
            )
        else:
            update.message.reply_text(
                "❌ Пожалуйста, выберите участок из меню.",
                reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        
    elif editing_field == '🔧 Редактировать систему':
        if text in ['🔙 Назад', '🔙 Назад в меню']:
            return edit_request_choice(update, context)
        elif text and text != '🔙 Назад к редактированию':
            context.user_data['system_type'] = text
            update.message.reply_text(
                "✅ Система обновлена!",
                reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
            )
        else:
            update.message.reply_text(
                "❌ Пожалуйста, выберите систему из меню.",
                reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        
    elif editing_field == '📝 Редактировать описание':
        if text and text != '🔙 Назад к редактированию':
            context.user_data['problem'] = text
            update.message.reply_text(
                "✅ Описание обновлено!",
                reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
            )
        else:
            update.message.reply_text(
                "❌ Описание не может быть пустым.",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        
    elif editing_field == '⏰ Редактировать срочность':
        if text == '🔙 Назад':
            return edit_request_choice(update, context)
        elif text and text != '🔙 Назад к редактированию':
            context.user_data['urgency'] = text
            update.message.reply_text(
                "✅ Срочность обновлена!",
                reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
            )
        else:
            update.message.reply_text(
                "❌ Пожалуйста, выберите срочность из меню.",
                reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        
    elif editing_field == '📷 Редактировать фото':
        if text == '📷 Добавить новое фото':
            update.message.reply_text(
                "📸 Отправьте новое фото:",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        elif text == '🗑️ Удалить фото':
            context.user_data['photo'] = None
            update.message.reply_text(
                "✅ Фото удалено!",
                reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
            )
        else:
            update.message.reply_text(
                "❌ Пожалуйста, выберите действие из меню.",
                reply_markup=ReplyKeyboardMarkup([
                    ['📷 Добавить новое фото', '🗑️ Удалить фото'],
                    ['🔙 Назад к редактированию']
                ], resize_keyboard=True)
            )
            return EDIT_FIELD
    
    # Обновляем сводку
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    update_summary(context)
    return edit_request_choice(update, context)

# ==================== ВИЗУАЛЬНОЕ МЕНЮ ====================

def get_admin_panel_with_counters():
    """Возвращает админ-панель с актуальными счетчиками"""
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    
    return [
        [f'🆕 Новые заявки ({len(new_requests)})', f'🔄 В работе ({len(in_progress_requests)})'],
        ['✅ Выполненные заявки', '📊 Статистика'],
        ['🔧 Управление', '📊 Excel онлайн']
    ]

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Показывает главное меню"""
    user = update.message.from_user
    user_id = user.id
    
    # Обновляем активность пользователя
    if user_id not in ADMIN_CHAT_IDS:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at, last_activity)
                VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM users WHERE user_id = ?), ?), ?)
            ''', (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                user.id,
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.commit()
    
    # Определяем клавиатуру в зависимости от прав
    if user_id in ADMIN_CHAT_IDS:
        # Администратору показываем сразу админ-панель
        return show_admin_panel(update, context)
    else:
        keyboard = user_main_menu_keyboard
        welcome_text = (
            "🏭 *Добро пожаловать в сервис заявок для слаботочных систем завода Контакт!*\n\n"
            "🔧 *Мы обслуживаем:*\n"
            "• 📹 Системы видеонаблюдения\n"
            "• 🔐 Системы контроля доступа (СКУД)\n" 
            "• 🌐 Компьютерные сети\n"
            "• 🚨 Пожарные сигнализации\n\n"
            "Выберите действие из меню ниже:"
        )
    
    update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает админ-панель с новыми, в работе и выполненными заявками"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu(update, context)
    
    # Получаем количество заявок по статусам
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    completed_requests = db.get_requests_by_filter('completed')
    
    admin_text = (
        "👑 *Админ-панель завода Контакт*\n\n"
        f"🆕 *Новых заявок:* {len(new_requests)}\n"
        f"🔄 *Заявок в работе:* {len(in_progress_requests)}\n"
        f"✅ *Выполненных заявок:* {len(completed_requests)}\n\n"
        "Выберите раздел для просмотра:"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ ====================

def show_my_requests(update: Update, context: CallbackContext) -> None:
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        return show_admin_panel(update, context)
    else:
        keyboard = user_main_menu_keyboard
    
    requests = db.get_user_requests(user_id, 50)
    
    if not requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок.\n\n"
            "Хотите создать первую заявку?",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return
    
    update.message.reply_text(
        f"📋 *Ваши заявки ({len(requests)}):*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    
    for req in requests:
        status_icons = {
            'new': '🆕',
            'in_progress': '🔄', 
            'completed': '✅'
        }
        
        request_text = (
            f"{status_icons.get(req['status'], '📋')} *Заявка #{req['id']}*\n"
            f"🔧 *Тип:* {req['system_type']}\n"
            f"📍 *Участок:* {req['plot']}\n"
            f"⏰ *Срочность:* {req['urgency']}\n"
            f"🔄 *Статус:* {req['status']}\n"
            f"🕒 *Создана:* {req['created_at'][:16]}\n"
        )
        
        if req.get('assigned_admin') and req['status'] == 'in_progress':
            request_text += f"👨‍💼 *Исполнитель:* {req['assigned_admin']}\n"
        
        update.message.reply_text(request_text, parse_mode=ParseMode.MARKDOWN)

def show_requests_by_filter(update: Update, context: CallbackContext, filter_type: str) -> None:
    """Показывает заявки по фильтру"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    requests = db.get_requests_by_filter(filter_type, 50)
    filter_names = {
        'new': '🆕 Новые заявки',
        'in_progress': '🔄 Заявки в работе',
        'completed': '✅ Выполненные заявки'
    }
    
    if not requests:
        update.message.reply_text(
            f"📭 {filter_names[filter_type]} отсутствуют.",
            reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True)
        )
        return
    
    update.message.reply_text(
        f"{filter_names[filter_type]} ({len(requests)}):",
        reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True)
    )
    
    for req in requests:
        request_text = (
            f"*Заявка #{req['id']}*\n"
            f"👤 *Клиент:* {req['name']}\n"
            f"📞 *Телефон:* `{req['phone']}`\n"
            f"📍 *Участок:* {req['plot']}\n"
            f"🔧 *Тип системы:* {req['system_type']}\n"
            f"⏰ *Срочность:* {req['urgency']}\n"
            f"📝 *Описание:* {req['problem'][:100]}...\n"
            f"🔄 *Статус:* {req['status']}\n"
            f"🕒 *Создана:* {req['created_at'][:16]}"
        )
        
        update.message.reply_text(request_text, parse_mode=ParseMode.MARKDOWN)

def show_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает статистику бота"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    stats = db.get_statistics()
    
    stats_text = (
        "📊 *Статистика бота*\n\n"
        f"👥 *Всего пользователей:* {stats['total_users']}\n"
        f"📋 *Всего заявок:* {stats['total_requests']}\n"
        f"🆕 *Новых заявок:* {stats['new_requests']}\n"
        f"🔄 *В работе:* {stats['in_progress_requests']}\n"
        f"✅ *Выполнено:* {stats['completed_requests']}\n"
        f"📅 *Заявок сегодня:* {stats['today_requests']}\n"
        f"📅 *Заявок вчера:* {stats['yesterday_requests']}"
    )
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_admin_management(update: Update, context: CallbackContext) -> None:
    """Показывает меню управления для админов"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    management_text = (
        "🔧 *Панель управления администратора*\n\n"
        "Выберите действие:"
    )
    
    update.message.reply_text(
        management_text,
        reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def start_broadcast(update: Update, context: CallbackContext) -> None:
    """Начинает процесс рассылки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    context.user_data['broadcast_mode'] = True
    update.message.reply_text(
        "📢 *Режим рассылки*\n\n"
        "Введите сообщение для рассылки всем пользователям:",
        reply_markup=ReplyKeyboardMarkup([['❌ Отменить рассылку']], resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def send_broadcast(update: Update, context: CallbackContext) -> None:
    """Отправляет рассылку всем пользователям"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS or not context.user_data.get('broadcast_mode'):
        return show_admin_management(update, context)
    
    if update.message.text == '❌ Отменить рассылку':
        context.user_data.pop('broadcast_mode', None)
        return show_admin_management(update, context)
    
    message_text = update.message.text
    
    # Получаем всех пользователей
    users = db.get_all_users()
    
    success_count = 0
    fail_count = 0
    
    for user in users:
        try:
            context.bot.send_message(
                chat_id=user['user_id'],
                text=f"📢 *Объявление от службы слаботочных систем:*\n\n{message_text}",
                parse_mode=ParseMode.MARKDOWN
            )
            success_count += 1
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {user['user_id']}: {e}")
            fail_count += 1
    
    # Отчет о рассылке
    report_text = (
        f"✅ *Рассылка завершена!*\n\n"
        f"📤 *Отправлено успешно:* {success_count}\n"
        f"❌ *Не удалось отправить:* {fail_count}\n"
        f"📝 *Всего пользователей:* {len(users)}"
    )
    
    update.message.reply_text(
        report_text,
        reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data.pop('broadcast_mode', None)

def export_requests(update: Update, context: CallbackContext) -> None:
    """Экспортирует заявки в CSV файл"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    try:
        filename = db.export_requests_to_csv()
        
        # Отправляем файл
        with open(filename, 'rb') as file:
            context.bot.send_document(
                chat_id=update.message.chat_id,
                document=file,
                filename=filename,
                caption="📁 *Экспорт заявок в CSV формате*\n\nФайл содержит все заявки из системы."
            )
        
        # Удаляем временный файл
        os.remove(filename)
        
    except Exception as e:
        logger.error(f"Ошибка экспорта заявок: {e}")
        update.message.reply_text(
            "❌ Ошибка при экспорте заявок.",
            reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True)
        )

def update_counters(update: Update, context: CallbackContext) -> None:
    """Обновляет счетчики в реальном времени"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    update.message.reply_text(
        "✅ Счетчики обновлены!",
        reply_markup=ReplyKeyboardMarkup(get_admin_panel_with_counters(), resize_keyboard=True)
    )

def handle_broadcast_message(update: Update, context: CallbackContext) -> None:
    """Обрабатывает сообщения в режиме рассылки"""
    if context.user_data.get('broadcast_mode'):
        return send_broadcast(update, context)
    else:
        return handle_admin_menu(update, context)

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================

def handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в главном меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Для администраторов показываем только админ-панель
    if user_id in ADMIN_CHAT_IDS:
        return show_admin_panel(update, context)
    
    # Для обычных пользователей
    if text == '📝 Создать заявку':
        return start_request_creation(update, context)
    elif text == '📋 Мои заявки':
        return show_my_requests(update, context)
    else:
        update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )

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

# ==================== ОСНОВНЫЕ ФУНКЦИИ ====================

def main() -> None:
    """Запускаем бота"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Обработчик создания заявки
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(📝 Создать заявку)$'), start_request_creation),
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
                MessageHandler(Filters.regex('^(✅ Завершить редактирование)$'), show_request_summary)
            ],
            allow_reentry=True
        )

        # Отдельный обработчик для кнопки редактирования заявки
        edit_handler = MessageHandler(
            Filters.regex('^(✏️ Редактировать заявку)$'), 
            confirm_request
        )

        # Регистрируем обработчики команд
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        
        # Обработчики разговоров
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(edit_handler)
        
        # Обработчики кнопок
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить отправку)$'), confirm_request))
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📋 Мои заявки)$'), handle_main_menu))
        
        # Обработчики админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки|🔄 В работе|✅ Выполненные заявки|📊 Статистика|🔧 Управление|📊 Excel онлайн|📢 Сделать рассылку|🔄 Обновить счетчики|📁 Экспорт заявок|🔄 Синхронизировать с Excel|🔙 Назад в админ-панель|🔄 Обновить данные|📊 Статистика Excel|⚙️ Настройки Excel|📋 Просмотреть Excel)$'), 
            handle_admin_menu
        ))
        
        # Обработчик рассылки
        dispatcher.add_handler(MessageHandler(
            Filters.text & ~Filters.command,
            handle_broadcast_message
        ))

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
