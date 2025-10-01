import os
import threading
from datetime import datetime
import requests
from flask import Flask, Response, abort
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from pymongo import MongoClient

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

@app.route("/")
def home():
    return "✅ Telegram MovieBot Running! (MongoDB + Streaming)"

@app.route("/download/<file_id>")
def download(file_id):
    file_data = files_collection.find_one({"file_id": file_id})
    if not file_data:
        return abort(404, "File not found!")

    try:
        with Client("TempClient", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN) as app_client:
            msg = app_client.get_messages(CHANNEL_ID, file_data["msg_id"])
            file = msg.document or msg.video or msg.audio
            file_info = app_client.get_file(file.file_id)
            tg_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"

            # Streaming via requests
            r = requests.get(tg_url, stream=True)
            return Response(
                r.iter_content(chunk_size=1024*1024),
                content_type=r.headers.get('Content-Type', 'application/octet-stream'),
                headers={"Content-Disposition": f"attachment; filename={file_data['name']}"}
            )

    except Exception as e:
        print(f"ডাউনলোড লিংক জেনারেশনে ত্রুটি: {e}")
        return abort(500, "Download link generation failed.")

# Pyrogram Bot
bot = Client("MovieBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@bot.on_message(filters.document | filters.video | filters.audio)
async def save_file(client: Client, message: Message):
    fwd = await message.forward(CHANNEL_ID)
    file = fwd.document or fwd.video or fwd.audio
    file_id = str(file.file_unique_id)

    files_collection.update_one(
        {"file_id": file_id},
        {"$set": {
            "msg_id": fwd.id,
            "name": getattr(file, "file_name", "unnamed"),
            "uploaded_by": message.from_user.id,
            "upload_time": datetime.utcnow()
        }},
        upsert=True
    )

    await message.reply_text(
        f"✅ ফাইল চ্যানেলে সেভ হয়েছে!\n\n"
        f"📥 ডাউনলোড লিংক:\n"
        f"https://yourdomain.com/download/{file_id}"
    )

# Run Flask + Bot
if __name__ == "__main__":
    def run_flask():
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

    threading.Thread(target=run_flask).start()
    bot.run()
