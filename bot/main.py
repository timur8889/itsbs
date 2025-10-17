import logging
import os
import sys

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def main():
    try:
        from config import BotConfig
        from database.models import Database
        from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return

    config = BotConfig()
    
    if not config.token:
        logger.error("BOT_TOKEN not set")
        print("❌ Error: BOT_TOKEN not found in .env file")
        return
    
    print(f"🤖 Starting IT Support Bot for factory 'Kontakt'...")
    print(f"🔑 Token: {config.token[:10]}...")
    print(f"👨‍💼 Admins: {config.admin_ids}")
    print(f"💾 Database: {config.db_url}")
    
    try:
        # Initialize database
        db = Database(config.db_url)
        print("✅ Database initialized")
        
        # Create application
        application = Application.builder().token(config.token).build()
        
        # User data storage
        user_sessions = {}
        
        # Start command
        async def start(update, context):
            user = update.effective_user
            welcome_text = f"""👋 Добро пожаловать, {user.first_name}!

🤖 Я - бот IT-отдела завода "Контакт". 
Я помогу вам оставить заявку на техническую поддержку.

Выберите действие:"""
            
            keyboard = [
                [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                [InlineKeyboardButton("📋 Мои заявки", callback_data="my_requests")],
                [InlineKeyboardButton("ℹ️ Справка", callback_data="help")]
            ]
            
            if user.id in config.admin_ids:
                keyboard.append([InlineKeyboardButton("👨‍💼 Админ панель", callback_data="admin_panel")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.message:
                await update.message.reply_text(welcome_text, reply_markup=reply_markup)
            else:
                await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup)
        
        # Help command
        async def help_command(update, context):
            help_text = """📖 Справка по использованию бота

Для создания заявки нажмите "📝 Создать заявку" и следуйте инструкциям.

Категории заявок:
🖥️ Оборудование - проблемы с техникой
💻 ПО - программные проблемы  
🌐 Сеть - интернет и сетевые вопросы
👤 Учетные записи - доступы и пароли
❓ Другое - все остальное

По вопросам: IT-отдел, тел. 1234"""
            
            if update.message:
                await update.message.reply_text(help_text)
            else:
                await update.callback_query.edit_message_text(help_text)
        
        # Show user requests
        async def show_my_requests(update, context):
            query = update.callback_query
            await query.answer()
            
            user_id = query.from_user.id
            
            session = db.get_session()
            try:
                from database.models import ITRequest
                requests = session.query(ITRequest).filter(
                    ITRequest.user_id == user_id
                ).order_by(ITRequest.created_at.desc()).limit(10).all()
                
                if not requests:
                    await query.edit_message_text(
                        "📭 У вас пока нет заявок.\n\n"
                        "Нажмите '📝 Создать заявку' чтобы создать первую заявку.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("📝 Создать заявку", callback_data="create_request"),
                            InlineKeyboardButton("🔙 Назад", callback_data="main_menu")
                        ]])
                    )
                    return
                
                text = "📋 Ваши последние заявки:\n\n"
                for req in requests:
                    status_icons = {
                        'new': '🆕',
                        'in_progress': '🔄',
                        'on_hold': '⏸️',
                        'resolved': '✅',
                        'closed': '📋'
                    }
                    
                    priority_icons = {
                        'low': '🟢',
                        'medium': '🟡', 
                        'high': '🔴',
                        'critical': '💥'
                    }
                    
                    status_icon = status_icons.get(req.status.value, '❓')
                    priority_icon = priority_icons.get(req.priority.value, '⚪')
                    
                    text += f"{status_icon} {priority_icon} Заявка #{req.id}\n"
                    text += f"   📝 {req.title}\n"
                    text += f"   🏷️ {req.category.value} | 📊 {req.status.value}\n"
                    text += f"   🕐 {req.created_at.strftime('%d.%m %H:%M')}\n\n"
                
                keyboard = [
                    [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting user requests: {e}")
                await query.edit_message_text("❌ Ошибка при получении заявок")
            finally:
                session.close()
        
        # Admin panel
        async def admin_panel(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.from_user.id not in config.admin_ids:
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                from sqlalchemy import func
                
                total = session.query(ITRequest).count()
                new = session.query(ITRequest).filter(ITRequest.status == Status.NEW).count()
                in_progress = session.query(ITRequest).filter(ITRequest.status == Status.IN_PROGRESS).count()
                
                stats_text = f"""👨‍💼 Панель администратора

📊 Статистика:
• 📋 Всего заявок: {total}
• 🆕 Новых: {new}
• 🔄 В работе: {in_progress}"""
                
                keyboard = [
                    [InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests")],
                    [InlineKeyboardButton("🆕 Новые заявки", callback_data="admin_new_requests")],
                    [InlineKeyboardButton("🔄 В работе", callback_data="admin_in_progress")],
                    [InlineKeyboardButton("📈 Подробная статистика", callback_data="admin_detailed_stats")],
                    [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(stats_text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error in admin panel: {e}")
                await query.edit_message_text("❌ Ошибка в админ панели")
            finally:
                session.close()
        
        # Admin: Show all requests
        async def admin_all_requests(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.from_user.id not in config.admin_ids:
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            session = db.get_session()
            try:
                from database.models import ITRequest
                requests = session.query(ITRequest).order_by(
                    ITRequest.created_at.desc()
                ).limit(20).all()
                
                if not requests:
                    await query.edit_message_text(
                        "📭 Нет заявок",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")
                        ]])
                    )
                    return
                
                text = "📋 Последние 20 заявок:\n\n"
                
                for req in requests:
                    status_icons = {'new': '🆕', 'in_progress': '🔄', 'on_hold': '⏸️', 'resolved': '✅', 'closed': '📋'}
                    priority_icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴', 'critical': '💥'}
                    
                    status_icon = status_icons.get(req.status.value, '❓')
                    priority_icon = priority_icons.get(req.priority.value, '⚪')
                    
                    text += f"{status_icon} {priority_icon} #{req.id}: {req.title[:30]}...\n"
                    text += f"   👤 {req.full_name} | 🏢 {req.location}\n"
                    text += f"   🕐 {req.created_at.strftime('%d.%m %H:%M')}\n"
                    
                    # Add action buttons for each request
                    context.user_data[f'req_{req.id}'] = req.id
                
                keyboard = []
                for req in requests[:5]:  # Show buttons for first 5 requests
                    keyboard.append([
                        InlineKeyboardButton(f"📝 #{req.id}", callback_data=f"admin_view_{req.id}")
                    ])
                
                keyboard.extend([
                    [InlineKeyboardButton("🆕 Новые", callback_data="admin_new_requests")],
                    [InlineKeyboardButton("🔄 В работе", callback_data="admin_in_progress")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting all requests: {e}")
                await query.edit_message_text("❌ Ошибка при получении заявок")
            finally:
                session.close()
        
        # Admin: View specific request
        async def admin_view_request(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.from_user.id not in config.admin_ids:
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
                
                status_icons = {'new': '🆕', 'in_progress': '🔄', 'on_hold': '⏸️', 'resolved': '✅', 'closed': '📋'}
                priority_icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴', 'critical': '💥'}
                
                text = f"""📋 Заявка #{request.id}
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
                
                # Action buttons based on current status
                keyboard = []
                
                if request.status.value == 'new':
                    keyboard.append([InlineKeyboardButton("🔄 Взять в работу", callback_data=f"admin_take_{request.id}")])
                
                if request.status.value in ['new', 'in_progress']:
                    keyboard.append([InlineKeyboardButton("⏸️ На паузу", callback_data=f"admin_hold_{request.id}")])
                    keyboard.append([InlineKeyboardButton("✅ Решено", callback_data=f"admin_resolve_{request.id}")])
                
                if request.status.value in ['on_hold', 'resolved']:
                    keyboard.append([InlineKeyboardButton("🔄 Вернуть в работу", callback_data=f"admin_retake_{request.id}")])
                
                if request.status.value == 'resolved':
                    keyboard.append([InlineKeyboardButton("📋 Закрыть", callback_data=f"admin_close_{request.id}")])
                
                keyboard.append([InlineKeyboardButton("✏️ Добавить решение", callback_data=f"admin_solution_{request.id}")])
                keyboard.append([InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests")])
                keyboard.append([InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error viewing request {request_id}: {e}")
                await query.edit_message_text("❌ Ошибка при просмотре заявки")
            finally:
                session.close()
        
        # Admin: Take request
        async def admin_take_request(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.from_user.id not in config.admin_ids:
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
                    
                    # Notify user
                    try:
                        await context.bot.send_message(
                            chat_id=request.user_id,
                            text=f"🔄 Ваша заявка #{request_id} взята в работу\nИсполнитель: {query.from_user.full_name}"
                        )
                    except Exception as e:
                        logger.error(f"Could not notify user: {e}")
                    
                    await query.answer("✅ Заявка взята в работу")
                    await admin_view_request(update, context)
                else:
                    await query.answer("❌ Заявка не найдена", show_alert=True)
                    
            except Exception as e:
                logger.error(f"Error taking request: {e}")
                await query.answer("❌ Ошибка", show_alert=True)
            finally:
                session.close()
        
        # Admin: Update request status
        async def admin_update_status(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.from_user.id not in config.admin_ids:
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            action, request_id = query.data.split('_')[1:3]
            request_id = int(request_id)
            
            status_map = {
                'hold': 'on_hold',
                'resolve': 'resolved', 
                'retake': 'in_progress',
                'close': 'closed'
            }
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                request = session.query(ITRequest).filter(ITRequest.id == request_id).first()
                
                if request and action in status_map:
                    new_status = status_map[action]
                    request.status = Status(new_status)
                    
                    if action == 'retake':
                        request.assigned_to = query.from_user.full_name
                    
                    session.commit()
                    
                    # Notify user
                    status_messages = {
                        'hold': f"⏸️ Заявка #{request_id} приостановлена",
                        'resolve': f"✅ Заявка #{request_id} решена", 
                        'retake': f"🔄 Заявка #{request_id} возвращена в работу",
                        'close': f"📋 Заявка #{request_id} закрыта"
                    }
                    
                    try:
                        await context.bot.send_message(
                            chat_id=request.user_id,
                            text=status_messages[action]
                        )
                    except Exception as e:
                        logger.error(f"Could not notify user: {e}")
                    
                    await query.answer(f"Статус обновлен: {new_status}")
                    await admin_view_request(update, context)
                else:
                    await query.answer("❌ Ошибка обновления", show_alert=True)
                    
            except Exception as e:
                logger.error(f"Error updating status: {e}")
                await query.answer("❌ Ошибка", show_alert=True)
            finally:
                session.close()
        
        # Admin: Add solution
        async def admin_add_solution(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.from_user.id not in config.admin_ids:
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            request_id = int(query.data.replace('admin_solution_', ''))
            context.user_data['editing_solution'] = request_id
            
            await query.message.reply_text(
                "💡 Введите решение по заявке:"
            )
        
        # Save solution
        async def save_solution(update, context):
            if 'editing_solution' not in context.user_data:
                return
            
            request_id = context.user_data['editing_solution']
            solution = update.message.text
            
            session = db.get_session()
            try:
                from database.models import ITRequest
                request = session.query(ITRequest).filter(ITRequest.id == request_id).first()
                
                if request:
                    request.solution = solution
                    session.commit()
                    
                    # Notify user
                    try:
                        await context.bot.send_message(
                            chat_id=request.user_id,
                            text=f"💡 По вашей заявке #{request_id} добавлено решение:\n\n{solution}"
                        )
                    except Exception as e:
                        logger.error(f"Could not notify user: {e}")
                    
                    await update.message.reply_text("✅ Решение сохранено")
                    
                    # Clear editing state
                    context.user_data.pop('editing_solution', None)
                else:
                    await update.message.reply_text("❌ Заявка не найдена")
                    
            except Exception as e:
                logger.error(f"Error saving solution: {e}")
                await update.message.reply_text("❌ Ошибка сохранения")
            finally:
                session.close()
        
        # Admin: Detailed stats
        async def admin_detailed_stats(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.from_user.id not in config.admin_ids:
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status, Category
                from sqlalchemy import func
                
                # Basic counts
                total = session.query(ITRequest).count()
                new = session.query(ITRequest).filter(ITRequest.status == Status.NEW).count()
                in_progress = session.query(ITRequest).filter(ITRequest.status == Status.IN_PROGRESS).count()
                resolved = session.query(ITRequest).filter(ITRequest.status == Status.RESOLVED).count()
                closed = session.query(ITRequest).filter(ITRequest.status == Status.CLOSED).count()
                
                # Today's stats
                from datetime import datetime, date
                today = date.today()
                today_requests = session.query(ITRequest).filter(
                    func.date(ITRequest.created_at) == today
                ).count()
                today_resolved = session.query(ITRequest).filter(
                    func.date(ITRequest.updated_at) == today,
                    ITRequest.status == Status.RESOLVED
                ).count()
                
                # Category stats
                category_stats = []
                for category in Category:
                    count = session.query(ITRequest).filter(ITRequest.category == category).count()
                    if count > 0:
                        category_stats.append(f"• {category.value}: {count}")
                
                stats_text = f"""📈 Детальная статистика

📊 Общая статистика:
• 📋 Всего заявок: {total}
• 🆕 Новых: {new}
• 🔄 В работе: {in_progress}
• ✅ Решено: {resolved}
• 📋 Закрыто: {closed}

📅 Сегодня:
• 📥 Новых заявок: {today_requests}
• ✅ Решено заявок: {today_resolved}

📂 По категориям:
{chr(10).join(category_stats)}"""
                
                keyboard = [
                    [InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests")],
                    [InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(stats_text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting detailed stats: {e}")
                await query.edit_message_text("❌ Ошибка получения статистики")
            finally:
                session.close()
        
        # Filtered requests for admin
        async def admin_filtered_requests(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.from_user.id not in config.admin_ids:
                await query.answer("❌ Нет доступа", show_alert=True)
                return
            
            filter_type = query.data.replace('admin_', '').replace('_requests', '')
            
            status_map = {
                'new': 'new',
                'in_progress': 'in_progress'
            }
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                
                if filter_type in status_map:
                    requests = session.query(ITRequest).filter(
                        ITRequest.status == Status(status_map[filter_type])
                    ).order_by(ITRequest.created_at.desc()).limit(20).all()
                    
                    filter_names = {
                        'new': '🆕 Новые',
                        'in_progress': '🔄 В работе'
                    }
                    
                    text = f"📋 {filter_names[filter_type]} заявки:\n\n"
                    
                    if not requests:
                        text += "Заявок нет"
                    else:
                        for req in requests:
                            status_icons = {'new': '🆕', 'in_progress': '🔄'}
                            priority_icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴', 'critical': '💥'}
                            
                            text += f"{status_icons[req.status.value]} {priority_icons[req.priority.value]} #{req.id}: {req.title[:30]}...\n"
                            text += f"   👤 {req.full_name} | 🏢 {req.location}\n"
                            text += f"   🕐 {req.created_at.strftime('%d.%m %H:%M')}\n\n"
                    
                    keyboard = []
                    for req in requests[:5]:
                        keyboard.append([
                            InlineKeyboardButton(f"📝 #{req.id}", callback_data=f"admin_view_{req.id}")
                        ])
                    
                    keyboard.extend([
                        [InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests")],
                        [InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")]
                    ])
                    
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting filtered requests: {e}")
                await query.edit_message_text("❌ Ошибка при получении заявок")
            finally:
                session.close()
        
        # Request creation conversation states
        TITLE, DESCRIPTION, LOCATION, PHONE = range(4)
        
        # Start creating request
        async def start_create_request(update, context):
            query = update.callback_query
            await query.answer()
            
            # Initialize user data
            user_sessions[query.from_user.id] = {
                'category': 'other',
                'priority': 'medium'
            }
            
            # Ask for category
            keyboard = [
                [InlineKeyboardButton("🖥️ Оборудование", callback_data="cat_hardware")],
                [InlineKeyboardButton("💻 ПО", callback_data="cat_software")],
                [InlineKeyboardButton("🌐 Сеть", callback_data="cat_network")],
                [InlineKeyboardButton("👤 Учетные записи", callback_data="cat_account")],
                [InlineKeyboardButton("❓ Другое", callback_data="cat_other")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "📂 Выберите категорию проблемы:",
                reply_markup=reply_markup
            )
        
        # Handle category selection
        async def handle_category(update, context):
            query = update.callback_query
            await query.answer()
            
            category = query.data.replace('cat_', '')
            user_sessions[query.from_user.id]['category'] = category
            
            # Ask for priority
            keyboard = [
                [InlineKeyboardButton("🟢 Низкий", callback_data="pri_low")],
                [InlineKeyboardButton("🟡 Средний", callback_data="pri_medium")],
                [InlineKeyboardButton("🔴 Высокий", callback_data="pri_high")],
                [InlineKeyboardButton("💥 Критический", callback_data="pri_critical")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "🚨 Выберите приоритет заявки:",
                reply_markup=reply_markup
            )
        
        # Handle priority selection and start conversation
        async def handle_priority(update, context):
            query = update.callback_query
            await query.answer()
            
            priority = query.data.replace('pri_', '')
            user_sessions[query.from_user.id]['priority'] = priority
            
            await query.edit_message_text(
                "📝 Введите краткое описание проблемы (максимум 200 символов):\n\n"
                "Пример: 'Не работает мышь на компьютере'"
            )
            
            return TITLE
        
        # Handle title input
        async def handle_title(update, context):
            title = update.message.text.strip()
            if len(title) > 200:
                await update.message.reply_text("❌ Слишком длинное описание. Максимум 200 символов. Попробуйте еще раз:")
                return TITLE
            
            user_sessions[update.effective_user.id]['title'] = title
            
            await update.message.reply_text(
                "📄 Опишите проблему подробно:\n\n"
                "Укажите все детали, которые помогут нам быстрее решить проблему"
            )
            
            return DESCRIPTION
        
        # Handle description input
        async def handle_description(update, context):
            description = update.message.text.strip()
            if len(description) < 10:
                await update.message.reply_text("❌ Слишком короткое описание. Минимум 10 символов. Попробуйте еще раз:")
                return DESCRIPTION
            
            user_sessions[update.effective_user.id]['description'] = description
            
            await update.message.reply_text(
                "🏢 Укажите ваше местоположение:\n\n"
                "Пример: 'Цех №5, кабинет 203' или 'Главный офис, 3 этаж'"
            )
            
            return LOCATION
        
        # Handle location input
        async def handle_location(update, context):
            location = update.message.text.strip()
            user_sessions[update.effective_user.id]['location'] = location
            
            await update.message.reply_text(
                "📞 Укажите ваш контактный телефон:\n\n"
                "Формат: +7 XXX XXX-XX-XX или 8 XXX XXX-XX-XX"
            )
            
            return PHONE
        
        # Handle phone input and save request
        async def handle_phone(update, context):
            phone = update.message.text.strip()
            user_data = user_sessions.get(update.effective_user.id, {})
            
            # Simple phone validation
            if len(phone) < 5:
                await update.message.reply_text("❌ Неверный формат телефона. Попробуйте еще раз:")
                return PHONE
            
            # Save to database
            session = db.get_session()
            try:
                from database.models import ITRequest, Category, Priority
                
                new_request = ITRequest(
                    user_id=update.effective_user.id,
                    username=update.effective_user.username,
                    full_name=update.effective_user.full_name,
                    category=Category(user_data.get('category', 'other')),
                    priority=Priority(user_data.get('priority', 'medium')),
                    title=user_data.get('title', ''),
                    description=user_data.get('description', ''),
                    location=user_data.get('location', ''),
                    contact_phone=phone
                )
                
                session.add(new_request)
                session.commit()
                
                # Notify admins
                await notify_admins(context, new_request)
                
                await update.message.reply_text(
                    f"✅ Заявка #{new_request.id} успешно создана!\n\n"
                    f"Мы уведомили IT-отдел. С вами свяжутся в ближайшее время.\n\n"
                    f"Для отслеживания статуса используйте меню '📋 Мои заявки'"
                )
                
            except Exception as e:
                logger.error(f"Error saving request: {e}")
                session.rollback()
                await update.message.reply_text(
                    "❌ Произошла ошибка при создании заявки. Попробуйте еще раз."
                )
            finally:
                session.close()
                # Clean up user data
                user_sessions.pop(update.effective_user.id, None)
            
            return ConversationHandler.END
        
        async def notify_admins(context, request):
            from config import BotConfig
            
            notification_text = f"""🆕 НОВАЯ ЗАЯВКА #{request.id}

👤 Сотрудник: {request.full_name}
📞 Телефон: {request.contact_phone}
🏢 Местоположение: {request.location}

📂 Категория: {request.category.value}
🚨 Приоритет: {request.priority.value}

📝 Тема: {request.title}
📄 Описание: {request.description}"""
            
            for admin_id in BotConfig().admin_ids:
                try:
                    keyboard = [[InlineKeyboardButton("📝 Просмотреть заявку", callback_data=f"admin_view_{request.id}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=notification_text,
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin_id}: {e}")
        
        # Cancel conversation
        async def cancel(update, context):
            user_sessions.pop(update.effective_user.id, None)
            await update.message.reply_text("Создание заявки отменено.")
            return ConversationHandler.END
        
        # Main button handler
        async def button_handler(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.data == 'create_request':
                await start_create_request(update, context)
            elif query.data == 'help':
                await help_command(update, context)
            elif query.data == 'my_requests':
                await show_my_requests(update, context)
            elif query.data == 'admin_panel':
                await admin_panel(update, context)
            elif query.data == 'admin_all_requests':
                await admin_all_requests(update, context)
            elif query.data == 'admin_new_requests':
                await admin_filtered_requests(update, context)
            elif query.data == 'admin_in_progress':
                await admin_filtered_requests(update, context)
            elif query.data == 'admin_detailed_stats':
                await admin_detailed_stats(update, context)
            elif query.data.startswith('admin_view_'):
                await admin_view_request(update, context)
            elif query.data.startswith('admin_take_'):
                await admin_take_request(update, context)
            elif query.data.startswith('admin_hold_'):
                await admin_update_status(update, context)
            elif query.data.startswith('admin_resolve_'):
                await admin_update_status(update, context)
            elif query.data.startswith('admin_retake_'):
                await admin_update_status(update, context)
            elif query.data.startswith('admin_close_'):
                await admin_update_status(update, context)
            elif query.data.startswith('admin_solution_'):
                await admin_add_solution(update, context)
            elif query.data.startswith('cat_'):
                await handle_category(update, context)
            elif query.data.startswith('pri_'):
                await handle_priority(update, context)
            elif query.data == 'main_menu':
                await start(update, context)
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        
        # Conversation handler for creating requests
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(handle_priority, pattern='^pri_')],
            states={
                TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title)],
                DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)],
                LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_location)],
                PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            },
            fallbacks=[CommandHandler('cancel', cancel)]
        )
        application.add_handler(conv_handler)
        
        # Button handlers
        application.add_handler(CallbackQueryHandler(button_handler))
        
        # Solution handler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_solution))
        
        print("✅ Bot initialized successfully")
        print("🔄 Starting polling...")
        
        # Start the bot
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"❌ Critical error: {e}")

if __name__ == '__main__':
    main()
