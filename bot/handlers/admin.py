from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters
from database.models import Database, Status
from keyboards.inline import get_admin_keyboard, get_request_actions
from utils.validators import format_request_text
from config import BotConfig
from sqlalchemy import func

class AdminHandlers:
    def __init__(self, db: Database):
        self.db = db
    
    def is_admin(self, user_id: int) -> bool:
        return str(user_id) in BotConfig.admin_ids
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.callback_query.answer("❌ У вас нет доступа к этой функции", show_alert=True)
            return
        
        stats_text = await self.get_stats_text()
        
        await update.callback_query.edit_message_text(
            f"👨‍💼 <b>Панель администратора IT-отдела</b>\n\n{stats_text}",
            reply_markup=get_admin_keyboard(),
            parse_mode='HTML'
        )
    
    async def get_stats_text(self) -> str:
        session = self.db.get_session()
        try:
            total = session.query(ITRequest).count()
            new = session.query(ITRequest).filter(ITRequest.status == Status.NEW).count()
            in_progress = session.query(ITRequest).filter(ITRequest.status == Status.IN_PROGRESS).count()
            resolved_today = session.query(ITRequest).filter(
                ITRequest.status == Status.RESOLVED,
                func.date(ITRequest.updated_at) == func.current_date()
            ).count()
            
            return f"""📊 <b>Статистика за сегодня:</b>
• 📋 Всего заявок: {total}
• 🆕 Новых: {new}
• 🔄 В работе: {in_progress}
• ✅ Решено сегодня: {resolved_today}"""
        
        finally:
            session.close()
    
    async def show_all_requests(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.callback_query.answer("❌ Нет доступа", show_alert=True)
            return
        
        session = self.db.get_session()
        try:
            requests = session.query(ITRequest).order_by(ITRequest.created_at.desc()).limit(10).all()
            
            if not requests:
                await update.callback_query.edit_message_text(
                    "📭 Нет заявок",
                    reply_markup=get_admin_keyboard()
                )
                return
            
            text = "📋 <b>Последние 10 заявок:</b>\n\n"
            for req in requests:
                status_icon = {
                    'new': '🆕',
                    'in_progress': '🔄', 
                    'on_hold': '⏸️',
                    'resolved': '✅',
                    'closed': '📋'
                }
                
                priority_icon = {
                    'low': '🟢',
                    'medium': '🟡',
                    'high': '🔴', 
                    'critical': '💥'
                }
                
                text += f"{status_icon[req.status.value]} #{req.id} {priority_icon[req.priority.value]} {req.title}\n"
                text += f"   👤 {req.full_name} | 🏢 {req.location}\n"
                text += f"   ⏰ {req.created_at.strftime('%d.%m %H:%M')}\n\n"
            
            await update.callback_query.edit_message_text(
                text,
                reply_markup=get_admin_keyboard()
            )
            
        finally:
            session.close()
    
    async def show_request_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.callback_query.answer("❌ Нет доступа", show_alert=True)
            return
        
        request_id = int(update.callback_query.data.split('_')[1])
        
        session = self.db.get_session()
        try:
            request = session.query(ITRequest).filter(ITRequest.id == request_id).first()
            
            if not request:
                await update.callback_query.answer("Заявка не найдена", show_alert=True)
                return
            
            text = format_request_text(request.to_dict())
            
            await update.callback_query.edit_message_text(
                text,
                reply_markup=get_request_actions(request_id, request.status.value),
                parse_mode='HTML'
            )
            
        finally:
            session.close()
    
    async def update_request_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.callback_query.answer("❌ Нет доступа", show_alert=True)
            return
        
        action, request_id = update.callback_query.data.split('_')
        request_id = int(request_id)
        
        session = self.db.get_session()
        try:
            request = session.query(ITRequest).filter(ITRequest.id == request_id).first()
            
            if not request:
                await update.callback_query.answer("Заявка не найдена", show_alert=True)
                return
            
            admin_name = update.effective_user.full_name
            
            if action == 'take':
                request.status = Status.IN_PROGRESS
                request.assigned_to = admin_name
            elif action == 'hold':
                request.status = Status.ON_HOLD
            elif action == 'resolve':
                request.status = Status.RESOLVED
            elif action == 'retake':
                request.status = Status.IN_PROGRESS
            elif action == 'close':
                request.status = Status.CLOSED
            
            session.commit()
            
            # Уведомляем пользователя
            await self.notify_user_status_change(context, request)
            
            await update.callback_query.answer(f"Статус обновлен: {request.status.value}")
            
            # Обновляем сообщение
            text = format_request_text(request.to_dict())
            await update.callback_query.edit_message_text(
                text,
                reply_markup=get_request_actions(request_id, request.status.value),
                parse_mode='HTML'
            )
            
        finally:
            session.close()
    
    async def notify_user_status_change(self, context: ContextTypes.DEFAULT_TYPE, request):
        status_messages = {
            'in_progress': f"🔄 Заявка #{request.id} взята в работу",
            'on_hold': f"⏸️ Заявка #{request.id} приостановлена", 
            'resolved': f"✅ Заявка #{request.id} решена",
            'closed': f"📋 Заявка #{request.id} закрыта"
        }
        
        message = status_messages.get(request.status.value)
        if message and request.assigned_to:
            message += f" исполнителем {request.assigned_to}"
        
        try:
            await context.bot.send_message(
                chat_id=request.user_id,
                text=message
            )
        except Exception as e:
            print(f"Не удалось уведомить пользователя {request.user_id}: {e}")
    
    async def request_solution(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.callback_query.answer("❌ Нет доступа", show_alert=True)
            return
        
        request_id = int(update.callback_query.data.split('_')[1])
        context.user_data['editing_request'] = request_id
        
        await update.callback_query.message.reply_text(
            "💡 <b>Введите решение по заявке:</b>",
            parse_mode='HTML'
        )
    
    async def save_solution(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if 'editing_request' not in context.user_data:
            return
        
        request_id = context.user_data['editing_request']
        solution = update.message.text
        
        session = self.db.get_session()
        try:
            request = session.query(ITRequest).filter(ITRequest.id == request_id).first()
            if request:
                request.solution = solution
                session.commit()
                
                await update.message.reply_text("✅ Решение сохранено")
                
                # Уведомляем пользователя
                try:
                    await context.bot.send_message(
                        chat_id=request.user_id,
                        text=f"💡 По вашей заявке #{request_id} добавлено решение:\n\n{solution}"
                    )
                except Exception as e:
                    print(f"Не удалось уведомить пользователя: {e}")
            
        finally:
            session.close()
            context.user_data.pop('editing_request', None)
    
    def get_handlers(self):
        return [
            CallbackQueryHandler(self.admin_panel, pattern='^admin_panel$'),
            CallbackQueryHandler(self.show_all_requests, pattern='^admin_all_requests$'),
            CallbackQueryHandler(self.show_request_detail, pattern='^detail_'),
            CallbackQueryHandler(self.update_request_status, pattern='^(take|hold|resolve|retake|close)_'),
            CallbackQueryHandler(self.request_solution, pattern='^solution_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.save_solution)
        ]
