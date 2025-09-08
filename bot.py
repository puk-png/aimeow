import requests
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
import calendar
import logging
import re

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ВАЖЛИВО: Замініть на ваш справжній токен!
API_TOKEN = "8046378279:AAEjTOBDflR7gQufceQWgwTsr-gWzD1_Xxk"
BASE_URL = f"https://api.telegram.org/bot{API_TOKEN}"

# Глобальні змінні
user_states = {}  # Зберігання станів користувачів
user_data = {}    # Тимчасові дані користувачів
reminders_thread = None
stop_reminders = False

# Стани FSM
class States:
    WAITING_FOR_TEXT = "waiting_for_text"
    WAITING_FOR_TIME = "waiting_for_time"
    WAITING_FOR_DAYS = "waiting_for_days"
    WAITING_FOR_BIRTHDAY_NAME = "waiting_for_birthday_name"
    WAITING_FOR_BIRTHDAY_DATE = "waiting_for_birthday_date"

# --- База даних ---
def init_db():
    try:
        conn = sqlite3.connect("reminders.db")
        cursor = conn.cursor()
        
        # Таблиця нагадувань
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            text TEXT,
            hour INTEGER,
            minute INTEGER,
            days TEXT,
            one_time INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
        """)
        
        # Таблиця фото розкладу
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedule_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            photo_file_id TEXT,
            schedule_type TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Таблиця днів народження
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS birthdays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            name TEXT,
            birth_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ База даних ініціалізована")
    except Exception as e:
        logger.error(f"❌ Помилка ініціалізації БД: {e}")

def get_db_connection():
    return sqlite3.connect("reminders.db")

# --- ШІ обробка природної мови ---
class AIMessageProcessor:
    """Клас для обробки повідомлень природною мовою"""
    
    def __init__(self):
        self.date_patterns = [
            r'(\d{1,2})\.(\d{1,2})\.?(\d{0,4})?',  # 01.01 або 01.01.2024
            r'(\d{1,2})/(\d{1,2})/(\d{2,4})',      # 01/01/24
            r'(\d{1,2})-(\d{1,2})-?(\d{0,4})?',    # 01-01 або 01-01-2024
        ]
        
        self.time_patterns = [
            r'(\d{1,2}):(\d{2})',                   # 14:30
            r'(\d{1,2})\.(\d{2})',                  # 14.30
            r'(\d{1,2}) год(?:ин[иа])?',            # 14 год
        ]
        
        self.schedule_keywords = [
            'розклад', 'schedule', 'заплановано', 'план', 'що у мене',
            'нагадування', 'справи', 'діла', 'завдання'
        ]
        
        self.today_keywords = [
            'сьогодні', 'today', 'на сьогодні', 'цього дня'
        ]
        
        self.tomorrow_keywords = [
            'завтра', 'tomorrow', 'на завтра'
        ]
    
    def extract_date(self, text):
        """Витягує дату з тексту"""
        for pattern in self.date_patterns:
            match = re.search(pattern, text)
            if match:
                day, month = int(match.group(1)), int(match.group(2))
                year = None
                if match.group(3) and len(match.group(3)) > 0:
                    year_str = match.group(3)
                    if len(year_str) == 2:
                        year = 2000 + int(year_str)
                    else:
                        year = int(year_str)
                
                try:
                    if year:
                        return datetime(year, month, day)
                    else:
                        # Якщо рік не вказано, використовуємо поточний або наступний
                        current_year = datetime.now().year
                        date = datetime(current_year, month, day)
                        if date < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0):
                            date = datetime(current_year + 1, month, day)
                        return date
                except ValueError:
                    continue
        return None
    
    def extract_time(self, text):
        """Витягує час з тексту"""
        for pattern in self.time_patterns:
            match = re.search(pattern, text)
            if match:
                hour = int(match.group(1))
                minute = int(match.group(2)) if len(match.groups()) > 1 else 0
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return hour, minute
        return None
    
    def is_schedule_request(self, text):
        """Перевіряє, чи є це запит на розклад"""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.schedule_keywords)
    
    def get_date_context(self, text):
        """Визначає контекст дати (сьогодні, завтра, конкретна дата)"""
        text_lower = text.lower()
        
        if any(keyword in text_lower for keyword in self.today_keywords):
            return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        if any(keyword in text_lower for keyword in self.tomorrow_keywords):
            return (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Спробуємо витягти конкретну дату
        return self.extract_date(text)
    
    def process_natural_message(self, text, chat_id):
        """Основна функція обробки природного повідомлення"""
        if not self.is_schedule_request(text):
            return None
        
        target_date = self.get_date_context(text)
        
        if target_date:
            return self.get_schedule_for_date(chat_id, target_date)
        else:
            # Якщо дата не визначена, показуємо загальний розклад
            return self.get_general_schedule(chat_id)
    
    def get_schedule_for_date(self, chat_id, target_date):
        """Отримує розклад на конкретну дату"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Визначаємо день тижня
            day_name = calendar.day_name[target_date.weekday()].lower()[:3]
            
            # Отримуємо нагадування на цей день
            cursor.execute("""
                SELECT * FROM reminders 
                WHERE chat_id=? AND is_active=1 
                AND (days LIKE ? OR days LIKE ?)
                ORDER BY hour, minute
            """, (chat_id, f'%{day_name}%', '%mon,tue,wed,thu,fri,sat,sun%'))
            
            reminders = cursor.fetchall()
            
            # Отримуємо дні народження на цю дату
            date_str = f"{target_date.month:02d}-{target_date.day:02d}"
            cursor.execute("""
                SELECT * FROM birthdays 
                WHERE chat_id=? AND birth_date=?
            """, (chat_id, date_str))
            
            birthdays = cursor.fetchall()
            
            conn.close()
            
            # Формуємо відповідь
            date_formatted = target_date.strftime('%d.%m.%Y')
            day_name_uk = self.get_day_name_ukrainian(target_date.weekday())
            
            response = f"📅 Котик знайшов розклад на {date_formatted} ({day_name_uk}):\n\n"
            
            if reminders:
                response += "🔔 **Нагадування:**\n"
                for r in reminders:
                    response += f"⏰ {r[3]:02d}:{r[4]:02d} - {r[2]}\n"
                response += "\n"
            
            if birthdays:
                response += "🎂 **Дні народження:**\n"
                for b in birthdays:
                    response += f"🎉 {b[2]}\n"
                response += "\n"
            
            if not reminders and not birthdays:
                response += "🐱 Мяу! На цей день немає запланованих подій.\nКотик може допомогти додати щось нове!"
            
            return response
            
        except Exception as e:
            logger.error(f"Помилка отримання розкладу: {e}")
            return "❌ Котик спіткнувся при пошуку розкладу."
    
    def get_general_schedule(self, chat_id):
        """Отримує загальний розклад"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM reminders 
                WHERE chat_id=? AND is_active=1
                ORDER BY hour, minute
                LIMIT 10
            """, (chat_id,))
            
            reminders = cursor.fetchall()
            conn.close()
            
            if not reminders:
                return "🐱 Мяу! У тебе немає активних нагадувань.\nКотик може допомогти створити нове!"
            
            response = "📋 **Котик знайшов твої нагадування:**\n\n"
            for r in reminders:
                days_emoji = get_days_emoji(r[5])
                response += f"⏰ {r[3]:02d}:{r[4]:02d} - {r[2]}\n"
                response += f"📅 {r[5]} {days_emoji}\n\n"
            
            return response
            
        except Exception as e:
            logger.error(f"Помилка отримання загального розкладу: {e}")
            return "❌ Котик спіткнувся при пошуку розкладу."
    
    def get_day_name_ukrainian(self, weekday):
        """Повертає назву дня тижня українською"""
        days = ['понеділок', 'вівторок', 'середа', 'четвер', "п'ятниця", 'субота', 'неділя']
        return days[weekday]

# Ініціалізуємо AI процесор
ai_processor = AIMessageProcessor()

# --- Telegram API функції ---
def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    """Надсилання повідомлення"""
    try:
        # Обмежуємо довжину повідомлення
        if len(text) > 4096:
            text = text[:4093] + "..."
        
        data = {
            'chat_id': chat_id,
            'text': text
        }
        if reply_markup:
            data['reply_markup'] = json.dumps(reply_markup)
        if parse_mode:
            data['parse_mode'] = parse_mode
            
        response = requests.post(f"{BASE_URL}/sendMessage", data=data, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Помилка надсилання повідомлення: {e}")
        return None

def send_photo(chat_id, photo_file_id, caption=None, reply_markup=None):
    """Надсилання фото"""
    try:
        data = {
            'chat_id': chat_id,
            'photo': photo_file_id
        }
        if caption:
            data['caption'] = caption[:1024]  # Обмеження для caption
        if reply_markup:
            data['reply_markup'] = json.dumps(reply_markup)
            
        response = requests.post(f"{BASE_URL}/sendPhoto", data=data, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Помилка надсилання фото: {e}")
        return None

def edit_message_text(chat_id, message_id, text, reply_markup=None):
    """Редагування повідомлення"""
    try:
        if len(text) > 4096:
            text = text[:4093] + "..."
            
        data = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text
        }
        if reply_markup:
            data['reply_markup'] = json.dumps(reply_markup)
            
        response = requests.post(f"{BASE_URL}/editMessageText", data=data, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Помилка редагування повідомлення: {e}")
        return None

def answer_callback_query(callback_query_id, text=None):
    """Відповідь на callback query"""
    try:
        data = {'callback_query_id': callback_query_id}
        if text:
            data['text'] = text
        response = requests.post(f"{BASE_URL}/answerCallbackQuery", data=data, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Помилка відповіді на callback: {e}")
        return None

# --- Клавіатури з кнопками ---
def get_main_menu_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '➕ Додати нагадування', 'callback_data': 'add_reminder'}],
            [{'text': '📝 Мої нагадування', 'callback_data': 'list_reminders'}],
            [{'text': '🎂 Дні народження', 'callback_data': 'birthdays_menu'}],
            [{'text': '📅 Розклад', 'callback_data': 'schedule_menu'}],
            [{'text': '📸 Фото розкладу', 'callback_data': 'photos_menu'}],
            [{'text': 'ℹ️ Допомога', 'callback_data': 'help'}]
        ]
    }

def get_schedule_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '📅 Сьогодні', 'callback_data': 'schedule_today'},
             {'text': '📆 Завтра', 'callback_data': 'schedule_tomorrow'}],
            [{'text': '🗓️ Цей тиждень', 'callback_data': 'schedule_week'},
             {'text': '📊 Цей місяць', 'callback_data': 'schedule_month'}],
            [{'text': '🔙 Назад', 'callback_data': 'main_menu'}]
        ]
    }

def get_days_keyboard():
    return {
        'inline_keyboard': [
            [{'text': 'Будні', 'callback_data': 'days_weekdays'},
             {'text': 'Вихідні', 'callback_data': 'days_weekend'}],
            [{'text': 'Щодня', 'callback_data': 'days_daily'}],
            [{'text': '❌ Скасувати', 'callback_data': 'cancel_reminder'}]
        ]
    }

def get_birthday_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '➕ Додати день народження', 'callback_data': 'add_birthday'}],
            [{'text': '📝 Мої дні народження', 'callback_data': 'list_birthdays'}],
            [{'text': '🗑️ Видалити день народження', 'callback_data': 'delete_birthday_menu'}],
            [{'text': '🔙 Назад', 'callback_data': 'main_menu'}]
        ]
    }

def get_photos_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '📋 Переглянути фото', 'callback_data': 'view_photos'}],
            [{'text': '➕ Додати фото', 'callback_data': 'add_photo_info'}],
            [{'text': '🔙 Назад', 'callback_data': 'main_menu'}]
        ]
    }

def get_cancel_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '❌ Скасувати', 'callback_data': 'main_menu'}]
        ]
    }

def get_back_to_main_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '🔙 Головне меню', 'callback_data': 'main_menu'}]
        ]
    }

# --- Допоміжні функції ---
def get_days_emoji(days_str):
    days_map = {
        'mon': '🟢', 'tue': '🟡', 'wed': '🔵', 'thu': '🟠', 
        'fri': '🔴', 'sat': '🟣', 'sun': '⚪'
    }
    if not days_str:
        return ""
    
    days_list = [d.strip().lower() for d in days_str.split(',')]
    return ' '.join([days_map.get(day, '⚫') for day in days_list])

def set_user_state(user_id, state):
    user_states[user_id] = state

def get_user_state(user_id):
    return user_states.get(user_id)

def clear_user_state(user_id):
    user_states.pop(user_id, None)
    user_data.pop(user_id, None)

def set_user_data(user_id, key, value):
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id][key] = value

def get_user_data(user_id, key=None):
    if key:
        return user_data.get(user_id, {}).get(key)
    return user_data.get(user_id, {})

# --- Система нагадувань ---
def check_reminders():
    """Перевірка нагадувань кожну хвилину"""
    global stop_reminders
    
    while not stop_reminders:
        try:
            now = datetime.now()
            current_hour = now.hour
            current_minute = now.minute
            current_day = calendar.day_name[now.weekday()].lower()[:3]
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Перевірка звичайних нагадувань
            cursor.execute("""
                SELECT * FROM reminders 
                WHERE is_active=1 AND hour=? AND minute=? 
                AND (days LIKE ? OR days LIKE ?)
            """, (current_hour, current_minute, f'%{current_day}%', '%mon,tue,wed,thu,fri,sat,sun%'))
            
            reminders = cursor.fetchall()
            
            for reminder in reminders:
                reminder_id, chat_id, text = reminder[0], reminder[1], reminder[2]
                send_message(chat_id, f"🔔 Мяу! Нагадування:\n{text}", 
                           reply_markup=get_back_to_main_keyboard())
                logger.info(f"✅ Відправлено нагадування {reminder_id}")
                
                # Якщо однократне нагадування, видаляємо
                if len(reminder) > 6 and reminder[6] == 1:  # one_time
                    cursor.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
            
            # Перевірка днів народження (тільки о 00:00)
            if current_hour == 0 and current_minute == 0:
                current_month = now.month
                current_day_num = now.day
                
                cursor.execute("""
                    SELECT * FROM birthdays 
                    WHERE birth_date = ?
                """, (f"{current_month:02d}-{current_day_num:02d}",))
                
                birthdays = cursor.fetchall()
                
                for birthday in birthdays:
                    chat_id, name = birthday[1], birthday[2]
                    message = f"🎂 Мяу! Сьогодні день народження!\n\n" \
                              f"🎉 {name}\n" \
                              f"📅 {current_day_num:02d}.{current_month:02d}\n\n" \
                              f"Котик нагадує: не забудь привітати! 🎁"
                    
                    send_message(chat_id, message, reply_markup=get_back_to_main_keyboard())
                    logger.info(f"✅ Відправлено привітання з днем народження: {name}")
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Помилка перевірки нагадувань: {e}")
            try:
                if 'conn' in locals():
                    conn.close()
            except:
                pass
        
        # Спочивати 60 секунд
        time.sleep(60)

def start_reminder_thread():
    """Запуск потоку для перевірки нагадувань"""
    global reminders_thread
    if reminders_thread is None or not reminders_thread.is_alive():
        reminders_thread = threading.Thread(target=check_reminders, daemon=True)
        reminders_thread.start()
        logger.info("✅ Потік нагадувань запущено")

# --- Обробники команд ---
def handle_start_or_main_menu(chat_id, message_id=None):
    """Головне меню"""
    text = ("🐱 Мяу! Привіт! Я котик-нагадувач!\n\n"
            "Що я можу:\n"
            "➕ Створювати нагадування\n"
            "🎂 Нагадувати про дні народження\n" 
            "📅 Показувати розклад\n"
            "📸 Зберігати фото розкладу\n"
            "🤖 Розуміти природну мову!\n\n"
            "**Приклади запитів:**\n"
            "• \"котику, який у мене розклад на завтра?\"\n"
            "• \"що заплановано на 01.01?\"\n"
            "• \"покажи мої справи на сьогодні\"\n\n"
            "Котик готовий допомогти! Обирай що потрібно:")
    
    if message_id:
        edit_message_text(chat_id, message_id, text, reply_markup=get_main_menu_keyboard())
    else:
        send_message(chat_id, text, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')

def handle_help(chat_id, message_id=None):
    """Допомога"""
    help_text = ("🐱 Котик-помічник тут!\n\n"
                 "**Як користув=keyboard, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Помилка отримання списку нагадувань: {e}")
        error_text = "❌ Котик спіткнувся при отриманні списку нагадувань."
        if message_id:
            edit_message_text(chat_id, message_id, error_text, reply_markup=get_back_to_main_keyboard())
        else:
            send_message(chat_id, error_text, reply_markup=get_back_to_main_keyboard())

def handle_list_birthdays(chat_id, message_id=None):
    """Список днів народження"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM birthdays WHERE chat_id=? ORDER BY name", (chat_id,))
        birthdays = cursor.fetchall()
        conn.close()
        
        if not birthdays:
            text = "🐱 Мяу! У тебе немає збережених днів народження.\n\nКотик може допомогти додати!"
            keyboard = {
                'inline_keyboard': [
                    [{'text': '➕ Додати день народження', 'callback_data': 'add_birthday'}],
                    [{'text': '🔙 Назад', 'callback_data': 'birthdays_menu'}]
                ]
            }
        else:
            text = "🎂 **Котик знайшов дні народження:**\n\n"
            for b in birthdays:
                birthday_id, _, name, birth_date = b[:4]
                month, day = map(int, birth_date.split('-'))
                text += f"🔹 **{name}** (ID: {birthday_id})\n"
                text += f"📅 {day:02d}.{month:02d}\n\n"
            
            text += "Для видалення надішли: /delete_birthday [ID]\nНаприклад: /delete_birthday 1"
            keyboard = {
                'inline_keyboard': [
                    [{'text': '🔙 Назад', 'callback_data': 'birthdays_menu'}]
                ]
            }
        
        if message_id:
            edit_message_text(chat_id, message_id, text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            send_message(chat_id, text, reply_markup=keyboard, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Помилка отримання списку днів народження: {e}")
        error_text = "❌ Котик спіткнувся при отриманні списку днів народження."
        if message_id:
            edit_message_text(chat_id, message_id, error_text, reply_markup=get_back_to_main_keyboard())
        else:
            send_message(chat_id, error_text, reply_markup=get_back_to_main_keyboard())

def handle_view_photos(chat_id, message_id=None):
    """Перегляд фото"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM schedule_photos 
            WHERE chat_id=? 
            ORDER BY created_at DESC 
            LIMIT 10
        """, (chat_id,))
        photos = cursor.fetchall()
        conn.close()
        
        if not photos:
            text = "🐱 Мяу! У тебе немає збережених фото розкладу.\n\nКотик може допомогти додати!"
            keyboard = {
                'inline_keyboard': [
                    [{'text': '➕ Додати фото', 'callback_data': 'add_photo_info'}],
                    [{'text': '🔙 Назад', 'callback_data': 'photos_menu'}]
                ]
            }
            
            if message_id:
                edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
            else:
                send_message(chat_id, text, reply_markup=keyboard)
        else:
            if message_id:
                edit_message_text(chat_id, message_id, 
                                 f"🐱 Котик знайшов {len(photos)} фото розкладу:", 
                                 reply_markup=get_back_to_main_keyboard())
            else:
                send_message(chat_id, f"🐱 Котик знайшов {len(photos)} фото розкладу:", 
                            reply_markup=get_back_to_main_keyboard())
            
            for photo in photos:
                caption = f"📅 {photo[4]} (ID: {photo[0]})\n📆 {photo[6] if len(photo) > 6 else 'Дата не вказана'}"
                send_photo(chat_id, photo[2], caption=caption)
                
    except Exception as e:
        logger.error(f"Помилка перегляду фото: {e}")
        error_text = "❌ Котик спіткнувся при перегляді фото."
        if message_id:
            edit_message_text(chat_id, message_id, error_text, reply_markup=get_back_to_main_keyboard())
        else:
            send_message(chat_id, error_text, reply_markup=get_back_to_main_keyboard())

# --- Обробка станів FSM ---
def handle_state_message(message):
    user_id = message['from']['id']
    chat_id = message['chat']['id']
    state = get_user_state(user_id)
    
    try:
        if state == States.WAITING_FOR_TEXT:
            set_user_data(user_id, 'text', message['text'])
            set_user_state(user_id, States.WAITING_FOR_TIME)
            send_message(chat_id, "🐱 Мяу! Котик записав текст.\n\n⏰ Тепер введи час у форматі ГГ:ХХ (наприклад, 14:30):", 
                        reply_markup=get_cancel_keyboard())
            
        elif state == States.WAITING_FOR_TIME:
            try:
                time_parts = message['text'].replace('.', ':').split(":")
                if len(time_parts) != 2:
                    raise ValueError
                    
                hour, minute = map(int, time_parts)
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
                
                set_user_data(user_id, 'hour', hour)
                set_user_data(user_id, 'minute', minute)
                set_user_state(user_id, States.WAITING_FOR_DAYS)
                send_message(chat_id, "🐱 Відмінно! Котик запам'ятав час.\n\n📅 Тепер обери дні для нагадування:", 
                            reply_markup=get_days_keyboard())
            except:
                send_message(chat_id, "🐱 Мяу! Котик не розуміє такий час.\n\n❌ Введи у форматі ГГ:ХХ (наприклад, 09:30):", 
                            reply_markup=get_cancel_keyboard())
                
        elif state == States.WAITING_FOR_BIRTHDAY_NAME:
            if len(message['text'].strip()) == 0:
                send_message(chat_id, "🐱 Мяу! Ім'я не може бути пустим.\n\n📝 Введи ім'я людини:", 
                            reply_markup=get_cancel_keyboard())
                return
                
            set_user_data(user_id, 'birthday_name', message['text'].strip())
            set_user_state(user_id, States.WAITING_FOR_BIRTHDAY_DATE)
            send_message(chat_id, f"🐱 Котик запам'ятав ім'я: {message['text']}\n\n📅 Тепер введи дату народження у форматі ММ-ДД\n(наприклад: 03-15 для 15 березня):", 
                        reply_markup=get_cancel_keyboard())
            
        elif state == States.WAITING_FOR_BIRTHDAY_DATE:
            try:
                date_text = message['text'].replace('.', '-').replace('/', '-')
                date_parts = date_text.split('-')
                if len(date_parts) != 2:
                    raise ValueError
                
                month, day = map(int, date_parts)
                if not (1 <= month <= 12 and 1 <= day <= 31):
                    raise ValueError
                
                # Перевірка на коректність дати
                try:
                    datetime(2024, month, day)  # Перевіряємо в високосний рік
                except ValueError:
                    raise ValueError("Неправильна дата")
                
                name = get_user_data(user_id, 'birthday_name')
                birth_date = f"{month:02d}-{day:02d}"
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO birthdays (chat_id, name, birth_date) VALUES (?, ?, ?)",
                    (chat_id, name, birth_date)
                )
                conn.commit()
                conn.close()
                
                clear_user_state(user_id)
                send_message(chat_id, 
                    f"✅ Мяу! Котик додав день народження!\n\n"
                    f"🎂 Ім'я: {name}\n"
                    f"📅 Дата: {day:02d}.{month:02d}\n\n"
                    f"🐱 Котик буде нагадувати щороку о 00:00! 🎉",
                    reply_markup=get_back_to_main_keyboard())
                    
            except:
                send_message(chat_id, 
                    "🐱 Мяу! Котик не розуміє таку дату.\n\n"
                    "❌ Введи у форматі ММ-ДД (наприклад: 03-15 для 15 березня):", 
                    reply_markup=get_cancel_keyboard())
                    
    except Exception as e:
        logger.error(f"Помилка обробки стану {state}: {e}")
        clear_user_state(user_id)
        send_message(chat_id, "❌ Котик спіткнувся. Спробуй ще раз з головного меню.", 
                    reply_markup=get_main_menu_keyboard())

# --- Обробка callback запитів ---
def handle_callback_query(callback_query):
    try:
        chat_id = callback_query['message']['chat']['id']
        message_id = callback_query['message']['message_id']
        user_id = callback_query['from']['id']
        data = callback_query['data']
        
        # Головне меню
        if data == 'main_menu':
            clear_user_state(user_id)
            handle_start_or_main_menu(chat_id, message_id)
        
        # Допомога
        elif data == 'help':
            handle_help(chat_id, message_id)
        
        # Додавання нагадування
        elif data == 'add_reminder':
            set_user_state(user_id, States.WAITING_FOR_TEXT)
            edit_message_text(chat_id, message_id, 
                             "🐱 Котик готовий допомогти!\n\n📝 Введи текст нагадування:", 
                             reply_markup=get_cancel_keyboard())
        
        # Скасування нагадування
        elif data == 'cancel_reminder':
            clear_user_state(user_id)
            edit_message_text(chat_id, message_id, 
                             "🐱 Мяу! Котик скасував створення нагадування.", 
                             reply_markup=get_back_to_main_keyboard())
        
        # Список нагадувань
        elif data == 'list_reminders':
            handle_list_reminders(chat_id, message_id)
        
        # Дні нагадування
        elif data.startswith('days_'):
            state = get_user_state(user_id)
            if state == States.WAITING_FOR_DAYS:
                user_data_dict = get_user_data(user_id)
                
                days_map = {
                    'days_weekdays': ('mon,tue,wed,thu,fri', 'Будні'),
                    'days_weekend': ('sat,sun', 'Вихідні'),
                    'days_daily': ('mon,tue,wed,thu,fri,sat,sun', 'Щодня')
                }
                
                if data in days_map:
                    days, days_text = days_map[data]
                    
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO reminders (chat_id,text,hour,minute,days,one_time) VALUES (?,?,?,?,?,?)",
                        (chat_id, user_data_dict['text'], user_data_dict['hour'], 
                         user_data_dict['minute'], days, 0)
                    )
                    conn.commit()
                    conn.close()
                    
                    edit_message_text(chat_id, message_id,
                        f"✅ Мяу! Котик створив нагадування!\n\n"
                        f"📝 Текст: {user_data_dict['text']}\n"
                        f"⏰ Час: {user_data_dict['hour']:02d}:{user_data_dict['minute']:02d}\n"
                        f"📅 Дні: {days_text} {get_days_emoji(days)}\n\n"
                        f"🐱 Котик буде нагадувати! 🔔",
                        reply_markup=get_back_to_main_keyboard()
                    )
                    
                    clear_user_state(user_id)
        
        # Меню днів народження
        elif data == 'birthdays_menu':
            edit_message_text(chat_id, message_id, 
                             "🎂 Котик керує днями народження!", 
                             reply_markup=get_birthday_keyboard())
        
        # Додавання дня народження
        elif data == 'add_birthday':
            set_user_state(user_id, States.WAITING_FOR_BIRTHDAY_NAME)
            edit_message_text(chat_id, message_id, 
                             "🐱 Котик готовий запам'ятати день народження!\n\n🎂 Введи ім'я людини:", 
                             reply_markup=get_cancel_keyboard())
        
        # Список днів народження
        elif data == 'list_birthdays':
            handle_list_birthdays(chat_id, message_id)
        
        # Меню розкладу
        elif data == 'schedule_menu':
            edit_message_text(chat_id, message_id, 
                             "📅 Котик покаже розклад!", 
                             reply_markup=get_schedule_keyboard())
        
        # Розклад по періодах
        elif data.startswith('schedule_'):
            period = data.split('_')[1]
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            now = datetime.now()
            
            if period == 'today':
                day_name = calendar.day_name[now.weekday()].lower()[:3]
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1 AND (days LIKE ? OR days LIKE ?)
                    ORDER BY hour, minute
                """, (chat_id, f'%{day_name}%', '%mon,tue,wed,thu,fri,sat,sun%'))
                title = f"📅 Котик знайшов розклад на сьогодні ({now.strftime('%d.%m.%Y')})"
                
            elif period == 'tomorrow':
                tomorrow = now + timedelta(days=1)
                day_name = calendar.day_name[tomorrow.weekday()].lower()[:3]
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1 AND (days LIKE ? OR days LIKE ?)
                    ORDER BY hour, minute
                """, (chat_id, f'%{day_name}%', '%mon,tue,wed,thu,fri,sat,sun%'))
                title = f"📆 Котик знайшов розклад на завтра ({tomorrow.strftime('%d.%m.%Y')})"
                
            elif period == 'week':
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1
                    ORDER BY hour, minute
                """, (chat_id,))
                title = "🗓️ Котик знайшов розклад на тиждень"
                
            elif period == 'month':
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1
                    ORDER BY hour, minute
                """, (chat_id,))
                title = "📊 Котик знайшов розклад на місяць"
            
            reminders = cursor.fetchall()
            conn.close()
            
            if not reminders:
                text = f"{title}\n\n🐱 Мяу! Немає запланованих нагадувань.\nКотик може допомогти додати!"
                keyboard = {
                    'inline_keyboard': [
                        [{'text': '➕ Додати нагадування', 'callback_data': 'add_reminder'}],
                        [{'text': '🔙 Назад', 'callback_data': 'schedule_menu'}]
                    ]
                }
            else:
                text = f"{title}\n\n"
                for r in reminders:
                    days_emoji = get_days_emoji(r[5])
                    text += f"⏰ {r[3]:02d}:{r[4]:02d} - {r[2]}\n"
                    text += f"📅 {r[5]} {days_emoji}\n\n"
                
                keyboard = {
                    'inline_keyboard': [
                        [{'text': '🔙 Назад', 'callback_data': 'schedule_menu'}]
                    ]
                }
            
            edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
        
        # Меню фото
        elif data == 'photos_menu':
            edit_message_text(chat_id, message_id, 
                             "📸 Котик керує фото розкладу!", 
                             reply_markup=get_photos_keyboard())
        
        # Перегляд фото
        elif data == 'view_photos':
            handle_view_photos(chat_id, message_id)
        
        # Інформація про додавання фото
        elif data == 'add_photo_info':
            edit_message_text(chat_id, message_id, 
                             "📸 Котик готовий зберегти фото!\n\n"
                             "🐱 Просто надішли мені фото розкладу, і котик попросить вибрати тип (день/тиждень/місяць).", 
                             reply_markup=get_back_to_main_keyboard())
        
        # Збереження фото з типом
        elif data.startswith('save_photo_'):
            parts = data.split('_')
            if len(parts) >= 4:
                schedule_type = parts[2]
                file_id = '_'.join(parts[3:])
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO schedule_photos (chat_id, photo_file_id, schedule_type, description)
                    VALUES (?, ?, ?, ?)
                """, (chat_id, file_id, schedule_type, f"Розклад ({schedule_type})"))
                conn.commit()
                conn.close()
                
                type_names = {'day': 'день', 'week': 'тиждень', 'month': 'місяць'}
                
                edit_message_text(chat_id, message_id,
                    f"✅ Мяу! Котик зберіг фото розкладу на {type_names.get(schedule_type, schedule_type)}!\n\n"
                    f"🐱 Можна переглянути у меню фото.",
                    reply_markup=get_back_to_main_keyboard()
                )
        
        answer_callback_query(callback_query['id'])
        
    except Exception as e:
        logger.error(f"Помилка обробки callback запиту: {e}")
        try:
            answer_callback_query(callback_query['id'], "❌ Котик спіткнувся")
        except:
            pass

# --- Обробка фото ---
def handle_photo(message):
    try:
        chat_id = message['chat']['id']
        photo = message['photo'][-1]  # Найбільше фото
        
        keyboard = {
            'inline_keyboard': [
                [{'text': '📅 День', 'callback_data': f'save_photo_day_{photo["file_id"]}'},
                 {'text': '🗓️ Тиждень', 'callback_data': f'save_photo_week_{photo["file_id"]}'}],
                [{'text': '📊 Місяць', 'callback_data': f'save_photo_month_{photo["file_id"]}'}],
                [{'text': '❌ Скасувати', 'callback_data': 'photos_menu'}]
            ]
        }
        
        send_message(chat_id, "📸 Мяу! Котик отримав фото!\n\n🐱 Для якого періоду це розклад?", 
                    reply_markup=keyboard)
                    
    except Exception as e:
        logger.error(f"Помилка обробки фото: {e}")
        send_message(message['chat']['id'], "❌ Котик спіткнувся при обробці фото.", 
                    reply_markup=get_back_to_main_keyboard())

# --- Обробка команд видалення ---
def handle_delete_reminder(message):
    try:
        chat_id = message['chat']['id']
        args_text = message['text'].replace('/delete', '').strip()
        if not args_text:
            send_message(chat_id, "🐱 Мяу! Котик не розуміє.\n\n❌ Вкажи ID нагадування: /delete 123", 
                        reply_markup=get_back_to_main_keyboard())
            return
        
        reminder_id = int(args_text)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id=? AND chat_id=?", 
                      (reminder_id, chat_id))
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            send_message(chat_id, f"✅ Мяу! Котик видалив нагадування {reminder_id}.", 
                        reply_markup=get_back_to_main_keyboard())
        else:
            send_message(chat_id, "❌ Котик не знайшов таке нагадування.", 
                        reply_markup=get_back_to_main_keyboard())
            
    except ValueError:
        send_message(chat_id, "❌ ID повинен бути числом.", 
                    reply_markup=get_back_to_main_keyboard())
    except Exception as e:
        logger.error(f"Помилка видалення нагадування: {e}")
        send_message(chat_id, f"❌ Котик спіткнувся: {str(e)}", 
                    reply_markup=get_back_to_main_keyboard())

def handle_delete_birthday(message):
    try:
        chat_id = message['chat']['id']
        args_text = message['text'].replace('/delete_birthday', '').strip()
        if not args_text:
            send_message(chat_id, "🐱 Мяу! Котик не розуміє.\n\n❌ Вкажи ID дня народження: /delete_birthday 123", 
                        reply_markup=get_back_to_main_keyboard())
            return
        
        birthday_id = int(args_text)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM birthdays WHERE id=? AND chat_id=?", 
                      (birthday_id, chat_id))
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_count > 0:
            send_message(chat_id, f"✅ Мяу! Котик видалив день народження {birthday_id}.", 
                        reply_markup=get_back_to_main_keyboard())
        else:
            send_message(chat_id, "❌ Котик не знайшов такий день народження.", 
                        reply_markup=get_back_to_main_keyboard())
            
    except ValueError:
        send_message(chat_id, "❌ ID повинен бути числом.", 
                    reply_markup=get_back_to_main_keyboard())
    except Exception as e:
        logger.error(f"Помилка видалення дня народження: {e}")
        send_message(chat_id, f"❌ Котик спіткнувся: {str(e)}", 
                    reply_markup=get_back_to_main_keyboard())

# --- Основна функція обробки повідомлень ---
def handle_message(message):
    try:
        text = message.get('text', '')
        user_id = message['from']['id']
        chat_id = message['chat']['id']
        
        # Перевіряємо стан користувача
        if get_user_state(user_id):
            handle_state_message(message)
            return
        
        # Обробка команд
        if text in ['/start', '/menu']:
            handle_start_or_main_menu(chat_id)
        elif text in ['/help', 'допомога', 'Допомога', 'помощь', 'Помощь']:
            handle_help(chat_id)
        elif text.startswith('/delete_birthday'):
            handle_delete_birthday(message)
        elif text.startswith('/delete'):
            handle_delete_reminder(message)
        else:
            # Спробуємо обробити як запит природною мовою
            ai_response = ai_processor.process_natural_message(text, chat_id)
            
            if ai_response:
                send_message(chat_id, ai_response, 
                           reply_markup=get_back_to_main_keyboard(), 
                           parse_mode='Markdown')
            else:
                # Невідома команда
                send_message(chat_id, 
                    "🐱 Мяу! Котик не розуміє цю команду.\n\n"
                    "Спробуй головне меню або надішли /help для допомоги!\n\n"
                    "**Або спитай природною мовою:**\n"
                    "• \"котику, що у мене сьогодні?\"\n"
                    "• \"покажи розклад на завтра\"\n"
                    "• \"які справи на 15.03?\"",
                    reply_markup=get_main_menu_keyboard(),
                    parse_mode='Markdown'
                )
                
    except Exception as e:
        logger.error(f"Помилка обробки повідомлення: {e}")
        send_message(message['chat']['id'], "❌ Котик спіткнувся при обробці повідомлення.", 
                    reply_markup=get_main_menu_keyboard())

# --- Основний цикл бота ---
def get_updates(offset=None):
    """Отримання оновлень від Telegram"""
    url = f"{BASE_URL}/getUpdates"
    params = {'timeout': 30, 'offset': offset}
    
    try:
        response = requests.get(url, params=params, timeout=35)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"HTTP Error {response.status_code}: {response.text}")
            return None
    except requests.exceptions.Timeout:
        logger.warning("Timeout при отриманні оновлень")
        return None
    except Exception as e:
        logger.error(f"Помилка отримання оновлень: {e}")
        return None

def run_bot():
    """Основний цикл бота"""
    logger.info("🐱 Котик-бот запускається...")
    
    # Ініціалізація
    init_db()
    start_reminder_thread()
    
    logger.info("✅ Котик готовий до роботи!")
    
    offset = None
    consecutive_errors = 0
    
    while True:
        try:
            updates = get_updates(offset)
            
            if updates and updates.get('ok'):
                consecutive_errors = 0  # Скидаємо лічильник помилок
                
                for update in updates['result']:
                    try:
                        offset = update['update_id'] + 1
                        
                        if 'message' in update:
                            message = update['message']
                            
                            # Обробка фото
                            if 'photo' in message:
                                handle_photo(message)
                            # Обробка текстових повідомлень
                            elif 'text' in message:
                                handle_message(message)
                        
                        elif 'callback_query' in update:
                            handle_callback_query(update['callback_query'])
                            
                    except Exception as e:
                        logger.error(f"Помилка обробки update {update.get('update_id', 'unknown')}: {e}")
                        continue
            else:
                consecutive_errors += 1
                if consecutive_errors > 5:
                    logger.error("Занадто багато помилок поспіль, збільшуємо затримку")
                    time.sleep(10)
                    consecutive_errors = 0
            
            time.sleep(0.1)  # Невелика затримка
            
        except KeyboardInterrupt:
            logger.info("🐱 Котик йде спати...")
            global stop_reminders
            stop_reminders = True
            if reminders_thread and reminders_thread.is_alive():
                reminders_thread.join(timeout=5)
            logger.info("✅ Котик заснув")
            break
        except Exception as e:
            logger.error(f"❌ Котик спіткнувся: {e}")
            consecutive_errors += 1
            sleep_time = min(5 * consecutive_errors, 60)  # Поступово збільшуємо затримку
            time.sleep(sleep_time)

if __name__ == "__main__":
    try:
        run_bot()
    except Exception as e:
        logger.error(f"❌ Котик сильно спіткнувся: {e}")
