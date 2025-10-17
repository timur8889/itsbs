import logging
import os
import sys

# Добавляем текущую директорию в путь для импортов
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from config import BotConfig
from database.models import Database

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def main():
    config = BotConfig()
    
    if not config.token:
        raise ValueError("BOT_TOKEN не установлен в переменных окружения")
    
    print(f"🤖 Запуск бота с токеном: {config.token[:10]}...")
    print(f"👨‍💼 Админы: {config.admin_ids}")
    
    # Инициализация базы данных
    db = Database(config.db_url)
    print("✅ База данных инициализирована")
    
    # Создание приложения
    application = Application.builder().token(config.token).build()
    
    # Базовые команды
    async def start(update, context):
        user = update.effective_user
        welcome_text = f"""👋 Добро пожаловать, {user.first_name}!

🤖 Я - бот IT-отдела завода "Контакт". 
Я помогу вам оставить заявку на техническую поддержку.

📋 Доступные функции:
• 📝 Создание заявок в IT-отдел
• 📊 Отслеживание статуса заявок
• 📱 Удобный интерфейс
• ⚡ Быстрая реакция исполнителей

Выберите действие из меню ниже:"""
        
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        keyboard = [
            [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
            [InlineKeyboardButton("📋 Мои заявки", callback_data="my_requests")],
            [InlineKeyboardButton("ℹ️ Справка", callback_data="help")]
        ]
        
        if user.id in config.admin_ids:
            keyboard.append([InlineKeyboardButton("👨‍💼 Панель администратора", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup)
        else:
            await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup)
    
    async def help_command(update, context):
        help_text = """📖 Справка по использованию бота

Для создания заявки:
1. Нажмите "📝 Создать заявку"
2. Следуйте инструкциям бота
3. Заполните все необходимые поля

По всем вопросам обращайтесь в IT-отдел."""
        
        if update.message:
            await update.message.reply_text(help_text)
        else:
            await update.callback_query.edit_message_text(help_text)
    
    # Обработчики кнопок
    async def button_handler(update, context):
        query = update.callback_query
        await query.answer()
        
        if query.data == 'create_request':
            from handlers.requests import RequestHandlers
            request_handler = RequestHandlers(db)
            await request_handler.create_request_start(update, context)
        
        elif query.data == 'help':
            await help_command(update, context)
        
        elif query.data == 'my_requests':
            await query.edit_message_text("📋 Функция просмотра заявок в разработке")
        
        elif query.data == 'admin_panel':
            from handlers.admin import AdminHandlers
            admin_handler = AdminHandlers(db)
            await admin_handler.admin_panel(update, context)
        
        else:
            await query.edit_message_text(f"Вы выбрали: {query.data}")
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Добавляем обработчик создания заявок
    from handlers.requests import RequestHandlers
    request_handlers = RequestHandlers(db)
    application.add_handler(request_handlers.get_conversation_handler())
    
    # Добавляем админские обработчики
    from handlers.admin import AdminHandlers
    admin_handlers = AdminHandlers(db)
    for handler in admin_handlers.get_handlers():
        application.add_handler(handler)
    
    print("🤖 Бот IT-отдела завода 'Контакт' запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
