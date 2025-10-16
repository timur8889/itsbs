import logging
import sqlite3
import os
import json
import re
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

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Конфигурация приложения"""
    # Безопасное получение токена из переменных окружения
    BOT_TOKEN = os.getenv('BOT_TOKEN', '7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw')
    ADMIN_CHAT_IDS = [5024165375]
    DB_PATH = "requests.db"
    LOG_LEVEL = logging.INFO

# ==================== ЛОГИРОВАНИЕ ====================

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=Config.LOG_LEVEL,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Определяем этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD, OTHER_PLOT, SELECT_REQUEST = range(11)

# ==================== ВАЛИДАЦИЯ ====================

class Validators:
    """Класс для валидации данных"""
    
    @staticmethod
    def validate_phone(phone: str) -> bool:
        """Проверяет валидность номера телефона"""
        pattern = r'^(\+7|7|8)?[\s\-]?\(?[489][0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}$'
        return bool(re.match(pattern, phone.strip()))
    
    @staticmethod
    def validate_name(name: str) -> bool:
        """Проверяет валидность имени"""
        return len(name.strip()) >= 2 and len(name.strip()) <= 50 and name.replace(' ', '').isalpha()
    
    @staticmethod
    def validate_problem(problem: str) -> bool:
        """Проверяет валидность описания проблемы"""
        return 10 <= len(problem.strip()) <= 1000

# ==================== КЛАВИАТУРЫ ====================

user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['✏️ Редактировать заявку']
]

admin_main_menu_keyboard = [
    ['👑 Админ-панель']
]

create_request_keyboard = [
    ['📹 Видеонаблюдение', '🔐 СКУД'],
    ['🌐 Компьютерная сеть', '🚨 Пожарная сигнализация'],
    ['🔙 Назад в меню']
]

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

edit_choice_keyboard = [
    ['📛 Редактировать имя', '📞 Редактировать телефон'],
    ['📍 Редактировать участок', '🔧 Редактировать систему'],
    ['📝 Редактировать описание', '⏰ Редактировать срочность'],
    ['📷 Редактировать фото', '✅ Завершить редактирование']
]

edit_field_keyboard = [['🔙 Назад к редактированию']]

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
        try:
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
                logger.info("База данных успешно инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}")
            raise

    def save_request(self, user_data: Dict) -> int:
        """Сохраняет заявку в базу данных"""
        try:
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
                
                today = datetime.now().strftime('%Y-%m-%d')
                cursor.execute('''
                    INSERT OR REPLACE INTO statistics (date, requests_count)
                    VALUES (?, COALESCE((SELECT requests_count FROM statistics WHERE date = ?), 0) + 1)
                ''', (today, today))
                
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
                logger.info(f"Заявка #{request_id} успешно сохранена для пользователя {user_data.get('user_id')}")
                return request_id
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            raise

    def get_user_requests(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Получает заявки пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE user_id = ? 
                    ORDER BY created_at DESC 
                    LIMIT ?
                ''', (user_id, limit))
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
                logger.debug(f"Получено {len(requests)} заявок для пользователя {user_id}")
                return requests
        except Exception as e:
            logger.error(f"Ошибка при получении заявок пользователя {user_id}: {e}")
            return []

    def get_requests_by_filter(self, filter_type: str = 'all', limit: int = 50) -> List[Dict]:
        """Получает заявки по фильтру"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                if filter_type == 'new':
                    status_filter = "status = 'new'"
                elif filter_type == 'in_progress':
                    status_filter = "status = 'in_progress'"
                elif filter_type == 'completed':
                    status_filter = "status = 'completed'"
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
                    LIMIT ?
                ''', (limit,))
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
                logger.debug(f"Получено {len(requests)} заявок с фильтром '{filter_type}'")
                return requests
        except Exception as e:
            logger.error(f"Ошибка при получении заявок с фильтром '{filter_type}': {e}")
            return []

    def get_request(self, request_id: int) -> Dict:
        """Получает заявку по ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
                row = cursor.fetchone()
                if row:
                    columns = [column[0] for column in cursor.description]
                    return dict(zip(columns, row))
                return {}
        except Exception as e:
            logger.error(f"Ошибка при получении заявки #{request_id}: {e}")
            return {}

    def update_request(self, request_id: int, update_data: Dict) -> bool:
        """Обновляет данные заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                set_parts = []
                parameters = []
                
                for field, value in update_data.items():
                    if field in ['name', 'phone', 'plot', 'system_type', 'problem', 'photo', 'urgency']:
                        set_parts.append(f"{field} = ?")
                        parameters.append(value)
                
                set_parts.append("updated_at = ?")
                parameters.append(datetime.now().isoformat())
                parameters.append(request_id)
                
                if set_parts:
                    sql = f"UPDATE requests SET {', '.join(set_parts)} WHERE id = ?"
                    cursor.execute(sql, parameters)
                    conn.commit()
                    logger.info(f"Заявка #{request_id} успешно обновлена")
                    return True
                return False
        except Exception as e:
            logger.error(f"Ошибка при обновлении заявки #{request_id}: {e}")
            return False

    def update_request_status(self, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
        """Обновляет статус заявки"""
        try:
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
                logger.info(f"Статус заявки #{request_id} изменен на '{status}'")
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса заявки #{request_id}: {e}")
            raise

# Инициализация базы данных
db = Database(Config.DB_PATH)

# ==================== СОЗДАНИЕ ЗАЯВКИ ====================

def start_request_creation(update: Update, context: CallbackContext) -> int:
    """Начинает процесс создания заявки"""
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} начал создание заявки")
    
    context.user_data.clear()
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
    name_text = update.message.text.strip()
    
    if not Validators.validate_name(name_text):
        update.message.reply_text(
            "❌ *Неверный формат имени!*\n\n"
            "Имя должно содержать только буквы и быть от 2 до 50 символов.\n"
            "Пожалуйста, введите ваше имя еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME
    
    context.user_data['name'] = name_text
    update.message.reply_text(
        "📞 *Укажите ваш контактный телефон:*\n\nПример: +7 999 123-45-67",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    phone_text = update.message.text.strip()
    
    if not Validators.validate_phone(phone_text):
        update.message.reply_text(
            "❌ *Неверный формат телефона!*\n\n"
            "Пожалуйста, введите номер в одном из форматов:\n"
            "+7 999 123-45-67\n8 999 123-45-67\n79991234567\n\n"
            "Попробуйте еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHONE
    
    context.user_data['phone'] = phone_text
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
    
    if update.message.text == '📦 Другой участок':
        update.message.reply_text(
            "✏️ *Введите название вашего участка:*\n\nПример: Сборочный цех, Склад №2 и т.д.",
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return OTHER_PLOT
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "🔧 *Выберите тип системы:*",
        reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

def other_plot(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод пользовательского участка"""
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📍 *Выберите тип участка:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
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
    
    # Проверка валидности выбора системы
    valid_systems = ['📹 Видеонаблюдение', '🔐 СКУД', '🌐 Компьютерная сеть', '🚨 Пожарная сигнализация']
    if update.message.text not in valid_systems:
        update.message.reply_text(
            "❌ Пожалуйста, выберите тип системы из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True)
        )
        return SYSTEM_TYPE
    
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        "📝 *Опишите проблему или необходимые работы:*\n\n"
        "Пример: Не работает видеонаблюдение на фрезерном участке\n\n"
        "*Минимум 10 символов, максимум 1000*",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    problem_text = update.message.text.strip()
    
    if not Validators.validate_problem(problem_text):
        update.message.reply_text(
            "❌ *Описание проблемы слишком короткое или длинное!*\n\n"
            "Пожалуйста, опишите проблему подробнее (от 10 до 1000 символов):",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PROBLEM
    
    context.user_data['problem'] = problem_text
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
    
    # Проверка валидности выбора срочности
    valid_urgency = ['🔴 Срочно (2 часа)', '🟡 Средняя (сегодня)', '🟢 Не срочно (3 дня)']
    if update.message.text not in valid_urgency:
        update.message.reply_text(
            "❌ Пожалуйста, выберите срочность из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    
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
    update_summary(context)
    
    if context.user_data.get('editing_mode'):
        return edit_request_choice(update, context)
    else:
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
        f"🕒 *Время:* {context.user_data['timestamp']}"
    )
    
    context.user_data['summary'] = summary

def confirm_request(update: Update, context: CallbackContext) -> int:  # ✅ ИСПРАВЛЕН ТИП ВОЗВРАТА
    """Подтверждает и отправляет заявку"""
    if update.message.text == '✅ Подтвердить отправку':
        user = update.message.from_user
        
        try:
            request_id = db.save_request(context.user_data)
            send_admin_notification(context, context.user_data, request_id)
            
            confirmation_text = (
                f"✅ *Заявка #{request_id} успешно создана!*\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n\n"
                f"_Спасибо за обращение в службу слаботочных систем завода Контакт!_ 🛠️"
            )
            
            if user.id in Config.ADMIN_CHAT_IDS:
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True),
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
            error_message = (
                "❌ *Произошла ошибка при создании заявки.*\n\n"
                "Пожалуйста, попробуйте позже или обратитесь в техническую поддержку."
            )
            
            if user.id in Config.ADMIN_CHAT_IDS:
                update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
        
        context.user_data.clear()
        return ConversationHandler.END
        
    elif update.message.text == '✏️ Редактировать заявку':
        context.user_data['editing_mode'] = True
        return edit_request_choice(update, context)
    
    # На случай, если пришло неожиданное сообщение
    return ConversationHandler.END

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
    
    for admin_id in Config.ADMIN_CHAT_IDS:
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
            logger.info(f"Уведомление отправлено администратору {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")

def cancel_request(update: Update, context: CallbackContext) -> int:
    """Отменяет создание заявки"""
    user_id = update.message.from_user.id
    logger.info(f"Пользователь {user_id} отменил создание заявки")
    
    if user_id in Config.ADMIN_CHAT_IDS:
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True)
        )
    else:
        update.message.reply_text(
            "❌ Создание заявки отменено.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
    
    context.user_data.clear()
    return ConversationHandler.END

# ==================== РЕДАКТИРОВАНИЕ ЗАЯВОК ====================

def start_edit_request(update: Update, context: CallbackContext) -> int:
    """Начинает процесс редактирования заявки"""
    user_id = update.message.from_user.id
    logger.info(f"Пользователь {user_id} начал редактирование заявки")
    
    requests = db.get_user_requests(user_id, 20)
    
    if not requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок для редактирования.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    active_requests = [req for req in requests if req['status'] != 'completed']
    
    if not active_requests:
        update.message.reply_text(
            "✅ У вас нет активных заявок для редактирования. Можно редактировать только заявки со статусом 'Новая' или 'В работе'.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    context.user_data['editable_requests'] = active_requests
    
    keyboard = []
    for req in active_requests:
        status_icon = '🆕' if req['status'] == 'new' else '🔄'
        button_text = f"{status_icon} Заявка #{req['id']} - {req['system_type']}"
        keyboard.append([button_text])
    
    keyboard.append(['🔙 Назад в меню'])
    
    update.message.reply_text(
        "✏️ *Выберите заявку для редактирования:*\n\n"
        "Доступны только активные заявки (не выполненные):",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return SELECT_REQUEST

# ... (остальные функции редактирования остаются без изменений, но с добавлением логирования)

# ==================== АДМИН-ПАНЕЛЬ ====================

def show_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает админ-панель"""
    user_id = update.message.from_user.id
    
    if user_id not in Config.ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu(update, context)
    
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

# ... (остальные функции админ-панели остаются без изменений)

# ==================== ОСНОВНЫЕ ФУНКЦИИ ====================

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Показывает главное меню"""
    user = update.message.from_user
    user_id = user.id
    
    if user_id in Config.ADMIN_CHAT_IDS:
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

# ... (остальные основные функции остаются без изменений)

# ==================== ОСНОВНЫЕ ФУНКЦИИ БОТА ====================

def main() -> None:
    """Запускаем бота"""
    if Config.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        updater = Updater(Config.BOT_TOKEN)
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
                OTHER_PLOT: [MessageHandler(Filters.text & ~Filters.command, other_plot)],
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

        # Обработчик редактирования заявки
        edit_conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(✏️ Редактировать заявку)$'), start_edit_request),
            ],
            states={
                SELECT_REQUEST: [MessageHandler(Filters.text & ~Filters.command, select_request_for_edit)],
                EDIT_CHOICE: [MessageHandler(Filters.text & ~Filters.command, handle_edit_choice)],
                EDIT_FIELD: [
                    MessageHandler(Filters.text & ~Filters.command, handle_edit_field),
                    MessageHandler(Filters.photo, handle_edit_field)
                ],
                OTHER_PLOT: [MessageHandler(Filters.text & ~Filters.command, other_plot_edit)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_edit),
                MessageHandler(Filters.regex('^(🔙 Назад в меню)$'), cancel_edit),
            ],
            allow_reentry=True
        )

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(edit_conv_handler)
        
        # Обработчики для кнопок подтверждения и редактирования
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить отправку)$'), confirm_request))
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✏️ Редактировать заявку)$'), confirm_request))
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📋 Мои заявки|👑 Админ-панель)$'), handle_main_menu))
        
        # Обработчики админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки|🔄 В работе|✅ Выполненные заявки)$'), 
            handle_admin_menu
        ))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(handle_admin_callback, pattern='^(take_|complete_|message_)'))

        # Запускаем бота
        logger.info("🤖 Бот запущен с функцией редактирования заявок!")
        logger.info(f"👑 Администраторы: {Config.ADMIN_CHAT_IDS}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
