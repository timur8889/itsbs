import logging
import sqlite3
import os
import json
import re
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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
)

# Загружаем переменные окружения из .env файла
from dotenv import load_dotenv
load_dotenv()

# ==================== ЛОГИРОВАНИЕ ====================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Конфигурация приложения"""
    # Получаем токен из переменных окружения
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не установлен! Добавьте его в .env файл или переменные окружения")
    
    # Супер-админ (имеет доступ ко всем отделам + массовая рассылка)
    SUPER_ADMIN_IDS = [5024165375]
    
    # Админы по отделам
    ADMIN_CHAT_IDS = {
        '💻 IT отдел': [5024165375, 123456789],
        '🔧 Механика': [5024165375, 987654321],
        '⚡ Электрика': [5024165375, 555555555]
    }
    
    DB_PATH = "requests.db"
    REQUEST_TIMEOUT_HOURS = 48

    @classmethod
    def is_super_admin(cls, user_id: int) -> bool:
        """Проверяет является ли пользователь супер-админом"""
        return user_id in cls.SUPER_ADMIN_IDS

    @classmethod
    def is_admin(cls, user_id: int, department: str = None) -> bool:
        """Проверяет является ли пользователь админом"""
        if cls.is_super_admin(user_id):
            return True
        if department:
            return user_id in cls.ADMIN_CHAT_IDS.get(department, [])
        for dept_admins in cls.ADMIN_CHAT_IDS.values():
            if user_id in dept_admins:
                return True
        return False

    @classmethod
    def get_admins_for_department(cls, department: str) -> List[int]:
        """Получает список админов для отдела"""
        return cls.ADMIN_CHAT_IDS.get(department, [])

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
                        department TEXT,
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
                        assigned_at TEXT,
                        completed_at TEXT
                    )
                ''')
                # Добавляем таблицу для комментариев
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS request_comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id INTEGER,
                        admin_id INTEGER,
                        admin_name TEXT,
                        comment TEXT,
                        created_at TEXT,
                        FOREIGN KEY (request_id) REFERENCES requests (id)
                    )
                ''')
                conn.commit()
                logger.info("✅ База данных успешно инициализирована")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации базы данных: {e}")
            raise

    def save_request(self, user_data: Dict) -> int:
        """Сохраняет заявку в базу данных"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO requests 
                    (user_id, username, name, phone, department, plot, system_type, problem, photo, urgency, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_data.get('user_id'),
                    user_data.get('username'),
                    user_data.get('name'),
                    user_data.get('phone'),
                    user_data.get('department'),
                    user_data.get('plot'),
                    user_data.get('system_type'),
                    user_data.get('problem'),
                    user_data.get('photo'),
                    user_data.get('urgency'),
                    datetime.now().isoformat(),
                    datetime.now().isoformat()
                ))
                request_id = cursor.lastrowid
                conn.commit()
                logger.info(f"✅ Заявка #{request_id} успешно сохранена")
                return request_id
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении заявки: {e}")
            raise

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
            logger.error(f"❌ Ошибка при получении заявки #{request_id}: {e}")
            return {}

    def update_request_status(self, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
        """Обновляет статус заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                update_data = {
                    'status': status,
                    'updated_at': datetime.now().isoformat()
                }
                
                if admin_comment:
                    update_data['admin_comment'] = admin_comment
                
                if assigned_admin:
                    update_data['assigned_admin'] = assigned_admin
                    update_data['assigned_at'] = datetime.now().isoformat()
                
                if status == 'completed':
                    update_data['completed_at'] = datetime.now().isoformat()
                
                set_parts = []
                parameters = []
                for field, value in update_data.items():
                    set_parts.append(f"{field} = ?")
                    parameters.append(value)
                
                parameters.append(request_id)
                sql = f"UPDATE requests SET {', '.join(set_parts)} WHERE id = ?"
                cursor.execute(sql, parameters)
                
                conn.commit()
                logger.info(f"✅ Статус заявки #{request_id} изменен на '{status}'")
        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении статуса заявки #{request_id}: {e}")
            raise

    def add_comment_to_request(self, request_id: int, admin_id: int, admin_name: str, comment: str):
        """Добавляет комментарий к заявке"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO request_comments (request_id, admin_id, admin_name, comment, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (request_id, admin_id, admin_name, comment, datetime.now().isoformat()))
                conn.commit()
                logger.info(f"✅ Комментарий добавлен к заявке #{request_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка добавления комментария к заявке #{request_id}: {e}")
            raise

    def get_request_comments(self, request_id: int) -> List[Dict]:
        """Получает комментарии к заявке"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM request_comments 
                    WHERE request_id = ? 
                    ORDER BY created_at DESC
                ''', (request_id,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"❌ Ошибка получения комментариев заявки #{request_id}: {e}")
            return []

    def get_requests_by_filter(self, department: str = None, status: str = 'all', limit: int = 50) -> List[Dict]:
        """Получает заявки по фильтру отдела и статуса"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                status_conditions = {
                    'new': "status = 'new'",
                    'in_progress': "status = 'in_progress'", 
                    'completed': "status = 'completed'",
                    'all': "status IN ('new', 'in_progress', 'completed')"
                }
                
                status_filter = status_conditions.get(status, "status IN ('new', 'in_progress')")
                
                if department:
                    query = f'''
                        SELECT * FROM requests 
                        WHERE department = ? AND {status_filter}
                        ORDER BY 
                            CASE urgency 
                                WHEN '🔥 СРОЧНО (1-2 часа)' THEN 1
                                WHEN '⚠️ СЕГОДНЯ (до конца дня)' THEN 2
                                ELSE 3
                            END,
                            created_at DESC
                        LIMIT ?
                    '''
                    cursor.execute(query, (department, limit))
                else:
                    query = f'''
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
                    '''
                    cursor.execute(query, (limit,))
                
                columns = [column[0] for column in cursor.description]
                requests = [dict(zip(columns, row)) for row in cursor.fetchall()]
                return requests
        except Exception as e:
            logger.error(f"❌ Ошибка при получении заявок: {e}")
            return []

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
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"❌ Ошибка при получении заявок пользователя {user_id}: {e}")
            return []

# Инициализация базы данных
db = Database(Config.DB_PATH)

# ==================== ВИЗУАЛЬНЫЕ КНОПКИ ====================

def create_request_actions_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру с действиями для заявки"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{request_id}"),
            InlineKeyboardButton("📝 Комментарий", callback_data=f"comment_{request_id}")
        ],
        [
            InlineKeyboardButton("✅ Завершить", callback_data=f"complete_{request_id}"),
            InlineKeyboardButton("📋 Подробнее", callback_data=f"details_{request_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_comment_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для комментария"""
    keyboard = [
        [
            InlineKeyboardButton("✏️ Ввести комментарий", callback_data=f"add_comment_{request_id}"),
            InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_request_{request_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ФОРМАТИРОВАНИЕ ТЕКСТА ====================

def format_request_text(request: Dict) -> str:
    """Форматирует текст заявки"""
    status_emoji = {
        'new': '🆕',
        'in_progress': '🔄',
        'completed': '✅'
    }.get(request['status'], '❓')
    
    return (
        f"📋 *Заявка #{request['id']}* {status_emoji}\n\n"
        f"👤 *ФИО:* {request['name']}\n"
        f"📞 *Телефон:* {request['phone']}\n"
        f"🏢 *Отдел:* {request['department']}\n"
        f"🔧 *Тип проблемы:* {request['system_type']}\n"
        f"📍 *Участок:* {request['plot']}\n"
        f"⏰ *Срочность:* {request['urgency']}\n"
        f"📝 *Описание:* {request['problem'][:200]}...\n"
        f"👨‍💼 *Исполнитель:* {request.get('assigned_admin', 'Не назначен')}\n"
        f"🕒 *Создана:* {request['created_at'][:16]}\n"
        f"💬 *Комментарий:* {request.get('admin_comment', 'Нет комментария')}"
    )

def format_detailed_request_text(request: Dict, comments: List[Dict]) -> str:
    """Форматирует детальный текст заявки с комментариями"""
    base_text = format_request_text(request)
    
    comments_text = "\n\n💬 *История комментариев:*\n"
    if comments:
        for comment in comments:
            comments_text += f"\n👤 {comment['admin_name']} ({comment['created_at'][:16]}):\n{comment['comment']}\n"
    else:
        comments_text += "\n📭 Комментариев пока нет"
    
    return base_text + comments_text

# ==================== ОБРАБОТЧИКИ INLINE КНОПОК ====================

async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатий на inline кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"Обработка inline кнопки: {data} пользователем {user_id}")
    
    if data.startswith('take_'):
        await take_request_inline(update, context)
    elif data.startswith('complete_'):
        await complete_request_inline(update, context)
    elif data.startswith('comment_'):
        await add_comment_inline(update, context)
    elif data.startswith('add_comment_'):
        await start_comment_input(update, context)
    elif data.startswith('details_'):
        await show_request_details(update, context)
    elif data.startswith('back_to_request_'):
        await show_request_with_actions(update, context)

async def take_request_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик взятия заявки в работу через inline кнопку"""
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        request_id = int(query.data.split('_')[1])
        request = db.get_request(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        # Проверяем права доступа
        if not Config.is_admin(user_id, request['department']):
            await query.edit_message_text("❌ У вас нет доступа к этой заявке")
            return
        
        # Проверяем статус
        if request['status'] != 'new':
            await query.edit_message_text("❌ Заявка уже взята в работу")
            return
        
        # Берем в работу
        admin_name = query.from_user.first_name
        db.update_request_status(
            request_id=request_id,
            status='in_progress',
            assigned_admin=admin_name
        )
        
        # Обновляем сообщение
        request_text = format_request_text(request)
        keyboard = create_request_actions_keyboard(request_id)
        
        await query.edit_message_text(
            f"🔄 *Взято в работу!*\n\n{request_text}",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Уведомляем пользователя
        await notify_user_about_request_status(
            update, context, request_id, 'in_progress', 
            assigned_admin=admin_name
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка взятия заявки: {e}")
        await query.edit_message_text("❌ Ошибка при взятии заявки")

async def complete_request_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик завершения заявки через inline кнопку"""
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        request_id = int(query.data.split('_')[1])
        request = db.get_request(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        # Проверяем права и статус
        if not Config.is_admin(user_id, request['department']):
            await query.edit_message_text("❌ У вас нет доступа к этой заявке")
            return
        
        if request['status'] != 'in_progress':
            await query.edit_message_text("❌ Заявка не в работе")
            return
        
        # Сохраняем ID заявки для комментария
        context.user_data['completing_request_id'] = request_id
        context.user_data['completing_message_id'] = query.message.message_id
        
        await query.edit_message_text(
            f"📝 *Завершение заявки #{request_id}*\n\n"
            "💬 *Введите комментарий к выполнению:*\n\n"
            "💡 Опишите что было сделано, какие детали заменены и т.д.",
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка завершения заявки: {e}")
        await query.edit_message_text("❌ Ошибка при завершении заявки")

async def add_comment_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик добавления комментария через inline кнопку"""
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        request_id = int(query.data.split('_')[1])
        request = db.get_request(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        # Проверяем права
        if not Config.is_admin(user_id, request['department']):
            await query.edit_message_text("❌ У вас нет доступа к этой заявке")
            return
        
        # Сохраняем ID заявки для комментария
        context.user_data['commenting_request_id'] = request_id
        context.user_data['commenting_message_id'] = query.message.message_id
        
        await query.edit_message_text(
            f"💬 *Добавление комментария к заявке #{request_id}*\n\n"
            "✍️ *Введите ваш комментарий:*\n\n"
            "💡 Комментарий будет виден всем администраторам отдела",
            reply_markup=create_comment_keyboard(request_id),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления комментария: {e}")
        await query.edit_message_text("❌ Ошибка при добавлении комментария")

async def start_comment_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начинает ввод комментария"""
    query = update.callback_query
    
    await query.edit_message_text(
        "✍️ *Введите ваш комментарий:*\n\n"
        "📝 Напишите сообщение и отправьте его как обычное текстовое сообщение",
        parse_mode=ParseMode.MARKDOWN
    )

async def show_request_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает детальную информацию о заявке"""
    query = update.callback_query
    
    try:
        request_id = int(query.data.split('_')[1])
        request = db.get_request(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        # Получаем комментарии
        comments = db.get_request_comments(request_id)
        
        details_text = format_detailed_request_text(request, comments)
        
        await query.edit_message_text(
            details_text,
            reply_markup=create_request_actions_keyboard(request_id),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка показа деталей заявки: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке деталей")

async def show_request_with_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает заявку с кнопками действий"""
    query = update.callback_query
    
    try:
        request_id = int(query.data.split('_')[2])  # back_to_request_123
        request = db.get_request(request_id)
        
        if not request:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        
        request_text = format_request_text(request)
        keyboard = create_request_actions_keyboard(request_id)
        
        await query.edit_message_text(
            request_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка возврата к заявке: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке заявки")

# ==================== ОБРАБОТКА КОММЕНТАРИЕВ ====================

async def handle_admin_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает комментарии администраторов"""
    user_id = update.message.from_user.id
    comment_text = update.message.text.strip()
    
    # Проверяем, есть ли активный процесс комментария
    commenting_request_id = context.user_data.get('commenting_request_id')
    completing_request_id = context.user_data.get('completing_request_id')
    
    if commenting_request_id:
        # Обработка обычного комментария
        await process_comment(update, context, commenting_request_id, user_id, comment_text, is_completion=False)
    
    elif completing_request_id:
        # Обработка комментария при завершении заявки
        await process_comment(update, context, completing_request_id, user_id, comment_text, is_completion=True)
    
    else:
        await update.message.reply_text(
            "❌ Нет активного процесса комментария. Используйте кнопки в заявке."
        )

async def process_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, admin_id: int, comment: str, is_completion: bool = False):
    """Обрабатывает комментарий администратора"""
    try:
        request = db.get_request(request_id)
        if not request:
            await update.message.reply_text("❌ Заявка не найдена")
            return
        
        admin_name = update.message.from_user.first_name
        
        if is_completion:
            # Завершаем заявку с комментарием
            db.update_request_status(
                request_id=request_id,
                status='completed',
                admin_comment=comment,
                assigned_admin=admin_name
            )
            
            # Добавляем в историю комментариев
            db.add_comment_to_request(request_id, admin_id, admin_name, f"✅ Завершено: {comment}")
            
            success_message = f"✅ *Заявка #{request_id} завершена!*\n\n💬 *Комментарий:* {comment}"
            
            # Уведомляем пользователя
            await notify_user_about_request_status(
                update, context, request_id, 'completed', 
                admin_comment=comment, assigned_admin=admin_name
            )
            
        else:
            # Добавляем обычный комментарий
            db.add_comment_to_request(request_id, admin_id, admin_name, comment)
            success_message = f"💬 *Комментарий добавлен к заявке #{request_id}*\n\n📝 *Текст:* {comment}"
            
            # Уведомляем других админов отдела
            await notify_admins_about_comment(update, context, request_id, admin_name, comment)
        
        # Очищаем данные контекста
        context.user_data.pop('commenting_request_id', None)
        context.user_data.pop('completing_request_id', None)
        context.user_data.pop('commenting_message_id', None)
        context.user_data.pop('completing_message_id', None)
        
        await update.message.reply_text(
            success_message,
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки комментария: {e}")
        await update.message.reply_text("❌ Ошибка при обработке комментария")

async def notify_admins_about_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, admin_name: str, comment: str):
    """Уведомляет админов отдела о новом комментарии"""
    try:
        request = db.get_request(request_id)
        if not request:
            return
        
        department = request['department']
        admin_ids = Config.get_admins_for_department(department)
        
        notification_text = (
            f"💬 *Новый комментарий к заявке #{request_id}*\n\n"
            f"🏢 *Отдел:* {department}\n"
            f"👤 *Администратор:* {admin_name}\n"
            f"📝 *Комментарий:* {comment}\n\n"
            f"🔧 *Тип проблемы:* {request['system_type']}\n"
            f"📍 *Участок:* {request['plot']}"
        )
        
        for admin_id in admin_ids:
            if admin_id != update.message.from_user.id:  # Не уведомляем автора комментария
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=notification_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки уведомления админу {admin_id}: {e}")
                    
    except Exception as e:
        logger.error(f"❌ Ошибка уведомления админов о комментарии: {e}")

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.message.from_user
    logger.info(f"Пользователь {user.id} запустил бота")
    
    welcome_text = (
        "👋 *Добро пожаловать в систему заявок!*\n\n"
        "🛠️ *Мы поможем с:*\n"
        "• 💻 IT проблемами\n"
        "• 🔧 Механическими неисправностями\n"
        "• ⚡ Электрическими вопросами\n\n"
        "🎯 *Выберите действие из меню ниже:*"
    )
    
    if Config.is_super_admin(user.id):
        keyboard = super_admin_main_menu_keyboard
    elif Config.is_admin(user.id):
        keyboard = admin_main_menu_keyboard
    else:
        keyboard = user_main_menu_keyboard
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает главное меню"""
    user = update.message.from_user
    user_id = user.id
    
    if Config.is_super_admin(user_id):
        keyboard = super_admin_main_menu_keyboard
        welcome_text = "👑 *Добро пожаловать, СУПЕР-АДМИНИСТРАТОР!*"
    elif Config.is_admin(user_id):
        keyboard = admin_main_menu_keyboard
        welcome_text = "👨‍💼 *Добро пожаловать, АДМИНИСТРАТОР!*"
    else:
        keyboard = user_main_menu_keyboard
        welcome_text = "💼 *Добро пожаловать в сервис заявок!*"
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== КЛАВИАТУРЫ ====================

# 🎯 Главное меню пользователя
user_main_menu_keyboard = [
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', 'ℹ️ Помощь'],
    ['🔙 Главное меню']
]

# 👑 Главное меню администратора
admin_main_menu_keyboard = [
    ['👑 Админ-панель', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', '🔄 Заявки в работе'],
    ['🔙 Главное меню']
]

# 👑 Главное меню супер-администратора
super_admin_main_menu_keyboard = [
    ['👑 Супер-админ', '📊 Статистика'],
    ['🎯 Создать заявку', '📂 Мои заявки'],
    ['🔍 Поиск заявки', '🔄 Заявки в работе'],
    ['🔙 Главное меню']
]

# 🏢 Админ-панели по отделам
it_admin_panel_keyboard = [
    ['🆕 Новые заявки IT', '🔄 В работе IT'],
    ['✅ Выполненные IT', '📊 Статистика IT'],
    ['🔙 Главное меню']
]

mechanics_admin_panel_keyboard = [
    ['🆕 Новые заявки механики', '🔄 В работе механики'],
    ['✅ Выполненные механики', '📊 Статистика механики'],
    ['🔙 Главное меню']
]

electricity_admin_panel_keyboard = [
    ['🆕 Новые заявки электрики', '🔄 В работе электрики'],
    ['✅ Выполненные электрики', '📊 Статистика электрики'],
    ['🔙 Главное меню']
]

# 🏢 Выбор отдела
department_keyboard = [
    ['💻 IT отдел', '🔧 Механика'],
    ['⚡ Электрика', '🔙 Назад в меню']
]

# ==================== ПОКАЗ ЗАЯВОК С INLINE КНОПКАМИ ====================

async def show_new_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает новые заявки отдела с inline кнопками"""
    user_id = update.message.from_user.id
    text = update.message.text
    
    department_map = {
        '🆕 Новые заявки IT': '💻 IT отдел',
        '🆕 Новые заявки механики': '🔧 Механика',
        '🆕 Новые заявки электрики': '⚡ Электрика'
    }
    
    if text not in department_map:
        return
    
    department = department_map[text]
    
    if not Config.is_admin(user_id, department):
        await update.message.reply_text(f"❌ У вас нет доступа к заявкам {department}")
        return
    
    requests = db.get_requests_by_filter(department=department, status='new', limit=10)
    
    if not requests:
        await update.message.reply_text(
            f"📭 *Новых заявок в {department} нет*",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    await update.message.reply_text(
        f"🆕 *Новые заявки {department}: {len(requests)}*\n\n"
        "Используйте кнопки ниже для управления заявками:",
        parse_mode=ParseMode.MARKDOWN
    )
    
    for request in requests:
        request_text = format_request_text(request)
        keyboard = create_request_actions_keyboard(request['id'])
        
        if request.get('photo'):
            try:
                with open(request['photo'], 'rb') as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=request_text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.MARKDOWN
                    )
            except:
                await update.message.reply_text(
                    request_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            await update.message.reply_text(
                request_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )

async def show_requests_in_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает заявки в работе с inline кнопками"""
    user_id = update.message.from_user.id
    
    if not Config.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой функции.")
        return
    
    # Для обычных админов показываем только заявки их отделов
    if Config.is_super_admin(user_id):
        requests = db.get_requests_by_filter(status='in_progress', limit=20)
    else:
        # Определяем отделы, к которым есть доступ
        user_departments = []
        for department, admins in Config.ADMIN_CHAT_IDS.items():
            if user_id in admins:
                user_departments.append(department)
        
        requests = []
        for department in user_departments:
            dept_requests = db.get_requests_by_filter(
                department=department, 
                status='in_progress', 
                limit=10
            )
            requests.extend(dept_requests)
    
    if not requests:
        await update.message.reply_text(
            "📭 *Заявок в работе нет*\n\n"
            "Все заявки обработаны! 🎉",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    await update.message.reply_text(
        f"🔄 *Заявки в работе: {len(requests)}*\n\n"
        "Используйте кнопки для управления заявками:",
        parse_mode=ParseMode.MARKDOWN
    )
    
    for request in requests[:10]:
        request_text = format_request_text(request)
        keyboard = create_request_actions_keyboard(request['id'])
        
        await update.message.reply_text(
            request_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== УВЕДОМЛЕНИЯ ====================

async def notify_user_about_request_status(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int, status: str, admin_comment: str = None, assigned_admin: str = None):
    """Уведомляет пользователя об изменении статуса заявки"""
    try:
        request = db.get_request(request_id)
        if not request:
            return
        
        user_id = request['user_id']
        
        if status == 'in_progress':
            message_text = (
                f"🔄 *Ваша заявка взята в работу!*\n\n"
                f"📋 *Заявка #{request_id}*\n"
                f"🏢 *Отдел:* {request['department']}\n"
                f"🔧 *Тип:* {request['system_type']}\n"
                f"👨‍💼 *Исполнитель:* {assigned_admin or 'Администратор'}\n"
                f"💬 *Комментарий:* {admin_comment or 'Без комментария'}\n\n"
                f"_Заявка будет выполнена в течение 48 часов_"
            )
        elif status == 'completed':
            message_text = (
                f"✅ *Ваша заявка выполнена!*\n\n"
                f"📋 *Заявка #{request_id}*\n"
                f"🏢 *Отдел:* {request['department']}\n"
                f"🔧 *Тип:* {request['system_type']}\n"
                f"👨‍💼 *Исполнитель:* {assigned_admin or 'Администратор'}\n"
                f"💬 *Комментарий:* {admin_comment or 'Без комментария'}\n\n"
                f"_Спасибо за обращение!_ 💼"
            )
        else:
            return
        
        await context.bot.send_message(
            chat_id=user_id,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"✅ Уведомление отправлено пользователю {user_id} о заявке #{request_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления пользователю о заявке #{request_id}: {e}")

# ==================== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ====================

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений для меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Обработка основных кнопок меню
    if text == '🎯 Создать заявку':
        await start_request_creation(update, context)
    elif text == '📂 Мои заявки':
        await show_my_requests(update, context)
    elif text == '🔍 Поиск заявки':
        await search_requests_menu(update, context)
    elif text == '📊 Статистика':
        await show_user_statistics(update, context)
    elif text == '🔄 Заявки в работе':
        await show_requests_in_progress(update, context)
    elif text == 'ℹ️ Помощь':
        await show_help(update, context)
    elif text == '👑 Админ-панель':
        await show_admin_panel(update, context)
    elif text == '👑 Супер-админ':
        await show_super_admin_panel(update, context)
    elif text == '🔙 Главное меню':
        await show_main_menu(update, context)
    
    # Обработка кнопок новых заявок
    elif text in ['🆕 Новые заявки IT', '🆕 Новые заявки механики', '🆕 Новые заявки электрики']:
        await show_new_requests(update, context)
    
    else:
        await update.message.reply_text(
            "Используйте кнопки меню для навигации",
            reply_markup=ReplyKeyboardMarkup(
                super_admin_main_menu_keyboard if Config.is_super_admin(user_id) else
                admin_main_menu_keyboard if Config.is_admin(user_id) else
                user_main_menu_keyboard, 
                resize_keyboard=True
            )
        )

# ==================== ЗАПУСК БОТА ====================

def main() -> None:
    """Запускаем бота"""
    try:
        if not Config.BOT_TOKEN:
            logger.error("❌ Токен бота не загружен!")
            return
        
        application = Application.builder().token(Config.BOT_TOKEN).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("menu", show_main_menu))
        application.add_handler(CommandHandler("help", show_help))
        
        # Обработчик inline кнопок
        application.add_handler(CallbackQueryHandler(handle_inline_buttons))
        
        # Обработчик комментариев админов
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_admin_comment
        ))
        
        # Обработчики текстовых сообщений для меню
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
        
        logger.info("🤖 Бот заявок успешно запущен!")
        print("✅ Бот успешно запущен!")
        print("🎯 Новые возможности:")
        print("   • 🔘 Визуальные кнопки для управления заявками") 
        print("   • 💬 Система комментариев для админов")
        print("   • ✅ Улучшенный интерфейс управления")
        
        application.run_polling()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
        print(f"❌ Критическая ошибка: {e}")

if __name__ == '__main__':
    main()
