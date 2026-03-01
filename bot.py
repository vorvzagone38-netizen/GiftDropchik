import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List
import json
import os
from collections import defaultdict

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import ParseMode, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import WebAppInfo

import sqlite3
import matplotlib.pyplot as plt
import io
from datetime import datetime
import speech_recognition as sr
from pydub import AudioSegment
import aiohttp
import tempfile
import os

# Настройки
API_TOKEN = 'YOUR_BOT_TOKEN'  # Замените на ваш токен
WEBAPP_URL = 'https://your-domain.com'  # URL вашего WebApp (для локальной разработки можно использовать ngrok)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(8414100608:AAGd_fiwaaCDlAc3W8CoNcgspPtqvnjm3I4)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# Состояния для FSM
class ExpenseStates(StatesGroup):
    waiting_for_voice = State()
    waiting_for_text = State()
    waiting_for_category = State()

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('budget_bot.db')
    c = conn.cursor()
    
    # Таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  monthly_budget REAL DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Таблица расходов
    c.execute('''CREATE TABLE IF NOT EXISTS expenses
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  amount REAL,
                  category TEXT,
                  description TEXT,
                  date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    # Таблица категорий
    c.execute('''CREATE TABLE IF NOT EXISTS categories
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  name TEXT,
                  emoji TEXT,
                  is_default BOOLEAN DEFAULT 0,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    # Добавляем стандартные категории для новых пользователей
    conn.commit()
    conn.close()

# Категории по умолчанию
DEFAULT_CATEGORIES = [
    ('🍔', 'Еда'),
    ('🏠', 'Жильё'),
    ('🚗', 'Транспорт'),
    ('🛒', 'Покупки'),
    ('🎮', 'Развлечения'),
    ('💊', 'Здоровье'),
    ('📚', 'Образование'),
    ('💼', 'Работа'),
    ('📱', 'Связь'),
    ('💸', 'Другое')
]

# Функции для работы с БД
def add_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect('budget_bot.db')
    c = conn.cursor()
    
    # Проверяем, существует ли пользователь
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not c.fetchone():
        c.execute("INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                 (user_id, username, first_name, last_name))
        
        # Добавляем стандартные категории для нового пользователя
        for emoji, name in DEFAULT_CATEGORIES:
            c.execute("INSERT INTO categories (user_id, name, emoji, is_default) VALUES (?, ?, ?, 1)",
                     (user_id, name, emoji))
    
    conn.commit()
    conn.close()

def add_expense(user_id, amount, category, description=''):
    conn = sqlite3.connect('budget_bot.db')
    c = conn.cursor()
    c.execute("INSERT INTO expenses (user_id, amount, category, description) VALUES (?, ?, ?, ?)",
             (user_id, amount, category, description))
    conn.commit()
    conn.close()

def get_user_categories(user_id):
    conn = sqlite3.connect('budget_bot.db')
    c = conn.cursor()
    c.execute("SELECT emoji, name FROM categories WHERE user_id = ? ORDER BY name", (user_id,))
    categories = c.fetchall()
    conn.close()
    return categories

def add_category(user_id, name, emoji='📌'):
    conn = sqlite3.connect('budget_bot.db')
    c = conn.cursor()
    c.execute("INSERT INTO categories (user_id, name, emoji) VALUES (?, ?, ?)",
             (user_id, name, emoji))
    conn.commit()
    conn.close()

def get_monthly_stats(user_id, year=None, month=None):
    if year is None or month is None:
        now = datetime.now()
        year = now.year
        month = now.month
    
    conn = sqlite3.connect('budget_bot.db')
    c = conn.cursor()
    
    # Расходы за указанный месяц
    c.execute('''
        SELECT category, SUM(amount) as total, COUNT(*) as count
        FROM expenses
        WHERE user_id = ? 
        AND strftime('%Y', date) = ? 
        AND strftime('%m', date) = ?
        GROUP BY category
        ORDER BY total DESC
    ''', (user_id, str(year), f"{month:02d}"))
    
    current_month = c.fetchall()
    
    # Общая сумма за месяц
    c.execute('''
        SELECT SUM(amount) 
        FROM expenses
        WHERE user_id = ? 
        AND strftime('%Y', date) = ? 
        AND strftime('%m', date) = ?
    ''', (user_id, str(year), f"{month:02d}"))
    
    total = c.fetchone()[0] or 0
    
    conn.close()
    return current_month, total

def get_previous_month_stats(user_id):
    now = datetime.now()
    if now.month == 1:
        prev_year = now.year - 1
        prev_month = 12
    else:
        prev_year = now.year
        prev_month = now.month - 1
    
    return get_monthly_stats(user_id, prev_year, prev_month)

def get_daily_expenses(user_id, days=30):
    conn = sqlite3.connect('budget_bot.db')
    c = conn.cursor()
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    c.execute('''
        SELECT date(date) as day, SUM(amount) as total
        FROM expenses
        WHERE user_id = ? AND date >= ? AND date <= ?
        GROUP BY date(date)
        ORDER BY day
    ''', (user_id, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
    
    data = c.fetchall()
    conn.close()
    return data

# Функция для распознавания голоса
async def recognize_speech(audio_file_path):
    recognizer = sr.Recognizer()
    
    # Конвертируем OGG в WAV
    audio = AudioSegment.from_ogg(audio_file_path)
    wav_path = audio_file_path.replace('.ogg', '.wav')
    audio.export(wav_path, format='wav')
    
    with sr.AudioFile(wav_path) as source:
        audio_data = recognizer.record(source)
        try:
            text = recognizer.recognize_google(audio_data, language='ru-RU')
            return text
        except sr.UnknownValueError:
            return None
        except sr.RequestError:
            return None
        finally:
            # Очищаем временные файлы
            if os.path.exists(audio_file_path):
                os.remove(audio_file_path)
            if os.path.exists(wav_path):
                os.remove(wav_path)

# Функция для парсинга текста с суммой и категорией
def parse_expense_text(text):
    import re
    
    # Ищем сумму (различные форматы)
    amount_pattern = r'(\d+[.,]?\d*)'
    amounts = re.findall(amount_pattern, text)
    
    if not amounts:
        return None, None, None
    
    # Берем первое найденное число как сумму
    amount_str = amounts[0].replace(',', '.')
    amount = float(amount_str)
    
    # Ищем категорию (слова после суммы или до)
    words = text.lower().split()
    
    # Список возможных категорий из стандартных
    categories_dict = {name.lower(): (emoji, name) for emoji, name in DEFAULT_CATEGORIES}
    
    # Проверяем каждое слово на совпадение с категорией
    for word in words:
        for cat_name, (emoji, full_name) in categories_dict.items():
            if word in cat_name or cat_name in word:
                return amount, full_name, text
    
    return amount, 'Другое', text

# Создание графиков
async def create_pie_chart(data, title):
    if not data:
        return None
    
    labels = [item[0] for item in data]
    sizes = [item[1] for item in data]
    
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
    ax.axis('equal')
    plt.title(title)
    
    # Сохраняем в буфер
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    
    return buf

async def create_bar_chart(data, title):
    if not data:
        return None
    
    days = [item[0][5:] for item in data]  # Только день-месяц
    amounts = [item[1] for item in data]
    
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(days, amounts, color='skyblue')
    ax.set_xlabel('Дата')
    ax.set_ylabel('Сумма (₽)')
    ax.set_title(title)
    plt.xticks(rotation=45)
    
    # Добавляем значения над столбцами
    for i, v in enumerate(amounts):
        ax.text(i, v + max(amounts)*0.01, str(int(v)), ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    
    return buf

# Обработчики команд
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user = message.from_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    
    # Создаем клавиатуру с WebApp
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    webapp_button = types.KeyboardButton(
        text="📱 Открыть мини-приложение",
        web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp")
    )
    buttons = [
        webapp_button,
        types.KeyboardButton("➕ Добавить расход"),
        types.KeyboardButton("📊 Статистика за месяц"),
        types.KeyboardButton("📈 Динамика за 30 дней"),
        types.KeyboardButton("⚙️ Категории"),
        types.KeyboardButton("🎤 Голосовой ввод")
    ]
    keyboard.add(*buttons)
    
    await message.reply(
        f"👋 Привет, {user.first_name}!\n\n"
        "Я бот-хранитель бюджета. Я помогу тебе отслеживать расходы.\n\n"
        "📝 Ты можешь:\n"
        "• Вводить траты текстом (например: 'обед 350')\n"
        "• Использовать голосовой ввод 🎤\n"
        "• Открыть удобное мини-приложение 📱\n"
        "• Смотреть статистику и графики 📊\n\n"
        "Выбери действие на клавиатуре или просто напиши сумму и описание!",
        reply_markup=keyboard
    )

@dp.message_handler(lambda message: message.text == "➕ Добавить расход")
async def cmd_add_expense(message: types.Message):
    await ExpenseStates.waiting_for_text.set()
    await message.reply(
        "💰 Введи расход в формате:\n"
        "сумма категория описание\n\n"
        "Например:\n"
        "350 обед в столовой\n"
        "2500 транспорт такси\n"
        "Или просто сумму, я предложу категорию"
    )

@dp.message_handler(lambda message: message.text == "🎤 Голосовой ввод")
async def cmd_voice_input(message: types.Message):
    await ExpenseStates.waiting_for_voice.set()
    await message.reply(
        "🎤 Отправь голосовое сообщение с описанием расхода.\n"
        "Например: 'потратил 500 рублей на обед'"
    )

@dp.message_handler(lambda message: message.text == "📊 Статистика за месяц")
async def cmd_monthly_stats(message: types.Message):
    user_id = message.from_user.id
    
    # Получаем статистику за текущий месяц
    current_stats, total = get_monthly_stats(user_id)
    
    if not current_stats:
        await message.reply("📊 В этом месяце пока нет расходов!")
        return
    
    # Создаем круговую диаграмму
    chart_data = [(cat, amount) for cat, amount, _ in current_stats]
    chart = await create_pie_chart(chart_data, f"Расходы за {datetime.now().strftime('%B %Y')}")
    
    # Получаем статистику за прошлый месяц для сравнения
    prev_stats, prev_total = get_previous_month_stats(user_id)
    
    # Формируем текстовый отчет
    report = f"📊 *Отчет за {datetime.now().strftime('%B %Y')}*\n\n"
    report += f"💰 *Всего потрачено: {total:.2f} ₽*\n\n"
    
    if prev_total > 0:
        diff = total - prev_total
        if diff > 0:
            report += f"📈 На {diff:.2f} ₽ больше, чем в прошлом месяце\n"
        elif diff < 0:
            report += f"📉 На {abs(diff):.2f} ₽ меньше, чем в прошлом месяце\n"
        else:
            report += "➡️ Столько же, сколько в прошлом месяце\n"
    
    report += "\n*По категориям:*\n"
    for category, amount, count in current_stats:
        percentage = (amount / total) * 100
        report += f"{category}: {amount:.2f} ₽ ({percentage:.1f}%) - {count} раз(а)\n"
    
    await message.reply(report, parse_mode=ParseMode.MARKDOWN)
    
    if chart:
        await message.reply_photo(types.InputFile(chart, filename='stats.png'))

@dp.message_handler(lambda message: message.text == "📈 Динамика за 30 дней")
async def cmd_daily_stats(message: types.Message):
    user_id = message.from_user.id
    
    data = get_daily_expenses(user_id)
    
    if not data:
        await message.reply("📊 За последние 30 дней нет расходов!")
        return
    
    chart = await create_bar_chart(data, "Динамика расходов за последние 30 дней")
    
    total = sum(amount for _, amount in data)
    avg = total / len(data)
    
    report = f"📈 *Динамика за 30 дней*\n\n"
    report += f"💰 Всего: {total:.2f} ₽\n"
    report += f"📊 В среднем в день: {avg:.2f} ₽\n"
    report += f"📅 Дней с расходами: {len(data)}\n"
    
    max_day = max(data, key=lambda x: x[1])
    report += f"🔝 Макс. расход: {max_day[1]:.2f} ₽ ({max_day[0]})"
    
    await message.reply(report, parse_mode=ParseMode.MARKDOWN)
    
    if chart:
        await message.reply_photo(types.InputFile(chart, filename='daily.png'))

@dp.message_handler(lambda message: message.text == "⚙️ Категории")
async def cmd_categories(message: types.Message):
    user_id = message.from_user.id
    categories = get_user_categories(user_id)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    for emoji, name in categories:
        keyboard.insert(InlineKeyboardButton(f"{emoji} {name}", callback_data=f"cat_{name}"))
    keyboard.add(InlineKeyboardButton("➕ Добавить категорию", callback_data="add_category"))
    
    await message.reply(
        "📋 *Твои категории расходов*\n\n"
        "Выбери категорию для просмотра статистики или добавь новую:",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message_handler(state=ExpenseStates.waiting_for_text)
async def process_text_expense(message: types.Message, state: FSMContext):
    text = message.text
    user_id = message.from_user.id
    
    amount, category, description = parse_expense_text(text)
    
    if amount is None:
        await message.reply(
            "❌ Не удалось определить сумму. Пожалуйста, укажи сумму цифрами.\n"
            "Например: обед 350"
        )
        return
    
    add_expense(user_id, amount, category, description)
    
    await state.finish()
    
    # Получаем статистику по категории за месяц
    current_stats, total = get_monthly_stats(user_id)
    cat_stats = next((item for item in current_stats if item[0] == category), None)
    
    response = f"✅ Расход добавлен!\n\n"
    response += f"💰 Сумма: {amount} ₽\n"
    response += f"📋 Категория: {category}\n"
    
    if cat_stats:
        cat_total = cat_stats[1]
        response += f"\n📊 В этом месяце на '{category}' потрачено всего: {cat_total:.2f} ₽"
    
    await message.reply(response)

@dp.message_handler(content_types=['voice'], state=ExpenseStates.waiting_for_voice)
async def process_voice_expense(message: types.Message, state: FSMContext):
    voice = message.voice
    
    # Скачиваем голосовое сообщение
    file_id = voice.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    
    # Создаем временный файл
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp_file:
        await bot.download_file(file_path, tmp_file.name)
        tmp_path = tmp_file.name
    
    # Распознаем речь
    text = await recognize_speech(tmp_path)
    
    if text is None:
        await message.reply(
            "❌ Не удалось распознать голос. Попробуй еще раз или введи текст вручную."
        )
        await state.finish()
        return
    
    # Парсим распознанный текст
    amount, category, description = parse_expense_text(text)
    
    if amount is None:
        await message.reply(
            f"🎤 Распознано: \"{text}\"\n"
            "❌ Не удалось определить сумму. Попробуй еще раз или введи текст вручную."
        )
        await state.finish()
        return
    
    user_id = message.from_user.id
    add_expense(user_id, amount, category, description or text)
    
    await state.finish()
    
    await message.reply(
        f"🎤 Распознано: \"{text}\"\n\n"
        f"✅ Расход добавлен!\n"
        f"💰 Сумма: {amount} ₽\n"
        f"📋 Категория: {category}"
    )

@dp.message_handler()
async def handle_message(message: types.Message):
    # Обработка простого текста (быстрый ввод расхода)
    text = message.text
    user_id = message.from_user.id
    
    amount, category, description = parse_expense_text(text)
    
    if amount is not None:
        add_expense(user_id, amount, category, description or text)
        await message.reply(f"✅ Добавлено: {amount} ₽ ({category})")
    else:
        # Если не удалось распознать расход, показываем меню
        await cmd_start(message)

@dp.callback_query_handler(lambda c: c.data == 'add_category')
async def process_add_category(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(
        callback_query.from_user.id,
        "📝 Отправь название новой категории (можно с эмодзи)\n"
        "Например: 🐱 Кот или 💻 Техника"
    )
    # Здесь можно добавить состояние для ожидания названия категории

# Запуск бота
if __name__ == '__main__':
    init_db()
    print("Бот запущен...")
    executor.start_polling(dp, skip_updates=True)
