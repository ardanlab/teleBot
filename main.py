import os
import threading
import time
import asyncio

import streamlit as st
from dotenv import load_dotenv

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------------------
# Load secrets (Streamlit or .env)
# ---------------------------
# Prioritaskan st.secrets (saat di Streamlit Cloud)
TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", None)
ALLOWED_CHAT_ID = st.secrets.get("ALLOWED_CHAT_ID", None)

# Fallback ke .env jika dijalankan lokal
if TELEGRAM_TOKEN is None:
    load_dotenv()
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if ALLOWED_CHAT_ID is None:
    ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID")

# Normalisasi tipe
if isinstance(ALLOWED_CHAT_ID, str) and ALLOWED_CHAT_ID.isdigit():
    ALLOWED_CHAT_ID = int(ALLOWED_CHAT_ID)
elif isinstance(ALLOWED_CHAT_ID, (int, type(None))):
    pass
else:
    ALLOWED_CHAT_ID = None

# ---------------------------
# Streamlit state
# ---------------------------
if "bot_started" not in st.session_state:
    st.session_state.bot_started = False

if "bot_thread" not in st.session_state:
    st.session_state.bot_thread = None

if "app" not in st.session_state:
    st.session_state.app = None

# ---------------------------
# Telegram Handlers
# ---------------------------
def allowed(chat_id: int) -> bool:
    if ALLOWED_CHAT_ID is None:
        return True
    return chat_id == ALLOWED_CHAT_ID

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and allowed(update.effective_chat.id):
        await update.message.reply_text("Halo! Bot terhubung ke Streamlit ✅")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and allowed(update.effective_chat.id):
        await update.message.reply_text("/start - cek bot\n/help - bantuan")

async def echo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat = update.effective_chat
    if not chat or not allowed(chat.id):
        return
    text = update.message.text or ""
    await update.message.reply_text(
        f"Kamu bilang:\n\n<code>{text}</code>", parse_mode=ParseMode.HTML
    )

def run_bot_polling():
    """Jalankan bot dengan polling di thread terpisah."""
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    st.session_state.app = app

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_msg))

    # Disable default signal handling karena kita ada di thread
    app.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

# ---------------------------
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="Telegram Bot x Streamlit", page_icon="🤖")
st.title("Telegram Bot x Streamlit 🤖")

# Validasi token
if not TELEGRAM_TOKEN:
    st.error(
        "TELEGRAM_TOKEN belum di-set.\n\n"
        "Set di: Streamlit Cloud → App Settings → Secrets, atau buat file .env (untuk lokal)."
    )
    st.stop()

with st.expander("Konfigurasi (ringkas)"):
    st.write(
        "- **Mode**: Polling (cocok untuk prototyping & Streamlit Cloud)\n"
        "- **ALLOWED_CHAT_ID**: "
        + (str(ALLOWED_CHAT_ID) if ALLOWED_CHAT_ID else "tidak dibatasi (hati-hati di bot publik)")
    )

col1, col2 = st.columns(2)
with col1:
    if not st.session_state.bot_started:
        if st.button("▶️ Start Bot"):
            t = threading.Thread(target=run_bot_polling, daemon=True)
            t.start()
            time.sleep(1.0)  # beri waktu inisialisasi
            st.session_state.bot_thread = t
            st.session_state.bot_started = True
            st.success("Bot dimulai (polling).")
    else:
        st.info("Bot sedang berjalan (polling).")

with col2:
    if st.session_state.bot_started:
        if st.button("⏹ Stop Bot"):
            if st.session_state.app:
                st.session_state.app.stop()
            st.session_state.bot_started = False
            st.success("Bot dihentikan.")
        else:
            st.caption("Gunakan tombol ini untuk stop.")

st.divider()
st.subheader("Kirim Pesan dari Streamlit ke Telegram")

default_chat = str(ALLOWED_CHAT_ID) if ALLOWED_CHAT_ID else ""
target_chat_id = st.text_input(
    "Chat ID tujuan (kosongkan untuk ALLOWED_CHAT_ID)",
    value=default_chat,
    help="Gunakan @userinfobot untuk mengetahui chat ID."
)

msg = st.text_area("Pesan", placeholder="Tulis pesan...")

if st.button("📤 Kirim"):
    if not st.session_state.app:
        st.warning("Bot belum berjalan.")
    else:
        async def _send():
            # Tentukan chat id
            chat_id = None
            txt = (target_chat_id or "").strip()
            if txt.isdigit():
                chat_id = int(txt)
            elif ALLOWED_CHAT_ID:
                chat_id = ALLOWED_CHAT_ID

            if not chat_id:
                st.error("Chat ID tidak valid dan ALLOWED_CHAT_ID tidak diset.")
                return

            if not msg.strip():
                st.error("Pesan kosong.")
                return

            await st.session_state.app.bot.send_message(chat_id=chat_id, text=msg)

        # Jalankan coroutine pengiriman
        try:
            asyncio.run(_send())
            st.success("Pesan terkirim ✅")
        except RuntimeError:
            # Jika event loop sudah jalan (kasus tertentu), gunakan loop yang ada
            loop = asyncio.get_event_loop()
            loop.run_until_complete(_send())

st.caption("Tips: Batasi akses dengan ALLOWED_CHAT_ID agar bot tidak disalahgunakan.")
