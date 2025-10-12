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

# Конфигурация - ЗАМЕНИТЕ НА РЕАЛЬНЫЕ ДАННЫЕ!
ADMIN_CHAT_IDS = [5024165375]  # Замените на реальные chat_id админов (только цифры)
BOT_TOKEN = "7391146893:AAFDi7qQTWjscSeqNBueKlWXbaXK99NpnHw"  # Замените на реальный токен бота

# Определяем этапы разговора
NAME, PHONE, PLOT, PROBLEM, SYSTEM_TYPE, PHOTO = range(6)

# Клавиатуры
confirm_keyboard = [['✅ Подтвердить', '✏️ Изменить']]
photo_keyboard = [['📷 Добавить фото', '⏭️ Пропустить']]
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

def send_admin_notification(context: CallbackContext, user_data: dict, user_id: int, username: str = None) -> None:
    """Отправляет уведомление администраторам"""
    user_info = f"👤 Пользователь: @{username}" if username else "👤 Пользователь: Не указан"
    
    notification_text = (
        f"🚨 *НОВАЯ ЗАЯВКА*\n\n"
        f"{user_info}\n"
        f"🆔 ID: {user_id}\n"
        f"📛 Имя: {user_data.get('name', 'Не указано')}\n"
        f"📞 Телефон: {user_data.get('phone', 'Не указан')}\n"
        f"📍 Участок: {user_data.get('plot', 'Не указан')}\n"
        f"🔧 Тип системы: {user_data.get('system_type', 'Не указан')}\n"
        f"📝 Описание: {user_data.get('problem', 'Не указано')}\n"
        f"📸 Фото: {'✅ Есть' if user_data.get('photo') else '❌ Нет'}\n\n"
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
            # Если есть фото, отправляем с фото
            if user_data.get('photo'):
                message = context.bot.send_photo(
                    chat_id=admin_id,
                    photo=user_data['photo'],
                    caption=notification_text,
                    parse_mode='Markdown'
                )
            else:
                message = context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    parse_mode='Markdown'
                )
            
            # Сохраняем информацию о сообщении администратора
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
    admin_id = update.message.from_user.id
    
    # Проверяем, что отправитель - администратор
    if admin_id not in ADMIN_CHAT_IDS:
        return
    
    # Ищем пользователя по ID администратора
    user_id = None
    for uid, data in user_requests.items():
        for admin_msg in data['admin_messages']:
            if admin_msg['admin_id'] == admin_id:
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
                update.message.reply_text("✅ Сообщение отправлено пользователю")
                
            elif update.message.photo:
                context.bot.send_photo(
                    chat_id=user_id,
                    photo=update.message.photo[-1].file_id,
                    caption=f"💬 *Сообщение от специалиста:*\n\n{update.message.caption}" if update.message.caption else "💬 Сообщение от специалиста",
                    parse_mode='Markdown'
                )
                update.message.reply_text("✅ Фото отправлено пользователю")
                
            elif update.message.document:
                context.bot.send_document(
                    chat_id=user_id,
                    document=update.message.document.file_id,
                    caption=f"💬 *Сообщение от специалиста:*\n\n{update.message.caption}" if update.message.caption else "💬 Сообщение от специалиста",
                    parse_mode='Markdown'
                )
                update.message.reply_text("✅ Документ отправлен пользователю")
            
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
            update.message.reply_text("❌ Ошибка отправки сообщения пользователю")

def start(update: Update, context: CallbackContext) -> int:
    """Начинаем разговор и спрашиваем имя."""
    # Очищаем данные пользователя при старте
    context.user_data.clear()
    
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

def start_from_button(update: Update, context: CallbackContext) -> int:
    """Начинаем разговор из кнопки и спрашиваем имя."""
    query = update.callback_query
    query.answer()
    
    # Очищаем данные пользователя
    context.user_data.clear()
    
    user = query.from_user
    context.user_data['user_id'] = user.id
    context.user_data['username'] = user.username
    
    # Редактируем сообщение с кнопкой
    query.edit_message_text(
        '✏️ *Давайте создадим новую заявку!*\n\n'
        'Как к вам обращаться?',
        parse_mode='Markdown'
    )
    return NAME

def name(update: Update, context: CallbackContext) -> int:
    """Сохраняем имя и спрашиваем телефон."""
    # Определяем откуда пришло сообщение
    if update.callback_query:
        text = update.callback_query.data
        message = update.callback_query.message
    else:
        text = update.message.text
        message = update.message
    
    context.user_data['name'] = text
    
    message.reply_text(
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
    """Сохраняем описание проблемы и спрашиваем фото."""
    context.user_data['problem'] = update.message.text
    update.message.reply_text(
        '*📸 Хотите добавить фото к заявке?*\n\n'
        'Фото поможет специалисту лучше понять проблему.',
        reply_markup=ReplyKeyboardMarkup(
            photo_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        ),
        parse_mode='Markdown'
    )
    return PHOTO

def photo(update: Update, context: CallbackContext) -> int:
    """Обрабатываем фото или пропуск."""
    if update.message.text == '📷 Добавить фото':
        update.message.reply_text(
            '*📸 Отправьте фото:*\n\n'
            'Вы можете отправить одно фото.',
            reply_markup=ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )
        return PHOTO
    elif update.message.text == '⏭️ Пропустить':
        context.user_data['photo'] = None
        return show_summary(update, context)
    elif update.message.photo:
        # Сохраняем file_id самого большого фото (последний элемент в списке)
        context.user_data['photo'] = update.message.photo[-1].file_id
        update.message.reply_text(
            '✅ Фото добавлено!',
            reply_markup=ReplyKeyboardRemove()
        )
        return show_summary(update, context)
    else:
        update.message.reply_text(
            '❌ Пожалуйста, отправьте фото или используйте кнопки.',
            reply_markup=ReplyKeyboardMarkup(
                photo_keyboard, 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
        return PHOTO

def show_summary(update: Update, context: CallbackContext) -> int:
    """Показываем сводку заявки."""
    from datetime import datetime
    
    context.user_data['timestamp'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Формируем сводку
    photo_status = "✅ Есть" if context.user_data.get('photo') else "❌ Нет"
    
    summary = (
        f"📋 *Сводка заявки:*\n\n"
        f"📛 *Имя:* {context.user_data['name']}\n"
        f"📞 *Телефон:* `{context.user_data['phone']}`\n"
        f"📍 *Участок:* {context.user_data['plot']}\n"
        f"🔧 *Тип системы:* {context.user_data['system_type']}\n"
        f"📝 *Описание:* {context.user_data['problem']}\n"
        f"📸 *Фото:* {photo_status}\n"
        f"🕒 *Время:* {context.user_data['timestamp']}"
    )
    
    context.user_data['summary'] = summary
    
    # Если есть фото, отправляем сводку с фото
    if context.user_data.get('photo'):
        update.message.reply_photo(
            photo=context.user_data['photo'],
            caption=f"{summary}\n\n*Подтвердите отправку заявки или измените данные:*",
            reply_markup=ReplyKeyboardMarkup(
                confirm_keyboard, 
                one_time_keyboard=True, 
                resize_keyboard=True
            ),
            parse_mode='Markdown'
        )
    else:
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

def confirm(update: Update, context: CallbackContext) -> int:
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
        return ConversationHandler.END
    else:
        # Начинаем заново
        update.message.reply_text(
            '✏️ *Давайте начнем заполнение заново.*\n\n'
            'Как к вам обращаться?',
            reply_markup=ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )
        return NAME

def new_request_callback(update: Update, context: CallbackContext) -> int:
    """Обработчик кнопки создания новой заявки"""
    query = update.callback_query
    query.answer()
    
    # Запускаем процесс создания новой заявки
    return start_from_button(update, context)

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
    
    if user_id not in ADMIN_CHAT_IDS:
        update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return
    
    # Статистика
    total_requests = len(user_requests)
    stats_text = (
        "📊 *Статистика бота*\n\n"
        f"👥 Активных заявок: {total_requests}\n"
        f"🔄 Бот работает стабильно\n"
        f"👤 Администраторов: {len(ADMIN_CHAT_IDS)}\n"
        f"📈 Всего заявок в памяти: {total_requests}"
    )
    
    update.message.reply_text(stats_text, parse_mode='Markdown')

def main() -> None:
    """Запускаем бота."""
    # Проверяем токен
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Замените BOT_TOKEN на реальный токен.")
        return
    
    if not ADMIN_CHAT_IDS or ADMIN_CHAT_IDS[0] == "ADMIN_CHAT_ID_1":
        logger.error("❌ ID администраторов не установлены! Замените ADMIN_CHAT_IDS на реальные chat_id.")
        return
    
    try:
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Определяем обработчик разговора с правильными entry points
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', start),
                CallbackQueryHandler(new_request_callback, pattern='^new_request$')
            ],
            states={
                NAME: [MessageHandler(Filters.text & ~Filters.command, name)],
                PHONE: [MessageHandler(Filters.text & ~Filters.command, phone)],
                PLOT: [MessageHandler(Filters.text & ~Filters.command, plot)],
                SYSTEM_TYPE: [MessageHandler(Filters.text & ~Filters.command, system_type)],
                PROBLEM: [MessageHandler(Filters.text & ~Filters.command, problem)],
                PHOTO: [
                    MessageHandler(Filters.text & ~Filters.command, photo),
                    MessageHandler(Filters.photo, photo)
                ],
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            per_message=False  # Явно указываем для избежания warning
        )

        dispatcher.add_handler(conv_handler)
        dispatcher.add_handler(MessageHandler(Filters.regex('^(✅ Подтвердить|✏️ Изменить)$'), confirm))
        dispatcher.add_handler(CommandHandler('stats', admin_stats))
        
        # Обработчик сообщений от администраторов
        dispatcher.add_handler(MessageHandler(
            Filters.chat(ADMIN_CHAT_IDS) & 
            (Filters.text | Filters.photo | Filters.document) & 
            ~Filters.command, 
            forward_to_user
        ))

        # Запускаем бота
        logger.info("Бот запущен и готов к работе!")
        logger.info(f"Администраторы: {ADMIN_CHAT_IDS}")
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")

if __name__ == '__main__':
    main()
