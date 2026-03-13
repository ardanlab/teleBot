import os
import threading
import time
import asyncio
import queue
from datetime import datetime
import logging

import streamlit as st
from dotenv import load_dotenv

import telegram
from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("telebot")

# Versi lib (diagnostik)
ST_VERSION = st.__version__
PTB_VERSION = getattr(telegram, "__version__", "unknown")

# ---------------------------
# Load secrets
# ---------------------------
TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", None)
ALLOWED_CHAT_ID = st.secrets.get("ALLOWED_CHAT_ID", None)

if TELEGRAM_TOKEN is None:
    load_dotenv()
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if ALLOWED_CHAT_ID is None:
    ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID")

# Normalisasi tipe
if isinstance(ALLOWED_CHAT_ID, str) and ALLOWED_CHAT_ID.strip().isdigit():
    ALLOWED_CHAT_ID = int(ALLOWED_CHAT_ID)
elif isinstance(ALLOWED_CHAT_ID, (int, type(None))):
    pass
else:
    ALLOWED_CHAT_ID = None

# ---------------------------
# Streamlit setup & state
# ---------------------------
st.set_page_config(page_title="Telegram Bot x Streamlit", page_icon="🤖")

if "bot_started" not in st.session_state:
    st.session_state.bot_started = False
if "bot_error" not in st.session_state:
    st.session_state.bot_error = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "msg_lock" not in st.session_state:
    st.session_state.msg_lock = threading.Lock()
if "incoming_queue" not in st.session_state:
    st.session_state.incoming_queue = queue.Queue()

# ---------------------------
# Global holder (aman diakses dari thread)
# ---------------------------
APP_REF = {"app": None}                 # Application instance
APP_READY = threading.Event()           # Diset saat app berhasil dibuat
STOP_REQUESTED = threading.Event()      # Untuk stop manual

# ---------------------------
# Utils
# ---------------------------
def allowed(chat_id: int) -> bool:
    if ALLOWED_CHAT_ID is None:
        return True
    return chat_id == ALLOWED_CHAT_ID

def enqueue_incoming(chat_id: int, name: str, text: str):
    try:
        st.session_state.incoming_queue.put({
            "ts": time.time(),
            "chat_id": chat_id,
            "name": name or str(chat_id),
            "text": text or "",
            "direction": "in",
        })
    except Exception as e:
        logger.exception("Gagal enqueue incoming: %s", e)

def add_outgoing(chat_id: int, name: str, text: str):
    with st.session_state.msg_lock:
        st.session_state.messages.append({
            "ts": time.time(),
            "chat_id": chat_id,
            "name": name or str(chat_id),
            "text": text or "",
            "direction": "out",
        })

def drain_queue_to_log() -> int:
    moved = 0
    while not st.session_state.incoming_queue.empty():
        try:
            evt = st.session_state.incoming_queue.get_nowait()
        except queue.Empty:
            break
        with st.session_state.msg_lock:
            st.session_state.messages.append(evt)
        moved += 1
    return moved

async def test_token(token: str) -> str:
    bot = Bot(token=token)
    me = await bot.get_me()
    return f"@{me.username}" if me.username else str(me.id)

# ---------------------------
# Telegram Handlers
# ---------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat and allowed(chat.id):
        enqueue_incoming(chat.id, user.full_name if user else str(chat.id), "/start")
        await update.message.reply_text("Halo! Bot terhubung ke Streamlit ✅")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat and allowed(chat.id):
        enqueue_incoming(chat.id, user.full_name if user else str(chat.id), "/help")
        await update.message.reply_text("/start - cek bot\n/help - bantuan")

async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not allowed(chat.id):
        return
    text = update.message.text or ""
    enqueue_incoming(chat.id, user.full_name if user else str(chat.id), text)
    await update.message.reply_text(
        f"Kamu bilang:\n\n<code>{text}</code>", parse_mode=ParseMode.HTML
    )

async def nontext_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not allowed(chat.id):
        return
    kind = "non-text"
    msg = update.message
    if msg:
        if msg.photo:
            kind = "photo"
        elif msg.sticker:
            kind = "sticker"
        elif msg.document:
            kind = "document"
        elif msg.audio:
            kind = "audio"
        elif msg.video:
            kind = "video"
    enqueue_incoming(chat.id, user.full_name if user else str(chat.id), f"[{kind} message]")
    try:
        await update.message.reply_text(f"Diterima {kind} 👍")
    except Exception:
        pass

# ---------------------------
# Bot thread
# ---------------------------
def run_bot_polling():
    """Bangun Application dan jalankan polling (di thread terpisah)."""
    try:
        STOP_REQUESTED.clear()
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        # simpan ke global holder (bukan session_state)
        APP_REF["app"] = app
        APP_READY.set()

        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))
        app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, nontext_msg))

        # Jalan blocking; stop_signals=None agar tidak mengganggu Streamlit
        app.run_polling(stop_signals=None)
    except Exception as e:
        logger.exception("Polling crash: %s", e)
        st.session_state.bot_error = repr(e)
        APP_REF["app"] = None
        APP_READY.clear()

# ---------------------------
# UI
# ---------------------------
st.title("Telegram Bot x Streamlit 🤖")

with st.expander("Diagnostik & Konfigurasi", expanded=False):
    st.write(f"- **Streamlit**: `{ST_VERSION}`")
    st.write(f"- **python-telegram-bot**: `{PTB_VERSION}`")
    st.write(
        "- **Mode**: Polling (cocok untuk prototyping & Streamlit Cloud)\n"
        "- **ALLOWED_CHAT_ID**: "
        + (str(ALLOWED_CHAT_ID) if ALLOWED_CHAT_ID else "tidak dibatasi (hati-hati untuk bot publik)")
    )

# Validasi token
if not TELEGRAM_TOKEN:
    st.error(
        "TELEGRAM_TOKEN belum di-set.\n\n"
        "Set di: Streamlit Cloud → App Settings → Secrets, atau buat file .env (untuk lokal)."
    )
    st.stop()

# Test token
colx1, colx2 = st.columns(2)
with colx1:
    if st.button("🔎 Tes Koneksi (getMe)"):
        async def _t():
            return await test_token(TELEGRAM_TOKEN)
        try:
            uname = asyncio.run(_t())
            st.success(f"Token valid. Bot: {uname}")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            uname = loop.run_until_complete(test_token(TELEGRAM_TOKEN))
            st.success(f"Token valid. Bot: {uname}")
        except Exception as e:
            st.error(f"Gagal getMe: {e}")

with colx2:
    if st.session_state.bot_error:
        st.warning(f"Kesalahan terakhir: {st.session_state.bot_error}")

# Kontrol Start / Stop
col1, col2 = st.columns(2)
with col1:
    if not st.session_state.bot_started:
        if st.button("▶️ Start Bot"):
            # reset flag siap
            st.session_state.bot_error = None
            APP_READY.clear()
            APP_REF["app"] = None

            t = threading.Thread(target=run_bot_polling, daemon=True)
            t.start()

            # Tunggu sampai app siap (maks 3 detik)
            waited = 0
            while waited < 30:  # 30 x 0.1s = 3 detik
                if APP_READY.is_set() and APP_REF["app"] is not None:
                    break
                time.sleep(0.1)
                waited += 1

            if not APP_READY.is_set() or APP_REF["app"] is None:
                st.error("Gagal memulai bot (app tidak terinisialisasi). Cek token & jaringan/log.")
            else:
                st.session_state.bot_started = True
                st.success("Bot dimulai (polling).")
    else:
        st.info("Bot sedang berjalan (polling).")

with col2:
    if st.session_state.bot_started:
        if st.button("⏹ Stop Bot"):
            try:
                app = APP_REF.get("app")
                if app is not None:
                    app.stop()
                st.session_state.bot_started = False
                APP_REF["app"] = None
                APP_READY.clear()
                st.success("Bot dihentikan.")
            except Exception as e:
                st.error(f"Gagal stop: {e}")
        else:
            st.caption("Gunakan tombol ini untuk stop.")

st.divider()

# ===========================
# Live Chat Log (Telegram -> Streamlit)
# ===========================
st.subheader("Live Chat Log")

# Autorefresh
_autorefresh_enabled = False
try:
    st.autorefresh(interval=2000, key="chat_refresh")  # 2 detik
    _autorefresh_enabled = True
except Exception:
    pass

if not _autorefresh_enabled:
    if "last_refresh" not in st.session_state:
        st.session_state.last_refresh = time.time()
    enable_fallback = st.checkbox("Auto-refresh (fallback) tiap 2 detik", value=False)
    if enable_fallback and (time.time() - st.session_state.last_refresh > 2):
        st.session_state.last_refresh = time.time()
        st.rerun()

# Pindahkan pesan dari queue ke log
new_count = drain_queue_to_log()

cols = st.columns([1, 1, 3])
with cols[0]:
    if st.button("🧹 Clear Log"):
        st.session_state.messages.clear()
with cols[1]:
    st.write(f"Baru masuk: **{new_count}**")

# Render pesan (maks 200 terakhir)
MAX_SHOW = 200
msgs = st.session_state.messages[-MAX_SHOW:]

for m in msgs:
    role = "user" if m["direction"] == "in" else "assistant"
    header = f"{m['name']} (ID: {m['chat_id']}) • {datetime.fromtimestamp(m['ts']).strftime('%H:%M:%S')}"
    avatar = "👤" if m["direction"] == "in" else "🤖"
    with st.chat_message(role, avatar=avatar):
        st.markdown(f"**{header}**\n\n{m['text']}")

st.caption("Log saat ini in-memory. Untuk histori permanen, simpan ke DB (SQLite/Firestore).")

st.divider()

# ===========================
# Kirim / Broadcast ke Telegram
# ===========================
st.subheader("Kirim / Broadcast ke Telegram")

colA, colB = st.columns(2)
with colA:
    default_chat = str(ALLOWED_CHAT_ID) if ALLOWED_CHAT_ID else ""
    target_chat_ids_raw = st.text_area(
        "Chat ID tujuan (boleh multiple, pisahkan koma/enter). Kosongkan untuk ALLOWED_CHAT_ID.",
        value=default_chat,
        height=90,
        help="Gunakan @userinfobot untuk mengetahui chat ID. Contoh: 11111,22222"
    )
with colB:
    msg = st.text_area("Pesan", placeholder="Tulis pesan...", height=90)

def parse_chat_ids(raw: str):
    parts = [p.strip() for p in (raw or "").replace("\n", ",").split(",")]
    ids = []
    for p in parts:
        if p.isdigit():
            ids.append(int(p))
    if not ids and ALLOWED_CHAT_ID:
        ids = [ALLOWED_CHAT_ID]
    return sorted(list(set(ids)))

if st.button("📤 Kirim / Broadcast"):
    app = APP_REF.get("app")
    if app is None:
        st.warning("Bot belum berjalan.")
    else:
        chat_ids = parse_chat_ids(target_chat_ids_raw)
        if not chat_ids:
            st.error("Chat ID tidak valid dan ALLOWED_CHAT_ID tidak diset.")
        elif not (msg or "").strip():
            st.error("Pesan kosong.")
        else:
            async def _broadcast():
                tasks = [app.bot.send_message(chat_id=cid, text=msg) for cid in chat_ids]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                sent = 0
                for cid, res in zip(chat_ids, results):
                    if isinstance(res, Exception):
                        logger.warning("Gagal kirim ke %s: %s", cid, res)
                        continue
                    add_outgoing(cid, "Me", msg)
                    sent += 1
                return sent

            try:
                count = asyncio.run(_broadcast())
                st.success(f"Pesan terkirim ke {count}/{len(chat_ids)} chat ✅")
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                count = loop.run_until_complete(_broadcast())
                st.success(f"Pesan terkirim ke {count}/{len(chat_ids)} chat ✅")
