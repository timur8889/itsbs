from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from database.models import Database, Category, Priority, Status, ITRequest
from keyboards.inline import get_categories_keyboard, get_priorities_keyboard, get_main_menu, get_confirmation_keyboard
from utils.validators import format_request_text, validate_phone
import re

# Состояния для ConversationHandler
CATEGORY, PRIORITY, TITLE, DESCRIPTION, LOCATION, PHONE, CONFIRM = range(7)

class RequestHandlers:
    def __init__(self, db: Database):
        self.db = db
    
    async def create_request_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "📂 <b>Выберите категорию проблемы:</b>",
            reply_markup=get_categories_keyboard(),
            parse_mode='HTML'
        )
        return CATEGORY
    
    async def category_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        category = query.data.replace('category_', '')
        context.user_data['category'] = category
        
        await query.edit_message_text(
            "🚨 <b>Выберите приоритет заявки:</b>",
            reply_markup=get_priorities_keyboard(),
            parse_mode='HTML'
        )
        return PRIORITY
    
    async def priority_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == 'back_to_categories':
            return await self.create_request_start(update, context)
        
        priority = query.data.replace('priority_', '')
        context.user_data['priority'] = priority
        
        await query.edit_message_text(
            "📝 <b>Введите краткое описание проблемы (до 200 символов):</b>\n\n"
            "<i>Пример: 'Не работает мышь на компьютере'</i>",
            parse_mode='HTML'
        )
        return TITLE
    
    async def title_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        title = update.message.text.strip()
        if len(title) > 200:
            await update.message.reply_text(
                "❌ Описание слишком длинное. Максимум 200 символов. Попробуйте еще раз:"
            )
            return TITLE
        
        context.user_data['title'] = title
        
        await update.message.reply_text(
            "📄 <b>Опишите проблему подробно:</b>\n\n"
            "<i>Укажите все детали, которые помогут нам быстрее решить проблему</i>",
            parse_mode='HTML'
        )
        return DESCRIPTION
    
    async def description_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        description = update.message.text.strip()
        if len(description) < 10:
            await update.message.reply_text(
                "❌ Описание слишком короткое. Минимум 10 символов. Попробуйте еще раз:"
            )
            return DESCRIPTION
        
        context.user_data['description'] = description
        
        await update.message.reply_text(
            "🏢 <b>Укажите ваше местоположение:</b>\n\n"
            "<i>Пример: 'Цех №5, кабинет 203' или 'Главный офис, 3 этаж'</i>",
            parse_mode='HTML'
        )
        return LOCATION
    
    async def location_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        location = update.message.text.strip()
        context.user_data['location'] = location
        
        await update.message.reply_text(
            "📞 <b>Укажите ваш контактный телефон:</b>\n\n"
            "<i>Формат: +7 XXX XXX-XX-XX или 8 XXX XXX-XX-XX</i>",
            parse_mode='HTML'
        )
        return PHONE
    
    async def phone_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        phone = update.message.text.strip()
        
        if not validate_phone(phone):
            await update.message.reply_text(
                "❌ Неверный формат телефона. Попробуйте еще раз:\n\n"
                "<i>Пример: +7 912 345-67-89 или 8 (912) 345-67-89</i>",
                parse_mode='HTML'
            )
            return PHONE
        
        context.user_data['contact_phone'] = phone
        
        # Формируем подтверждение
        request_data = {
            'id': 'NEW',
            'category': context.user_data['category'],
            'priority': context.user_data['priority'],
            'title': context.user_data['title'],
            'description': context.user_data['description'],
            'location': context.user_data['location'],
            'contact_phone': phone,
            'full_name': update.effective_user.full_name,
            'username': update.effective_user.username or 'Не указан',
            'status': 'new',
            'created_at': 'Сейчас'
        }
        
        confirmation_text = format_request_text(request_data)
        confirmation_text += "\n\n✅ <b>Все верно? Подтвердите создание заявки:</b>"
        
        await update.message.reply_text(
            confirmation_text,
            reply_markup=get_confirmation_keyboard(),
            parse_mode='HTML'
        )
        return CONFIRM
    
    async def confirm_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == 'confirm_request':
            # Сохраняем заявку в базу данных
            session = self.db.get_session()
            try:
                new_request = ITRequest(
                    user_id=query.from_user.id,
                    username=query.from_user.username,
                    full_name=query.from_user.full_name,
                    category=Category(context.user_data['category']),
                    priority=Priority(context.user_data['priority']),
                    title=context.user_data['title'],
                    description=context.user_data['description'],
                    location=context.user_data['location'],
                    contact_phone=context.user_data['contact_phone']
                )
                
                session.add(new_request)
                session.commit()
                
                # Отправляем уведомления администраторам
                await self.notify_admins(context, new_request)
                
                await query.edit_message_text(
                    f"✅ <b>Заявка #{new_request.id} успешно создана!</b>\n\n"
                    f"Мы уведомили IT-отдел. С вами свяжутся в ближайшее время.\n"
                    f"Для отслеживания статуса используйте меню '📋 Мои заявки'",
                    parse_mode='HTML',
                    reply_markup=get_main_menu()
                )
                
            except Exception as e:
                session.rollback()
                await query.edit_message_text(
                    "❌ <b>Произошла ошибка при создании заявки.</b>\n\n"
                    "Попробуйте еще раз или обратитесь в IT-отдел напрямую.",
                    parse_mode='HTML',
                    reply_markup=get_main_menu()
                )
            finally:
                session.close()
        else:
            await query.edit_message_text(
                "❌ Создание заявки отменено.",
                reply_markup=get_main_menu()
            )
        
        # Очищаем данные пользователя
        context.user_data.clear()
        return ConversationHandler.END
    
    async def notify_admins(self, context: ContextTypes.DEFAULT_TYPE, request):
        from config import BotConfig
        from keyboards.inline import get_request_actions
        
        notification_text = f"🆕 <b>НОВАЯ ЗАЯВКА #{request.id}</b>\n\n"
        notification_text += format_request_text(request.to_dict())
        
        for admin_id in BotConfig.admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    reply_markup=get_request_actions(request.id, 'new'),
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
    
    async def cancel_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        context.user_data.clear()
        await query.edit_message_text(
            "Создание заявки отменено.",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END
    
    def get_conversation_handler(self):
        return ConversationHandler(
            entry_points=[CallbackQueryHandler(self.create_request_start, pattern='^create_request$')],
            states={
                CATEGORY: [CallbackQueryHandler(self.category_handler, pattern='^category_')],
                PRIORITY: [CallbackQueryHandler(self.priority_handler, pattern='^priority_|back_to_categories')],
                TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.title_handler)],
                DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.description_handler)],
                LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.location_handler)],
                PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.phone_handler)],
                CONFIRM: [CallbackQueryHandler(self.confirm_request, pattern='^confirm_request|cancel_request$')]
            },
            fallbacks=[CallbackQueryHandler(self.cancel_request, pattern='^cancel$')]
        )
