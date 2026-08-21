import os
import json
import html
import uuid
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from fastapi import FastAPI, Request, HTTPException
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

logging.basicConfig(level=logging.INFO)

# --- Таймзона: якщо tzdata недоступна, не падаємо, а беремо фіксований +3 ---
try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Kyiv"))
except Exception:
    logging.warning("ZoneInfo недоступна, використовую фіксований UTC+3")
    TZ = timezone(timedelta(hours=3))

# --- Змінні оточення Vercel ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ALLOWED_USER_IDS = {
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}

DATE_FMT = "%d.%m.%Y %H:%M"

# Порядок колонок: Дата | Текст | ID
COL_DATE, COL_TEXT, COL_ID = 1, 2, 3

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

_sheet_cache = None


def get_sheet():
    global _sheet_cache
    if _sheet_cache is None:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON), scopes=scopes
        )
        client = gspread.authorize(creds)
        _sheet_cache = client.open_by_url(SPREADSHEET_URL).sheet1
    return _sheet_cache


def now_local():
    return datetime.now(TZ)


def start_of_week():
    now = now_local()
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_note_date(date_str):
    try:
        return datetime.strptime(date_str.strip(), DATE_FMT).replace(tzinfo=TZ)
    except (ValueError, AttributeError):
        return None


def find_cell(sheet, value, column):
    """gspread 5.x кидає CellNotFound, 6.x повертає None. Обробляємо обидва варіанти."""
    try:
        return sheet.find(value, in_column=column)
    except Exception as e:
        if type(e).__name__ == "CellNotFound":
            return None
        raise


def delete_sheet_row(sheet, row_index):
    """gspread 6.x прибрав delete_row(); лишився тільки delete_rows()."""
    if hasattr(sheet, "delete_rows"):
        sheet.delete_rows(row_index)
    else:
        sheet.delete_row(row_index)


def is_allowed(user_id):
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


# --- /start ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    if not is_allowed(message.from_user.id):
        return
    await message.answer(
        "Привіт, найкраща булочка! 🥐\n\n"
        "Що я вмію:\n"
        "💬 Просто пиши мені нотатки — я їх збережу.\n"
        "📋 <b>/list</b> — подивитися або видалити записи за цей тиждень.\n"
        "📊 <b>/report</b> — зібрати гарний звіт у п'ятницю.",
        parse_mode="HTML",
    )


# --- /list ---
@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    if not is_allowed(message.from_user.id):
        return

    status_msg = await message.answer("⏳ Дістаю твої записи з таблиці...")
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()[1:]
        week_start = start_of_week()

        found = []
        for row in rows:
            date_str = row[COL_DATE - 1] if len(row) >= COL_DATE else ""
            text = row[COL_TEXT - 1] if len(row) >= COL_TEXT else ""
            note_id = row[COL_ID - 1] if len(row) >= COL_ID else ""
            note_date = parse_note_date(date_str)
            if note_date and note_date >= week_start:
                found.append((date_str, text, note_id))

        if not found:
            await status_msg.edit_text("🤷‍♀️ За цей тиждень ти ще нічого не записувала.")
            return

        await status_msg.delete()

        for date_str, text, note_id in found[-30:]:
            key = note_id or date_str
            kb = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="❌ Видалити", callback_data="del_" + key
                        )
                    ]
                ]
            )
            await message.answer(
                "📅 <b>" + html.escape(date_str) + "</b>\n" + html.escape(text),
                reply_markup=kb,
                parse_mode="HTML",
            )

    except Exception:
        logging.exception("Помилка /list")
        await message.answer("❌ Помилка при пошуку. Скажи Дімі перевірити логи.")


# --- Кнопка "Видалити" ---
@dp.callback_query(F.data.startswith("del_"))
async def process_delete(callback: types.CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer()
        return

    key = callback.data.replace("del_", "", 1)
    try:
        sheet = get_sheet()

        # Спочатку по ID (колонка 3), потім по даті (колонка 1).
        # in_column обов'язковий: інакше find() зачепить збіг усередині тексту нотатки.
        cell = find_cell(sheet, key, COL_ID) or find_cell(sheet, key, COL_DATE)

        if cell is None:
            await callback.answer(
                "Запис не знайдено. Можливо, він вже видалений.", show_alert=True
            )
            return

        delete_sheet_row(sheet, cell.row)
        await callback.message.edit_text(
            "🗑 <i>Цей запис було видалено.</i>", parse_mode="HTML"
        )
        await callback.answer("Успішно видалено!")

    except Exception as e:
        logging.exception("Помилка видалення")
        await callback.answer("❌ Помилка: " + type(e).__name__, show_alert=True)


# --- /report ---
PREFERRED_MODELS = (
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
)


def pick_model_name():
    available = [
        m.name
        for m in genai.list_models()
        if "generateContent" in m.supported_generation_methods
    ]
    if not available:
        return None
    for preferred in PREFERRED_MODELS:
        for name in available:
            if preferred in name:
                return name
    return available[0]


@dp.message(Command("report"))
async def generate_report(message: types.Message):
    if not is_allowed(message.from_user.id):
        return

    status_msg = await message.answer(
        "⏳ Збираю записи та віддаю нейромережі на причісування..."
    )
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()[1:]
        week_start = start_of_week()

        weekly_notes = []
        for row in rows:
            date_str = row[COL_DATE - 1] if len(row) >= COL_DATE else ""
            text = row[COL_TEXT - 1] if len(row) >= COL_TEXT else ""
            note_date = parse_note_date(date_str)
            if note_date and note_date >= week_start and text.strip():
                weekly_notes.append(text.strip())

        if not weekly_notes:
            await status_msg.edit_text("🤷‍♀️ За цей тиждень поки немає жодного запису.")
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
        3. Інформацію групуй по іменам моделей. ЩОБ ЗВІТ ВИГЛЯДАВ ЖИВИМ І НАПИСАНИМ ЛЮДИНОЮ, постійно чергуй формати заголовків для кожної дівчини. Наприклад, для однієї напиши "Щодо Дафі", для іншої "По Аліні", для третьої просто "Марта", для четвертої "Катя" і так далі. Не роби всі заголовки за одним шаблоном!
        4. Кожен пункт під ім'ям моделі пиши з нового рядка, з великої літери. Ніяких дефісів, крапок чи маркерів списку на початку рядка (просто чистий текст).
        5. Між блоками різних моделей обов'язково роби один порожній рядок (відступ) для візуальної краси.
        6. Пиши у діловому, але легкому, живому стилі, так, як це робить реальна людина в робочому чаті. Не вигадуй нових фактів, використовуй ЛИШЕ те, що є в нотатках.
        7. Не додавай ніяких вступів ("Ось ваш звіт") або висновків ("Гарних вихідних"). Тільки сам звіт.
        """

        model_name = pick_model_name()
        if not model_name:
            await status_msg.edit_text(
                "❌ Твій API-ключ не має доступу до жодної моделі. "
                "Перевір налаштування в Google AI Studio."
            )
            return

        model = genai.GenerativeModel(model_name)
        response = await model.generate_content_async(prompt)
        final_report = response.text.strip()

        if len(final_report) <= 4000:
            await status_msg.edit_text(final_report)
        else:
            await status_msg.delete()
            for i in range(0, len(final_report), 4000):
                await message.answer(final_report[i : i + 4000])

    except Exception as e:
        logging.exception("Помилка /report")
        await status_msg.edit_text("❌ Помилка: " + type(e).__name__ + ": " + str(e))


# --- Збереження нотатки ---
@dp.message(F.text)
async def save_note(message: types.Message):
    if not is_allowed(message.from_user.id):
        return
    try:
        sheet = get_sheet()
        current_date = now_local().strftime(DATE_FMT)
        note_id = uuid.uuid4().hex[:8]
        sheet.append_row(
            [current_date, message.text.strip(), note_id],
            value_input_option="USER_ENTERED",
        )
        await message.reply("✅ Записав!")
    except Exception:
        logging.exception("Помилка збереження")
        await message.reply("❌ Не зміг зберегти запис.")


# --- Webhook ---
@app.post("/api/webhook")
async def webhook_handler(request: Request):
    if WEBHOOK_SECRET:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="forbidden")

    update_data = await request.json()
    update = types.Update.model_validate(update_data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}