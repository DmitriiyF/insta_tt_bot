import asyncio
import logging
import os
import aiohttp
import yt_dlp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, InputMediaPhoto, InputMediaVideo
from aiogram.filters import CommandStart

# === НАСТРОЙКИ ===
BOT_TOKEN = "7586125644:AAEAEnAWhS8WqHt8V5fwkSbUYPuj7uGBZQY" # <-- ВСТАВЬ СЮДА СВОЙ ТОКЕН
RAPIDAPI_KEY = "629c3bef94mshb4228d83dfa67e5p1795bajsn5b5710c16460"
RAPIDAPI_HOST = "instagram120.p.rapidapi.com"
# =================

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ЛОГИКА ДЛЯ TIKTOK ---
def download_tiktok_locally(url: str) -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    download_path = os.path.join(current_dir, "downloads", "%(id)s.%(ext)s")

    ydl_opts = {
        'format': 'best', 
        'outtmpl': download_path,
        'quiet': True, 
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception as e:
        logging.error(f"Ошибка yt-dlp (TikTok): {e}")
        return None

# --- ЛОГИКА ДЛЯ INSTAGRAM ---
async def get_instagram_media_links(ig_url: str):
    api_url = f"https://{RAPIDAPI_HOST}/api/instagram/links"
    payload = {"url": ig_url}
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, headers=headers) as response:
                data = await response.json()
                media_links = []
                
                if isinstance(data, list):
                    for item in data:
                        if 'urls' in item and len(item['urls']) > 0:
                            media_info = item['urls'][0]
                            url = media_info.get('url')
                            ext = media_info.get('extension', '').lower()
                            
                            if ext == 'mp4':
                                media_links.append(('video', url))
                            else:
                                media_links.append(('photo', url))
                        elif 'pictureUrl' in item:
                            media_links.append(('photo', item['pictureUrl']))
                            
                elif isinstance(data, dict):
                    if 'urls' in data and len(data['urls']) > 0:
                        media_info = data['urls'][0]
                        url = media_info.get('url')
                        ext = media_info.get('extension', '').lower()
                        
                        if ext == 'mp4':
                            media_links.append(('video', url))
                        else:
                            media_links.append(('photo', url))
                    elif 'pictureUrl' in data:
                        media_links.append(('photo', data['pictureUrl']))

                return media_links
    except Exception as e:
        logging.error(f"Ошибка при запросе к RapidAPI: {e}")
        return []

async def download_file(url: str, ext: str) -> str:
    file_path = f"downloads/ig_media_{asyncio.get_event_loop().time()}.{ext}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    with open(file_path, 'wb') as f:
                        f.write(await resp.read())
                    return file_path
    except Exception as e:
        logging.error(f"Ошибка скачивания файла: {e}")
    return None

# --- ОБРАБОТЧИКИ ТЕЛЕГРАМ ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("Привет! Кидай ссылку на TikTok или пост/Reels из Instagram.")

@dp.message(F.text.contains("tiktok.com"))
async def handle_tiktok(message: Message):
    status_msg = await message.answer("⏳ Скачиваю TikTok...")
    loop = asyncio.get_event_loop()
    file_path = await loop.run_in_executor(None, download_tiktok_locally, message.text.strip())

    if file_path and os.path.exists(file_path):
        try:
            await message.reply_video(video=FSInputFile(file_path))
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text("❌ Ошибка при отправке видео.")
        finally:
            try:
                os.remove(file_path)
            except:
                pass
    else:
        await status_msg.edit_text("❌ Не удалось скачать TikTok.")

@dp.message(F.text.contains("instagram.com"))
async def handle_instagram(message: Message):
    status_msg = await message.answer("🔍 Ищу пост в Instagram...")
    url = message.text.strip()
    
    media_links = await get_instagram_media_links(url)
    
    if not media_links:
        await status_msg.edit_text("❌ Не удалось найти медиа. Возможно, аккаунт закрыт.")
        return

    await status_msg.edit_text(f"⬇️ Найдено файлов: {len(media_links)}. Скачиваю на сервер...")

    downloaded_files = []
    for media_type, media_url in media_links:
        ext = "mp4" if media_type == "video" else "jpg"
        file_path = await download_file(media_url, ext)
        if file_path and os.path.exists(file_path):
            downloaded_files.append((media_type, file_path))

    if not downloaded_files:
        await status_msg.edit_text("❌ Ошибка при загрузке файлов.")
        return

    await status_msg.edit_text("🚀 Формирую альбомы и отправляю в чат...")

    chunk_size = 10
    for i in range(0, len(downloaded_files), chunk_size):
        chunk = downloaded_files[i:i + chunk_size]
        
        if len(chunk) == 1:
            m_type, f_path = chunk[0]
            try:
                if m_type == "video":
                    await message.reply_video(video=FSInputFile(f_path))
                else:
                    await message.reply_photo(photo=FSInputFile(f_path))
            except Exception as e:
                logging.error(f"Ошибка отправки одиночного файла: {e}")
        else:
            media_group = []
            for m_type, f_path in chunk:
                if m_type == "video":
                    media_group.append(InputMediaVideo(media=FSInputFile(f_path)))
                else:
                    media_group.append(InputMediaPhoto(media=FSInputFile(f_path)))
            try:
                await message.answer_media_group(media=media_group)
            except Exception as e:
                logging.error(f"Ошибка отправки альбома: {e}")

    for _, f_path in downloaded_files:
        try:
            os.remove(f_path)
        except:
            pass
            
    await status_msg.delete()

# --- ЗАГЛУШКА ДЛЯ RENDER ---
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def main():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    downloads_dir = os.path.join(current_dir, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем поллинг бота в фоне
    asyncio.create_task(dp.start_polling(bot))
    
    # Поднимаем фейковый веб-сервер
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logging.info(f"Сервер-заглушка запущен на порту {port}")
    
    # Бесконечный цикл, чтобы скрипт не завершался
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())