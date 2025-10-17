async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - главное меню"""
    user = update.effective_user
    
    # Детальная проверка администратора
    is_admin_user = is_admin(user.id, ADMIN_IDS)
    print(f"👤 User: {user.id} ({user.full_name}), Admin: {is_admin_user}")
    
    welcome_text = f"""👋 Добро пожаловать, {user.first_name}!

🤖 Я - бот IT-отдела завода "Контакт". 
Я помогу вам оставить заявку на техническую поддержку.

Выберите действие:"""
    
    keyboard = [
        [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
        [InlineKeyboardButton("📋 Мои заявки", callback_data="my_requests")],
        [InlineKeyboardButton("ℹ️ Справка", callback_data="help")]
    ]
    
    # Добавляем кнопку админ-панели только для администраторов
    if is_admin_user:
        keyboard.append([InlineKeyboardButton("👨‍💼 Админ панель", callback_data="admin_panel")])
        print(f"✅ Admin panel button ADDED for user {user.id}")
    else:
        print(f"❌ Admin panel button NOT added for user {user.id}")
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup)
