def main() -> None:
    """Запускаем бота"""
    if not Config.BOT_TOKEN or Config.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Токен бота не установлен! Установите BOT_TOKEN в переменных окружения.")
        return
    
    try:
        updater = Updater(Config.BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Существующие обработчики разговоров...
        
        # Обработчик массовой рассылки
        broadcast_conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(Filters.regex('^(📢 Массовая рассылка)$'), start_broadcast),
            ],
            states={
                BROADCAST_AUDIENCE: [MessageHandler(Filters.text & ~Filters.command, handle_broadcast_audience)],
                BROADCAST_MESSAGE: [
                    MessageHandler(Filters.text & ~Filters.command, handle_broadcast_message),
                    MessageHandler(Filters.photo, handle_broadcast_message),
                    MessageHandler(Filters.document, handle_broadcast_message)
                ],
                BROADCAST_CONFIRM: [MessageHandler(Filters.text & ~Filters.command, confirm_broadcast)],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_broadcast),
                MessageHandler(Filters.regex('^(❌ Отменить|🔙 В админ-панель)$'), cancel_broadcast),
            ],
            allow_reentry=True
        )

        # Регистрируем обработчики...
        
        # Новые обработчики для админ-панелей
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(💻 IT админ-панель|🔧 Механика админ-панель|⚡ Электрика админ-панель)$'), 
            handle_department_admin_panel
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки IT|🔄 В работе IT|✅ Выполненные IT|📊 Статистика IT)$'), 
            handle_it_admin_requests
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки механики|🔄 В работе механики|✅ Выполненные механики|📊 Статистика механики)$'), 
            handle_mechanics_admin_requests
        ))
        
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(🆕 Новые заявки электрики|🔄 В работе электрики|✅ Выполненные электрики|📊 Статистика электрики)$'), 
            handle_electricity_admin_requests
        ))
        
        # Обработчики супер-админ панели
        dispatcher.add_handler(MessageHandler(
            Filters.regex('^(📢 Массовая рассылка|👥 Управление админами|🏢 Все заявки|📈 Общая статистика)$'), 
            handle_super_admin_menu
        ))
        
        dispatcher.add_handler(broadcast_conv_handler)

        # Запускаем бота
        logger.info("🤖 Бот заявок запущен с системой раздельных админ-панелей!")
        logger.info(f"👑 Супер-администраторы: {Config.SUPER_ADMIN_IDS}")
        logger.info(f"👥 Администраторы по отделам: {Config.ADMIN_CHAT_IDS}")
        
        updater.start_polling()
        updater.idle()

    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

def handle_super_admin_menu(update: Update, context: CallbackContext) -> None:
    """Обрабатывает меню супер-администратора"""
    text = update.message.text
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        return show_main_menu(update, context)
    
    if text == '📢 Массовая рассылка':
        return start_broadcast(update, context)
    elif text == '👥 Управление админами':
        return show_admin_management(update, context)
    elif text == '🏢 Все заявки':
        return show_all_requests(update, context)
    elif text == '📈 Общая статистика':
        return show_complete_statistics(update, context)
    elif text == '🔙 Главное меню':
        return show_main_menu(update, context)

def show_admin_management(update: Update, context: CallbackContext) -> None:
    """Показывает управление администраторами"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        update.message.reply_text("❌ Только супер-администратор может управлять админами.")
        return
    
    admin_list_text = "👥 *СПИСОК АДМИНИСТРАТОРОВ*\n\n"
    
    for department, admins in Config.ADMIN_CHAT_IDS.items():
        admin_list_text += f"🏢 *{department}:*\n"
        for admin_id in admins:
            if admin_id in Config.SUPER_ADMIN_IDS:
                admin_list_text += f"  👑 Супер-админ: {admin_id}\n"
            else:
                admin_list_text += f"  👨‍💼 Админ: {admin_id}\n"
        admin_list_text += "\n"
    
    update.message.reply_text(
        admin_list_text,
        reply_markup=ReplyKeyboardMarkup(admin_management_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_all_requests(update: Update, context: CallbackContext) -> None:
    """Показывает все заявки системы"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        update.message.reply_text("❌ Только супер-администратор может просматривать все заявки.")
        return
    
    all_requests = db.get_requests_by_filter('all', 100)
    
    if not all_requests:
        update.message.reply_text(
            "📭 В системе пока нет заявок.",
            reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True)
        )
        return
    
    # Группируем по отделам
    departments = {}
    for req in all_requests:
        dept = req['department']
        if dept not in departments:
            departments[dept] = []
        departments[dept].append(req)
    
    for department, requests in departments.items():
        update.message.reply_text(
            f"🏢 *{department} - {len(requests)} заявок:*",
            parse_mode=ParseMode.MARKDOWN
        )
        
        for req in requests:
            show_request_for_admin(update, context, req)
    
    update.message.reply_text(
        f"📊 *Всего заявок в системе: {len(all_requests)}*",
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )

def show_complete_statistics(update: Update, context: CallbackContext) -> None:
    """Показывает полную статистику системы"""
    user_id = update.message.from_user.id
    
    if not Config.is_super_admin(user_id):
        update.message.reply_text("❌ Только супер-администратор может просматривать полную статистику.")
        return
    
    all_requests = db.get_requests_by_filter('all', 1000)
    
    # Расширенная статистика
    department_stats = {}
    status_stats = {'new': 0, 'in_progress': 0, 'completed': 0}
    urgency_stats = {}
    system_type_stats = {}
    
    for req in all_requests:
        dept = req['department']
        status = req['status']
        urgency = req['urgency']
        system_type = req['system_type']
        
        department_stats[dept] = department_stats.get(dept, 0) + 1
        status_stats[status] = status_stats.get(status, 0) + 1
        urgency_stats[urgency] = urgency_stats.get(urgency, 0) + 1
        system_type_stats[system_type] = system_type_stats.get(system_type, 0) + 1
    
    stats_text = (
        "📈 *ПОЛНАЯ СТАТИСТИКА СИСТЕМЫ*\n\n"
        f"📊 *Общая статистика:*\n"
        f"• 🆕 Новых: {status_stats['new']}\n"
        f"• 🔄 В работе: {status_stats['in_progress']}\n"
        f"• ✅ Выполненных: {status_stats['completed']}\n"
        f"• 📈 Всего заявок: {len(all_requests)}\n\n"
    )
    
    stats_text += "🏢 *По отделам:*\n"
    for dept, count in sorted(department_stats.items()):
        stats_text += f"• {dept}: {count}\n"
    
    stats_text += "\n⏰ *По срочности:*\n"
    for urgency, count in sorted(urgency_stats.items()):
        stats_text += f"• {urgency}: {count}\n"
    
    stats_text += "\n🔧 *Популярные типы проблем:*\n"
    for system_type, count in sorted(system_type_stats.items(), key=lambda x: x[1], reverse=True)[:10]:
        stats_text += f"• {system_type}: {count}\n"
    
    update.message.reply_text(
        stats_text,
        reply_markup=ReplyKeyboardMarkup(super_admin_panel_keyboard, resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
