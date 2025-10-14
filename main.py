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

# ==================== КЛАВИАТУРЫ ====================

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

# Админ-панель (НОВЫЕ ЗАЯВКИ, В РАБОТЕ И ВЫПОЛНЕННЫЕ)
admin_panel_keyboard = [
    ['🆕 Новые заявки', '🔄 В работе'],
    ['✅ Выполненные заявки']
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

# ==================== ВИЗУАЛЬНОЕ МЕНЮ ====================

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Показывает главное меню"""
    user = update.message.from_user
    user_id = user.id
    
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

def show_my_requests(update: Update, context: CallbackContext) -> None:
    """Показывает заявки пользователя с разделением по статусам"""
    user_id = update.message.from_user.id
    
    # Определяем клавиатуру в зависимости от прав
    if user_id in ADMIN_CHAT_IDS:
        # Администратору показываем админ-панель
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
    
    # Разделяем заявки по статусам
    active_requests = [req for req in requests if req['status'] != 'completed']
    completed_requests = [req for req in requests if req['status'] == 'completed']
    
    if not active_requests and not completed_requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок.\n\n"
            "Хотите создать первую заявку?",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return
    
    # Показываем активные заявки
    if active_requests:
        update.message.reply_text(
            f"📋 *Ваши активные заявки ({len(active_requests)}):*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        for req in active_requests:
            status_icons = {
                'new': '🆕 НОВАЯ',
                'in_progress': '🔄 В РАБОТЕ', 
                'completed': '✅ ВЫПОЛНЕНА'
            }
            
            status_text = status_icons.get(req['status'], req['status'])
            
            request_text = (
                f"{status_icons.get(req['status'], '📋')} *Заявка #{req['id']}*\n"
                f"🔧 *Тип:* {req['system_type']}\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"🔄 *Статус:* {status_text}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}\n"
            )
            
            if req.get('assigned_admin') and req['status'] == 'in_progress':
                request_text += f"👨‍💼 *Исполнитель:* {req['assigned_admin']}\n"
            
            if req.get('admin_comment'):
                request_text += f"💬 *Комментарий:* {req['admin_comment']}\n"
            
            update.message.reply_text(request_text, parse_mode=ParseMode.MARKDOWN)
    
    # Показываем выполненные заявки отдельно
    if completed_requests:
        update.message.reply_text(
            f"✅ *История выполненных заявок ({len(completed_requests)}):*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        for req in completed_requests:
            request_text = (
                f"✅ *Заявка #{req['id']} - ВЫПОЛНЕНА*\n"
                f"🔧 *Тип системы:* {req['system_type']}\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem'][:100]}{'...' if len(req['problem']) > 100 else ''}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}\n"
                f"✅ *Завершена:* {req['updated_at'][:16] if req.get('updated_at') else req['created_at'][:16]}\n"
            )
            
            if req.get('assigned_admin'):
                request_text += f"👨‍💼 *Исполнитель:* {req['assigned_admin']}\n"
            
            if req.get('admin_comment'):
                request_text += f"💬 *Комментарий:* {req['admin_comment']}\n"
            
            update.message.reply_text(request_text, parse_mode=ParseMode.MARKDOWN)
    
    # Итоговое сообщение
    total_text = f"📊 *Итого:* {len(active_requests)} активных, {len(completed_requests)} выполненных заявок"
    update.message.reply_text(
        total_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== АДМИН-ПАНЕЛЬ ====================

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
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

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
    filter_name = f"{filter_names[filter_type]} ({len(requests)})"
    
    if not requests:
        update.message.reply_text(
            f"📭 {filter_name} отсутствуют.",
            reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True)
        )
        return
    
    update.message.reply_text(
        filter_name,
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True)
    )
    
    for req in requests:
        # Определяем текст в зависимости от статуса заявки
        if req['status'] == 'completed':
            request_text = (
                f"✅ *Заявка #{req['id']} - ВЫПОЛНЕНА*\n\n"
                f"👤 *Клиент:* {req['name']}\n"
                f"📞 *Телефон:* `{req['phone']}`\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"🔧 *Тип системы:* {req['system_type']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem']}\n"
                f"📸 *Фото:* {'✅ Есть' if req['photo'] else '❌ Нет'}\n"
                f"👨‍💼 *Исполнитель:* {req.get('assigned_admin', 'Не назначен')}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}\n"
                f"✅ *Завершена:* {req['updated_at'][:16] if req.get('updated_at') else req['created_at'][:16]}"
            )
        elif req['status'] == 'in_progress':
            request_text = (
                f"🔄 *Заявка #{req['id']} - В РАБОТЕ*\n\n"
                f"👤 *Клиент:* {req['name']}\n"
                f"📞 *Телефон:* `{req['phone']}`\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"🔧 *Тип системы:* {req['system_type']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem']}\n"
                f"📸 *Фото:* {'✅ Есть' if req['photo'] else '❌ Нет'}\n"
                f"👨‍💼 *Исполнитель:* {req.get('assigned_admin', 'Не назначен')}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}\n"
                f"🔄 *Обновлена:* {req['updated_at'][:16] if req.get('updated_at') else req['created_at'][:16]}"
            )
        else:
            request_text = (
                f"🆕 *Заявка #{req['id']} - НОВАЯ*\n\n"
                f"👤 *Клиент:* {req['name']}\n"
                f"📞 *Телефон:* `{req['phone']}`\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"🔧 *Тип системы:* {req['system_type']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem']}\n"
                f"📸 *Фото:* {'✅ Есть' if req['photo'] else '❌ Нет'}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}"
            )
        
        if req.get('admin_comment'):
            request_text += f"\n💬 *Комментарий администратора:* {req['admin_comment']}"
        
        # Определяем кнопки в зависимости от статуса заявки
        if req['status'] == 'completed':
            # Для выполненных заявок - только кнопки связи
            keyboard = [[
                InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{req['id']}"),
                InlineKeyboardButton("💬 Написать", callback_data=f"message_{req['id']}")
            ]]
        elif req['status'] == 'in_progress':
            # Для заявок в работе
            if req.get('assigned_admin') == update.message.from_user.first_name:
                # Если текущий администратор - исполнитель
                keyboard = [[
                    InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{req['id']}"),
                    InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{req['id']}")
                ],
                [
                    InlineKeyboardButton("💬 Написать", callback_data=f"message_{req['id']}")
                ]]
            else:
                # Если заявка в работе у другого администратора
                keyboard = [[
                    InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{req['id']}"),
                    InlineKeyboardButton("💬 Написать", callback_data=f"message_{req['id']}")
                ]]
        else:
            # Для новых заявок
            keyboard = [[
                InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{req['id']}"),
                InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{req['id']}")
            ],
            [
                InlineKeyboardButton("💬 Написать", callback_data=f"message_{req['id']}")
            ]]
        
        # Отправляем заявку с фото или без
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
            f"📝 *Описание:* {request['problem']}\n\n"
            f"🔄 *Статус:* В работе\n"
            f"👨‍💼 *Исполнитель:* {admin_name}"
        )
        
        # Обновляем inline-клавиатуру - добавляем кнопку "Выполнено"
        keyboard = [[
            InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{request_id}"),
            InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{request_id}")
        ],
        [
            InlineKeyboardButton("💬 Написать", callback_data=f"message_{request_id}")
        ]]
        
        if query.message.caption:
            query.edit_message_caption(
                caption=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            query.edit_message_text(
                text=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        
    elif data.startswith('complete_'):
        request_id = int(data.split('_')[1])
        admin_name = query.from_user.first_name
        
        # Обновляем статус заявки на "выполнено"
        db.update_request_status(
            request_id, 
            "completed", 
            f"Заявка выполнена администратором {admin_name}",
            admin_name
        )
        
        # Получаем информацию о заявке для уведомления пользователя
        request = db.get_request(request_id)
        if request and request.get('user_id'):
            try:
                context.bot.send_message(
                    chat_id=request['user_id'],
                    text=f"✅ *Ваша заявка #{request_id} выполнена!*\n\n"
                         f"👨‍💼 *Исполнитель:* {admin_name}\n"
                         f"💬 *Комментарий:* Заявка выполнена\n\n"
                         f"_Спасибо, что воспользовались нашими услугами!_ 🛠️",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {request['user_id']}: {e}")
        
        # Обновляем сообщение с заявкой
        request_text = (
            f"✅ *Заявка #{request_id} ВЫПОЛНЕНА!*\n\n"
            f"👤 *Клиент:* {request['name']}\n"
            f"📞 *Телефон:* `{request['phone']}`\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"🔧 *Тип системы:* {request['system_type']}\n"
            f"⏰ *Срочность:* {request['urgency']}\n"
            f"📝 *Описание:* {request['problem']}\n"
            f"📸 *Фото:* {'✅ Есть' if request['photo'] else '❌ Нет'}\n\n"
            f"✅ *Статус:* Выполнено\n"
            f"👨‍💼 *Исполнитель:* {admin_name}\n"
            f"💬 *Комментарий:* Заявка выполнена\n"
            f"🕒 *Завершена:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        # Обновляем inline-клавиатуру для выполненных заявок
        keyboard = [[
            InlineKeyboardButton("📞 Позвонить", callback_data=f"call_{request_id}"),
            InlineKeyboardButton("💬 Написать", callback_data=f"message_{request_id}")
        ]]
        
        if query.message.caption:
            query.edit_message_caption(
                caption=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            query.edit_message_text(
                text=request_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        
        # Отправляем подтверждение администратору
        query.answer("✅ Заявка выполнена!")
    
    elif data.startswith('call_'):
        request_id = int(data.split('_')[1])
        request = db.get_request(request_id)
        
        if request:
            # Очищаем номер телефона от всех символов кроме цифр
            phone_number = request['phone']
            # Убираем все нецифровые символы, кроме плюса в начале
            clean_number = ''.join(c for c in phone_number if c.isdigit() or c == '+')
            
            # Если номер начинается с +7, оставляем как есть, иначе добавляем +7
            if clean_number.startswith('+'):
                call_number = clean_number
            elif clean_number.startswith('7') and len(clean_number) == 11:
                call_number = '+' + clean_number
            elif clean_number.startswith('8') and len(clean_number) == 11:
                call_number = '+7' + clean_number[1:]
            else:
                call_number = '+7' + clean_number
            
            # Создаем кнопку для звонка с tel: ссылкой
            call_button = InlineKeyboardButton(
                "📞 Позвонить сейчас", 
                url=f"tel:{call_number}"
            )
            
            # Создаем кнопку для копирования номера
            copy_button = InlineKeyboardButton(
                "📋 Скопировать номер", 
                callback_data=f"copy_{request_id}"
            )
            
            contact_text = (
                f"📞 *Контактная информация по заявке #{request_id}*\n\n"
                f"👤 *Клиент:* {request['name']}\n"
                f"📞 *Телефон:* `{request['phone']}`\n"
                f"📍 *Участок:* {request['plot']}\n"
                f"🔧 *Тип системы:* {request['system_type']}\n"
                f"⏰ *Срочность:* {request['urgency']}\n\n"
                f"_Нажмите кнопку '📞 Позвонить сейчас' для автоматического набора номера._\n"
                f"_На мобильных устройствах откроется приложение телефона._"
            )
            
            query.answer("📞 Открывается набор номера...")
            
            # Отправляем отдельное сообщение с кнопками для связи
            context.bot.send_message(
                chat_id=query.message.chat_id,
                text=contact_text,
                reply_markup=InlineKeyboardMarkup([
                    [call_button],
                    [copy_button]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )
    
    elif data.startswith('copy_'):
        request_id = int(data.split('_')[1])
        request = db.get_request(request_id)
        
        if request:
            # Просто показываем номер крупным текстом для удобного копирования
            contact_text = (
                f"📋 *Номер телефона для копирования*\n\n"
                f"👤 *Клиент:* {request['name']}\n"
                f"📞 *Телефон:* \n\n"
                f"`{request['phone']}`\n\n"
                f"_Нажмите на номер выше, чтобы скопировать его_"
            )
            
            query.answer("📋 Номер готов для копирования")
            
            # Отправляем сообщение с номером для копирования
            context.bot.send_message(
                chat_id=query.message.chat_id,
                text=contact_text,
                parse_mode=ParseMode.MARKDOWN
            )
    
    elif data.startswith('message_'):
        request_id = int(data.split('_')[1])
        request = db.get_request(request_id)
        
        if request:
            # Очищаем номер телефона для Telegram
            phone_number = request['phone'].replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
            
            # Убираем + для Telegram
            telegram_number = phone_number.replace('+', '')
            
            # Создаем кнопку для написания сообщения в Telegram
            message_button = InlineKeyboardButton(
                "💬 Написать в Telegram", 
                url=f"https://t.me/{telegram_number}"
            )
            
            contact_text = (
                f"💬 *Контактная информация по заявке #{request_id}*\n\n"
                f"👤 *Клиент:* {request['name']}\n"
                f"📞 *Телефон:* `{request['phone']}`\n"
                f"📍 *Участок:* {request['plot']}\n"
                f"🔧 *Тип системы:* {request['system_type']}\n"
                f"⏰ *Срочность:* {request['urgency']}\n\n"
                f"_Нажмите кнопку ниже для написания сообщения в Telegram_"
            )
            
            query.answer("💬 Открывается чат...")
            
            # Отправляем отдельное сообщение с кнопкой для сообщения
            context.bot.send_message(
                chat_id=query.message.chat_id,
                text=contact_text,
                reply_markup=InlineKeyboardMarkup([[message_button]]),
                parse_mode=ParseMode.MARKDOWN
            )

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
    
    if text == '🆕 Новые заявки':
        return show_requests_by_filter(update, context, 'new')
    elif text == '🔄 В работе':
        return show_requests_by_filter(update, context, 'in_progress')
    elif text == '✅ Выполненные заявки':
        return show_requests_by_filter(update, context, 'completed')

# Остальной код (создание заявки, редактирование и т.д.) остается без изменений
# ... [здесь должен быть остальной код создания заявки и редактирования] ...

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

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(edit_handler)
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить отправку)$'), confirm_request))
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📋 Мои заявки|👑 Админ-панель)$'), handle_main_menu))
        
        # Обработчики админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки|🔄 В работе|✅ Выполненные заявки)$'), 
            handle_admin_menu
        ))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(handle_admin_callback, pattern='^(take_|complete_|call_|message_|copy_)'))

        # Запускаем бота
        logger.info("🤖 Бот запущен с визуальным меню!")
        logger.info(f"👑 Администраторы: {ADMIN_CHAT_IDS}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
