import os
import json
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from fastapi import FastAPI, Request
import gspread
from google.oauth2.service_account import Credentials
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)

# Переменные окружения Vercel
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # НОВЫЙ КЛЮЧ ДЛЯ CHATGPT

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

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
    status_msg = await message.answer("⏳ Збираю записи та віддаю штучному інтелекту на причісування...")
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        
        if not records:
            await status_msg.edit_text("🤷‍♀️ За цей тиждень поки немає жодного запису.")
            return

        # 1. ФИЛЬТРАЦИЯ ПО ТЕКУЩЕЙ НЕДЕЛЕ (С понедельника)
        now = datetime.now()
        start_of_week = now - timedelta(days=now.weekday()) # Получаем понедельник
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

        weekly_notes = []
        for row in records:
            date_str = str(row.get("Дата", ""))
            text = str(row.get("Текст", ""))
            try:
                # Пробуем распарсить дату из таблицы
                note_date = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
                if note_date >= start_of_week:
                    weekly_notes.append(text)
            except ValueError:
                continue # Пропускаем кривые даты

        if not weekly_notes:
            await status_msg.edit_text("🤷‍♀️ На цьому тижні записів не знайдено (старі записи проігноровано).")
            return

        raw_text = "\n- ".join(weekly_notes)

        # 2. МАГИЯ CHATGPT
        prompt = f"""
        Ти — персональний асистент. Твоє завдання — написати звіт про роботу моделей за тиждень для начальниці.
        Ось сирі нотатки, які дівчина накидала за тиждень:
        
        - {raw_text}

        Сформуй з них гарний, структурований звіт українською мовою. 
        Обов'язково згрупуй інформацію по іменам моделей (наприклад: Дафі, Флора, Катя тощо).
        Пиши у діловому, але легкому стилі. Не вигадуй нових фактів, використовуй ЛИШЕ те, що є в нотатках.
        Формат має починатися словами "По цьому тижню" і далі йти блоками по іменам.
        """

        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        final_report = response.choices[0].message.content
            
        await status_msg.edit_text(final_report)
    except Exception as e:
        logging.error(e)
        await status_msg.edit_text("❌ Помилка. Скажи Дімі перевірити логи.")

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