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
reminders_thread = # --- Обробка callback запитів ---
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
        
        # Вибір типу нагадування
        elif data == 'reminder_type_recurring':
            set_user_data(user_id, 'reminder_type', 'recurring')
            set_user_state(user_id, States.WAITING_FOR_TIME)
            edit_message_text(chat_id, message_id, 
                             "🔁 Регулярне нагадування\n\n⏰ Введи час у форматі ГГ:ХХ (наприклад, 14:30):", 
                             reply_markup=get_cancel_keyboard())
        
        elif data == 'reminder_type_onetime':
            set_user_data(user_id, 'reminder_type', 'one_time')
            set_user_state(user_id, States.WAITING_FOR_TIME)
            edit_message_text(chat_id, message_id, 
                             "📌 Одноразове нагадування\n\n⏰ Введи час у форматі ГГ:ХХ (наприклад, 14:30):", 
                             reply_markup=get_cancel_keyboard())
        
        # Список нагадувань
        elif data == 'list_reminders':
            handle_list_reminders(chat_id, message_id)
        
        # Дні нагадування для регулярних
        elif data.startswith('days_'):
            state = get_user_state(user_id)
            if state == States.WAITING_FOR_DAYS:
                user_data_dict = get_user_data(user_id)
                
                if data == 'days_custom':
                    # Вибіркові дні - показуємо клавіатуру з днями тижня
                    set_user_data(user_id, 'selected_days', [])
                    edit_message_text(chat_id, message_id,
                        "🐱 Обери дні тижня для нагадування:\n(Можна вибрати кілька)",
                        reply_markup=get_weekdays_keyboard())
                    return
                
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
                        """INSERT INTO reminders (chat_id, text, hour, minute, days, one_time, reminder_type) 
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (chat_id, user_data_dict['text'], user_data_dict['hour'], 
                         user_data_dict['minute'], days, 0, 'recurring')
                    )
                    conn.commit()
                    conn.close()
                    
                    edit_message_text(chat_id, message_id,
                        f"✅ Котик створив регулярне нагадування!\n\n"
                        f"📝 Текст: {user_data_dict['text']}\n"
                        f"⏰ Час: {user_data_dict['hour']:02d}:{user_data_dict['minute']:02d}\n"
                        f"📅 Дні: {days_text} {get_days_emoji(days)}\n\n"
                        f"🐱 Котик буде нагадувати! 🔁",
                        reply_markup=get_back_to_main_keyboard()
                    )
                    
                    clear_user_state(user_id)
        
        # Вибір окремих днів тижня
        elif data.startswith('day_'):
            state = get_user_state(user_id)
            if state == States.WAITING_FOR_DAYS:
                day = data.split('_')[1]
                selected_days = get_user_data(user_id, 'selected_days') or []
                
                if day in selected_days:
                    selected_days.remove(day)
                else:
                    selected_days.append(day)
                
                set_user_data(user_id, 'selected_days', selected_days)
                
                # Оновлюємо клавіатуру, щоб показати вибрані дні
                keyboard = get_weekdays_keyboard()
                for row in keyboard['inline_keyboard']:
                    for button in row:
                        if button['callback_data'].startswith('day_'):
                            button_day = button['callback_data'].split('_')[1]
                            if button_day in selected_days:
                                button['text'] = '✅ ' + button['text']
                
                selected_text = "Вибрані дні: " + ', '.join(selected_days) if selected_days else "Дні не вибрані"
                
                edit_message_text(chat_id, message_id,
                    f"🐱 Обери дні тижня для нагадування:\n\n{selected_text}",
                    reply_markup=keyboard)
        
        # Завершення вибору днів
        elif data == 'days_selected':
            state = get_user_state(user_id)
            if state == States.WAITING_FOR_DAYS:
                selected_days = get_user_data(user_id, 'selected_days') or []
                
                if not selected_days:
                    edit_message_text(chat_id, message_id,
                        "🐱 Потрібно вибрати хоча б один день!\n\nОбери дні тижня:",
                        reply_markup=get_weekdays_keyboard())
                    return
                
                user_data_dict = get_user_data(user_id)
                days_string = ','.join(selected_days)
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO reminders (chat_id, text, hour, minute, days, one_time, reminder_type) 
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (chat_id, user_data_dict['text'], user_data_dict['hour'], 
                     user_data_dict['minute'], days_string, 0, 'recurring')
                )
                conn.commit()
                conn.close()
                
                days_names = {'mon': 'Пн', 'tue': 'Вт', 'wed': 'Ср', 'thu': 'Чт', 'fri': 'Пт', 'sat': 'Сб', 'sun': 'Нд'}
                days_text = ', '.join([days_names.get(day, day) for day in selected_days])
                
                edit_message_text(chat_id, message_id,
                    f"✅ Котик створив регулярне нагадування!\n\n"
                    f"📝 Текст: {user_data_dict['text']}\n"
                    f"⏰ Час: {user_data_dict['hour']:02d}:{user_data_dict['minute']:02d}\n"
                    f"📅 Дні: {days_text} {get_days_emoji(days_string)}\n\n"
                    f"🐱 Котик буде нагадувати! 🔁",
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
                today_str = now.strftime('%Y-%m-%d')
                
                # Регулярні нагадування
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1 AND reminder_type='recurring'
                    AND (days LIKE ? OR days LIKE ?)
                    ORDER BY hour, minute
                """, (chat_id, f'%{day_name}%', '%mon,tue,wed,thu,fri,sat,sun%'))
                recurring = cursor.fetchall()
                
                # Одноразові на сьогодні
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1 AND reminder_type='one_time'
                    AND specific_date=?
                    ORDER BY hour, minute
                """, (chat_id, today_str))
                onetime = cursor.fetchall()
                
                title = f"📅 Розклад на сьогодні ({now.strftime('%d.%m.%Y')})"
                
            elif period == 'tomorrow':
                tomorrow = now + timedelta(days=1)
                day_name = calendar.day_name[tomorrow.weekday()].lower()[:3]
                tomorrow_str = tomorrow.strftime('%Y-%m-%d')
                
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1 AND reminder_type='recurring'
                    AND (days LIKE ? OR days LIKE ?)
                    ORDER BY hour, minute
                """, (chat_id, f'%{day_name}%', '%mon,tue,wed,thu,fri,sat,sun%'))
                recurring = cursor.fetchall()
                
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1 AND reminder_type='one_time'
                    AND specific_date=?
                    ORDER BY hour, minute
                """, (chat_id, tomorrow_str))
                onetime = cursor.fetchall()
                
                title = f"📆 Розклад на завтра ({tomorrow.strftime('%d.%m.%Y')})"
                
            elif period == 'week':
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1
                    ORDER BY reminder_type, hour, minute
                """, (chat_id,))
                all_reminders = cursor.fetchall()
                recurring = [r for r in all_reminders if len(r) > 7 and r[7] == 'recurring']
                onetime = [r for r in all_reminders if len(r) > 7 and r[7] == 'one_time']
                title = "🗓️ Розклад на тиждень"
                
            elif period == 'month':
                cursor.execute("""
                    SELECT * FROM reminders 
                    WHERE chat_id=? AND is_active=1
                    ORDER BY reminder_type, hour, minute
                """, (chat_id,))
                all_reminders = cursor.fetchall()
                recurring = [r for r in all_reminders if len(r) > 7 and r[7] == 'recurring']
                onetime = [r for r in all_reminders if len(r) > 7 and r[7] == 'one_time']
                title = "📊 Розклад на місяць"
            
            conn.close()
            
            if not recurring and not onetime:
                text = f"{title}\n\n🐱 Немає запланованих нагадувань.\nКотик може допомогти додати!"
                keyboard = {
                    'inline_keyboard': [
                        [{'text': '➕ Додати нагадування', 'callback_data': 'add_reminder'}],
                        [{'text': '🔙 Назад', 'callback_data': 'schedule_menu'}]
                    ]
                }
            else:
                text = f"{title}\n\n"
                
                all_reminders = list(recurring) + list(onetime)
                all_reminders.sort(key=lambda x: (x[3], x[4]))  # Сортуємо за часом
                
                for r in all_reminders:
                    reminder_type = r[7] if len(r) > 7 else 'recurring'
                    type_emoji = "🔁" if reminder_type == 'recurring' else "📌"
                    
                    text += f"{type_emoji} {r[3]:02d}:{r[4]:02d} - {r[2]}\n"
                    
                    if reminder_type == 'recurring' and r[5]:
                        days_emoji = get_days_emoji(r[5])
                        text += f"📅 {r[5]} {days_emoji}\n"
                    elif reminder_type == 'one_time' and len(r) > 8 and r[8]:
                        try:
                            date_obj = datetime.strptime(r[8], '%Y-%m-%d')
                            text += f"📅 {date_obj.strftime('%d.%m.%Y')}\n"
                        except:
                            pass
                    
                    text += "\n"
                
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
                             "🐱 Просто надішли мені фото розкладу, і котик попросить вибрати тип.", 
                             reply_markup=get_back_to_main_keyboard())
        
        # Вибір типу фото
        elif data.startswith('photo_type_'):
            photo_type = data.split('_')[2]
            photo_file_id = get_user_data(user_id, 'photo_file_id')
            
            if photo_file_id:
                set_user_data(user_id, 'photo_type', photo_type)
                set_user_state(user_id, States.WAITING_FOR_PHOTO_TYPE)
                
                type_names = {'day': 'день', 'week': 'тиждень', 'month': 'місяць'}
                edit_message_text(chat_id, message_id,
                    f"📸 Тип фото: {type_names.get(photo_type, photo_type)}\n\n"
                    f"📝 Введи опис для фото (наприклад: 'Розклад на понеділок'):",
                    reply_markup=get_cancel_keyboard())
        
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
        user_id = message['from']['id']
        photo = message['photo'][-1]  # Найбільше фото
        
        # Зберігаємо file_id фото
        set_user_data(user_id, 'photo_file_id', photo['file_id'])
        
        keyboard = get_photo_type_keyboard()
        
        send_message(chat_id, "📸 Котик отримав фото!\n\n🐱 Для якого періоду це розклад?", 
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
            send_message(chat_id, "🐱 Котик не розуміє.\n\n❌ Вкажи ID нагадування: /delete 123", 
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
            send_message(chat_id, f"✅ Котик видалив нагадування {reminder_id}.", 
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
            send_message(chat_id, "🐱 Котик не розуміє.\n\n❌ Вкажи ID дня народження: /delete_birthday 123", 
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
            send_message(chat_id, f"✅ Котик видалив день народження {birthday_id}.", 
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
                    "🐱 Котик не розуміє цю команду.\n\n"
                    "Спробуй головне меню або надішли /help для допомоги!\n\n"
                    "**Або спитай природною мовою:**\n"
                    "• \"котику, що у мене сьогодні?\"\n"
                    "• \"покажи розклад на завтра\"\n"
                    "• \"які справи на 15.03?\"\n"
                    "• \"додай нагадування завтра о 15:00\"",
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
                consecutive_errors = 0
                
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
            
            time.sleep(0.1)
            
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
            sleep_time = min(5 * consecutive_errors, 60)
            time.sleep(sleep_time)

if __name__ == "__main__":
    try:
        run_bot()
    except Exception as e:
        logger.error(f"❌ Котик сильно спіткнувся: {e}")
stop_reminders = False

# Стани FSM
class States:
    WAITING_FOR_TEXT = "waiting_for_text"
    WAITING_FOR_TIME = "waiting_for_time"
    WAITING_FOR_DAYS = "waiting_for_days"
    WAITING_FOR_SPECIFIC_DAYS = "waiting_for_specific_days"
    WAITING_FOR_BIRTHDAY_NAME = "waiting_for_birthday_name"
    WAITING_FOR_BIRTHDAY_DATE = "waiting_for_birthday_date"
    WAITING_FOR_PHOTO_TYPE = "waiting_for_photo_type"
    WAITING_FOR_REMINDER_TYPE = "waiting_for_reminder_type"

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
            reminder_type TEXT DEFAULT 'recurring',
            specific_date TEXT,
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

# --- Розширена ШІ обробка природної мови ---
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
            r'о?(\d{1,2}) год(?:ин[иа])?',          # 14 год, о 14 годині
            r'в (\d{1,2}):(\d{2})',                 # в 14:30
        ]
        
        # Розширені ключові слова для розпізнавання
        self.schedule_keywords = [
            'розклад', 'schedule', 'заплановано', 'план', 'що у мене', 'що в мене',
            'нагадування', 'справи', 'діла', 'завдання', 'подія', 'події', 'зустріч'
        ]
        
        self.add_keywords = [
            'додай', 'добавь', 'створи', 'нагадай', 'запланій', 'зробити',
            'треба', 'потрібно', 'не забути', 'важливо'
        ]
        
        self.birthday_keywords = [
            'день народження', 'др', 'birthday', 'народився', 'народилася'
        ]
        
        self.today_keywords = [
            'сьогодні', 'today', 'на сьогодні', 'цього дня', 'зараз'
        ]
        
        self.tomorrow_keywords = [
            'завтра', 'tomorrow', 'на завтра'
        ]
        
        self.week_days = {
            'понеділок': 'mon', 'monday': 'mon', 'пн': 'mon',
            'вівторок': 'tue', 'tuesday': 'tue', 'вт': 'tue',
            'середа': 'wed', 'wednesday': 'wed', 'ср': 'wed',
            'четвер': 'thu', 'thursday': 'thu', 'чт': 'thu',
            'п\'ятниця': 'fri', 'friday': 'fri', 'пт': 'fri',
            'субота': 'sat', 'saturday': 'sat', 'сб': 'sat',
            'неділя': 'sun', 'sunday': 'sun', 'нд': 'sun'
        }
        
        # Фрази для видалення
        self.delete_keywords = [
            'видали', 'удали', 'delete', 'прибери', 'скасуй'
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
                if len(match.groups()) == 2:
                    hour = int(match.group(1))
                    minute = int(match.group(2))
                else:
                    hour = int(match.group(1))
                    minute = 0
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return hour, minute
        return None
    
    def extract_weekday(self, text):
        """Витягує день тижня з тексту"""
        text_lower = text.lower()
        for day_name, day_code in self.week_days.items():
            if day_name in text_lower:
                return day_code
        return None
    
    def is_schedule_request(self, text):
        """Перевіряє, чи є це запит на розклад"""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.schedule_keywords)
    
    def is_add_request(self, text):
        """Перевіряє, чи є це запит на додавання"""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.add_keywords)
    
    def is_birthday_request(self, text):
        """Перевіряє, чи є це запит про день народження"""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.birthday_keywords)
    
    def is_delete_request(self, text):
        """Перевіряє, чи є це запит на видалення"""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.delete_keywords)
    
    def get_date_context(self, text):
        """Визначає контекст дати"""
        text_lower = text.lower()
        
        if any(keyword in text_lower for keyword in self.today_keywords):
            return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        if any(keyword in text_lower for keyword in self.tomorrow_keywords):
            return (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Перевіряємо день тижня
        weekday = self.extract_weekday(text)
        if weekday:
            return self.get_next_weekday_date(weekday)
        
        return self.extract_date(text)
    
    def get_next_weekday_date(self, target_day):
        """Отримує дату наступного вказаного дня тижня"""
        days_order = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
        current_day = datetime.now().weekday()
        target_index = days_order.index(target_day)
        
        days_ahead = (target_index - current_day) % 7
        if days_ahead == 0:
            days_ahead = 7
            
        return datetime.now() + timedelta(days=days_ahead)
    
    def process_natural_message(self, text, chat_id):
        """Основна функція обробки природного повідомлення"""
        # Перевіряємо різні типи запитів
        if self.is_delete_request(text):
            return self.handle_delete_request(text, chat_id)
        
        if self.is_add_request(text):
            return self.handle_add_request(text, chat_id)
        
        if self.is_birthday_request(text):
            return self.handle_birthday_request(text, chat_id)
        
        if self.is_schedule_request(text):
            target_date = self.get_date_context(text)
            if target_date:
                return self.get_schedule_for_date(chat_id, target_date)
            else:
                return self.get_general_schedule(chat_id)
        
        return None
    
    def handle_add_request(self, text, chat_id):
        """Обробляє запити на додавання"""
        time_info = self.extract_time(text)
        date_info = self.get_date_context(text)
        
        if time_info and date_info:
            # Можемо створити нагадування автоматично
            reminder_text = self.extract_reminder_text(text)
            if reminder_text:
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    
                    hour, minute = time_info
                    date_str = date_info.strftime('%Y-%m-%d')
                    
                    cursor.execute("""
                        INSERT INTO reminders (chat_id, text, hour, minute, days, one_time, reminder_type, specific_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (chat_id, reminder_text, hour, minute, '', 1, 'one_time', date_str))
                    
                    conn.commit()
                    conn.close()
                    
                    return (f"✅ Котик додав нагадування!\n\n"
                           f"📝 {reminder_text}\n"
                           f"⏰ {hour:02d}:{minute:02d}\n"
                           f"📅 {date_info.strftime('%d.%m.%Y')}")
                
                except Exception as e:
                    logger.error(f"Помилка створення нагадування: {e}")
                    return "❌ Котик спіткнувся при створенні нагадування."
        
        return "🐱 Котик розуміє, що треба щось додати, але потрібно більше деталей. Спробуй через меню!"
    
    def extract_reminder_text(self, text):
        """Витягує текст нагадування з повідомлення"""
        # Видаляємо ключові слова
        clean_text = text
        for keyword in self.add_keywords:
            clean_text = re.sub(r'\b' + keyword + r'\b', '', clean_text, flags=re.IGNORECASE)
        
        # Видаляємо час та дату
        for pattern in self.time_patterns + self.date_patterns:
            clean_text = re.sub(pattern, '', clean_text)
        
        # Видаляємо дні тижня
        for day_name in self.week_days.keys():
            clean_text = re.sub(r'\b' + day_name + r'\b', '', clean_text, flags=re.IGNORECASE)
        
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        return clean_text if len(clean_text) > 3 else None
    
    def handle_birthday_request(self, text, chat_id):
        """Обробляє запити про дні народження"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM birthdays WHERE chat_id=? ORDER BY name", (chat_id,))
        birthdays = cursor.fetchall()
        conn.close()
        
        if not birthdays:
            return "🎂 У котика немає збережених днів народження. Додати через меню?"
        
        response = "🎂 **Дні народження:**\n\n"
        for b in birthdays:
            birthday_id, _, name, birth_date = b[:4]
            month, day = map(int, birth_date.split('-'))
            response += f"🔹 {name} - {day:02d}.{month:02d}\n"
        
        return response
    
    def handle_delete_request(self, text, chat_id):
        """Обробляє запити на видалення"""
        return "🗑️ Для видалення використовуй команди:\n/delete [ID] - видалити нагадування\n/delete_birthday [ID] - видалити день народження"
    
    def get_schedule_for_date(self, chat_id, target_date):
        """Отримує розклад на конкретну дату"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            day_name = calendar.day_name[target_date.weekday()].lower()[:3]
            date_str = target_date.strftime('%Y-%m-%d')
            
            # Отримуємо регулярні нагадування на цей день
            cursor.execute("""
                SELECT * FROM reminders 
                WHERE chat_id=? AND is_active=1 AND reminder_type='recurring'
                AND (days LIKE ? OR days LIKE ?)
                ORDER BY hour, minute
            """, (chat_id, f'%{day_name}%', '%mon,tue,wed,thu,fri,sat,sun%'))
            
            recurring_reminders = cursor.fetchall()
            
            # Отримуємо одноразові нагадування на цю дату
            cursor.execute("""
                SELECT * FROM reminders 
                WHERE chat_id=? AND is_active=1 AND reminder_type='one_time'
                AND specific_date=?
                ORDER BY hour, minute
            """, (chat_id, date_str))
            
            one_time_reminders = cursor.fetchall()
            
            # Отримуємо дні народження на цю дату
            date_format = f"{target_date.month:02d}-{target_date.day:02d}"
            cursor.execute("""
                SELECT * FROM birthdays 
                WHERE chat_id=? AND birth_date=?
            """, (chat_id, date_format))
            
            birthdays = cursor.fetchall()
            conn.close()
            
            # Формуємо відповідь
            date_formatted = target_date.strftime('%d.%m.%Y')
            day_name_uk = self.get_day_name_ukrainian(target_date.weekday())
            
            response = f"📅 Розклад на {date_formatted} ({day_name_uk}):\n\n"
            
            all_reminders = list(recurring_reminders) + list(one_time_reminders)
            all_reminders.sort(key=lambda x: (x[3], x[4]))  # Сортуємо за часом
            
            if all_reminders:
                response += "⏰ **Нагадування:**\n"
                for r in all_reminders:
                    type_icon = "🔁" if len(r) > 7 and r[7] == 'recurring' else "📌"
                    response += f"{type_icon} {r[3]:02d}:{r[4]:02d} - {r[2]}\n"
                response += "\n"
            
            if birthdays:
                response += "🎂 **Дні народження:**\n"
                for b in birthdays:
                    response += f"🎉 {b[2]}\n"
                response += "\n"
            
            if not all_reminders and not birthdays:
                response += "🐱 На цей день немає запланованих подій.\nКотик може допомогти додати щось нове!"
            
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
                ORDER BY reminder_type, hour, minute
                LIMIT 15
            """, (chat_id,))
            
            reminders = cursor.fetchall()
            conn.close()
            
            if not reminders:
                return "🐱 У котика немає активних нагадувань.\nМожна додати через меню!"
            
            response = "📋 **Твої нагадування:**\n\n"
            
            recurring = [r for r in reminders if len(r) > 7 and r[7] == 'recurring']
            one_time = [r for r in reminders if len(r) > 7 and r[7] == 'one_time']
            
            if recurring:
                response += "🔁 **Регулярні:**\n"
                for r in recurring:
                    days_emoji = get_days_emoji(r[5])
                    response += f"⏰ {r[3]:02d}:{r[4]:02d} - {r[2]}\n"
                    if r[5]:
                        response += f"📅 {r[5]} {days_emoji}\n"
                    response += "\n"
            
            if one_time:
                response += "📌 **Одноразові:**\n"
                for r in one_time:
                    specific_date = ""
                    if len(r) > 8 and r[8]:
                        try:
                            date_obj = datetime.strptime(r[8], '%Y-%m-%d')
                            specific_date = f" ({date_obj.strftime('%d.%m.%Y')})"
                        except:
                            pass
                    response += f"⏰ {r[3]:02d}:{r[4]:02d} - {r[2]}{specific_date}\n\n"
            
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
            data['caption'] = caption[:1024]
        if reply_markup:
            data['reply_markup'] = json.dumps(reply_markup)
            
        response = requests.post(f"{BASE_URL}/sendPhoto", data=data, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Помилка надсилання фото: {e}")
        return None

def edit_message_text(chat_id, message_id, text, reply_markup=None, parse_mode=None):
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
        if parse_mode:
            data['parse_mode'] = parse_mode
            
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

def get_reminder_type_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '🔁 Регулярне нагадування', 'callback_data': 'reminder_type_recurring'}],
            [{'text': '📌 Одноразове нагадування', 'callback_data': 'reminder_type_onetime'}],
            [{'text': '❌ Скасувати', 'callback_data': 'main_menu'}]
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
            [{'text': 'Вибіркові дні', 'callback_data': 'days_custom'}],
            [{'text': '❌ Скасувати', 'callback_data': 'main_menu'}]
        ]
    }

def get_weekdays_keyboard():
    return {
        'inline_keyboard': [
            [{'text': 'Пн', 'callback_data': 'day_mon'}, 
             {'text': 'Вт', 'callback_data': 'day_tue'}, 
             {'text': 'Ср', 'callback_data': 'day_wed'}],
            [{'text': 'Чт', 'callback_data': 'day_thu'}, 
             {'text': 'Пт', 'callback_data': 'day_fri'}, 
             {'text': 'Сб', 'callback_data': 'day_sat'}],
            [{'text': 'Нд', 'callback_data': 'day_sun'}],
            [{'text': '✅ Готово', 'callback_data': 'days_selected'}, 
             {'text': '❌ Скасувати', 'callback_data': 'main_menu'}]
        ]
    }

def get_birthday_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '➕ Додати день народження', 'callback_data': 'add_birthday'}],
            [{'text': '📝 Мої дні народження', 'callback_data': 'list_birthdays'}],
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

def get_photo_type_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '📅 День', 'callback_data': 'photo_type_day'},
             {'text': '🗓️ Тиждень', 'callback_data': 'photo_type_week'}],
            [{'text': '📊 Місяць', 'callback_data': 'photo_type_month'}],
            [{'text': '❌ Скасувати', 'callback_data': 'photos_menu'}]
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
