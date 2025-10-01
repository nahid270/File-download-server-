import os
import threading
from datetime import datetime
import requests
from flask import Flask, Response, abort, jsonify
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from pymongo import MongoClient
import asyncio
import traceback # <-- এই লাইনটি যোগ করুন

# Load env variables
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
MONGO_URI = os.getenv("MONGO_URI") 
SERVER_URL = os.getenv("SERVER_URL") # <-- আপনার সার্ভারের URL

# MongoDB Setup
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client["TelegramFileBot"]
    files_collection = db["files"]
    print("✅ MongoDB কানেকশন সফল।")
except Exception as e:
    print(f"❌ MongoDB কানেকশন ব্যর্থ: {e}")
    exit()

# Flask App
app = Flask(__name__)

# Pyrogram Bot
# বট অবজেক্টটি আগে ডিক্লেয়ার করুন
bot = Client("MovieBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# এই ভেরিয়েবলটি Pyrogram-এর event loop ধরে রাখবে
pyrogram_loop = None

# --- Helper ফাংশন ---
# এই async ফাংশনটি টেলিগ্রাম থেকে ফাইলের তথ্য আনবে
async def get_tg_file_url(msg_id):
    # 'async with bot' ব্যবহার করার দরকার নেই কারণ বট আগে থেকেই চলছে
    msg = await bot.get_messages(CHANNEL_ID, msg_id)
    if not msg or (not msg.document and not msg.video and not msg.audio):
        print(f"মেসেজ আইডি {msg_id} খুঁজে পাওয়া যায়নি বা এটি কোনো ফাইল নয়।")
        return None
    
    file = msg.document or msg.video or msg.audio
    file_info = await bot.get_file(file.file_id)
    # সরাসরি Telegram Bot API থেকে ফাইল স্ট্রিম করার URL
    tg_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
    return tg_url

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
        # বট চালু না থাকলে অপেক্ষা করার জন্য একটি বার্তা দিন
        if not pyrogram_loop or not pyrogram_loop.is_running():
            print("❌ Pyrogram event loop চালু নেই।")
            return abort(503, "Service temporarily unavailable, bot is not ready.")

        # চলমান event loop-এ async ফাংশনটি চালানোর জন্য
        future = asyncio.run_coroutine_threadsafe(get_tg_file_url(file_data["msg_id"]), pyrogram_loop)
        tg_url = future.result(timeout=30) # ৩০ সেকেন্ড পর্যন্ত অপেক্ষা করবে

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
        # ===== সবচেয়ে গুরুত্বপূর্ণ পরিবর্তন =====
        # আসল এররটি প্রিন্ট করুন যাতে আমরা বুঝতে পারি সমস্যাটা কী
        print(f"❌ ডাউনলোড লিংক জেনারেশনে মারাত্মক ত্রুটি: {e}")
        print(traceback.format_exc()) # <-- এটি এররের সম্পূর্ণ বিবরণ দেখাবে
        return abort(500, "Download link generation failed.")


@bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def save_file(client: Client, message: Message):
    try:
        # ফাইল ফরওয়ার্ড করার আগে ব্যবহারকারীকে একটি বার্তা দিন
        progress_msg = await message.reply_text("⏳ ফাইল প্রসেস করা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...", quote=True)

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
            await progress_msg.edit("❌ সার্ভারের URL সেট করা নেই! ডাউনলোড লিংক তৈরি করা সম্ভব নয়।")
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

# Run Flask + Bot
if __name__ == "__main__":
    def run_flask():
        port = int(os.getenv("PORT", 10000))
        app.run(host="0.0.0.0", port=port)

    # Flask একটি আলাদা থ্রেডে চালান
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Pyrogram বট মূল থ্রেডে চালান এবং তার event loop ক্যাপচার করুন
    print("🤖 বট চালু হচ্ছে...")
    try:
        # bot.run() একটি নতুন event loop তৈরি করে এবং এটিকে ব্লক করে
        # আমাদের সেই loop-টি অ্যাক্সেস করতে হবে
        pyrogram_loop = asyncio.get_event_loop()
        pyrogram_loop.run_until_complete(bot.start())
        print(f"✅ বট @{(bot.get_me()).username} হিসেবে লগইন করেছে।")
        
        # বটকে চলমান রাখার জন্য
        pyrogram_loop.run_forever()
        
    except KeyboardInterrupt:
        print("🛑 বট বন্ধ করা হচ্ছে...")
    finally:
        if bot.is_initialized:
            asyncio.get_event_loop().run_until_complete(bot.stop())
        print("✅ বট সফলভাবে বন্ধ হয়েছে।")
