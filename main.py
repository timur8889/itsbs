import logging
import sqlite3
import os
import json
import re
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ParseMode,
    InputFile,
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

BOT_TOKEN = os.getenv('BOT_TOKEN', "7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw")
ADMIN_CHAT_IDS = [int(x) for x in os.getenv('ADMIN_CHAT_IDS', '5024165375').split(',')]

# Расширенные настройки
MAX_REQUESTS_PER_HOUR = 15
BACKUP_RETENTION_DAYS = 30
AUTO_BACKUP_HOUR = 3
AUTO_BACKUP_MINUTE = 0

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Этапы разговора (сохраняем старые + добавляем новые)
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO, URGENCY, EDIT_CHOICE, EDIT_FIELD = range(9)

DB_PATH = "requests.db"
BACKUP_DIR = "backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

# ==================== НОВЫЕ УТИЛИТЫ ====================

class AdvancedValidators(Validators):
    """Расширенный класс валидации с сохранением старых методов"""
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Валидация email адреса"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email.strip()))
    
    @staticmethod
    def validate_plot_number(plot: str) -> bool:
        """Валидация номера участка"""
        return bool(re.match(r'^[А-Яа-яA-Za-z0-9\s\-]{2,20}$', plot.strip()))
    
    @staticmethod
    def sanitize_text(text: str) -> str:
        """Очистка текста от потенциально опасных символов"""
        return re.sub(r'[<>&\"\']', '', text.strip())

class NotificationManager:
    """Менеджер уведомлений с расширенными функциями"""
    
    def __init__(self, bot):
        self.bot = bot
        self.notification_queue = []
        self.lock = threading.Lock()
    
    def add_notification(self, chat_id: int, text: str, photo: str = None, 
                        keyboard: List[List[str]] = None, priority: int = 1):
        """Добавляет уведомление в очередь"""
        with self.lock:
            self.notification_queue.append({
                'chat_id': chat_id,
                'text': text,
                'photo': photo,
                'keyboard': keyboard,
                'priority': priority,
                'timestamp': datetime.now()
            })
            # Сортируем по приоритету
            self.notification_queue.sort(key=lambda x: x['priority'])
    
    def send_priority_notification(self, chat_ids: List[int], text: str, 
                                 parse_mode: str = ParseMode.MARKDOWN):
        """Отправляет приоритетное уведомление нескольким пользователям"""
        for chat_id in chat_ids:
            try:
                self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode
                )
                logger.info(f"Приоритетное уведомление отправлено {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки приоритетного уведомления {chat_id}: {e}")
    
    def process_queue(self):
        """Обрабатывает очередь уведомлений (вызывается периодически)"""
        with self.lock:
            for notification in self.notification_queue[:10]:  # Обрабатываем первые 10
                try:
                    if notification['photo']:
                        self.bot.send_photo(
                            chat_id=notification['chat_id'],
                            photo=notification['photo'],
                            caption=notification['text'],
                            reply_markup=ReplyKeyboardMarkup(
                                notification['keyboard'], 
                                resize_keyboard=True
                            ) if notification['keyboard'] else None,
                            parse_mode=ParseMode.MARKDOWN
                        )
                    else:
                        self.bot.send_message(
                            chat_id=notification['chat_id'],
                            text=notification['text'],
                            reply_markup=ReplyKeyboardMarkup(
                                notification['keyboard'], 
                                resize_keyboard=True
                            ) if notification['keyboard'] else None,
                            parse_mode=ParseMode.MARKDOWN
                        )
                    self.notification_queue.remove(notification)
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}")
                    # Удаляем проблемное уведомление после 3 попыток
                    if notification.get('attempts', 0) >= 3:
                        self.notification_queue.remove(notification)
                    else:
                        notification['attempts'] = notification.get('attempts', 0) + 1

class EnhancedBackupManager(BackupManager):
    """Расширенный менеджер бэкапов"""
    
    @staticmethod
    def create_encrypted_backup(password: str = None):
        """Создает зашифрованный бэкап (базовая реализация)"""
        backup_path = BackupManager.create_backup()
        if backup_path and password:
            # Здесь может быть реализация шифрования
            logger.info(f"Бэкап создан: {backup_path} (шифрование отключено)")
        return backup_path
    
    @staticmethod
    def get_backup_info():
        """Возвращает информацию о бэкапах"""
        try:
            backups = []
            for f in os.listdir(BACKUP_DIR):
                if f.startswith('backup_') and f.endswith('.db'):
                    path = os.path.join(BACKUP_DIR, f)
                    stats = os.stat(path)
                    backups.append({
                        'name': f,
                        'size': stats.st_size,
                        'created': datetime.fromtimestamp(stats.st_ctime),
                        'path': path
                    })
            
            # Сортируем по дате создания
            backups.sort(key=lambda x: x['created'], reverse=True)
            return backups
        except Exception as e:
            logger.error(f"Ошибка получения информации о бэкапах: {e}")
            return []
    
    @staticmethod
    def cleanup_old_backups():
        """Удаляет старые бэкапы"""
        try:
            cutoff_date = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
            backups = EnhancedBackupManager.get_backup_info()
            
            deleted_count = 0
            for backup in backups:
                if backup['created'] < cutoff_date:
                    os.remove(backup['path'])
                    deleted_count += 1
                    logger.info(f"Удален старый бэкап: {backup['name']}")
            
            return deleted_count
        except Exception as e:
            logger.error(f"Ошибка очистки бэкапов: {e}")
            return 0

# ==================== РАСШИРЕННАЯ БАЗА ДАННЫХ ====================

class EnhancedDatabase(Database):
    """Расширенная база данных с новыми функциями"""
    
    def init_db(self):
        """Инициализация с дополнительными таблицами"""
        super().init_db()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Таблица для уведомлений
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    message TEXT,
                    sent_at TEXT,
                    is_read INTEGER DEFAULT 0
                )
            ''')
            
            # Таблица настроек
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # Таблица для истории изменений заявок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS request_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER,
                    action TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    changed_by TEXT,
                    changed_at TEXT,
                    FOREIGN KEY (request_id) REFERENCES requests (id)
                )
            ''')
            
            conn.commit()
    
    def log_request_change(self, request_id: int, action: str, old_value: str, 
                          new_value: str, changed_by: str):
        """Логирует изменения заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO request_history 
                    (request_id, action, old_value, new_value, changed_by, changed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (request_id, action, old_value, new_value, changed_by, 
                      datetime.now().isoformat()))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Ошибка логирования изменения заявки: {e}")
    
    def get_request_history(self, request_id: int) -> List[Dict]:
        """Получает историю изменений заявки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM request_history 
                    WHERE request_id = ? 
                    ORDER BY changed_at DESC
                ''', (request_id,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения истории заявки: {e}")
            return []
    
    def get_urgent_requests(self, hours: int = 2) -> List[Dict]:
        """Получает срочные заявки с истекающим сроком"""
        try:
            time_threshold = (datetime.now() - timedelta(hours=hours)).isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM requests 
                    WHERE urgency LIKE '%Срочно%' 
                    AND status IN ('new', 'in_progress')
                    AND created_at > ?
                    ORDER BY created_at ASC
                ''', (time_threshold,))
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения срочных заявок: {e}")
            return []
    
    def get_user_statistics(self, user_id: int) -> Dict:
        """Получает расширенную статистику пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Основная статистика
                cursor.execute('''
                    SELECT 
                        COUNT(*) as total_requests,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                        SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                        MIN(created_at) as first_request,
                        MAX(created_at) as last_request
                    FROM requests 
                    WHERE user_id = ?
                ''', (user_id,))
                
                stats = cursor.fetchone()
                if stats:
                    columns = [column[0] for column in cursor.description]
                    result = dict(zip(columns, stats))
                    
                    # Среднее время выполнения
                    cursor.execute('''
                        SELECT AVG(
                            (julianday(updated_at) - julianday(created_at)) * 24
                        ) as avg_hours
                        FROM requests 
                        WHERE user_id = ? AND status = 'completed'
                    ''', (user_id,))
                    
                    avg_hours = cursor.fetchone()[0]
                    result['avg_completion_hours'] = round(avg_hours, 2) if avg_hours else 0
                    
                    return result
                return {}
        except sqlite3.Error as e:
            logger.error(f"Ошибка получения статистики пользователя: {e}")
            return {}

# ==================== НОВЫЕ КЛАВИАТУРЫ ====================

# Расширенное главное меню пользователя
enhanced_user_main_menu_keyboard = [
    ['📝 Создать заявку', '📋 Мои заявки'],
    ['📊 Моя статистика', '🆘 Срочная помощь']
]

# Расширенное админ-меню
enhanced_admin_main_menu_keyboard = [
    ['🆕 Новые заявки', '🔄 В работе'],
    ['⏰ Срочные заявки', '📊 Статистика'],
    ['👥 Пользователи', '⚙️ Настройки'],
    ['💾 Бэкапы', '🔄 Обновить']
]

# Меню настроек
settings_keyboard = [
    ['📊 Общая статистика', '🔔 Уведомления'],
    ['🔄 Авто-обновление', '💾 Управление бэкапами'],
    ['🔙 Назад в админ-панель']
]

# Меню бэкапов
backup_keyboard = [
    ['💾 Создать бэкап', '📋 Список бэкапов'],
    ['🧹 Очистить старые', '🔙 Назад']
]

# ==================== РАСШИРЕННЫЕ ФУНКЦИИ СОЗДАНИЯ ЗАЯВКИ ====================

def enhanced_start_request_creation(update: Update, context: CallbackContext) -> int:
    """Улучшенное начало создания заявки с проверкой лимитов и статистики"""
    user_id = update.message.from_user.id
    
    # Проверка расширенного лимита
    if rate_limiter.is_limited(user_id, 'create_request', MAX_REQUESTS_PER_HOUR):
        user_stats = db.get_user_statistics(user_id)
        
        update.message.reply_text(
            "❌ *Превышен лимит запросов!*\n\n"
            f"📊 *Ваша статистика:*\n"
            f"• Всего заявок: {user_stats.get('total_requests', 0)}\n"
            f"• Выполнено: {user_stats.get('completed', 0)}\n"
            f"• В работе: {user_stats.get('in_progress', 0)}\n\n"
            "Вы можете создавать не более 15 заявок в час.\n"
            "Пожалуйста, попробуйте позже.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END
    
    context.user_data.clear()
    
    user = update.message.from_user
    context.user_data.update({
        'user_id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'creation_started': datetime.now().isoformat()
    })
    
    # Показываем статистику пользователя
    user_stats = db.get_user_statistics(user_id)
    if user_stats.get('total_requests', 0) > 0:
        stats_text = (
            f"📊 *Ваша статистика:*\n"
            f"• Всего заявок: {user_stats['total_requests']}\n"
            f"• Выполнено: {user_stats['completed']}\n"
            f"• Среднее время выполнения: {user_stats.get('avg_completion_hours', 0)} ч.\n\n"
        )
    else:
        stats_text = "🎉 *Это ваша первая заявка!*\n\n"
    
    update.message.reply_text(
        f"{stats_text}"
        "📝 *Создание новой заявки*\n\n"
        "Для начала укажите ваше имя:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return NAME

def enhanced_confirm_request(update: Update, context: CallbackContext) -> None:
    """Улучшенное подтверждение заявки с дополнительными проверками"""
    if update.message.text == '✅ Подтвердить отправку':
        user = update.message.from_user
        
        try:
            # Дополнительная проверка данных
            required_fields = ['name', 'phone', 'plot', 'system_type', 'problem', 'urgency']
            for field in required_fields:
                if field not in context.user_data or not context.user_data[field]:
                    update.message.reply_text(
                        f"❌ Отсутствует обязательное поле: {field}",
                        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
                    )
                    return
            
            # Сохраняем заявку
            request_id = db.save_request(context.user_data)
            
            # Логируем создание
            db.log_request_change(
                request_id=request_id,
                action='created',
                old_value='',
                new_value='new',
                changed_by=f"user_{user.id}"
            )
            
            # Отправляем уведомления
            enhanced_send_admin_notification(context, context.user_data, request_id)
            
            # Расширенное подтверждение пользователю
            user_stats = db.get_user_statistics(user.id)
            
            confirmation_text = (
                f"✅ *Заявка #{request_id} успешно создана!*\n\n"
                f"📞 Наш специалист свяжется с вами в ближайшее время.\n"
                f"⏱️ *Срочность:* {context.user_data['urgency']}\n"
                f"📍 *Участок:* {context.user_data['plot']}\n\n"
                f"📊 *Ваша статистика:* {user_stats.get('total_requests', 0)} заявок "
                f"({user_stats.get('completed', 0)} выполнено)\n\n"
                f"_Спасибо за обращение в службу слаботочных систем завода Контакт!_ 🛠️"
            )
            
            # Определяем клавиатуру
            if user.id in ADMIN_CHAT_IDS:
                reply_markup = ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
            else:
                reply_markup = ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
            
            update.message.reply_text(
                confirmation_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Новая заявка #{request_id} от {user.username}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении заявки: {e}")
            
            error_text = (
                "❌ *Произошла ошибка при создании заявки.*\n\n"
                "Пожалуйста, попробуйте позже или обратитесь к администратору."
            )
            
            if user.id in ADMIN_CHAT_IDS:
                update.message.reply_text(
                    error_text,
                    reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    error_text,
                    reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
                    parse_mode=ParseMode.MARKDOWN
                )
        
        context.user_data.clear()
        
    elif update.message.text == '✏️ Редактировать заявку':
        context.user_data['editing_mode'] = True
        return edit_request_choice(update, context)

def enhanced_send_admin_notification(context: CallbackContext, user_data: Dict, request_id: int) -> None:
    """Расширенное уведомление администраторов"""
    # Основное уведомление
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
    
    # Уведомление о срочности
    if '🔴 Срочно' in user_data.get('urgency', ''):
        notification_text = "🔴🔴🔴 СРОЧНАЯ ЗАЯВКА 🔴🔴🔴\n\n" + notification_text
    
    for admin_id in ADMIN_CHAT_IDS:
        try:
            if user_data.get('photo'):
                context.bot.send_photo(
                    chat_id=admin_id,
                    photo=user_data['photo'],
                    caption=notification_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{request_id}"),
                        InlineKeyboardButton("📋 Подробнее", callback_data=f"view_{request_id}")
                    ]])
                )
            else:
                context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Взять в работу", callback_data=f"take_{request_id}"),
                        InlineKeyboardButton("📋 Подробнее", callback_data=f"view_{request_id}")
                    ]])
                )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")

# ==================== НОВЫЕ КОМАНДЫ ====================

def show_user_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает статистику пользователя"""
    user_id = update.message.from_user.id
    user_stats = db.get_user_statistics(user_id)
    
    if not user_stats or user_stats.get('total_requests', 0) == 0:
        update.message.reply_text(
            "📊 *Ваша статистика*\n\n"
            "У вас пока нет созданных заявок.\n\n"
            "Создайте первую заявку, чтобы начать отслеживать статистику!",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Рассчитываем дополнительные метрики
    completion_rate = (user_stats['completed'] / user_stats['total_requests']) * 100
    avg_hours = user_stats.get('avg_completion_hours', 0)
    
    stats_text = (
        "📊 *Ваша статистика заявок*\n\n"
        f"📈 *Всего заявок:* {user_stats['total_requests']}\n"
        f"✅ *Выполнено:* {user_stats['completed']}\n"
        f"🔄 *В работе:* {user_stats.get('in_progress', 0)}\n"
        f"🆕 *Новых:* {user_stats.get('new', 0)}\n\n"
        f"📊 *Эффективность:*\n"
        f"• Процент выполнения: {completion_rate:.1f}%\n"
        f"• Среднее время выполнения: {avg_hours} часов\n\n"
    )
    
    # Добавляем информацию о первой и последней заявке
    if user_stats.get('first_request'):
        first_date = datetime.fromisoformat(user_stats['first_request']).strftime('%d.%m.%Y')
        stats_text += f"🎉 *Первая заявка:* {first_date}\n"
    
    if user_stats.get('last_request'):
        last_date = datetime.fromisoformat(user_stats['last_request']).strftime('%d.%m.%Y')
        stats_text += f"📅 *Последняя заявка:* {last_date}\n"
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def emergency_help(update: Update, context: CallbackContext) -> None:
    """Экстренная помощь"""
    user_id = update.message.from_user.id
    
    emergency_text = (
        "🆘 *Экстренная помощь*\n\n"
        "Для срочных вопросов и аварийных ситуаций:\n\n"
        "📞 *Телефон службы поддержки:*\n"
        "+7 (XXX) XXX-XX-XX\n\n"
        "👨‍💼 *Ответственный:*\n"
        "Иванов Иван Иванович\n\n"
        "📍 *Местоположение службы:*\n"
        "Главный корпус, кабинет 101\n\n"
        "⏰ *Режим работы:*\n"
        "Пн-Пт: 8:00-17:00\n"
        "Сб: 9:00-15:00\n"
        "Вс: выходной\n\n"
        "⚠️ *Для аварийных ситуаций:*\n"
        "Круглосуточный телефон: +7 (XXX) XXX-XX-XX"
    )
    
    update.message.reply_text(
        emergency_text,
        reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Уведомляем администраторов об обращении к экстренной помощи
    admin_notification = (
        f"🆘 Пользователь @{update.message.from_user.username or 'N/A'} "
        f"обратился к экстренной помощи"
    )
    
    for admin_id in ADMIN_CHAT_IDS:
        try:
            context.bot.send_message(admin_id, admin_notification)
        except Exception as e:
            logger.error(f"Ошибка уведомления администратора: {e}")

# ==================== РАСШИРЕННАЯ АДМИН-ПАНЕЛЬ ====================

def get_enhanced_admin_panel():
    """Возвращает расширенную админ-панель"""
    new_requests = db.get_requests_by_filter('new')
    in_progress_requests = db.get_requests_by_filter('in_progress')
    urgent_requests = db.get_urgent_requests()
    
    return [
        [f'🆕 Новые ({len(new_requests)})', f'🔄 В работе ({len(in_progress_requests)})'],
        [f'⏰ Срочные ({len(urgent_requests)})', '📊 Статистика'],
        ['👥 Пользователи', '⚙️ Настройки'],
        ['💾 Бэкапы', '🔄 Обновить']
    ]

def show_enhanced_admin_panel(update: Update, context: CallbackContext) -> None:
    """Показывает расширенную админ-панель"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return show_main_menu(update, context)
    
    # Получаем расширенную статистику
    stats = db.get_statistics(7)  # За 7 дней
    urgent_requests = db.get_urgent_requests()
    
    admin_text = (
        "👑 *Расширенная админ-панель завода Контакт*\n\n"
        f"📊 *За последние 7 дней:*\n"
        f"• Всего заявок: {stats['total']}\n"
        f"• Выполнено: {stats['completed']}\n"
        f"• Новых: {stats['new']}\n"
        f"• В работе: {stats['in_progress']}\n\n"
        f"⚠️ *Срочные заявки:* {len(urgent_requests)}\n\n"
        "Выберите раздел для управления:"
    )
    
    update.message.reply_text(
        admin_text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_users_management(update: Update, context: CallbackContext) -> None:
    """Управление пользователями"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, first_name, last_name, request_count, created_at
                FROM users 
                ORDER BY request_count DESC 
                LIMIT 50
            ''')
            users = cursor.fetchall()
        
        if not users:
            update.message.reply_text(
                "👥 *Управление пользователями*\n\n"
                "Пользователей не найдено.",
                reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        users_text = "👥 *Топ пользователей по количеству заявок:*\n\n"
        
        for i, (user_id, username, first_name, last_name, request_count, created_at) in enumerate(users[:10], 1):
            user_display = username or f"{first_name} {last_name}".strip() or f"ID: {user_id}"
            users_text += f"{i}. {user_display} - {request_count} заявок\n"
        
        users_text += f"\nВсего пользователей: {len(users)}"
        
        update.message.reply_text(
            users_text,
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        update.message.reply_text(
            "❌ Ошибка получения списка пользователей.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True)
        )

def show_settings(update: Update, context: CallbackContext) -> None:
    """Показывает настройки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    settings_text = (
        "⚙️ *Настройки системы*\n\n"
        f"🤖 *Бот:*\n"
        f"• Администраторов: {len(ADMIN_CHAT_IDS)}\n"
        f"• Лимит заявок: {MAX_REQUESTS_PER_HOUR}/час\n"
        f"• Хранение бэкапов: {BACKUP_RETENTION_DAYS} дней\n\n"
        f"💾 *База данных:*\n"
        f"• Путь: {DB_PATH}\n"
        f"• Размер: {os.path.getsize(DB_PATH) / 1024 / 1024:.2f} МБ\n\n"
        "Выберите раздел для настройки:"
    )
    
    update.message.reply_text(
        settings_text,
        reply_markup=ReplyKeyboardMarkup(settings_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_backup_management(update: Update, context: CallbackContext) -> None:
    """Управление бэкапами"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    backups = EnhancedBackupManager.get_backup_info()
    total_size = sum(b['size'] for b in backups) / 1024 / 1024  # в МБ
    
    backup_text = (
        "💾 *Управление бэкапами*\n\n"
        f"📊 *Статистика:*\n"
        f"• Всего бэкапов: {len(backups)}\n"
        f"• Общий размер: {total_size:.2f} МБ\n"
        f"• Авто-очистка: {BACKUP_RETENTION_DAYS} дней\n\n"
        "Выберите действие:"
    )
    
    update.message.reply_text(
        backup_text,
        reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def list_backups(update: Update, context: CallbackContext) -> None:
    """Показывает список бэкапов"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    backups = EnhancedBackupManager.get_backup_info()
    
    if not backups:
        update.message.reply_text(
            "📋 *Список бэкапов*\n\n"
            "Бэкапы не найдены.",
            reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    backups_text = "📋 *Последние 10 бэкапов:*\n\n"
    
    for i, backup in enumerate(backups[:10], 1):
        size_mb = backup['size'] / 1024 / 1024
        date_str = backup['created'].strftime('%d.%m.%Y %H:%M')
        backups_text += f"{i}. {backup['name']}\n"
        backups_text += f"   📅 {date_str} | 💾 {size_mb:.1f} МБ\n\n"
    
    update.message.reply_text(
        backups_text,
        reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def cleanup_backups(update: Update, context: CallbackContext) -> None:
    """Очищает старые бэкапы"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    deleted_count = EnhancedBackupManager.cleanup_old_backups()
    
    if deleted_count > 0:
        update.message.reply_text(
            f"🧹 *Очистка бэкапов*\n\n"
            f"Удалено {deleted_count} старых бэкапов.",
            reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        update.message.reply_text(
            "🧹 *Очистка бэкапов*\n\n"
            "Старые бэкапы не найдены.",
            reply_markup=ReplyKeyboardMarkup(backup_keyboard, resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ====================

def enhanced_handle_main_menu(update: Update, context: CallbackContext) -> None:
    """Улучшенный обработчик главного меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id in ADMIN_CHAT_IDS:
        return show_enhanced_admin_panel(update, context)
    
    if text == '📝 Создать заявку':
        return enhanced_start_request_creation(update, context)
    elif text == '📋 Мои заявки':
        return show_my_requests(update, context)
    elif text == '📊 Моя статистика':
        return show_user_statistics(update, context)
    elif text == '🆘 Срочная помощь':
        return emergency_help(update, context)
    else:
        update.message.reply_text(
            "Пожалуйста, выберите действие из меню:",
            reply_markup=ReplyKeyboardMarkup(enhanced_user_main_menu_keyboard, resize_keyboard=True)
        )

def enhanced_handle_admin_menu(update: Update, context: CallbackContext) -> None:
    """Улучшенный обработчик админ-меню"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    if text.startswith('🆕 Новые'):
        return show_requests_by_filter(update, context, 'new')
    elif text.startswith('🔄 В работе'):
        return show_requests_by_filter(update, context, 'in_progress')
    elif text.startswith('⏰ Срочные'):
        return show_urgent_requests(update, context)
    elif text == '📊 Статистика':
        return show_statistics(update, context)
    elif text == '👥 Пользователи':
        return show_users_management(update, context)
    elif text == '⚙️ Настройки':
        return show_settings(update, context)
    elif text == '💾 Бэкапы':
        return show_backup_management(update, context)
    elif text == '🔄 Обновить':
        return show_enhanced_admin_panel(update, context)

def show_urgent_requests(update: Update, context: CallbackContext) -> None:
    """Показывает срочные заявки"""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_CHAT_IDS:
        return show_main_menu(update, context)
    
    urgent_requests = db.get_urgent_requests()
    
    if not urgent_requests:
        update.message.reply_text(
            "⏰ *Срочные заявки*\n\n"
            "Срочных заявок не найдено.",
            reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    text = f"⏰ *Срочные заявки ({len(urgent_requests)}):*\n\n"
    
    for req in urgent_requests[:10]:
        created_time = datetime.fromisoformat(req['created_at'])
        time_diff = datetime.now() - created_time
        hours_passed = time_diff.total_seconds() / 3600
        
        text += (
            f"🔴 *Заявка #{req['id']}*\n"
            f"📍 {req['plot']} | {req['system_type']}\n"
            f"⏰ Прошло: {hours_passed:.1f} ч.\n"
            f"👤 {req['name']} | 📞 {req['phone']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
    
    update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(get_enhanced_admin_panel(), resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== ОБНОВЛЕННЫЙ ЗАПУСК ====================

def enhanced_main() -> None:
    """Улучшенный запуск бота"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Инициализация расширенных компонентов
        global db, notification_manager
        db = EnhancedDatabase(DB_PATH)
        notification_manager = NotificationManager(updater.bot)

        # Обработчик ошибок
        dispatcher.add_error_handler(error_handler)

        # Расширенные задания по расписанию
        job_queue = updater.job_queue
        if job_queue:
            # Ежедневное резервное копирование
            job_queue.run_daily(
                backup_job, 
                time=datetime.time(hour=AUTO_BACKUP_HOUR, minute=AUTO_BACKUP_MINUTE)
            )
            
            # Ежечасная проверка срочных заявок
            job_queue.run_repeating(
                check_urgent_requests, 
                interval=3600,  # 1 час
                first=10
            )
            
            # Обработка очереди уведомлений каждые 30 секунд
            job_queue.run_repeating(
                lambda context: notification_manager.process_queue(),
                interval=30,
                first=5
            )
            
            # Еженедельная очистка старых бэкапов
            job_queue.run_repeating(
                lambda context: EnhancedBackupManager.cleanup_old_backups(),
                interval=604800,  # 7 дней
                first=3600
            )

        # Обработчик создания заявки (сохраняем старый)
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(📝 Создать заявку)$'), enhanced_start_request_creation),
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

        # Регистрируем обработчики
        dispatcher.add_handler(CommandHandler('start', show_main_menu))
        dispatcher.add_handler(CommandHandler('menu', show_main_menu))
        dispatcher.add_handler(CommandHandler('admin', show_enhanced_admin_panel))
        dispatcher.add_handler(CommandHandler('stats', show_statistics))
        dispatcher.add_handler(CommandHandler('backup', create_backup_command))
        dispatcher.add_handler(CommandHandler('mystats', show_user_statistics))
        dispatcher.add_handler(CommandHandler('help', emergency_help))
        
        dispatcher.add_handler(conv_handler)
        
        # Обработчик подтверждения заявки
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(✅ Подтвердить отправку)$'), 
            enhanced_confirm_request
        ))
        
        # Обработчики главного меню
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📝 Создать заявку|📋 Мои заявки|📊 Моя статистика|🆘 Срочная помощь)$'), 
            enhanced_handle_main_menu
        ))
        
        # Обработчики админ-панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые|🔄 В работе|⏰ Срочные|📊 Статистика|👥 Пользователи|⚙️ Настройки|💾 Бэкапы|🔄 Обновить)'), 
            enhanced_handle_admin_menu
        ))
        
        # Обработчики настроек
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📊 Общая статистика|🔔 Уведомления|🔄 Авто-обновление|💾 Управление бэкапами|🔙 Назад в админ-панель)$'),
            lambda update, context: handle_settings(update, context)
        ))
        
        # Обработчики бэкапов
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(💾 Создать бэкап|📋 Список бэкапов|🧹 Очистить старые|🔙 Назад)$'),
            lambda update, context: handle_backup_commands(update, context)
        ))
        
        # Обработчики callback для админ-панели
        dispatcher.add_handler(CallbackQueryHandler(
            handle_admin_callback, 
            pattern='^(take_|complete_|message_|confirm_take_|cancel_take_|confirm_complete_|cancel_complete_|view_|back_)'
        ))

        # Запускаем бота
        logger.info("🤖 Улучшенный бот запущен с расширенными функциями!")
        logger.info(f"👑 Администраторы: {len(ADMIN_CHAT_IDS)}")
        logger.info(f"💾 Автоматические бэкапы: {AUTO_BACKUP_HOUR}:{AUTO_BACKUP_MINUTE:02d}")
        logger.info(f"📊 Лимит заявок: {MAX_REQUESTS_PER_HOUR}/час")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска улучшенного бота: {e}")

def check_urgent_requests(context: CallbackContext):
    """Проверяет срочные заявки и отправляет напоминания"""
    try:
        urgent_requests = db.get_urgent_requests()
        
        for request in urgent_requests:
            if request['status'] == 'new':
                # Уведомление о невзятых срочных заявках
                notification_text = (
                    f"⏰ *Напоминание о срочной заявке #{request['id']}*\n\n"
                    f"Заявка ожидает взятия в работу более 1 часа!\n"
                    f"📍 {request['plot']} | {request['system_type']}\n"
                    f"👤 {request['name']} | 📞 {request['phone']}"
                )
                
                notification_manager.send_priority_notification(
                    ADMIN_CHAT_IDS,
                    notification_text
                )
                
    except Exception as e:
        logger.error(f"Ошибка проверки срочных заявок: {e}")

def handle_settings(update: Update, context: CallbackContext):
    """Обрабатывает команды настроек"""
    text = update.message.text
    
    if text == '🔙 Назад в админ-панель':
        return show_enhanced_admin_panel(update, context)
    elif text == '💾 Управление бэкапами':
        return show_backup_management(update, context)
    # Добавьте обработку других настроек по необходимости

def handle_backup_commands(update: Update, context: CallbackContext):
    """Обрабатывает команды управления бэкапами"""
    text = update.message.text
    
    if text == '🔙 Назад':
        return show_settings(update, context)
    elif text == '💾 Создать бэкап':
        return create_backup_command(update, context)
    elif text == '📋 Список бэкапов':
        return list_backups(update, context)
    elif text == '🧹 Очистить старые':
        return cleanup_backups(update, context)

# Сохраняем старые функции для обратной совместимости
def show_main_menu(update: Update, context: CallbackContext) -> None:
    """Совместимость со старым кодом"""
    return enhanced_handle_main_menu(update, context)

def start_request_creation(update: Update, context: CallbackContext) -> int:
    """Совместимость со старым кодом"""
    return enhanced_start_request_creation(update, context)

if __name__ == '__main__':
    enhanced_main()
