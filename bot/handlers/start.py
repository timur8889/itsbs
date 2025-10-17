from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from database.models import Database
from keyboards.inline import get_main_menu, get_admin_keyboard
from config import BotConfig

class StartHandler:
    def __init__(self, db: Database):
        self.db = db
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        welcome_text = f"""
👋 Добро пожаловать, {user.first_name}!

🤖 Я - бот IT-отдела завода "Контакт". 
Я помогу вам оставить заявку на техническую поддержку.

📋 <b>Доступные функции:</b>
• 📝 Создание заявок в IT-отдел
• 📊 Отслеживание статуса заявок
• 📱 Удобный интерфейс
• ⚡ Быстрая реакция исполнителей

Выберите действие из меню ниже:
        """
        
        keyboard = get_main_menu()
        
        # Добавляем админскую панель для администраторов
        if str(user.id) in BotConfig.admin_ids:
            keyboard = InlineKeyboardMarkup(
                keyboard.inline_keyboard + 
                [[InlineKeyboardButton("👨‍💼 Панель администратора", callback_data='admin_panel')]]
            )
        
        if update.message:
            await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode='HTML')
        else:
            await update.callback_query.edit_message_text(welcome_text, reply_markup=keyboard, parse_mode='HTML')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
📖 <b>Справка по использованию бота</b>

<b>Создание заявки:</b>
1. Нажмите "📝 Создать заявку"
2. Выберите категорию проблемы
3. Укажите приоритет
4. Заполните все необходимые поля

<b>Категории заявок:</b>
🖥️ <b>Оборудование</b> - проблемы с компьютерами, принтерами, телефонами
💻 <b>ПО</b> - установка, обновление, ошибки программ
🌐 <b>Сеть</b> - интернет, Wi-Fi, сетевые ресурсы
👤 <b>Учетные записи</b> - пароли, доступы, права
❓ <b>Другое</b> - все остальные вопросы

<b>Приоритеты:</b>
🟢 <b>Низкий</b> - некритичная проблема
🟡 <b>Средний</b> - стандартная проблема
🔴 <b>Высокий</b> - срочная проблема
💥 <b>Критический</b> - остановка работы

По всем вопросам обращайтесь в IT-отдел: 
📞 +7 (XXX) XXX-XX-XX
        """
        
        if update.message:
            await update.message.reply_text(help_text, parse_mode='HTML')
        else:
            await update.callback_query.edit_message_text(help_text, parse_mode='HTML')
        
        await self.start(update, context)
    
    def get_handlers(self):
        return [
            CommandHandler('start', self.start),
            CommandHandler('help', self.help_command),
            CallbackQueryHandler(self.start, pattern='^main_menu$')
        ]
