import logging
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

# Включим логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Определяем этапы разговора
NAME, PHONE, ADDRESS, PROBLEM = range(4)

# Клавиатура для подтверждения
confirm_keyboard = [['Подтвердить', 'Изменить']]

def start(update: Update, context: CallbackContext) -> int:
    """Начинаем разговор и спрашиваем имя."""
    update.message.reply_text(
        'Добро пожаловать в сервис заявок для слаботочных систем!\n'
        'Для оформления заявки нам потребуется некоторная информация.\n\n'
        'Как к вам обращаться?',
        reply_markup=ReplyKeyboardRemove(),
    )
    return NAME

def name(update: Update, context: CallbackContext) -> int:
    """Сохраняем имя и спрашиваем телефон."""
    context.user_data['name'] = update.message.text
    update.message.reply_text(
        'Укажите ваш контактный телефон:',
        reply_markup=ReplyKeyboardRemove(),
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    """Сохраняем телефон и спрашиваем адрес."""
    context.user_data['phone'] = update.message.text
    update.message.reply_text(
        'Укажите адрес объекта:',
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADDRESS

def address(update: Update, context: CallbackContext) -> int:
    """Сохраняем адрес и спрашиваем описание проблемы."""
    context.user_data['address'] = update.message.text
    update.message.reply_text(
        'Опишите проблему или необходимые работы:',
        reply_markup=ReplyKeyboardRemove(),
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    """Сохраняем описание проблемы и показываем summary."""
    context.user_data['problem'] = update.message.text
    
    # Формируем сводку
    summary = (
        f"Новая заявка:\n\n"
        f"Имя: {context.user_data['name']}\n"
        f"Телефон: {context.user_data['phone']}\n"
        f"Адрес: {context.user_data['address']}\n"
        f"Проблема: {context.user_data['problem']}"
    )
    
    context.user_data['summary'] = summary
    update.message.reply_text(
        f"{summary}\n\n"
        "Подтвердите отправку заявки или измените данные:",
        reply_markup=ReplyKeyboardMarkup(
            confirm_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return ConversationHandler.END

def confirm(update: Update, context: CallbackContext) -> None:
    """Отправляем заявку и завершаем разговор."""
    if update.message.text == 'Подтвердить':
        # Здесь можно добавить отправку в базу данных, email или другому боту
        admin_chat_id = "5024165375"  # Замените на реальный chat_id администратора
        
        try:
            context.bot.send_message(
                chat_id=admin_chat_id,
                text=context.user_data['summary']
            )
            update.message.reply_text(
                'Заявка отправлена! С вами свяжутся в ближайшее время.',
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception as e:
            logger.error(f"Ошибка отправки заявки: {e}")
            update.message.reply_text(
                'Произошла ошибка при отправке заявки. Пожалуйста, попробуйте позже.',
                reply_markup=ReplyKeyboardRemove(),
            )
        
        # Очищаем данные пользователя
        context.user_data.clear()
    else:
        update.message.reply_text(
            'Давайте начнем заново. Введите ваше имя:',
            reply_markup=ReplyKeyboardRemove(),
        )
        return NAME

def cancel(update: Update, context: CallbackContext) -> int:
    """Отменяем разговор."""
    update.message.reply_text(
        'Заявка отменена. Если потребуется помощь - обращайтесь!',
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    """Запускаем бота."""
    # Замените "YOUR_BOT_TOKEN" на токен вашего бота
    updater = Updater("7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw")
    dispatcher = updater.dispatcher

    # Определяем обработчик разговора
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            NAME: [MessageHandler(Filters.text & ~Filters.command, name)],
            PHONE: [MessageHandler(Filters.text & ~Filters.command, phone)],
            ADDRESS: [MessageHandler(Filters.text & ~Filters.command, address)],
            PROBLEM: [MessageHandler(Filters.text & ~Filters.command, problem)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    dispatcher.add_handler(conv_handler)
    dispatcher.add_handler(MessageHandler(Filters.regex('^(Подтвердить|Изменить)$'), confirm))

    # Запускаем бота
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
