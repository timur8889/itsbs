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

# Конфигурация
ADMIN_CHAT_IDS = ["5024165375", "ADMIN_CHAT_ID_2"]  # Замените на реальные chat_id админов
BOT_TOKEN = "7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw"  # Замените на токен вашего бота

# Определяем этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE = range(5)

# Клавиатуры
confirm_keyboard = [['✅ Подтвердить', '✏️ Изменить']]
system_type_keyboard = [
    ['📹 Видеонаблюдение', '🔐 СКУД'],
    ['📞 Телефония', '🌐 Компьютерная сеть'],
    ['🎵 Аудиосистема', '🚨 Охранная сигнализация'],
    ['🏠 Домофонная система', '❓ Другое']
]

def send_admin_notification(context: CallbackContext, user_data: dict, chat_id: str = None) -> None:
    """Отправляет уведомление администраторам"""
    user_info = f"👤 Пользователь: @{chat_id}" if chat_id else "👤 Пользователь: Не указан"
    
    notification_text = (
        f"🚨 *НОВАЯ ЗАЯВКА*\n\n"
        f"{user_info}\n"
        f"📛 Имя: {user_data.get('name', 'Не указано')}\n"
        f"📞 Телефон: {user_data.get('phone', 'Не указан')}\n"
        f"📍 Участок: {user_data.get('plot', 'Не указан')}\n"
        f"🔧 Тип системы: {user_data.get('system_type', 'Не указан')}\n"
        f"📝 Описание: {user_data.get('problem', 'Не указано')}\n\n"
        f"🕒 Время заявки: {user_data.get('timestamp', 'Не указано')}"
    )
    
    success_count = 0
    for admin_id in ADMIN_CHAT_IDS:
        try:
            context.bot.send_message(
                chat_id=admin_id,
                text=notification_text,
                parse_mode='Markdown'
            )
            success_count += 1
            logger.info(f"Уведомление отправлено администратору {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки администратору {admin_id}: {e}")
    
    return success_count

def start(update: Update, context: CallbackContext) -> int:
    """Начинаем разговор и спрашиваем имя."""
    user = update.message.from_user
    context.user_data['user_id'] = user.id
    context.user_data['username'] = user.username
    
    update.message.reply_text(
        '🏠 *Добро пожаловать в сервис заявок для слаботочных систем!*\n\n'
        'Для оформления заявки нам потребуется некоторая информация.\n'
        'Заполните данные последовательно.\n\n'
        '*Как к вам обращаться?*',
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )
    return NAME

def name(update: Update, context: CallbackContext) -> int:
    """Сохраняем имя и спрашиваем телефон."""
    context.user_data['name'] = update.message.text
    update.message.reply_text(
        '*📞 Укажите ваш контактный телефон:*\n\n'
        'Пример: +7 999 123-45-67',
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )
    return PHONE

def phone(update: Update, context: CallbackContext) -> int:
    """Сохраняем телефон и спрашиваем участок."""
    context.user_data['phone'] = update.message.text
    update.message.reply_text(
        '*📍 Укажите адрес участка:*\n\n'
        'Пример: Фрезерный устасток
        'Или: Токарный участок
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )
    return PLOT

def plot(update: Update, context: CallbackContext) -> int:
    """Сохраняем участок и спрашиваем тип системы."""
    context.user_data['plot'] = update.message.text
    update.message.reply_text(
        '*🔧 Выберите тип слаботочной системы:*',
        reply_markup=ReplyKeyboardMarkup(
            system_type_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        ),
        parse_mode='Markdown'
    )
    return SYSTEM_TYPE

def system_type(update: Update, context: CallbackContext) -> int:
    """Сохраняем тип системы и спрашиваем описание проблемы."""
    context.user_data['system_type'] = update.message.text
    update.message.reply_text(
        '*📝 Опишите проблему или необходимые работы:*\n\n'
        'Пример: Не работает видеонаблюдение на входе, требуется диагностика и ремонт\n'
        'Или: Нужно установить домофонную систему на участке',
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )
    return PROBLEM

def problem(update: Update, context: CallbackContext) -> int:
    """Сохраняем описание проблемы и показываем summary."""
    from datetime import datetime
    
    context.user_data['problem'] = update.message.text
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Формируем сводку
    summary = (
        f"📋 *Сводка заявки:*\n\n"
        f"📛 *Имя:* {context.user_data['name']}\n"
        f"📞 *Телефон:* `{context.user_data['phone']}`\n"
        f"📍 *Участок:* {context.user_data['plot']}\n"
        f"🔧 *Тип системы:* {context.user_data['system_type']}\n"
        f"📝 *Описание:* {context.user_data['problem']}\n"
        f"🕒 *Время:* {context.user_data['timestamp']}"
    )
    
    context.user_data['summary'] = summary
    update.message.reply_text(
        f"{summary}\n\n"
        "*Подтвердите отправку заявки или измените данные:*",
        reply_markup=ReplyKeyboardMarkup(
            confirm_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        ),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

def confirm(update: Update, context: CallbackContext) -> None:
    """Отправляем заявку и завершаем разговор."""
    if update.message.text == '✅ Подтвердить':
        user = update.message.from_user
        
        # Отправляем уведомление администраторам
        success_count = send_admin_notification(
            context, 
            context.user_data,
            user.username
        )
        
        if success_count > 0:
            # Отправляем подтверждение пользователю
            update.message.reply_text(
                '✅ *Заявка успешно отправлена!*\n\n'
                '📞 Наш специалист свяжется с вами в ближайшее время.\n'
                '⏱️ Обычно мы перезваниваем в течение 15 минут.\n\n'
                '_Спасибо, что выбрали наш сервис!_ 🛠️',
                reply_markup=ReplyKeyboardRemove(),
                parse_mode='Markdown'
            )
            
            # Логируем успешную заявку
            logger.info(f"Новая заявка от {user.username}: {context.user_data['name']} - {context.user_data['phone']}")
        else:
            update.message.reply_text(
                '❌ *Произошла ошибка при отправке заявки.*\n\n'
                'Пожалуйста, попробуйте позже или свяжитесь с нами по телефону.',
                reply_markup=ReplyKeyboardRemove(),
                parse_mode='Markdown'
            )
        
        # Очищаем данные пользователя
        context.user_data.clear()
    else:
        # Начинаем заново
        update.message.reply_text(
            '✏️ *Давайте начнем заполнение заново.*\n\n'
            'Как к вам обращаться?',
            reply_markup=ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )
        return NAME

def cancel(update: Update, context: CallbackContext) -> int:
    """Отменяем разговор."""
    update.message.reply_text(
        '❌ *Заявка отменена.*\n\n'
        'Если потребуется помощь - обращайтесь! 👷',
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )
    context.user_data.clear()
    return ConversationHandler.END

def admin_stats(update: Update, context: CallbackContext) -> None:
    """Команда для получения статистики (только для админов)"""
    user_id = update.message.from_user.id
    
    if str(user_id) not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    # Здесь можно добавить логику для сбора статистики
    stats_text = (
        "📊 *Статистика бота*\n\n"
        "👥 Администраторы: 2\n"
        "🔄 Бот работает стабильно\n"
        "📈 За сегодня заявок: 5\n"
        "✅ Обработано: 3\n"
        "⏳ В работе: 2"
    )
    
    update.message.reply_text(stats_text, parse_mode='Markdown')

def main() -> None:
    """Запускаем бота."""
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher

    # Определяем обработчик разговора
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            NAME: [MessageHandler(Filters.text & ~Filters.command, name)],
            PHONE: [MessageHandler(Filters.text & ~Filters.command, phone)],
            PLOT: [MessageHandler(Filters.text & ~Filters.command, plot)],
            SYSTEM_TYPE: [MessageHandler(Filters.text & ~Filters.command, system_type)],
            PROBLEM: [MessageHandler(Filters.text & ~Filters.command, problem)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    dispatcher.add_handler(conv_handler)
    dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить|✏️ Изменить)$'), confirm))
    dispatcher.add_handler(CommandHandler('stats', admin_stats))

    # Запускаем бота
    logger.info("Бот запущен и готов к работе!")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
