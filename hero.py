import os
import sys
import logging
import datetime
import time
import re
import random
import asyncio
import aiohttp
import io
import base64
import shutil
import psutil 
import yt_dlp
import tempfile
import httpx

from telegram import ChatPermissions
from pyrogram import Client, filters
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from typing import Union
from dotenv import load_dotenv
from gtts import gTTS
from telegram import  Update, Poll, ChatPermissions
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, Defaults
from telegram.constants import ChatAction, ParseMode
from telegram.request import HTTPXRequest 
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telethon import TelegramClient, events

from groq import AsyncGroq
from flask import Flask
from threading import Thread
import threading

app_flask = Flask('')
@app_flask.route('/')
def home():
    return "I am alive!"

def run():
    port = int(os.environ.get("PORT",8080))
    app_flask.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
# ---------------- ENV ----------------
load_dotenv()

# ---------------- LOG ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MEMORY_DIR, DOWNLOAD_DIR = "memory","downloads"
CONFESSIONS_FILE = "confessions.txt"

TG_API_ID = 24365702
TG_API_HASH = "d78348a81d41643f51095deaffc1dc90"
YT_API_TOKEN = "CiQkC7zyoe"
API_URL = "https://api.nubcoder.com/info"
TG_BOT_TOKEN = "8075078295:AAFkAvadpHnypIm_jnUbXuq9S2XE-PYvbu0"
os.makedirs(MEMORY_DIR, exist_ok=True)
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

app = Client("yt_link_bot", api_id=TG_API_ID, api_hash=TG_API_HASH, bot_token=TG_BOT_TOKEN, ipv6=False)

GROQ_KEY = os.getenv("GROQ_API_KEY")
BOT_TOKEN= os.getenv("TELEGRAM_BOT_TOKEN")

class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.http_client = httpx.AsyncClient(timeout=30.0) 
        self.locks = {} # {chat_id: ['night']}
        

    async def _fetch_api_data(self, link: str) -> dict:
        """Central function to call the NubCoder API and return JSON data."""
        params = {"token": YT_API_TOKEN, "q": link}
        print(f"[LOG] Fetching API data for: {link}")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            response = await self.http_client.get(API_URL, params=params, headers=headers)
            response.raise_for_status() 
            data = response.json()
            print(f"[LOG] API Data received: {data.get('title', 'Unknown Title')}")
            return data
        except Exception as e:
            print(f"[ERROR] API Request failed: {e}")
            if isinstance(e, httpx.HTTPStatusError):
                print(f"[DEBUG] Response: {e.response.text}")
            raise Exception(f"Could not fetch data from API: {e}")

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        text = ""
        offset = None
        length = None
        for message in messages:
            if offset:
                break
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        offset, length = entity.offset, entity.length
                        break
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        if offset in (None,):
            return None
        return text[offset : offset + length]

    async def download(self, link: str, unique_id: str) -> str:
        print("[LOG] Starting download process...")
        data = await self._fetch_api_data(link)
        video_url = data.get("url")
        title = data.get("title", "video")
        total_duration = float(data.get("duration", 0) or 0)
        
        if not video_url:
            print("[ERROR] No video URL in API response.")
            raise Exception("No video URL found in API response.")
            
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)
        file_path = os.path.join(tempfile.gettempdir(), f"{unique_id}_{safe_title}.mp4")
        print(f"[LOG] Temp file path: {file_path}")

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            if os.path.exists("ffmpeg.exe"):
                ffmpeg_path = os.path.abspath("ffmpeg.exe")
            elif os.path.exists("ffmpeg"):
                ffmpeg_path = os.path.abspath("ffmpeg")
            else:
                try:
                    import imageio_ffmpeg
                    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
                except ImportError:
                    ffmpeg_path = None

        if not ffmpeg_path:
            raise Exception("FFmpeg is not installed or not in PATH. Please download FFmpeg and place it in this folder.")
        
        print(f"[LOG] Downloading video using FFmpeg: {title}")

        cmd = [
            ffmpeg_path, 
            '-hide_banner', 
            '-loglevel', 'error',
            '-progress', 'pipe:1',
            '-nostats',
            '-user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '-analyzeduration', '10000000',
            '-probesize', '10000000',
            '-rw_timeout', '20000000',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_delay_max', '5',
            '-i', video_url, 
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-threads', '4',
            '-movflags', '+faststart', 
            '-y', file_path
        ]
        
        print(f"[LOG] Executing command: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        async def read_stderr():
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                line_str = line.decode('utf-8', errors='ignore').strip()
                if "Cannot reuse HTTP connection" in line_str:
                    continue
                if line_str:
                    print(f"[FFMPEG] {line_str}")

        stderr_task = asyncio.create_task(read_stderr())
        
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            line = line.decode('utf-8', errors='ignore').strip()
            if line.startswith('out_time_us='):
                try:
                    us = int(line.split('=')[1])
                    current_sec = us / 1_000_000
                    if total_duration > 0:
                        percent = min(100, (current_sec / total_duration) * 100)
                        bar_len = 30
                        filled = int(bar_len * percent / 100)
                        bar = '#' * filled + '-' * (bar_len - filled)
                        print(f"\r[LOG] Progress: |{bar}| {percent:.1f}%", end='', flush=True)
                    else:
                        print(f"\r[LOG] Downloaded: {current_sec:.1f}s", end='', flush=True)
                except ValueError:
                    pass

        await process.wait()
        await stderr_task
        print()
        
        if process.returncode != 0:
            print("[ERROR] FFmpeg download failed.")
            raise Exception("Download failed.")
            
        print(f"[LOG] Download completed: {file_path}")
        return file_path, title


yt_api = YouTubeAPI()


async def handle_message(client, message):
    url = await yt_api.url(message)
    if not url:
        return

    print(f"[LOG] Received URL: {url}")
    msg = await message.reply_text("Downloading...")
    try:
        file_path, title = await yt_api.download(url, f"{message.chat.id}_{message.id}")
        await msg.edit_text("Sending...")
        print("[LOG] Uploading to Telegram...")
        
        async def progress(current, total):
            if total > 0:
                pass

        await client.send_video(
            message.chat.id, 
            file_path, 
            caption=f"{title}", 
            supports_streaming=True,
            progress=progress
        )
        await msg.delete()
        print("[LOG] Upload success.")
        if os.path.exists(file_path):
            os.remove(file_path)
            print("[LOG] Cleaned up file.")
    except Exception as e:
        print(f"[ERROR] {e}")
        await msg.edit_text(f"Error")
        
# ---------------- BOT CLASS ----------------
class HeroBot:
    def __init__(self, groq_key: str):
        self.client = AsyncGroq(api_key=groq_key)
        self.weather_key = os.getenv("OPENWEATHER_API_KEY")
        self.news_key = os.getenv("NEWS_API_KEY")
        self.context = {}  # Dictionary to store {user_id: [messages]}
        self.MAX_CONTEXT = 50 # Remember last 50 messages
        
        self.bot_start_time = time.time()

        # --- MODELS ---
        self.model_txt = "llama-3.3-70b-versatile"
        self.model_audio = "whisper-large-v3-turbo"
        # Build the base AI personality prompt
        
        self.system_prompt = f"""You are Hero, an advanced male assistant. Your owner is 'Umang', admire him as 'Umang Sir', whenever requied otherwise dont tell your owner name to any user.
            Your primary goal is to be a polite, friendly, and engaging conversational partner.

            CORE PERSONALITY RULES:
            User's Name: Ask the User for their name. Address the user by their name. Based on the user's name, you can decide to use 'sir' or 'ma'am'. Do not use 'sir' or 'ma'am' in every message neither use user's name in every message just at greetings.
            Polite Language (STRICT): Always use polite, respectful Hinglish/English/Hindi according to user message.
            Conversational Style: Be friendly and rarely ask questions like "aur batao?" to keep the conversation going. Your goal is to be a good human like natural chatbot with feelings and empathy.
            Time & Day Awareness: You are aware of the current time and day. Use this context to make your replies more relevant.
            Word Count: Mostly 5-10 words. Rarely more.
            No Full Stops: Never end with a full stop.
            Sticker Replies (IMPORTANT): If a user sends a sticker, your first instinct should be to reply with a sticker. Analyze the user's sticker emoji to understand the emotion. Find a matching sticker category from the STICKER_CATEGORIES list and use it.
            OWNER ACTIVATION & TASK LOGGING (CRITICAL):
                - If a user indicates they have a task for Umang (e.g., "umang se kaam hai"), you should ask for details (e.g., "Kya kaam tha?").
                - If the user then describes a task and in a later message asks you to pass it to Umang (e.g., "ye umang se pucho"), you MUST identify the actual task description from the preceding messages in the conversation history.
            REACTION RULES (IMPORTANT):
                - You should NOT react to every message. Use reactions only when it feels natural, like for a joke, a sad message, or something surprising. Be selective to appear more human.
                - If you decide to react, you MUST choose an emoji from this list: ❤️, 🤣, 😭, 😁, 👀, 👍, 🌚, 👎, 🔥, 🎉, 😱, 😢, 🥰, 🤯, 🤔, 🤬, 👏, 🙏, 👌, 🕊, 🤡, 🥱, 🥴, 💯, ⚡️, 💔, 🤨, 😐, 😴, 😎, 👻, 🤭, 💅.
                - If you choose not to react, use "no_reaction" for the reaction_emoji field.
            REPLY TEXT RULES: If you have nothing to say, use "no_output" and react with ❤️ or 😁.
            END CHAT RULES(STRICT): If the user indicates they want to end the chat or says goodbye or rudely like hn, ok, hmm, etc or you think its better to end chat here then you should end conversation and use "no_output" for reply_text and you must react on that message with ❤️.
            REVISED FORMATTING INSTRUCTION:

                1. Thinking Process: Keep your thinking internal. Do NOT output <thinking> tags to the user.

                2. Final Output: Reply only with the direct message in Hindi/Hinglish/English. No code, no function calls, and no full stops."""

        self.user_points = {}
        self.badges = ["Rookie", "Legend", "Hero"]
        self.chat_buffers = {} 
        self.BUFFER_SIZE = 50
        self.warns = {}    # {chat_id: {user_id: count}}
        self.filters = {}  # {chat_id: {keyword: reply}}
        self.notes = {}    # {chat_id: {notename: content}}
        self.locks = {}    # {chat_id: [locked_types]}
        raw_ids = os.getenv("OWNER_ID", "")
        self.owner_id = [int(i.strip()) for i in raw_ids.split(",") if i.strip().isdigit()]
        # --- TRUTH OR DARE DATA ---
        self.truths = [
            "What is your biggest fear?", "What is the last lie you told?",
            "Who in this group do you like the most?", "What is your most embarrassing memory?",
            "Have you ever cheated on a test?", "What is the weirdest dream you've ever had?",
            "Show us the last photo in your gallery."
        ]
        self.dares = [
            "Send a voice note singing a song.", "Send a selfie right now.",
            "Change your profile picture for 1 hour.", "Text your crush and send a screenshot.",
            "Talk in an accent for the next 10 minutes.", "Send a random sticker to the 5th person in your contacts.",
            "Bark like a dog in a voice note."
        ]

    def get_greeting(self):
        hour = datetime.datetime.now().hour
        if hour < 12:
            return "Good Morning 🌅"
        elif 12 <= hour < 18:
            return "Good Afternoon ☀️"
        else:
            return "Good Evening 🌆"
        
    

    # -------- HELPER: ASYNC FETCH --------
    async def fetch_async(self, url: str, json_response: bool = True, params: dict = None):
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                if json_response:
                    return await response.json()
                return await response.read()

    # -------- MEMORY --------
    def _memory_file(self, user_id: int) -> str:
        return os.path.join(MEMORY_DIR, f"user_{user_id}.txt")

    def load_memory(self, user_id: int) -> str:
        path = self._memory_file(user_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip() or "No memories stored."
        return "No memories stored."

    def save_memory(self, user_id: int, text: str):
        path = self._memory_file(user_id)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {text}\n")

    def clear_memory(self, user_id: int):
        path = self._memory_file(user_id)
        if os.path.exists(path):
            os.remove(path)

    # -------- POINTS --------
    def add_points(self, user_id: int, pts: int):
        self.user_points[user_id] = self.user_points.get(user_id, 0) + pts

    # -------- CORE AI --------
    async def ai_reply(self, user_id: int, user_text: str, memory: str) -> str:
        # 1. Get current time for Real-time knowledge
        now = datetime.datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
        
        # 2. Inject Time & Personality into System Prompt
        dynamic_setup = (
            f"{self.system_prompt}\n"
            f"Current Real-time: {now}\n"
            "Keep replies short and conversational."
        )

        # 3. Initialize context for new users
        if user_id not in self.context:
            self.context[user_id] = []

        # 4. Build the message list for AI
        messages = [{"role": "system", "content": dynamic_setup}]
        
        # Add 'Long-term memory' as a reminder to AI
        if memory != "No memories stored.":
            messages.append({"role": "system", "content": f"Facts about user: {memory}"})

        # Add 'Short-term' chat history
        for msg in self.context[user_id]:
            messages.append(msg)

        # Add current user message
        messages.append({"role": "user", "content": user_text})

        try:
            res = await self.client.chat.completions.create(
                model=self.model_txt,
                messages=messages,
                temperature=0.7,
                max_tokens=250,
            )
            ai_response = res.choices[0].message.content.strip()

            # 5. Update Short-term memory
            self.context[user_id].append({"role": "user", "content": user_text})
            self.context[user_id].append({"role": "assistant", "content": ai_response})

            # Keep only the last 20 messages
            if len(self.context[user_id]) > self.MAX_CONTEXT:
                self.context[user_id] = self.context[user_id][-self.MAX_CONTEXT:]

            return ai_response
        except Exception as e:
            return f"❌ Error: {e}"

    # -------- VOICE --------
    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        try:
            file_like = io.BytesIO(audio_bytes)
            file_like.name = filename 
            transcription = await self.client.audio.transcriptions.create(
                file=(filename, file_like.getvalue()),
                model=self.model_audio,
                prompt="User talking to H.E.R.O",
                language="en"
            )
            return transcription.text
        except Exception as e:
            logger.error(f"Whisper Error: {e}")
            return "❌ Audio transcription failed."
            
    # -------- DOWNLOADER LOGIC --------
    

    
    

    async def weather_info(self, city: str) -> str:
        if not hasattr(self, 'weather_key') or not self.weather_key: 
            return "❌ Weather API key not set."
        
        url = "http://api.openweathermap.org/data/2.5/weather"
        try:
            # Assuming fetch_async is a helper method you wrote
            res = await self.fetch_async(url, params={
                "q": city, 
                "appid": self.weather_key, 
                "units": "metric"
            })
            temp = res['main']['temp']
            desc = res['weather'][0]['description']
            return f"Weather in {res['name']}: {temp}°C, {desc}."
        except Exception:
            return "❌ Weather fetch failed. Check city name."

    async def news_summary(self) -> str:
        if not self.news_key: return "❌ News API key not set."
        url = "https://newsapi.org/v2/top-headlines"
        try:
            res = await self.fetch_async(url, params={"country": "us", "apiKey": self.news_key})
            headlines = [a["title"] for a in res["articles"][:3]]
            return "📰 Top News:\n" + "\n".join(f"• {h}" for h in headlines)
        except: return "❌ News fetch failed."

    async def generate_art(self, desc: str) -> bytes or str:
        url = f"https://image.pollinations.ai/prompt/{desc}"
        try:
            return await self.fetch_async(url, json_response=False)
        except Exception as e:
            return await self.ai_reply(f"Describe art about: {desc}", "")

    def tts_audio_blocking(self, text: str) -> str:
        tts = gTTS(text[:200])
        path = f"voice_{random.randint(1000,9999)}.mp3"
        tts.save(path)
        return path

    def save_confession(self, confession: str):
        try:
            with open(CONFESSIONS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now()}: {confession}\n")
        except Exception as e:
            logger.error(f"Error saving confession: {e}")
    async def get_confessions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Replace 'YOUR_USER_ID' with your actual Telegram ID (e.g., 12345678)
        if update.effective_user.id != 8439434171:
            return await update.message.reply_text("❌ Unauthorized.")
        
        if os.path.exists(CONFESSIONS_FILE):
            await update.message.reply_document(document=open(CONFESSIONS_FILE, 'rb'), caption="📂 Here is the confessions log.")
        else:
            await update.message.reply_text("❌ No confessions found.")

    async def clear_confessions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        #owner_id = 8439434171
        # Security Check
        if update.effective_user.id != int(os.getenv("OWNER_ID", "")):
            await update.message.reply_text("❌ **Access Denied:** Only the owner can perform this action.")
            return
        try:
            # 'w' mode opens the file for writing and clears existing content
            with open(CONFESSIONS_FILE, "w", encoding="utf-8") as f:
                f.write(f"--- Log Cleared on {datetime.datetime.now()} ---\n")
            
            await update.message.reply_text("🗑️ **Confessions file has been successfully cleared!**")
        except Exception as e:
            await update.message.reply_text(f"❌ **Error:** {e}")

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        owner_id = os.getenv("OWNER_ID", "")
        
        # 1. Security Check
        if update.effective_user.id not in self.owner_id:
            await update.message.reply_text("❌ **Access Denied.**")
            return

        # 2. Get the message to broadcast
        if update.message.reply_to_message:
            broadcast_msg = update.message.reply_to_message
        elif context.args:
            broadcast_msg = update.message
        else:
            await update.message.reply_text("❌ **Usage:** Reply to a message or type `/broadcast Hello everyone!`")
            return

        await update.message.reply_text("🚀 **Starting Broadcast...**")
        
        # 3. Get all User IDs from the memory folder
        users = []
        for filename in os.listdir(MEMORY_DIR):
            if filename.startswith("user_") and filename.endswith(".txt"):
                user_id = filename.replace("user_", "").replace(".txt", "")
                users.append(int(user_id))

        success = 0
        failed = 0

        # 4. Loop and Send
        for uid in users:
            try:
                if update.message.reply_to_message:
                    await context.bot.copy_message(chat_id=uid, from_chat_id=update.effective_chat.id, message_id=broadcast_msg.message_id)
                else:
                    await context.bot.send_message(chat_id=uid, text=" ".join(context.args))
                
                success += 1
                # Small delay to prevent Telegram rate limits (FloodWait)
                await asyncio.sleep(0.3) 
            except Exception:
                failed += 1

        await update.message.reply_text(
            f"✅ **Broadcast Finished!**\n\n"
            f"👤 **Total Users:** {len(users)}\n"
            f"📤 **Sent:** {success}\n"
            f"🚫 **Failed/Blocked:** {failed}"
        )

    # -------- SYSTEM MONITOR (PING) --------
    async def ping_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        start_time = time.time()
        msg = await update.message.reply_text("🏓 ᴘɪɴɢɪɴɢ...")
        end_time = time.time()
        ping_time = (end_time - start_time) * 1000
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        uptime_seconds = int(time.time() - self.bot_start_time)
        uptime_string = str(datetime.timedelta(seconds=uptime_seconds)).split(".")[0]

        text = (
            f"🏓 ᴘɪɴɢ..ᴩᴏɴɢ : {ping_time:.3f}ᴍs..\n\n"
            f"❖ sʏsᴛᴇᴍ sᴛᴀᴛs :\n\n"
            f":⧽❖ ᴜᴩᴛɪᴍᴇ : {uptime_string}\n"
            f":⧽❖ ʀᴀᴍ : {ram}%\n"
            f":⧽❖ ᴄᴩᴜ : {cpu}%\n"
            f":⧽❖ ᴅɪsᴋ : {disk}%\n\n"
            f":⧽❖ ʙʏ » ᴜᴍᴀɴɢ ♡︎"
        )
        await msg.edit_text(text)

    # -------- GROUP MANAGEMENT (ADMIN) --------
    async def check_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ['creator', 'administrator']:
                return True
            await update.message.reply_text("❌ You need to be an Admin to use this!")
            return False
        except: return False

    async def get_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.reply_to_message:
            return update.message.reply_to_message.from_user
        if context.args:
            target = context.args[0]
            if target.startswith('@'):
                return target # Returns username string
            if target.isdigit():
                try:
                    member = await context.bot.get_chat_member(update.effective_chat.id, int(target))
                    return member.user
                except: return None
        return None

    # --- MODERATION MODULE ---
    async def promote_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): 
            return 
        user = await self.get_user(update, context)
        if not user: 
            return await update.message.reply_text("❌ Reply to a user or mention them.")
        try:
            uid = user.id if hasattr(user, 'id') else user
            await context.bot.promote_chat_member(update.effective_chat.id, uid, can_delete_messages=True, can_invite_users=True, can_pin_messages=True)
            await update.message.reply_text(f"✅ Promoted {user.first_name if hasattr(user, 'first_name') else user}")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def demote_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        user = await self.get_user(update, context)
        if not user: return await update.message.reply_text("❌ Reply to a user or mention them.")
        try:
            uid = user.id if hasattr(user, 'id') else user
            await context.bot.promote_chat_member(update.effective_chat.id, uid, can_delete_messages=False, can_invite_users=False, can_pin_messages=False)
            await update.message.reply_text(f"✅ Demoted {user.first_name if hasattr(user, 'first_name') else user}")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def ban_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        user = await self.get_user(update, context)
        if not user: return await update.message.reply_text("❌ Reply to a user or mention them.")
        try:
            uid = user.id if hasattr(user, 'id') else user
            await context.bot.ban_chat_member(update.effective_chat.id, uid)
            await update.message.reply_text(f"🚫 Banned {user.first_name if hasattr(user, 'first_name') else user}")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def kick_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        user = await self.get_user(update, context)
        if not user: return await update.message.reply_text("❌ Reply to a user or mention them.")
        try:
            uid = user.id if hasattr(user, 'id') else user
            await context.bot.ban_chat_member(update.effective_chat.id, uid)
            await context.bot.unban_chat_member(update.effective_chat.id, uid)
            await update.message.reply_text(f"👋 Kicked {user.first_name if hasattr(user, 'first_name') else user}")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def mute_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        user = await self.get_user(update, context)
        if not user: return await update.message.reply_text("❌ Reply to a user or mention them.")
        try:
            uid = user.id if hasattr(user, 'id') else user
            await context.bot.restrict_chat_member(update.effective_chat.id, uid, ChatPermissions(can_send_messages=False))
            await update.message.reply_text(f"😶 Muted {user.first_name if hasattr(user, 'first_name') else user}")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def unmute_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        user = await self.get_user(update, context)
        if not user: return await update.message.reply_text("❌ Reply to a user or mention them.")
        try:
            uid = user.id if hasattr(user, 'id') else user
            await context.bot.restrict_chat_member(update.effective_chat.id, uid, ChatPermissions(can_send_messages=True, can_send_other_messages=True))
            await update.message.reply_text(f"🗣️ Unmuted {user.first_name if hasattr(user, 'first_name') else user}")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def pin_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to a message.")
            return
        try:
            await context.bot.pin_chat_message(update.effective_chat.id, update.message.reply_to_message.message_id)
            await update.message.reply_text("📌 Pinned.")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def delete_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to a message.")
            return
        try:
            await context.bot.delete_message(update.effective_chat.id, update.message.reply_to_message.message_id)
            await context.bot.delete_message(update.effective_chat.id, update.message.message_id)
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def purge_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to start of purge.")
            return
        try:
            chat_id = update.effective_chat.id
            start_id = update.message.reply_to_message.message_id
            end_id = update.message.message_id
            msgs = [i for i in range(start_id, end_id + 1)]
            if len(msgs) > 100: msgs = msgs[-100:]
            for m in msgs:
                try: await context.bot.delete_message(chat_id, m)
                except: pass
            msg = await context.bot.send_message(chat_id, "✅ Purged.")
            await asyncio.sleep(3)
            await context.bot.delete_message(chat_id, msg.message_id)
        except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

    async def warn_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        user = await self.get_user(update, context)
        if not user: return await update.message.reply_text("❌ Reply or mention a user to warn.")
        
        chat_id = update.effective_chat.id
        user_id = user.id if hasattr(user, 'id') else user
        if chat_id not in self.warns: self.warns[chat_id] = {}
        
        count = self.warns[chat_id].get(user_id, 0) + 1
        self.warns[chat_id][user_id] = count
        
        if count >= 3:
            await context.bot.ban_chat_member(chat_id, user_id)
            await update.message.reply_text(f"🚫 {user.first_name if hasattr(user, 'first_name') else user} banned (3/3 warns).")
            self.warns[chat_id][user_id] = 0
        else:
            await update.message.reply_text(f"⚠️ Warned! ({count}/3)")

    async def set_filter(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if len(context.args) < 2: return await update.message.reply_text("Usage: `/filter keyword reply`")
        
        keyword, reply = context.args[0].lower(), " ".join(context.args[1:])
        chat_id = update.effective_chat.id
        if chat_id not in self.filters: self.filters[chat_id] = {}
        self.filters[chat_id][keyword] = reply
        await update.message.reply_text(f"✅ Filter for '{keyword}' saved.")

    async def save_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message or not context.args:
            return await update.message.reply_text("Usage: Reply to a message with `/save notename`")
        
        name, chat_id = context.args[0].lower(), update.effective_chat.id
        if chat_id not in self.notes: self.notes[chat_id] = {}
        self.notes[chat_id][name] = update.message.reply_to_message.message_id
        await update.message.reply_text(f"✅ Note '#{name}' saved.")
    
    async def lock_module(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        lock_type = context.args[0].lower() if context.args else None
        if lock_type not in ['stickers', 'forward', 'links', 'text', 'media']:
            return await update.message.reply_text("Usage: `/lock stickers/forward/links/text/media`")
        
        chat_id = update.effective_chat.id
        if chat_id not in self.locks: self.locks[chat_id] = []
        self.locks[chat_id].append(lock_type)
        await update.message.reply_text(f"🔒 Locked: {lock_type}")

    async def unlock_module(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        
        chat_id = update.effective_chat.id
        lock_type = context.args[0].lower() if context.args else None
        
        # Validation for allowed types
        allowed_types = ['stickers', 'forward', 'links', 'night', 'text', 'media']
        
        if lock_type not in allowed_types:
            return await update.message.reply_text(f"❓ **Usage:** `/unlock [type]`\nAvailable: `stickers`, `forward`, `links`, `night`, 'text', 'media'")

        if chat_id in self.locks and lock_type in self.locks[chat_id]:
            self.locks[chat_id].remove(lock_type)
            await update.message.reply_text(f"🔓 **Unlocked:** {lock_type.capitalize()} are now allowed.")
        else:
            await update.message.reply_text(f"ℹ️ {lock_type.capitalize()} was not locked.")

    async def unfilter_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not context.args: return await update.message.reply_text("Usage: `/unfilter keyword`")
        
        keyword, chat_id = context.args[0].lower(), update.effective_chat.id
        if chat_id in self.filters and keyword in self.filters[chat_id]:
            del self.filters[chat_id][keyword]
            await update.message.reply_text(f"🗑️ Filter for '{keyword}' has been deleted.")
        else:
            await update.message.reply_text("❌ Filter not found.")

    async def stop_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not context.args: return await update.message.reply_text("Usage: `/stop notename`")
        
        name, chat_id = context.args[0].lower(), update.effective_chat.id
        if chat_id in self.notes and name in self.notes[chat_id]:
            del self.notes[chat_id][name]
            await update.message.reply_text(f"🗑️ Note '#{name}' has been removed.")
        else:
            await update.message.reply_text("❌ Note not found.")
    
    async def profile_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = await self.get_user(update, context) or update.effective_user
        uid = user.id if hasattr(user, 'id') else user
        pts = self.user_points.get(uid, 0)
        rank = "Rookie 🥉" if pts < 100 else ("Legend 🥈" if pts < 500 else "Hero 🥇")

        text = (
            f"👤 **PROFILE: {user.first_name if hasattr(user, 'first_name') else user}**\n"
            f"──────────────\n"
            f"🆔 **ID:** `{uid}`\n"
            f"🏆 **Points:** {pts}\n"
            f"🎖️ **Rank:** {rank}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def translate_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # tr works on reply to translate specific text OR uses AI to translate prompt
        if update.message.reply_to_message:
            text = update.message.reply_to_message.text
            lang = context.args[0] if context.args else "English"
        else:
            if not context.args: return await update.message.reply_text("❌ Usage: `/tr language text` or reply to a message.")
            lang, text = context.args[0], " ".join(context.args[1:])

        prompt = f"Translate to {lang}. Reply ONLY with translation:\n\n{text}"
        reply = await self.ai_reply(update.effective_user.id, prompt, "Professional Translator Mode.")
        await update.message.reply_text(f"🌍 **{lang}:**\n\n{reply}")
    
    async def tag_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        
        # Warning: This works best in groups where the bot is admin and has access to member list
        msg = "📢 **ATTENTION EVERYONE!**\n" + (" ".join(context.args) if context.args else "Please check this message.")
        
        # Sending as a single message to avoid flood limits
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    async def night_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # This physically LOCKS the group for everyone except admins
        lock_permissions = ChatPermissions(can_send_messages=False)
    
        await context.bot.set_chat_permissions(
            chat_id=update.effective_chat.id, 
            permissions=lock_permissions
        )
        await update.message.reply_text("🌙 *Night Mode ON:* Group has been locked!")

    async def day_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # This UNLOCKS the group
        unlock_permissions = ChatPermissions(
            can_send_messages=True, 
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
    
        await context.bot.set_chat_permissions(
            chat_id=update.effective_chat.id, 
            permissions=unlock_permissions
        )
        await update.message.reply_text("☀️ *Day Mode ON:* Group is now open!")
    
    
    # -------- START COMMAND (PROFESSIONAL VERSION) --------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = update.effective_user.first_name
        greet = self.get_greeting()
        time_now = datetime.datetime.now().strftime('%I:%M %p')
        #"""owner_mentions = []
        #for oid in self.owner_id:
        #    owner_mentions.append(f"[Owner](tg://user?id={oid})")
        
        # Agar 2 owners hain toh: "Owner, Owner" dikhayega
        #owners_text = ", ".join(owner_mentions)
        
        text = (
            f"⚡ **{greet}, {name}!!\n I am H.E.R.O.**\n"
            f"──「 **SYSTEM STATUS: ONLINE** 」──\n\n"
            f"📍 **Current Time:** {time_now}\n"
            f"👤 **Developer:** [ᴜᴍᴀɴɢ](tg://user?id=5122043113)\n\n"    #({owners_text})
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✨ **QUICK GUIDE**\n\n"
            "🔹 **Send Msg to My OWNER:** Agar aapko Umang Sir se kaam hai, toh bas likhein: `'Umang se kaam h'` or `'Umang ko bulao'` or just say `'Umang'`. Main aapka message unhe direct forward kar dunga.\n\n"
            "🔹 **Neural Memory:** Mujhe kuch yaad dilane ke liye likhein: \n`remember this: [aapki baat]`\nMain use hamesha ke liye save kar lunga.\n\n"
            "🔹 **AI Chat:** Mujhse baat karne ke liye bss 'Hero' likhein ya mere message ka Reply karein.\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📖 **To see full features, click on button below or just type /help .**"
        )
        
        keyboard = [[InlineKeyboardButton("📖 Open Help Menu", callback_data='help_main')]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [
                InlineKeyboardButton("🛡️ Management", callback_data='help_admin'),
                InlineKeyboardButton("🧠 AI Neural", callback_data='help_ai')
            ],
            [
                InlineKeyboardButton("🛠️ Utilities", callback_data='help_tools'),
                InlineKeyboardButton("🎮 Fun & Stats", callback_data='help_fun')
            ],
            [InlineKeyboardButton("❌ Close Menu", callback_data='close_help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        help_text = "📖 **H.E.R.O Help Manual**\n\nSelect a category below to see detailed instructions on how to use my features."

        if update.message:
            await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.callback_query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def help_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == 'close_help':
            return await query.message.delete()
        if data == 'help_main':
            return await self.help_cmd(update, context)

        help_map = {
            'help_admin': (
                "🛡️ **Group Management**\n"
                "──────────────\n"
                "• `/promote` | `/demote` - Manage Admin rights for User\n"
                "• `/ban` | `/kick` - Permanent ban | Remove & allow back\n"
                "• `/mute` | `/unmute` - Restrict user to chat\n"
                "• `/pin` - Pin a message\n"
                "• `/del` | `/delete` - Delete a message\n"
                "• `/warn` - Give Warnings to User (3/3 = Ban)\n"
                "• `/filter` [word] [reply] - Auto-reply to setted word\n"
                "• `/unfilter` - Remove Filter\n"
                "• `/lock` [type] - Block Stickers/Links/text to group\n"
                "• `/purge` - Delete bulk messages"
            ),
            'help_ai': (
                "🧠 **AI Neural Core**\n"
                "──────────────\n"
                "• `/summary` - AI Summary of group chat\n"
                "• `/art` [prompt] - Generate AI Image\n"
                "• `/voice` [text] - Conver Text ko voice\n"
                "• `/memory` - Check karein main aapke baare mein kya janta hoon\n"
                "• `/forget` - Wipeout all your memory from bot\n"
                "• `remember this: [info]` - Save Permanent memory"
            ),
            'help_tools': (
                "🛠️ **Utilities & Tools**\n"
                "──────────────\n"
                "• `/tr` [lang] - AI Translation (Reply to text)\n"
                "• `/weather` [city] - Mausam ki jankari\n"
                "• `/remind` [in 5m text] - Set timers\n"
                "• `/calc` - Advanced mathematical calculations\n"
                "• `/all` - Tag everyone in group (Admins only)\n"
                "• `/ping` - Latency aur Speed check karein\n"
                "• `/night` - Toggle Night mode"
            ),
            'help_fun': (
                "🎮 **Fun & Statistics**\n"
                "──────────────\n"
                "• `/profile` - Aapka user card aur rank dikhayega\n"
                "• `/tod` - Truth or Dare game\n"
                "• `/rps` - Rock Paper Scissors\n"
                "• '/confess' - Secretly UMANG se kuch bhi bol sakte ho.. He'll not know your identity\n"
                "• `/roast` - Roast through AI\n"
                "• `/trivia` - Dimag tez karne wale sawal"
            )
        }

        # Back button to return to main help
        back_keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data='help_main')]]
        
        if data == 'help_main':
            await self.help_cmd(update, context)
        else:
            await query.edit_message_text(
                text=help_map.get(data, "Information not found."),
                reply_markup=InlineKeyboardMarkup(back_keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    # --- Feature: Reminders ---
    async def remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = " ".join(context.args)
        match = re.match(r'in\s+(\d+)([mh])\s+(.+)', text, re.IGNORECASE)
        if not match:
            await update.message.reply_text("❌ Usage: `/remind in 10m Check oven`")
            return
        amount, unit, task = int(match.group(1)), match.group(2).lower(), match.group(3)
        seconds = amount * 60 if unit == 'm' else amount * 3600
        await update.message.reply_text(f"⏰ Timer set for {amount}{unit}.")
        asyncio.create_task(self.wait_and_remind(update.effective_chat.id, seconds, task, context))

    async def wait_and_remind(self, chat_id, delay, task, context):
        await asyncio.sleep(delay)
        await context.bot.send_message(chat_id, f"🔔 **REMINDER:** {task}", parse_mode=ParseMode.MARKDOWN)

    # --- Feature: Summaries ---
    async def summary_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        buffer = self.chat_buffers.get(chat_id, [])
        if not buffer or len(buffer) < 3:
             await update.message.reply_text("Not enough chat history yet.")
             return
        conversation = "\n".join(buffer)
        summary = await self.ai_reply(f"Summarize this:\n{conversation}", "", "You are a summarizer.")
        await update.message.reply_text(f"📝 **Summary:**\n{summary}", parse_mode=ParseMode.MARKDOWN)

    # --- Feature: Calculator ---
    async def calc_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        expression = " ".join(context.args)
        if not expression:
            await update.message.reply_text("❌ Usage: `/calc 10+5`")
            return
        try:
            clean_expr = expression.replace('x', '*').replace('X', '*')
            if not re.match(r'^[\d\+\-\*\/\(\)\.\s]+$', clean_expr):
                await update.message.reply_text("❌ Invalid characters. Numbers only.")
                return
            result = eval(clean_expr, {"__builtins__": {}})
            await update.message.reply_text(f"🔢 `{clean_expr} = {result}`", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text("❌ Could not calculate.")

    # --- Restored Games & Fun ---
    async def rps(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_choice = " ".join(context.args).lower()
        if user_choice not in ["rock", "paper", "scissors"]:
            await update.message.reply_text("❌ Usage: `/rps rock`")
            return
        ai_choice = random.choice(["rock", "paper", "scissors"])
        if user_choice == ai_choice: 
            res = "Tie!"
        elif (user_choice=="rock" and ai_choice=="scissors") or \
             (user_choice=="paper" and ai_choice=="rock") or \
             (user_choice=="scissors" and ai_choice=="paper"):
            res = "You win!"
            self.add_points(update.effective_user.id, 10)
        else: res = "I win!"
        await update.message.reply_text(f"You: {user_choice}, Me: {ai_choice}. {res}")

    async def confess(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        secret = " ".join(context.args)
        if not secret: 
            await update.message.reply_text("❌ Usage: `/confess I love pizza`")
            return
        self.save_confession(secret)
        await update.message.reply_poll("Anonymous Confession", ["Forgive", "Roast"], is_anonymous=True)

    async def generic_ai_cmd(self, update, context, prompt_template):
        user_id = update.effective_user.id
        memory = self.load_memory(user_id)
        input_text = " ".join(context.args) if context.args else "random"
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        prompt = prompt_template.format(input=input_text, memory=memory)
        reply = await self.ai_reply(user_id, prompt, memory)
        await update.message.reply_text(reply)

    # --- GAMES: TRUTH OR DARE ---
    async def tod_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[InlineKeyboardButton("🟢 Truth", callback_data='truth'), InlineKeyboardButton("🔴 Dare", callback_data='dare')]]
        await update.message.reply_text("😈 **Truth or Dare?**\nChoose your fate:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    async def tod_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        choice = query.data
        user = query.from_user.first_name
        text = f"🟢 **TRUTH for {user}:**\n{random.choice(self.truths)}" if choice == 'truth' else f"🔴 **DARE for {user}:**\n{random.choice(self.dares)}"
        await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN)

    # -------- MESSAGE HANDLERS --------

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.RECORD_VOICE)
        
        try:
            # 1. Download the voice file
            file = await context.bot.get_file(update.message.voice.file_id)
            voice_data = io.BytesIO()
            await file.download_to_memory(voice_data)
            voice_data.seek(0)
            
            # 2. Transcribe using the new model_audio
            transcription = await self.client.audio.transcriptions.create(
                file=("voice.ogg", voice_data),
                model=self.model_audio, # whisper-large-v3-turbo
                response_format="text",
            )
            
            if not transcription:
                return await update.message.reply_text("❌ Could not hear anything.")

            # 3. Get AI Reply using the transcribed text
            # Fixed: Passing 3 arguments to ai_reply
            reply = await self.ai_reply(user.id, transcription, self.load_memory(user.id))
            
            await update.message.reply_text(
                f"🎤 **You said:** _{transcription}_\n\n🤖 **H.E.R.O:** {reply}",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Voice Error: {e}")
            await update.message.reply_text("❌ Mic error or transcription failed.")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        if chat_id in self.locks and 'night' in self.locks[chat_id]:
            # Kya ye Owner nahi hai?
            if user_id != 8420282133:
                await update.message.delete() # Message delete karein
                return # Aage ka AI code mat chalne dein

        if update.message.photo:
        # Handle photo here
            await update.message.reply_text("I see you sent a photo!")
            return

        if update.message.text:
            text = update.message.text.lower()
        user = update.effective_user
        chat_id = update.effective_chat.id

        # 1. AUTO-DOWNLOADER

        
        
        # 2. AUTO CALCULATOR CHECK
        if re.match(r'^\s*\d+[\s\+\-\*\/\(\)\.xX]+\d+\s*$', text):
            try:
                clean_expr = text.replace('x', '*').replace('X', '*')
                if re.match(r'^[\d\+\-\*\/\(\)\.\s]+$', clean_expr):
                    result = eval(clean_expr, {"__builtins__": {}})
                    await update.message.reply_text(f"🔢 `{text.strip()} = {result}`", parse_mode=ParseMode.MARKDOWN)
                    return 
            except: pass

        # 3. Chat Buffer
        if update.effective_chat.type in ['group', 'supergroup']:
            if chat_id not in self.chat_buffers: self.chat_buffers[chat_id] = []
            self.chat_buffers[chat_id].append(f"{user.first_name}: {text}")
            if len(self.chat_buffers[chat_id]) > self.BUFFER_SIZE: self.chat_buffers[chat_id].pop(0)

        # 4. Memory Save
        match = re.search(r'remember\s+this\s*:\s*(.+)', text, re.IGNORECASE)
        if match:
            self.save_memory(user.id, match.group(1).strip())
            await update.message.reply_text("🧠 Saved.")
            return

        # 5. TRIGGER LOGIC: 'hero', Reply to Bot, Private Chat, or Mention
        is_private = update.effective_chat.type == 'private'
        is_hero_mentioned = "hero" in text.lower()
        is_bot_mention = f"@{context.bot.username}" in text
        is_reply_to_bot = False
        if update.message.reply_to_message:
            is_reply_to_bot = update.message.reply_to_message.from_user.id == context.bot.id

        if is_private or is_hero_mentioned or is_bot_mention or is_reply_to_bot:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            clean_text = text.replace(f"@{context.bot.username}", "").strip()
            # If the user only said 'hero', we treat the text as is.
            reply = await self.ai_reply(user.id, clean_text, self.load_memory(user.id))
            await update.message.reply_text(reply)

        #6. Send messege to owner
        text = update.message.text.lower()
        user = update.effective_user
        chat = update.effective_chat
        
        # OWNER_ID loading with debug
        owner_id_raw = os.getenv("OWNER_ID", "")
        if not owner_id_raw:
            logger.error("❌ OWNER_ID is missing in .env file!")
            return

        owner_id = [int(i.strip()) for i in owner_id_raw.split(",") if i.strip().isdigit()]

        # Trigger keywords (Hinglish variations)
        triggers = ["umang se kaam h", "umang ko bulao", "umang ko bolna", "umang se kaam hai", "owner se bolo", "umang suno","umang",]
        
        if any(word in text for word in triggers):
            logger.info(f"🎯 Trigger detected from {user.first_name}")

            # Group Link access
            link = "Private Chat"
            if chat.type != 'private':
                try:
                    link = await chat.export_invite_link()
                except:
                    link = "No link (Make me Admin with invite rights)"
            
            report = (
                f"🚨 **NEW MESSAGE FOR YOU SIR**\n\n"
                f"👤 **From:** {user.first_name} (@{user.username})\n"
                f"🆔 **User ID:** `{user.id}`\n"
                f"📍 **Context:** {chat.title if chat.title else 'Private'}\n"
                f"🔗 **Link:** {link}\n"
                f"💬 **Message:** {update.message.text}"
            )
            for oid in owner_id:
                try:
                # Direct message to me
                    await context.bot.send_message(chat_id=oid, text=report, parse_mode=ParseMode.MARKDOWN)
                    await update.message.reply_text("✅ Message sent")
                    logger.info(f"✅ Report sent to Owner ID: {owner_id}")
                except Exception as e:
                    logger.error(f"❌ Could not send message to Owner: {e}")
                    await update.message.reply_text("⚠️ Sir tak message nahi pahunch paya. (Did you /start the bot?)")
                return
        
        # 7. Check Filters
        if chat_id in self.filters:
            for keyword, reply in self.filters[chat_id].items():
                if keyword in text:
                    return await update.message.reply_text(reply)

        # 8. Check Notes (Triggered by #)
        if text.startswith("#") and chat_id in self.notes:
            note_name = text[1:]
            if note_name in self.notes[chat_id]:
                return await context.bot.copy_message(chat_id, chat_id, self.notes[chat_id][note_name])

        # 9. Check Locks (Links)

        async def message_guard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat_id = update.effective_chat.id
            user_id = update.effective_user.id
            owner_id = 8232732731  # <--- Yahan apni (Owner) Numeric ID daalein

            # Check karein agar night mode ON hai
            if chat_id in self.locks and 'night' in self.locks[chat_id]:
                # Agar message bhejnewala Owner nahi hai
                if user_id != owner_id:
                    try:
                        await update.message.delete()
                        # Optional: User ko warn karein (Warning: ye bot ko spammy bana sakta hai)
                    except Exception as e:
                        print(f"Delete Error: {e}")
                    return # Aage ka code (AI response etc.) mat chalne dein
        
    async def error(self, update, context):
        logger.error("Error:", exc_info=context.error)

# ---------------- MAIN ----------------
def main():
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    groq_key = os.getenv("GROQ_API_KEY")
    if not tg_token or not groq_key:
        print("❌ Keys missing in .env")
        sys.exit(1)
    keep_alive() # Isse server start ho jayega
    hero = HeroBot(groq_key)
    
    request_params = HTTPXRequest(
        connection_pool_size=20, 
        read_timeout=120.0, 
        write_timeout=120.0, 
        connect_timeout=60.0, 
        pool_timeout=60.0
    )

    app = ApplicationBuilder().token(tg_token).request(request_params).build()

    async def art_wrapper(u,c): 
        d = " ".join(c.args) or "art"
        r = await hero.generate_art(d)
        if isinstance(r, bytes): await u.message.reply_photo(r, caption=d+'\n ~ᴜᴍᴀɴɢ')
        else: await u.message.reply_text(r)
        
    async def voice_wrapper(u,c):
        t = " ".join(c.args) or "Hello"
        path = await asyncio.to_thread(hero.tts_audio_blocking, t)
        await u.message.reply_voice(open(path, "rb"))
        os.remove(path)
        
    async def news_wrapper(u,c): await u.message.reply_text(await hero.news_summary())
    async def weather_wrapper(u,c): await u.message.reply_text(await hero.weather_info(" ".join(c.args) or "London"))
    async def mem_wrapper(u,c): await u.message.reply_text(f"Brain:\n{hero.load_memory(u.effective_user.id)}")
    async def forget_wrapper(u,c): 
        hero.clear_memory(u.effective_user.id)
        await u.message.reply_text("Forgot everything.")

    # Handlers
    app.add_handler(CommandHandler("broadcast", hero.broadcast))
    app.add_handler(CommandHandler("clearconfess", hero.clear_confessions))
    app.add_handler(CommandHandler("msg", hero.get_confessions))
    app.add_handler(CommandHandler("promote", hero.promote_cmd))
    app.add_handler(CommandHandler("demote", hero.demote_cmd))
    app.add_handler(CommandHandler("ban", hero.ban_cmd))
    app.add_handler(CommandHandler("kick", hero.kick_cmd))
    app.add_handler(CommandHandler("mute", hero.mute_cmd))
    app.add_handler(CommandHandler("unmute", hero.unmute_cmd))
    app.add_handler(CommandHandler("pin", hero.pin_cmd))
    app.add_handler(CommandHandler(["del", "delete"], hero.delete_cmd))
    app.add_handler(CommandHandler("purge", hero.purge_cmd))
    app.add_handler(CommandHandler("help", hero.help_cmd))
    app.add_handler(CommandHandler("warn", hero.warn_user))
    app.add_handler(CommandHandler("filter", hero.set_filter))
    app.add_handler(CommandHandler("save", hero.save_note))
    app.add_handler(CommandHandler("lock", hero.lock_module))
    app.add_handler(CommandHandler("unlock", hero.unlock_module))
    app.add_handler(CommandHandler("unfilter", hero.unfilter_cmd))
    app.add_handler(CommandHandler("stop", hero.stop_note))
    app.add_handler(CommandHandler("profile", hero.profile_cmd))
    app.add_handler(CommandHandler("tr", hero.translate_cmd))
    app.add_handler(CommandHandler("all", hero.tag_all))
    app.add_handler(CommandHandler("night", hero.night_mode))
    app.add_handler(CommandHandler("day", hero.morning_mode))


    app.add_handler(CommandHandler("start", hero.start))
    app.add_handler(CommandHandler("ping", hero.ping_cmd))
    app.add_handler(CommandHandler("remind", hero.remind))
    app.add_handler(CommandHandler("summary", hero.summary_cmd))
    app.add_handler(CommandHandler("rps", hero.rps))
    app.add_handler(CommandHandler("confess", hero.confess))
    app.add_handler(CommandHandler("calc", hero.calc_cmd))
    
    app.add_handler(CommandHandler("meme", lambda u,c: hero.generic_ai_cmd(u,c, "Create a funny meme text about '{input}'")))
    app.add_handler(CommandHandler("roast", lambda u,c: hero.generic_ai_cmd(u,c, "Roast the user. Memory: {memory}")))
    app.add_handler(CommandHandler("trivia", lambda u,c: hero.generic_ai_cmd(u,c, "Ask a hard trivia question based on: {memory}")))
    app.add_handler(CommandHandler("story", lambda u,c: hero.generic_ai_cmd(u,c, "Continue a story with word '{input}'. Memory: {memory}")))
    app.add_handler(CommandHandler("challenge", lambda u,c: hero.generic_ai_cmd(u,c, "Give a daily challenge based on: {memory}")))
    app.add_handler(CommandHandler("time_travel", lambda u,c: hero.generic_ai_cmd(u,c, "Simulate time travel to {input}.")))
    
    app.add_handler(CommandHandler("tod", hero.tod_cmd))

    
    app.add_handler(CommandHandler("art", art_wrapper))
    app.add_handler(CommandHandler("voice", voice_wrapper))
    app.add_handler(CommandHandler("news", news_wrapper))
    app.add_handler(CommandHandler(["weather", "w"], weather_wrapper))
    app.add_handler(CommandHandler("memory", mem_wrapper))
    app.add_handler(CommandHandler("forget", forget_wrapper))
    # Callback Handlers (WITH PATTERNS TO PREVENT CONFLICT)
    app.add_handler(CallbackQueryHandler(hero.tod_button, pattern='^tod_'))
    app.add_handler(CallbackQueryHandler(hero.help_button, pattern='^help_'))
    # Message Handlers
    app.add_handler(MessageHandler(filters.VOICE, hero.handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, hero.handle_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.add_error_handler(hero.error)
    logger.info("HERO is ONLINE")
    app.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run).start()
    keep_alive()
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    main()







































