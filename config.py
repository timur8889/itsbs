import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class BotConfig:
    token: str = os.getenv('BOT_TOKEN')
    admin_ids: list = os.getenv('ADMIN_IDS', '').split(',')
    db_url: str = os.getenv('DATABASE_URL', 'sqlite:///it_requests.db')

@dataclass
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
