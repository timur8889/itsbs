import logging
import os
import sys
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные переменные для доступа из обработчиков
ADMIN_IDS = []
db = None

def is_admin(user_id: int, admin_ids: list) -> bool:
    """Проверка прав администратора с логированием"""
    is_admin_user = user_id in admin_ids
    print(f"🔐 Admin check: User {user_id} -> {is_admin_user}")
    return is_admin_user

def load_config():
    """Загрузка и проверка конфигурации"""
    from dotenv import load_dotenv
    load_dotenv()
    
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_IDS_STR = os.getenv('ADMIN_IDS', '')
    DB_URL = os.getenv('DATABASE_URL', 'sqlite:///it_requests.db')
    
    # Парсинг ADMIN_IDS
    ADMIN_IDS = []
    if ADMIN_IDS_STR:
        try:
            ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(',') if x.strip()]
            print(f"✅ Parsed ADMIN_IDS: {ADMIN_IDS}")
        except ValueError as e:
            print(f"❌ Error parsing ADMIN_IDS: {e}")
            ADMIN_IDS = []
    
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set in .env file")
    
    return BOT_TOKEN, ADMIN_IDS, DB_URL

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для отладки"""
    user = update.effective_user
    debug_text = f"""
🔍 ДЕБАГ ИНФОРМАЦИЯ:

👤 Ваш ID: {user.id}
👤 Ваше имя: {user.full_name}
🔐 Админ: {is_admin(user.id, ADMIN_IDS)}
📋 Admin IDs: {ADMIN_IDS}
"""
    await update.message.reply_text(debug_text)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена текущей операции"""
    await update.message.reply_text(
        "Операция отменена.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")
        ]])
    )
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик необработанных исключений"""
    logger.error(f"Exception while handling an update: {context.error}")
    
    # Отправляем сообщение пользователю
    if update and update.effective_user:
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="❌ Произошла непредвиденная ошибка. Попробуйте позже."
            )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

def main():
    global ADMIN_IDS, db
    
    try:
        from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return

    # Загружаем конфигурацию
    try:
        BOT_TOKEN, ADMIN_IDS, DB_URL = load_config()
        print(f"🤖 Starting IT Support Bot...")
        print(f"🔑 Token: {BOT_TOKEN[:10]}...")
        print(f"👨‍💼 Admins: {ADMIN_IDS}")
        print(f"💾 Database: {DB_URL}")
    except Exception as e:
        logger.error(f"Config error: {e}")
        print(f"❌ Configuration error: {e}")
        return
    
    # Инициализация базы данных
    try:
        from database.models import Database
        db = Database(DB_URL)
        print("✅ Database initialized")
    except Exception as e:
        logger.error(f"Database error: {e}")
        print("❌ Database initialization failed")
        return
    
    try:
        # Создание приложения
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Временное хранилище данных пользователей
        user_sessions = {}
        
        # ==================== ОСНОВНЫЕ КОМАНДЫ ====================
        
        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Команда /start - главное меню"""
            user = update.effective_user
            
            # Детальная проверка администратора
            is_admin_user = is_admin(user.id, ADMIN_IDS)
            print(f"👤 User: {user.id} ({user.full_name}), Admin: {is_admin_user}")
            
            welcome_text = f"""👋 Добро пожаловать, {user.first_name}!

🤖 Я - бот IT-отдела завода "Контакт". 
Я помогу вам оставить заявку на техническую поддержку.

Выберите действие:"""
            
            keyboard = [
                [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                [InlineKeyboardButton("📋 Мои заявки", callback_data="my_requests")],
                [InlineKeyboardButton("ℹ️ Справка", callback_data="help")]
            ]
            
            # Добавляем кнопку админ-панели только для администраторов
            if is_admin_user:
                keyboard.append([InlineKeyboardButton("👨‍💼 Админ панель", callback_data="admin_panel")])
                print(f"✅ Admin panel button ADDED for user {user.id}")
            else:
                print(f"❌ Admin panel button NOT added for user {user.id}")
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.message:
                await update.message.reply_text(welcome_text, reply_markup=reply_markup)
            else:
                await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup)
        
        async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Команда помощи"""
            help_text = """📖 Справка по использованию бота

Для создания заявки нажмите "📝 Создать заявку" и следуйте инструкциям.

Категории заявок:
🖥️ Оборудование - проблемы с техникой
💻 ПО - программные проблемы  
🌐 Сеть - интернет и сетевые вопросы
👤 Учетные записи - доступы и пароли
❓ Другое - все остальное

По вопросам обращайтесь в IT-отдел."""
            
            if update.message:
                await update.message.reply_text(help_text)
            else:
                await update.callback_query.edit_message_text(help_text)
        
        # ==================== АДМИН ПАНЕЛЬ ====================
        
        async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Главное меню админ-панели"""
            query = update.callback_query
            await query.answer()
            
            user_id = query.from_user.id
            print(f"🛠️ Admin panel requested by user {user_id}")
            
            if not is_admin(user_id, ADMIN_IDS):
                await query.answer("❌ У вас нет доступа к админ-панели", show_alert=True)
                print(f"❌ Access DENIED for user {user_id}")
                return
            
            print(f"✅ Access GRANTED for user {user_id}")
            
            # Получаем статистику
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                total = session.query(ITRequest).count()
                new = session.query(ITRequest).filter(ITRequest.status == Status.NEW).count()
                in_progress = session.query(ITRequest).filter(ITRequest.status == Status.IN_PROGRESS).count()
                
                stats_text = f"""👨‍💼 ПАНЕЛЬ АДМИНИСТРАТОРА

📊 Статистика системы:
• 📋 Всего заявок: {total}
• 🆕 Новых: {new}
• 🔄 В работе: {in_progress}

Выберите раздел:"""
                
            except Exception as e:
                logger.error(f"Error getting stats: {e}")
                stats_text = "👨‍💼 ПАНЕЛЬ АДМИНИСТРАТОРА\n\nВыберите раздел:"
            finally:
                session.close()
            
            keyboard = [
                [InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests")],
                [InlineKeyboardButton("🆕 Новые заявки", callback_data="admin_new_requests")],
                [InlineKeyboardButton("🔄 Заявки в работе", callback_data="admin_in_progress")],
                [InlineKeyboardButton("📊 Подробная статистика", callback_data="admin_stats")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(stats_text, reply_markup=reply_markup)
        
        async def admin_all_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Показать все заявки"""
            query = update.callback_query
            await query.answer()
            
            if not is_admin(query.from_user.id, ADMIN_IDS):
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            session = db.get_session()
            try:
                from database.models import ITRequest
                requests = session.query(ITRequest).order_by(ITRequest.created_at.desc()).limit(10).all()
                
                if not requests:
                    text = "📭 В системе пока нет заявок"
                    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]
                else:
                    text = "📋 Последние заявки:\n\n"
                    for req in requests:
                        status_icons = {'new': '🆕', 'in_progress': '🔄', 'on_hold': '⏸️', 'resolved': '✅', 'closed': '📋'}
                        priority_icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴', 'critical': '💥'}
                        
                        status_icon = status_icons.get(req.status.value, '❓')
                        priority_icon = priority_icons.get(req.priority.value, '⚪')
                        
                        text += f"{status_icon}{priority_icon} #{req.id}: {req.title}\n"
                        text += f"   👤 {req.full_name} | {req.created_at.strftime('%d.%m %H:%M')}\n\n"
                    
                    keyboard = []
                    for req in requests[:3]:
                        keyboard.append([InlineKeyboardButton(f"📝 #{req.id} - {req.title[:20]}...", callback_data=f"admin_view_{req.id}")])
                    
                    keyboard.extend([
                        [InlineKeyboardButton("🆕 Новые", callback_data="admin_new_requests")],
                        [InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")]
                    ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting requests: {e}")
                await query.edit_message_text("❌ Ошибка при загрузке заявок")
            finally:
                session.close()
        
        async def admin_view_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Просмотр конкретной заявки"""
            query = update.callback_query
            await query.answer()
            
            if not is_admin(query.from_user.id, ADMIN_IDS):
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            request_id = int(query.data.replace('admin_view_', ''))
            
            session = db.get_session()
            try:
                from database.models import ITRequest
                request = session.query(ITRequest).filter(ITRequest.id == request_id).first()
                
                if not request:
                    await query.answer("Заявка не найдена", show_alert=True)
                    return
                
                # Форматируем информацию о заявке
                status_icons = {'new': '🆕', 'in_progress': '🔄', 'on_hold': '⏸️', 'resolved': '✅', 'closed': '📋'}
                priority_icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴', 'critical': '💥'}
                
                text = f"""📋 ЗАЯВКА #{request.id}
━━━━━━━━━━━━━━━━━━━━

👤 Сотрудник: {request.full_name}
📞 Телефон: {request.contact_phone}
🏢 Местоположение: {request.location}

📂 Категория: {request.category.value}
🚨 Приоритет: {priority_icons.get(request.priority.value)} {request.priority.value}
📊 Статус: {status_icons.get(request.status.value)} {request.status.value}

📝 Тема: {request.title}
📄 Описание:
{request.description}"""
                
                if request.assigned_to:
                    text += f"\n\n👨‍💼 Исполнитель: {request.assigned_to}"
                
                if request.solution:
                    text += f"\n\n💡 Решение:\n{request.solution}"
                
                text += f"\n\n⏰ Создана: {request.created_at.strftime('%d.%m.%Y %H:%M')}"
                
                # Кнопки действий
                keyboard = []
                
                if request.status.value == 'new':
                    keyboard.append([InlineKeyboardButton("🔄 Взять в работу", callback_data=f"admin_take_{request.id}")])
                
                keyboard.append([InlineKeyboardButton("✏️ Добавить решение", callback_data=f"admin_solution_{request.id}")])
                keyboard.append([
                    InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests"),
                    InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error viewing request: {e}")
                await query.edit_message_text("❌ Ошибка при загрузке заявки")
            finally:
                session.close()
        
        async def admin_take_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Взять заявку в работу"""
            query = update.callback_query
            await query.answer()
            
            if not is_admin(query.from_user.id, ADMIN_IDS):
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            request_id = int(query.data.replace('admin_take_', ''))
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                request = session.query(ITRequest).filter(ITRequest.id == request_id).first()
                
                if request:
                    request.status = Status.IN_PROGRESS
                    request.assigned_to = query.from_user.full_name
                    session.commit()
                    
                    await query.answer("✅ Заявка взята в работу")
                    # Обновляем просмотр заявки
                    await admin_view_request(update, context)
                else:
                    await query.answer("❌ Заявка не найдена", show_alert=True)
                    
            except Exception as e:
                logger.error(f"Error taking request: {e}")
                await query.answer("❌ Ошибка", show_alert=True)
            finally:
                session.close()
        
        async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Подробная статистика"""
            query = update.callback_query
            await query.answer()
            
            if not is_admin(query.from_user.id, ADMIN_IDS):
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status, Category
                from sqlalchemy import func
                
                # Базовая статистика
                total = session.query(ITRequest).count()
                new = session.query(ITRequest).filter(ITRequest.status == Status.NEW).count()
                in_progress = session.query(ITRequest).filter(ITRequest.status == Status.IN_PROGRESS).count()
                resolved = session.query(ITRequest).filter(ITRequest.status == Status.RESOLVED).count()
                closed = session.query(ITRequest).filter(ITRequest.status == Status.CLOSED).count()
                
                # Статистика по категориям
                categories = {}
                for category in Category:
                    count = session.query(ITRequest).filter(ITRequest.category == category).count()
                    categories[category.value] = count
                
                stats_text = f"""📈 ДЕТАЛЬНАЯ СТАТИСТИКА

📊 Общая статистика:
• 📋 Всего заявок: {total}
• 🆕 Новых: {new}
• 🔄 В работе: {in_progress}
• ✅ Решено: {resolved}
• 📋 Закрыто: {closed}

📂 По категориям:
• 🖥️ Оборудование: {categories.get('hardware', 0)}
• 💻 ПО: {categories.get('software', 0)}
• 🌐 Сеть: {categories.get('network', 0)}
• 👤 Учетные записи: {categories.get('account', 0)}
• ❓ Другое: {categories.get('other', 0)}"""
                
                keyboard = [
                    [InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests")],
                    [InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(stats_text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting stats: {e}")
                await query.edit_message_text("❌ Ошибка получения статистики")
            finally:
                session.close()
        
        async def admin_filtered_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Фильтрованные заявки"""
            query = update.callback_query
            await query.answer()
            
            if not is_admin(query.from_user.id, ADMIN_IDS):
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            filter_type = query.data.replace('admin_', '').replace('_requests', '')
            
            status_map = {
                'new': 'new',
                'in_progress': 'in_progress'
            }
            
            filter_names = {
                'new': '🆕 Новые',
                'in_progress': '🔄 В работе'
            }
            
            if filter_type not in status_map:
                await query.edit_message_text("❌ Неверный фильтр")
                return
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                
                requests = session.query(ITRequest).filter(
                    ITRequest.status == Status(status_map[filter_type])
                ).order_by(ITRequest.created_at.desc()).limit(10).all()
                
                text = f"{filter_names[filter_type]} заявки:\n\n"
                
                if not requests:
                    text += "Заявок нет"
                else:
                    for req in requests:
                        status_icons = {'new': '🆕', 'in_progress': '🔄'}
                        priority_icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴', 'critical': '💥'}
                        
                        text += f"{status_icons[req.status.value]}{priority_icons[req.priority.value]} #{req.id}: {req.title}\n"
                        text += f"   👤 {req.full_name} | {req.created_at.strftime('%d.%m %H:%M')}\n\n"
                
                keyboard = []
                for req in requests[:3]:
                    keyboard.append([InlineKeyboardButton(f"📝 #{req.id}", callback_data=f"admin_view_{req.id}")])
                
                keyboard.extend([
                    [InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests")],
                    [InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")]
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting filtered requests: {e}")
                await query.edit_message_text("❌ Ошибка при загрузке заявок")
            finally:
                session.close()
        
        # ==================== СИСТЕМА ЗАЯВОК ====================
        
        async def show_my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Показать заявки пользователя"""
            query = update.callback_query
            await query.answer()
            
            user_id = query.from_user.id
            
            session = db.get_session()
            try:
                from database.models import ITRequest
                requests = session.query(ITRequest).filter(
                    ITRequest.user_id == user_id
                ).order_by(ITRequest.created_at.desc()).limit(5).all()
                
                if not requests:
                    text = "📭 У вас пока нет заявок"
                else:
                    text = "📋 Ваши заявки:\n\n"
                    for req in requests:
                        status_icons = {'new': '🆕', 'in_progress': '🔄', 'resolved': '✅', 'closed': '📋'}
                        text += f"{status_icons.get(req.status.value, '❓')} #{req.id}: {req.title}\n"
                        text += f"   Статус: {req.status.value}\n"
                        text += f"   Создана: {req.created_at.strftime('%d.%m %H:%M')}\n\n"
                
                keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting user requests: {e}")
                await query.edit_message_text("❌ Ошибка при загрузке заявок")
            finally:
                session.close()
        
        # ==================== ОБРАБОТЧИК КНОПОК ====================
        
        async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Главный обработчик кнопок"""
            query = update.callback_query
            await query.answer()
            
            print(f"Button pressed: {query.data} by user {query.from_user.id}")
            
            # Основные кнопки
            if query.data == "main_menu":
                await start(update, context)
            elif query.data == "help":
                await help_command(update, context)
            elif query.data == "my_requests":
                await show_my_requests(update, context)
            
            # Админ кнопки
            elif query.data == "admin_panel":
                await admin_panel(update, context)
            elif query.data == "admin_all_requests":
                await admin_all_requests(update, context)
            elif query.data == "admin_new_requests":
                await admin_filtered_requests(update, context)
            elif query.data == "admin_in_progress":
                await admin_filtered_requests(update, context)
            elif query.data == "admin_stats":
                await admin_stats(update, context)
            elif query.data.startswith("admin_view_"):
                await admin_view_request(update, context)
            elif query.data.startswith("admin_take_"):
                await admin_take_request(update, context)
            
            # Создание заявки (упрощенное)
            elif query.data == "create_request":
                await query.edit_message_text(
                    "📝 Создание заявки\n\n"
                    "В данный момент функция находится в разработке.\n"
                    "Для создания заявки обратитесь в IT-отдел напрямую.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад", callback_data="main_menu")
                    ]])
                )
        
        # ==================== РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ====================
        
        # Основные команды
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("debug", debug_command))
        application.add_handler(CommandHandler("cancel", cancel))
        
        # Обработчик кнопок
        application.add_handler(CallbackQueryHandler(button_handler))
        
        # Обработчик ошибок
        application.add_error_handler(error_handler)
        
        print("✅ Bot initialized successfully")
        print("🔄 Starting polling...")
        
        # Запускаем бота
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"❌ Critical error: {e}")

if __name__ == '__main__':
    main()
