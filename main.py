import logging
import sqlite3
import os
import json
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

# Определяем этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD = range(9)

# База данных
DB_PATH = "requests.db"

# ==================== УЛУЧШЕННЫЕ КЛАВИАТУРЫ АДМИН-ПАНЕЛИ ====================

# Главное меню пользователя
user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки']
]

# Главное меню администратора
admin_main_menu_keyboard = [
    ['👑 Админ-панель']
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

# УЛУЧШЕННАЯ АДМИН-ПАНЕЛЬ
admin_panel_keyboard = [
    ['📊 Статистика', '👥 Пользователи'],
    ['🆕 Новые заявки', '🔄 В работе'],
    ['🚨 Срочные заявки', '✅ Завершенные'],
    ['🔍 Поиск заявки', '⚙️ Настройки'],
    ['🔙 Главное меню']
]

admin_stats_keyboard = [
    ['📈 За сегодня', '📅 За неделю'],
    ['📆 За месяц', '🗓️ За все время'],
    ['📊 Сравнительная статистика', '🔙 Админ-панель']
]

admin_settings_keyboard = [
    ['👥 Управление админами', '🔔 Настройка уведомлений'],
    ['📝 Шаблоны ответов', '🔄 Сброс статистики'],
    ['🔙 Админ-панель']
]

admin_users_keyboard = [
    ['👥 Активные пользователи', '📈 Топ пользователей'],
    ['📊 Статистика по пользователям', '🔙 Админ-панель']
]

# ==================== УЛУЧШЕННАЯ БАЗА ДАННЫХ ====================

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
                    completed_at TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS statistics (
                    date TEXT PRIMARY KEY,
                    requests_count INTEGER DEFAULT 0,
                    completed_count INTEGER DEFAULT 0,
                    avg_completion_time REAL DEFAULT 0
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
                CREATE TABLE IF NOT EXISTS admin_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
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
            return request_id

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

    def get_statistics(self, period: str = 'week') -> Dict:
        """Получает статистику за указанный период"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if period == 'today':
                start_date = datetime.now().strftime('%Y-%m-%d')
            elif period == 'week':
                start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            elif period == 'month':
                start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            else:  # all time
                start_date = '2000-01-01'
            
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_requests,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                    SUM(CASE WHEN urgency LIKE '%Срочно%' THEN 1 ELSE 0 END) as urgent,
                    AVG(CASE WHEN status = 'completed' THEN 
                        (julianday(completed_at) - julianday(created_at)) * 24 
                    END) as avg_completion_hours
                FROM requests 
                WHERE created_at >= ?
            ''', (start_date,))
            
            result = cursor.fetchone()
            
            # Получаем количество пользователей
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]
            
            # Получаем активных пользователей (за последние 30 дней)
            active_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            cursor.execute('SELECT COUNT(*) FROM users WHERE last_activity >= ?', (active_date,))
            active_users = cursor.fetchone()[0]
            
            return {
                'total_requests': result[0] or 0,
                'completed': result[1] or 0,
                'new': result[2] or 0,
                'in_progress': result[3] or 0,
                'urgent': result[4] or 0,
                'avg_completion_hours': round(result[5] or 0, 1),
                'total_users': total_users,
                'active_users': active_users
            }

    def get_comparative_statistics(self) -> Dict:
        """Получает сравнительную статистику"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            periods = {
                'today': datetime.now().strftime('%Y-%m-%d'),
                'yesterday': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                'week': (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'),
                'last_week': (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
            }
            
            stats = {}
            for period, date in periods.items():
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                    FROM requests 
                    WHERE created_at >= ?
                ''', (date,))
                result = cursor.fetchone()
                stats[period] = {
                    'total': result[0] or 0,
                    'completed': result[1] or 0
                }
            
            return stats

    def get_user_statistics(self, limit: int = 10) -> List[Dict]:
        """Получает статистику по пользователям"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    user_id,
                    username,
                    first_name,
                    last_name,
                    request_count,
                    last_activity,
                    (SELECT COUNT(*) FROM requests WHERE user_id = users.user_id AND status = 'completed') as completed_count
                FROM users 
                ORDER BY request_count DESC 
                LIMIT ?
            ''', (limit,))
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

    def search_requests(self, search_term: str, limit: int = 20) -> List[Dict]:
        """Ищет заявки по различным полям"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests 
                WHERE id = ? OR name LIKE ? OR phone LIKE ? OR problem LIKE ? OR plot LIKE ?
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (
                search_term if search_term.isdigit() else -1,
                f'%{search_term}%',
                f'%{search_term}%',
                f'%{search_term}%',
                f'%{search_term}%',
                limit
            ))
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

    def update_request_status(self, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
        """Обновляет статус заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            completed_at = datetime.now().isoformat() if status == 'completed' else None
            
            if admin_comment and assigned_admin:
                cursor.execute('''
                    UPDATE requests SET status = ?, admin_comment = ?, assigned_admin = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                ''', (status, admin_comment, assigned_admin, datetime.now().isoformat(), completed_at, request_id))
            elif admin_comment:
                cursor.execute('''
                    UPDATE requests SET status = ?, admin_comment = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                ''', (status, admin_comment, datetime.now().isoformat(), completed_at, request_id))
            elif assigned_admin:
                cursor.execute('''
                    UPDATE requests SET status = ?, assigned_admin = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                ''', (status, assigned_admin, datetime.now().isoformat(), completed_at, request_id))
            else:
                cursor.execute('''
                    UPDATE requests SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?
                ''', (status, datetime.now().isoformat(), completed_at, request_id))
            
            if status == 'completed':
                today = datetime.now().strftime('%Y-%m-%d')
                cursor.execute('''
                    UPDATE statistics SET completed_count = completed_count + 1
                    WHERE date = ?
                ''', (today,))
            
            conn.commit()

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

# Инициализация базы данных
db = Database(DB_PATH)

# ==================== УЛУЧШЕННАЯ АДМИН-ПАНЕЛЬ ====================

def show_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает улучшенную админ-панель"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu(update, context)
    
    stats = db.get_statistics('today')
    admin_text = (
        "👑 *Улучшенная админ-панель завода Контакт*\n\n"
        "📊 *Быстрая статистика за сегодня:*\n"
        f"• 🆕 Новых заявок: {stats['new']}\n"
        f"• 🔄 В работе: {stats['in_progress']}\n"
        f"• ✅ Завершено: {stats['completed']}\n"
        f"• 🚨 Срочных: {stats['urgent']}\n"
        f"• ⏱️ Среднее время выполнения: {stats['avg_completion_hours']} ч.\n"
        f"• 👥 Активных пользователей: {stats['active_users']}\n\n"
        "🎛️ *Выберите раздел для управления:*"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_admin_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает улучшенную статистику"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    update.message.reply_text(
        "📊 *Расширенная статистика системы*\n\n"
        "Выберите тип статистики для просмотра:",
        reply_markup=ReplyKeyboardMarkup(admin_stats_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_comparative_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает сравнительную статистику"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    stats = db.get_comparative_statistics()
    
    today = stats['today']
    yesterday = stats['yesterday']
    week = stats['week']
    last_week = stats['last_week']
    
    # Расчет изменений
    today_change = today['total'] - yesterday['total']
    week_change = week['total'] - last_week['total']
    
    stats_text = (
        "📈 *Сравнительная статистика*\n\n"
        
        "📅 *Сегодня vs Вчера:*\n"
        f"• Сегодня: {today['total']} заявок ({today['completed']} выполнено)\n"
        f"• Вчера: {yesterday['total']} заявок ({yesterday['completed']} выполнено)\n"
        f"• Изменение: {'📈 +' if today_change >= 0 else '📉 '}{today_change}\n\n"
        
        "📊 *Неделя vs Прошлая неделя:*\n"
        f"• Эта неделя: {week['total']} заявок ({week['completed']} выполнено)\n"
        f"• Прошлая неделя: {last_week['total']} заявок ({last_week['completed']} выполнено)\n"
        f"• Изменение: {'📈 +' if week_change >= 0 else '📉 '}{week_change}\n\n"
        
        "📋 *Эффективность:*\n"
        f"• Сегодня: {round(today['completed'] / max(today['total'], 1) * 100, 1)}%\n"
        f"• Неделя: {round(week['completed'] / max(week['total'], 1) * 100, 1)}%"
    )
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(admin_stats_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_admin_users(update: Update, context: CallbackContext) -> None:
    """Показывает управление пользователями"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    stats = db.get_statistics('all')
    
    users_text = (
        "👥 *Управление пользователями*\n\n"
        f"📊 *Общая статистика пользователей:*\n"
        f"• Всего пользователей: {stats['total_users']}\n"
        f"• Активных пользователей: {stats['active_users']}\n"
        f"• Всего заявок от пользователей: {stats['total_requests']}\n"
        f"• Среднее время выполнения: {stats['avg_completion_hours']} ч.\n\n"
        "Выберите раздел для просмотра:"
    )
    
    update.message.reply_text(
        users_text,
        reply_markup=ReplyKeyboardMarkup(admin_users_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_top_users(update: Update, context: CallbackContext) -> None:
    """Показывает топ пользователей"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    users = db.get_user_statistics(10)
    
    if not users:
        update.message.reply_text(
            "📭 Пользователи не найдены.",
            reply_markup=ReplyKeyboardMarkup(admin_users_keyboard, resize_keyboard=True)
        )
        return
    
    users_text = "🏆 *Топ 10 пользователей по количеству заявок:*\n\n"
    
    for i, user in enumerate(users, 1):
        user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        if not user_name:
            user_name = f"@{user.get('username', 'Пользователь')}"
        
        users_text += (
            f"{i}. {user_name}\n"
            f"   📞 Заявок: {user['request_count']}\n"
            f"   ✅ Выполнено: {user.get('completed_count', 0)}\n"
            f"   🕒 Активность: {user['last_activity'][:10] if user.get('last_activity') else 'Нет данных'}\n\n"
        )
    
    update.message.reply_text(
        users_text,
        reply_markup=ReplyKeyboardMarkup(admin_users_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_admin_settings(update: Update, context: CallbackContext) -> None:
    """Показывает настройки админ-панели"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    settings_text = (
        "⚙️ *Настройки админ-панели*\n\n"
        "Здесь вы можете настроить различные параметры системы:\n\n"
        "• 👥 *Управление админами* - добавление/удаление администраторов\n"
        "• 🔔 *Настройка уведомлений* - управление оповещениями\n"
        "• 📝 *Шаблоны ответов* - создание стандартных ответов\n"
        "• 🔄 *Сброс статистики* - очистка статистических данных\n\n"
        "Выберите раздел для настройки:"
    )
    
    update.message.reply_text(
        settings_text,
        reply_markup=ReplyKeyboardMarkup(admin_settings_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def search_requests(update: Update, context: CallbackContext) -> None:
    """Поиск заявок"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    context.user_data['waiting_for_search'] = True
    update.message.reply_text(
        "🔍 *Поиск заявок*\n\n"
        "Введите номер заявки, имя клиента, телефон, участок или описание проблемы:",
        reply_markup=ReplyKeyboardMarkup([['🔙 Отмена поиска']], resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_search(update: Update, context: CallbackContext) -> None:
    """Обрабатывает поисковый запрос"""
    if not context.user_data.get('waiting_for_search'):
        return show_admin_panel(update, context)
    
    search_term = update.message.text.strip()
    
    if search_term == '🔙 Отмена поиска':
        context.user_data.pop('waiting_for_search', None)
        return show_admin_panel(update, context)
    
    requests = db.search_requests(search_term, 20)
    
    if not requests:
        update.message.reply_text(
            f"🔍 *Результаты поиска по запросу: '{search_term}'*\n\n"
            "❌ Заявки не найдены.\n\n"
            "Попробуйте другой поисковый запрос:",
            reply_markup=ReplyKeyboardMarkup([['🔙 Отмена поиска']], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    update.message.reply_text(
        f"🔍 *Результаты поиска по запросу: '{search_term}'*\n\n"
        f"📋 Найдено заявок: {len(requests)}",
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True)
    )
    
    for req in requests:
        status_icons = {'new': '🆕', 'in_progress': '🔄', 'completed': '✅'}
        
        request_text = (
            f"{status_icons.get(req['status'], '📋')} *Заявка #{req['id']}*\n"
            f"👤 *Клиент:* {req['name']}\n"
            f"📞 *Телефон:* `{req['phone']}`\n"
            f"📍 *Участок:* {req['plot']}\n"
            f"🔧 *Тип:* {req['system_type']}\n"
            f"⏰ *Срочность:* {req['urgency']}\n"
            f"🔄 *Статус:* {req['status']}\n"
            f"🕒 *Создана:* {req['created_at'][:16]}\n"
            f"📝 *Описание:* {req['problem'][:100]}..."
        )
        
        if req.get('assigned_admin'):
            request_text += f"\n👨‍💼 *Исполнитель:* {req['assigned_admin']}"
        
        # Кнопки действий
        keyboard = None
        if req['status'] == 'new':
            keyboard = [[
                InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{req['id']}"),
                InlineKeyboardButton("📋 Подробнее", callback_data=f"view_{req['id']}")
            ]]
        elif req['status'] == 'in_progress':
            keyboard = [[
                InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{req['id']}"),
                InlineKeyboardButton("📋 Подробнее", callback_data=f"view_{req['id']}")
            ]]
        
        if req.get('photo'):
            if keyboard:
                update.message.reply_photo(
                    photo=req['photo'],
                    caption=request_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_photo(
                    photo=req['photo'],
                    caption=request_text,
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            if keyboard:
                update.message.reply_text(
                    request_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    request_text,
                    parse_mode=ParseMode.MARKDOWN
                )
    
    context.user_data.pop('waiting_for_search', None)

# ==================== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ====================

def handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в улучшенном админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if text == '📊 Статистика':
        return show_admin_statistics(update, context)
    elif text == '👥 Пользователи':
        return show_admin_users(update, context)
    elif text == '🆕 Новые заявки':
        return show_requests_by_filter(update, context, 'new')
    elif text == '🔄 В работе':
        return show_requests_by_filter(update, context, 'my_in_progress')
    elif text == '🚨 Срочные заявки':
        return show_requests_by_filter(update, context, 'urgent')
    elif text == '✅ Завершенные':
        return show_requests_by_filter(update, context, 'completed')
    elif text == '🔍 Поиск заявки':
        return search_requests(update, context)
    elif text == '⚙️ Настройки':
        return show_admin_settings(update, context)
    elif text == '🔙 Главное меню':
        return show_main_menu(update, context)
    elif text == '🔙 Админ-панель':
        return show_admin_panel(update, context)
    elif text == '📊 Сравнительная статистика':
        return show_comparative_statistics(update, context)
    elif text == '👥 Активные пользователи':
        return show_top_users(update, context)
    elif text == '📈 Топ пользователей':
        return show_top_users(update, context)
    else:
        # Если это поисковый запрос
        if context.user_data.get('waiting_for_search'):
            return handle_search(update, context)
        else:
            update.message.reply_text(
                "Пожалуйста, выберите действие из меню:",
                reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True)
            )

# Остальной код остается без изменений (функции создания заявок, обработчики callback и т.д.)
# Добавляем только новые обработчики в основную функцию

def main() -> None:
    """Запускаем бота с улучшенной админ-панелью"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Обработчик создания заявки (включая редактирование)
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

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(edit_handler)
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить отправку)$'), confirm_request))
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📋 Мои заявки|👑 Админ-панель)$'), handle_main_menu))
        
        # Обработчики улучшенной админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📊 Статистика|👥 Пользователи|🆕 Новые заявки|🔄 В работе|🚨 Срочные заявки|✅ Завершенные|🔍 Поиск заявки|⚙️ Настройки|🔙 Главное меню|🔙 Админ-панель)$'), 
            handle_admin_menu
        ))
        
        # Обработчики статистики
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📈 За сегодня|📅 За неделю|📆 За месяц|🗓️ За все время|📊 Сравнительная статистика)$'), 
            handle_admin_menu
        ))
        
        # Обработчики пользователей
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(👥 Активные пользователи|📈 Топ пользователей|📊 Статистика по пользователям)$'), 
            handle_admin_menu
        ))
        
        # Обработчики настроек
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(👥 Управление админами|🔔 Настройка уведомлений|📝 Шаблоны ответов|🔄 Сброс статистики)$'), 
            handle_admin_menu
        ))
        
        # Обработчик поиска (любой текст, когда ожидается поиск)
        dispatcher.add_handler(MessageHandler(
            Filters.text & ~Filters.command, 
            handle_admin_menu
        ))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(handle_admin_callback, pattern='^(take_|view_|complete_|contact_)'))

        # Запускаем с главного меню
        logger.info("🤖 Бот запущен с улучшенной админ-панелью!")
        logger.info(f"👑 Администраторы: {ADMIN_CHAT_IDS}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
