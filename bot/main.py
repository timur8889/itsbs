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
    except ImportError as e:
        logger.error(f"Ошибка импорта: {e}")
        print("Проверьте структуру проекта и наличие всех файлов")
        return
    
    config = BotConfig()
    
    if not config.token:
        logger.error("BOT_TOKEN не установлен в переменных окружения")
        print("❌ Ошибка: BOT_TOKEN не найден в .env файле")
        return
    
    print(f"🤖 Запуск бота IT-отдела завода 'Контакт'...")
    print(f"🔑 Токен: {config.token[:10]}...")
    print(f"👨‍💼 Админы: {config.admin_ids}")
    print(f"💾 База данных: {config.db_url}")
    
    try:
        # Инициализация базы данных
        db = Database(config.db_url)
        print("✅ База данных инициализирована")
        
        # Создание приложения
        from telegram.ext import Application
        application = Application.builder().token(config.token).build()
        
        # Простые обработчики команд
        async def start(update, context):
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            
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
        
        # Обработчик кнопок
        async def button_handler(update, context):
            query = update.callback_query
            await query.answer()
            
            if query.data == 'create_request':
                # Простой создатель заявок
                await query.edit_message_text(
                    "📝 Для создания заявки воспользуйтесь командой /newrequest\n\n"
                    "Или дождитесь полной версии с интерактивным созданием."
                )
            
            elif query.data == 'help':
                await help_command(update, context)
            
            elif query.data == 'my_requests':
                await query.edit_message_text("📋 Функция просмотра заявок будет доступна в следующем обновлении")
            
            elif query.data == 'admin_panel':
                if query.from_user.id in config.admin_ids:
                    await query.edit_message_text(
                        "👨‍💼 Панель администратора\n\n"
                        "Доступные команды:\n"
                        "/stats - статистика заявок\n"
                        "/list - список заявок\n"
                        "Полная версия в разработке..."
                    )
                else:
                    await query.answer("❌ Нет доступа", show_alert=True)
            
            else:
                await query.edit_message_text(f"Команда: {query.data}")
        
        # Команда для создания заявки
        async def new_request(update, context):
            await update.message.reply_text(
                "📝 Создание заявки\n\n"
                "Временно используйте этот формат:\n"
                "Категория: оборудование/ПО/сеть/учетка/другое\n"
                "Проблема: краткое описание\n"
                "Место: ваш кабинет/цех\n"
                "Телефон: ваш номер\n\n"
                "Пример:\n"
                "Категория: оборудование\n"
                "Проблема: не работает мышь\n"
                "Место: цех 5, каб. 203\n"
                "Телефон: 1234"
            )
        
        # Добавляем обработчики
        from telegram.ext import CommandHandler, CallbackQueryHandler
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("newrequest", new_request))
        application.add_handler(CallbackQueryHandler(button_handler))
        
        print("✅ Бот успешно инициализирован")
        print("🔄 Запуск опроса...")
        
        # Запускаем бота
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        print(f"❌ Критическая ошибка: {e}")

if __name__ == '__main__':
    main()
