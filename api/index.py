import os
import json
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from fastapi import FastAPI, Request
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

logging.basicConfig(level=logging.INFO)

# Змінні оточення Vercel
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # Твій ключ від Google AI Studio

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
    await message.answer("Привіт, найкраща булочка! 🥐\n\nПросто пиши мені сюди будь-які апдейти протягом тижня, а в п'ятницю натисни /report.")

@dp.message(Command("report"))
async def generate_report(message: types.Message):
    status_msg = await message.answer("⏳ Збираю записи та віддаю нейромережі на причісування...")
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        
        if not records:
            await status_msg.edit_text("🤷‍♀️ За цей тиждень поки немає жодного запису.")
            return

        # 1. ФІЛЬТРАЦІЯ ПО ДАТІ (Тільки цей тиждень, з понеділка)
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

        # 2. ПРОМПТ ДЛЯ GEMINI
        prompt = f"""
        Ти — персональний помічник. Твоє завдання — написати звіт про роботу моделей за тиждень для начальниці Ірини.
        Ось сирі нотатки, які дівчина накидала за тиждень:
        
        {raw_text}

        Сформуй з них гарний, структурований звіт українською мовою. 
        Обов'язково згрупуй інформацію по іменам моделей (наприклад: Дафі, Флора, Катя).
        Пиши у діловому, але легкому стилі. Не вигадуй нових фактів, використовуй ЛИШЕ те, що є в нотатках.
        Формат має починатися словами "По цьому тижню" і далі йти блоками по іменам. Без зайвих вступів та висновків, лише сам звіт.
        """

        # 3. ГЕНЕРАЦІЯ
        model = genai.GenerativeModel('gemini-pro')
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