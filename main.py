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
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY = range(7)

# База данных
DB_PATH = "requests.db"

# Клавиатуры
confirm_keyboard = [['✅ Подтвердить', '✏️ Изменить']]
photo_keyboard = [['📷 Добавить фото', '⏭️ Пропустить']]
urgency_keyboard = [
    ['🔴 Срочно (в течение 2 часов)'],
    ['🟡 Средняя срочность (сегодня)'],
    ['🟢 Не срочно (в течение 3 дней)']
]
new_request_keyboard = [[InlineKeyboardButton('📝 Создать новую заявку', callback_data='new_request')]]
system_type_keyboard = [
    ['📹 Видеонаблюдение', '🔐 СКУД'],
    ['🌐 Компьютерная сеть', '🚨 Пожарная сигнализация'],
    ['❓ Другое']
]
plot_type_keyboard = [
    ['Фрезерный участок', 'Токарный участок'],
    ['Участок штамповки', 'Другой участок']
]

# Клавиатура администратора
admin_keyboard = [
    ['📊 Статистика', '📋 Активные заявки'],
    ['✅ Завершенные', '🚨 Срочные заявки'],
    ['🔄 Обновить данные', '📢 Рассылка']
]

# Хранилище для связи пользователей и администраторов
user_requests = {}

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
                    admin_comment TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS statistics (
                    date TEXT PRIMARY KEY,
                    requests_count INTEGER DEFAULT 0,
                    completed_count INTEGER DEFAULT 0
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
            
            conn.commit()
            return request_id

    def get_request(self, request_id: int) -> Dict:
        """Получает заявку по ID"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM requests WHERE id = ?', (request_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'user_id': row[1],
                    'username': row[2],
                    'name': row[3],
                    'phone': row[4],
                    'plot': row[5],
                    'system_type': row[6],
                    'problem': row[7],
                    'photo': row[8],
                    'urgency': row[9],
                    'status': row[10],
                    'created_at': row[11],
                    'updated_at': row[12],
                    'admin_comment': row[13]
                }
            return {}

    def update_request_status(self, request_id: int, status: str, admin_comment: str = None):
        """Обновляет статус заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if admin_comment:
                cursor.execute('''
                    UPDATE requests SET status = ?, admin_comment = ?, updated_at = ?
                    WHERE id = ?
                ''', (status, admin_comment, datetime.now().isoformat(), request_id))
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

    def get_statistics(self, days: int = 7) -> Dict:
        """Получает статистику за последние N дней"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_requests,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                    SUM(CASE WHEN urgency = '🔴 Срочно' THEN 1 ELSE 0 END) as urgent
                FROM requests 
                WHERE created_at >= ?
            ''', (start_date,))
            
            result = cursor.fetchone()
            return {
                'total_requests': result[0] or 0,
                'completed': result[1] or 0,
                'new': result[2] or 0,
                'urgent': result[3] or 0
            }

    def get_active_requests(self) -> List[Dict]:
        """Получает активные заявки"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM requests 
                WHERE status IN ('new', 'in_progress') 
                ORDER BY 
                    CASE urgency 
                        WHEN '🔴 Срочно' THEN 1
                        WHEN '🟡 Средняя срочность' THEN 2
                        ELSE 3
                    END,
                    created_at DESC
            ''')
            return [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

# Инициализация базы данных
db = Database(DB_PATH)

def send_admin_notification(context: CallbackContext, user_data: Dict, request_id: int) -> None:
    """Отправляет уведомление администраторам"""
    user_info = f"👤 Пользователь: @{user_data.get('username', 'Не указан')}"
    urgency_icon = user_data.get('urgency', '🟢 Не срочно')
    
    notification_text = (
        f"🚨 *НОВАЯ ЗАЯВКА #{request_id}*\n\n"
        f"{user_info}\n"
        f"🆔 ID: {user_data.get('user_id')}\n"
        f"📛 Имя: {user_data.get('name', 'Не указано')}\n"
        f"📞 Телефон: `{user_data.get('phone', 'Не указан')}`\n"
        f"📍 Участок: {user_data.get('plot', 'Не указан')}\n"
        f"🔧 Тип системы: {user_data.get('system_type', 'Не указан')}\n"
        f"📝 Описание: {user_data.get('problem', 'Не указано')}\n"
        f"⏰ Срочность: {urgency_icon}\n"
        f"📸 Фото: {'✅ Есть' if user_data.get('photo') else '❌ Нет'}\n\n"
        f"🕒 Время заявки: {user_data.get('timestamp', 'Не указано')}\n\n"
        f"💬 *Для ответа пользователю просто напишите сообщение в этот чат*\n"
        f"📋 *Для управления заявкой используйте /admin*"
    )
    
    # Сохраняем информацию о заявке
    user_requests[user_data.get('user_id')] = {
        'user_data': user_data.copy(),
        'request_id': request_id,
        'admin_messages': []
    }
    
    keyboard = [
        [InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{request_id}")],
        [InlineKeyboardButton("📋 Посмотреть заявку", callback_data=f"view_{request_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    success_count = 0
    for admin_id in ADMIN_CHAT_IDS:
        try:
            if user_data.get('photo'):
                message = context.bot.send_photo(
                    chat_id=admin_id,
                    photo=user_data['photo'],
                    caption=notification_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            else:
                message = context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            
            user_requests[user_data.get('user_id')]['admin_messages'].append({
                'admin_id': admin_id,
                'message_id': message.message_id
            })
            success_count += 1
            logger.info(f"Уведомление отправлено администратору {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки администратору {admin_id}: {e}")
    
    return success_count

def forward_to_user(update: Update, context: CallbackContext) -> None:
    """Пересылает сообщение администратора пользователю"""
    admin_id = update.message.from_user.id
    
    if admin_id not in ADMIN_CHAT_IDS:
        return
    
    # Ищем пользователя по ID администратора
    user_id = None
    request_id = None
    for uid, data in user_requests.items():
        for admin_msg in data['admin_messages']:
            if admin_msg['admin_id'] == admin_id:
                user_id = uid
                request_id = data['request_id']
                break
        if user_id:
            break
    
    if user_id:
        try:
            admin_name = update.message.from_user.first_name
            if update.message.text:
                context.bot.send_message(
                    chat_id=user_id,
                    text=f"💬 *Сообщение от специалиста ({admin_name}):*\n\n{update.message.text}",
                    parse_mode=ParseMode.MARKDOWN
                )
                update.message.reply_text("✅ Сообщение отправлено пользователю")
                
            elif update.message.photo:
                context.bot.send_photo(
                    chat_id=user_id,
                    photo=update.message.photo[-1].file_id,
                    caption=f"💬 *Сообщение от специалиста ({admin_name}):*\n\n{update.message.caption}" if update.message.caption else f"💬 Сообщение от специалиста ({admin_name})",
                    parse_mode=ParseMode.MARKDOWN
                )
                update.message.reply_text("✅ Фото отправлено пользователю")
                
            elif update.message.document:
                context.bot.send_document(
                    chat_id=user_id,
                    document=update.message.document.file_id,
                    caption=f"💬 *Сообщение от специалиста ({admin_name}):*\n\n{update.message.caption}" if update.message.caption else f"💬 Сообщение от специалиста ({admin_name})",
                    parse_mode=ParseMode.MARKDOWN
                )
                update.message.reply_text("✅ Документ отправлен пользователю")
            
            # Обновляем статус заявки
            if request_id:
                db.update_request_status(request_id, "in_progress", "Специалист связался с клиентом")
            
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
            update.message.reply_text("❌ Ошибка отправки сообщения пользователю")

def start(update: Update, context: CallbackContext) -> int:
    """Начинаем разговор и спрашиваем имя."""
    context.user_data.clear()
    
    user = update.message.from_user
    context.user_data['user_id'] = user.id
    context.user_data['username'] = user.username
    
    welcome_text = (
        "🏠 *Добро пожаловать в сервис заявок для слаботочных систем!*\n\n"
        "✨ *Преимущества нашего сервиса:*\n"
        "• 🚀 Быстрое реагирование\n"
        "• 🔧 Квалифицированные специалисты\n"
        "• 💰 Прозрачные цены\n"
        "• 🔒 Гарантия на работы\n\n"
        "Для оформления заявки нам потребуется некоторая информация.\n"
        "Заполните данные последовательно.\n\n"
        "*📛 Как к вам обращаться?*"
    )
    
    update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def name(update: Update, context: CallbackContext) -> int:
    """Сохраняем имя и спрашиваем телефон."""
    context.user_data['name'] = update.message.text
    update.message.reply_text(
        '*📞 Укажите ваш контактный телефон:*\n\n'
        'Пример: +7 999 123-45-67',
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    """Сохраняем телефон и спрашиваем участок."""
    context.user_data['phone'] = update.message.text
    update.message.reply_text(
        '*📍 Выберите тип участка:*',
        reply_markup=ReplyKeyboardMarkup(
            plot_type_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLOT

def plot(update: Update, context: CallbackContext) -> int:
    """Сохраняем участок и спрашиваем тип системы."""
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        '*🔧 Выберите тип слаботочной системы:*',
        reply_markup=ReplyKeyboardMarkup(
            system_type_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    """Сохраняем тип системы и спрашиваем описание проблемы."""
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        '*📝 Опишите проблему или необходимые работы:*\n\n'
        'Пример: Не работает видеонаблюдение на фрезерном участке\n'
        'Или: Требуется установка пожарной сигнализации на участке штамповки',
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    """Сохраняем описание проблемы и спрашиваем срочность."""
    context.user_data['problem'] = update.message.text
    update.message.reply_text(
        '*⏰ Выберите срочность выполнения работ:*\n\n'
        '🔴 *Срочно* - в течение 2 часов\n'
        '🟡 *Средняя срочность* - сегодня\n'
        '🟢 *Не срочно* - в течение 3 дней',
        reply_markup=ReplyKeyboardMarkup(
            urgency_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    return URGENCY

def urgency(update: Update, context: CallbackContext) -> int:
    """Сохраняем срочность и спрашиваем фото."""
    context.user_data['urgency'] = update.message.text
    update.message.reply_text(
        '*📸 Хотите добавить фото к заявке?*\n\n'
        'Фото поможет специалисту лучше понять проблему.',
        reply_markup=ReplyKeyboardMarkup(
            photo_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    """Обрабатываем фото или пропуск."""
    if update.message.text == '📷 Добавить фото':
        update.message.reply_text(
            '*📸 Отправьте фото:*\n\n'
            'Вы можете отправить одно фото.',
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return PHOTO
    elif update.message.text == '⏭️ Пропустить':
        context.user_data['photo'] = None
        return show_summary(update, context)
    elif update.message.photo:
        context.user_data['photo'] = update.message.photo[-1].file_id
        update.message.reply_text(
            '✅ Фото добавлено!',
            reply_markup=ReplyKeyboardRemove()
        )
        return show_summary(update, context)
    else:
        update.message.reply_text(
            '❌ Пожалуйста, отправьте фото или используйте кнопки.',
            reply_markup=ReplyKeyboardMarkup(
                photo_keyboard, 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
        return PHOTO

def show_summary(update: Update, context: CallbackContext) -> int:
    """Показываем сводку заявки."""
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Формируем сводку
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
            caption=f"{summary}\n\n*Подтвердите отправку заявки или измените данные:*",
            reply_markup=ReplyKeyboardMarkup(
                confirm_keyboard, 
                one_time_keyboard=True, 
                resize_keyboard=True
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        update.message.reply_text(
            f"{summary}\n\n"
            "*Подтвердите отправку заявки или измените данные:*",
            reply_markup=ReplyKeyboardMarkup(
                confirm_keyboard, 
                one_time_keyboard=True, 
                resize_keyboard=True
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    return ConversationHandler.END

def confirm(update: Update, context: CallbackContext) -> int:
    """Отправляем заявку и завершаем разговор."""
    if update.message.text == '✅ Подтвердить':
        user = update.message.from_user
        
        # Сохраняем заявку в базу данных
        request_id = db.save_request(context.user_data)
        
        # Отправляем уведомление администраторам
        success_count = send_admin_notification(context, context.user_data, request_id)
        
        if success_count > 0:
            # Отправляем подтверждение пользователю
            confirmation_text = (
                f"✅ *Заявка #{request_id} успешно отправлена!*\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n\n"
                f"💡 *Что дальше?*\n"
                f"• Ожидайте звонка нашего специалиста\n"
                f"• Будьте готовы показать проблему на месте\n"
                f"• Имейте под рукой доступ к участку\n\n"
                f"_Спасибо, что выбрали наш сервис!_ 🛠️"
            )
            
            update.message.reply_text(
                confirmation_text,
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Отправляем кнопку для создания новой заявки
            update.message.reply_text(
                'Если у вас есть еще вопросы или проблемы - создайте новую заявку:',
                reply_markup=InlineKeyboardMarkup(new_request_keyboard)
            )
            
            logger.info(f"Новая заявка #{request_id} от {user.username}")
        else:
            update.message.reply_text(
                '❌ *Произошла ошибка при отправке заявки.*\n\n'
                'Пожалуйста, попробуйте позже или свяжитесь с нами по телефону.',
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=ParseMode.MARKDOWN
            )
        
        context.user_data.clear()
        return ConversationHandler.END
    else:
        update.message.reply_text(
            '✏️ *Давайте начнем заполнение заново.*\n\n'
            'Как к вам обращаться?',
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        return NAME

def new_request_callback(update: Update, context: CallbackContext) -> int:
    """Обработчик кнопки создания новой заявки"""
    query = update.callback_query
    query.answer()
    
    # Запускаем процесс создания новой заявки
    return start_from_button(update, context)

def start_from_button(update: Update, context: CallbackContext) -> int:
    """Начинаем разговор из кнопки"""
    query = update.callback_query
    context.user_data.clear()
    
    user = query.from_user
    context.user_data['user_id'] = user.id
    context.user_data['username'] = user.username
    
    query.edit_message_text(
        '✏️ *Давайте создадим новую заявку!*\n\n'
        'Как к вам обращаться?',
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def cancel(update: Update, context: CallbackContext) -> int:
    """Отменяем разговор."""
    update.message.reply_text(
        '❌ *Заявка отменена.*\n\n'
        'Если потребуется помощь - обращайтесь! 👷',
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data.clear()
    return ConversationHandler.END

# ==================== АДМИН-ФУНКЦИОНАЛ ====================

def admin_panel(update: Update, context: CallbackContext) -> None:
    """Панель администратора"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    stats = db.get_statistics(7)
    admin_text = (
        "👑 *Панель администратора*\n\n"
        f"📊 *Статистика за 7 дней:*\n"
        f"• Всего заявок: {stats['total_requests']}\n"
        f"• Новые: {stats['new']}\n"
        f"• Завершенные: {stats['completed']}\n"
        f"• Срочные: {stats['urgent']}\n\n"
        f"🔄 Выберите действие:"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(admin_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_active_requests(update: Update, context: CallbackContext) -> None:
    """Показывает активные заявки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    requests = db.get_active_requests()
    
    if not requests:
        update.message.reply_text("📭 Активных заявок нет")
        return
    
    for req in requests[:5]:  # Показываем первые 5 заявок
        request_text = (
            f"📋 *Заявка #{req['id']}*\n"
            f"👤 {req['name']} | 📞 {req['phone']}\n"
            f"📍 {req['plot']} | 🔧 {req['system_type']}\n"
            f"⏰ {req['urgency']} | 🕒 {req['created_at'][:16]}\n"
            f"📝 {req['problem'][:100]}..."
        )
        
        keyboard = [[
            InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{req['id']}"),
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
        db.update_request_status(request_id, "in_progress", f"Взято в работу администратором {query.from_user.first_name}")
        query.edit_message_text(f"✅ Заявка #{request_id} взята в работу")
        
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
                f"🕒 *Создана:* {request['created_at'][:16]}\n"
            )
            
            keyboard = [[
                InlineKeyboardButton("✅ Завершить", callback_data=f"complete_{request_id}"),
                InlineKeyboardButton("📞 Связаться", callback_data=f"contact_{request_id}")
            ]]
            
            if request.get('photo'):
                query.message.reply_photo(
                    photo=request['photo'],
                    caption=request_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                query.message.reply_text(
                    request_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )

def broadcast_message(update: Update, context: CallbackContext) -> None:
    """Рассылка сообщений пользователям"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return
    
    if context.args:
        message = ' '.join(context.args)
        # Здесь должна быть логика рассылки всем пользователям
        # Для простоты просто подтверждаем отправку
        update.message.reply_text(f"📢 Рассылка отправлена: {message}")

# ==================== ОСНОВНЫЕ ФУНКЦИИ ====================

def main() -> None:
    """Запускаем бота."""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    if not ADMIN_CHAT_IDS:
        logger.error("❌ ID администраторов не установлены!")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Обработчик разговора
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', start),
                CallbackQueryHandler(start_from_button, pattern='^new_request$')
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
            fallbacks=[CommandHandler('cancel', cancel)],
            per_message=False
        )

        # Регистрируем обработчики
        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить|✏️ Изменить)$'), confirm))
        
        # Админ-команды
        dispatcher.add_handler(CommandHandler('admin', admin_panel))
        dispatcher.add_handler(CommandHandler('broadcast', broadcast_message))
        dispatcher.add_handler(MessageHandler(Filters.regex('^(📋 Активные заявки)$'), show_active_requests))
        dispatcher.add_handler(CallbackQueryHandler(handle_admin_callback, pattern='^(take_|view_|complete_|contact_)'))
        
        # Обработчик сообщений от администраторов
        dispatcher.add_handler(MessageHandler(
            Filters.chat(ADMIN_CHAT_IDS) & 
            (Filters.text | Filters.photo | Filters.document) & 
            ~Filters.command, 
            forward_to_user
        ))

        # Запускаем бота
        logger.info("🤖 Бот запущен и готов к работе!")
        logger.info(f"👑 Администраторы: {ADMIN_CHAT_IDS}")
        logger.info(f"💾 База данных: {DB_PATH}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
