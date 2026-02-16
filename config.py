import os
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# TELEGRAM БОТ
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # опционально

#OpenAI
api_key = os.getenv("OPENAI_API_KEY")
proxy_url = os.getenv("PROXY_URL")

# WEBHOOK
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://dmtr.fvds.ru/bot-webhook/")
WEBHOOK_FULL_URL = f"{WEBHOOK_URL}{TELEGRAM_TOKEN}"

# FASTAPI СЕРВЕР
FASTAPI_HOST = os.getenv("FASTAPI_HOST", "127.0.0.1")
FASTAPI_PORT = int(os.getenv("FASTAPI_PORT", "8080"))
UVICORN_WORKERS = int(os.getenv("UVICORN_WORKERS", "1"))

# БАЗА ДАННЫХ
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, 'sessions.db')

# ЛОГИРОВАНИЕ
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ПРОВЕРКА
if not TELEGRAM_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле!")