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
import psutil 
import yt_dlp

from dotenv import load_dotenv
from gtts import gTTS
from telegram import Update, Poll, ChatPermissions
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
from telegram.constants import ChatAction, ParseMode
from telegram.request import HTTPXRequest 
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from groq import AsyncGroq
from flask import Flask
from threading import Thread

app = Flask('')
@app.route('/')
def home():
    return "I am alive!"

def run():
    # Render default port 10000 use karta hai
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
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
os.makedirs(MEMORY_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ---------------- BOT CLASS ----------------
class HeroBot:
    def __init__(self, groq_key: str):
        self.client = AsyncGroq(api_key=groq_key)
        self.weather_key = os.getenv("OPENWEATHER_API_KEY")
        self.news_key = os.getenv("NEWS_API_KEY")
        
        self.bot_start_time = time.time()

        # --- MODELS ---
        self.model_txt = "llama-3.3-70b-versatile"
        self.model_audio = "distil-whisper-large-v3-en"

        self.system_prompt = (
            "You are H.E.R.O — an advanced AI assistant, created by ᴜᴍᴀɴɢ .\n"
            "Traits: Intelligent, Witty, Confident, Helpful, Uncensored.\n"
            "You have memory for each user separately.\n"
            "Only store memory when the user explicitly says 'remember this:'."
        )

        self.user_points = {}
        self.badges = ["Rookie", "Hero", "Legend"]
        self.chat_buffers = {} 
        self.BUFFER_SIZE = 50

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
    async def ai_reply(self, user_text: str, memory: str, system_override: str = None) -> str:
        try:
            messages = [
                {"role": "system", "content": system_override or self.system_prompt},
                {"role": "user", "content": f"USER MEMORY:\n{memory}\n\nUSER MESSAGE:\n{user_text}"},
            ]
            res = await self.client.chat.completions.create(
                model=self.model_txt,
                messages=messages,
                temperature=0.7,
                max_tokens=600,
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            logger.exception("Groq error")
            return f"❌ AI Error: {e}"

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
    async def auto_download(self, url: str, update: Update):
        msg = await update.message.reply_text("⏳ **Detecting Link... Downloading...**")
        ydl_opts = {'format': 'best', 'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s', 'quiet': True}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=True)
                filename = ydl.prepare_filename(info)
                await update.message.reply_video(video=open(filename, 'rb'), caption=f"✅ {info.get('title')}")
                os.remove(filename)
                await msg.delete()
        except: await msg.edit_text("❌ Download Failed.")
    # -------- UTILITIES --------
    async def weather_info(self, city: str) -> str:
        if not self.weather_key: return "❌ Weather API key not set."
        url = "http://api.openweathermap.org/data/2.5/weather"
        try:
            res = await self.fetch_async(url, params={"q": city, "appid": self.weather_key, "units": "metric"})
            return f"Weather in {res['name']}: {res['main']['temp']}°C, {res['weather'][0]['description']}."
        except: return "❌ Weather fetch failed."

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
            f":⧽❖ ʙʏ » mayank ♡︎"
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

    async def promote_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to a user.")
            return
        try:
            await context.bot.promote_chat_member(
                update.effective_chat.id, update.message.reply_to_message.from_user.id,
                can_delete_messages=True, can_invite_users=True, can_pin_messages=True
            )
            await update.message.reply_text("✅ User Promoted.")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def demote_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to a user.")
            return
        try:
            await context.bot.promote_chat_member(
                update.effective_chat.id, update.message.reply_to_message.from_user.id,
                can_delete_messages=False, can_invite_users=False, can_pin_messages=False
            )
            await update.message.reply_text("✅ User Demoted.")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def ban_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to a user.")
            return
        try:
            await context.bot.ban_chat_member(update.effective_chat.id, update.message.reply_to_message.from_user.id)
            await update.message.reply_text("🚫 Banned.")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def kick_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to a user.")
            return
        try:
            uid = update.message.reply_to_message.from_user.id
            await context.bot.ban_chat_member(update.effective_chat.id, uid)
            await context.bot.unban_chat_member(update.effective_chat.id, uid)
            await update.message.reply_text("👋 Kicked.")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def mute_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to a user.")
            return
        try:
            uid = update.message.reply_to_message.from_user.id
            await context.bot.restrict_chat_member(update.effective_chat.id, uid, ChatPermissions(can_send_messages=False))
            await update.message.reply_text("😶 Muted.")
        except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

    async def unmute_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.check_admin(update, context): return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to a user.")
            return
        try:
            uid = update.message.reply_to_message.from_user.id
            await context.bot.restrict_chat_member(update.effective_chat.id, uid, ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True))
            await update.message.reply_text("🗣️ Unmuted.")
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

    # -------- START COMMAND (PROFESSIONAL VERSION) --------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = update.effective_user.first_name
        text = (
            f"⚡ **Welcome, {name}! I am H.E.R.O.**\n"
            f"─── 「 **SYSTEM STATUS: ONLINE** 」 ───\n\n"
            "🛡️ **ADMIN COMMAND CENTER**\n"
            "• `/promote` | `/demote` - Manage admin rights\n"
            "• `/ban` | `/kick` - Remove members\n"
            "• `/mute` | `/unmute` - Restrict chat\n"
            "• `/pin` | `/del` - Message management\n"
            "• `/purge` - Clean chat (Reply to start)\n\n"
            "🧠 **AI NEURAL CORE**\n"
            "• `/memory` - View what I know about you\n"
            "• `/forget` - Wipe your local memory\n"
            "• `/summary` - Recap recent group chat\n"
            "• `/voice` [text] - Generate AI speech\n"
            "• `/art` [prompt] - Generate AI images\n\n"
            "🛠️ **UTILITY & TOOLS**\n"
            "• `/calc` [math] - Advanced calculator\n"
            "• `/remind` [in 5m text] - Set timers\n"
            "• `/weather` [city] - Real-time weather\n"
            "• `/news` - Get latest global headlines\n"
            "• `/ping` - Check latency & system health\n\n"
            "🎮 **ENTERTAINMENT HUB**\n"
            "• `/tod` - Truth or Dare engine\n"
            "• `/rps` [choice] - Rock Paper Scissors\n"
            "• `/confess` [text] - Anonymous polls\n"
            "• `/trivia` | `/roast` - AI humor\n\n"
            "──────────────\n"
            "💡 *Tip: Mention 'Hero' or reply to me to chat! Use /help for more info.*"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    # -------- HELP COMMAND --------
    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [
                InlineKeyboardButton("🛡️ Admin", callback_data='help_admin'),
                InlineKeyboardButton("🧠 AI Core", callback_data='help_ai')
            ],
            [
                InlineKeyboardButton("🛠️ Tools", callback_data='help_tools'),
                InlineKeyboardButton("🎮 Fun", callback_data='help_fun')
            ],
            [InlineKeyboardButton("📜 Full List", callback_data='help_all')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        help_text = (
            "📖 **H.E.R.O Help Manual**\n\n"
            "Select a category below to see detailed instructions on how to use my features."
        )
        
        if update.message:
            await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else: # For callback queries
            await update.callback_query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    # -------- HELP CALLBACK HANDLER --------
    async def help_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        # Define help text for each category
        help_map = {
            'help_admin': (
                "🛡️ **Admin Command Details**\n\n"
                "• `/promote` - Give a user admin rights (Reply to user)\n"
                "• `/ban` - Permanent ban from group\n"
                "• `/kick` - Remove and allow back\n"
                "• `/mute` - Stop user from sending messages\n"
                "• `/purge` - Delete many messages (Reply to the start message)"
            ),
            'help_ai': (
                "🧠 **AI & Memory Details**\n\n"
                "• `/summary` - AI generates a recap of the last 50 messages\n"
                "• `/art [prompt]` - Creates an image based on your text\n"
                "• `/voice [text]` - Converts your text into an audio file\n"
                "• `remember this: [info]` - Saves a fact to your personal memory"
            ),
            'help_tools': (
                "🛠️ **Utilities & Tools**\n\n"
                "• `/calc [expression]` - Solve complex math\n"
                "• `/remind in [time][m/h] [task]` - e.g., `/remind in 10m check coffee`\n"
                "• `/weather [city]` - Get current weather reports\n"
                "• `/news` - Headlines from around the world"
            ),
            'help_fun': (
                "🎮 **Games & Fun**\n\n"
                "• `/tod` - Interactive Truth or Dare game\n"
                "• `/rps [rock/paper/scissors]` - Play against the AI\n"
                "• `/confess [secret]` - Start an anonymous voting poll\n"
                "• `/roast` - AI will roast you (based on your memory!)"
            ),
            'help_all': "Check the /start command for the complete quick-reference list!"
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
        reply = await self.ai_reply(prompt, memory)
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
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        file = await update.message.voice.get_file()
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        text = await self.transcribe_audio(buf.getvalue(), "voice.oga")
        await update.message.reply_text(f"🗣️ **Heard:** {text}", parse_mode=ParseMode.MARKDOWN)
        reply = await self.ai_reply(text, self.load_memory(update.effective_user.id))
        await update.message.reply_text(reply)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        text = update.message.text
        chat_id = update.effective_chat.id

        # 1. AUTO-DOWNLOADER
        url_match = re.search(r'(https?://[^\s]+)', text)
        if url_match:
            url = url_match.group(1)
            if any(x in url for x in ["instagram.com", "youtube.com", "youtu.be", "shorts"]):
                return await self.auto_download(url, update)
        
        # 1. AUTO CALCULATOR CHECK
        if re.match(r'^\s*\d+[\s\+\-\*\/\(\)\.xX]+\d+\s*$', text):
            try:
                clean_expr = text.replace('x', '*').replace('X', '*')
                if re.match(r'^[\d\+\-\*\/\(\)\.\s]+$', clean_expr):
                    result = eval(clean_expr, {"__builtins__": {}})
                    await update.message.reply_text(f"🔢 `{text.strip()} = {result}`", parse_mode=ParseMode.MARKDOWN)
                    return 
            except: pass

        # 2. Chat Buffer
        if update.effective_chat.type in ['group', 'supergroup']:
            if chat_id not in self.chat_buffers: self.chat_buffers[chat_id] = []
            self.chat_buffers[chat_id].append(f"{user.first_name}: {text}")
            if len(self.chat_buffers[chat_id]) > self.BUFFER_SIZE: self.chat_buffers[chat_id].pop(0)

        # 3. Memory Save
        match = re.search(r'remember\s+this\s*:\s*(.+)', text, re.IGNORECASE)
        if match:
            self.save_memory(user.id, match.group(1).strip())
            await update.message.reply_text("🧠 Saved.")
            return

        # 4. TRIGGER LOGIC: 'hero', Reply to Bot, Private Chat, or Mention
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
            reply = await self.ai_reply(clean_text, self.load_memory(user.id))
            await update.message.reply_text(reply)

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
        if isinstance(r, bytes): await u.message.reply_photo(r, caption=d+'\n ~MAYANK')
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
    
    app.add_error_handler(hero.error)
    logger.info("HERO is ONLINE")
    app.run_polling()

if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    main()
