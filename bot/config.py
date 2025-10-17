def load_config():
    """Загрузка и проверка конфигурации"""
    from dotenv import load_dotenv
    load_dotenv()
    
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_IDS_STR = os.getenv('ADMIN_IDS', '')
    DB_URL = os.getenv('DATABASE_URL', 'sqlite:///it_requests.db')
    
    # Парсинг ADMIN_IDS
    ADMIN_IDS = []
    if ADMIN_IDS_STR:
        try:
            ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(',') if x.strip()]
            print(f"✅ Parsed ADMIN_IDS: {ADMIN_IDS}")
        except ValueError as e:
            print(f"❌ Error parsing ADMIN_IDS: {e}")
            ADMIN_IDS = []
    
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set in .env file")
    
    return BOT_TOKEN, ADMIN_IDS, DB_URL
