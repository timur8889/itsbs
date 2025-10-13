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
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Замените на реальный токен бота

# Определяем этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY = range(7)

# База данных
DB_PATH = "requests.db"

# ==================== КЛАВИАТУРЫ ====================

# Главное меню пользователя (базовое)
base_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки']
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

# Админ-панель
admin_main_keyboard = [
    ['📊 Статистика', '📋 Активные заявки'],
    ['🔙 Главное меню']
]

admin_stats_keyboard = [
    ['📈 За сегодня', '📅 За неделю'],
    ['📆 За месяц', '🗓️ За все время'],
    ['🔙 Админ-панель']
]

admin_requests_keyboard = [
    ['🆕 Новые заявки', '🔄 В работе'],
    ['📤 Все активные'],
    ['🔙 Админ-панель']
]

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
                    assigned_admin TEXT
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
                    request_count INTEGER DEFAULT 0
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
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at, request_count)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT request_count FROM users WHERE user_id = ?), 0) + 1)
            ''', (
                user_data.get('user_id'),
                user_data.get('username'),
                user_data.get('first_name', ''),
                user_data.get('last_name', ''),
                datetime.now().isoformat(),
                user_data.get('user_id')
            ))
            
            conn.commit()
            return request_id

    def get_user_requests(self, user_id: int, limit: int = 5) -> List[Dict]:
        """Получает заявки пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (user_id, limit))
            return [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

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
                    SUM(CASE WHEN urgency LIKE '%Срочно%' THEN 1 ELSE 0 END) as urgent
                FROM requests 
                WHERE created_at >= ?
            ''', (start_date,))
            
            result = cursor.fetchone()
            
            # Получаем количество пользователей
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]
            
            return {
                'total_requests': result[0] or 0,
                'completed': result[1] or 0,
                'new': result[2] or 0,
                'in_progress': result[3] or 0,
                'urgent': result[4] or 0,
                'total_users': total_users
            }

    def get_active_requests(self, filter_type: str = 'all') -> List[Dict]:
        """Получает активные заявки с фильтром"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if filter_type == 'new':
                status_filter = "status = 'new'"
            elif filter_type == 'in_progress':
                status_filter = "status = 'in_progress'"
            elif filter_type == 'urgent':
                status_filter = "urgency LIKE '%Срочно%' AND status IN ('new', 'in_progress')"
            else:
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
            ''')
            return [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

    def get_request(self, request_id: int) -> Dict:
        """Получает заявку по ID"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
            row = cursor.fetchone()
            if row:
                return dict(zip([column[0] for column in cursor.description], row))
            return {}

    def update_request_status(self, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
        """Обновляет статус заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if admin_comment and assigned_admin:
                cursor.execute('''
                    UPDATE requests SET status = ?, admin_comment = ?, assigned_admin = ?, updated_at = ?
                    WHERE id = ?
                ''', (status, admin_comment, assigned_admin, datetime.now().isoformat(), request_id))
            elif admin_comment:
                cursor.execute('''
                    UPDATE requests SET status = ?, admin_comment = ?, updated_at = ?
                    WHERE id = ?
                ''', (status, admin_comment, datetime.now().isoformat(), request_id))
            elif assigned_admin:
                cursor.execute('''
                    UPDATE requests SET status = ?, assigned_admin = ?, updated_at = ?
                    WHERE id = ?
                ''', (status, assigned_admin, datetime.now().isoformat(), request_id))
            else:
                cursor.execute('''
                    UPDATE requests SET status = ?, updated_at = ? WHERE id = ?
                ''', (status, datetime.now().isoformat(), request_id))
            
            if status == 'completed':
                today = datetime.now().strftime('%Y-%m-%d')
                cursor.execute('''
                    UPDATE statistics SET completed_count = completed_count + 1
                    WHERE date = ?
                ''', (today,))
            
            conn.commit()

# Инициализация базы данных
db = Database(DB_PATH)

# ==================== ВИЗУАЛЬНОЕ МЕНЮ ====================

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Показывает главное меню"""
    user = update.message.from_user
    user_id = user.id
    
    # Создаем клавиатуру в зависимости от прав
    keyboard = base_main_menu_keyboard.copy()
    if user_id in ADMIN_CHAT_IDS:
        keyboard.append(['👑 Админ-панель'])
    
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

def show_my_requests(update: Update, context: CallbackContext) -> None:
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    requests = db.get_user_requests(user_id)
    
    # Создаем клавиатуру в зависимости от прав
    keyboard = base_main_menu_keyboard.copy()
    if user_id in ADMIN_CHAT_IDS:
        keyboard.append(['👑 Админ-панель'])
    
    if not requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок.\n\n"
            "Хотите создать первую заявку?",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return
    
    update.message.reply_text(
        f"📋 *Ваши последние заявки ({len(requests)}):*",
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
        
        if req.get('admin_comment'):
            request_text += f"💬 *Комментарий:* {req['admin_comment']}\n"
        
        update.message.reply_text(request_text, parse_mode=ParseMode.MARKDOWN)

# ==================== АДМИН-ПАНЕЛЬ ====================

def show_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает админ-панель"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu(update, context)
    
    stats = db.get_statistics('today')
    admin_text = (
        "👑 *Админ-панель завода Контакт*\n\n"
        "📊 *Сегодня:*\n"
        f"• Новых заявок: {stats['new']}\n"
        f"• В работе: {stats['in_progress']}\n"
        f"• Завершено: {stats['completed']}\n"
        f"• Срочных: {stats['urgent']}\n\n"
        "Выберите раздел для управления:"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(admin_main_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_admin_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает статистику для администратора"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    update.message.reply_text(
        "📊 *Статистика системы*\n\n"
        "Выберите период для просмотра статистики:",
        reply_markup=ReplyKeyboardMarkup(admin_stats_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_statistics_period(update: Update, context: CallbackContext, period: str) -> None:
    """Показывает статистику за указанный период"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    stats = db.get_statistics(period)
    
    period_names = {
        'today': 'сегодня',
        'week': 'за неделю',
        'month': 'за месяц',
        'all': 'за все время'
    }
    
    stats_text = (
        f"📊 *Статистика {period_names[period]}*\n\n"
        f"👥 *Пользователи:* {stats['total_users']}\n"
        f"📋 *Всего заявок:* {stats['total_requests']}\n"
        f"🆕 *Новые:* {stats['new']}\n"
        f"🔄 *В работе:* {stats['in_progress']}\n"
        f"✅ *Завершено:* {stats['completed']}\n"
        f"🚨 *Срочных:* {stats['urgent']}\n\n"
        f"📈 *Эффективность:* {round(stats['completed'] / max(stats['total_requests'], 1) * 100, 1)}%"
    )
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(admin_stats_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_admin_requests(update: Update, context: CallbackContext) -> None:
    """Показывает управление заявками для администратора"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    update.message.reply_text(
        "📋 *Управление заявками*\n\n"
        "Выберите тип заявок для просмотра:",
        reply_markup=ReplyKeyboardMarkup(admin_requests_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_requests_by_filter(update: Update, context: CallbackContext, filter_type: str) -> None:
    """Показывает заявки по фильтру"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    requests = db.get_active_requests(filter_type)
    
    filter_names = {
        'new': '🆕 Новые заявки',
        'in_progress': '🔄 Заявки в работе', 
        'urgent': '🚨 Срочные заявки',
        'all': '📋 Все активные заявки'
    }
    
    if not requests:
        update.message.reply_text(
            f"📭 {filter_names[filter_type]} отсутствуют.",
            reply_markup=ReplyKeyboardMarkup(admin_requests_keyboard, resize_keyboard=True)
        )
        return
    
    update.message.reply_text(
        f"{filter_names[filter_type]} ({len(requests)}):",
        reply_markup=ReplyKeyboardMarkup(admin_requests_keyboard, resize_keyboard=True)
    )
    
    for req in requests[:10]:  # Показываем первые 10 заявок
        status_icons = {'new': '🆕', 'in_progress': '🔄', 'completed': '✅'}
        
        request_text = (
            f"{status_icons.get(req['status'], '📋')} *Заявка #{req['id']}*\n"
            f"👤 *Клиент:* {req['name']} (@{req['username'] or 'N/A'})\n"
            f"📞 *Телефон:* `{req['phone']}`\n"
            f"🔧 *Тип:* {req['system_type']}\n"
            f"📍 *Участок:* {req['plot']}\n"
            f"⏰ *Срочность:* {req['urgency']}\n"
            f"🕒 *Создана:* {req['created_at'][:16]}\n"
            f"📝 *Описание:* {req['problem'][:100]}..."
        )
        
        if req.get('assigned_admin'):
            request_text += f"\n👨‍💼 *Исполнитель:* {req['assigned_admin']}"
        
        # Для новых заявок показываем кнопку "Взять в работу", для остальных - "Подробнее"
        if req['status'] == 'new':
            keyboard = [[
                InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{req['id']}")
            ]]
        else:
            keyboard = [[
                InlineKeyboardButton("📋 Подробнее", callback_data=f"view_{req['id']}")
            ]]
        
        if req.get('photo'):
            update.message.reply_photo(
                photo=req['photo'],
                caption=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

def handle_admin_callback(update: Update, context: CallbackContext) -> None:
    """Обработчик callback от админ-кнопок"""
    query = update.callback_query
    query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    if data.startswith('take_'):
        request_id = int(data.split('_')[1])
        admin_name = query.from_user.first_name
        
        # Обновляем статус заявки и назначаем администратора
        db.update_request_status(
            request_id, 
            "in_progress", 
            f"Заявка взята в работу администратором {admin_name}",
            admin_name
        )
        
        # Получаем информацию о заявке для уведомления пользователя
        request = db.get_request(request_id)
        if request and request.get('user_id'):
            try:
                context.bot.send_message(
                    chat_id=request['user_id'],
                    text=f"🔄 *Ваша заявка #{request_id} взята в работу!*\n\n"
                         f"👨‍💼 *Исполнитель:* {admin_name}\n"
                         f"📞 С вами свяжутся в ближайшее время.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {request['user_id']}: {e}")
        
        # Обновляем сообщение с заявкой
        request_text = (
            f"✅ *Заявка #{request_id} взята вами в работу!*\n\n"
            f"👤 *Клиент:* {request['name']}\n"
            f"📞 *Телефон:* `{request['phone']}`\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"🔧 *Тип:* {request['system_type']}\n"
            f"⏰ *Срочность:* {request['urgency']}\n"
            f"📝 *Описание:* {request['problem'][:100]}...\n\n"
            f"🔄 *Статус:* В работе\n"
            f"👨‍💼 *Исполнитель:* {admin_name}"
        )
        
        query.edit_message_caption(
            caption=request_text if query.message.caption else request_text,
            parse_mode=ParseMode.MARKDOWN
        )
        
    elif data.startswith('view_'):
        request_id = int(data.split('_')[1])
        request = db.get_request(request_id)
        
        if request:
            request_text = (
                f"📋 *Заявка #{request['id']}*\n\n"
                f"👤 *Клиент:* {request['name']}\n"
                f"📞 *Телефон:* `{request['phone']}`\n"
                f"📍 *Участок:* {request['plot']}\n"
                f"🔧 *Система:* {request['system_type']}\n"
                f"⏰ *Срочность:* {request['urgency']}\n"
                f"📝 *Описание:* {request['problem']}\n"
                f"📸 *Фото:* {'✅ Есть' if request['photo'] else '❌ Нет'}\n"
                f"🔄 *Статус:* {request['status']}\n"
            )
            
            if request.get('assigned_admin'):
                request_text += f"👨‍💼 *Исполнитель:* {request['assigned_admin']}\n"
            
            if request.get('admin_comment'):
                request_text += f"💬 *Комментарий:* {request['admin_comment']}\n"
            
            request_text += f"🕒 *Создана:* {request['created_at'][:16]}\n"
            
            keyboard = [[
                InlineKeyboardButton("✅ Завершить", callback_data=f"complete_{request_id}"),
                InlineKeyboardButton("📞 Связаться", callback_data=f"contact_{request_id}")
            ]]
            
            # Редактируем существующее сообщение
            if query.message.caption:
                query.edit_message_caption(
                    caption=request_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                query.edit_message_text(
                    request_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================

def handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в главном меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Создаем клавиатуру в зависимости от прав
    keyboard = base_main_menu_keyboard.copy()
    if user_id in ADMIN_CHAT_IDS:
        keyboard.append(['👑 Админ-панель'])
    
    if text == '📝 Создать заявку':
        return start_request_creation(update, context)
    elif text == '📋 Мои заявки':
        return show_my_requests(update, context)
    elif text == '👑 Админ-панель' and user_id in ADMIN_CHAT_IDS:
        return show_admin_panel(update, context)
    else:
        update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )

def handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if text == '📊 Статистика':
        return show_admin_statistics(update, context)
    elif text == '📋 Активные заявки':
        return show_admin_requests(update, context)
    elif text == '🔙 Главное меню':
        return show_main_menu(update, context)
    elif text == '🔙 Админ-панель':
        return show_admin_panel(update, context)

def handle_stats_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в меню статистики"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if text == '📈 За сегодня':
        return show_statistics_period(update, context, 'today')
    elif text == '📅 За неделю':
        return show_statistics_period(update, context, 'week')
    elif text == '📆 За месяц':
        return show_statistics_period(update, context, 'month')
    elif text == '🗓️ За все время':
        return show_statistics_period(update, context, 'all')
    elif text == '🔙 Админ-панель':
        return show_admin_panel(update, context)

def handle_requests_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в меню заявок"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if text == '🆕 Новые заявки':
        return show_requests_by_filter(update, context, 'new')
    elif text == '🔄 В работе':
        return show_requests_by_filter(update, context, 'in_progress')
    elif text == '📤 Все активные':
        return show_requests_by_filter(update, context, 'all')
    elif text == '🔙 Админ-панель':
        return show_admin_panel(update, context)

# ==================== СОЗДАНИЕ ЗАЯВКИ ====================

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

def show_request_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку заявки перед отправкой"""
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    
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
        f"🕒 *Время:* {context.user_data['timestamp']}"
    )
    
    context.user_data['summary'] = summary
    
    if context.user_data.get('photo'):
        update.message.reply_photo(
            photo=context.user_data['photo'],
            caption=f"{summary}\n\n*Подтвердите отправку заявки:*",
            reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        update.message.reply_text(
            f"{summary}\n\n*Подтвердите отправку заявки:*",
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
            
            # Создаем клавиатуру в зависимости от прав
            keyboard = base_main_menu_keyboard.copy()
            if user.id in ADMIN_CHAT_IDS:
                keyboard.append(['👑 Админ-панель'])
            
            update.message.reply_text(
                confirmation_text,
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            
            # Создаем клавиатуру в зависимости от прав
            keyboard = base_main_menu_keyboard.copy()
            if user.id in ADMIN_CHAT_IDS:
                keyboard.append(['👑 Админ-панель'])
            
            update.message.reply_text(
                "❌ *Произошла ошибка при создании заявки.*\n\nПожалуйста, попробуйте позже.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        
        context.user_data.clear()
        
    elif update.message.text == '✏️ Редактировать заявку':
        update.message.reply_text(
            "✏️ *Редактирование заявки*\n\nУкажите ваше имя:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME

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
        f"🕒 *Время:* {user_data.get('timestamp')}"
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
    # Создаем клавиатуру в зависимости от прав
    user_id = update.message.from_user.id
    keyboard = base_main_menu_keyboard.copy()
    if user_id in ADMIN_CHAT_IDS:
        keyboard.append(['👑 Админ-панель'])
    
    update.message.reply_text(
        "❌ Создание заявки отменено.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    context.user_data.clear()
    return ConversationHandler.END

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
            },
            fallbacks=[
                CommandHandler('cancel', cancel_request),
                MessageHandler(Filters.regex('^(🔙 Назад в меню)$'), cancel_request)
            ],
            per_message=False
        )

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить отправку|✏️ Редактировать заявку)$'), confirm_request))
        
        # Обработчики меню
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📝 Создать заявку|📋 Мои заявки|👑 Админ-панель)$'), handle_main_menu))
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📊 Статистика|📋 Активные заявки|🔙 Главное меню|🔙 Админ-панель)$'), handle_admin_menu))
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📈 За сегодня|📅 За неделю|📆 За месяц|🗓️ За все время)$'), handle_stats_menu))
        dispatcher.add_handler(MessageHandler(Filters.regex('^(🆕 Новые заявки|🔄 В работе|📤 Все активные)$'), handle_requests_menu))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(handle_admin_callback, pattern='^(take_|view_|complete_|contact_)'))

        # Запускаем с главного меню
        logger.info("🤖 Бот запущен с визуальным меню!")
        logger.info(f"👑 Администраторы: {ADMIN_CHAT_IDS}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
