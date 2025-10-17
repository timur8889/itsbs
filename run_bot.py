#!/usr/bin/env python3
import os
import sys
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Добавляем путь к модулям
current_dir = os.path.dirname(os.path.abspath(__file__))
bot_dir = os.path.join(current_dir, 'bot')
sys.path.insert(0, current_dir)
sys.path.insert(0, bot_dir)

from bot.main import main

if __name__ == '__main__':
    print("🚀 Запуск IT Support Bot...")
    main()
