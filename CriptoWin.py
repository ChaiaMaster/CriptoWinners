import os
import logging
import time
from decimal import Decimal
from datetime import datetime, timedelta
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

# Puntos/DOGE por acciones
PUNTOS_POR_REFERIDO = Decimal("0.01")
PUNTOS_BONO_DIARIO = Decimal("0.005")  # Ajustable a tu gusto

# Máximo de recompensas por referido que un mismo usuario puede generar en 24 h (anti-abuso)
MAX_REFERRALS_PER_24H = int(os.environ.get("MAX_REFERRALS_PER_24H", "30"))

BOT_LINKS = {
    "🐶 DOGEs": [
        ("🤖 Mineria DOGE ⛏️", "https://t.me/dogecoingeneratorbot?start=14435")
    ],
    "💎 TON": [
        ("🤖 Gana en TON 💎", "https://t.me/OilTycoonTON_bot/game?startapp=ai_273829196"),
        ("🤖 Dulces TON 💎", "https://t.me/dulcecandybot?start=273829196"),
        ("🤖 Mina tus GRAM 💎", "https://t.me/minegramtonbot?start=273829196")
    ],
    "🪙 USDT": [
        ("🤖 Gana en USDT 💰", "https://t.me/GmailFProBot?start=273829196"),
        ("🤖 Wallet USDT 🔐","https://t.me/FaucetWallet_bot?start=KN58XCX4")
    ],
    "🌐 WEBs": [
        ("🔗 Canal De Peliculas", "https://t.me/+WNsNDyjmf7PAihoN")
    ]
}

# --- 2. Funciones de Base de Datos (SQL) con Espera Paciente ---

def init_db():
    """Crea tablas y aplica migraciones de forma segura esperando a la DB en Railway."""
    max_retries = 5
    conn = None
    
    for i in range(max_retries):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            logging.info("✅ Conexión exitosa a PostgreSQL en Railway.")
            break
        except psycopg2.OperationalError:
            logging.warning(f"⚠️ Base de datos no lista. Reintentando conexión ({i+1}/{max_retries})...")
            time.sleep(4)
            
    if not conn:
        raise Exception("❌ No se pudo conectar a la base de datos después de varios intentos.")

    cur = conn.cursor()
    # Tabla principal de usuarios actualizada con soporte para Billeteras y último Bono
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id BIGINT PRIMARY KEY,
            puntos NUMERIC(20, 8) DEFAULT 0 NOT NULL,
            referido_por BIGINT,
            recompensa_referido_pagada BOOLEAN DEFAULT FALSE NOT NULL,
            billetera TEXT DEFAULT 'No configurada',
            ultimo_bono TIMESTAMP WITH TIME ZONE
        )
    ''')
    
    # Agregar columnas de manera segura por si la tabla ya existía de antes
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS billetera TEXT DEFAULT 'No configurada'")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ultimo_bono TIMESTAMP WITH TIME ZONE")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS recompensa_referido_pagada BOOLEAN DEFAULT FALSE NOT NULL")
    
    # Tablas de logs para referidos (Anti-cheat)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS referral_rewards_log (
            id BIGSERIAL PRIMARY KEY,
            referrer_id BIGINT NOT NULL,
            referred_id BIGINT NOT NULL UNIQUE,
            rewarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer_time
        ON referral_rewards_log (referrer_id, rewarded_at)
    ''')
    conn.commit()
    cur.close()
    conn.close()

def get_user_data(user_id):
    """Obtiene toda la información financiera y de control de un usuario."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT puntos, billetera, ultimo_bono FROM usuarios WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        if result:
            return result[0], result[1], result[2]
        return Decimal("0"), "No configurada", None
    except Exception as e:
        logging.error(f"Error al obtener datos: {e}")
        return Decimal("0"), "No configurada", None

def update_user_wallet(user_id, wallet_address):
    """Guarda o actualiza la dirección de retiro del usuario."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET billetera = %s WHERE user_id = %s", (wallet_address, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Error al actualizar billetera: {e}")
        return False

def update_bonus_time(user_id):
    """Registra el cobro del bono asignando el timestamp actual."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET puntos = puntos + %s, ultimo_bono = NOW() WHERE user_id = %s", (PUNTOS_BONO_DIARIO, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Error al procesar bono: {e}")
        return False

def format_doge_display(amount) -> str:
    """Muestra cantidades legibles."""
    if amount is None:
        return "0"
    try:
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except Exception:
        return str(amount)
    text = f"{d:.8f}".rstrip("0").rstrip(".")
    return text if text else "0"

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

def _grant_referral_reward_tx(referred_user_id: int):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SELECT referido_por, recompensa_referido_pagada FROM usuarios WHERE user_id = %s FOR UPDATE", (referred_user_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return None
        referrer_id, ya_pagada = row
        if referrer_id is None or ya_pagada:
            conn.rollback()
            return None

        cur.execute("SELECT COUNT(*) FROM referral_rewards_log WHERE referrer_id = %s AND rewarded_at > NOW() - INTERVAL '24 hours'", (referrer_id,))
        if cur.fetchone()[0] >= MAX_REFERRALS_PER_24H:
            conn.rollback()
            return "rate_limited"

        cur.execute("UPDATE usuarios SET puntos = puntos + %s WHERE user_id = %s", (PUNTOS_POR_REFERIDO, referrer_id))
        cur.execute("UPDATE usuarios SET recompensa_referido_pagada = TRUE WHERE user_id = %s", (referred_user_id,))
        cur.execute("INSERT INTO referral_rewards_log (referrer_id, referred_id) VALUES (%s, %s)", (referrer_id, referred_user_id))
        conn.commit()
        return referrer_id
    except Exception as e:
        conn.rollback()
        logging.error(f"Error en tx referido: {e}")
        return None
    finally:
        conn.close()

async def try_grant_referral_after_subscription(referred_user_id: int, context: ContextTypes.DEFAULT_TYPE, is_subscribed: bool) -> None:
    if not is_subscribed:
        return
    result = _grant_referral_reward_tx(referred_user_id)
    if isinstance(result, int):
        try:
            await context.bot.send_message(
                chat_id=result,
                text=f"🔥 ¡Un referido se unió al canal y validó tu invitación! Ganaste {format_doge_display(PUNTOS_POR_REFERIDO)} DOGE."
            )
        except Exception:
            pass

# --- 3. Funciones de Interfaz ---

def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("🐶 DOGEs"), KeyboardButton("💎 TON")],
        [KeyboardButton("🪙 USDT"), KeyboardButton("🌐 WEBs")],
        [KeyboardButton("💰 Balance"), KeyboardButton("👥 Referidos")],
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

# --- 5. Handlers de Comandos y Texto ---

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

    register_user(user_id, referrer_id)

    is_member = await check_subscription(user_id, context)
    if is_member:
        await try_grant_referral_after_subscription(user_id, context, True)
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

async def handle_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text_received = update.message.text
    user_id = update.effective_user.id
    
    is_member = await check_subscription(user_id, context)
    if not is_member:
        await start_command(update, context)
        return

    await try_grant_referral_after_subscription(user_id, context, True)

    # Captura de dirección de billetera si el usuario la está enviando por texto plano
    if context.user_data.get("esperando_billetera"):
        if text_received.startswith("/") or len(text_received) < 20:
            await update.message.reply_text("❌ Dirección inválida. Envía una billetera Dogecoin válida:")
            return
        update_user_wallet(user_id, text_received)
        context.user_data["esperando_billetera"] = False
        await update.message.reply_text(f"✅ <b>Billetera guardada con éxito:</b>\n<code>{text_received}</code>", parse_mode='HTML')
        return

    if text_received in BOT_LINKS:
        response_text = f"Has seleccionado <b>{text_received}</b>. Aquí tienes los enlaces 👇"
        reply_markup = create_inline_keyboard(BOT_LINKS[text_received])

    elif text_received == "💰 Balance":
        puntos, billetera, _ = get_user_data(user_id)
        fecha_actual = datetime.now().strftime("%d/%m/%Y")
        
        response_text = (
            f"💰 <b>MI BALANCE CRIPTO</b>\n\n"
            f"👤 <b>ID Usuario:</b> <code>{user_id}</code>\n"
            f"💵 <b>Saldo:</b> <code>{format_doge_display(puntos)} DOGE</code>\n"
            f"📅 <b>Fecha Actual:</b> {fecha_actual}\n"
            f"👛 <b>Billetera:</b> <code>{billetera}</code>\n\n"
            f"<i>Usa las opciones de abajo para interactuar con tu saldo.</i>"
        )
        
        # Estructura limpia e interactiva solicitada:
        keyboard_balance = [
            [InlineKeyboardButton("🎁 Bono Gratis", callback_data="btn_bono"),
             InlineKeyboardButton("👛 Config Billetera", callback_data="btn_billetera")],
            [InlineKeyboardButton("🔄 Cambiar / Retirar", callback_data="btn_cambiar")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard_balance)
        
    elif text_received == "👥 Referidos":
        referral_link = f"https://t.me/{context.bot.username}?start={user_id}" 
        response_text = (
            f"👥 <b>¡Gana DOGE invitando amigos!</b>\n\n"
            f"Tu link único:\n<code>{referral_link}</code>\n\n"
            f"¡Ganas {format_doge_display(PUNTOS_POR_REFERIDO)} DOGE cuando tu invitado se une al canal "
            f"y usa el bot! 🚀"
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

# --- 6. Handler de Consultas Inline (Callback Queries) ---

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    user_id = user.id
    
    await query.answer() # Evita el reloj de arena en Telegram

    if query.data == "btn_bono":
        _, _, ultimo_bono = get_user_data(user_id)
        ahora = datetime.now(ultimo_bono.tzinfo) if ultimo_bono else datetime.now()
        
        if ultimo_bono and ahora < ultimo_bono + timedelta(hours=24):
            tiempo_restante = (ultimo_bono + timedelta(hours=24)) - ahora
            horas, rem = divmod(tiempo_restante.seconds, 3600)
            minutos = rem // 60
            await query.message.reply_text(f"⏳ <b>¡Ya reclamaste tu bono diario!</b>\nVuelve en <code>{horas}h {minutos}m</code>.", parse_mode='HTML')
        else:
            update_bonus_time(user_id)
            await query.message.reply_text(f"🎁 <b>¡Bono Diario Reclamado!</b>\nSe han acreditado <code>{format_doge_display(PUNTOS_BONO_DIARIO)} DOGE</code> a tu balance.", parse_mode='HTML')

    elif query.data == "btn_billetera":
        context.user_data["esperando_billetera"] = True
        await query.message.reply_text("✍️ <b>Envía tu dirección de billetera Dogecoin (DOGE) por el chat:</b>", parse_mode='HTML')

    elif query.data == "btn_cambiar":
        puntos, billetera, _ = get_user_data(user_id)
        if puntos < Decimal("0.1"):
            await query.message.reply_text(f"❌ <b>Saldo insuficiente.</b>\nEl mínimo de retiro es de <code>0.1 DOGE</code>. Sigue invitando amigos.", parse_mode='HTML')
        elif billetera == "No configurada":
            await query.message.reply_text("⚠️ Primero debes configurar tu billetera Dogecoin pulsando el botón <b>👛 Config Billetera</b>.", parse_mode='HTML')
        else:
            if ADMIN_ID != 0:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🚨 <b>SOLICITUD DE RETIRO SOLICITADA</b>\n\nUsuario: {user.first_name} (@{user.username})\nID: {user.id}\nSaldo a retirar: {format_doge_display(puntos)} DOGE\nBilletera: <code>{billetera}</code>",
                    parse_mode='HTML'
                )
                await query.message.reply_text("✅ <b>¡Solicitud enviada!</b>\nEl Administrador procesará tu retiro a la brevedad.", parse_mode='HTML')
            else:
                await query.message.reply_text("❌ Error interno: El administrador no está configurado en las variables del servidor.", parse_mode='HTML')

# --- 7. Función Principal ---

def main():
    if not BOT_TOKEN:
        logging.error("❌ TOKEN NO CONFIGURADO")
        return
    
    # Inicializamos la base de datos de manera segura
    init_db()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_text))
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    RAILWAY_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    
    if RAILWAY_URL:
        logging.info(f"🚀 Iniciando Webhook en {RAILWAY_URL} por puerto {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"https://{RAILWAY_URL}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES
        )
    else:
        logging.info("🚀 Iniciando Polling Local...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
