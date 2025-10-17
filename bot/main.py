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
        
        # States for conversation
        TITLE, DESCRIPTION, LOCATION, PHONE = range(4)
        
        # Storage for temporary request data
        user_requests = {}
        
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
        
        # Start creating request
        async def start_create_request(update, context):
            query = update.callback_query
            await query.answer()
            
            # Initialize user data
            user_requests[query.from_user.id] = {
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
            user_requests[query.from_user.id]['category'] = category
            
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
            user_requests[query.from_user.id]['priority'] = priority
            
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
            
            user_requests[update.effective_user.id]['title'] = title
            
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
            
            user_requests[update.effective_user.id]['description'] = description
            
            await update.message.reply_text(
                "🏢 Укажите ваше местоположение:\n\n"
                "Пример: 'Цех №5, кабинет 203' или 'Главный офис, 3 этаж'"
            )
            
            return LOCATION
        
        # Handle location input
        async def handle_location(update, context):
            location = update.message.text.strip()
            user_requests[update.effective_user.id]['location'] = location
            
            await update.message.reply_text(
                "📞 Укажите ваш контактный телефон:\n\n"
                "Формат: +7 XXX XXX-XX-XX или 8 XXX XXX-XX-XX"
            )
            
            return PHONE
        
        # Handle phone input and save request
        async def handle_phone(update, context):
            phone = update.message.text.strip()
            user_data = user_requests.get(update.effective_user.id, {})
            
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
                    f"Мы уведомили IT-отдел. С вами свяжутся в ближайшее время."
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
                user_requests.pop(update.effective_user.id, None)
            
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
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=notification_text
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin_id}: {e}")
        
        # Cancel conversation
        async def cancel(update, context):
            user_requests.pop(update.effective_user.id, None)
            await update.message.reply_text("Создание заявки отменено.")
            return ConversationHandler.END
        
        # Button handler
        async def button_handler(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.data == 'create_request':
                await start_create_request(update, context)
            elif query.data == 'help':
                await help_command(update, context)
            elif query.data == 'my_requests':
                await query.edit_message_text("📋 Функция просмотра заявок будет доступна в следующем обновлении")
            elif query.data == 'admin_panel':
                if query.from_user.id in config.admin_ids:
                    await query.edit_message_text("👨‍💼 Админ панель:\n\n/stats - статистика\n/list - список заявок")
                else:
                    await query.answer("❌ Нет доступа", show_alert=True)
            elif query.data.startswith('cat_'):
                await handle_category(update, context)
            elif query.data.startswith('pri_'):
                await handle_priority(update, context)
        
        # Stats command for admins
        async def stats(update, context):
            if update.effective_user.id not in config.admin_ids:
                await update.message.reply_text("❌ Нет доступа")
                return
            
            session = db.get_session()
            try:
                from database.models import ITRequest, Status
                from sqlalchemy import func
                
                total = session.query(ITRequest).count()
                new = session.query(ITRequest).filter(ITRequest.status == Status.NEW).count()
                in_progress = session.query(ITRequest).filter(ITRequest.status == Status.IN_PROGRESS).count()
                
                stats_text = f"""📊 Статистика заявок:

📋 Всего заявок: {total}
🆕 Новых: {new}
🔄 В работе: {in_progress}"""
                
                await update.message.reply_text(stats_text)
                
            except Exception as e:
                logger.error(f"Error getting stats: {e}")
                await update.message.reply_text("❌ Ошибка при получении статистики")
            finally:
                session.close()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("stats", stats))
        
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
        
        # Button handler
        application.add_handler(CallbackQueryHandler(button_handler, pattern='^(create_request|help|my_requests|admin_panel|cat_)'))
        
        print("✅ Bot initialized successfully")
        print("🔄 Starting polling...")
        
        # Start the bot
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"❌ Critical error: {e}")

if __name__ == '__main__':
    main()
