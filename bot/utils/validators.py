import re
from datetime import datetime

def validate_phone(phone: str) -> bool:
    pattern = r'^(\+7|8)?[\s\-]?\(?[489][0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}$'
    return bool(re.match(pattern, phone))

def validate_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def format_request_text(request_data: dict) -> str:
    categories = {
        'hardware': '🖥️ Оборудование',
        'software': '💻 ПО',
        'network': '🌐 Сеть',
        'account': '👤 Учетные записи',
        'other': '❓ Другое'
    }
    
    priorities = {
        'low': '🟢 Низкий',
        'medium': '🟡 Средний',
        'high': '🔴 Высокий',
        'critical': '💥 Критический'
    }
    
    statuses = {
        'new': '🆕 Новая',
        'in_progress': '🔄 В работе',
        'on_hold': '⏸️ На паузе',
        'resolved': '✅ Решена',
        'closed': '📋 Закрыта'
    }
    
    text = f"""
📋 <b>Заявка #{request_data['id']}</b>
━━━━━━━━━━━━━━━━━━━━

👤 <b>Сотрудник:</b> {request_data['full_name']}
📞 <b>Телефон:</b> {request_data.get('contact_phone', 'Не указан')}
🏢 <b>Местоположение:</b> {request_data.get('location', 'Не указано')}

📂 <b>Категория:</b> {categories[request_data['category']]}
🚨 <b>Приоритет:</b> {priorities[request_data['priority']]}
📊 <b>Статус:</b> {statuses[request_data['status']]}

📝 <b>Тема:</b> {request_data['title']}
📄 <b>Описание:</b>
{request_data['description']}
"""
    
    if request_data.get('assigned_to'):
        text += f"\n👨‍💼 <b>Исполнитель:</b> {request_data['assigned_to']}"
    
    if request_data.get('solution'):
        text += f"\n💡 <b>Решение:</b>\n{request_data['solution']}"
    
    text += f"\n⏰ <b>Создана:</b> {request_data['created_at'][:16].replace('T', ' ')}"
    
    return text
