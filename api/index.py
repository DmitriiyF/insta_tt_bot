import os
import json
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from fastapi import FastAPI, Request
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

# Берем настройки из переменных окружения Vercel
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON") # Сюда положим содержимое файла credentials.json

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_url(SPREADSHEET_URL).sheet1

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Привет, самая лучшая булочка! 🥐\n\nПросто пиши мне сюда любые апдейты в течение недели, а в пятницу нажми /report.")

@dp.message(Command("report"))
async def generate_report(message: types.Message):
    status_msg = await message.answer("⏳ Собираю записи...")
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        if not records:
            await status_msg.edit_text("🤷‍♀️ За эту неделю пока нет ни одной записи.")
            return

        report_text = "✨ **Отчет за неделю:**\n\n"
        for row in records:
            date = row.get("Дата", "")
            text = row.get("Текст", "")
            report_text += f"🗓 {date}\n💬 {text}\n\n"
            
        await status_msg.edit_text(report_text, parse_mode="Markdown")
    except Exception as e:
        logging.error(e)
        await status_msg.edit_text("❌ Ошибка. Скажи Диме проверить логи.")

@dp.message(F.text)
async def save_note(message: types.Message):
    try:
        sheet = get_sheet()
        current_date = datetime.now().strftime("%d.%m.%Y %H:%M")
        sheet.append_row([current_date, message.text.strip()])
        await message.reply("✅ Записал!")
    except Exception as e:
        logging.error(e)
        await message.reply("❌ Не смог сохранить запись.")

# Эндпоинт, на который Telegram будет присылать сообщения
@app.post("/api/webhook")
async def webhook_handler(request: Request):
    update_data = await request.json()
    update = types.Update(**update_data)
    await dp.feed_update(bot, update)
    return {"status": "ok"}