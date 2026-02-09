import os
import logging
import psycopg2  # Librería para la base de datos
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# --- 1. Configuración Inicial y Constantes ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")  # Railway la detecta automáticamente
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0")) 

PORT = int(os.environ.get('PORT', '8080'))
CHANNEL_ID = -1002925650616 
CHANNEL_USERNAME = "finanzas0inversion"

# Puntos por referido según tu última edición
PUNTOS_POR_REFERIDO = 100

BOT_LINKS = {
    "🐶 DOGEs": [
        ("🤖 Gana en DOGE 🪙", "https://t.me/CryptoDogePayBot?start=user445676"),
        ("🤖 Mineria DOGE ⛏️", "https://t.me/dogecoingeneratorbot?start=14435")
    ],
    "💎 TON": [
        ("🤖 Gana en TON 💎", "https://t.me/OilTycoonTON_bot/game?startapp=ii_273829196")
    ],
    "🪙 USDT": [
        ("🤖 Gana en USDT 💰", "https://t.me/GmailFProBot?start=273829196"),
        ("🤖 Staking USDT 🔐","https://t.me/Mine_Fi_bot/mine?startapp=r_b9m339z3m6tm")
    ],
    "🌐 WEBs": [
        ("🔗 Web mineria en DOGE", "https://t.me/+WNsNDyjmf7PAihoN")
    ]
}

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

def get_user_points(user_id):
    """Obtiene los puntos de un usuario."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT puntos FROM usuarios WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result[0] if result else 0
    except Exception as e:
        logging.error(f"Error al obtener puntos: {e}")
        return 0

def register_user(user_id, referrer_id=None):
    """Registra un nuevo usuario y retorna True si es nuevo."""
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
    """Suma puntos a un usuario existente."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET puntos = puntos + %s WHERE user_id = %s", (points, user_id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error al sumar puntos: {e}")

# --- 3. Funciones de Interfaz ---

def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("🐶 DOGEs"), KeyboardButton("💎 TON")],
        [KeyboardButton("🪙 USDT"), KeyboardButton("🌐 WEBs")],
        [KeyboardButton("🎁 Canje de Puntos"), KeyboardButton("👥 Referidos")],
        [KeyboardButton("👤 Soporte")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def create_inline_keyboard(links: list) -> InlineKeyboardMarkup:
    keyboard = []
    for text, url in links:
        keyboard.append([InlineKeyboardButton(text, url=url)])
    return InlineKeyboardMarkup(keyboard)

# --- 4. Lógica de Suscripción ---

async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"Error suscripción para {user_id}: {e}")
        return False 

# --- 5. Handler /start con REFERIDOS (SQL) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    
    referrer_id = None
    if context.args:
        try:
            potential_referrer = int(context.args[0])
            if potential_referrer != user_id:
                referrer_id = potential_referrer
        except ValueError:
            pass

    # Registro en la base de datos
    es_nuevo = register_user(user_id, referrer_id)
    
    if es_nuevo and referrer_id:
        add_points(referrer_id, PUNTOS_POR_REFERIDO)
        try:
            await context.bot.send_message(
                chat_id=referrer_id, 
                text=f"🔥 ¡Un pana se unió con tu link! Ganaste {PUNTOS_POR_REFERIDO} puntos."
            )
        except Exception:
            pass

    is_member = await check_subscription(user_id, context)
    
    if is_member:
        reply_text = f"¡<b>Épale, {user.first_name}!</b> Bienvenido al menú. ✅\nSelecciona una opción abajo."
        reply_markup = get_main_keyboard()
    else:
        reply_text = (
            f"¡Un momento! 🛑\nDebes unirte a nuestro canal:\n"
            f"👉 <a href='https://t.me/{CHANNEL_USERNAME}'>Únete aquí</a>\n"
            f"Luego envía /start de nuevo."
        )
        reply_markup = None 
        
    await update.message.reply_text(reply_text, parse_mode='HTML', reply_markup=reply_markup)

# --- 6. Handler de Botones ---

async def handle_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text_received = update.message.text
    user_id = update.effective_user.id
    
    is_member = await check_subscription(user_id, context)
    if not is_member:
        await start_command(update, context)
        return
        
    if text_received in BOT_LINKS:
        response_text = f"Has seleccionado <b>{text_received}</b>. Aquí tienes los enlaces 👇"
        reply_markup = create_inline_keyboard(BOT_LINKS[text_received])

    elif text_received == "🎁 Canje de Puntos":
        puntos = get_user_points(user_id)
        response_text = (
            f"🎁 <b>Balance Actual</b>\n"
            f"Tienes: <b>{puntos}</b> puntos.\n\n"
            f"Puedes canjear tus puntos por:\n- Servicios de automatización.\n- Dogecoins. (1000pts = 0.1 DOGE)\n- Acceso a bots exclusivos."
        )
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Solicitar Canje al Admin", callback_data="solicitar_canje")]])
        
    elif text_received == "👥 Referidos":
        referral_link = f"https://t.me/{context.bot.username}?start={user_id}" 
        response_text = (
            f"👥 <b>¡Gana Puntos Invitando!</b>\n\n"
            f"Tu link único:\n<code>{referral_link}</code>\n\n"
            f"¡Ganas {PUNTOS_POR_REFERIDO} puntos por cada referido real! 🚀"
        )
        reply_markup = None

    elif text_received == "👤 Soporte":
        response_text = (
            f"👥 <b>Asistencia y Soporte</b>\n\n"
            f"Comunica tus dudas, inquietudes o fallas en los bots a nuestro equipo de asistencia usando el siguiente bot\n 👇\n\n"
        )
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Asistencia y Soporte", url="https://t.me/asisfinancierobot?start=12345678")]])
    
    else:
        response_text = "Selecciona una opción del teclado."
        reply_markup = None
        
    await update.message.reply_text(response_text, parse_mode='HTML', reply_markup=reply_markup)

# --- 7. Handler para Notificar Canje (Admin) ---

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    
    if query.data == "solicitar_canje":
        puntos = get_user_points(user.id)
        if ADMIN_ID != 0:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🚨 <b>SOLICITUD DE CANJE</b>\n\nUsuario: {user.first_name} (@{user.username})\nID: {user.id}\nSaldo: {puntos} puntos.",
                parse_mode='HTML'
            )
            await query.answer("✅ Solicitud enviada. El admin te contactará.")
        else:
            await query.answer("❌ Error: Admin no configurado.")

# --- 8. Función Principal (Síncrona para evitar errores de loop) ---

def main():
    if not BOT_TOKEN:
        logging.error("❌ TOKEN NO CONFIGURADO")
        return
    
    # Inicializamos la tabla en la base de datos
    init_db()
    
    # Construimos la aplicación de forma síncrona
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Registramos handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_text))
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Lógica de Ejecución
    RAILWAY_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    
    if RAILWAY_URL:
        # MODO PRODUCCIÓN: WEBHOOK
        logging.info(f"🚀 Iniciando Webhook en {RAILWAY_URL} puerto {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"https://{RAILWAY_URL}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES
        )
    else:
        # MODO DESARROLLO: POLLING
        logging.info("🚀 Iniciando Polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() # Llamada directa sin asyncio.run()


