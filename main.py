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

# Главное меню администратора (ОБНОВЛЕНО - добавлены счетчики)
admin_main_menu_keyboard = [
    ['🆕 Новые заявки (0)', '🔄 В работе (0)'],
    ['✅ Выполненные заявки']
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

# ==================== РЕДАКТИРОВАНИЕ ЗАЯВКИ ====================

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
        
        # Обновляем сводку
        context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
        update_summary(context)
        
        # Показываем обновленную сводку для подтверждения
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

def cancel_editing(update: Update, context: CallbackContext) -> int:
    """Отменяет редактирование и возвращает к подтверждению"""
    context.user_data.pop('editing_mode', None)
    context.user_data.pop('editing_field', None)
    
    return show_request_summary(update, context)

# ==================== ВИЗУАЛЬНОЕ МЕНЮ ====================

def get_admin_panel_with_counters():
    """Возвращает админ-панель с актуальными счетчиками"""
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    
    return [
        [f'🆕 Новые заявки ({len(new_requests)})', f'🔄 В работе ({len(in_progress_requests)})'],
        ['✅ Выполненные заявки']
    ]

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

# ... (остальной код остается без изменений, включая функции show_my_requests, show_admin_panel, show_requests_by_filter, handle_admin_callback, handle_main_menu, handle_admin_menu, main)

# Остальной код (функции show_my_requests, show_admin_panel, show_requests_by_filter, handle_admin_callback, handle_main_menu, handle_admin_menu, main) остается без изменений

if __name__ == '__main__':
    main()
