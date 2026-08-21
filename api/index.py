import os
import json
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from fastapi import FastAPI, Request
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

logging.basicConfig(level=logging.INFO)

# Змінні оточення Vercel
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_url(SPREADSHEET_URL).sheet1

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/list"), KeyboardButton(text="/report")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши нотатку або обери дію..."
    )
    await message.answer(
        "Привіт, найкраща булочка! 🥐\n\n"
        "Що я вмію:\n"
        "💬 Просто пиши мені нотатки — я їх збережу.\n"
        "📋 <b>/list</b> — подивитися або видалити записи за цей тиждень.\n"
        "📊 <b>/report</b> — зібрати гарний звіт у п'ятницю.\n\n"
        "Кнопки керування внизу екрану активні! 👇",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    status_msg = await message.answer("⏳ Дістаю твої записи з таблиці...")
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        
        if not records:
            await status_msg.edit_text("🤷‍♀️ Таблиця порожня, записів немає.")
            return

        now = datetime.now()
        start_of_week = now - timedelta(days=now.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

        found = False
        for row in records:
            date_str = str(row.get("Дата", ""))
            text = str(row.get("Текст", ""))
            try:
                note_date = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
                if note_date >= start_of_week:
                    found = True
                    kb = types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="❌ Видалити", callback_data=f"del_{date_str}")]
                    ])
                    await message.answer(f"📅 <b>{date_str}</b>\n{text}", reply_markup=kb, parse_mode="HTML")
            except ValueError:
                continue

        if not found:
            await status_msg.edit_text("🤷‍♀️ За цей тиждень ти ще нічого не записувала.")
        else:
            await status_msg.delete()
            
    except Exception as e:
        logging.error(e)
        await status_msg.edit_text("❌ Помилка при пошуку.")

@dp.callback_query(F.data.startswith("del_"))
async def process_delete(callback: types.CallbackQuery):
    target_date = callback.data.replace("del_", "")
    try:
        sheet = get_sheet()
        cell = sheet.find(target_date)
        
        if cell:
            sheet.delete_row(cell.row)
            await callback.message.edit_text(f"🗑 <i>Цей запис було видалено.</i>", parse_mode="HTML")
            await callback.answer("Успішно видалено!")
        else:
            await callback.answer("Запис не знайдено або вже видалений.", show_alert=True)
            
    except Exception as e:
        logging.error(f"Помилка видалення: {e}")
        await callback.answer("❌ Помилка видалення.", show_alert=True)

@dp.message(Command("report"))
async def generate_report(message: types.Message):
    status_msg = await message.answer("⏳ Збираю записи та віддаю нейромережі на причісування...")
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        
        if not records:
            await status_msg.edit_text("🤷‍♀️ За цей тиждень поки немає жодного запису.")
            return

        now = datetime.now()
        start_of_week = now - timedelta(days=now.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

        weekly_notes = []
        for row in records:
            date_str = str(row.get("Дата", ""))
            text = str(row.get("Текст", ""))
            try:
                note_date = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
                if note_date >= start_of_week:
                    weekly_notes.append(text)
            except ValueError:
                continue

        if not weekly_notes:
            await status_msg.edit_text("🤷‍♀️ На цьому тижні записів не знайдено (старі записи проігноровано).")
            return

        raw_text = "\n- ".join(weekly_notes)

        prompt = f"""
        Ти — персональний помічник. Твоє завдання — написати звіт про роботу моделей за тиждень для начальниці Ірини.
        Ось сирі нотатки, які менеджерка накидала за тиждень:
        
        {raw_text}

        Сформуй з них гарний, структурований звіт українською мовою. 
        Жорсткі правила форматування:
        1. Звіт має починатися рівно з фрази "По цьому тижню" (без зірочок і жирного шрифту).
        2. Далі обов'язково порожній рядок.
        3. Інформацію групуй по іменам моделей. ЩОБ ЗВІТ ВИГЛЯДАВ ЖИВИМ І НАПИСАНИМ ЛЮДИНОЮ, постійно чергуй формати заголовків для кожної дівчини (наприклад: "Щодо Дафі", "По Аліні", просто "Марта", "Катя" і так далі).
        4. Кожен пункт під ім'ям моделі пиши з нового рядка, з великої літери. Ніяких дефісів, крапок чи маркерів списку на початку рядка (просто чистий текст).
        5. Між блоками різних моделей обов'язково роби один порожній рядок (відступ) для візуальної краси.
        6. Пиши у діловому, але легкому, живому стилі. Не вигадуй нових фактів, використовуй ЛИШЕ те, що є в нотатках.
        7. Не додавай ніяких вступів чи висновків. Тільки сам звіт.
        """

        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        if not available_models:
            await status_msg.edit_text("❌ Твій API-ключ не має доступу до жодної моделі.")
            return
            
        model = genai.GenerativeModel(available_models[0])
        response = await model.generate_content_async(prompt)
        final_report = response.text.strip()
            
        await status_msg.edit_text(final_report)
    except Exception as e:
        logging.error(e)
        await status_msg.edit_text(f"❌ Помилка: {e}")

@dp.message(F.text)
async def save_note(message: types.Message):
    try:
        sheet = get_sheet()
        current_date = datetime.now().strftime("%d.%m.%Y %H:%M")
        sheet.append_row([current_date, message.text.strip()])
        await message.reply("✅ Записав!")
    except Exception as e:
        logging.error(e)
        await message.reply("❌ Не зміг зберегти запис.")

@app.post("/api/webhook")
async def webhook_handler(request: Request):
    update_data = await request.json()
    update = types.Update(**update_data)
    await dp.feed_update(bot, update)
    return {"status": "ok"}