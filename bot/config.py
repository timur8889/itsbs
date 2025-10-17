import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

def get_admin_ids():
    admin_ids = os.getenv('ADMIN_IDS', '')
    return [int(x) for x in admin_ids.split(',') if x]

@dataclass
class BotConfig:
    token: str = os.getenv('BOT_TOKEN', '')
    admin_ids: list = field(default_factory=get_admin_ids)
    db_url: str = os.getenv('DATABASE_URL', 'sqlite:///it_requests.db')

class ITConfig:
    categories = {
        'hardware': '🖥️ Оборудование',
        'software': '💻 Программное обеспечение',
        'network': '🌐 Сеть и интернет',
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
