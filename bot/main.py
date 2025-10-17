import logging
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
    
    # Инициализация базы данных
    db = Database(config.db_url)
    
    # Создание приложения
    application = Application.builder().token(config.token).build()
    
    # Базовые команды
    async def start(update, context):
        user = update.effective_user
        welcome_text = f"👋 Добро пожаловать, {user.first_name}!\n\nЯ - бот IT-отдела завода 'Контакт'."
        
        keyboard = [
            [{"text": "📝 Создать заявку", "callback_data": "create_request"}],
            [{"text": "📋 Мои заявки", "callback_data": "my_requests"}],
            [{"text": "ℹ️ Справка", "callback_data": "help"}]
        ]
        
        if user.id in config.admin_ids:
            keyboard.append([{"text": "👨‍💼 Панель администратора", "callback_data": "admin_panel"}])
        
        reply_markup = {"inline_keyboard": keyboard}
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    
    # Простой обработчик для кнопки создания заявки
    async def button_handler(update, context):
        query = update.callback_query
        await query.answer()
        
        if query.data == 'create_request':
            await query.edit_message_text(
                "📂 <b>Выберите категорию проблемы:</b>",
                parse_mode='HTML'
            )
        elif query.data == 'help':
            await query.edit_message_text(
                "ℹ️ <b>Справка:</b>\n\nДля создания заявки нажмите '📝 Создать заявку'",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(f"Вы выбрали: {query.data}")
    
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("🤖 Бот IT-отдела завода 'Контакт' запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
