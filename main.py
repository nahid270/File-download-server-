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
from concurrent.futures import Future

# Load env variables
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
MONGO_URI = os.getenv("MONGO_URI") 

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
# বট অবজেক্টটি আগে ডিক্লেয়ার করুন যাতে Flask রুট থেকে এটি অ্যাক্সেস করা যায়
bot = Client("MovieBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- নতুন Helper ফাংশন ---
# এই async ফাংশনটি টেলিগ্রাম থেকে ফাইলের তথ্য আনবে
async def get_file_details(msg_id):
    async with bot:
        msg = await bot.get_messages(CHANNEL_ID, msg_id)
        if not msg or (not msg.document and not msg.video and not msg.audio):
            return None
        
        file = msg.document or msg.video or msg.audio
        file_info = await bot.get_file(file.file_id)
        tg_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        return tg_url

@app.route("/")
def home():
    return "✅ Telegram MovieBot Running! (MongoDB + Streaming)"

@app.route("/download/<file_id>")
def download(file_id):
    file_data = files_collection.find_one({"file_id": file_id})
    if not file_data:
        return abort(404, "File not found!")

    try:
        # চলমান event loop-এ async ফাংশনটি চালানোর জন্য
        loop = asyncio.get_event_loop()
        future = asyncio.run_coroutine_threadsafe(get_file_details(file_data["msg_id"]), loop)
        tg_url = future.result() # ফলাফল পাওয়ার জন্য অপেক্ষা করুন

        if not tg_url:
            return abort(404, "File not found on Telegram channel.")

        # Streaming via requests
        r = requests.get(tg_url, stream=True, allow_redirects=True)
        
        # Check if the request to Telegram was successful
        if r.status_code != 200:
            print(f"Error fetching from Telegram API: {r.status_code} - {r.text}")
            return abort(502, "Failed to fetch file from Telegram.")

        return Response(
            r.iter_content(chunk_size=1024*1024),
            content_type=r.headers.get('Content-Type', 'application/octet-stream'),
            headers={"Content-Disposition": f"attachment; filename=\"{file_data['name']}\""}
        )

    except Exception as e:
        print(f"ডাউনলোড লিংক জেনারেশনে ত্রুটি: {e}")
        return abort(500, "Download link generation failed.")


@bot.on_message(filters.document | filters.video | filters.audio)
async def save_file(client: Client, message: Message):
    # কোনো প্রাইভেট চ্যাট বা গ্রুপ থেকে আসা ফাইল সেভ করুন
    if message.chat.type != "private":
        return

    try:
        fwd_msg = await message.forward(CHANNEL_ID)
        file = fwd_msg.document or fwd_msg.video or fwd_msg.audio
        if not file:
            await message.reply_text("❌ এই মেসেজটি ফরোয়ার্ড করা যায়নি।")
            return

        file_id = str(file.file_unique_id)

        files_collection.update_one(
            {"file_id": file_id},
            {"$set": {
                "msg_id": fwd_msg.id,
                "name": getattr(file, "file_name", "unnamed_file"),
                "uploaded_by": message.from_user.id,
                "upload_time": datetime.utcnow()
            }},
            upsert=True
        )
        
        # আপনার সার্ভারের সঠিক ডোমেইন দিন
        server_url = os.getenv("SERVER_URL", "https://file-download-server-zzqm.onrender.com")

        await message.reply_text(
            f"✅ ফাইল চ্যানেলে সেভ হয়েছে!\n\n"
            f"📥 ডাউনলোড লিংক:\n"
            f"{server_url}/download/{file_id}"
        )
    except Exception as e:
        print(f"ফাইল সেভ করতে গিয়ে ত্রুটি: {e}")
        await message.reply_text(f"❌ একটি সমস্যা হয়েছে: {e}")

# Run Flask + Bot
if __name__ == "__main__":
    def run_flask():
        # Render.com সাধারণত 10000 পোর্ট ব্যবহার করে
        port = int(os.getenv("PORT", 10000))
        app.run(host="0.0.0.0", port=port)

    # Run Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Run Pyrogram bot in the main thread
    print("🤖 বট চালু হচ্ছে...")
    bot.run()
