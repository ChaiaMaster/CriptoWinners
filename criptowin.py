import os
import logging
import psycopg2 # Librería para la base de datos
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
import asyncio 

# --- 1. Configuración Inicial ---

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL") # Railway la pone sola
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
CHANNEL_ID = -1002925650616 
CHANNEL_USERNAME = "finanzas0inversion"

# --- 2. Funciones de Base de Datos (SQL) ---

def init_db():
    """Crea la tabla de usuarios si no existe."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id BIGINT PRIMARY KEY,
            puntos INTEGER DEFAULT 0,
            referido_por BIGINT
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def get_user(user_id):
    """Obtiene los datos de un usuario."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT puntos, referido_por FROM usuarios WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    if user:
        return {"puntos": user[0], "referido_por": user[1]}
    return None

def register_user(user_id, referrer_id=None):
    """Registra un nuevo usuario."""
    if not get_user(user_id):
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("INSERT INTO usuarios (user_id, referido_por) VALUES (%s, %s)", (user_id, referrer_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    return False

def add_points(user_id, points):
    """Suma puntos a un usuario."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET puntos = puntos + %s WHERE user_id = %s", (points, user_id))
    conn.commit()
    cur.close()
    conn.close()

# --- 3. Handlers del Bot ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    
    # Lógica de Referidos con DB
    if context.args:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user_id:
                # Intentamos registrar al nuevo usuario con su referido
                if register_user(user_id, referrer_id):
                    # Si el registro fue exitoso, le damos puntos al que invitó
                    add_points(referrer_id, 10)
                    await context.bot.send_message(chat_id=referrer_id, text="🔥 ¡Un nuevo referido se unió! +10 puntos.")
        except:
            pass
    
    # Si no entró por referido, lo registramos igual (sin referrer)
    register_user(user_id)

    # Verificación de canal... (Igual que antes)
    is_member = await check_subscription(user_id, context)
    if is_member:
        await update.message.reply_text(f"¡Hola {user.first_name}!", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text("Únete al canal para empezar.", parse_mode='HTML')

async def handle_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    user_id = update.effective_user.id
    
    if text == "🎁 Canje de Puntos":
        data = get_user(user_id)
        puntos = data['puntos'] if data else 0
        await update.message.reply_text(
            f"Tienes: <b>{puntos}</b> puntos.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 Solicitar Canje", callback_data="solicitar_canje")]]),
            parse_mode='HTML'
        )
    # ... Resto de botones igual ...

# --- 4. Motor Principal ---

async def main() -> None:
    # 🚨 IMPORTANTE: Inicializar la base de datos al arrancar
    init_db()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_text))
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Tu lógica de Railway Webhook de siempre...
    RAILWAY_STATIC_URL = os.environ.get("RAILWAY_STATIC_URL")
    if RAILWAY_STATIC_URL:
        # ... (Tu código de webhook aquí)
        pass
    else:
        await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
