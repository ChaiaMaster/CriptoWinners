import os
import logging
import psycopg2
import asyncio
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# --- 1. Configuración Inicial ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0")) 
PORT = int(os.environ.get('PORT', '8080'))
CHANNEL_ID = -1002925650616 
CHANNEL_USERNAME = "finanzas0inversion"
PUNTOS_POR_REFERIDO = 100
PUNTOS_BONO_DIARIO = 20

# --- 2. Servidor de Health Check para Railway ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    # Usamos un puerto distinto o el mismo si Railway lo permite
    # Nota: Si usas Webhooks, el bot ya ocupa el PORT. Railway detecta el puerto abierto.
    server = HTTPServer(('0.0.0.0', PORT + 1), HealthCheckHandler)
    server.serve_forever()

# --- 3. Funciones de Base de Datos ---
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id BIGINT PRIMARY KEY,
            puntos INTEGER DEFAULT 0,
            referido_por BIGINT,
            ultimo_bono TIMESTAMP,
            billetera TEXT
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def get_user_data(user_id):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT puntos, ultimo_bono, billetera FROM usuarios WHERE user_id = %s", (user_id,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    return res if res else (0, None, "No configurada")

def update_wallet(user_id, wallet):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET billetera = %s WHERE user_id = %s", (wallet, user_id))
    conn.commit()
    cur.close()
    conn.close()

# (Otras funciones de DB como register_user y add_points se mantienen igual que en tu archivo anterior)
def register_user(user_id, referrer_id=None):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM usuarios WHERE user_id = %s", (user_id,))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO usuarios (user_id, referido_por) VALUES (%s, %s)", (user_id, referrer_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    cur.close()
    conn.close()
    return False

def add_points(user_id, points):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET puntos = puntos + %s WHERE user_id = %s", (points, user_id))
    conn.commit()
    cur.close()
    conn.close()

# --- 4. Interfaz ---
def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("🐶 DOGEs"), KeyboardButton("💎 TON")],
        [KeyboardButton("🪙 USDT"), KeyboardButton("🌐 WEBs")],
        [KeyboardButton("💰 Balance"), KeyboardButton("👥 Referidos")],
        [KeyboardButton("👤 Soporte")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- 5. Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referrer_id = int(context.args[0]) if context.args and context.args[0].isdigit() else None
    
    if register_user(user_id, referrer_id) and referrer_id and referrer_id != user_id:
        add_points(referrer_id, PUNTOS_POR_REFERIDO)
        try: await context.bot.send_message(chat_id=referrer_id, text=f"🔥 ¡Alguien se unió! Ganaste {PUNTOS_POR_REFERIDO} pts.")
        except: pass

    await update.message.reply_text(f"¡Épale! Bienvenido al bot oficial de CriptoWinners. ✅", reply_markup=get_main_keyboard())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "💰 Balance":
        puntos, _, billetera = get_user_data(user_id)
        fecha = datetime.now().strftime("%d/%m/%Y")
        
        msg = (f"💰 <b>MI BALANCE</b>\n\n"
               f"👤 <b>ID:</b> <code>{user_id}</code>\n"
               f"💵 <b>Saldo:</b> <code>{puntos} puntos</code>\n"
               f"📅 <b>Fecha:</b> {fecha}\n"
               f"👛 <b>Billetera:</b> <code>{billetera if billetera else 'No configurada'}</code>")
        
        btns = [
            [InlineKeyboardButton("🎁 Bono", callback_data="bono_diario"),
             InlineKeyboardButton("👛 Billetera", callback_data="config_wallet")],
            [InlineKeyboardButton("🔄 Cambiar", callback_data="menu_canje")]
        ]
        await update.message.reply_text(msg, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(btns))
    
    # Lógica para capturar la billetera cuando el usuario la escribe
    elif context.user_data.get("esperando_billetera"):
        update_wallet(user_id, text)
        context.user_data["esperando_billetera"] = False
        await update.message.reply_text(f"✅ Billetera guardada: <code>{text}</code>", parse_mode='HTML')

async def callback_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "bono_diario":
        puntos, ultimo_bono, _ = get_user_data(user_id)
        if ultimo_bono and datetime.now() < ultimo_bono + timedelta(hours=24):
            espera = (ultimo_bono + timedelta(hours=24)) - datetime.now()
            horas, rem = divmod(espera.seconds, 3600)
            await query.edit_message_text(f"⏳ Ya reclamaste tu bono. Vuelve en {horas}h {rem//60}m.")
        else:
            add_points(user_id, PUNTOS_BONO_DIARIO)
            conn = psycopg2.connect(DATABASE_URL); cur = conn.cursor()
            cur.execute("UPDATE usuarios SET ultimo_bono = %s WHERE user_id = %s", (datetime.now(), user_id))
            conn.commit(); cur.close(); conn.close()
            await query.edit_message_text(f"🎁 ¡Felicidades! Has recibido {PUNTOS_BONO_DIARIO} puntos gratis.")

    elif query.data == "config_wallet":
        context.user_data["esperando_billetera"] = True
        await query.message.reply_text("✍️ Envía tu dirección de billetera Dogecoin (DOGE) ahora:")

    elif query.data == "menu_canje":
        await query.edit_message_text("🔄 <b>Menú de Retiro/Canje</b>\n\n- Retirar Doges (Mínimo 10.000 pts)\n- Comprar servicios de automatización.\n\nEscribe al soporte para procesar manualmente.", parse_mode='HTML')

# --- 6. Ejecución ---
def main():
    init_db()
    # Iniciar Health Check en segundo plano
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback_logic))

    RAILWAY_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if RAILWAY_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN,
                        webhook_url=f"https://{RAILWAY_URL}/{BOT_TOKEN}")
    else:
        app.run_polling()

if __name__ == '__main__':
    main()
