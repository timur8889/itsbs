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

# ==================== УЛУЧШЕННЫЕ КЛАВИАТУРЫ ====================

# 🎯 Главное меню пользователя
user_main_menu_keyboard = [
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['✏️ Редактировать заявку', '📞 Контакты IT']
]

# 👑 Главное меню администратора
admin_main_menu_keyboard = [
    ['👑 Админ-панель'],
    ['🎯 Создать заявку', '📂 Мои заявки']
]

# 💻 Типы IT систем
create_request_keyboard = [
    ['💻 Компьютеры', '🖨️ Принтеры'],
    ['🌐 Интернет', '📞 Телефония'],
    ['🔐 Программы', '📊 1С и Базы'],
    ['🎥 Оборудование', '⚡ Другое'],
    ['🔙 Назад в меню']
]

# ✅ Клавиатура подтверждения
confirm_keyboard = [
    ['🚀 Отправить заявку', '✏️ Исправить'],
    ['🔙 Отменить']
]

# 📸 Клавиатура для фото
photo_keyboard = [
    ['📷 Добавить фото', '⏭️ Без фото'],
    ['🔙 Назад']
]

# ⏰ Клавиатура срочности
urgency_keyboard = [
    ['🔥 СРОЧНО (1-2 часа)'],
    ['⚠️ СЕГОДНЯ (до конца дня)'],
    ['💤 НЕ СРОЧНО (1-3 дня)'],
    ['🔙 Назад']
]

# 🏢 Типы участков
plot_type_keyboard = [
    ['🏢 Центральный офис', '🏭 Производство'],
    ['📦 Складской комплекс', '🛒 Торговый зал'],
    ['💻 Удаленные рабочие места', '📋 Другой участок'],
    ['🔙 Назад']
]

# ✏️ Клавиатура редактирования
edit_choice_keyboard = [
    ['👤 Имя', '📞 Телефон', '📍 Участок'],
    ['💻 Система', '📝 Описание', '⏰ Срочность'],
    ['📷 Фото', '✅ Готово'],
    ['🔙 Отменить']
]

# ◀️ Клавиатура назад
edit_field_keyboard = [['◀️ Назад к редактированию']]

# 👑 Панель администратора (убран "Главное меню")
admin_panel_keyboard = [
    ['🆕 Новые заявки', '🔄 В работе'],
    ['✅ Выполненные заявки', '📊 Статистика']
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
                    status_filter = "status = 'completed'"  # ✅ Исправлено
                else:
                    status_filter = "status IN ('new', 'in_progress')"
                
                cursor.execute(f'''
                    SELECT * FROM requests 
                    WHERE {status_filter}
                    ORDER BY 
                        CASE urgency 
                            WHEN '🔥 СРОЧНО (1-2 часа)' THEN 1
                            WHEN '⚠️ СЕГОДНЯ (до конца дня)' THEN 2
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
        "🎯 *Создание новой заявки в IT отдел*\n\n"
        "📝 *Шаг 1 из 7*\n"
        "👤 Для начала укажите ваше *имя и фамилию*:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def name(update: Update, context: CallbackContext) -> int:
    name_text = update.message.text.strip()
    
    if not Validators.validate_name(name_text):
        update.message.reply_text(
            "❌ *Неверный формат имени!*\n\n"
            "👤 Имя должно содержать только буквы и быть от 2 до 50 символов.\n"
            "Пожалуйста, введите ваше имя еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME
    
    context.user_data['name'] = name_text
    update.message.reply_text(
        "📝 *Шаг 2 из 7*\n"
        "📞 *Укажите ваш контактный телефон:*\n\n"
        "📋 Примеры:\n"
        "• +7 999 123-45-67\n"
        "• 8 999 123-45-67\n"
        "• 79991234567",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    phone_text = update.message.text.strip()
    
    if not Validators.validate_phone(phone_text):
        update.message.reply_text(
            "❌ *Неверный формат телефона!*\n\n"
            "📞 Пожалуйста, введите номер в одном из форматов:\n"
            "• +7 999 123-45-67\n"
            "• 8 999 123-45-67\n"
            "• 79991234567\n\n"
            "Попробуйте еще раз:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHONE
    
    context.user_data['phone'] = phone_text
    update.message.reply_text(
        "📝 *Шаг 3 из 7*\n"
        "📍 *Выберите ваш участок или отдел:*",
        reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLOT

def plot(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "👤 Укажите ваше имя и фамилию:",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME
    
    if update.message.text == '📋 Другой участок':
        update.message.reply_text(
            "📝 *Шаг 3 из 7*\n"
            "✏️ *Введите название вашего участка или отдела:*\n\n"
            "📋 Примеры:\n"
            "• Бухгалтерия\n"
            "• Отдел кадров\n"
            "• Производственный цех №1",
            reply_markup=ReplyKeyboardMarkup([['🔙 Назад']], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return OTHER_PLOT
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 4 из 7*\n"
        "💻 *Выберите тип IT-проблемы:*",
        reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

def other_plot(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод пользовательского участка"""
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📍 *Выберите ваш участок или отдел:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 4 из 7*\n"
        "💻 *Выберите тип IT-проблемы:*",
        reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад в меню':
        return show_main_menu(update, context)
    elif update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📍 *Выберите ваш участок или отдел:*",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True)
        )
        return PLOT
    
    # Проверка валидности выбора системы
    valid_systems = ['💻 Компьютеры', '🖨️ Принтеры', '🌐 Интернет', '📞 Телефония', 
                    '🔐 Программы', '📊 1С и Базы', '🎥 Оборудование', '⚡ Другое']
    if update.message.text not in valid_systems:
        update.message.reply_text(
            "❌ Пожалуйста, выберите тип проблемы из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True)
        )
        return SYSTEM_TYPE
    
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 5 из 7*\n"
        "📖 *Опишите проблему подробно:*\n\n"
        "💡 Примеры хороших описаний:\n"
        "• 'Не включается компьютер, при нажатии кнопки питания ничего не происходит'\n"
        "• 'Принтер HP LaserJet печатает пустые листы'\n"
        "• 'Не работает интернет на 3 этаже в бухгалтерии'\n\n"
        "⚠️ *Требования:* от 10 до 1000 символов",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    problem_text = update.message.text.strip()
    
    if not Validators.validate_problem(problem_text):
        update.message.reply_text(
            "❌ *Описание проблемы слишком короткое или длинное!*\n\n"
            "📝 Пожалуйста, опишите проблему подробнее (от 10 до 1000 символов):",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PROBLEM
    
    context.user_data['problem'] = problem_text
    update.message.reply_text(
        "📝 *Шаг 6 из 7*\n"
        "⏰ *Выберите срочность выполнения:*",
        reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "📖 *Опишите проблему подробно:*",
            reply_markup=ReplyKeyboardRemove()
        )
        return PROBLEM
    
    # Проверка валидности выбора срочности
    valid_urgency = ['🔥 СРОЧНО (1-2 часа)', '⚠️ СЕГОДНЯ (до конца дня)', '💤 НЕ СРОЧНО (1-3 дня)']
    if update.message.text not in valid_urgency:
        update.message.reply_text(
            "❌ Пожалуйста, выберите срочность из предложенных вариантов:",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    
    context.user_data['urgency'] = update.message.text
    update.message.reply_text(
        "📝 *Шаг 7 из 7*\n"
        "📸 *Хотите добавить фото к заявке?*\n\n"
        "🖼️ Фото помогает быстрее понять проблему.\n"
        "📎 Можно отправить скриншот ошибки или фото оборудования",
        reply_markup=ReplyKeyboardMarkup(photo_keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    if update.message.text == '🔙 Назад':
        update.message.reply_text(
            "⏰ *Выберите срочность выполнения:*",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True)
        )
        return URGENCY
    elif update.message.text == '📷 Добавить фото':
        update.message.reply_text(
            "📸 *Отправьте фото или скриншот:*\n\n"
            "📎 Можно отправить несколько фото по очереди",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHOTO
    elif update.message.text == '⏭️ Без фото':
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
        summary_text = (
            f"{context.user_data['summary']}\n\n"
            "🎯 *Проверьте данные заявки:*\n"
            "✅ Все верно - отправляем заявку\n"
            "✏️ Нужно что-то исправить\n"
            "🔙 Можно начать заново"
        )
        
        if context.user_data.get('photo'):
            update.message.reply_photo(
                photo=context.user_data['photo'],
                caption=summary_text,
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                summary_text,
                reply_markup=ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
        return ConversationHandler.END

def update_summary(context: CallbackContext) -> None:
    """Обновляет сводку заявки в user_data"""
    photo_status = "✅ Добавлено" if context.user_data.get('photo') else "❌ Отсутствует"
    
    summary = (
        f"📋 *Сводка заявки в IT отдел:*\n\n"
        f"👤 *Имя:* {context.user_data['name']}\n"
        f"📞 *Телефон:* `{context.user_data['phone']}`\n"
        f"📍 *Участок:* {context.user_data['plot']}\n"
        f"💻 *Тип проблемы:* {context.user_data['system_type']}\n"
        f"📝 *Описание:* {context.user_data['problem']}\n"
        f"⏰ *Срочность:* {context.user_data['urgency']}\n"
        f"📸 *Фото/скриншот:* {photo_status}\n"
        f"🕒 *Время создания:* {context.user_data['timestamp']}"
    )
    
    context.user_data['summary'] = summary

def confirm_request(update: Update, context: CallbackContext) -> int:
    """Подтверждает и отправляет заявку"""
    if update.message.text == '🚀 Отправить заявку':
        user = update.message.from_user
        
        try:
            request_id = db.save_request(context.user_data)
            send_admin_notification(context, context.user_data, request_id)
            
            confirmation_text = (
                f"🎉 *Заявка #{request_id} успешно создана!*\n\n"
                f"📋 *Детали заявки:*\n"
                f"• 💻 Тип: {context.user_data['system_type']}\n"
                f"• 📍 Участок: {context.user_data['plot']}\n"
                f"• ⏰ Срочность: {context.user_data['urgency']}\n\n"
                f"👨‍💼 *Специалист IT отдела свяжется с вами в ближайшее время.*\n\n"
                f"_Спасибо за обращение в IT отдел!_ 💻"
            )
            
            if user.id in Config.ADMIN_CHAT_IDS:
                update.message.reply_text(
                    confirmation_text,
                    reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True),
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
                "⚠️ Пожалуйста, попробуйте позже или обратитесь в IT отдел напрямую."
            )
            
            if user.id in Config.ADMIN_CHAT_IDS:
                update.message.reply_text(
                    error_message,
                    reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True),
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
        
    elif update.message.text == '✏️ Исправить':
        context.user_data['editing_mode'] = True
        return edit_request_choice(update, context)
    
    elif update.message.text == '🔙 Отменить':
        return cancel_request(update, context)
    
    # На случай, если пришло неожиданное сообщение
    return ConversationHandler.END

def send_admin_notification(context: CallbackContext, user_data: Dict, request_id: int) -> None:
    """Отправляет уведомление администраторам о новой заявке"""
    notification_text = (
        f"🚨 *НОВАЯ ЗАЯВКА В IT ОТДЕЛ #{request_id}*\n\n"
        f"👤 *Пользователь:* @{user_data.get('username', 'N/A')}\n"
        f"📛 *Имя:* {user_data.get('name')}\n"
        f"📞 *Телефон:* `{user_data.get('phone')}`\n"
        f"📍 *Участок:* {user_data.get('plot')}\n"
        f"💻 *Тип проблемы:* {user_data.get('system_type')}\n"
        f"⏰ *Срочность:* {user_data.get('urgency')}\n"
        f"📸 *Фото:* {'✅ Добавлено' if user_data.get('photo') else '❌ Отсутствует'}\n\n"
        f"📝 *Описание:* {user_data.get('problem')}\n\n"
        f"🕒 *Время создания:* {user_data.get('timestamp')}"
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
            reply_markup=ReplyKeyboardMarkup(admin_main_menu_keyboard, resize_keyboard=True)
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
    """Начинает процесс редактирования заявки - ИСПРАВЛЕНА"""
    user_id = update.message.from_user.id
    logger.info(f"Пользователь {user_id} начал редактирование заявки")
    
    # Очищаем предыдущие данные редактирования
    context.user_data.pop('editing_request_id', None)
    context.user_data.pop('editing_request_data', None)
    context.user_data.pop('editing_existing', None)
    
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
            "✅ У вас нет активных заявок для редактирования.\n\n"
            "📋 Можно редактировать только заявки со статусом:\n"
            "• 🆕 Новая\n"
            "• 🔄 В работе",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    context.user_data['editable_requests'] = active_requests
    
    keyboard = []
    for req in active_requests:
        status_icon = '🆕' if req['status'] == 'new' else '🔄'
        button_text = f"{status_icon} #{req['id']} - {req['system_type']}"
        keyboard.append([button_text])
    
    keyboard.append(['🔙 Главное меню'])
    
    update.message.reply_text(
        "✏️ *Редактирование заявки*\n\n"
        "📋 *Выберите заявку для редактирования:*\n"
        "Доступны только активные заявки:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return SELECT_REQUEST

def select_request_for_edit(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор заявки для редактирования - ИСПРАВЛЕНА"""
    text = update.message.text
    
    if text == '🔙 Главное меню':
        return cancel_edit(update, context)
    
    # Ищем выбранную заявку
    editable_requests = context.user_data.get('editable_requests', [])
    selected_request = None
    
    for req in editable_requests:
        expected_text = f"{'🆕' if req['status'] == 'new' else '🔄'} #{req['id']} - {req['system_type']}"
        if text == expected_text:
            selected_request = req
            break
    
    if not selected_request:
        update.message.reply_text(
            "❌ Заявка не найдена. Пожалуйста, выберите заявку из списка:",
            reply_markup=ReplyKeyboardMarkup([['🔙 Главное меню']], resize_keyboard=True)
        )
        return SELECT_REQUEST
    
    # Сохраняем выбранную заявку в context
    context.user_data['editing_request_id'] = selected_request['id']
    context.user_data['editing_request_data'] = selected_request
    
    # Загружаем данные заявки в user_data для редактирования
    context.user_data.update({
        'name': selected_request['name'],
        'phone': selected_request['phone'],
        'plot': selected_request['plot'],
        'system_type': selected_request['system_type'],
        'problem': selected_request['problem'],
        'urgency': selected_request['urgency'],
        'photo': selected_request['photo'],
        'user_id': selected_request['user_id'],
        'username': selected_request['username'],
        'editing_existing': True  # Флаг что редактируем существующую заявку
    })
    
    # Показываем сводку заявки и меню редактирования
    return show_edit_summary(update, context)

def show_edit_summary(update: Update, context: CallbackContext) -> int:
    """Показывает сводку редактируемой заявки"""
    request_data = context.user_data
    request_id = context.user_data.get('editing_request_id')
    
    photo_status = "✅ Добавлено" if request_data.get('photo') else "❌ Отсутствует"
    
    summary = (
        f"✏️ *Редактирование заявки #{request_id}*\n\n"
        f"👤 *Имя:* {request_data['name']}\n"
        f"📞 *Телефон:* `{request_data['phone']}`\n"
        f"📍 *Участок:* {request_data['plot']}\n"
        f"💻 *Тип проблемы:* {request_data['system_type']}\n"
        f"📝 *Описание:* {request_data['problem']}\n"
        f"⏰ *Срочность:* {request_data['urgency']}\n"
        f"📸 *Фото:* {photo_status}\n"
        f"🕒 *Последнее обновление:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    update.message.reply_text(
        f"{summary}\n\n"
        "🎯 *Выберите поле для редактирования:*",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return EDIT_CHOICE

def edit_request_choice(update: Update, context: CallbackContext) -> int:
    """Показывает меню выбора поля для редактирования (для создания заявки)"""
    summary = context.user_data.get('summary', '')
    
    update.message.reply_text(
        f"{summary}\n\n"
        "🎯 *Выберите поле для редактирования:*",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    return EDIT_CHOICE

def handle_edit_choice(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор поля для редактирования"""
    choice = update.message.text
    context.user_data['editing_field'] = choice
    
    if choice == '👤 Имя':
        update.message.reply_text(
            f"✏️ *Введите новое имя:*\nТекущее: {context.user_data['name']}",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📞 Телефон':
        update.message.reply_text(
            f"✏️ *Введите новый телефон:*\nТекущий: {context.user_data['phone']}\n\n"
            "📋 Пример: +7 999 123-45-67",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📍 Участок':
        update.message.reply_text(
            f"✏️ *Выберите новый участок:*\nТекущий: {context.user_data['plot']}",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '💻 Система':
        update.message.reply_text(
            f"✏️ *Выберите новую систему:*\nТекущая: {context.user_data['system_type']}",
            reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📝 Описание':
        update.message.reply_text(
            f"✏️ *Введите новое описание проблемы:*\nТекущее: {context.user_data['problem']}",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '⏰ Срочность':
        update.message.reply_text(
            f"✏️ *Выберите новую срочность:*\nТекущая: {context.user_data['urgency']}",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📷 Фото':
        photo_status = "есть фото" if context.user_data.get('photo') else "нет фото"
        update.message.reply_text(
            f"✏️ *Работа с фото:*\nТекущее: {photo_status}",
            reply_markup=ReplyKeyboardMarkup([
                ['📷 Добавить новое фото', '🗑️ Удалить фото'],
                ['◀️ Назад к редактированию']
            ], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '✅ Готово':
        if context.user_data.get('editing_existing'):
            return save_edited_request(update, context)
        else:
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
    if text == '◀️ Назад к редактированию':
        if context.user_data.get('editing_existing'):
            return show_edit_summary(update, context)
        else:
            return edit_request_choice(update, context)
    
    # Обработка фото
    if update.message.photo:
        context.user_data['photo'] = update.message.photo[-1].file_id
        update.message.reply_text(
            "✅ Фото обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        if context.user_data.get('editing_existing'):
            return show_edit_summary(update, context)
        else:
            return edit_request_choice(update, context)
    
    # Обработка текстовых полей
    if editing_field == '👤 Имя':
        if not Validators.validate_name(text):
            update.message.reply_text(
                "❌ Неверный формат имени! Должно быть от 2 до 50 букв.",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        context.user_data['name'] = text
        update.message.reply_text(
            "✅ Имя обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📞 Телефон':
        if not Validators.validate_phone(text):
            update.message.reply_text(
                "❌ Неверный формат телефона! Попробуйте еще раз.",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        context.user_data['phone'] = text
        update.message.reply_text(
            "✅ Телефон обновлен!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📍 Участок':
        if text in ['🔙 Назад', '🔙 Главное меню']:
            if context.user_data.get('editing_existing'):
                return show_edit_summary(update, context)
            else:
                return edit_request_choice(update, context)
        
        if text == '📋 Другой участок':
            update.message.reply_text(
                "✏️ *Введите название вашего участка:*",
                reply_markup=ReplyKeyboardMarkup([['◀️ Назад к редактированию']], resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['editing_other_plot'] = True
            return OTHER_PLOT
        
        context.user_data['plot'] = text
        update.message.reply_text(
            "✅ Участок обновлен!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '💻 Система':
        if text in ['🔙 Назад', '🔙 Главное меню']:
            if context.user_data.get('editing_existing'):
                return show_edit_summary(update, context)
            else:
                return edit_request_choice(update, context)
        context.user_data['system_type'] = text
        update.message.reply_text(
            "✅ Система обновлена!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📝 Описание':
        if not Validators.validate_problem(text):
            update.message.reply_text(
                "❌ Описание должно быть от 10 до 1000 символов!",
                reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True)
            )
            return EDIT_FIELD
        context.user_data['problem'] = text
        update.message.reply_text(
            "✅ Описание обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '⏰ Срочность':
        if text == '🔙 Назад':
            if context.user_data.get('editing_existing'):
                return show_edit_summary(update, context)
            else:
                return edit_request_choice(update, context)
        context.user_data['urgency'] = text
        update.message.reply_text(
            "✅ Срочность обновлена!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📷 Фото':
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
                    ['◀️ Назад к редактированию']
                ], resize_keyboard=True)
            )
            return EDIT_FIELD
    
    if context.user_data.get('editing_existing'):
        return show_edit_summary(update, context)
    else:
        return edit_request_choice(update, context)

def save_edited_request(update: Update, context: CallbackContext) -> int:
    """Сохраняет отредактированную заявку"""
    request_id = context.user_data.get('editing_request_id')
    
    if not request_id:
        update.message.reply_text(
            "❌ Ошибка: не найдена заявка для сохранения.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    # Подготавливаем данные для обновления
    update_data = {
        'name': context.user_data.get('name'),
        'phone': context.user_data.get('phone'),
        'plot': context.user_data.get('plot'),
        'system_type': context.user_data.get('system_type'),
        'problem': context.user_data.get('problem'),
        'urgency': context.user_data.get('urgency'),
        'photo': context.user_data.get('photo')
    }
    
    try:
        # Обновляем заявку в базе данных
        success = db.update_request(request_id, update_data)
        
        if success:
            # Отправляем уведомление администраторам об изменении заявки
            send_edit_notification(context, request_id, update_data)
            
            update.message.reply_text(
                f"✅ *Заявка #{request_id} успешно обновлена!*\n\n"
                f"📋 Изменения сохранены в системе.\n"
                f"👨‍💼 Специалист IT отдела увидит обновленные данные.",
                reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Заявка #{request_id} отредактирована пользователем {context.user_data.get('user_id')}")
        else:
            update.message.reply_text(
                "❌ *Произошла ошибка при сохранении изменений.*\n\nПожалуйста, попробуйте позже.",
                reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
    
    except Exception as e:
        logger.error(f"Ошибка при сохранении отредактированной заявки #{request_id}: {e}")
        update.message.reply_text(
            "❌ *Произошла ошибка при сохранении изменений.*\n\nПожалуйста, попробуйте позже.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    
    # Очищаем данные редактирования
    context.user_data.pop('editing_request_id', None)
    context.user_data.pop('editing_request_data', None)
    context.user_data.pop('editing_existing', None)
    context.user_data.pop('editing_field', None)
    
    return ConversationHandler.END

def send_edit_notification(context: CallbackContext, request_id: int, update_data: Dict) -> None:
    """Отправляет уведомление администраторам об изменении заявки"""
    notification_text = (
        f"✏️ *ЗАЯВКА #{request_id} ОБНОВЛЕНА*\n\n"
        f"👤 *Пользователь:* @{context.user_data.get('username', 'N/A')}\n"
        f"📛 *Имя:* {update_data['name']}\n"
        f"📞 *Телефон:* `{update_data['phone']}`\n"
        f"📍 *Участок:* {update_data['plot']}\n"
        f"💻 *Тип проблемы:* {update_data['system_type']}\n"
        f"⏰ *Срочность:* {update_data['urgency']}\n"
        f"📸 *Фото:* {'✅ Добавлено' if update_data.get('photo') else '❌ Отсутствует'}\n\n"
        f"📝 *Описание:* {update_data['problem']}\n\n"
        f"🕒 *Время обновления:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    for admin_id in Config.ADMIN_CHAT_IDS:
        try:
            if update_data.get('photo'):
                context.bot.send_photo(
                    chat_id=admin_id,
                    photo=update_data['photo'],
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
            logger.error(f"Ошибка отправки уведомления об изменении администратору {admin_id}: {e}")

def cancel_edit(update: Update, context: CallbackContext) -> int:
    """Отменяет редактирование заявки"""
    # Очищаем данные редактирования
    context.user_data.pop('editing_request_id', None)
    context.user_data.pop('editing_request_data', None)
    context.user_data.pop('editing_existing', None)
    context.user_data.pop('editing_field', None)
    context.user_data.pop('editable_requests', None)
    
    update.message.reply_text(
        "❌ Редактирование заявки отменено.",
        reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
    )
    
    return ConversationHandler.END

def other_plot_edit(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод пользовательского участка в режиме редактирования"""
    if update.message.text == '◀️ Назад к редактированию':
        if context.user_data.get('editing_existing'):
            return show_edit_summary(update, context)
        else:
            return edit_request_choice(update, context)
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "✅ Участок обновлен!",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
    )
    
    if context.user_data.get('editing_existing'):
        return show_edit_summary(update, context)
    else:
        return edit_request_choice(update, context)

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
        "👑 *Панель администратора IT отдела*\n\n"
        f"📊 *Статистика заявок:*\n"
        f"🆕 *Новых:* {len(new_requests)}\n"
        f"🔄 *В работе:* {len(in_progress_requests)}\n"
        f"✅ *Выполненных:* {len(completed_requests)}\n"
        f"📈 *Всего активных:* {len(new_requests) + len(in_progress_requests)}\n\n"
        "🎯 *Выберите раздел для работы:*"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_requests_by_filter(update: Update, context: CallbackContext, filter_type: str) -> None:
    """Показывает заявки по фильтру"""
    user_id = update.message.from_user.id
    if user_id not in Config.ADMIN_CHAT_IDS:
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
        if req['status'] == 'completed':
            request_text = (
                f"✅ *Заявка #{req['id']} - ВЫПОЛНЕНА*\n\n"
                f"👤 *Клиент:* {req['name']}\n"
                f"📞 *Телефон:* `{req['phone']}`\n"
                f"📍 *Участок:* {req['plot']}\n"
                f"💻 *Тип проблемы:* {req['system_type']}\n"
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
                f"💻 *Тип проблемы:* {req['system_type']}\n"
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
                f"💻 *Тип проблемы:* {req['system_type']}\n"
                f"⏰ *Срочность:* {req['urgency']}\n"
                f"📝 *Описание:* {req['problem']}\n"
                f"📸 *Фото:* {'✅ Есть' if req['photo'] else '❌ Нет'}\n"
                f"🕒 *Создана:* {req['created_at'][:16]}"
            )
        
        if req.get('admin_comment'):
            request_text += f"\n💬 *Комментарий администратора:* {req['admin_comment']}"
        
        # Кнопки без "Позвонить"
        if req['status'] == 'completed':
            keyboard = [[
                InlineKeyboardButton("💬 Написать", callback_data=f"message_{req['id']}")
            ]]
        elif req['status'] == 'in_progress':
            if req.get('assigned_admin') == update.message.from_user.first_name:
                keyboard = [[
                    InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{req['id']}")
                ]]
            else:
                keyboard = [[
                    InlineKeyboardButton("💬 Написать", callback_data=f"message_{req['id']}")
                ]]
        else:
            keyboard = [[
                InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{req['id']}")
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
    
    if user_id not in Config.ADMIN_CHAT_IDS:
        return
    
    if data.startswith('take_'):
        request_id = int(data.split('_')[1])
        admin_name = query.from_user.first_name
        
        db.update_request_status(
            request_id, 
            "in_progress", 
            f"Заявка взята в работу администратором {admin_name}",
            admin_name
        )
        
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
        
        request_text = (
            f"✅ *Заявка #{request_id} взята вами в работу!*\n\n"
            f"👤 *Клиент:* {request['name']}\n"
            f"📞 *Телефон:* `{request['phone']}`\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"💻 *Тип:* {request['system_type']}\n"
            f"⏰ *Срочность:* {request['urgency']}\n"
            f"📝 *Описание:* {request['problem']}\n\n"
            f"🔄 *Статус:* В работе\n"
            f"👨‍💼 *Исполнитель:* {admin_name}"
        )
        
        keyboard = [[
            InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{request_id}")
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
        
        db.update_request_status(
            request_id, 
            "completed", 
            f"Заявка выполнена администратором {admin_name}",
            admin_name
        )
        
        request = db.get_request(request_id)
        if request and request.get('user_id'):
            try:
                context.bot.send_message(
                    chat_id=request['user_id'],
                    text=f"✅ *Ваша заявка #{request_id} выполнена!*\n\n"
                         f"👨‍💼 *Исполнитель:* {admin_name}\n"
                         f"💬 *Комментарий:* Заявка выполнена\n\n"
                         f"_Спасибо, что воспользовались услугами IT отдела!_ 💻",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {request['user_id']}: {e}")
        
        request_text = (
            f"✅ *Заявка #{request_id} ВЫПОЛНЕНА!*\n\n"
            f"👤 *Клиент:* {request['name']}\n"
            f"📞 *Телефон:* `{request['phone']}`\n"
            f"📍 *Участок:* {request['plot']}\n"
            f"💻 *Тип проблемы:* {request['system_type']}\n"
            f"⏰ *Срочность:* {request['urgency']}\n"
            f"📝 *Описание:* {request['problem']}\n"
            f"📸 *Фото:* {'✅ Есть' if request['photo'] else '❌ Нет'}\n\n"
            f"✅ *Статус:* Выполнено\n"
            f"👨‍💼 *Исполнитель:* {admin_name}\n"
            f"💬 *Комментарий:* Заявка выполнена\n"
            f"🕒 *Завершена:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        keyboard = [[
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
        
        query.answer("✅ Заявка выполнена!")
    
    elif data.startswith('message_'):
        request_id = int(data.split('_')[1])
        request = db.get_request(request_id)
        
        if request:
            phone_number = request['phone'].replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
            
            message_button = InlineKeyboardButton(
                "💬 Написать сообщение", 
                url=f"https://t.me/{phone_number}" if phone_number.startswith('+') else f"https://t.me/{phone_number}"
            )
            
            contact_text = (
                f"💬 *Контактная информация по заявке #{request_id}*\n\n"
                f"👤 *Клиент:* {request['name']}\n"
                f"📞 *Телефон:* `{request['phone']}`\n"
                f"📍 *Участок:* {request['plot']}\n"
                f"💻 *Тип проблемы:* {request['system_type']}\n"
                f"⏰ *Срочность:* {request['urgency']}\n\n"
                f"_Нажмите кнопку ниже для написания сообщения в Telegram_"
            )
            
            query.answer("💬 Открывается чат...")
            
            context.bot.send_message(
                chat_id=query.message.chat_id,
                text=contact_text,
                reply_markup=InlineKeyboardMarkup([[message_button]]),
                parse_mode=ParseMode.MARKDOWN
            )

def handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in Config.ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if text == '🆕 Новые заявки':
        return show_requests_by_filter(update, context, 'new')
    elif text == '🔄 В работе':
        return show_requests_by_filter(update, context, 'in_progress')
    elif text == '✅ Выполненные заявки':  # ✅ Исправлено название кнопки
        return show_requests_by_filter(update, context, 'completed')
    elif text == '📊 Статистика':
        return show_statistics(update, context)

def show_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает статистику"""
    user_id = update.message.from_user.id
    if user_id not in Config.ADMIN_CHAT_IDS:
        return
    
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    completed_requests = db.get_requests_by_filter('completed')
    
    # Простая статистика по типам проблем
    system_stats = {}
    for req in new_requests + in_progress_requests + completed_requests:
        system_type = req['system_type']
        system_stats[system_type] = system_stats.get(system_type, 0) + 1
    
    stats_text = "📊 *Статистика IT отдела*\n\n"
    stats_text += f"🆕 *Новых заявок:* {len(new_requests)}\n"
    stats_text += f"🔄 *В работе:* {len(in_progress_requests)}\n"
    stats_text += f"✅ *Выполненных:* {len(completed_requests)}\n"
    stats_text += f"📈 *Всего заявок:* {len(new_requests) + len(in_progress_requests) + len(completed_requests)}\n\n"
    
    stats_text += "💻 *Распределение по типам проблем:*\n"
    for system_type, count in sorted(system_stats.items(), key=lambda x: x[1], reverse=True):
        stats_text += f"• {system_type}: {count}\n"
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ОСНОВНЫЕ ФУНКЦИИ ====================

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Показывает главное меню"""
    user = update.message.from_user
    user_id = user.id
    
    if user_id in Config.ADMIN_CHAT_IDS:
        keyboard = admin_main_menu_keyboard
        welcome_text = (
            "👑 *Добро пожаловать в панель администратора IT отдела!*\n\n"
            "💻 *Обслуживаемые системы:*\n"
            "• Компьютеры и рабочее место\n"
            "• Принтеры и МФУ\n" 
            "• Интернет и локальная сеть\n"
            "• Телефония и связь\n"
            "• Программное обеспечение\n"
            "• 1С и базы данных\n"
            "• Оборудование и периферия\n\n"
            "🎯 *Выберите действие из меню:*"
        )
    else:
        keyboard = user_main_menu_keyboard
        welcome_text = (
            "💻 *Добро пожаловать в сервис заявок IT отдела!*\n\n"
            "🛠️ *Мы поможем с:*\n"
            "• Настройкой компьютеров и программ\n"
            "• Ремонтом принтеров и МФУ\n"
            "• Проблемами с интернетом и сетью\n"
            "• Настройкой телефонии\n"
            "• Установкой программного обеспечения\n"
            "• Работой с 1С и базами данных\n"
            "• Любыми техническими вопросами\n\n"
            "🎯 *Выберите действие из меню ниже:*"
        )
    
    update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_my_requests(update: Update, context: CallbackContext) -> None:
    """Показывает заявки пользователя"""
    user_id = update.message.from_user.id
    
    if user_id in Config.ADMIN_CHAT_IDS:
        keyboard = admin_main_menu_keyboard
    else:
        keyboard = user_main_menu_keyboard
    
    requests = db.get_user_requests(user_id, 50)
    
    if not requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок.\n\n"
            "🎯 Хотите создать первую заявку?",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return
    
    active_requests = [req for req in requests if req['status'] != 'completed']
    completed_requests = [req for req in requests if req['status'] == 'completed']
    
    if not active_requests and not completed_requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок.\n\n"
            "🎯 Хотите создать первую заявку?",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return
    
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
                f"💻 *Тип:* {req['system_type']}\n"
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
    
    if completed_requests:
        update.message.reply_text(
            f"✅ *История выполненных заявок ({len(completed_requests)}):*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        for req in completed_requests:
            request_text = (
                f"✅ *Заявка #{req['id']} - ВЫПОЛНЕНА*\n"
                f"💻 *Тип проблемы:* {req['system_type']}\n"
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
    
    total_text = f"📊 *Итого:* {len(active_requests)} активных, {len(completed_requests)} выполненных заявок"
    update.message.reply_text(
        total_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_contacts(update: Update, context: CallbackContext) -> None:
    """Показывает контакты IT отдела"""
    contacts_text = (
        "📞 *Контакты IT отдела*\n\n"
        "👔 *Руководитель отдела:*\n"
        "• Комаров Денис\n"
        "• 📱 +7 911 426 18 66\n\n"
        "💻 *Системный администратор:*\n"
        "• Михаил\n"
        "• 📱 +7 995 830 37 92\n\n"
        "🕒 *Время работы:*\n"
        "• Пн-Пт: 9:00 - 18:00\n"
        "• Сб: 10:00 - 15:00\n"
        "• Вс: выходной\n\n"
        "📍 *Расположение:*\n"
        "• Кабинет IT отдела: 3 этаж\n"
        "• Техническая поддержка: 3 этаж\n\n"
        "💬 *Экстренная связь:*\n"
        "• По телефону в рабочее время\n"
        "• Через бот в любое время"
    )
    
    user_id = update.message.from_user.id
    keyboard = admin_main_menu_keyboard if user_id in Config.ADMIN_CHAT_IDS else user_main_menu_keyboard
    
    update.message.reply_text(
        contacts_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в главном меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id in Config.ADMIN_CHAT_IDS:
        if text == '👑 Админ-панель':
            return show_admin_panel(update, context)
        elif text == '🎯 Создать заявку':
            return start_request_creation(update, context)
        elif text == '📂 Мои заявки':
            return show_my_requests(update, context)
    else:
        if text == '🎯 Создать заявку':
            return start_request_creation(update, context)
        elif text == '📂 Мои заявки':
            return show_my_requests(update, context)
        elif text == '✏️ Редактировать заявку':
            return start_edit_request(update, context)
        elif text == '📞 Контакты IT':
            return show_contacts(update, context)
    
    update.message.reply_text(
        "🎯 Пожалуйста, выберите действие из меню:",
        reply_markup=ReplyKeyboardMarkup(
            admin_main_menu_keyboard if user_id in Config.ADMIN_CHAT_IDS else user_main_menu_keyboard, 
            resize_keyboard=True
        )
    )

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
                MessageHandler(Filters.regex('^(🎯 Создать заявку)$'), start_request_creation),
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
                MessageHandler(Filters.regex('^(🔙 Главное меню|🔙 Отменить)$'), cancel_request),
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
                MessageHandler(Filters.regex('^(🔙 Главное меню)$'), cancel_edit),
            ],
            allow_reentry=True
        )

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_admin_panel))
        dispatcher.add_handler(CommandHandler('contacts', show_contacts))
        dispatcher.add_handler(CommandHandler('statistics', show_statistics))
        
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(edit_conv_handler)
        
        # Обработчики для кнопок подтверждения и редактирования
        dispatcher.add_handler(MessageHandler(Filters.regex('^(🚀 Отправить заявку)$'), confirm_request))
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✏️ Исправить)$'), confirm_request))
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(Filters.regex(
            '^(📂 Мои заявки|👑 Админ-панель|📊 Статистика|📞 Контакты IT)$'), 
            handle_main_menu
        ))
        
        # Обработчики админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки|🔄 В работе|✅ Выполненные заявки)$'), 
            handle_admin_menu
        ))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(handle_admin_callback, pattern='^(take_|complete_|message_)'))

        # Запускаем бота
        logger.info("🤖 Бот IT отдела запущен с исправлениями!")
        logger.info(f"👑 Администраторы: {Config.ADMIN_CHAT_IDS}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
