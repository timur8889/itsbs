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
ADMIN_CHAT_IDS = [5024165375]
BOT_TOKEN = "7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw"

# Определяем этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD, OTHER_PLOT, SELECT_REQUEST = range(11)

# База данных
DB_PATH = "requests.db"

# ==================== КЛАВИАТУРЫ ====================

user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['✏️ Редактировать заявку']  # Новая кнопка редактирования
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

# Клавиатура для выбора заявки для редактирования
edit_request_selection_keyboard = [
    ['🔙 Назад в меню']
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

    def update_request(self, request_id: int, update_data: Dict) -> bool:
        """Обновляет данные заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Формируем SQL запрос для обновления
                set_parts = []
                parameters = []
                
                for field, value in update_data.items():
                    if field in ['name', 'phone', 'plot', 'system_type', 'problem', 'photo', 'urgency']:
                        set_parts.append(f"{field} = ?")
                        parameters.append(value)
                
                # Добавляем время обновления
                set_parts.append("updated_at = ?")
                parameters.append(datetime.now().isoformat())
                
                # Добавляем ID заявки
                parameters.append(request_id)
                
                if set_parts:
                    sql = f"UPDATE requests SET {', '.join(set_parts)} WHERE id = ?"
                    cursor.execute(sql, parameters)
                    conn.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Ошибка при обновлении заявки #{request_id}: {e}")
            return False

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

# Инициализация базы данных
db = Database(DB_PATH)

# ==================== РЕДАКТИРОВАНИЕ ЗАЯВОК ====================

def start_edit_request(update: Update, context: CallbackContext) -> int:
    """Начинает процесс редактирования заявки"""
    user_id = update.message.from_user.id
    
    # Получаем заявки пользователя
    requests = db.get_user_requests(user_id, 20)
    
    if not requests:
        update.message.reply_text(
            "📭 У вас пока нет созданных заявок для редактирования.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    # Фильтруем только активные заявки (не выполненные)
    active_requests = [req for req in requests if req['status'] != 'completed']
    
    if not active_requests:
        update.message.reply_text(
            "✅ У вас нет активных заявок для редактирования. Можно редактировать только заявки со статусом 'Новая' или 'В работе'.",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    # Сохраняем заявки в context для дальнейшего использования
    context.user_data['editable_requests'] = active_requests
    
    # Создаем клавиатуру с заявками для выбора
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

def select_request_for_edit(update: Update, context: CallbackContext) -> int:
    """Обрабатывает выбор заявки для редактирования"""
    text = update.message.text
    
    if text == '🔙 Назад в меню':
        return cancel_edit(update, context)
    
    # Ищем выбранную заявку
    editable_requests = context.user_data.get('editable_requests', [])
    selected_request = None
    
    for req in editable_requests:
        expected_text = f"{'🆕' if req['status'] == 'new' else '🔄'} Заявка #{req['id']} - {req['system_type']}"
        if text == expected_text:
            selected_request = req
            break
    
    if not selected_request:
        update.message.reply_text(
            "❌ Заявка не найдена. Пожалуйста, выберите заявку из списка:",
            reply_markup=ReplyKeyboardMarkup(edit_request_selection_keyboard, resize_keyboard=True)
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
    
    photo_status = "✅ Есть" if request_data.get('photo') else "❌ Нет"
    
    summary = (
        f"✏️ *Редактирование заявки #{request_id}*\n\n"
        f"📛 *Имя:* {request_data['name']}\n"
        f"📞 *Телефон:* `{request_data['phone']}`\n"
        f"📍 *Участок:* {request_data['plot']}\n"
        f"🔧 *Тип системы:* {request_data['system_type']}\n"
        f"📝 *Описание:* {request_data['problem']}\n"
        f"⏰ *Срочность:* {request_data['urgency']}\n"
        f"📸 *Фото:* {photo_status}\n"
        f"🕒 *Последнее обновление:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
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
            f"✏️ *Введите новое имя:*\nТекущее: {context.user_data['name']}",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📞 Редактировать телефон':
        update.message.reply_text(
            f"✏️ *Введите новый телефон:*\nТекущий: {context.user_data['phone']}\n\nПример: +7 999 123-45-67",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📍 Редактировать участок':
        update.message.reply_text(
            f"✏️ *Выберите новый участок:*\nТекущий: {context.user_data['plot']}",
            reply_markup=ReplyKeyboardMarkup(plot_type_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '🔧 Редактировать систему':
        update.message.reply_text(
            f"✏️ *Выберите новую систему:*\nТекущая: {context.user_data['system_type']}",
            reply_markup=ReplyKeyboardMarkup(create_request_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📝 Редактировать описание':
        update.message.reply_text(
            f"✏️ *Введите новое описание проблемы:*\nТекущее: {context.user_data['problem']}",
            reply_markup=ReplyKeyboardMarkup(edit_field_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '⏰ Редактировать срочность':
        update.message.reply_text(
            f"✏️ *Выберите новую срочность:*\nТекущая: {context.user_data['urgency']}",
            reply_markup=ReplyKeyboardMarkup(urgency_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '📷 Редактировать фото':
        photo_status = "есть фото" if context.user_data.get('photo') else "нет фото"
        update.message.reply_text(
            f"✏️ *Отправьте новое фото или выберите действие:*\nТекущее: {photo_status}",
            reply_markup=ReplyKeyboardMarkup([
                ['📷 Добавить новое фото', '🗑️ Удалить фото'],
                ['🔙 Назад к редактированию']
            ], resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return EDIT_FIELD
        
    elif choice == '✅ Завершить редактирование':
        return save_edited_request(update, context)
    
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
        return show_edit_summary(update, context)
    
    # Обработка фото
    if update.message.photo:
        context.user_data['photo'] = update.message.photo[-1].file_id
        update.message.reply_text(
            "✅ Фото обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        return show_edit_summary(update, context)
    
    # Обработка текстовых полей
    if editing_field == '📛 Редактировать имя':
        context.user_data['name'] = text
        update.message.reply_text(
            "✅ Имя обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📞 Редактировать телефон':
        context.user_data['phone'] = text
        update.message.reply_text(
            "✅ Телефон обновлен!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📍 Редактировать участок':
        if text in ['🔙 Назад', '🔙 Назад в меню']:
            return show_edit_summary(update, context)
        
        if text == '📦 Другой участок':
            update.message.reply_text(
                "✏️ *Введите название вашего участка:*\n\nПример: Сборочный цех, Склад №2 и т.д.",
                reply_markup=ReplyKeyboardMarkup([['🔙 Назад к редактированию']], resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['editing_other_plot'] = True
            return OTHER_PLOT
        
        context.user_data['plot'] = text
        update.message.reply_text(
            "✅ Участок обновлен!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '🔧 Редактировать систему':
        if text in ['🔙 Назад', '🔙 Назад в меню']:
            return show_edit_summary(update, context)
        context.user_data['system_type'] = text
        update.message.reply_text(
            "✅ Система обновлена!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '📝 Редактировать описание':
        context.user_data['problem'] = text
        update.message.reply_text(
            "✅ Описание обновлено!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
    elif editing_field == '⏰ Редактировать срочность':
        if text == '🔙 Назад':
            return show_edit_summary(update, context)
        context.user_data['urgency'] = text
        update.message.reply_text(
            "✅ Срочность обновлена!",
            reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
        )
        
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
    
    return show_edit_summary(update, context)

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
                f"Изменения сохранены в системе.",
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
        f"🔧 *Система:* {update_data['system_type']}\n"
        f"⏰ *Срочность:* {update_data['urgency']}\n"
        f"📸 *Фото:* {'✅ Есть' if update_data.get('photo') else '❌ Нет'}\n\n"
        f"📝 *Описание:* {update_data['problem']}\n\n"
        f"🕒 *Время обновления:* {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    
    for admin_id in ADMIN_CHAT_IDS:
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

# ==================== ОСНОВНЫЕ ФУНКЦИИ (сохранены из предыдущего кода) ====================

def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Показывает главное меню"""
    user = update.message.from_user
    user_id = user.id
    
    if user_id in ADMIN_CHAT_IDS:
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

def handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает выбор в главном меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        return show_admin_panel(update, context)
    
    if text == '📝 Создать заявку':
        return start_request_creation(update, context)
    elif text == '📋 Мои заявки':
        return show_my_requests(update, context)
    elif text == '✏️ Редактировать заявку':
        return start_edit_request(update, context)
    else:
        update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(user_main_menu_keyboard, resize_keyboard=True)
        )

# ==================== ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РЕДАКТИРОВАНИЯ ====================

def other_plot_edit(update: Update, context: CallbackContext) -> int:
    """Обрабатывает ввод пользовательского участка в режиме редактирования"""
    if update.message.text == '🔙 Назад к редактированию':
        return show_edit_summary(update, context)
    
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        "✅ Участок обновлен!",
        reply_markup=ReplyKeyboardMarkup(edit_choice_keyboard, resize_keyboard=True)
    )
    return show_edit_summary(update, context)

# ==================== ОСНОВНЫЕ ФУНКЦИИ БОТА ====================

def main() -> None:
    """Запускаем бота"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Обработчик создания заявки (сохранен из предыдущего кода)
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
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📋 Мои заявки|👑 Админ-панель)$'), handle_main_menu))
        
        # Запускаем бота
        logger.info("🤖 Бот запущен с функцией редактирования заявок!")
        logger.info(f"👑 Администраторы: {ADMIN_CHAT_IDS}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
