import telebot
import config
import datetime
import sqlite3
import uvicorn
import json
import pytz
import re
import threading
import time
import numpy as np
import asyncio
import threading
import requests
import secrets
from fastapi import Response
from fastapi.responses import RedirectResponse
from datetime import timedelta
from prompts import RECOMMENDATION_CORRECTION_PROMPT
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from embedding_service import embedding_service
from openai import call_openai, voice_openai 
from telebot import types
from config import DATABASE_PATH
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sessions import session_storage
from prompts import SYSTEM_PROMPT, FOOD_ANALYSIS_SYSTEM_PROMPT, BODY_ANALYSIS_PROMPT
from prompts import SPORTS_NUTRITION_PROMPT
from prompts import SPORTS_NUTRITION_CALCULATION_PROMPT
from prompts import SPORTS_NUTRITION_PROMPT

TOKEN = config.TELEGRAM_TOKEN
WEBHOOK_URL = config.WEBHOOK_FULL_URL
FASTAPI_HOST = config.FASTAPI_HOST
FASTAPI_PORT = config.FASTAPI_PORT
bot = telebot.TeleBot(TOKEN)
MSK = pytz.timezone('Europe/Moscow')

#Инициализация бота
app = FastAPI()
templates = Jinja2Templates(directory="/var/www/dmtr.fvds.ru")



# Глобальный словарь для отслеживания режима редактирования
editing_users = {}

def reset_editing_mode(user_id):
    """Сбрасывает режим редактирования для пользователя"""
    if user_id in editing_users:
        del editing_users[user_id]

def download_file_with_retry(url, max_retries=5, timeout=60):
    """Скачивает файл с повторными попытками и таймаутом"""
    session = requests.Session()
    
    # Настройка повторных попыток
    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    for attempt in range(max_retries):
        try:
            print(f"📥 Попытка скачивания {attempt + 1}/{max_retries}...")
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            print(f"✅ Файл успешно скачан! ({len(response.content)} байт)")
            return response.content
        except requests.exceptions.Timeout as e:
            print(f"⏱️ Таймаут при скачивании (попытка {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                print(f"❌ Все попытки исчерпаны")
                return None
            wait_time = 3 * (attempt + 1)
            print(f"⏳ Ждём {wait_time} секунд перед следующей попыткой...")
            time.sleep(wait_time)
        except requests.exceptions.ConnectionError as e:
            print(f"🔌 Ошибка соединения (попытка {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return None
            wait_time = 3 * (attempt + 1)
            print(f"⏳ Ждём {wait_time} секунд...")
            time.sleep(wait_time)
        except Exception as e:
            print(f"❌ Неизвестная ошибка: {e}")
            return None
    return None

def escape_markdown(text):
    """Экранирует специальные символы для Telegram MarkdownV2"""
    # Список символов для экранирования в MarkdownV2
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    # Экранируем каждый символ
    for char in special_chars:
        text = text.replace(char, '\\' + char)
    
    # Дополнительно обрабатываем нумерованные списки
    text = re.sub(r'^(\d+)\.', r'\1\\.', text, flags=re.MULTILINE)
    
    return text

#Обработчик команд
@bot.message_handler(commands=['start', 'старт'])
def start_message(message):
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    # Получаем сессию пользователя
    session = session_storage.get_session(user_id)
    
    if session and session['accepted_terms']:
        # Пользователь уже принял условия - показываем меню
        # Получаем время последнего визита из БД
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT last_visit_at FROM sessions WHERE telegram_id = ?', 
            (user_id,)
        )
        last_visit_result = cursor.fetchone()
        conn.close()
        
        # Определяем приветствие
        greeting = "👋 Добро пожаловать обратно!"
        if last_visit_result and last_visit_result[0]:
            try:
                last_visit = datetime.datetime.fromisoformat(last_visit_result[0].replace('Z', '+00:00'))
                hours_passed = (datetime.datetime.now() - last_visit).total_seconds() / 3600
                
                if hours_passed < 1:
                    greeting = "👋 Вы вернулись быстро! Чем займёмся?"
                elif hours_passed < 24:
                    greeting = "👋 С возвращением!"
                elif hours_passed < 168:  # 7 дней
                    greeting = "🙂 Рад снова вас видеть!"
                else:
                    greeting = "🤝 Давно не виделись! С чего начнём?"
            except:
                pass
        
        bot.send_message(message.chat.id, greeting)
        show_main_menu(message)
        return
    
    # Если пользователь не принял условия или его нет - показываем условия
    markup = types.InlineKeyboardMarkup()
    accept_btn = types.InlineKeyboardButton(
        text="✅ Принимаю условия", 
        callback_data="accept_terms"
    )
    markup.add(accept_btn)
    
    conditions = """
🤖 *FitVision — твой AI-фитнес помощник!* 🦾

Я помогу:
• 📸 *Анализировать питание* по фото еды
• 🏋️‍♂️ *Оценивать форму тела* по фото (не медицински!)
• 🎯 *Давать персональные рекомендации* по тренировкам
• 📊 *Отслеживать прогресс* по весу и метрикам

*Как это работает:*
1. Расскажешь о себе (цель, параметры)
2. Будешь отправлять фото еды и тела
3. Получать AI-анализ и советы

*Важно:* я не заменяю врача или диетолога. Все рекомендации — ориентировочные.

📜 *Условия использования:*
1. Для лиц от 18 лет
2. Данные хранятся анонимно
3. Результаты — не медицинский диагноз
    """
    
    bot.send_message(
        message.chat.id,
        conditions,
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['help', 'помощь'])
def help_message(message):
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    help_text = """
🤖 *FitVision — Команды бота*

*Основные команды:*
/start — Начать работу с ботом
/menu — Показать меню
/reset — Сбросить настройки и данные
/help — Показать это сообщение
/foodlog — История питания (последние 12 приёмов)
/sportpit — Общие советы по спортивному питанию
/mysportpit — Индивидуальный расчёт спортпита под ваши параметры
/mysporthistory — История ваших советов по спортпиту
/clearsportpit — Очистить историю советов по спортпиту  # НОВАЯ СТРОКА

*Что умеет бот:*
• 🍽 Анализ еды по фото (оценка калорийности)
• 🏋️‍♂️ Анализ фигуры по фото (не медицинский!)
• 💬 Чат с AI-тренером
• 📊 Отслеживание прогресса по весу
• 💪 Общие советы по спортивному питанию
• 📊 Индивидуальный расчёт протеина, креатина и других добавок под ваши параметры

*Как использовать:*
1. Настройте профиль через меню
2. Отправляйте фото еды или тела
3. Получайте персонализированные рекомендации
4. Отслеживайте прогресс в весе
5. Используйте /mysportpit для точного расчёта добавок
6. Используйте /mysporthistory для просмотра истории
7. Используйте /clearsportpit для очистки истории

*Важно:* Бот не заменяет врача или диетолога.
Все рекомендации носят ознакомительный характер.
    """
    
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown')    

@bot.message_handler(commands=['menu', 'меню'])
def menu_command(message):
    """Показать главное меню по команде /menu"""
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    # Проверяем, принял ли пользователь условия
    conn = sqlite3.connect(config.DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT accepted_terms FROM sessions WHERE telegram_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result or not result[0]:
        bot.send_message(
            message.chat.id,
            "❌ Сначала примите условия использования. Отправьте /start"
        )
        return
    
    # Показываем главное меню
    show_main_menu(message)

def add_back_to_menu_button(chat_id, message_text="Вернуться в меню"):
    """Добавить кнопку для возврата в меню"""
    markup = types.InlineKeyboardMarkup()
    menu_btn = types.InlineKeyboardButton('📋 Главное меню', callback_data='show_main_menu')
    markup.add(menu_btn)
    
    bot.send_message(chat_id, message_text, reply_markup=markup)

#Обработчик кнопки:
@bot.callback_query_handler(func=lambda call: call.data == "accept_terms")
def handle_accept(call):
    user_id = call.from_user.id
    update_user_activity(user_id)
    
    # Сохраняем сессию с принятыми условиями
    session_storage.save_session(
        telegram_id=user_id,
        accepted_terms=True,
        data={
            "settings": {},
            "metrics": [],
            "chats": [],
            "food_logs": []
        }
    )

    # Удаляем кнопку и показываем меню
    bot.edit_message_text(
        "✅ Отлично! Теперь можем начинать!",
        call.message.chat.id,
        call.message.message_id
    )
    
    show_main_menu(call.message)

@bot.message_handler(commands=['reset', 'сброс'])
def reset_data(message):
    user_id = message.from_user.id
    update_user_activity(user_id)
    session = session_storage.get_session(user_id)
    
    if session and session['accepted_terms']:
        # Очищаем ВСЕ данные, сохраняя accepted_terms
        session_storage.save_session(
            telegram_id=user_id,
            data={
                "settings": {},                    # Настройки профиля
                "metrics": [],                      # История веса
                "chats": [],                        # История чатов
                "food_logs": [],                    # История питания
                "body_analyses": [],                # Анализы тела
                "sport_pit_advice": [],             # История спортпитания
                "last_analyzed_photo_id": None,     # Очищаем ID последнего фото еды
                "last_analyzed_body_photo_id": None, # Очищаем ID последнего фото тела
                "last_photo_id": None,               # Очищаем ID последнего фото
                "last_analysis_time": None,          # Очищаем время последнего анализа
                "last_correction_date": None         # Очищаем дату последней коррекции
            }
        )
        
        # Показываем индикатор печати
        try:
            bot.send_chat_action(message.chat.id, 'typing')
        except:
            pass
        
        # Небольшая задержка для эффекта
        time.sleep(0.5)
        
        bot.send_message(
            message.chat.id, 
            "✅ Все данные полностью очищены!\n\n"
            "• Настройки профиля\n"
            "• История веса\n"
            "• История чатов\n"
            "• История питания\n"
            "• Анализы тела\n"
            "• Советы по спортивному питанию\n"
            "• ID последних фото\n\n"
            "Используйте /start для начала работы"
        )
    else:
        bot.send_message(message.chat.id, "❌ Сначала примите условия (/start)")

#функция главного меню:
def show_main_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    btn1 = types.InlineKeyboardButton('Настроить фитнес агента', callback_data='setup_fitness')
    btn2 = types.InlineKeyboardButton('🍽 Анализ еды по фото', callback_data='food_analysis')
    btn3 = types.InlineKeyboardButton('🏋️‍♂️ Анализ фигуры по фото', callback_data='body_analysis')
    btn4 = types.InlineKeyboardButton('📊 Проверить прогресс', callback_data='check_progress')
    btn5 = types.InlineKeyboardButton('📋 История питания', callback_data='show_foodlog')
    btn6 = types.InlineKeyboardButton('💪 Спортивное питание', callback_data='sport_pit')
    btn7 = types.InlineKeyboardButton('📊 Мой расчёт спортпита', callback_data='my_sport_pit')
    btn8 = types.InlineKeyboardButton('📋 История спортпита', callback_data='sport_history')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8)
    
    bot.send_message(message.chat.id, "Выберите опцию:", reply_markup=markup)

def ask_setup_mode(chat_id, user_id):
    """Спрашиваем режим настройки"""
    session = session_storage.get_session(user_id)
    has_settings = session and session['data'].get('settings', {})
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    if has_settings:
        btn1 = types.InlineKeyboardButton('📝 Редактировать текущие настройки', callback_data='edit_settings')
        btn2 = types.InlineKeyboardButton('🆕 Создать новые настройки', callback_data='new_settings')
        btn3 = types.InlineKeyboardButton('❌ Отмена', callback_data='show_main_menu')
        markup.add(btn1, btn2, btn3)
        text = "У вас уже есть сохранённые настройки. Что хотите сделать?"
    else:
        btn = types.InlineKeyboardButton('Создать настройки', callback_data='new_settings')
        btn_cancel = types.InlineKeyboardButton('❌ Отмена', callback_data='show_main_menu')
        markup.add(btn, btn_cancel)
        text = "Начнём настройку фитнес-агента!"
    
    bot.send_message(chat_id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    print(f"📞 CALLBACK: {call.data} from user={call.from_user.id}")
    user_id = call.from_user.id
    update_user_activity(user_id)

    # ✅ Сбрасываем режим редактирования при любом новом callback
    reset_editing_mode(user_id)
    
    if call.data == 'setup_fitness':
        # Проверяем, есть ли уже настройки у пользователя
        session = session_storage.get_session(user_id)
        has_settings = session and session['data'].get('settings', {})
        
        if not has_settings:
            # Если настроек нет - сразу начинаем создание новых
            bot.edit_message_text(
                "🆕 Создаём новые настройки!",
                call.message.chat.id,
                call.message.message_id
            )
            ask_gender(call.message.chat.id)
        else:
            # Если настройки есть - показываем выбор режима
            ask_setup_mode(call.message.chat.id, user_id)
    
    elif call.data == 'edit_settings':
        # Показываем текущие настройки и предлагаем редактировать
        session = session_storage.get_session(user_id)
        if session:
            settings = session['data'].get('settings', {})
            if settings:
                # Показываем текущие настройки
                current_settings = (
                    f"📋 *Текущие настройки:*\n\n"
                    f"• Пол: {settings.get('gender', 'не указан')}\n"
                    f"• Рост: {settings.get('height', 'не указан')} см\n"
                    f"• Возраст: {settings.get('age', 'не указан')} лет\n"
                    f"• Текущий вес: {settings.get('current_weight', 'не указан')} кг\n"
                    f"• Желаемый вес: {settings.get('goal_weight', 'не указан')} кг\n"
                    f"• Цель: {settings.get('goal', 'не указан')}\n\n"
                    f"Что хотите изменить?"
                )
                bot.edit_message_text(
                    current_settings,
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode='Markdown'
                )
                
                # Кнопки для выбора параметра для редактирования
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    types.InlineKeyboardButton('✏️ Пол', callback_data='edit_gender'),
                    types.InlineKeyboardButton('✏️ Вес', callback_data='edit_weight'),
                    types.InlineKeyboardButton('✏️ Рост', callback_data='edit_height'),
                    types.InlineKeyboardButton('✏️ Дата рождения', callback_data='edit_birthdate'),
                    types.InlineKeyboardButton('✏️ Желаемый вес', callback_data='edit_goal_weight'),
                    types.InlineKeyboardButton('✏️ Цель', callback_data='edit_goal'),
                    types.InlineKeyboardButton('❌ Отмена', callback_data='cancel_edit')
                )
                bot.send_message(call.message.chat.id, "Выберите параметр для редактирования:", reply_markup=markup)
            else:
                bot.edit_message_text(
                    "❌ У вас нет сохранённых настроек. Сначала создайте их!",
                    call.message.chat.id,
                    call.message.message_id
                )
                ask_gender(call.message.chat.id)
    
    elif call.data == 'new_settings':
        # Начинаем новую настройку с пола
        bot.edit_message_text(
            "🆕 Создаём новые настройки!",
            call.message.chat.id,
            call.message.message_id
        )
        ask_gender(call.message.chat.id)
    
    elif call.data == 'cancel_edit':
        # Отмена редактирования
        bot.edit_message_text(
            "Редактирование отменено.",
            call.message.chat.id,
            call.message.message_id
        )
        show_main_menu(call.message)
    
    elif call.data in ['gender_male', 'gender_female']:
        # Обработка выбора пола
        gender = 'мужской' if call.data == 'gender_male' else 'женский'
        
        # Сохраняем в data.settings
        session = session_storage.get_session(call.from_user.id)
        if session:
            data = session['data']
            data.setdefault('settings', {})
            data['settings']['gender'] = gender
            session_storage.save_session(call.from_user.id, data=data)
        
        # Просто отправляем новое сообщение (НЕ редактируем старое)
        bot.send_message(
            call.message.chat.id, 
            f"✅ Пол: {gender}"
        )
        msg = bot.send_message(call.message.chat.id, "Введите ваш текущий вес (кг):")
        bot.register_next_step_handler(msg, process_weight)
    
    elif call.data in ['goal_loss', 'goal_gain', 'goal_maintain']:
        # Обрабатываем выбор цели
        goal_map = {
            'goal_loss': 'похудение',
            'goal_gain': 'набор массы',
            'goal_maintain': 'поддержание веса'
        }
        
        session = session_storage.get_session(call.from_user.id)
        if session:
            data = session['data']
            data['settings']['goal'] = goal_map[call.data]
            session_storage.save_session(call.from_user.id, data=data)
        
        # Выводим итоги
        settings = data['settings']
        bot.edit_message_text(
            f"✅ Настройки сохранены!\n\n"
            f"• Пол: {settings.get('gender', 'не указан')}\n"
            f"• Рост: {settings.get('height', 'не указан')} см\n"
            f"• Возраст: {settings.get('age', 'не указан')} лет\n"
            f"• Текущий вес: {settings.get('current_weight', 'не указан')} кг\n"
            f"• Желаемый вес: {settings.get('goal_weight', 'не указан')} кг\n"
            f"• Цель: {goal_map[call.data]}\n\n"
            f"Используйте /reset для сброса настроек.",
            call.message.chat.id,
            call.message.message_id
        )
        
        # Показываем главное меню
        show_main_menu(call.message)
    
    elif call.data.startswith('edit_'):
        # Устанавливаем флаг, что мы в режиме редактирования
        session = session_storage.get_session(user_id)
        if session:
            data = session['data']
            data['editing_mode'] = True
            data['editing_parameter'] = call.data  # запоминаем какой параметр
            session_storage.save_session(user_id, data=data)
        
        # Обработка редактирования конкретных параметров
        if call.data == 'edit_gender':
            ask_gender(call.message.chat.id)
        elif call.data == 'edit_weight':
            # Устанавливаем режим редактирования веса
            editing_users[user_id] = 'weight'
            msg = bot.send_message(call.message.chat.id, "Введите новый текущий вес (кг):")
            bot.register_next_step_handler(msg, process_weight_edit)
        elif call.data == 'edit_height':
            # Устанавливаем режим редактирования роста
            editing_users[user_id] = 'height'
            msg = bot.send_message(call.message.chat.id, "Введите новый рост (см):")
            bot.register_next_step_handler(msg, process_height_edit)
        elif call.data == 'edit_birthdate':
            # Устанавливаем режим редактирования даты рождения
            editing_users[user_id] = 'birthdate'
            msg = bot.send_message(call.message.chat.id, "Введите новую дату рождения (ДД.ММ.ГГГГ):")
            bot.register_next_step_handler(msg, process_birthdate_edit)
        elif call.data == 'edit_goal_weight':
            # Устанавливаем режим редактирования желаемого веса
            editing_users[user_id] = 'goal_weight'
            msg = bot.send_message(call.message.chat.id, "Введите новый желаемый вес (кг):")
            bot.register_next_step_handler(msg, process_goal_weight_edit)
        elif call.data == 'edit_goal':
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton('Похудение', callback_data='goal_loss_edit'),
                types.InlineKeyboardButton('Набор массы', callback_data='goal_gain_edit'),
                types.InlineKeyboardButton('Поддержание веса', callback_data='goal_maintain_edit')
            )
            bot.send_message(call.message.chat.id, "Выберите новую цель:", reply_markup=markup)

    elif call.data in ['goal_loss_edit', 'goal_gain_edit', 'goal_maintain_edit']:
        goal_map = {
            'goal_loss_edit': 'похудение',
            'goal_gain_edit': 'набор массы',
            'goal_maintain_edit': 'поддержание веса'
        }
        
        session = session_storage.get_session(call.from_user.id)
        if session:
            data = session['data']
            data['settings']['goal'] = goal_map[call.data]
            
            # Снимаем флаг редактирования
            data.pop('editing_mode', None)
            data.pop('editing_parameter', None)
            
            session_storage.save_session(call.from_user.id, data=data)
        
        bot.send_message(call.message.chat.id, f"✅ Цель обновлена: {goal_map[call.data]}")
        show_main_menu(call.message)

    elif call.data == 'food_analysis':
        # Устанавливаем ожидание фото еды
        session = session_storage.get_session(call.from_user.id)
        if session:
            data = session['data']
            
            # ✅ УБИРАЕМ ПРОВЕРКУ - просто устанавливаем режим
            data['awaiting_photo_type'] = 'food'
            session_storage.save_session(call.from_user.id, data=data)
        
        bot.send_message(call.message.chat.id, "🍽 Отправьте фото еды для анализа")
        
        # Удаляем кнопки после выбора
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None
            )
        except:
            pass
        return
    
    elif call.data == 'body_analysis':
        # Устанавливаем ожидание фото тела
        session = session_storage.get_session(call.from_user.id)
        if session:
            data = session['data']
            data['awaiting_photo_type'] = 'body'
            session_storage.save_session(call.from_user.id, data=data)
        
        bot.send_message(call.message.chat.id, "🏋️ Отправьте фото для анализа фигуры")
        
        # Удаляем кнопки после выбора
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None
            )
        except:
            pass
        return
    
    elif call.data == 'cancel_photo':
        bot.edit_message_text(
            "❌ Анализ отменён",
            call.message.chat.id,
            call.message.message_id
        )

    elif call.data == 'show_main_menu':
        # Показываем главное меню
        show_main_menu(call.message)

    elif call.data == 'show_foodlog':
        print(f"🔥 ВЫЗОВ show_foodlog для user={call.from_user.id}")
        # Показать историю питания
        user_id = call.from_user.id
        update_user_activity(user_id)
        session = session_storage.get_session(user_id)
        
        if not session:
            bot.send_message(call.message.chat.id, "❌ Сначала отправьте /start")
            return
        
        if 'food_logs' not in session['data'] or not session['data']['food_logs']:
            bot.send_message(call.message.chat.id, "📭 История питания пуста")
            return
        
        # ПОСЛЕДНИЕ 12 ЗАПИСЕЙ
        food_logs = session['data']['food_logs'][-12:]
        response = "📊 🍽 ИСТОРИЯ ПИТАНИЯ\n"
        response += "══════════════════════\n\n"
        
        current_date = None
        counter = 1
        daily_calories = 0
        today = datetime.datetime.now(MSK).strftime('%d.%m.%Y')
        
        for log in food_logs:
            try:
                dt = datetime.datetime.fromisoformat(log['date'])
                if dt.tzinfo is None:
                    dt = pytz.UTC.localize(dt)
                dt = dt.astimezone(MSK)
                date = dt.strftime('%d.%m.%Y')
                time = dt.strftime('%H:%M')
            except:
                date = "??"
                time = "??"
            
            # РАЗДЕЛИТЕЛЬ ПО ДАТАМ И ПОДСЧЁТ ЗА СУТКИ
            if current_date != date:
                # Выводим итог за предыдущий день
                if current_date is not None:
                    if current_date == today:
                        response += f"📊 ИТОГО СЕГОДНЯ: {daily_calories} ккал\n"
                    else:
                        response += f"📊 ИТОГО за {current_date}: {daily_calories} ккал\n"
                    response += "══════════════════════\n\n"
                
                # Новый день
                current_date = date
                daily_calories = 0
                counter = 1
                
                if date == today:
                    response += f"📅 СЕГОДНЯ ({date})\n"
                else:
                    response += f"📅 {date}\n"
                response += "──────────────────────\n\n"
            
            # ИЗВЛЕКАЕМ ПОЛНЫЙ ТЕКСТ АНАЛИЗА И ПРЕОБРАЗУЕМ ** В HTML ТЕГИ
            analysis = log.get('analysis', '')
            if analysis:
                # Заменяем ** на HTML теги <b> и </b>
                parts = analysis.split('**')
                full_analysis = ''
                for i, part in enumerate(parts):
                    if i % 2 == 1:  # Нечетные индексы - это текст между **
                        full_analysis += f'<b>{part}</b>'
                    else:
                        full_analysis += part
            else:
                full_analysis = "❓ Анализ отсутствует"
            
            calories = log.get('calories', 0)
            if isinstance(calories, (int, float)):
                calories_val = int(calories)
                daily_calories += calories_val
            
            # ФОРМАТ ЗАПИСИ
            response += f"┌─ {counter}. ─────────────────────\n"
            response += f"│ 🕐 {time}\n"
            response += f"│ {full_analysis}\n"
            response += f"└──────────────────────────\n\n"
            
            counter += 1
        
        # ИТОГ ЗА ПОСЛЕДНИЙ ДЕНЬ
        if current_date is not None:
            if current_date == today:
                response += f"📊 ИТОГО СЕГОДНЯ: {daily_calories} ккал\n"
            else:
                response += f"📊 ИТОГО за {current_date}: {daily_calories} ккал\n"
            response += "══════════════════════\n"
        
        # Общий итог за все показанные дни
        total_calories = 0
        count = 0
        for log in food_logs:
            cal = log.get('calories')
            if isinstance(cal, (int, float)):
                total_calories += cal
                count += 1
        
        if count > 0:
            response += f"\n📊 ВСЕГО за {count} приёмов: {total_calories} ккал"
        
        # Кнопка меню
        markup = types.InlineKeyboardMarkup()
        menu_btn = types.InlineKeyboardButton('📋 Главное меню', callback_data='show_main_menu')
        markup.add(menu_btn)

        # Отправляем с HTML форматированием
        bot.send_message(call.message.chat.id, response, parse_mode='HTML', reply_markup=markup)
        return

    elif call.data == 'check_progress':
        # Проверить прогресс
        user_id = call.from_user.id
        weight_progress = session_storage.get_weight_progress(user_id, days=7)
        
        if not weight_progress.get('has_data', False):
            bot.send_message(call.message.chat.id, "📊 Недостаточно данных для анализа прогресса.")
            return
        
        report = f"📊 *Отчёт о прогрессе за 7 дней:*\n\n"
        report += f"• Начальный вес: {weight_progress['first_weight']} кг\n"
        report += f"• Текущий вес: {weight_progress['last_weight']} кг\n"
        report += f"• Изменение: {weight_progress['weight_change']:+.1f} кг\n"
        report += f"• Тренд: {_get_trend_emoji(weight_progress['trend'])}\n\n"
        report += f"💡 *Рекомендация:*\n{weight_progress['message']}"
        
        bot.send_message(call.message.chat.id, report, parse_mode='Markdown')

        # ✅ ДОБАВЛЯЕМ ПРОВЕРКУ НА ПЛАТО
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(check_for_plateau_and_correct(user_id, call.message.chat.id, bot))
        finally:
            loop.close()

    elif call.data == 'sport_pit':
        # Проверяем, заполнены ли настройки
        session = session_storage.get_session(user_id)
        if not session:
            bot.send_message(call.message.chat.id, "❌ Сначала отправьте /start")
            return
        
        settings = session['data'].get('settings', {})
        if not settings or not settings.get('goal'):
            bot.send_message(
                call.message.chat.id,
                "❌ Сначала заполните профиль через меню 'Настроить фитнес агента'"
            )
            return
        
        # Получаем данные пользователя
        user_goal = settings.get('goal', 'не указана')
        current_weight = settings.get('current_weight', 'не указан')
        goal_weight = settings.get('goal_weight', 'не указан')
        body_type = settings.get('body_type', 'среднее')
        
        # Показываем индикатор печати
        try:
            bot.send_chat_action(call.message.chat.id, 'typing')
        except:
            pass
        
        sport_prompt = SPORTS_NUTRITION_PROMPT.format(
            user_goal=user_goal,
            current_weight=current_weight,
            goal_weight=goal_weight,
            body_type=body_type
        )
        
        # Отправляем сообщение о начале анализа
        wait_msg = bot.send_message(
            call.message.chat.id, 
            "💪 Подбираю рекомендации по спортивному питанию...\n⏳ Это может занять несколько секунд"
        )
        
        # Запускаем асинхронную функцию
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(generate_sport_pit_advice_from_callback(bot, call, sport_prompt, wait_msg))
        finally:
            loop.close()

    elif call.data in ['analyze_food', 'analyze_body', 'cancel_photo']:
        if call.data == 'cancel_photo':
            bot.edit_message_text("❌ Анализ отменён", call.message.chat.id, call.message.message_id)
            return
        
        # ✅ ПОЛУЧАЕМ СВЕЖУЮ СЕССИЮ
        session = session_storage.get_session(user_id)
        if not session or 'last_photo_id' not in session['data']:
            bot.answer_callback_query(call.id, "Фото не найдено, отправьте заново")
            return
        
        photo_id = session['data']['last_photo_id']
        
        # ✅ ПРИНУДИТЕЛЬНО УДАЛЯЕМ РЕЖИМ ИЗ БД
        data = session['data']
        if data.get('awaiting_photo_type'):
            print(f"🔄 СБРАСЫВАЕМ РЕЖИМ В CALLBACK: {data['awaiting_photo_type']}")
            data.pop('awaiting_photo_type', None)
            session_storage.save_session(user_id, data=data)
            # ✅ ПЕРЕЧИТЫВАЕМ СЕССИЮ
            session = session_storage.get_session(user_id)
            data = session['data']
            print(f"✅ РЕЖИМ ПОСЛЕ СБРОСА: {data.get('awaiting_photo_type')}")
        
        # ✅ СОХРАНЯЕМ ID ФОТО ДЛЯ АНАЛИЗА (ЭТО ВАЖНО!)
        data['last_photo_id'] = photo_id
        session_storage.save_session(user_id, data=data)
        
        if call.data == 'analyze_food':
            # ✅ ИСПРАВЛЕНО: создаём временный event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(analyze_food_photo(bot, call, photo_id))
            finally:
                loop.close()
        elif call.data == 'analyze_body':
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(analyze_body_photo(bot, call, photo_id))
            finally:
                loop.close()
        return
    
    elif call.data == 'my_sport_pit':
        # Перенаправляем на команду /mysportpit
        # Создаём фиктивное сообщение для вызова функции
        class FakeMessage:
            def __init__(self, chat_id, from_user_id):
                self.chat = type('obj', (object,), {'id': chat_id})()
                self.from_user = type('obj', (object,), {'id': from_user_id})()
        
        fake_msg = FakeMessage(call.message.chat.id, user_id)
        my_sport_pit_command(fake_msg)
        return
    
    elif call.data == 'sport_history':
        class FakeMessage:
            def __init__(self, chat_id, from_user_id):
                self.chat = type('obj', (object,), {'id': chat_id})()
                self.from_user = type('obj', (object,), {'id': from_user_id})()
        
        fake_msg = FakeMessage(call.message.chat.id, user_id)
        my_sport_history(fake_msg)
        return

async def reply(bot, message_or_call):
    """Обработчик OpenAI с поддержкой как message, так и call и доступом к истории питания"""
    
    # Определяем user_id и текст в зависимости от типа
    if hasattr(message_or_call, 'data'):  # Это callback
        user_id = message_or_call.from_user.id
        user_text = "Привет! Я готов помочь с фитнес-вопросами."
        chat_id = message_or_call.message.chat.id  
    else:  # Это обычное сообщение
        user_id = message_or_call.from_user.id
        user_text = message_or_call.text
        chat_id = message_or_call.chat.id  
    
    session = session_storage.get_session(user_id)
    
    if not session:
        bot.send_message(chat_id, "❌ Сначала отправьте /start")  
        return
    
    user_data = session['data']
    chats_history = user_data.get('chats', [])
    food_logs = user_data.get('food_logs', [])
    settings = user_data.get('settings', {})
    
    # ✅ ПОКАЗЫВАЕМ ИНДИКАТОР ПЕЧАТИ
    typing_indicator = None
    try:
        typing_indicator = bot.send_chat_action(chat_id, 'typing')
    except:
        pass
    
    # ✅ ФОРМИРУЕМ КОНТЕКСТ О ПИТАНИИ
    food_context = ""
    if food_logs:
        # Берем последние 5 приёмов пищи
        recent_meals = food_logs[-5:]
        food_context = "\n\n📋 **История вашего питания (последние приёмы):**\n"
        
        for i, meal in enumerate(reversed(recent_meals), 1):
            try:
                dt = datetime.datetime.fromisoformat(meal['date'])
                if dt.tzinfo is None:
                    dt = pytz.UTC.localize(dt)
                dt = dt.astimezone(MSK)
                date = dt.strftime('%d.%m %H:%M')
            except:
                date = "неизвестно"
            
            # Извлекаем название блюда
            meal_text = meal.get('meal_text', '')
            if not meal_text:
                analysis = meal.get('analysis', '')
                if analysis:
                    first_line = analysis.split('\n')[0]
                    first_line = re.sub(r'^\d+\.\s*\*\*?', '', first_line)
                    first_line = re.sub(r'\*\*', '', first_line)
                    meal_text = first_line[:100]
                else:
                    meal_text = "блюдо"
            
            calories = meal.get('calories', '?')
            if isinstance(calories, (int, float)):
                calories = f"{int(calories)} ккал"
            else:
                calories = "калории не указаны"
            
            food_context += f"  {i}. {date} — {meal_text} ({calories})\n"
    
    # ✅ КОНТЕКСТ ОБ АНАЛИЗАХ ТЕЛА
    body_context = ""
    body_analyses = user_data.get('body_analyses', [])
    if body_analyses:
        # Берем последние 3 анализа тела
        recent_body = body_analyses[-3:]
        body_context = "\n\n📋 **История анализов вашего тела (последние):**\n"
        
        for i, body in enumerate(reversed(recent_body), 1):
            try:
                dt = datetime.datetime.fromisoformat(body['date'])
                if dt.tzinfo is None:
                    dt = pytz.UTC.localize(dt)
                dt = dt.astimezone(MSK)
                date = dt.strftime('%d.%m %H:%M')
            except:
                date = "неизвестно"
            
            # Берем первую строку анализа
            analysis_lines = body['analysis'].split('\n')
            first_line = analysis_lines[0] if analysis_lines else "Анализ тела"
            first_line = re.sub(r'^\d+\.\s*\*\*?', '', first_line)
            first_line = re.sub(r'\*\*', '', first_line)
            
            body_context += f"  {i}. {date} — {first_line[:100]}\n"

    # ✅ КОНТЕКСТ О СПОРТИВНОМ ПИТАНИИ
    sport_context = ""
    sport_advice = user_data.get('sport_pit_advice', [])
    if sport_advice:
        # Берем последний совет
        last_advice = sport_advice[-1]
        try:
            advice_date = datetime.datetime.fromisoformat(last_advice['date'])
            if advice_date.tzinfo is None:
                advice_date = pytz.UTC.localize(advice_date)
            advice_date = advice_date.astimezone(MSK)
            date_str = advice_date.strftime('%d.%m %H:%M')
        except:
            date_str = "недавно"
        
        sport_context = f"""
    📋 **Последний совет по спортивному питанию** (от {date_str}):
    {last_advice['advice'][:300]}...
    """

    # ✅ ФОРМИРУЕМ КОНТЕКСТ О ПОЛЬЗОВАТЕЛЕ
    user_context = "\n\n👤 **Информация о пользователе:**\n"
    if settings:
        user_context += f"  • Цель: {settings.get('goal', 'не указана')}\n"
        user_context += f"  • Текущий вес: {settings.get('current_weight', 'не указан')} кг\n"
        user_context += f"  • Желаемый вес: {settings.get('goal_weight', 'не указан')} кг\n"
        user_context += f"  • Рост: {settings.get('height', 'не указан')} см\n"
        user_context += f"  • Возраст: {settings.get('age', 'не указан')} лет\n"
        user_context += f"  • Пол: {settings.get('gender', 'не указан')}\n"
    else:
        user_context += "  • Настройки не заполнены\n"
    
    # Добавляем сообщение пользователя
    chats_history.append({
        "date": datetime.datetime.now().isoformat(),
        "role": "user",
        "content": user_text
    })
    
    # ✅ ФОРМИРУЕМ РАСШИРЕННЫЙ СИСТЕМНЫЙ ПРОМПТ
    enhanced_system_prompt = SYSTEM_PROMPT + f"""
    
{user_context}
{food_context}
{body_context}
{sport_context}

💡 **Важно:** 
- Учитывай историю питания при ответах
- Если пользователь спрашивает про прогресс, анализируй его питание
- Предлагай рекомендации на основе съеденных блюд
- Если данных мало, предложи заполнить профиль или добавить фото еды
- Ты знаешь, какие советы по спортивному питанию я уже давал пользователю
"""
    
    # Формируем промпт для GPT
    messages_for_gpt = [{"role": "system", "content": enhanced_system_prompt}]

    # Добавляем последние сообщения из чата (до 10)
    for chat in chats_history[-10:]:
        messages_for_gpt.append({"role": chat["role"], "content": chat["content"]})

    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "messages": messages_for_gpt
    }

    try:
        # ✅ ПРОВЕРЯЕМ, НЕ ХОЧЕТ ЛИ ПОЛЬЗОВАТЕЛЬ ОБНОВИТЬ ВЕС (ЧЕРЕЗ GPT)
        weight_check_payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Ты анализируешь сообщения пользователя. Определи, хочет ли пользователь обновить свой вес. Если да - верни ТОЛЬКО число (новый вес). Если нет - верни 'None'. Примеры: 'мой вес 65 кг' -> 65, 'поправь на 70' -> 70, 'я похудел до 63' -> 63, 'сегодня 64.5' -> 64.5, 'привет' -> None"},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0,
            "max_tokens": 10
        }
        
        weight_response = await call_openai(weight_check_payload)
        weight_result = weight_response['choices'][0]['message']['content'].strip()
        
        weight_update = None
        if weight_result != 'None':
            try:
                weight_update = float(weight_result)
                print(f"✅ GPT распознал вес: {weight_update}")
            except:
                print(f"❌ GPT вернул не число: {weight_result}")
        
        if weight_update:
            # Обновляем вес в настройках
            if 'settings' not in user_data:
                user_data['settings'] = {}
            
            # Сохраняем старый вес для истории
            old_weight = user_data['settings'].get('current_weight')
            
            # Обновляем текущий вес
            user_data['settings']['current_weight'] = weight_update
            
            # Добавляем в историю метрик
            if 'metrics' not in user_data:
                user_data['metrics'] = []
            
            user_data['metrics'].append({
                "date": datetime.datetime.now().isoformat(),
                "weight": weight_update
            })
            
            # ✅ СОХРАНЯЕМ ИЗМЕНЕНИЯ В СЕССИЮ
            session_storage.save_session(user_id, data=user_data)
            
            # Формируем специальный ответ об обновлении
            answer = f"✅ Вес успешно обновлён!\n\n"
            answer += f"**Новый текущий вес:** {weight_update} кг\n"
            if old_weight:
                change = weight_update - old_weight
                if change > 0:
                    answer += f"📈 **Изменение:** +{change:.1f} кг"
                elif change < 0:
                    answer += f"📉 **Изменение:** {change:.1f} кг"
                else:
                    answer += f"➡️ **Изменение:** без изменений"
            
            # Сохраняем ответ в историю
            chats_history.append({
                "date": datetime.datetime.now().isoformat(),
                "role": "assistant",
                "content": answer
            })
            
            user_data['chats'] = chats_history[-20:]
            session_storage.save_session(user_id, data=user_data)
            
            # Отправляем ответ
            if hasattr(message_or_call, 'data'):  # Это callback
                chat_id = message_or_call.message.chat.id
            else:  # Это message
                chat_id = message_or_call.chat.id
            
            # Функция для конвертации Markdown в HTML
            def markdown_to_html(text):
                text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
                return text
            
            html_answer = markdown_to_html(answer)
            
            try:
                bot.send_message(chat_id, html_answer, parse_mode='HTML')
            except:
                bot.send_message(chat_id, answer)
            
            return  # ⚠️ ВЫХОДИМ ИЗ ФУНКЦИИ
        
        else:
            # Если не обновление веса - отправляем в GPT как обычно
            first_resp = await call_openai(payload)
            answer = first_resp['choices'][0]['message']['content']
        
        # Сохраняем ответ
        chats_history.append({
            "date": datetime.datetime.now().isoformat(),
            "role": "assistant",
            "content": answer
        })
        
        user_data['chats'] = chats_history[-20:]
        session_storage.save_session(user_id, data=user_data)
        
        # Отправляем ответ
        if hasattr(message_or_call, 'data'):  # Это callback
            chat_id = message_or_call.message.chat.id
        else:  # Это message
            chat_id = message_or_call.chat.id

        # ✅ ФУНКЦИЯ ДЛЯ КОНВЕРТАЦИИ MARKDOWN В HTML
        def markdown_to_html(text):
            """Конвертирует Markdown разметку в HTML теги"""
            
            # 1. Сначала обрабатываем заголовки: ### Заголовок -> <b>Заголовок</b>
            lines = text.split('\n')
            for i, line in enumerate(lines):
                # Обрабатываем заголовки с решетками
                if line.strip().startswith('### '):
                    lines[i] = '<b>' + line.strip()[4:] + '</b>'
                elif line.strip().startswith('## '):
                    lines[i] = '<b>' + line.strip()[3:] + '</b>'
                elif line.strip().startswith('# '):
                    lines[i] = '<b>' + line.strip()[2:] + '</b>'
            
            text = '\n'.join(lines)
            
            # 2. Затем обрабатываем жирный текст: **текст** -> <b>текст</b>
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            
            # 3. Затем обрабатываем курсив: _текст_ -> <i>текст</i> или *текст* -> <i>текст</i>
            text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
            text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
            
            # 4. Заголовки с эмодзи: 🔍 *текст* -> <b>🔍 текст</b>
            text = re.sub(r'(🔍\s*)\*(.*?)\*', r'<b>\1\2</b>', text)
            text = re.sub(r'(💡\s*)\*(.*?)\*', r'<b>\1\2</b>', text)
            text = re.sub(r'(⚠️\s*)\*(.*?)\*', r'<b>\1\2</b>', text)
            text = re.sub(r'(✅\s*)\*(.*?)\*', r'<b>\1\2</b>', text)
            text = re.sub(r'(❌\s*)\*(.*?)\*', r'<b>\1\2</b>', text)
            
            # 5. Маркированные списки: • текст или - текст
            lines = text.split('\n')
            for i, line in enumerate(lines):
                # Если строка начинается с •, - или • с пробелом
                if re.match(r'^[•\-]\s', line.strip()):
                    # Оставляем как есть, но убеждаемся что есть символ
                    if not line.strip().startswith('•'):
                        lines[i] = '• ' + line.strip()[1:].strip()
            
            text = '\n'.join(lines)
            
            return text

        # Применяем конвертацию
        html_answer = markdown_to_html(answer)

        try:
            # Пробуем отправить с HTML
            bot.send_message(chat_id, html_answer, parse_mode='HTML')
        except Exception as e:
            print(f"⚠️ Ошибка HTML форматирования: {e}")
            # Если не работает - отправляем без форматирования
            bot.send_message(chat_id, answer)
                
    except Exception as e:
        error_message = str(e)[:100]
        print(f"❌ Ошибка в reply: {e}")
        bot.send_message(chat_id, f"❌ Произошла ошибка при обработке запроса: {error_message}")


async def analyze_body_photo(bot, call, photo_id):
    """Анализ фигуры по фото (не медицинский!)"""
    user_id = call.from_user.id
    print(f"🏋️ АНАЛИЗ ТЕЛА: user={user_id}")
    update_user_activity(user_id)

    # ✅ ОПРЕДЕЛЯЕМ chat_id
    chat_id = call.message.chat.id  
    
    # Получаем данные пользователя
    session = session_storage.get_session(user_id)
    if not session:
        bot.send_message(call.message.chat.id, "❌ Сначала отправьте /start")
        return
    
    # ✅ ПРОВЕРКА НА ПОВТОРНЫЙ АНАЛИЗ
    if session['data'].get('last_analyzed_body_photo_id') == photo_id:
        print(f"⏭️ ПРОПУСК: фото тела {photo_id} уже анализировалось")
        bot.send_message(
            call.message.chat.id, 
            "📸 Это фото уже было проанализировано ранее!\n"
            "Отправьте новое фото для анализа или выберите другое действие в меню."
        )
        return
    
    settings = session['data'].get('settings', {})
    user_goal = settings.get('goal', 'не указана')
    
    # Формируем промпт с дисклеймером
    system_prompt = f"""Ты фитнес-тренер. Оцени форму тела по фото.

⚠️ ВАЖНО: Это НЕ медицинская оценка! Только визуальный анализ.

Опиши:
1. Общее телосложение (худощавое/среднее/плотное)
2. Визуальные признаки (мышечный тонус, пропорции)
3. Рекомендации по тренировкам для цели: {user_goal}

Будь краток, используй эмодзи. Не давай медицинских советов."""
    
    try:
        # ✅ ПОКАЗЫВАЕМ ИНДИКАТОР ПЕЧАТИ
        typing_indicator = None
        try:
            typing_indicator = bot.send_chat_action(call.message.chat.id, 'typing')
        except:
            pass
        
        # ✅ ПОЛУЧАЕМ ФАЙЛ С ТАЙМАУТОМ И УВЕЛИЧЕННЫМИ ПОВТОРНЫМИ ПОПЫТКАМИ
        max_retries = 5  # ✅ УВЕЛИЧИВАЕМ ДО 5 ПОПЫТОК
        file_info = None
        last_error = None

        for attempt in range(max_retries):
            try:
                print(f"📥 Попытка {attempt + 1}/{max_retries} получить file_info...")
                file_info = bot.get_file(photo_id)
                if file_info:
                    print(f"✅ file_info получен на попытке {attempt + 1}")
                    break
            except Exception as e:
                last_error = str(e)
                print(f"⚠️ Попытка {attempt + 1}/{max_retries} не удалась: {e}")
                if attempt < max_retries - 1:
                    wait_time = 3 * (attempt + 1)  # ✅ УВЕЛИЧИВАЕМ ЗАДЕРЖКУ
                    print(f"⏳ Ждём {wait_time} секунд перед следующей попыткой...")
                    time.sleep(wait_time)
                else:
                    error_message = f"❌ Не удалось загрузить фото после {max_retries} попыток"
                    if last_error:
                        error_message += f"\nПоследняя ошибка: {last_error[:100]}"
                    bot.send_message(call.message.chat.id, error_message)
                    return

        if not file_info:
            bot.send_message(call.message.chat.id, "❌ Не удалось получить информацию о фото после всех попыток")
            return

        photo_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        
        # Скачиваем фото с таймаутом
        photo_data = download_file_with_retry(photo_url, timeout=30)
        if not photo_data:
            print(f"❌ ОШИБКА ЗАГРУЗКИ ФОТО: user={user_id}, фото={photo_id}")
            bot.send_message(call.message.chat.id, "❌ Не удалось загрузить фото.\n🔄 Отправьте это же фото ещё раз — обычно помогает!")
            return

        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Проанализируй фигуру на фото:"},
                        {"type": "image_url", "image_url": {"url": photo_url}}
                    ]
                }
            ],
            "max_tokens": 500,
            "temperature": 0.7
        }
        
        # Показываем "Анализирую..."
        wait_msg = bot.send_message(call.message.chat.id, "🔍 Анализирую фото фигуры...\n⏳ Это может занять 10-15 секунд")
        
        response = await call_openai(payload)
        
        # ✅ ПРОВЕРКА НА ОШИБКУ ОТ OPENAI
        if 'error' in response:
            error_msg = response['error'].get('message', 'Неизвестная ошибка')
            print(f"❌ Ошибка OpenAI: {error_msg}")
            bot.send_message(call.message.chat.id, f"❌ Ошибка OpenAI: {error_msg[:100]}")
            # Удаляем сообщение "Анализирую..."
            try:
                bot.delete_message(call.message.chat.id, wait_msg.message_id)
            except:
                pass
            return
            
        if 'choices' not in response or len(response['choices']) == 0:
            print(f"❌ Странный ответ: {response}")
            bot.send_message(call.message.chat.id, "❌ Не удалось получить анализ. Попробуйте позже.")
            try:
                bot.delete_message(call.message.chat.id, wait_msg.message_id)
            except:
                pass
            return
            
        analysis = response['choices'][0]['message']['content']
        
        # Удаляем сообщение "Анализирую..."
        try:
            bot.delete_message(call.message.chat.id, wait_msg.message_id)
        except:
            pass
        
        # ✅ ЗАПОМИНАЕМ ID ФОТО
        if session:
            data = session['data']
            data['last_analyzed_body_photo_id'] = photo_id
            session_storage.save_session(user_id, data=data)
        
        # Кнопки
        markup = types.InlineKeyboardMarkup(row_width=2)
        menu_btn = types.InlineKeyboardButton('📋 Меню', callback_data='show_main_menu')
        progress_btn = types.InlineKeyboardButton('📊 Прогресс', callback_data='check_progress')
        markup.add(menu_btn, progress_btn)
        
        # ✅ ФОРМАТИРУЕМ ОТВЕТ КАК В АНАЛИЗЕ ЕДЫ
        body_report = f"🏋️‍♂️ Анализ фигуры\n\n{analysis}\n\n⚠️ Это не медицинская оценка"
        
        # ФУНКЦИЯ ДЛЯ КОНВЕРТАЦИИ ** В HTML ТЕГИ
        def convert_markdown_to_html(text):
            """Преобразует **текст** в <b>текст</b>"""
            if '**' not in text:
                return text
            parts = text.split('**')
            result = ''
            for i, part in enumerate(parts):
                if i % 2 == 1:  # Нечетные индексы - текст между **
                    result += f'<b>{part}</b>'
                else:
                    result += part
            return result
        
        # Применяем конвертацию
        html_report = convert_markdown_to_html(body_report)
        
        # Отправляем с HTML форматированием
        try:
            bot.send_message(call.message.chat.id, html_report, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            print(f"⚠️ Ошибка HTML форматирования: {e}")
            # Если ошибка - отправляем без форматирования
            bot.send_message(call.message.chat.id, body_report, reply_markup=markup)
        
                # Сохраняем в историю чатов
        if session:
            data = session['data']
            
            # ✅ СОХРАНЯЕМ В СПЕЦИАЛЬНЫЙ СПИСОК АНАЛИЗОВ ТЕЛА
            if 'body_analyses' not in data:
                data['body_analyses'] = []
            
            data['body_analyses'].append({
                "date": datetime.datetime.now().isoformat(),
                "photo_id": photo_id,
                "analysis": analysis,
                "goal": user_goal
            })
            
            # Ограничиваем последними 10 анализами
            data['body_analyses'] = data['body_analyses'][-10:]
            
            # Сохраняем в историю чатов (для отображения)
            data.setdefault('chats', [])
            data['chats'].append({
                "date": datetime.datetime.now().isoformat(),
                "role": "assistant",
                "content": f"🏋️ Анализ фигуры:\n{analysis[:200]}..."
            })
            
            # Сохраняем сессию
            session_storage.save_session(user_id, data=data)

            # ✅ Сохраняем время последнего анализа
            data['last_analysis_time'] = datetime.datetime.now(MSK).isoformat()
            session_storage.save_session(user_id, data=data)

    except Exception as e:
        error_msg = str(e)[:150]
        bot.send_message(call.message.chat.id, f"❌ Ошибка анализа: {error_msg}")
        print(f"Ошибка analyze_body_photo: {e}")


async def analyze_food_photo(bot, call, photo_id):
    """Анализ еды по фото с embeddings и расширенным анализом"""
    user_id = call.from_user.id
    print(f"🍽 АНАЛИЗ ЕДЫ: user={user_id}")
    update_user_activity(user_id)
    
    # ✅ ОПРЕДЕЛЯЕМ chat_id
    chat_id = call.message.chat.id  

    # Получаем данные пользователя
    session = session_storage.get_session(user_id)
    if not session:
        bot.send_message(call.message.chat.id, "❌ Сначала отправьте /start")
        return
    
    # ✅ ПРОВЕРКА НА ПОВТОРНЫЙ АНАЛИЗ
    if session['data'].get('last_analyzed_photo_id') == photo_id:
        print(f"⏭️ ПРОПУСК: фото {photo_id} уже анализировалось")
        bot.send_message(
            call.message.chat.id, 
            "📸 Это фото уже было проанализировано ранее!\n"
            "Отправьте новое фото еды для анализа или выберите другое действие в меню."
        )
        return
        
    settings = session['data'].get('settings', {})
    user_goal = settings.get('goal', 'не указана')
    current_weight = settings.get('current_weight', 'не указан')
    goal_weight = settings.get('goal_weight', 'не указан')
    gender = settings.get('gender', 'не указан')
    age = settings.get('age', 'не указан')
    height = settings.get('height', 'не указан')
    
    # Формируем расширенный промпт с данными пользователя
    enhanced_prompt = f"""Ты диетолог. Проанализируй фото еды и дай ответ СТРОГО в формате:

1. **Блюдо:** [название]
2. **Калорийность:** [примерно XXX ккал]
3. **Питательность (приблизительно):**
   • Белки: [XX г]
   • Жиры: [XX г] 
   • Углеводы: [XX г]
4. **Оценка для цели пользователя:** [подходит/не подходит] для [{user_goal}]
5. **Рекомендации:** [2-3 коротких совета]

Дополнительные данные пользователя:
- Пол: {gender}
- Возраст: {age}
- Рост: {height} см
- Текущий вес: {current_weight} кг
- Целевой вес: {goal_weight} кг
- Цель: {user_goal}

⚠️ ВАЖНО: 
- НЕ пиши "Альтернативные варианты", "###", "Дополнительные рекомендации"
- НЕ ставь дефисы в начале строк кроме маркеров списка
- Только 5 пунктов в ответе, ничего лишнего
- Рекомендации — коротко, без нумерации
"""
    
    try:
        # ✅ ПОКАЗЫВАЕМ ИНДИКАТОР ПЕЧАТИ
        typing_indicator = None
        try:
            typing_indicator = bot.send_chat_action(call.message.chat.id, 'typing')
        except:
            pass
        
        # ✅ ПОЛУЧАЕМ ФАЙЛ С ТАЙМАУТОМ И УВЕЛИЧЕННЫМИ ПОВТОРНЫМИ ПОПЫТКАМИ
        max_retries = 5  # ✅ УВЕЛИЧИВАЕМ ДО 5 ПОПЫТОК
        file_info = None
        last_error = None

        for attempt in range(max_retries):
            try:
                print(f"📥 Попытка {attempt + 1}/{max_retries} получить file_info...")
                file_info = bot.get_file(photo_id)
                if file_info:
                    print(f"✅ file_info получен на попытке {attempt + 1}")
                    break
            except Exception as e:
                last_error = str(e)
                print(f"⚠️ Попытка {attempt + 1}/{max_retries} не удалась: {e}")
                if attempt < max_retries - 1:
                    wait_time = 3 * (attempt + 1)  # ✅ УВЕЛИЧИВАЕМ ЗАДЕРЖКУ
                    print(f"⏳ Ждём {wait_time} секунд перед следующей попыткой...")
                    time.sleep(wait_time)
                else:
                    error_message = f"❌ Не удалось загрузить фото после {max_retries} попыток"
                    if last_error:
                        error_message += f"\nПоследняя ошибка: {last_error[:100]}"
                    bot.send_message(call.message.chat.id, error_message)
                    return

        if not file_info:
            bot.send_message(call.message.chat.id, "❌ Не удалось получить информацию о фото после всех попыток")
            return

        photo_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"

        # Скачиваем фото с таймаутом
        photo_data = download_file_with_retry(photo_url, timeout=30)
        if not photo_data:
            print(f"❌ ОШИБКА ЗАГРУЗКИ ФОТО: user={user_id}, фото={photo_id}")
            bot.send_message(call.message.chat.id, "❌ Не удалось загрузить фото.\n🔄 Отправьте это же фото ещё раз — обычно помогает!")
            return
        
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "system",
                    "content": "Ты профессиональный диетолог. Отвечай кратко, по делу, используй эмодзи."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": enhanced_prompt},
                        {"type": "image_url", "image_url": {"url": photo_url}}
                    ]
                }
            ],
            "max_tokens": 800,
            "temperature": 0.7
        }
        
        # Показываем "Анализирую..."
        wait_msg = bot.send_message(call.message.chat.id, "🍽 Анализирую фото еды...\n⏳ Это может занять 10-20 секунд")
        
        response = await call_openai(payload)
        
        # ✅ ПРОВЕРКА НА ОШИБКУ ОТ OPENAI
        if 'error' in response:
            error_msg = response['error'].get('message', 'Неизвестная ошибка')
            print(f"❌ Ошибка OpenAI: {error_msg}")
            bot.send_message(call.message.chat.id, f"❌ Ошибка OpenAI: {error_msg[:100]}")
            try:
                bot.delete_message(call.message.chat.id, wait_msg.message_id)
            except:
                pass
            return
            
        if 'choices' not in response or len(response['choices']) == 0:
            print(f"❌ Странный ответ: {response}")
            bot.send_message(call.message.chat.id, "❌ Не удалось получить анализ. Попробуйте позже.")
            try:
                bot.delete_message(call.message.chat.id, wait_msg.message_id)
            except:
                pass
            return
            
        basic_analysis = response['choices'][0]['message']['content']
        
        # Удаляем сообщение "Анализирую..."
        try:
            bot.delete_message(call.message.chat.id, wait_msg.message_id)
        except:
            pass
        
        # Извлекаем калории для сохранения
        calories = extract_calories(basic_analysis)
        
        # ========== СОХРАНЯЕМ В ИСТОРИЮ ПИТАНИЯ ==========
        if session:
            data = session['data']
            
            # ✅ ПРИНУДИТЕЛЬНО ПРЕОБРАЗУЕМ В LIST
            if 'food_logs' not in data:
                data['food_logs'] = []
            elif not isinstance(data['food_logs'], list):
                print(f"⚠️ ВНИМАНИЕ: food_logs был {type(data['food_logs'])}, преобразуем в list")
                data['food_logs'] = list(data['food_logs'])
            
            # Добавляем новую запись
            data['food_logs'].append({
                "date": datetime.datetime.now(MSK).isoformat(),
                "photo_id": photo_id,
                "calories": calories,
                "goal": user_goal,
                "analysis": basic_analysis[:500],
                "meal_text": basic_analysis[:500]
            })
            
            # Ограничиваем 50 записями
            data['food_logs'] = data['food_logs'][-50:]
            
            # ✅ ЗАПОМИНАЕМ ID ФОТО
            data['last_analyzed_photo_id'] = photo_id
            
            # Сохраняем сессию
            session_storage.save_session(user_id, data=data)

            # Сохраняем время последнего анализа
            data['last_analysis_time'] = datetime.datetime.now(MSK).isoformat()
            
            print(f"✅ СОХРАНЕНО: {len(data['food_logs'])} записей, калории: {calories}")
            
            # СОХРАНЯЕМ EMBEDDING
            try:
                if 'embedding_service' in globals():
                    # Берем больше текста для embedding
                    text_for_embedding = basic_analysis[:2000] if len(basic_analysis) > 100 else basic_analysis
                    embedding = await embedding_service.get_embedding(text_for_embedding)
                    
                    # Сохраняем с явной проверкой
                    if embedding and len(embedding) > 0:
                        session_storage.save_meal_embedding(user_id, text_for_embedding[:200], embedding)
                        print(f"✅ Embedding сохранён для user={user_id}")
                    else:
                        print(f"❌ Embedding пустой")
            except Exception as e:
                print(f"Ошибка сохранения embedding: {e}")
        
        # ========== АНАЛИЗ СХОЖЕСТИ ==========
        similarity_result = {"has_past_data": False, "message": ""}
        try:
            if 'embedding_service' in globals() and hasattr(session_storage, 'get_meal_embeddings'):
                similarity_result = await analyze_meal_similarity(user_id, basic_analysis)
        except Exception as e:
            print(f"Ошибка анализа схожести: {e}")
        
        # ========== ПРОГРЕСС ПО ВЕСУ ==========
        weight_progress = {"has_data": False, "message": "Нет данных о весе"}
        try:
            if hasattr(session_storage, 'get_weight_progress'):
                weight_progress = session_storage.get_weight_progress(user_id, days=7)
        except Exception as e:
            print(f"Ошибка получения прогресса: {e}")
        
        # ========== УЛУЧШЕННЫЙ АНАЛИЗ ==========
        final_analysis = await get_enhanced_food_analysis(
            basic_analysis=basic_analysis,
            similarity_result=similarity_result,
            weight_progress=weight_progress,
            user_goal=user_goal,
            current_weight=current_weight,
            goal_weight=goal_weight
        )
        
        # ========== ФОРМИРУЕМ ОТЧЁТ ==========
        report = f"🍽 Анализ питания\n\n"
        report += f"{basic_analysis}\n\n"
        
        # Добавляем секцию с embeddings
        if similarity_result.get('has_past_data', False):
            similarity_percent = similarity_result.get('average_similarity', 0) * 100
            report += f"🔍 Сравнение с историей:\n"
            report += f"• Схожесть с прошлыми приёмами: {similarity_percent:.0f}%\n"
            
            if similarity_percent > 85:
                report += f"• ⚠️ Рацион однообразный, добавьте разнообразия\n"
            elif similarity_percent > 60:
                report += f"• 📊 Средняя схожесть\n"
            else:
                report += f"• ✅ Рацион разнообразный\n"
            report += f"\n"
        
        # Добавляем секцию с прогрессом
        if weight_progress.get('has_data', False):
            report += f"📊 Прогресс за 7 дней:\n"
            report += f"• Изменение веса: {weight_progress.get('weight_change', 0):+.1f} кг\n"
            report += f"• {weight_progress.get('message', '')}\n\n"
        
        # Добавляем рекомендации
        if final_analysis and final_analysis != basic_analysis:
            report += f"💡 Рекомендации:\n{final_analysis}\n"
        
        # ========== КНОПКИ ==========
        markup = types.InlineKeyboardMarkup(row_width=2)
        menu_btn = types.InlineKeyboardButton('📋 Меню', callback_data='show_main_menu')
        progress_btn = types.InlineKeyboardButton('📊 Прогресс', callback_data='check_progress')
        foodlog_btn = types.InlineKeyboardButton('📋 История питания', callback_data='show_foodlog')
        markup.add(menu_btn, progress_btn, foodlog_btn)
        
        # ФУНКЦИЯ ДЛЯ КОНВЕРТАЦИИ ** В HTML ТЕГИ
        def convert_markdown_to_html(text):
            """Преобразует **текст** в <b>текст</b>"""
            if '**' not in text:
                return text
            parts = text.split('**')
            result = ''
            for i, part in enumerate(parts):
                if i % 2 == 1:  # Нечетные индексы - текст между **
                    result += f'<b>{part}</b>'
                else:
                    result += part
            return result
        
        # Применяем конвертацию ко всему отчету
        html_report = convert_markdown_to_html(report)
        
        # ✅ ОТПРАВЛЯЕМ СООБЩЕНИЕ С HTML ФОРМАТИРОВАНИЕМ
        try:
            if len(html_report) > 4000:
                parts = [html_report[i:i+4000] for i in range(0, len(html_report), 4000)]
                for i, part in enumerate(parts):
                    if i == len(parts) - 1:
                        bot.send_message(call.message.chat.id, part, parse_mode='HTML', reply_markup=markup)
                    else:
                        bot.send_message(call.message.chat.id, part, parse_mode='HTML')
            else:
                bot.send_message(call.message.chat.id, html_report, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            print(f"⚠️ Ошибка HTML форматирования: {e}")
            # Если ошибка - отправляем без форматирования
            bot.send_message(call.message.chat.id, report, reply_markup=markup)
        
        # Сохраняем в историю чатов
        if session:
            data = session['data']
            data.setdefault('chats', [])
            data['chats'].append({
                "date": datetime.datetime.now().isoformat(),
                "role": "assistant",
                "content": f"🍽 Анализ питания:\n{basic_analysis[:200]}..."
            })
        
    except Exception as e:
        error_msg = str(e)[:150]
        bot.send_message(call.message.chat.id, f"❌ Ошибка анализа: {error_msg}")
        print(f"Ошибка analyze_food_photo: {e}")


async def generate_sport_pit_advice_from_callback(bot, call, prompt, wait_msg):
    """Генерирует советы по спортивному питанию из callback"""
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "Ты эксперт по спортивному питанию. Отвечай кратко, по делу, используй эмодзи."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 800
    }
    
    try:
        # ✅ ПОКАЗЫВАЕМ ИНДИКАТОР ПЕЧАТИ
        try:
            bot.send_chat_action(call.message.chat.id, 'typing')
        except:
            pass
        
        response = await call_openai(payload)
        
        # Удаляем сообщение "Подбираю рекомендации..."
        try:
            bot.delete_message(call.message.chat.id, wait_msg.message_id)
        except:
            pass
        
        if 'error' in response:
            error_msg = response['error'].get('message', 'Неизвестная ошибка')
            bot.send_message(call.message.chat.id, f"❌ Ошибка: {error_msg[:100]}")
            return
        
        if 'choices' not in response or len(response['choices']) == 0:
            bot.send_message(call.message.chat.id, "❌ Не удалось получить рекомендации")
            return
        
        answer = response['choices'][0]['message']['content']

        # ✅ СОХРАНЯЕМ СОВЕТ С ДЕТАЛЬНОЙ ИНФОРМАЦИЕЙ
        session = session_storage.get_session(call.from_user.id)
        if session:
            data = session['data']
            
            # Получаем цель пользователя
            user_goal = data.get('settings', {}).get('goal', 'не указана')
            
            # Извлекаем детальную информацию
            details = {}
            
            # Ищем рекомендованные добавки
            recommended = []
            if re.search(r'протеин', answer, re.IGNORECASE):
                recommended.append("Протеин")
            if re.search(r'креатин', answer, re.IGNORECASE):
                recommended.append("Креатин")
            if re.search(r'BCAA|ВСАА', answer, re.IGNORECASE):
                recommended.append("BCAA")
            if re.search(r'гейнер', answer, re.IGNORECASE):
                recommended.append("Гейнер")
            
            details["recommended"] = recommended if recommended else ["нет рекомендаций"]
            
            # Создаём поле для истории
            if 'sport_pit_advice' not in data:
                data['sport_pit_advice'] = []
            
            data['sport_pit_advice'].append({
                "date": datetime.datetime.now(MSK).isoformat(),
                "advice": answer[:500],
                "details": details,
                "goal": user_goal,  # ✅ ИСПРАВЛЕНО: используем user_goal
                "type": "general"
            })

            data['sport_pit_advice'] = data['sport_pit_advice'][-10:]
            session_storage.save_session(call.from_user.id, data=data)
        
        # ✅ ПОЛНАЯ ФУНКЦИЯ ДЛЯ КОНВЕРТАЦИИ MARKDOWN В HTML
        def markdown_to_html(text):
            """Конвертирует Markdown разметку в HTML теги с разными стилями"""
            
            # 1. Сначала обрабатываем заголовки с решетками
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if line.strip().startswith('### '):
                    lines[i] = '<b>' + line.strip()[4:] + '</b>'
                elif line.strip().startswith('## '):
                    lines[i] = '<b>' + line.strip()[3:] + '</b>'
                elif line.strip().startswith('# '):
                    lines[i] = '<b>' + line.strip()[2:] + '</b>'
            
            text = '\n'.join(lines)
            
            # 2. Обрабатываем нумерованные заголовки (1. **Текст**:)
            lines = text.split('\n')
            for i, line in enumerate(lines):
                # Ищем строки вида "1. **Текст**:"
                match = re.match(r'^(\d+\.\s+)\*\*(.*?)\*\*:', line)
                if match:
                    lines[i] = f'<b>{match.group(1)}{match.group(2)}:</b>'
                else:
                    # Ищем просто "1. Текст:" без звездочек
                    match = re.match(r'^(\d+\.\s+)(.*?):', line)
                    if match:
                        lines[i] = f'<b>{match.group(1)}{match.group(2)}:</b>'
            
            text = '\n'.join(lines)
            
            # 3. Обрабатываем жирный текст: **текст** -> <b>текст</b>
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            
            # 4. Обрабатываем курсив: _текст_ -> <i>текст</i> или *текст* -> <i>текст</i>
            text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
            text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
            
            # 5. Обрабатываем названия добавок (Протеин:, Креатин: и т.д.)
            supplement_patterns = [
                r'(Протеин:)',
                r'(Креатин:)',
                r'(BCAA/EAA:)',
                r'(Предтренировочные комплексы:)',
                r'(Гейнер:)',
                r'(Жиросжигатели:)',
                r'(ВСАА/ЕАА:)'
            ]
            
            for pattern in supplement_patterns:
                text = re.sub(pattern, r'<b>\1</b>', text)
            
            # 6. Обрабатываем поля с двоеточием (Дозировка:, Когда: и т.д.)
            field_patterns = [
                r'(Дозировка:)',
                r'(Когда:)',
                r'(Сколько:)',
                r'(Смысл:)',
                r'(Нужны ли:)',
                r'(Стоит ли:)'
            ]
            
            for pattern in field_patterns:
                text = re.sub(pattern, r'<u>\1</u>', text)
            
            # 7. Обрабатываем эмодзи с текстом
            emoji_patterns = [
                (r'(🌟)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(💪)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(🔄)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(⚡)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(🍫)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(🚫)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(🕒)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(🏋️‍♂️)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(🌞)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(🍽️)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(💧)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(⚖️)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
                (r'(🎯)\s*(.*?)(?=\n|$)', r'<b>\1</b> <i>\2</i>'),
            ]
            
            for pattern, replacement in emoji_patterns:
                text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
            
            # 8. Маркированные списки: - текст или • текст
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if line.strip().startswith('- '):
                    lines[i] = '• ' + line.strip()[2:]
            
            text = '\n'.join(lines)
            
            return text
        
        # ✅ ПРИМЕНЯЕМ КОНВЕРТАЦИЮ
        formatted_answer = f"💪 **Рекомендации по спортивному питанию**\n\n{answer}\n\n"
        formatted_answer += "⚠️ *Важно: проконсультируйтесь с врачом перед применением добавок*"

        # Применяем нашу функцию форматирования
        html_answer = markdown_to_html(formatted_answer)

        # ✅ СОЗДАЁМ КНОПКУ "МЕНЮ"
        markup = types.InlineKeyboardMarkup()
        menu_btn = types.InlineKeyboardButton('📋 Главное меню', callback_data='show_main_menu')
        markup.add(menu_btn)

        # Отправляем с HTML форматированием и кнопкой
        try:
            bot.send_message(call.message.chat.id, html_answer, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            print(f"⚠️ Ошибка HTML форматирования: {e}")
            # Если ошибка - отправляем без форматирования, но с кнопкой
            bot.send_message(call.message.chat.id, formatted_answer, reply_markup=markup)
        
        # Сохраняем в историю чатов
        session = session_storage.get_session(call.from_user.id)
        if session:
            data = session['data']
            data.setdefault('chats', [])
            data['chats'].append({
                "date": datetime.datetime.now(MSK).isoformat(),
                "role": "assistant",
                "content": f"💪 Спортпит: {answer[:200]}..."
            })
            session_storage.save_session(call.from_user.id, data=data)
            
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Ошибка: {str(e)[:100]}")
        print(f"Ошибка generate_sport_pit_advice_from_callback: {e}")

async def generate_my_sport_pit_advice(bot, message, prompt, wait_msg):
    """Генерирует индивидуальные рекомендации по спортивному питанию"""
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "Ты эксперт по спортивному питанию и нутрициологии. Делай точные расчёты на основе данных пользователя."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.5,  # меньше творчества, больше точности
        "max_tokens": 1000
    }
    
    try:
        # ✅ ПОКАЗЫВАЕМ ИНДИКАТОР ПЕЧАТИ
        try:
            bot.send_chat_action(message.chat.id, 'typing')
        except:
            pass
        
        response = await call_openai(payload)
        
        # Удаляем сообщение о расчёте
        try:
            bot.delete_message(message.chat.id, wait_msg.message_id)
        except:
            pass
        
        if 'error' in response:
            error_msg = response['error'].get('message', 'Неизвестная ошибка')
            bot.send_message(message.chat.id, f"❌ Ошибка: {error_msg[:100]}")
            return
        
        if 'choices' not in response or len(response['choices']) == 0:
            bot.send_message(message.chat.id, "❌ Не удалось получить рекомендации")
            return
        
        answer = response['choices'][0]['message']['content']
        
        # ✅ СОХРАНЯЕМ СОВЕТ В ИСТОРИЮ СПОРТПИТА С ДЕТАЛЬНОЙ ИНФОРМАЦИЕЙ
        session = session_storage.get_session(message.from_user.id)
        if session:
            data = session['data']
            # Получаем цель пользователя
            user_goal = data.get('settings', {}).get('goal', 'не указана')
            
            # Извлекаем детальную информацию из ответа
            details = {
                "protein": {"recommended": "не указано", "when": "не указано", "benefit": "Восстановление и рост мышц"},
                "creatine": {"recommended": "не указано", "when": "не указано", "benefit": "Увеличение силы и выносливости"},
                "bcaa": {"recommended": "не указано", "when": "не указано", "benefit": "Защита мышц от разрушения"},
                "pre_workout": {"recommended": "не указано", "when": "не указано", "benefit": "Повышение энергии и фокуса"},
                "gainer": {"recommended": "не указано", "when": "не указано", "benefit": "Быстрый набор калорий"},
                "calories": {"value": "не указано", "benefit": "Общая энергия для тренировок"}
            }
            
            # ПОИСК ПРОТЕИНА - ищем числа рядом со словом "протеин" или "белок"
            protein_section = re.search(r'(?:протеин|белок).*?(\d+)\s*[-–]\s*(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
            if protein_section:
                details["protein"]["recommended"] = f"{protein_section.group(1)}-{protein_section.group(2)} г/день"
            else:
                protein_section = re.search(r'(?:протеин|белок).*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                if protein_section:
                    details["protein"]["recommended"] = f"{protein_section.group(1)} г/день"
            
            # Время для протеина
            protein_time = re.search(r'протеин.*?(?:принимать|пить|употреблять).*?(после тренировки|утром|вечером|перед сном|до тренировки|между приемами)', answer, re.IGNORECASE)
            if protein_time and protein_time.group(1):
                details["protein"]["when"] = protein_time.group(1).lower()
            else:
                protein_time = re.search(r'(после тренировки|утром|вечером|перед сном|до тренировки|между приемами).*?протеин', answer, re.IGNORECASE)
                if protein_time and protein_time.group(1):
                    details["protein"]["when"] = protein_time.group(1).lower()
            
            # ПОИСК КРЕАТИНА
            creatine_section = re.search(r'креатин.*?(\d+)\s*[-–]\s*(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
            if creatine_section:
                # Проверяем, не слишком ли большие числа
                val1 = int(creatine_section.group(1))
                val2 = int(creatine_section.group(2))
                if val1 > 50 or val2 > 50:  # Если числа больше 50, вероятно это не креатин
                    # Ищем просто число
                    simple_match = re.search(r'креатин.*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                    if simple_match:
                        details["creatine"]["recommended"] = f"{simple_match.group(1)} г/день"
                else:
                    details["creatine"]["recommended"] = f"{creatine_section.group(1)}-{creatine_section.group(2)} г/день"
            else:
                creatine_section = re.search(r'креатин.*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                if creatine_section:
                    details["creatine"]["recommended"] = f"{creatine_section.group(1)} г/день"
            
            # Время для креатина
            creatine_time = re.search(r'креатин.*?(?:принимать|пить).*?(после тренировки|до тренировки|утром|вечером)', answer, re.IGNORECASE)
            if creatine_time and creatine_time.group(1):
                details["creatine"]["when"] = creatine_time.group(1).lower()
            
            # ПОИСК BCAA
            bcaa_section = re.search(r'BCAA|ВСАА.*?(\d+)\s*[-–]\s*(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
            if bcaa_section:
                details["bcaa"]["recommended"] = f"{bcaa_section.group(1)}-{bcaa_section.group(2)} г"
            else:
                bcaa_section = re.search(r'BCAA|ВСАА.*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                if bcaa_section:
                    details["bcaa"]["recommended"] = f"{bcaa_section.group(1)} г"
            
            # Время для BCAA
            bcaa_time = re.search(r'BCAA|ВСАА.*?(?:принимать|пить).*?(во время тренировки|до тренировки|после тренировки)', answer, re.IGNORECASE)
            if bcaa_time and bcaa_time.group(1):
                details["bcaa"]["when"] = bcaa_time.group(1).lower()
            
            # ПОИСК ПРЕДТРЕНИРОВОЧНЫХ
            if re.search(r'предтренировочный|pre.?workout|предтрен', answer, re.IGNORECASE):
                details["pre_workout"]["recommended"] = "рекомендуется"
                
                # Время для предтренировочных
                pre_time = re.search(r'(предтренировочный|pre.?workout|предтрен).*?(?:принимать|пить).*?(за 30 минут|до тренировки|перед тренировкой)', answer, re.IGNORECASE)
                if pre_time:
                    if len(pre_time.groups()) >= 2 and pre_time.group(2):
                        details["pre_workout"]["when"] = pre_time.group(2).lower()
                    else:
                        time_in_text = re.search(r'(за 30 минут|до тренировки|перед тренировкой)', answer, re.IGNORECASE)
                        if time_in_text and time_in_text.group(0):
                            details["pre_workout"]["when"] = time_in_text.group(0).lower()
            
            # ПОИСК ГЕЙНЕРА
            gainer_section = re.search(r'гейнер.*?(\d+)\s*[-–]\s*(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
            if gainer_section:
                details["gainer"]["recommended"] = f"{gainer_section.group(1)}-{gainer_section.group(2)} г"
            else:
                gainer_section = re.search(r'гейнер.*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                if gainer_section:
                    details["gainer"]["recommended"] = f"{gainer_section.group(1)} г"
            
            # Время для гейнера
            gainer_time = re.search(r'гейнер.*?(?:принимать|пить).*?(после тренировки|между приемами|утром|вечером)', answer, re.IGNORECASE)
            if gainer_time and gainer_time.group(1):
                details["gainer"]["when"] = gainer_time.group(1).lower()
            
            # ПОИСК КАЛОРИЙ
            calories_section = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*ккал', answer, re.IGNORECASE)
            if calories_section:
                details["calories"]["value"] = f"{calories_section.group(1)}-{calories_section.group(2)} ккал"
            else:
                calories_section = re.search(r'(\d+)\s*ккал', answer, re.IGNORECASE)
                if calories_section:
                    details["calories"]["value"] = f"{calories_section.group(1)} ккал"
            
            # ВЫВОДИМ В КОНСОЛЬ ЧТО НАШЛИ
            print(f"📊 НАЙДЕННЫЕ ДЕТАЛИ:")
            print(f"   Протеин: {details['protein']['recommended']} ({details['protein']['when']})")
            print(f"   Креатин: {details['creatine']['recommended']} ({details['creatine']['when']})")
            print(f"   BCAA: {details['bcaa']['recommended']} ({details['bcaa']['when']})")
            print(f"   Предтрен: {details['pre_workout']['recommended']} ({details['pre_workout']['when']})")
            print(f"   Гейнер: {details['gainer']['recommended']} ({details['gainer']['when']})")
            print(f"   Калории: {details['calories']['value']}")
            
            # Создаём поле для истории советов по спортпиту
            if 'sport_pit_advice' not in data:
                data['sport_pit_advice'] = []
            
            # Добавляем новый совет с детальной информацией
            data['sport_pit_advice'].append({
                "date": datetime.datetime.now(MSK).isoformat(),
                "advice": answer[:1000],
                "details": details,
                "goal": user_goal,
                "type": "individual"
            })
            
            data['sport_pit_advice'] = data['sport_pit_advice'][-10:]
            session_storage.save_session(message.from_user.id, data=data)
            print(f"✅ Совет сохранён в историю!")
        
        # Функция для конвертации Markdown в HTML
        def markdown_to_html(text):
            """Конвертирует Markdown разметку в HTML теги"""
            
            # 1. Заголовки с решетками
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if line.strip().startswith('### '):
                    lines[i] = '<b>' + line.strip()[4:] + '</b>'
                elif line.strip().startswith('## '):
                    lines[i] = '<b>' + line.strip()[3:] + '</b>'
                elif line.strip().startswith('# '):
                    lines[i] = '<b>' + line.strip()[2:] + '</b>'
                elif line.strip().startswith('📊 '):
                    lines[i] = '<b>' + line.strip() + '</b>'
                elif line.strip().startswith('👤 '):
                    lines[i] = '<b>' + line.strip() + '</b>'
                elif line.strip().startswith('💪 '):
                    lines[i] = '<b>' + line.strip() + '</b>'
            
            text = '\n'.join(lines)
            
            # 2. Жирный текст
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            
            # 3. Курсив
            text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
            text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
            
            # 4. Заголовки списков с цифрами
            lines = text.split('\n')
            for i, line in enumerate(lines):
                match = re.match(r'^(\d+\.\s+)(.*?)(:)?$', line.strip())
                if match:
                    lines[i] = '<b>' + line.strip() + '</b>'
            
            text = '\n'.join(lines)
            
            # 5. Поля с расчётами
            calculation_patterns = [
                r'(Суточная норма:)',
                r'(Базовый обмен:)',
                r'(С учётом активности:)',
                r'(Для цели)',
                r'(Протеин:)',
                r'(Креатин:)',
                r'(BCAA/EAA:)',
                r'(Гейнер:)',
                r'(Предтренировочные комплексы:)',
                r'(Витамины и минералы:)'
            ]
            
            for pattern in calculation_patterns:
                text = re.sub(pattern, r'<u>\1</u>', text)
            
            # 6. Числа в расчётах (делаем жирными)
            text = re.sub(r'(\d+)\s*г', r'<b>\1 г</b>', text)
            text = re.sub(r'(\d+)\s*ккал', r'<b>\1 ккал</b>', text)
            text = re.sub(r'(\d+)-(\d+)\s*г', r'<b>\1-\2 г</b>', text)
            
            # 7. Маркированные списки
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if line.strip().startswith('- ') or line.strip().startswith('• '):
                    if not line.strip().startswith('<b>'):
                        lines[i] = '• ' + line.strip().lstrip('-• ')
            
            text = '\n'.join(lines)
            
            return text
        
        # Применяем форматирование
        html_answer = markdown_to_html(answer)
        
        # Создаём кнопки
        markup = types.InlineKeyboardMarkup(row_width=2)
        menu_btn = types.InlineKeyboardButton('📋 Главное меню', callback_data='show_main_menu')
        sportpit_btn = types.InlineKeyboardButton('💪 Общие советы', callback_data='sport_pit')
        history_btn = types.InlineKeyboardButton('📋 История спортпитания', callback_data='sport_history')
        markup.add(menu_btn, sportpit_btn, history_btn)
        
        # Отправляем с HTML форматированием
        try:
            bot.send_message(message.chat.id, html_answer, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            print(f"⚠️ Ошибка HTML форматирования: {e}")
            bot.send_message(message.chat.id, answer, reply_markup=markup)
        
        # Сохраняем в историю чатов
        session = session_storage.get_session(message.from_user.id)
        if session:
            data = session['data']
            data.setdefault('chats', [])
            data['chats'].append({
                "date": datetime.datetime.now(MSK).isoformat(),
                "role": "assistant",
                "content": f"📊 Индивидуальный расчёт спортпита"
            })
            session_storage.save_session(message.from_user.id, data=data)
            
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)[:100]}")
        print(f"Ошибка generate_my_sport_pit_advice: {e}")


def extract_calories(text):
    """Извлекает калории из текста анализа"""
    import re
    # Ищем паттерны типа "500 ккал", "~300 ккал", "калорийность: 450 ккал"
    patterns = [
        r'(\d+)\s*ккал',
        r'калорийность.*?(\d+)',
        r'~(\d+)\s*ккал',
        r'≈(\d+)\s*ккал'
    ]
    
    text_lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                return int(match.group(1))
            except:
                pass
    return None


def process_weight_edit(message):
    """Обрабатываем редактирование веса"""
    user_id = message.from_user.id
    
    # ✅ Проверяем, действительно ли пользователь хочет редактировать вес
    if editing_users.get(user_id) != 'weight':
        return
    
    try:
        weight = float(message.text)
        if not 30 <= weight <= 300:
            raise ValueError
    except:
        msg = bot.send_message(message.chat.id, "Пожалуйста, введите вес от 30 до 300 кг:")
        # Снова устанавливаем режим
        editing_users[user_id] = 'weight'
        bot.register_next_step_handler(msg, process_weight_edit)
        return
    
    # Сохраняем только вес, не трогаем другие настройки
    session = session_storage.get_session(message.from_user.id)
    if session:
        data = session['data']
        data['settings']['current_weight'] = weight
        
        # Добавляем в историю метрик
        data.setdefault('metrics', [])
        data['metrics'].append({
            "date": datetime.datetime.now().isoformat(),
            "weight": weight
        })
        
        # Снимаем флаг редактирования
        data.pop('editing_mode', None)
        data.pop('editing_parameter', None)
        
        session_storage.save_session(message.from_user.id, data=data)
        
        # Сбрасываем режим редактирования
        reset_editing_mode(user_id)
        
        bot.send_message(message.chat.id, f"✅ Вес обновлён: {weight} кг")
        show_main_menu(message)

def process_height_edit(message):
    """Обрабатываем редактирование роста"""
    user_id = message.from_user.id
    
    # ✅ Проверяем, действительно ли пользователь хочет редактировать рост
    if editing_users.get(user_id) != 'height':
        return
    
    try:
        height = int(message.text)
        if not 100 <= height <= 250:
            raise ValueError
    except:
        msg = bot.send_message(message.chat.id, "Пожалуйста, введите рост от 100 до 250 см:")
        # Снова устанавливаем режим
        editing_users[user_id] = 'height'
        bot.register_next_step_handler(msg, process_height_edit)
        return
    
    # Сохраняем только рост
    session = session_storage.get_session(message.from_user.id)
    if session:
        data = session['data']
        data['settings']['height'] = height
        
        # Снимаем флаг редактирования
        data.pop('editing_mode', None)
        data.pop('editing_parameter', None)
        
        session_storage.save_session(message.from_user.id, data=data)
        
        # Сбрасываем режим редактирования
        reset_editing_mode(user_id)
        
        bot.send_message(message.chat.id, f"✅ Рост обновлён: {height} см")
        # ✅ ПОКАЗЫВАЕМ ТОЛЬКО МЕНЮ, БЕЗ ДОПОЛНИТЕЛЬНЫХ ЗАПРОСОВ
        show_main_menu(message)

def process_birthdate_edit(message):
    """Обрабатываем редактирование даты рождения"""
    user_id = message.from_user.id
    
    # ✅ Проверяем, действительно ли пользователь хочет редактировать дату рождения
    if editing_users.get(user_id) != 'birthdate':
        return
    
    try:
        birthdate = datetime.datetime.strptime(message.text, "%d.%m.%Y")
        if birthdate > datetime.datetime.now():
            raise ValueError
    except:
        msg = bot.send_message(
            message.chat.id,
            "Неверный формат. Введите дату в формате ДД.ММ.ГГГГ:"
        )
        # Снова устанавливаем режим
        editing_users[user_id] = 'birthdate'
        bot.register_next_step_handler(msg, process_birthdate_edit)
        return
    
    # Рассчитываем возраст
    today = datetime.datetime.now()
    age = today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))
    
    session = session_storage.get_session(message.from_user.id)
    if session:
        data = session['data']
        data['settings']['birthdate'] = birthdate.isoformat()
        data['settings']['age'] = age
        
        # Снимаем флаг редактирования
        data.pop('editing_mode', None)
        data.pop('editing_parameter', None)
        
        session_storage.save_session(message.from_user.id, data=data)
        
        # Сбрасываем режим редактирования
        reset_editing_mode(user_id)
        
        bot.send_message(message.chat.id, f"✅ Дата рождения обновлена. Возраст: {age} лет")
        show_main_menu(message)

def process_goal_weight_edit(message):
    """Обрабатываем редактирование желаемого веса"""
    user_id = message.from_user.id
    
    # ✅ Проверяем, действительно ли пользователь хочет редактировать желаемый вес
    if editing_users.get(user_id) != 'goal_weight':
        return
    
    try:
        goal_weight = float(message.text)
        if not 30 <= goal_weight <= 300:
            raise ValueError
    except:
        msg = bot.send_message(message.chat.id, "Пожалуйста, введите вес от 30 до 300 кг:")
        # Снова устанавливаем режим
        editing_users[user_id] = 'goal_weight'
        bot.register_next_step_handler(msg, process_goal_weight_edit)
        return
    
    session = session_storage.get_session(message.from_user.id)
    if session:
        data = session['data']
        data['settings']['goal_weight'] = goal_weight
        
        # Снимаем флаг редактирования
        data.pop('editing_mode', None)
        data.pop('editing_parameter', None)
        
        session_storage.save_session(message.from_user.id, data=data)
        
        # Сбрасываем режим редактирования
        reset_editing_mode(user_id)
        
        bot.send_message(message.chat.id, f"✅ Желаемый вес обновлён: {goal_weight} кг")
        show_main_menu(message)

def add_metric(user_id, weight):
    """Добавить метрику веса"""
    session = session_storage.get_session(user_id)
    if session:
        data = session['data']
        data.setdefault('metrics', [])
        data['metrics'].append({
            "date": datetime.datetime.now().isoformat(),
            "weight": weight
        })
        session_storage.save_session(user_id, data=data)
        return True
    return False

async def analyze_meal_similarity(user_id: int, current_meal_analysis: str) -> dict:
    """Анализировать схожесть текущего приёма пищи с прошлыми"""
    try:
        # Проверяем, доступен ли сервис embeddings
        if 'embedding_service' not in globals():
            return {"has_past_data": False, "message": "Сервис embeddings недоступен"}
        
        # Получаем embeddings прошлых приёмов пищи
        if not hasattr(session_storage, 'get_meal_embeddings'):
            return {"has_past_data": False, "message": "Хранилище embeddings недоступно"}
        
        past_embeddings = session_storage.get_meal_embeddings(user_id, limit=10)
        
        if not past_embeddings:
            return {
                "has_past_data": False,
                "message": "Это ваш первый анализ питания"
            }
        
        # Получаем embedding для текущего приёма
        current_embedding = await embedding_service.get_embedding(current_meal_analysis[:1000])
        
        # Вычисляем схожесть с каждым прошлым приёмом
        similarities = []
        
        for past in past_embeddings:
            if 'embedding' in past and past['embedding']:
                similarity = embedding_service.cosine_similarity(
                    current_embedding, 
                    past["embedding"]
                )
                similarities.append(similarity)
        
        if not similarities:
            return {"has_past_data": False, "message": "Нет данных для сравнения"}
        
        avg_similarity = sum(similarities) / len(similarities)
        max_similarity = max(similarities)
        
        return {
            "has_past_data": True,
            "average_similarity": avg_similarity,
            "max_similarity": max_similarity,
            "is_very_similar": avg_similarity > 0.85,
            "past_meals_count": len(past_embeddings),
            "message": f"Схожесть с прошлыми приёмами: {avg_similarity:.0%}"
        }
        
    except Exception as e:
        print(f"Ошибка в analyze_meal_similarity: {e}")
        return {"has_past_data": False, "error": str(e)}

async def get_enhanced_food_analysis(
    basic_analysis: str, 
    similarity_result: dict, 
    weight_progress: dict,
    user_goal: str,
    current_weight=None,
    goal_weight=None
) -> str:
    """Получить улучшенный анализ с учётом истории и прогресса"""
    
    # Если нет данных для улучшенного анализа, возвращаем базовый
    if not similarity_result.get('has_past_data', False) and not weight_progress.get('has_data', False):
        return basic_analysis
    
    # ✅ ЛОГИРУЕМ, ПОЧЕМУ ДЕЛАЕМ УЛУЧШЕННЫЙ АНАЛИЗ
    reasons = []
    if similarity_result.get('has_past_data', False):
        similarity = similarity_result.get('average_similarity', 0) * 100
        reasons.append(f"схожесть с историей {similarity:.0f}%")
    if weight_progress.get('has_data', False):
        reasons.append(f"прогресс: {weight_progress.get('weight_change', 0):+.1f} кг")
    
    print(f"🔍 УЛУЧШЕННЫЙ АНАЛИЗ для user: причины: {', '.join(reasons)}")
    
    try:
        # Формируем промпт для улучшенного анализа
        enhanced_prompt = f"""Ты фитнес-аналитик. Проанализируй питание пользователя с учётом истории.

ОСНОВНОЙ АНАЛИЗ:
{basic_analysis[:500]}

КОНТЕКСТ ИСТОРИИ:
• Цель пользователя: {user_goal}
• Текущий вес: {current_weight if current_weight else 'не указан'} кг
• Целевой вес: {goal_weight if goal_weight else 'не указан'} кг
• Схожесть с прошлыми приёмами: {similarity_result.get('average_similarity', 0):.0%} (высокая схожесть >85%)
• Прогресс по весу: {weight_progress.get('message', 'нет данных')}

ДАЙ РЕКОМЕНДАЦИИ:
1. Если питание слишком похоже на прошлое (>85%) - предложи варианты разнообразия
2. Если нет прогресса по весу 7+ дней - предложи корректировки
3. Конкретные советы по улучшению блюда для цели "{user_goal}"

Формат:
• 💡 Рекомендация 1: [конкретный совет]
• 💡 Рекомендация 2: [конкретный совет]
• 🚀 Что изменить: [конкретные изменения]"""
        
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Ты опытный диетолог и фитнес-тренер."},
                {"role": "user", "content": enhanced_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 300
        }
        
        response = await call_openai(payload)
        enhanced = response['choices'][0]['message']['content']
        
        # ✅ ЛОГИРУЕМ РЕЗУЛЬТАТ
        print(f"✅ Улучшенный анализ получен: {enhanced[:100]}...")
        
        return enhanced
        
    except Exception as e:
        print(f"Ошибка enhanced анализа: {e}")
        return basic_analysis

async def check_for_plateau_and_correct(user_id: int, chat_id: int, bot):
    """Проверяет, нет ли плато 7+ дней, и даёт корректирующие рекомендации"""
    
    # Получаем данные
    session = session_storage.get_session(user_id)
    if not session:
        return
    
    # Проверяем прогресс за последние 7+ дней
    weight_progress = session_storage.get_weight_progress(user_id, days=10)  # берём 10 дней для запаса
    
    if not weight_progress.get('has_data', False):
        return
    
    # Проверяем, было ли плато
    # Для простоты: если тренд stable и данных достаточно
    if weight_progress.get('trend') == 'stable' and weight_progress.get('days_analyzed', 0) >= 7:
        
        # Проверяем, не отправляли ли уже сегодня
        data = session['data']
        last_correction = data.get('last_correction_date')
        today = datetime.datetime.now(MSK).strftime('%Y-%m-%d')
        
        if last_correction == today:
            print(f"⏭️ Коррекция уже отправлялась сегодня для user={user_id}")
            return
        
        # Получаем данные пользователя
        settings = data.get('settings', {})
        user_goal = settings.get('goal', 'не указана')
        current_weight = settings.get('current_weight', 'не указан')
        goal_weight = settings.get('goal_weight', 'не указан')
        
        # Анализируем схожесть питания
        food_logs = data.get('food_logs', [])
        similarity_analysis = "недостаточно данных"
        
        if len(food_logs) >= 3:
            # Берём последние 3 приёма
            recent_meals = [log.get('analysis', '')[:100] for log in food_logs[-3:] if log.get('analysis')]
            similarity_analysis = f"Последние приёмы: {' | '.join(recent_meals)}"
        
        correction_prompt = RECOMMENDATION_CORRECTION_PROMPT.format(
            goal=user_goal,
            current_weight=current_weight,
            goal_weight=goal_weight,
            progress_message=weight_progress.get('message', 'нет прогресса 7+ дней'),
            similarity_analysis=similarity_analysis
        )
        
        # Отправляем запрос к GPT
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Ты персональный фитнес-тренер. Давай конкретные советы."},
                {"role": "user", "content": correction_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }
        
        try:
            response = await call_openai(payload)
            correction = response['choices'][0]['message']['content']
            
            # Отправляем пользователю
            markup = types.InlineKeyboardMarkup()
            menu_btn = types.InlineKeyboardButton('📋 Меню', callback_data='show_main_menu')
            markup.add(menu_btn)
            
            bot.send_message(
                chat_id, 
                f"⚠️ *Заметил, что вес не меняется уже 7+ дней*\n\n{correction}",
                parse_mode='Markdown',
                reply_markup=markup
            )
            
            # Запоминаем, что отправили сегодня
            data['last_correction_date'] = today
            session_storage.save_session(user_id, data=data)
            
            print(f"✅ Отправлена коррекция для user={user_id}")
            
        except Exception as e:
            print(f"Ошибка при отправке коррекции: {e}")


async def auto_analyze_photo(bot, user_id, chat_id, photo_id, wait_msg=None):
    """Автоматическое определение типа фото и соответствующий анализ"""
    
    # Получаем сессию пользователя ОДИН РАЗ
    session = session_storage.get_session(user_id)
    
    # ✅ ПРОВЕРКА НА ПОВТОРНЫЙ АНАЛИЗ
    if session and session['data'].get('last_analyzed_photo_id') == photo_id:
        print(f"⏭️ ПРОПУСК auto_analyze: фото {photo_id} уже анализировалось")
        if wait_msg:
            try:
                bot.delete_message(chat_id, wait_msg.message_id)
            except:
                pass
        bot.send_message(
            chat_id, 
            "📸 Это фото уже было проанализировано ранее!\n"
            "Отправьте новое фото для анализа или выберите другое действие в меню."
        )
        return
    
    # ✅ ВАЖНО: ПРОВЕРЯЕМ, НЕТ ЛИ РЕЖИМА В БД
    if session and session['data'].get('awaiting_photo_type'):
        photo_type = session['data']['awaiting_photo_type']
        print(f"⚠️ В auto_analyze_photo НАЙДЕН РЕЖИМ: {photo_type}")
        
        # Сбрасываем режим
        data = session['data']
        data.pop('awaiting_photo_type', None)
        session_storage.save_session(user_id, data=data)
        
        # Удаляем сообщение "Анализирую фото..."
        if wait_msg:
            try:
                bot.delete_message(chat_id, wait_msg.message_id)
            except:
                pass
        
        # Используем режим
        if photo_type == 'food':
            bot.send_message(chat_id, "🍽 Анализирую фото еды...")
            class MockCall:
                def __init__(self, user_id, message):
                    self.from_user = type('obj', (object,), {'id': user_id})()
                    self.message = message
                    self.data = 'analyze_food'
            
            class MockMessage:
                def __init__(self, chat_id):
                    self.chat = type('obj', (object,), {'id': chat_id})()
            
            mock_message = MockMessage(chat_id)
            mock_call = MockCall(user_id, mock_message)
            
            await analyze_food_photo(bot, mock_call, photo_id)
            return
        elif photo_type == 'body':
            bot.send_message(chat_id, "🏋️ Анализирую фото фигуры...")
            class MockCall:
                def __init__(self, user_id, message):
                    self.from_user = type('obj', (object,), {'id': user_id})()
                    self.message = message
                    self.data = 'analyze_body'
            
            class MockMessage:
                def __init__(self, chat_id):
                    self.chat = type('obj', (object,), {'id': chat_id})()
            
            mock_message = MockMessage(chat_id)
            mock_call = MockCall(user_id, mock_message)
            
            await analyze_body_photo(bot, mock_call, photo_id)
            return
    
    try:
        # ✅ ПОКАЗЫВАЕМ ИНДИКАТОР ПЕЧАТИ
        typing_indicator = None
        try:
            typing_indicator = bot.send_chat_action(chat_id, 'typing')
        except:
            pass
        
        # ✅ ПОЛУЧАЕМ ФАЙЛ С ТАЙМАУТОМ И УВЕЛИЧЕННЫМИ ПОВТОРНЫМИ ПОПЫТКАМИ
        max_retries = 5
        file_info = None
        last_error = None

        for attempt in range(max_retries):
            try:
                print(f"📥 Попытка {attempt + 1}/{max_retries} получить file_info...")
                # Увеличиваем таймаут для получения file_info
                file_info = bot.get_file(photo_id)
                if file_info:
                    print(f"✅ file_info получен на попытке {attempt + 1}")
                    break
            except Exception as e:
                last_error = str(e)
                print(f"⚠️ Попытка {attempt + 1}/{max_retries} не удалась: {e}")
                if attempt < max_retries - 1:
                    wait_time = 3 * (attempt + 1)
                    print(f"⏳ Ждём {wait_time} секунд перед следующей попыткой...")
                    time.sleep(wait_time)
                else:
                    # Удаляем сообщение "Анализирую фото..." при ошибке
                    if wait_msg:
                        try:
                            bot.delete_message(chat_id, wait_msg.message_id)
                        except:
                            pass
                    error_message = f"❌ Не удалось загрузить фото после {max_retries} попыток"
                    if last_error:
                        error_message += f"\nПоследняя ошибка: {last_error[:100]}"
                    bot.send_message(chat_id, error_message)
                    return

        if not file_info:
            # Удаляем сообщение "Анализирую фото..." при ошибке
            if wait_msg:
                try:
                    bot.delete_message(chat_id, wait_msg.message_id)
                except:
                    pass
            bot.send_message(chat_id, "❌ Не удалось получить информацию о фото после всех попыток")
            return

        photo_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"

        # Скачиваем фото с увеличенным таймаутом
        photo_data = download_file_with_retry(photo_url, max_retries=5, timeout=60)
        if not photo_data:
            # Удаляем сообщение "Анализирую фото..." при ошибке
            if wait_msg:
                try:
                    bot.delete_message(chat_id, wait_msg.message_id)
                except:
                    pass
            bot.send_message(chat_id, "❌ Не удалось загрузить фото. Попробуйте позже.")
            return
        
        # Сначала определяем, что на фото
        detection_payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "system",
                    "content": "Ты определяешь, что изображено на фото. Ответь только одним словом: 'food' если это еда, блюдо, продукты; 'body' если это человек, фигура, тело; 'other' если ни то, ни другое."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Что на этом фото?"},
                        {"type": "image_url", "image_url": {"url": photo_url}}
                    ]
                }
            ],
            "max_tokens": 10,
            "temperature": 0
        }
        
        detection_response = await call_openai(detection_payload)
        
        # ✅ ПРОВЕРКА НА ОШИБКУ
        if 'error' in detection_response:
            error_msg = detection_response['error'].get('message', 'Неизвестная ошибка')
            print(f"❌ Ошибка OpenAI: {error_msg}")
            # Удаляем сообщение "Анализирую фото..." при ошибке
            if wait_msg:
                try:
                    bot.delete_message(chat_id, wait_msg.message_id)
                except:
                    pass
            bot.send_message(chat_id, f"❌ Ошибка API: {error_msg[:100]}")
            return
            
        if 'choices' not in detection_response:
            print(f"❌ Странный ответ: {detection_response}")
            # Удаляем сообщение "Анализирую фото..." при ошибке
            if wait_msg:
                try:
                    bot.delete_message(chat_id, wait_msg.message_id)
                except:
                    pass
            bot.send_message(chat_id, "❌ Не удалось определить тип фото. Попробуйте позже.")
            return
            
        detection = detection_response['choices'][0]['message']['content'].strip().lower()
        
        # Удаляем сообщение "Анализирую фото..." после успешного определения
        if wait_msg:
            try:
                bot.delete_message(chat_id, wait_msg.message_id)
            except:
                pass
        
        # Анализируем в зависимости от типа
        if 'food' in detection:
            bot.send_message(chat_id, "🍽 Обнаружена еда! Анализирую...")
            
            class MockCall:
                def __init__(self, user_id, message):
                    self.from_user = type('obj', (object,), {'id': user_id})()
                    self.message = message
                    self.data = 'analyze_food'
            
            class MockMessage:
                def __init__(self, chat_id):
                    self.chat = type('obj', (object,), {'id': chat_id})()
            
            mock_message = MockMessage(chat_id)
            mock_call = MockCall(user_id, mock_message)
            
            await analyze_food_photo(bot, mock_call, photo_id)
            
        elif 'body' in detection or 'person' in detection or 'human' in detection:
            bot.send_message(chat_id, "🏋️ Обнаружено фото фигуры! Анализирую...")
            
            class MockCall:
                def __init__(self, user_id, message):
                    self.from_user = type('obj', (object,), {'id': user_id})()
                    self.message = message
                    self.data = 'analyze_body'
            
            class MockMessage:
                def __init__(self, chat_id):
                    self.chat = type('obj', (object,), {'id': chat_id})()
            
            mock_message = MockMessage(chat_id)
            mock_call = MockCall(user_id, mock_message)
            
            await analyze_body_photo(bot, mock_call, photo_id)
        else:
            # Функция для конвертации Markdown в HTML
            def markdown_to_html(text):
                """Конвертирует Markdown разметку в HTML теги"""
                # Жирный текст
                text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
                # Курсив
                text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
                text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
                return text
            
            # Формируем первое сообщение
            first_msg = "🤔 **Это фото не похоже ни на еду, ни на фото тела.**\n\n"
            first_msg += "📸 **Я могу анализировать:**\n"
            first_msg += "• 🍽 **Фото еды** — оценка калорийности и состава\n"
            first_msg += "• 🏋️‍♂️ **Фото фигуры** — визуальная оценка телосложения\n\n"
            first_msg += "🔍 **А пока — вот подробное описание того, что я вижу:**"
            
            # Применяем форматирование к первому сообщению
            html_first_msg = markdown_to_html(first_msg)
            
            # Отправляем первое сообщение с HTML
            try:
                bot.send_message(chat_id, html_first_msg, parse_mode='HTML')
            except Exception as e:
                print(f"⚠️ Ошибка HTML форматирования: {e}")
                bot.send_message(chat_id, first_msg)
            
            # Отправляем фото в GPT для очень подробного описания
            chat_payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты очень подробно описываешь фото. Напиши развернутое описание из 5-7 предложений: что изображено, какие цвета, детали, атмосфера, что происходит на фото. Используй эмодзи для наглядности."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Опиши это фото максимально подробно, что на нём изображено? Напиши развернутый ответ."},
                            {"type": "image_url", "image_url": {"url": photo_url}}
                        ]
                    }
                ],
                "max_tokens": 500,
                "temperature": 0.8
            }
            
            chat_response = await call_openai(chat_payload)
            
            if 'error' in chat_response:
                error_msg = chat_response['error'].get('message', 'Неизвестная ошибка')
                bot.send_message(chat_id, f"❌ Ошибка API: {error_msg[:100]}")
                return
                
            if 'choices' in chat_response:
                answer = chat_response['choices'][0]['message']['content']
                
                # Формируем второе сообщение с подробным описанием
                second_msg = f"📸 **Подробное описание фото:**\n\n{answer}\n\n"
                second_msg += f"🔄 Отправьте **фото еды** или **фото фигуры** для полноценного анализа питания или оценки телосложения!"
                
                # Применяем форматирование ко второму сообщению
                html_second_msg = markdown_to_html(second_msg)
                
                # Отправляем второе сообщение с HTML
                try:
                    bot.send_message(chat_id, html_second_msg, parse_mode='HTML')
                except Exception as e:
                    print(f"⚠️ Ошибка HTML форматирования: {e}")
                    bot.send_message(chat_id, second_msg)
            else:
                error_msg = "❌ Не удалось распознать изображение.\n\n"
                error_msg += "Пожалуйста, отправьте:\n"
                error_msg += "• 🍽 Фото еды для анализа питания\n"
                error_msg += "• 🏋️‍♂️ Фото фигуры для оценки телосложения"
                
                # Применяем форматирование к сообщению об ошибке
                html_error_msg = markdown_to_html(error_msg)
                
                try:
                    bot.send_message(chat_id, html_error_msg, parse_mode='HTML')
                except Exception as e:
                    print(f"⚠️ Ошибка HTML форматирования: {e}")
                    bot.send_message(chat_id, error_msg)
            
    except Exception as e:
        # Удаляем сообщение "Анализирую фото..." при любой ошибке
        if wait_msg:
            try:
                bot.delete_message(chat_id, wait_msg.message_id)
            except:
                pass
        bot.send_message(chat_id, f"❌ Ошибка анализа: {str(e)[:100]}")
        print(f"Ошибка auto_analyze_photo: {e}")


def _get_trend_emoji(trend):
    return "📉 Снижение" if trend == "loss" else "📈 Набор" if trend == "gain" else "➡️ Стабильно"

def ask_gender(chat_id):
    """Спрашиваем пол с кнопками"""
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton('Мужской', callback_data='gender_male'),
        types.InlineKeyboardButton('Женский', callback_data='gender_female')
    )
    bot.send_message(chat_id, "Выберите ваш пол:", reply_markup=markup)

def process_weight(message):
    """Обрабатываем вес"""
    try:
        weight = float(message.text)
        if not 30 <= weight <= 300:
            raise ValueError
    except:
        msg = bot.send_message(message.chat.id, "Пожалуйста, введите вес от 30 до 300 кг:")
        bot.register_next_step_handler(msg, process_weight)
        return
    
    # Сохраняем в data.metrics как текущую метрику
    session = session_storage.get_session(message.from_user.id)
    if session:
        data = session['data']
        data.setdefault('metrics', [])
        data['metrics'].append({
            "date": datetime.datetime.now().isoformat(),
            "weight": weight
        })
        data.setdefault('settings', {})
        data['settings']['current_weight'] = weight  # дублируем в settings для удобства
        session_storage.save_session(message.from_user.id, data=data)
    
    # Спрашиваем рост
    msg = bot.send_message(message.chat.id, "Введите ваш рост (см):")
    bot.register_next_step_handler(msg, process_height)

def process_height(message):
    """Обрабатываем рост"""
    try:
        height = int(message.text)
        if not 100 <= height <= 250:
            raise ValueError
    except:
        msg = bot.send_message(message.chat.id, "Пожалуйста, введите рост от 100 до 250 см:")
        bot.register_next_step_handler(msg, process_height)
        return
    
    session = session_storage.get_session(message.from_user.id)
    if session:
        data = session['data']
        data['settings']['height'] = height
        session_storage.save_session(message.from_user.id, data=data)
    
    # Спрашиваем дату рождения
    msg = bot.send_message(
        message.chat.id,
        "Введите дату рождения (ДД.ММ.ГГГГ):\nНапример: 15.05.1985"
    )
    bot.register_next_step_handler(msg, process_birthdate)

def process_birthdate(message):
    """Обрабатываем дату рождения"""
    try:
        birthdate = datetime.datetime.strptime(message.text, "%d.%m.%Y")
        if birthdate > datetime.datetime.now():
            raise ValueError
    except:
        msg = bot.send_message(
            message.chat.id,
            "Неверный формат. Введите дату в формате ДД.ММ.ГГГГ:"
        )
        bot.register_next_step_handler(msg, process_birthdate)
        return
    
    # Рассчитываем возраст
    today = datetime.datetime.now()
    age = today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))
    
    session = session_storage.get_session(message.from_user.id)
    if session:
        data = session['data']
        data['settings']['birthdate'] = birthdate.isoformat()
        data['settings']['age'] = age  # сохраняем и возраст для удобства
        session_storage.save_session(message.from_user.id, data=data)
    
    # Спрашиваем желаемый вес
    msg = bot.send_message(message.chat.id, "Введите желаемый вес (кг):")
    bot.register_next_step_handler(msg, process_goal_weight)

def process_goal_weight(message):
    """Обрабатываем желаемый вес"""
    try:
        goal_weight = float(message.text)
        if not 30 <= goal_weight <= 300:
            raise ValueError
    except:
        msg = bot.send_message(message.chat.id, "Пожалуйста, введите вес от 30 до 300 кг:")
        bot.register_next_step_handler(msg, process_goal_weight)
        return
    
    session = session_storage.get_session(message.from_user.id)
    if session:
        data = session['data']
        data['settings']['goal_weight'] = goal_weight
        session_storage.save_session(message.from_user.id, data=data)
    
    # Спрашиваем цель
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton('Похудение', callback_data='goal_loss'),
        types.InlineKeyboardButton('Набор массы', callback_data='goal_gain'),
        types.InlineKeyboardButton('Поддержание веса', callback_data='goal_maintain')
    )
    
    bot.send_message(message.chat.id, "Выберите вашу цель:", reply_markup=markup)

        
@bot.message_handler(commands=['foodlog'])
def show_food_log(message):
    """Показать историю питания по команде /foodlog"""
    user_id = message.from_user.id
    update_user_activity(user_id)
    session = session_storage.get_session(user_id)
    
    if not session:
        bot.send_message(message.chat.id, "❌ Сначала отправьте /start")
        return
    
    food_logs = session['data'].get('food_logs', [])
    
    if not food_logs:
        bot.send_message(message.chat.id, "📭 История питания пуста")
        return
    
    # ПОСЛЕДНИЕ 12 ЗАПИСЕЙ
    food_logs = food_logs[-12:]
    response = "📊 🍽 ИСТОРИЯ ПИТАНИЯ\n"
    response += "══════════════════════\n\n"
    
    current_date = None
    counter = 1
    daily_calories = 0
    today = datetime.datetime.now(MSK).strftime('%d.%m.%Y')
    
    for log in food_logs:
        try:
            dt = datetime.datetime.fromisoformat(log['date'])
            if dt.tzinfo is None:
                dt = pytz.UTC.localize(dt)
            dt = dt.astimezone(MSK)
            date = dt.strftime('%d.%m.%Y')
            time = dt.strftime('%H:%M')
        except:
            date = "??"
            time = "??"
        
        # РАЗДЕЛИТЕЛЬ ПО ДАТАМ И ПОДСЧЁТ ЗА СУТКИ
        if current_date != date:
            # Выводим итог за предыдущий день
            if current_date is not None:
                if current_date == today:
                    response += f"📊 ИТОГО СЕГОДНЯ: {daily_calories} ккал\n"
                else:
                    response += f"📊 ИТОГО за {current_date}: {daily_calories} ккал\n"
                response += "══════════════════════\n\n"
            
            # Новый день
            current_date = date
            daily_calories = 0
            counter = 1
            
            if date == today:
                response += f"📅 СЕГОДНЯ ({date})\n"
            else:
                response += f"📅 {date}\n"
            response += "──────────────────────\n\n"
        
        # ИЗВЛЕКАЕМ ПОЛНЫЙ ТЕКСТ АНАЛИЗА
        analysis = log.get('analysis', '')
        if analysis:
            # Заменяем ** на HTML теги <b> и </b>
            parts = analysis.split('**')
            full_analysis = ''
            for i, part in enumerate(parts):
                if i % 2 == 1:  # Нечетные индексы - это текст между **
                    full_analysis += f'<b>{part}</b>'
                else:
                    full_analysis += part
        else:
            full_analysis = "❓ Анализ отсутствует"
        
        calories = log.get('calories', 0)
        if isinstance(calories, (int, float)):
            calories_val = int(calories)
            daily_calories += calories_val
        
        # ФОРМАТ ЗАПИСИ
        response += f"┌─ {counter}. ─────────────────────\n"
        response += f"│ 🕐 {time}\n"
        response += f"│ {full_analysis}\n"
        response += f"└──────────────────────────\n\n"
        
        counter += 1
    
    # ИТОГ ЗА ПОСЛЕДНИЙ ДЕНЬ
    if current_date is not None:
        if current_date == today:
            response += f"📊 ИТОГО СЕГОДНЯ: {daily_calories} ккал\n"
        else:
            response += f"📊 ИТОГО за {current_date}: {daily_calories} ккал\n"
        response += "══════════════════════\n"
    
    # Общий итог за все показанные дни
    total_calories = 0
    count = 0
    for log in food_logs:
        cal = log.get('calories')
        if isinstance(cal, (int, float)):
            total_calories += cal
            count += 1
    
    if count > 0:
        response += f"\n📊 ВСЕГО за {count} приёмов: {total_calories} ккал"
    
    # Кнопка меню
    markup = types.InlineKeyboardMarkup()
    menu_btn = types.InlineKeyboardButton('📋 Главное меню', callback_data='show_main_menu')
    markup.add(menu_btn)

    # Отправляем с HTML
    bot.send_message(message.chat.id, response, parse_mode='HTML', reply_markup=markup)

@bot.message_handler(commands=['sportpit', 'спортпит'])
def sport_pit_command(message):
    """Команда для получения советов по спортивному питанию"""
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    # Проверяем, принял ли пользователь условия
    session = session_storage.get_session(user_id)
    if not session or not session['accepted_terms']:
        bot.send_message(message.chat.id, "❌ Сначала примите условия использования (/start)")
        return
    
    user_data = session['data']
    settings = user_data.get('settings', {})
    
    # Проверяем, заполнены ли настройки
    if not settings or not settings.get('goal'):
        bot.send_message(
            message.chat.id,
            "❌ Сначала заполните профиль через меню 'Настроить фитнес агента'"
        )
        return
    
    # Получаем данные пользователя
    user_goal = settings.get('goal', 'не указана')
    current_weight = settings.get('current_weight', 'не указан')
    goal_weight = settings.get('goal_weight', 'не указан')
    
    # Определяем тип телосложения (если есть данные)
    body_type = settings.get('body_type', 'среднее')
    
    # Показываем индикатор печати
    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except:
        pass
    
    # Формируем промпт для GPT  
    sport_prompt = SPORTS_NUTRITION_PROMPT.format(
        user_goal=user_goal,
        current_weight=current_weight,
        goal_weight=goal_weight,
        body_type=body_type
    )
    
    # Отправляем сообщение о начале анализа
    wait_msg = bot.send_message(
        message.chat.id, 
        "💪 Подбираю рекомендации по спортивному питанию...\n⏳ Это может занять несколько секунд"
    )
    
    # Запускаем асинхронную функцию
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(generate_sport_pit_advice(bot, message, sport_prompt, wait_msg))
    finally:
        loop.close()

async def generate_sport_pit_advice(bot, message, prompt, wait_msg):
    """Генерирует советы по спортивному питанию"""
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "Ты эксперт по спортивному питанию. Отвечай кратко, по делу, используй эмодзи."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 800
    }
    
    try:
        # ✅ ПОКАЗЫВАЕМ ИНДИКАТОР ПЕЧАТИ
        try:
            bot.send_chat_action(message.chat.id, 'typing')
        except:
            pass
        
        response = await call_openai(payload)
        
        # Удаляем сообщение "Подбираю рекомендации..."
        try:
            bot.delete_message(message.chat.id, wait_msg.message_id)
        except:
            pass
        
        if 'error' in response:
            error_msg = response['error'].get('message', 'Неизвестная ошибка')
            bot.send_message(message.chat.id, f"❌ Ошибка: {error_msg[:100]}")
            return
        
        if 'choices' not in response or len(response['choices']) == 0:
            bot.send_message(message.chat.id, "❌ Не удалось получить рекомендации")
            return
        
        answer = response['choices'][0]['message']['content']

        # ✅ СОХРАНЯЕМ СОВЕТ В ИСТОРИЮ СПОРТПИТА С ДЕТАЛЬНОЙ ИНФОРМАЦИЕЙ
        session = session_storage.get_session(message.from_user.id)
        if session:
            data = session['data']
            # Получаем цель пользователя
            user_goal = data.get('settings', {}).get('goal', 'не указана')
            
            # Извлекаем детальную информацию из ответа
            details = {
                "protein": {"recommended": "не указано", "when": "не указано", "benefit": "Восстановление и рост мышц"},
                "creatine": {"recommended": "не указано", "when": "не указано", "benefit": "Увеличение силы и выносливости"},
                "bcaa": {"recommended": "не указано", "when": "не указано", "benefit": "Защита мышц от разрушения"},
                "pre_workout": {"recommended": "не указано", "when": "не указано", "benefit": "Повышение энергии и фокуса"},
                "gainer": {"recommended": "не указано", "when": "не указано", "benefit": "Быстрый набор калорий"},
                "calories": {"value": "не указано", "benefit": "Общая энергия для тренировок"}
            }
            
            # ПОИСК ПРОТЕИНА
            protein_section = re.search(r'(?:протеин|белок).*?(\d+)\s*[-–]\s*(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
            if protein_section:
                details["protein"]["recommended"] = f"{protein_section.group(1)}-{protein_section.group(2)} г/день"
            else:
                protein_section = re.search(r'(?:протеин|белок).*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                if protein_section:
                    details["protein"]["recommended"] = f"{protein_section.group(1)} г/день"
            
            # Время для протеина
            protein_time = re.search(r'протеин.*?(?:принимать|пить|употреблять).*?(после тренировки|утром|вечером|перед сном|до тренировки|между приемами)', answer, re.IGNORECASE)
            if protein_time and protein_time.group(1):
                details["protein"]["when"] = protein_time.group(1).lower()
            else:
                protein_time = re.search(r'(после тренировки|утром|вечером|перед сном|до тренировки|между приемами).*?протеин', answer, re.IGNORECASE)
                if protein_time and protein_time.group(1):
                    details["protein"]["when"] = protein_time.group(1).lower()
            
            # ПОИСК КРЕАТИНА
            creatine_section = re.search(r'креатин.*?(\d+)\s*[-–]\s*(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
            if creatine_section:
                details["creatine"]["recommended"] = f"{creatine_section.group(1)}-{creatine_section.group(2)} г/день"
            else:
                creatine_section = re.search(r'креатин.*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                if creatine_section:
                    details["creatine"]["recommended"] = f"{creatine_section.group(1)} г/день"
            
            # Время для креатина
            creatine_time = re.search(r'креатин.*?(?:принимать|пить).*?(после тренировки|до тренировки|утром|вечером)', answer, re.IGNORECASE)
            if creatine_time and creatine_time.group(1):
                details["creatine"]["when"] = creatine_time.group(1).lower()
            
            # ПОИСК BCAA
            bcaa_section = re.search(r'BCAA|ВСАА.*?(\d+)\s*[-–]\s*(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
            if bcaa_section:
                details["bcaa"]["recommended"] = f"{bcaa_section.group(1)}-{bcaa_section.group(2)} г"
            else:
                bcaa_section = re.search(r'BCAA|ВСАА.*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                if bcaa_section:
                    details["bcaa"]["recommended"] = f"{bcaa_section.group(1)} г"
            
            # Время для BCAA
            bcaa_time = re.search(r'BCAA|ВСАА.*?(?:принимать|пить).*?(во время тренировки|до тренировки|после тренировки)', answer, re.IGNORECASE)
            if bcaa_time and bcaa_time.group(1):
                details["bcaa"]["when"] = bcaa_time.group(1).lower()
            
            # ПОИСК ПРЕДТРЕНИРОВОЧНЫХ
            if re.search(r'предтренировочный|pre.?workout|предтрен', answer, re.IGNORECASE):
                details["pre_workout"]["recommended"] = "рекомендуется"
                
                # Время для предтренировочных
                pre_time = re.search(r'(предтренировочный|pre.?workout|предтрен).*?(?:принимать|пить).*?(за 30 минут|до тренировки|перед тренировкой)', answer, re.IGNORECASE)
                if pre_time:
                    if len(pre_time.groups()) >= 2 and pre_time.group(2):
                        details["pre_workout"]["when"] = pre_time.group(2).lower()
                    else:
                        time_in_text = re.search(r'(за 30 минут|до тренировки|перед тренировкой)', answer, re.IGNORECASE)
                        if time_in_text:
                            details["pre_workout"]["when"] = time_in_text.group(0).lower()
            
            # ПОИСК ГЕЙНЕРА
            gainer_section = re.search(r'гейнер.*?(\d+)\s*[-–]\s*(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
            if gainer_section:
                details["gainer"]["recommended"] = f"{gainer_section.group(1)}-{gainer_section.group(2)} г"
            else:
                gainer_section = re.search(r'гейнер.*?(\d+)\s*г', answer, re.IGNORECASE | re.DOTALL)
                if gainer_section:
                    details["gainer"]["recommended"] = f"{gainer_section.group(1)} г"
            
            # Время для гейнера
            gainer_time = re.search(r'гейнер.*?(?:принимать|пить).*?(после тренировки|между приемами|утром|вечером)', answer, re.IGNORECASE)
            if gainer_time and gainer_time.group(1):
                details["gainer"]["when"] = gainer_time.group(1).lower()
            
            # ПОИСК КАЛОРИЙ
            calories_section = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*ккал', answer, re.IGNORECASE)
            if calories_section:
                details["calories"]["value"] = f"{calories_section.group(1)}-{calories_section.group(2)} ккал"
            else:
                calories_section = re.search(r'(\d+)\s*ккал', answer, re.IGNORECASE)
                if calories_section:
                    details["calories"]["value"] = f"{calories_section.group(1)} ккал"
            
            # ВЫВОДИМ В КОНСОЛЬ ЧТО НАШЛИ
            print(f"📊 НАЙДЕННЫЕ ДЕТАЛИ:")
            print(f"   Протеин: {details['protein']['recommended']} ({details['protein']['when']})")
            print(f"   Креатин: {details['creatine']['recommended']} ({details['creatine']['when']})")
            print(f"   BCAA: {details['bcaa']['recommended']} ({details['bcaa']['when']})")
            print(f"   Предтрен: {details['pre_workout']['recommended']} ({details['pre_workout']['when']})")
            print(f"   Гейнер: {details['gainer']['recommended']} ({details['gainer']['when']})")
            print(f"   Калории: {details['calories']['value']}")
            
            # Создаём поле для истории советов по спортпиту
            if 'sport_pit_advice' not in data:
                data['sport_pit_advice'] = []
            
            # Добавляем новый совет с детальной информацией
            data['sport_pit_advice'].append({
                "date": datetime.datetime.now(MSK).isoformat(),
                "advice": answer[:1000],
                "details": details,
                "goal": user_goal,
                "type": "individual"
            })
            
            data['sport_pit_advice'] = data['sport_pit_advice'][-10:]
            session_storage.save_session(message.from_user.id, data=data)
            print(f"✅ Совет сохранён в историю!")
        
        # ✅ ПОЛНАЯ ФУНКЦИЯ ДЛЯ КОНВЕРТАЦИИ MARKDOWN В HTML
        def markdown_to_html(text):
            """Конвертирует Markdown разметку в HTML теги с разными стилями"""
            
            # 1. Сначала обрабатываем заголовки с решетками
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if line.strip().startswith('### '):
                    lines[i] = '<b>' + line.strip()[4:] + '</b>'
                elif line.strip().startswith('## '):
                    lines[i] = '<b>' + line.strip()[3:] + '</b>'
                elif line.strip().startswith('# '):
                    lines[i] = '<b>' + line.strip()[2:] + '</b>'
            
            text = '\n'.join(lines)
            
            # 2. Жирный текст
            text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
            
            # 3. Курсив
            text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
            text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
            
            # 4. Заголовки списков с цифрами
            lines = text.split('\n')
            for i, line in enumerate(lines):
                match = re.match(r'^(\d+\.\s+)(.*?)(:)?$', line.strip())
                if match:
                    lines[i] = '<b>' + line.strip() + '</b>'
            
            text = '\n'.join(lines)
            
            # 5. Названия добавок - курсив
            supplement_patterns = [
                r'(Протеин)',
                r'(Креатин)',
                r'(BCAA/EAA)',
                r'(Предтренировочные комплексы)',
                r'(Гейнер)',
                r'(Жиросжигатели)',
                r'(ВСАА/ЕАА)'
            ]
            
            for pattern in supplement_patterns:
                text = re.sub(pattern, r'<i>\1</i>', text)
            
            # 6. Поля - подчеркнутые
            field_patterns = [
                r'(Когда:)',
                r'(Сколько:)',
                r'(Смысл:)',
                r'(Дозировка:)',
                r'(Нужны ли:)',
                r'(Стоит ли:)',
                r'(Для набора массы:)',
                r'(Для похудения:)'
            ]
            
            for pattern in field_patterns:
                text = re.sub(pattern, r'<u>\1</u>', text)
            
            # 7. Эмодзи с текстом
            emoji_patterns = [
                (r'(✅\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(❌\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(⚠️\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(💪\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(🥛\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(🍌\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(🍃\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(⏳\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(💧\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>'),
                (r'(🍽️\s*)(.*?)(?=\n|$)', r'<b>\1</b><i>\2</i>')
            ]
            
            for pattern, replacement in emoji_patterns:
                text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
            
            # 8. Маркированные списки
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if line.strip().startswith('- '):
                    if not line.strip().startswith('<b>'):
                        lines[i] = '• ' + line.strip()[2:]
            
            text = '\n'.join(lines)
            
            return text
        
        # ✅ ПРИМЕНЯЕМ КОНВЕРТАЦИЮ
        formatted_answer = f"💪 **Рекомендации по спортивному питанию**\n\n{answer}\n\n"
        formatted_answer += "⚠️ *Важно: проконсультируйтесь с врачом перед применением добавок*"

        html_answer = markdown_to_html(formatted_answer)

        # ✅ СОЗДАЁМ КНОПКУ "МЕНЮ"
        markup = types.InlineKeyboardMarkup()
        menu_btn = types.InlineKeyboardButton('📋 Главное меню', callback_data='show_main_menu')
        markup.add(menu_btn)

        try:
            bot.send_message(message.chat.id, html_answer, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            print(f"⚠️ Ошибка HTML форматирования: {e}")
            bot.send_message(message.chat.id, formatted_answer, reply_markup=markup)
        
        # Сохраняем в историю чатов
        session = session_storage.get_session(message.from_user.id)
        if session:
            data = session['data']
            data.setdefault('chats', [])
            data['chats'].append({
                "date": datetime.datetime.now(MSK).isoformat(),
                "role": "assistant",
                "content": f"💪 Спортпит: {answer[:200]}..."
            })
            session_storage.save_session(message.from_user.id, data=data)
            
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)[:100]}")
        print(f"Ошибка generate_sport_pit_advice: {e}")

@bot.message_handler(commands=['mysportpit', 'моёспортпит'])
def my_sport_pit_command(message):
    """Команда для индивидуального расчёта спортивного питания"""
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    # Проверяем, принял ли пользователь условия
    session = session_storage.get_session(user_id)
    if not session or not session['accepted_terms']:
        bot.send_message(message.chat.id, "❌ Сначала примите условия использования (/start)")
        return
    
    user_data = session['data']
    settings = user_data.get('settings', {})
    
    # Проверяем, заполнены ли настройки
    required_fields = ['goal', 'current_weight', 'goal_weight', 'height', 'age', 'gender']
    missing_fields = [field for field in required_fields if not settings.get(field)]
    
    if missing_fields:
        bot.send_message(
            message.chat.id,
            f"❌ Для расчёта нужно заполнить все данные в профиле.\n"
            f"Отсутствуют: {', '.join(missing_fields)}\n"
            f"Зайдите в меню 'Настроить фитнес агента'"
        )
        return
    
    # Получаем данные пользователя
    user_goal = settings.get('goal', 'не указана')
    current_weight = settings.get('current_weight', 0)
    goal_weight = settings.get('goal_weight', 0)
    height = settings.get('height', 0)
    age = settings.get('age', 0)
    gender = settings.get('gender', 'не указан')
    
    # Определяем уровень активности (можно спросить у пользователя или взять из настроек)
    activity_level = settings.get('activity_level', 2)  # по умолчанию средний
    
    # Показываем индикатор печати
    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except:
        pass
    
    sport_calc_prompt = SPORTS_NUTRITION_CALCULATION_PROMPT.format(
        user_goal=user_goal,
        current_weight=current_weight,
        goal_weight=goal_weight,
        height=height,
        age=age,
        gender=gender,
        activity_level=activity_level
    )
    
    # Отправляем сообщение о начале расчёта
    wait_msg = bot.send_message(
        message.chat.id, 
        "🧮 Рассчитываю индивидуальные рекомендации по спортивному питанию...\n⏳ Это может занять несколько секунд"
    )
    
    # Запускаем асинхронную функцию
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(generate_my_sport_pit_advice(bot, message, sport_calc_prompt, wait_msg))
    finally:
        loop.close()

@bot.message_handler(commands=['mysporthistory', 'историяспортпита'])
def my_sport_history(message):
    """Показать историю советов по спортивному питанию"""
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    session = session_storage.get_session(user_id)
    if not session:
        bot.send_message(message.chat.id, "❌ Сначала отправьте /start")
        return
    
    sport_advice = session['data'].get('sport_pit_advice', [])
    
    if not sport_advice:
        bot.send_message(message.chat.id, "📭 У вас ещё нет сохранённых советов по спортивному питанию")
        return
    
    response = "📊 **ИСТОРИЯ СОВЕТОВ ПО СПОРТПИТАНИЮ**\n"
    response += "══════════════════════════════\n\n"
    
    for i, advice in enumerate(reversed(sport_advice[-10:]), 1):
        try:
            dt = datetime.datetime.fromisoformat(advice['date'])
            # Если время без таймзоны, считаем что это UTC
            if dt.tzinfo is None:
                dt = pytz.UTC.localize(dt)
            # Конвертируем в MSK
            dt = dt.astimezone(MSK)
            date = dt.strftime('%d.%m.%Y %H:%M')
        except Exception as e:
            print(f"⚠️ Ошибка конвертации даты: {e}, исходная дата: {advice.get('date')}")
            date = "неизвестно"
        
        goal = advice.get('goal', 'не указана')
        advice_type = advice.get('type', 'general')
        type_emoji = "📊" if advice_type == "individual" else "💪"
        type_text = "ИНДИВИДУАЛЬНЫЙ РАСЧЁТ" if advice_type == "individual" else "ОБЩИЙ СОВЕТ"
        
        response += f"{type_emoji} **{i}. {date}** ({type_text})\n"
        response += f"   📌 **Цель:** {goal}\n"
        
        details = advice.get('details', {})
        
        # Для индивидуальных расчётов
        if advice_type == "individual" and isinstance(details, dict):
            added_count = 0
            
            # Протеин
            if details.get('protein', {}).get('recommended', 'не указано') != "не указано":
                p = details['protein']
                protein_text = f"   🥛 **Протеин:** {p['recommended']}"
                if p.get('when') and p['when'] != "не указано" and "не указано" not in p['when']:
                    protein_text += f" — принимать **{p['when']}**"
                response += protein_text + "\n"
                if p.get('benefit'):
                    response += f"      • {p['benefit']}\n"
                added_count += 1
            
            # Креатин
            if details.get('creatine', {}).get('recommended', 'не указано') != "не указано":
                c = details['creatine']
                creatine_text = f"   ⚡ **Креатин:** {c['recommended']}"
                if c.get('when') and c['when'] != "не указано" and "не указано" not in c['when']:
                    creatine_text += f" — принимать **{c['when']}**"
                response += creatine_text + "\n"
                if c.get('benefit'):
                    response += f"      • {c['benefit']}\n"
                added_count += 1
            
            # BCAA
            if details.get('bcaa', {}).get('recommended', 'не указано') != "не указано":
                b = details['bcaa']
                bcaa_value = b['recommended']
                if "None" not in bcaa_value and bcaa_value != "не указано":
                    bcaa_text = f"   🏋️ **BCAA:** {bcaa_value}"
                    if b.get('when') and b['when'] != "не указано" and "не указано" not in b['when']:
                        bcaa_text += f" — принимать **{b['when']}**"
                    response += bcaa_text + "\n"
                    if b.get('benefit'):
                        response += f"      • {b['benefit']}\n"
                    added_count += 1
            
            # Предтренировочные
            if details.get('pre_workout', {}).get('recommended', 'не указано') != "не указано":
                pw = details['pre_workout']
                pre_text = f"   ⚡ **Предтрен:** {pw['recommended']}"
                if pw.get('when') and pw['when'] != "не указано" and "не указано" not in pw['when']:
                    pre_text += f" — принимать **{pw['when']}**"
                response += pre_text + "\n"
                if pw.get('benefit'):
                    response += f"      • {pw['benefit']}\n"
                added_count += 1
            
            # Гейнер
            if details.get('gainer', {}).get('recommended', 'не указано') != "не указано":
                g = details['gainer']
                gainer_text = f"   🍌 **Гейнер:** {g['recommended']}"
                if g.get('when') and g['when'] != "не указано" and "не указано" not in g['when']:
                    gainer_text += f" — принимать **{g['when']}**"
                elif "г" in g['recommended'] and g['recommended'] != "не указано":
                    gainer_text += f" — принимать **после тренировки** (стандартно)"
                response += gainer_text + "\n"
                if g.get('benefit'):
                    response += f"      • {g['benefit']}\n"
                added_count += 1
            
            # Калории
            if details.get('calories', {}).get('value', 'не указано') != "не указано":
                response += f"   🔥 **Калории:** {details['calories']['value']}\n"
                if details['calories'].get('benefit'):
                    response += f"      • {details['calories']['benefit']}\n"
                added_count += 1
            
            # Если ничего не добавили, показываем часть совета
            if added_count == 0:
                advice_text = advice.get('advice', '')
                if advice_text.startswith('📊 ИНДИВИДУАЛЬНЫЙ'):
                    lines = advice_text.split('\n')
                    if len(lines) > 1:
                        advice_text = '\n'.join(lines[1:])
                preview = advice_text[:300] + "..." if len(advice_text) > 300 else advice_text
                response += f"   💡 {preview}\n"
        
        # Для общих советов
        elif advice_type == "general" and isinstance(details, dict):
            recommended = details.get('recommended', [])
            if recommended and recommended != ["нет рекомендаций"]:
                response += f"   💡 **РЕКОМЕНДОВАНО:** {', '.join(recommended)}\n"
            else:
                preview = advice.get('advice', '')[:200] + "..."
                response += f"   💡 {preview}\n"
            
            response += f"   💧 **Важно:** Пейте 2-3 л воды, добавки не заменяют еду\n"
        
        # Если тип не определён
        else:
            preview = advice.get('advice', '')[:200] + "..."
            response += f"   💡 {preview}\n"
        
        response += "\n   ───────────────────────────\n\n"
    
    response += "══════════════════════════════\n"
    response += "Используйте /mysportpit для нового расчёта\n"
    response += "Используйте /clearsportpit для очистки истории"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    menu_btn = types.InlineKeyboardButton('📋 Главное меню', callback_data='show_main_menu')
    sportpit_btn = types.InlineKeyboardButton('💪 Новый расчёт', callback_data='my_sport_pit')
    markup.add(menu_btn, sportpit_btn)
    
    try:
        bot.send_message(message.chat.id, response, parse_mode='Markdown', reply_markup=markup)
    except Exception as e:
        print(f"⚠️ Ошибка Markdown в истории: {e}")
        bot.send_message(message.chat.id, response.replace('*', ''), reply_markup=markup)


@bot.message_handler(commands=['clearsportpit', 'очиститьспортпит'])
def clear_sport_pit_history(message):
    """Очищает историю советов по спортивному питанию"""
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    # Проверяем, принял ли пользователь условия
    session = session_storage.get_session(user_id)
    if not session or not session['accepted_terms']:
        bot.send_message(message.chat.id, "❌ Сначала примите условия использования (/start)")
        return
    
    # Проверяем, есть ли вообще история
    sport_advice = session['data'].get('sport_pit_advice', [])
    
    if not sport_advice:
        bot.send_message(
            message.chat.id, 
            "📭 У вас и так нет сохранённых советов по спортивному питанию."
        )
        return
    
    # Сохраняем количество для статистики
    count = len(sport_advice)
    
    # Очищаем историю
    data = session['data']
    data['sport_pit_advice'] = []
    session_storage.save_session(user_id, data=data)
    
    # Отправляем подтверждение
    bot.send_message(
        message.chat.id,
        f"✅ История спортивного питания очищена!\n"
        f"Удалено {count} сохранённых советов."
    )
    
    # Показываем индикатор печати
    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except:
        pass
    
    # Небольшая задержка для эффекта
    time.sleep(0.5)

@bot.message_handler(commands=['embedstats'])
def show_embedding_stats(message):
    """Показать статистику embeddings (для отладки)"""
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    # Проверяем, принял ли пользователь условия
    session = session_storage.get_session(user_id)
    if not session or not session['accepted_terms']:
        bot.send_message(message.chat.id, "❌ Сначала примите условия использования (/start)")
        return
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT COUNT(*), MAX(created_at) FROM meal_embeddings WHERE telegram_id = ?
    ''', (user_id,))
    
    count, last_date = cursor.fetchone()
    
    cursor.execute('''
        SELECT meal_text FROM meal_embeddings WHERE telegram_id = ? ORDER BY created_at DESC LIMIT 5
    ''', (user_id,))
    
    recent = cursor.fetchall()
    conn.close()
    
    response = f"📊 **Статистика embeddings**\n\n"
    response += f"• Всего сохранено: {count}\n"
    response += f"• Последнее сохранение: {last_date or 'никогда'}\n\n"
    
    if recent:
        response += "**Последние 5 текстов:**\n"
        for i, (text,) in enumerate(recent, 1):
            # Обрезаем слишком длинные тексты
            short_text = text[:100] + "..." if len(text) > 100 else text
            response += f"{i}. {short_text}\n"
    else:
        response += "❌ Нет сохранённых embeddings"
    
    bot.send_message(message.chat.id, response, parse_mode='Markdown')

@bot.message_handler(commands=['clear_embeddings', 'очистить_embeddings'])
def clear_embeddings_command(message):
    """Очищает только embeddings, сохраняя всю историю и настройки"""
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    # Проверяем, принял ли пользователь условия
    session = session_storage.get_session(user_id)
    if not session or not session['accepted_terms']:
        bot.send_message(message.chat.id, "❌ Сначала примите условия использования (/start)")
        return
    
    # Подключаемся к БД
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Считаем сколько embeddings у пользователя
        cursor.execute('SELECT COUNT(*) FROM meal_embeddings WHERE telegram_id = ?', (user_id,))
        count_before = cursor.fetchone()[0]
        
        if count_before == 0:
            bot.send_message(
                message.chat.id, 
                "📊 У вас нет сохранённых embeddings для удаления."
            )
            conn.close()
            return
        
        # Получаем информацию о последних embeddings для красивого отчёта
        cursor.execute('''
            SELECT created_at, meal_text 
            FROM meal_embeddings 
            WHERE telegram_id = ? 
            ORDER BY created_at DESC 
            LIMIT 3
        ''', (user_id,))
        
        recent = cursor.fetchall()
        
        # Удаляем все embeddings пользователя
        cursor.execute('DELETE FROM meal_embeddings WHERE telegram_id = ?', (user_id,))
        
        # Проверяем что удалилось
        cursor.execute('SELECT COUNT(*) FROM meal_embeddings WHERE telegram_id = ?', (user_id,))
        count_after = cursor.fetchone()[0]
        
        conn.commit()
        
        # Формируем красивое сообщение
        response = f"🗑️ **Embeddings успешно очищены!**\n\n"
        response += f"• Удалено записей: **{count_before}**\n"
        response += f"• Осталось: **{count_after}**\n\n"
        
        if recent:
            response += "📋 **Последние удалённые записи:**\n"
            for i, (created_at, meal_text) in enumerate(recent, 1):
                # Обрезаем слишком длинный текст
                short_text = meal_text[:50] + "..." if len(meal_text) > 50 else meal_text
                # Форматируем дату
                try:
                    dt = datetime.datetime.fromisoformat(created_at)
                    if dt.tzinfo is None:
                        dt = pytz.UTC.localize(dt)
                    dt = dt.astimezone(MSK)
                    date_str = dt.strftime('%d.%m.%Y %H:%M')
                except:
                    date_str = created_at
                
                response += f"{i}. {date_str} — _{short_text}_\n"
        
        response += f"\n💡 Теперь можно начать заново накапливать данные!"
        
        # Отправляем сообщение с кнопкой для проверки
        markup = types.InlineKeyboardMarkup(row_width=2)
        stats_btn = types.InlineKeyboardButton('📊 Проверить статистику', callback_data='check_embed_stats')
        menu_btn = types.InlineKeyboardButton('📋 Меню', callback_data='show_main_menu')
        markup.add(stats_btn, menu_btn)
        
        bot.send_message(message.chat.id, response, parse_mode='Markdown', reply_markup=markup)
        
    except Exception as e:
        print(f"❌ Ошибка при очистке embeddings: {e}")
        bot.send_message(message.chat.id, f"❌ Произошла ошибка: {str(e)[:100]}")
    finally:
        conn.close()


def utc_to_msk(utc_time_str):
    if not utc_time_str:
        return "не указано"
    try:
        # Убираем возможные Z в конце
        utc_time_str = utc_time_str.replace('Z', '')
        
        # Пробуем разные форматы
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']:
            try:
                utc_time = datetime.datetime.strptime(utc_time_str, fmt)
                break
            except:
                continue
        
        # Преобразуем в московское время
        msk_tz = pytz.timezone('Europe/Moscow')
        utc_time = pytz.UTC.localize(utc_time)
        msk_time = utc_time.astimezone(msk_tz)
        return msk_time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        return utc_time_str

def update_user_activity(user_id):
    """Увеличить счётчик токенов и обновить время"""
    conn = sqlite3.connect(config.DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Сначала проверим, существует ли пользователь
        cursor.execute('SELECT telegram_id FROM sessions WHERE telegram_id = ?', (user_id,))
        user_exists = cursor.fetchone()
        
        if user_exists:
            # Обновляем существующего пользователя
            cursor.execute('''
                UPDATE sessions 
                SET tokens_used = COALESCE(tokens_used, 0) + 1,
                    last_visit_at = datetime('now')
                WHERE telegram_id = ?
            ''', (user_id,))
        else:
            # Создаем новую запись, если пользователя нет
            cursor.execute('''
                INSERT INTO sessions 
                (telegram_id, accepted_terms, tokens_used, last_visit_at, status)
                VALUES (?, 0, 1, datetime('now'), 'active')
            ''', (user_id,))
        
        conn.commit()
    except Exception as e:
        print(f"Ошибка update_user_activity: {e}")
    finally:
        conn.close()

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_id = message.from_user.id
    update_user_activity(user_id)
    
    # Проверяем, принял ли пользователь условия
    session = session_storage.get_session(user_id)
    if not session or not session['accepted_terms']:
        bot.send_message(message.chat.id, "❌ Сначала примите условия использования (/start)")
        return
    
    photo_id = message.photo[-1].file_id
    data = session['data'].copy()
    
    # ✅ ПРОВЕРКА НА СЛИШКОМ ЧАСТЫЕ ФОТО
    last_analysis_time = data.get('last_analysis_time')
    if last_analysis_time:
        try:
            last_time = datetime.datetime.fromisoformat(last_analysis_time)
            now_time = datetime.datetime.now(MSK)
            time_diff = (now_time - last_time).total_seconds()
            if time_diff < 5:
                print(f"⏭️ ПРОПУСК: слишком часто, прошло {time_diff:.1f} сек")
                bot.send_message(message.chat.id, "⏳ Слишком часто! Подождите 5 секунд...")
                return
        except:
            pass
    
    # ✅ СОХРАНЯЕМ ID ФОТО
    data['last_photo_id'] = photo_id
    
    # ✅ ЗАПОМИНАЕМ РЕЖИМ ДО СБРОСА
    current_mode = data.get('awaiting_photo_type')
    print(f"📸 ТЕКУЩИЙ РЕЖИМ: {current_mode}")
    
    # ✅ ПРИНУДИТЕЛЬНО УДАЛЯЕМ РЕЖИМ ИЗ БД ПРЯМО СЕЙЧАС
    if 'awaiting_photo_type' in data:
        print(f"🔄 УДАЛЯЕМ РЕЖИМ: {data['awaiting_photo_type']}")
        del data['awaiting_photo_type']
        session_storage.save_session(user_id, data=data)
        
        # ✅ ПЕРЕЧИТЫВАЕМ СЕССИЮ ДВАЖДЫ для гарантии
        session = session_storage.get_session(user_id)
        data = session['data']
        print(f"✅ РЕЖИМ ПОСЛЕ УДАЛЕНИЯ: {data.get('awaiting_photo_type')}")
        
        # ✅ ФИНАЛЬНАЯ ПРОВЕРКА - если режим всё ещё есть, удаляем через БД напрямую
        if data.get('awaiting_photo_type'):
            print(f"⚠️ КРИТИЧЕСКАЯ ОШИБКА! Режим всё ещё есть! Принудительное удаление...")
            # Получаем прямой доступ к БД
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute('SELECT data FROM sessions WHERE telegram_id = ?', (user_id,))
            db_result = cursor.fetchone()
            if db_result and db_result[0]:
                db_data = json.loads(db_result[0])
                if 'awaiting_photo_type' in db_data:
                    del db_data['awaiting_photo_type']
                    cursor.execute('UPDATE sessions SET data = ? WHERE telegram_id = ?', 
                                 (json.dumps(db_data), user_id))
                    conn.commit()
            conn.close()
            # Перечитываем ещё раз
            session = session_storage.get_session(user_id)
            data = session['data']
            print(f"✅ ПОСЛЕ ПРЯМОГО УДАЛЕНИЯ: {data.get('awaiting_photo_type')}")
    
    # ✅ ЕСЛИ БЫЛ РЕЖИМ - ИСПОЛЬЗУЕМ ЕГО ДЛЯ ЭТОГО ФОТО
    if current_mode == 'food':
        bot.send_message(message.chat.id, "🍽 Анализирую фото еды...")
        class MockCall:
            def __init__(self, user_id, message):
                self.from_user = type('obj', (object,), {'id': user_id})()
                self.message = message
                self.data = 'analyze_food'
        mock_call = MockCall(user_id, message)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(analyze_food_photo(bot, mock_call, photo_id))
        finally:
            loop.close()
        return
        
    elif current_mode == 'body':
        bot.send_message(message.chat.id, "🏋️ Анализирую фото фигуры...")
        class MockCall:
            def __init__(self, user_id, message):
                self.from_user = type('obj', (object,), {'id': user_id})()
                self.message = message
                self.data = 'analyze_body'
        mock_call = MockCall(user_id, message)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(analyze_body_photo(bot, mock_call, photo_id))
        finally:
            loop.close()
        return
    
    # ✅ АВТО-РЕЖИМ (РЕЖИМА НЕ БЫЛО)
    wait_msg = bot.send_message(message.chat.id, "🔍 Анализирую фото...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(auto_analyze_photo(bot, user_id, message.chat.id, photo_id))
    finally:
        loop.close()
    return

@bot.message_handler(func=lambda message: True)
def handle_all_text(message):
    user_id = message.from_user.id

    # ✅ Если пользователь в режиме редактирования - не отправляем в GPT
    if user_id in editing_users:
        print(f"⏭️ Пользователь {user_id} в режиме редактирования {editing_users[user_id]}")
        return

    update_user_activity(user_id)
    
    session = session_storage.get_session(user_id)
    if not session or not session['accepted_terms']:
        bot.send_message(message.chat.id, "❌ Сначала примите условия использования (/start)")
        return
    
    # НЕ ОТПРАВЛЯЕМ В GPT, ТОЛЬКО ЕСЛИ МЫ В РЕЖИМЕ РЕДАКТИРОВАНИЯ
    if session['data'].get('editing_mode'):
        return
    
    # ✅ ПОКАЗЫВАЕМ ИНДИКАТОР ПЕЧАТИ ПРЯМО ЗДЕСЬ
    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except:
        pass
    
    # ОТПРАВЛЯЕМ ВСЕ ТЕКСТОВЫЕ СООБЩЕНИЯ В GPT
    asyncio.run(reply(bot, message))




# Файл с пользователями
USERS_FILE = "/var/www/dmtr.fvds.ru/users.json"
SESSIONS = {}  # простейшее хранилище сессий {session_id: username}

def load_users():
    """Загрузить пользователей из JSON"""
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    except:
        # Если файла нет, создаём с admin/admin
        users = {"admin": {"password": "admin", "role": "admin", "created_at": datetime.now().isoformat()}}
        save_users(users)
        return users

def save_users(users):
    """Сохранить пользователей в JSON"""
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def verify_user(username, password):
    """Проверить логин/пароль"""
    users = load_users()
    user = users.get(username)
    if user and user['password'] == password:
        return True
    return False

def create_session(username):
    """Создать сессию"""
    session_id = secrets.token_hex(16)
    SESSIONS[session_id] = {
        'username': username,
        'expires': datetime.datetime.now() + timedelta(hours=24)
    }
    return session_id

def verify_session(session_id):
    """Проверить сессию"""
    session = SESSIONS.get(session_id)
    if session and session['expires'] > datetime.datetime.now():
        return session['username']
    return None

# Страница логина
@app.get("/login", response_class=HTMLResponse)
async def login_page():
    with open("/var/www/dmtr.fvds.ru/login.html", "r") as f:
        return f.read()

# Обработка логина
@app.post("/login")
async def login(request: Request):
    data = await request.json()
    username = data.get('username')
    password = data.get('password')
    
    if verify_user(username, password):
        session_id = create_session(username)
        response = Response(status_code=200)
        response.set_cookie(
            key="session_id", 
            value=session_id,
            httponly=True,
            max_age=86400,  # 24 часа
            secure=True,
            samesite="lax"
        )
        return response
    raise HTTPException(status_code=401, detail="Неверный логин или пароль")

# Выход
@app.post("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id in SESSIONS:
        del SESSIONS[session_id]
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_id")
    return response

# Middleware для проверки авторизации
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Пропускаем запросы к login и статике
    if request.url.path in ["/login", "/icon.png"] or request.url.path.startswith("/bot-webhook"):
        return await call_next(request)
    
    # Проверяем сессию
    session_id = request.cookies.get("session_id")
    username = verify_session(session_id)
    
    if not username and request.url.path.startswith("/stats"):
        # Если нет сессии и пытается зайти на stats - редирект на login
        return RedirectResponse(url="/login")
    
    return await call_next(request)

# Обновлённый эндпоинт stats (просто возвращает HTML)
@app.get("/stats", response_class=HTMLResponse)
async def show_stats_page(request: Request):
    # Проверка уже есть в middleware, но для уверенности:
    session_id = request.cookies.get("session_id")
    if not verify_session(session_id):
        return RedirectResponse(url="/login")
    
    with open("/var/www/dmtr.fvds.ru/stats.html", "r") as f:
        return f.read()

# API для данных статистики (чтобы не мешать с HTML)
@app.get("/api/stats")
async def get_stats_data(request: Request):
    session_id = request.cookies.get("session_id")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            telegram_id,
            data,
            accepted_terms,
            registered_at,
            last_visit_at,
            status,
            tokens_used
        FROM sessions 
        ORDER BY registered_at DESC
    ''')
    users = cursor.fetchall()

    users_msk = []
    for user in users:
        user_list = list(user)
        user_list[3] = utc_to_msk(user[3])
        user_list[4] = utc_to_msk(user[4])
        users_msk.append(tuple(user_list))
    
    cursor.execute('SELECT COUNT(*) FROM sessions')
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM sessions WHERE accepted_terms = 1")
    accepted_terms = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM sessions WHERE status = 'active'")
    active_users = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(*) FROM sessions 
        WHERE last_visit_at >= datetime('now', '-24 hours')
    """)
    last_24h = cursor.fetchone()[0]

    conn.close()
    
    return {
        "users": users_msk,
        "total_users": total_users,
        "accepted_terms": accepted_terms,
        "active_users": active_users,
        "last_24h": last_24h,
        "update_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

# API для данных пользователя
@app.get("/api/stats/{telegram_id}")
async def get_user_data(request: Request, telegram_id: int):
    session_id = request.cookies.get("session_id")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            telegram_id,
            data,
            accepted_terms,
            registered_at,
            last_visit_at,
            status,
            tokens_used
        FROM sessions 
        WHERE telegram_id = ?
    ''', (telegram_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    try:
        user_data = json.loads(result[1]) if result[1] else {}
        data_json = json.dumps(user_data, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        data_json = f"Ошибка парсинга JSON: {str(e)}"
    
    return {
        "telegram_id": result[0],
        "data_json": data_json,
        "accepted_terms": bool(result[2]),
        "registered_at": utc_to_msk(result[3]),
        "last_visit_at": utc_to_msk(result[4]),
        "status": result[5],
        "tokens_used": result[6] or 0
    }

# Страница пользователя (HTML)
@app.get("/stats/{telegram_id}", response_class=HTMLResponse)
async def show_user_page(request: Request, telegram_id: int):
    session_id = request.cookies.get("session_id")
    if not verify_session(session_id):
        return RedirectResponse(url="/login")
    
    with open("/var/www/dmtr.fvds.ru/user_stats.html", "r") as f:
        html = f.read()
        # Подставляем ID пользователя в HTML
        html = html.replace("{{ user_id }}", str(telegram_id))
        return html

@app.delete("/delete-user/{telegram_id}")
async def delete_user(telegram_id: int):
    """
    Удаляет пользователя из БД и его embeddings
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Удаляем пользователя из sessions
        cursor.execute('DELETE FROM sessions WHERE telegram_id = ?', (telegram_id,))
        deleted = cursor.rowcount
        
        # Удаляем его embeddings (если есть)
        cursor.execute('DELETE FROM meal_embeddings WHERE telegram_id = ?', (telegram_id,))
        
        conn.commit()
        
        if deleted:
            return {"success": True, "message": f"Пользователь {telegram_id} удалён"}
        else:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
            
    except Exception as e:
        print(f"❌ Ошибка при удалении: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()


      # WEBHOOK ENDPOINT
@app.post(f"/bot-webhook/{TOKEN}")
async def webhook(request: Request):
    if request.headers.get("content-type") == "application/json":
        json_string = await request.body()
        update = telebot.types.Update.de_json(json_string.decode("utf-8"))
        bot.process_new_updates([update])
        return ""
    raise HTTPException(status_code=400, detail="Bad request")

# if __name__ == "__main__":
#     uvicorn.run(app, host="127.0.0.1", port=8080)
# else:
#     # Этот блок выполняется при запуске через uvicorn (systemd)
#     pass

# Держим бота запущенным
# if __name__ == "__main__":
#     while True:
#         time.sleep(60)

#        # ЗАПУСК ПРИЛОЖЕНИЯ
# if __name__ == "__main__":
#     print(f"🤖 Токен: {TOKEN[:10]}...")
#     print(f"📡 Webhook: {WEBHOOK_URL}")
    
#     bot.remove_webhook()
#     bot.set_webhook(url=WEBHOOK_URL)
    
#     uvicorn.run(
#         "main:app",
#         host=FASTAPI_HOST,
#         port=FASTAPI_PORT,
#         reload=False
#     )

