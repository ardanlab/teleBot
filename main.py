import os
import threading
import time
import asyncio
import queue
from datetime import datetime

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
st.set_page_config(page_title="Telegram Bot x Streamlit", page_icon="🤖")

if "bot_started" not in st.session_state:
    st.session_state.bot_started = False
if "bot_thread" not in st.session_state:
    st.session_state.bot_thread = None
if "app" not in st.session_state:
    st.session_state.app = None

# Queue untuk komunikasi thread-safe dari handler (thread bot) ke UI Streamlit
if "incoming_queue" not in st.session_state:
    st.session_state.incoming_queue = queue.Queue()

# List log pesan (in/out). Setiap item: dict {ts, chat_id, name, text, direction}
if "messages" not in st.session_state:
    st.session_state.messages = []

# Lock opsional kalau mau jaga konsistensi saat append (di sini cukup aman karena append sederhana)
if "msg_lock" not in st.session_state:
    st.session_state.msg_lock = threading.Lock()

# ---------------------------
# Util
# ---------------------------
def allowed(chat_id: int) -> bool:
    """Batasi siapa yang boleh interaksi (inbound) jika ALLOWED_CHAT_ID diset."""
    if ALLOWED_CHAT_ID is None:
        return True
    return chat_id == ALLOWED_CHAT_ID

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def enqueue_incoming(chat_id: int, name: str, text: str):
    """Masukkan pesan masuk ke queue (diproses di thread UI)."""
    st.session_state.incoming_queue.put({
        "ts": time.time(),
        "chat_id": chat_id,
        "name": name or str(chat_id),
        "text": text or "",
        "direction": "in",
    })

def add_outgoing(chat_id: int, name: str, text: str):
    """Langsung append pesan keluar ke log (dipanggil dari thread UI setelah kirim)."""
    with st.session_state.msg_lock:
        st.session_state.messages.append({
            "ts": time.time(),
            "chat_id": chat_id,
            "name": name or str(chat_id),
            "text": text or "",
            "direction": "out",
        })

def drain_queue_to_log():
    """Pindahkan semua event dari queue ke log untuk ditampilkan."""
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

async def echo_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not allowed(chat.id):
        return
    text = update.message.text or ""
    # Masukkan pesan inbound ke queue (biar UI Streamlit bisa tampilkan)
    enqueue_incoming(chat.id, user.full_name if user else str(chat.id), text)
    # Balasan contoh (opsional)
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
# UI
# ---------------------------
st.title("Telegram Bot x Streamlit 🤖")

# Validasi token
if not TELEGRAM_TOKEN:
    st.error(
        "TELEGRAM_TOKEN belum di-set.\n\n"
        "Set di: Streamlit Cloud → App Settings → Secrets, atau buat file .env (untuk lokal)."
    )
    st.stop()

with st.expander("Konfigurasi", expanded=False):
    st.write(
        "- **Mode**: Polling (cocok untuk prototyping & Streamlit Cloud)\n"
        "- **ALLOWED_CHAT_ID**: "
        + (str(ALLOWED_CHAT_ID) if ALLOWED_CHAT_ID else "tidak dibatasi (hati-hati di bot publik)")
    )

# Kontrol start/stop bot
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

# ===========================
# Panel Chat: Tampilkan pesan masuk/keluar
# ===========================
st.subheader("Live Chat Log")

# Auto-refresh ringan agar log bergerak saat ada pesan baru
st_autorefresh = st.experimental_rerun  # fallback nama, kita pakai st.autorefresh kalau ada
try:
    # Streamlit >= 1.18 punya st.autorefresh
    from streamlit.runtime.scriptrunner import add_script_run_ctx  # just to ensure runtime available
    _ = st.autorefresh(interval=2000, key="chat_refresh")  # 2 detik
except Exception:
    # Jika tidak ada, kita tidak paksa (user bisa klik rerun manual)
    pass

# Drain queue ke log sebelum render
new_count = drain_queue_to_log()

# Tombol bersihkan log
cols = st.columns([1, 1, 3])
with cols[0]:
    if st.button("🧹 Clear Log"):
        st.session_state.messages.clear()
with cols[1]:
    st.write(f"Baru masuk: **{new_count}**")

# Tampilkan chat dengan gaya chat_message
# Batasi tampilan terakhir N pesan agar ringan
MAX_SHOW = 200
msgs = st.session_state.messages[-MAX_SHOW:]

for m in msgs:
    role = "user" if m["direction"] == "in" else "assistant"
    header = f"{m['name']} (ID: {m['chat_id']}) • {datetime.fromtimestamp(m['ts']).strftime('%H:%M:%S')}"
    avatar = "👤" if m["direction"] == "in" else "🤖"
    with st.chat_message(role, avatar=avatar):
        st.markdown(f"**{header}**\n\n{m['text']}")

st.caption("Catatan: Log ini hanya in-memory. Untuk persist, simpan ke DB (SQLite/Firestore) sesuai kebutuhan.")

st.divider()

# ===========================
# Kirim pesan dari Streamlit (single / broadcast)
# ===========================
st.subheader("Kirim Pesan ke Telegram")

colA, colB = st.columns(2)
with colA:
    default_chat = str(ALLOWED_CHAT_ID) if ALLOWED_CHAT_ID else ""
    target_chat_ids_raw = st.text_area(
        "Chat ID tujuan (boleh multiple, pisahkan koma/enter). Kosongkan untuk ALLOWED_CHAT_ID.",
        value=default_chat,
        height=80,
        help="Gunakan @userinfobot untuk mengetahui chat ID. Contoh: 11111,22222"
    )
with colB:
    msg = st.text_area("Pesan", placeholder="Tulis pesan...", height=80)

def parse_chat_ids(raw: str):
    parts = [p.strip() for p in (raw or "").replace("\n", ",").split(",")]
    ids = []
    for p in parts:
        if p.isdigit():
            ids.append(int(p))
    # fallback ke ALLOWED_CHAT_ID jika kosong
    if not ids and ALLOWED_CHAT_ID:
        ids = [ALLOWED_CHAT_ID]
    # unique
    return sorted(list(set(ids)))

if st.button("📤 Kirim / Broadcast"):
    if not st.session_state.app:
        st.warning("Bot belum berjalan.")
    else:
        chat_ids = parse_chat_ids(target_chat_ids_raw)
        if not chat_ids:
            st.error("Chat ID tidak valid dan ALLOWED_CHAT_ID tidak diset.")
        elif not (msg or "").strip():
            st.error("Pesan kosong.")
        else:
            async def _broadcast():
                tasks = []
                for cid in chat_ids:
                    tasks.append(st.session_state.app.bot.send_message(chat_id=cid, text=msg))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                # Tambahkan ke log untuk setiap pengiriman yang berhasil
                for cid, res in zip(chat_ids, results):
                    if isinstance(res, Exception):
                        # Bisa tampilkan error per chat id kalau mau
                        continue
                    # Kita tidak tahu nama dari Streamlit sisi outbound; simpan sebagai 'Me'
                    add_outgoing(cid, "Me", msg)

            try:
                asyncio.run(_broadcast())
                st.success(f"Pesan terkirim ke {len(chat_ids)} chat ✅")
            except RuntimeError:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(_broadcast())
