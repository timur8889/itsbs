import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from config import BotConfig
from database.models import Database
from handlers.start import StartHandler
from handlers.requests import RequestHandlers
from handlers.admin import AdminHandlers

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ITSupportBot:
    def __init__(self, token: str, db_url: str):
        self.application = Application.builder().token(token).build()
        self.db = Database(db_url)
        
        # Инициализация обработчиков
        self.start_handler = StartHandler(self.db)
        self.request_handlers = RequestHandlers(self.db)
        self.admin_handlers = AdminHandlers(self.db)
        
        self.setup_handlers()
    
    def setup_handlers(self):
        # Базовые команды
        for handler in self.start_handler.get_handlers():
            self.application.add_handler(handler)
        
        # Обработка заявок
        self.application.add_handler(
            self.request_handlers.get_conversation_handler()
        )
        
        # Админские обработчики
        for handler in self.admin_handlers.get_handlers():
            self.application.add_handler(handler)
        
        # Обработка ошибок
        self.application.add_error_handler(self.error_handler)
    
    async def error_handler(self, update: object, context):
        logger.error(msg="Exception while handling an update:", exc_info=context.error)
    
    def run(self):
        self.application.run_polling()

def main():
    config = BotConfig()
    
    if not config.token:
        raise ValueError("BOT_TOKEN не установлен в переменных окружения")
    
    bot = ITSupportBot(config.token, config.db_url)
    print("🤖 Бот IT-отдела завода 'Контакт' запущен...")
    bot.run()

if __name__ == '__main__':
    main()
