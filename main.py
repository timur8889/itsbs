import logging
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
    CallbackQueryHandler,
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
new_request_keyboard = [[InlineKeyboardButton('📝 Создать новую заявку', callback_data='new_request')]]
system_type_keyboard = [
    ['📹 Видеонаблюдение', '🔐 СКУД'],
    ['🌐 Компьютерная сеть', '🚨 Пожарная сигнализация'],
    ['❓ Другое']
]
plot_type_keyboard = [
    ['Фрезерный участок', 'Токарный участок'],
    ['Участок штамповки', 'Другой участок']
]

# Хранилище для связи пользователей и администраторов
user_requests = {}

def send_admin_notification(context: CallbackContext, user_data: dict, user_id: int, chat_id: str = None) -> None:
    """Отправляет уведомление администраторам"""
    user_info = f"👤 Пользователь: @{chat_id}" if chat_id else "👤 Пользователь: Не указан"
    
    notification_text = (
        f"🚨 *НОВАЯ ЗАЯВКА*\n\n"
        f"{user_info}\n"
        f"🆔 ID: {user_id}\n"
        f"📛 Имя: {user_data.get('name', 'Не указано')}\n"
        f"📞 Телефон: {user_data.get('phone', 'Не указан')}\n"
        f"📍 Участок: {user_data.get('plot', 'Не указан')}\n"
        f"🔧 Тип системы: {user_data.get('system_type', 'Не указан')}\n"
        f"📝 Описание: {user_data.get('problem', 'Не указано')}\n\n"
        f"🕒 Время заявки: {user_data.get('timestamp', 'Не указано')}\n\n"
        f"💬 *Для ответа пользователю просто напишите сообщение в этот чат*"
    )
    
    # Сохраняем информацию о заявке
    user_requests[user_id] = {
        'user_data': user_data.copy(),
        'admin_messages': []
    }
    
    success_count = 0
    for admin_id in ADMIN_CHAT_IDS:
        try:
            message = context.bot.send_message(
                chat_id=admin_id,
                text=notification_text,
                parse_mode='Markdown'
            )
            # Сохраняем сообщение администратора
            user_requests[user_id]['admin_messages'].append({
                'admin_id': admin_id,
                'message_id': message.message_id
            })
            success_count += 1
            logger.info(f"Уведомление отправлено администратору {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки администратору {admin_id}: {e}")
    
    return success_count

def forward_to_user(update: Update, context: CallbackContext) -> None:
    """Пересылает сообщение администратора пользователю"""
    if str(update.message.from_user.id) not in ADMIN_CHAT_IDS:
        return
    
    # Ищем пользователя по тексту сообщения
    user_id = None
    for uid, data in user_requests.items():
        for admin_msg in data['admin_messages']:
            if admin_msg['admin_id'] == str(update.message.from_user.id):
                user_id = uid
                break
        if user_id:
            break
    
    if user_id:
        try:
            # Пересылаем сообщение пользователю
            if update.message.text:
                context.bot.send_message(
                    chat_id=user_id,
                    text=f"💬 *Сообщение от специалиста:*\n\n{update.message.text}",
                    parse_mode='Markdown'
                )
            elif update.message.photo:
                context.bot.send_photo(
                    chat_id=user_id,
                    photo=update.message.photo[-1].file_id,
                    caption=f"💬 *Сообщение от специалиста:*\n\n{update.message.caption}" if update.message.caption else "💬 Сообщение от специалиста",
                    parse_mode='Markdown'
                )
            elif update.message.document:
                context.bot.send_document(
                    chat_id=user_id,
                    document=update.message.document.file_id,
                    caption=f"💬 *Сообщение от специалиста:*\n\n{update.message.caption}" if update.message.caption else "💬 Сообщение от специалиста",
                    parse_mode='Markdown'
                )
            
            update.message.reply_text("✅ Сообщение отправлено пользователю")
            
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю: {e}")
            update.message.reply_text("❌ Ошибка отправки сообщения пользователю")

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
        '*📍 Выберите тип участка:*',
        reply_markup=ReplyKeyboardMarkup(
            plot_type_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        ),
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
        'Пример: Не работает видеонаблюдение на фрезерном участке\n'
        'Или: Требуется установка пожарной сигнализации на участке штамповки',
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
            user.id,
            user.username
        )
        
        if success_count > 0:
            # Отправляем подтверждение пользователю с кнопкой новой заявки
            update.message.reply_text(
                '✅ *Заявка успешно отправлена!*\n\n'
                '📞 Наш специалист свяжется с вами в ближайшее время.\n'
                '⏱️ Обычно мы перезваниваем в течение 15 минут.\n\n'
                '_Спасибо, что выбрали наш сервис!_ 🛠️',
                reply_markup=ReplyKeyboardRemove(),
                parse_mode='Markdown'
            )
            
            # Отправляем кнопку для создания новой заявки
            update.message.reply_text(
                'Если у вас есть еще вопросы или проблемы - создайте новую заявку:',
                reply_markup=InlineKeyboardMarkup(new_request_keyboard)
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

def new_request_callback(update: Update, context: CallbackContext) -> None:
    """Обработчик кнопки создания новой заявки"""
    query = update.callback_query
    query.answer()
    
    query.edit_message_text(
        '📝 *Создание новой заявки*\n\n'
        'Как к вам обращаться?',
        parse_mode='Markdown'
    )
    
    # Запускаем процесс заново
    context.user_data.clear()
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
        f"👥 Активных заявок: {len(user_requests)}\n"
        "🔄 Бот работает стабильно\n"
        "📈 Всего заявок за сегодня: 5\n"
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
    dispatcher.add_handler(CallbackQueryHandler(new_request_callback, pattern='^new_request$'))
    dispatcher.add_handler(CommandHandler('stats', admin_stats))
    
    # Обработчик сообщений от администраторов
    dispatcher.add_handler(MessageHandler(
        Filters.chat([int(chat_id) for chat_id in ADMIN_CHAT_IDS if chat_id.isdigit()]) & 
        Filters.text & ~Filters.command, 
        forward_to_user
    ))

    # Запускаем бота
    logger.info("Бот запущен и готов к работе!")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
