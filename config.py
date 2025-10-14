
import os
import json
from typing import List

def get_required_env(var_name: str, default=None):
    """Получение обязательной переменной окружения"""
    value = os.getenv(var_name, default)
    if not value:
        raise ValueError(f"❌ {var_name} не установлен!")
    return value

def get_optional_env(var_name: str, default=None):
    """Получение опциональной переменной окружения"""
    return os.getenv(var_name, default)

# Обязательные настройки
BOT_TOKEN = get_required_env('BOT_TOKEN')
ADMIN_CHAT_IDS = [int(x.strip()) for x in get_required_env('ADMIN_CHAT_IDS').split(',')]

# Опциональные настройки (Google Sheets)
GOOGLE_SERVICE_ACCOUNT_JSON = get_optional_env('GOOGLE_SERVICE_ACCOUNT_JSON')
if GOOGLE_SERVICE_ACCOUNT_JSON:
    try:
        GOOGLE_CREDENTIALS = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    except json.JSONDecodeError:
        GOOGLE_CREDENTIALS = None
        logging.warning("❌ Неверный формат GOOGLE_SERVICE_ACCOUNT_JSON")
else:
    GOOGLE_CREDENTIALS = None
