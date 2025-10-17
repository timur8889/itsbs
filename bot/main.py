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

        # ADMIN PANEL FUNCTIONS

        # Admin panel main menu
        async def admin_panel(update, context):
            query = update.callback_query
            await query.answer()
            
            user_id = query.from_user.id
            if user_id not in config.admin_ids:
                await query.answer("❌ Нет доступа к админ панели", show_alert=True)
                return
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                from sqlalchemy import func
                
                # Get statistics
                total = session.query(ITRequest).count()
                new = session.query(ITRequest).filter(ITRequest.status == Status.NEW).count()
                in_progress = session.query(ITRequest).filter(ITRequest.status == Status.IN_PROGRESS).count()
                resolved_today = session.query(ITRequest).filter(
                    ITRequest.status == Status.RESOLVED,
                    func.date(ITRequest.updated_at) == func.current_date()
                ).count()
                
                stats_text = f"""👨‍💼 Панель администратора IT-отдела

📊 Статистика за сегодня:
• 📋 Всего заявок: {total}
• 🆕 Новых: {new}
• 🔄 В работе: {in_progress}
• ✅ Решено сегодня: {resolved_today}

Выберите действие:"""
                
                keyboard = [
                    [InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests")],
                    [InlineKeyboardButton("🆕 Новые заявки", callback_data="admin_new_requests")],
                    [InlineKeyboardButton("🔄 В работе", callback_data="admin_in_progress")],
                    [InlineKeyboardButton("📈 Детальная статистика", callback_data="admin_detailed_stats")],
                    [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(stats_text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error in admin panel: {e}")
                await query.edit_message_text("❌ Ошибка при загрузке админ панели")
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
                ).limit(15).all()
                
                if not requests:
                    await query.edit_message_text(
                        "📭 Нет заявок в системе",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")
                        ]])
                    )
                    return
                
                text = "📋 Последние заявки в системе:\n\n"
                
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
                    
                    text += f"{status_icon}{priority_icon} #{req.id}: {req.title}\n"
                    text += f"   👤 {req.full_name} | 🏢 {req.location or 'Не указано'}\n"
                    text += f"   🕐 {req.created_at.strftime('%d.%m %H:%M')}\n\n"
                
                # Create buttons for first 5 requests
                keyboard = []
                for req in requests[:5]:
                    keyboard.append([
                        InlineKeyboardButton(f"📝 #{req.id} - {req.title[:15]}...", 
                                          callback_data=f"admin_view_{req.id}")
                    ])
                
                keyboard.extend([
                    [InlineKeyboardButton("🆕 Только новые", callback_data="admin_new_requests")],
                    [InlineKeyboardButton("🔄 Только в работе", callback_data="admin_in_progress")],
                    [InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")]
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, reply_markup=reply_markup)
                
            except Exception as e:
                logger.error(f"Error getting all requests: {e}")
                await query.edit_message_text("❌ Ошибка при загрузке заявок")
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
                    await query.answer("❌ Заявка не найдена", show_alert=True)
                    return
                
                # Format request details
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
                
                category_names = {
                    'hardware': '🖥️ Оборудование',
                    'software': '💻 ПО',
                    'network': '🌐 Сеть',
                    'account': '👤 Учетные записи',
                    'other': '❓ Другое'
                }
                
                text = f"""📋 ЗАЯВКА #{request.id}
━━━━━━━━━━━━━━━━━━━━

👤 <b>Сотрудник:</b> {request.full_name}
📱 <b>Username:</b> @{request.username or 'не указан'}
📞 <b>Телефон:</b> {request.contact_phone}
🏢 <b>Местоположение:</b> {request.location or 'Не указано'}

📂 <b>Категория:</b> {category_names.get(request.category.value, request.category.value)}
🚨 <b>Приоритет:</b> {priority_icons.get(request.priority.value)} {request.priority.value}
📊 <b>Статус:</b> {status_icons.get(request.status.value)} {request.status.value}

📝 <b>Тема:</b> {request.title}
📄 <b>Описание:</b>
{request.description}"""
                
                if request.assigned_to:
                    text += f"\n\n👨‍💼 <b>Исполнитель:</b> {request.assigned_to}"
                
                if request.solution:
                    text += f"\n\n💡 <b>Решение:</b>\n{request.solution}"
                
                text += f"\n\n⏰ <b>Создана:</b> {request.created_at.strftime('%d.%m.%Y %H:%M')}"
                if request.updated_at != request.created_at:
                    text += f"\n✏️ <b>Обновлена:</b> {request.updated_at.strftime('%d.%m.%Y %H:%M')}"
                
                # Create action buttons based on current status
                keyboard = []
                
                if request.status.value == 'new':
                    keyboard.append([
                        InlineKeyboardButton("🔄 Взять в работу", callback_data=f"admin_take_{request.id}")
                    ])
                
                if request.status.value in ['new', 'in_progress']:
                    keyboard.append([
                        InlineKeyboardButton("⏸️ На паузу", callback_data=f"admin_hold_{request.id}"),
                        InlineKeyboardButton("✅ Решено", callback_data=f"admin_resolve_{request.id}")
                    ])
                
                if request.status.value in ['on_hold', 'resolved']:
                    keyboard.append([
                        InlineKeyboardButton("🔄 Вернуть в работу", callback_data=f"admin_retake_{request.id}")
                    ])
                
                if request.status.value == 'resolved':
                    keyboard.append([
                        InlineKeyboardButton("📋 Закрыть", callback_data=f"admin_close_{request.id}")
                    ])
                
                keyboard.append([
                    InlineKeyboardButton("✏️ Добавить решение", callback_data=f"admin_solution_{request.id}")
                ])
                keyboard.append([
                    InlineKeyboardButton("📋 Все заявки", callback_data="admin_all_requests"),
                    InlineKeyboardButton("🔙 В админку", callback_data="admin_panel")
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
                
            except Exception as e:
                logger.error(f"Error viewing request {request_id}: {e}")
                await query.edit_message_text("❌ Ошибка при загрузке заявки")
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
                            text=f"🔄 Ваша заявка #{request_id} взята в работу\n\nИсполнитель: {query.from_user.full_name}"
                        )
                    except Exception as e:
                        logger.error(f"Could not notify user: {e}")
                    
                    await query.answer("✅ Заявка взята в работу")
                    # Refresh the request view
                    await admin_view_request(update, context)
                else:
                    await query.answer("❌ Заявка не найдена", show_alert=True)
                    
            except Exception as e:
                logger.error(f"Error taking request: {e}")
                session.rollback()
                await query.answer("❌ Ошибка при взятии заявки", show_alert=True)
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
            
            status_messages = {
                'hold': "⏸️ приостановлена",
                'resolve': "✅ решена", 
                'retake': "🔄 возвращена в работу",
                'close': "📋 закрыта"
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
                    try:
                        await context.bot.send_message(
                            chat_id=request.user_id,
                            text=f"📢 Ваша заявка #{request_id} {status_messages[action]}"
                        )
                    except Exception as e:
                        logger.error(f"Could not notify user: {e}")
                    
                    await query.answer(f"✅ Статус обновлен: {new_status}")
                    # Refresh the request view
                    await admin_view_request(update, context)
                else:
                    await query.answer("❌ Ошибка обновления статуса", show_alert=True)
                    
            except Exception as e:
                logger.error(f"Error updating status: {e}")
                session.rollback()
                await query.answer("❌ Ошибка при обновлении статуса", show_alert=True)
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
            context.user_data['editing_solution_for'] = request_id
            
            await query.message.reply_text(
                "💡 Введите решение по заявке:"
            )

        # Save solution
        async def save_solution(update, context):
            if 'editing_solution_for' not in context.user_data:
                return
            
            request_id = context.user_data['editing_solution_for']
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
                    context.user_data.pop('editing_solution_for', None)
                else:
                    await update.message.reply_text("❌ Заявка не найдена")
                    
            except Exception as e:
                logger.error(f"Error saving solution: {e}")
                session.rollback()
                await update.message.reply_text("❌ Ошибка при сохранении решения")
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
                from database.models import ITRequest, Status, Category, Priority
                from sqlalchemy import func
                from datetime import datetime, date, timedelta
                
                # Basic counts
                total = session.query(ITRequest).count()
                new = session.query(ITRequest).filter(ITRequest.status == Status.NEW).count()
                in_progress = session.query(ITRequest).filter(ITRequest.status == Status.IN_PROGRESS).count()
                resolved = session.query(ITRequest).filter(ITRequest.status == Status.RESOLVED).count()
                closed = session.query(ITRequest).filter(ITRequest.status == Status.CLOSED).count()
                
                # Today's stats
                today = date.today()
                today_requests = session.query(ITRequest).filter(
                    func.date(ITRequest.created_at) == today
                ).count()
                today_resolved = session.query(ITRequest).filter(
                    func.date(ITRequest.updated_at) == today,
                    ITRequest.status.in_([Status.RESOLVED, Status.CLOSED])
                ).count()
                
                # This week stats
                week_ago = today - timedelta(days=7)
                week_requests = session.query(ITRequest).filter(
                    ITRequest.created_at >= week_ago
                ).count()
                
                # Category stats
                category_stats = []
                for category in Category:
                    count = session.query(ITRequest).filter(ITRequest.category == category).count()
                    if count > 0:
                        category_name = {
                            'hardware': '🖥️ Оборудование',
                            'software': '💻 ПО',
                            'network': '🌐 Сеть', 
                            'account': '👤 Учетные записи',
                            'other': '❓ Другое'
                        }.get(category.value, category.value)
                        category_stats.append(f"• {category_name}: {count}")
                
                # Priority stats
                priority_stats = []
                for priority in Priority:
                    count = session.query(ITRequest).filter(ITRequest.priority == priority).count()
                    if count > 0:
                        priority_name = {
                            'low': '🟢 Низкий',
                            'medium': '🟡 Средний',
                            'high': '🔴 Высокий',
                            'critical': '💥 Критический'
                        }.get(priority.value, priority.value)
                        priority_stats.append(f"• {priority_name}: {count}")
                
                stats_text = f"""📈 Детальная статистика IT-отдела

📊 Общая статистика:
• 📋 Всего заявок: {total}
• 🆕 Новых: {new}
• 🔄 В работе: {in_progress}
• ✅ Решено: {resolved}
• 📋 Закрыто: {closed}

📅 За последнее время:
• 📥 Сегодня: {today_requests} новых, {today_resolved} решено
• 📈 За неделю: {week_requests} заявок

📂 Распределение по категориям:
{chr(10).join(category_stats)}

🚨 Распределение по приоритетам:
{chr(10).join(priority_stats)}"""
                
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

        # Admin: Filtered requests
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
            
            filter_names = {
                'new': '🆕 Новые',
                'in_progress': '🔄 В работе'
            }
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                
                if filter_type in status_map:
                    requests = session.query(ITRequest).filter(
                        ITRequest.status == Status(status_map[filter_type])
                    ).order_by(ITRequest.created_at.desc()).limit(20).all()
                    
                    text = f"{filter_names[filter_type]} заявки:\n\n"
                    
                    if not requests:
                        text += "Заявок нет"
                    else:
                        for req in requests:
                            status_icons = {'new': '🆕', 'in_progress': '🔄'}
                            priority_icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴', 'critical': '💥'}
                            
                            text += f"{status_icons[req.status.value]} {priority_icons[req.priority.value]} #{req.id}: {req.title}\n"
                            text += f"   👤 {req.full_name} | 🏢 {req.location or 'Не указано'}\n"
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
                await query.edit_message_text("❌ Ошибка при загрузке заявок")
            finally:
                session.close()

        # REQUEST CREATION SYSTEM

        # Conversation states
        CATEGORY, PRIORITY, TITLE, DESCRIPTION, LOCATION, PHONE = range(6)
        
        # Start creating request
        async def start_create_request(update, context):
            query = update.callback_query
            await query.answer()
            
            # Initialize user data
            user_id = query.from_user.id
            user_sessions[user_id] = {
                'step': 'category',
                'category': None,
                'priority': None,
                'title': None,
                'description': None,
                'location': None,
                'contact_phone': None
            }
            
            # Ask for category
            keyboard = [
                [InlineKeyboardButton("🖥️ Оборудование", callback_data="cat_hardware")],
                [InlineKeyboardButton("💻 ПО", callback_data="cat_software")],
                [InlineKeyboardButton("🌐 Сеть", callback_data="cat_network")],
                [InlineKeyboardButton("👤 Учетные записи", callback_data="cat_account")],
                [InlineKeyboardButton("❓ Другое", callback_data="cat_other")],
                [InlineKeyboardButton("🔙 Отмена", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "📂 Выберите категорию проблемы:",
                reply_markup=reply_markup
            )
            return CATEGORY

        # Handle category selection
        async def handle_category(update, context):
            query = update.callback_query
            await query.answer()
            
            user_id = query.from_user.id
            if user_id not in user_sessions:
                await query.edit_message_text("❌ Сессия истекла. Начните заново.")
                return ConversationHandler.END
            
            category = query.data.replace('cat_', '')
            user_sessions[user_id]['category'] = category
            user_sessions[user_id]['step'] = 'priority'
            
            # Ask for priority
            keyboard = [
                [InlineKeyboardButton("🟢 Низкий", callback_data="pri_low")],
                [InlineKeyboardButton("🟡 Средний", callback_data="pri_medium")],
                [InlineKeyboardButton("🔴 Высокий", callback_data="pri_high")],
                [InlineKeyboardButton("💥 Критический", callback_data="pri_critical")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_categories")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "🚨 Выберите приоритет заявки:",
                reply_markup=reply_markup
            )
            return PRIORITY

        # Handle priority selection
        async def handle_priority(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.data == 'back_to_categories':
                return await start_create_request(update, context)
            
            user_id = query.from_user.id
            if user_id not in user_sessions:
                await query.edit_message_text("❌ Сессия истекла. Начните заново.")
                return ConversationHandler.END
            
            priority = query.data.replace('pri_', '')
            user_sessions[user_id]['priority'] = priority
            user_sessions[user_id]['step'] = 'title'
            
            await query.edit_message_text(
                "📝 Введите краткое описание проблемы (максимум 200 символов):\n\n"
                "Пример: 'Не работает мышь на компьютере'"
            )
            return TITLE

        # Handle title input
        async def handle_title(update, context):
            user_id = update.effective_user.id
            if user_id not in user_sessions:
                await update.message.reply_text("❌ Сессия истекла. Начните заново.")
                return ConversationHandler.END
            
            title = update.message.text.strip()
            if len(title) > 200:
                await update.message.reply_text("❌ Слишком длинное описание. Максимум 200 символов. Попробуйте еще раз:")
                return TITLE
            
            user_sessions[user_id]['title'] = title
            user_sessions[user_id]['step'] = 'description'
            
            await update.message.reply_text(
                "📄 Опишите проблему подробно:\n\n"
                "Укажите все детали, которые помогут нам быстрее решить проблему"
            )
            return DESCRIPTION

        # Handle description input
        async def handle_description(update, context):
            user_id = update.effective_user.id
            if user_id not in user_sessions:
                await update.message.reply_text("❌ Сессия истекла. Начните заново.")
                return ConversationHandler.END
            
            description = update.message.text.strip()
            if len(description) < 10:
                await update.message.reply_text("❌ Слишком короткое описание. Минимум 10 символов. Попробуйте еще раз:")
                return DESCRIPTION
            
            user_sessions[user_id]['description'] = description
            user_sessions[user_id]['step'] = 'location'
            
            await update.message.reply_text(
                "🏢 Укажите ваше местоположение:\n\n"
                "Пример: 'Цех №5, кабинет 203' или 'Главный офис, 3 этаж'"
            )
            return LOCATION

        # Handle location input
        async def handle_location(update, context):
            user_id = update.effective_user.id
            if user_id not in user_sessions:
                await update.message.reply_text("❌ Сессия истекла. Начните заново.")
                return ConversationHandler.END
            
            location = update.message.text.strip()
            user_sessions[user_id]['location'] = location
            user_sessions[user_id]['step'] = 'phone'
            
            await update.message.reply_text(
                "📞 Укажите ваш контактный телефон:\n\n"
                "Формат: +7 XXX XXX-XX-XX или 8 XXX XXX-XX-XX"
            )
            return PHONE

        # Handle phone input and save request
        async def handle_phone(update, context):
            user_id = update.effective_user.id
            if user_id not in user_sessions:
                await update.message.reply_text("❌ Сессия истекла. Начните заново.")
                return ConversationHandler.END
            
            phone = update.message.text.strip()
            
            # Simple phone validation
            if len(phone) < 5:
                await update.message.reply_text("❌ Неверный формат телефона. Попробуйте еще раз:")
                return PHONE
            
            user_data = user_sessions[user_id]
            user_data['contact_phone'] = phone
            
            # Save to database
            session = db.get_session()
            try:
                from database.models import ITRequest, Category, Priority
                
                new_request = ITRequest(
                    user_id=user_id,
                    username=update.effective_user.username,
                    full_name=update.effective_user.full_name,
                    category=Category(user_data['category']),
                    priority=Priority(user_data['priority']),
                    title=user_data['title'],
                    description=user_data['description'],
                    location=user_data['location'],
                    contact_phone=user_data['contact_phone']
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
                user_sessions.pop(user_id, None)
            
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
            user_id = update.effective_user.id
            user_sessions.pop(user_id, None)
            await update.message.reply_text("❌ Создание заявки отменено.")
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
            elif query.data.startswith('admin_hold_') or query.data.startswith('admin_resolve_') or query.data.startswith('admin_retake_') or query.data.startswith('admin_close_'):
                await admin_update_status(update, context)
            elif query.data.startswith('admin_solution_'):
                await admin_add_solution(update, context)
            elif query.data.startswith('cat_'):
                await handle_category(update, context)
            elif query.data.startswith('pri_'):
                await handle_priority(update, context)
            elif query.data == 'main_menu':
                await start(update, context)
            elif query.data == 'back_to_categories':
                await start_create_request(update, context)

        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("admin", admin_panel))
        
        # Conversation handler for creating requests
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(handle_category, pattern='^cat_')],
            states={
                CATEGORY: [CallbackQueryHandler(handle_category, pattern='^cat_')],
                PRIORITY: [CallbackQueryHandler(handle_priority, pattern='^pri_|back_to_categories')],
                TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title)],
                DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)],
                LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_location)],
                PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel),
                CallbackQueryHandler(cancel, pattern='^main_menu$')
            ]
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
