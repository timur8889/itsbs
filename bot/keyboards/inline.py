from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import ITConfig

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("📝 Создать заявку", callback_data='create_request')],
        [InlineKeyboardButton("📋 Мои заявки", callback_data='my_requests')],
        [InlineKeyboardButton("ℹ️ Справка", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_categories_keyboard():
    keyboard = []
    for key, value in ITConfig.categories.items():
        keyboard.append([InlineKeyboardButton(value, callback_data=f'category_{key}')])
    return InlineKeyboardMarkup(keyboard)

def get_priorities_keyboard():
    keyboard = []
    for key, value in ITConfig.priorities.items():
        keyboard.append([InlineKeyboardButton(value, callback_data=f'priority_{key}')])
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data='back_to_categories')])
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Все заявки", callback_data='admin_all_requests')],
        [InlineKeyboardButton("🆕 Новые заявки", callback_data='admin_new_requests')],
        [InlineKeyboardButton("🔄 В работе", callback_data='admin_in_progress')],
        [InlineKeyboardButton("📈 Статистика", callback_data='admin_stats')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_request_actions(request_id, current_status):
    keyboard = []
    
    if current_status == 'new':
        keyboard.append([InlineKeyboardButton("🔄 Взять в работу", callback_data=f'take_{request_id}')])
    
    if current_status in ['new', 'in_progress']:
        keyboard.append([InlineKeyboardButton("⏸️ На паузу", callback_data=f'hold_{request_id}')])
        keyboard.append([InlineKeyboardButton("✅ Решено", callback_data=f'resolve_{request_id}')])
    
    if current_status in ['on_hold', 'resolved']:
        keyboard.append([InlineKeyboardButton("🔄 Вернуть в работу", callback_data=f'retake_{request_id}')])
    
    if current_status == 'resolved':
        keyboard.append([InlineKeyboardButton("📋 Закрыть", callback_data=f'close_{request_id}')])
    
    keyboard.append([InlineKeyboardButton("✏️ Добавить решение", callback_data=f'solution_{request_id}')])
    keyboard.append([InlineKeyboardButton("📋 Назад к списку", callback_data='admin_all_requests')])
    
    return InlineKeyboardMarkup(keyboard)
