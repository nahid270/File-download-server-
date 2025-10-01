import os
import threading
from datetime import datetime
import requests
from flask import Flask, Response, abort
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from pymongo import MongoClient
import asyncio
import traceback

# Load env variables
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
MONGO_URI = os.getenv("MONGO_URI")
SERVER_URL = os.getenv("SERVER_URL")

# --- Global Variables ---
bot = None
pyrogram_loop = None
app = Flask(__name__)

# MongoDB Setup
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client["TelegramFileBot"]
    files_collection = db["files"]
    print("✅ MongoDB কানেকশন সফল।")
except Exception as e:
    print(f"❌ MongoDB কানেকশন ব্যর্থ: {e}")
    exit()


# --- Telegram Related Functions ---
async def get_tg_file_url(msg_id):
    try:
        msg = await bot.get_messages(CHANNEL_ID, msg_id)
        if not msg or (not msg.document and not msg.video and not msg.audio):
            print(f"মেসেজ আইডি {msg_id} খুঁজে পাওয়া যায়নি বা এটি কোনো ফাইল নয়।")
            return None
        
        file = msg.document or msg.video or msg.audio
        file_info = await bot.get_file(file.file_id)
        return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
    except Exception as e:
        print(f"❌ get_tg_file_url ফাংশনে সমস্যা: {e}")
        print(traceback.format_exc())
        return None

# --- Flask Routes ---
@app.route("/")
def home():
    return "✅ Telegram MovieBot Running! (MongoDB + Streaming)"

@app.route("/download/<file_id>")
def download(file_id):
    print(f"ডাউনলোড অনুরোধ এসেছে ফাইল আইডি-র জন্য: {file_id}")
    file_data = files_collection.find_one({"file_id": file_id})
    if not file_data:
        print(f"ফাইল আইডি {file_id} ডাটাবেসে পাওয়া যায়নি।")
        return abort(404, "File not found in database!")

    try:
        if not bot or not bot.is_connected or not pyrogram_loop.is_running():
            print("❌ বট কানেক্টেড নয় অথবা Pyrogram লুপ চালু নেই।")
            return abort(503, "Service temporarily unavailable, bot is not ready.")

        future = asyncio.run_coroutine_threadsafe(get_tg_file_url(file_data["msg_id"]), pyrogram_loop)
        tg_url = future.result(timeout=30)

        if not tg_url:
            print(f"টেলিগ্রাম থেকে URL পাওয়া যায়নি মেসেজ আইডি {file_data['msg_id']}-এর জন্য।")
            return abort(404, "File not found on Telegram channel.")

        print(f"স্ট্রিমিং শুরু হচ্ছে এই URL থেকে: {tg_url}")
        r = requests.get(tg_url, stream=True, allow_redirects=True)
        
        if r.status_code != 200:
            print(f"❌ টেলিগ্রাম API থেকে ফাইল আনতে সমস্যা: {r.status_code} - {r.text}")
            return abort(502, "Failed to fetch file from Telegram.")

        return Response(
            r.iter_content(chunk_size=1024*1024),
            content_type=r.headers.get('Content-Type', 'application/octet-stream'),
            headers={"Content-Disposition": f"attachment; filename=\"{file_data['name']}\""}
        )

    except Exception as e:
        print(f"❌ ডাউনলোড লিংক জেনারেশনে মারাত্মক ত্রুটি: {e}")
        print(traceback.format_exc())
        return abort(500, "Download link generation failed.")

# --- Pyrogram Bot Setup Function ---
def setup_bot():
    global bot, pyrogram_loop
    
    bot = Client("MovieBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

    @bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
    async def save_file(client: Client, message: Message):
        try:
            progress_msg = await message.reply_text("⏳ ফাইল প্রসেস করা হচ্ছে...", quote=True)
            fwd_msg = await message.forward(CHANNEL_ID)
            file = fwd_msg.document or fwd_msg.video or fwd_msg.audio
            if not file:
                await progress_msg.edit("❌ এই মেসেজটি ফরোয়ার্ড করা যায়নি।")
                return

            file_id = str(file.file_unique_id)
            file_name = getattr(file, "file_name", "unnamed_file")

            files_collection.update_one(
                {"file_id": file_id},
                {"$set": {
                    "msg_id": fwd_msg.id,
                    "name": file_name,
                    "uploaded_by": message.from_user.id,
                    "upload_time": datetime.utcnow()
                }},
                upsert=True
            )
            
            if not SERVER_URL:
                await progress_msg.edit("❌ সার্ভারের URL সেট করা নেই!")
                return

            download_link = f"{SERVER_URL.rstrip('/')}/download/{file_id}"
            await progress_msg.edit(
                f"✅ **ফাইল সফলভাবে সেভ হয়েছে!**\n\n"
                f"📄 **ফাইলের নাম:** `{file_name}`\n\n"
                f"📥 **ডাউনলোড লিংক:**\n{download_link}"
            )
        except Exception as e:
            print(f"❌ ফাইল সেভ করতে গিয়ে ত্রুটি: {e}")
            print(traceback.format_exc())
            await message.reply_text(f"❌ একটি মারাত্মক সমস্যা হয়েছে: {e}")

    # Pyrogram বটকে মূল থ্রেডে চালান এবং এর event loop ক্যাপচার করুন
    print("🤖 বট চালু হচ্ছে...")
    pyrogram_loop = asyncio.get_event_loop()
    pyrogram_loop.run_until_complete(bot.start())
    me = bot.get_me()
    print(f"✅ বট @{me.username} হিসেবে লগইন করেছে।")
    
# --- Main Execution Block ---
if __name__ == "__main__":
    # প্রথমে বট সেটআপ এবং চালু করুন
    setup_bot()
    
    # তারপর Flask অ্যাপ একটি আলাদা থ্রেডে চালান
    def run_flask():
        port = int(os.getenv("PORT", 10000))
        app.run(host="0.0.0.0", port=port, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("🚀 Flask সার্ভার চালু হয়েছে। বট এখন মেসেজের জন্য অপেক্ষা করছে...")
    
    # বটকে চলমান রাখুন
    pyrogram_loop.run_forever()
