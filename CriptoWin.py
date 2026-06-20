import os
import logging
from decimal import Decimal

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

# Puntos por referido (NUMERIC en BD; usar Decimal para no perder fracciones)
PUNTOS_POR_REFERIDO = Decimal("0.01")

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

# --- 2. Funciones de Base de Datos (SQL) ---

def init_db():
    """Crea tablas y aplica migraciones ligeras (puntos decimales, anti-abuso referidos)."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id BIGINT PRIMARY KEY,
            puntos NUMERIC(20, 8) DEFAULT 0 NOT NULL,
            referido_por BIGINT,
            recompensa_referido_pagada BOOLEAN DEFAULT FALSE NOT NULL
        )
    ''')
    cur.execute('''
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'usuarios'
                  AND column_name = 'puntos' AND data_type = 'integer'
            ) THEN
                ALTER TABLE usuarios
                    ALTER COLUMN puntos TYPE NUMERIC(20, 8) USING puntos::numeric;
            END IF;
        END $$;
    ''')
    cur.execute('''
        ALTER TABLE usuarios
        ADD COLUMN IF NOT EXISTS recompensa_referido_pagada BOOLEAN DEFAULT FALSE NOT NULL
    ''')
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
    cur.execute('''
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY
        )
    ''')
    cur.execute(
        "SELECT 1 FROM schema_migrations WHERE name = %s",
        ("backfill_recompensa_referido_v1",),
    )
    if cur.fetchone() is None:
        cur.execute(
            """
            UPDATE usuarios
            SET recompensa_referido_pagada = TRUE
            WHERE referido_por IS NOT NULL
            """
        )
        cur.execute(
            "INSERT INTO schema_migrations (name) VALUES (%s)",
            ("backfill_recompensa_referido_v1",),
        )
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
        if result and result[0] is not None:
            return result[0]
        return Decimal("0")
    except Exception as e:
        logging.error(f"Error al obtener puntos: {e}")
        return Decimal("0")


def format_doge_display(amount) -> str:
    """Muestra cantidades legibles (evita 0E-8 u otra notación científica de Decimal/NUMERIC)."""
    if amount is None:
        return "0"
    try:
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except Exception:
        return str(amount)
    text = f"{d:.8f}".rstrip("0").rstrip(".")
    return text if text else "0"


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


def _grant_referral_reward_tx(referred_user_id: int):
    """
    Si el usuario fue referido, aún no se pagó recompensa y el referidor no superó el límite 24h,
    suma PUNTOS_POR_REFERIDO al referidor y marca como pagada. Devuelve el referidor pagado o None.
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """
            SELECT referido_por, recompensa_referido_pagada
            FROM usuarios
            WHERE user_id = %s
            FOR UPDATE
            """,
            (referred_user_id,),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return None
        referrer_id, ya_pagada = row
        if referrer_id is None or ya_pagada:
            conn.rollback()
            return None

        cur.execute(
            """
            SELECT COUNT(*) FROM referral_rewards_log
            WHERE referrer_id = %s AND rewarded_at > NOW() - INTERVAL '24 hours'
            """,
            (referrer_id,),
        )
        if cur.fetchone()[0] >= MAX_REFERRALS_PER_24H:
            conn.rollback()
            logging.warning(
                "Referido omitido por límite 24h: referidor=%s referido=%s",
                referrer_id,
                referred_user_id,
            )
            return "rate_limited"

        cur.execute(
            "UPDATE usuarios SET puntos = puntos + %s WHERE user_id = %s",
            (PUNTOS_POR_REFERIDO, referrer_id),
        )
        cur.execute(
            "UPDATE usuarios SET recompensa_referido_pagada = TRUE WHERE user_id = %s",
            (referred_user_id,),
        )
        cur.execute(
            """
            INSERT INTO referral_rewards_log (referrer_id, referred_id)
            VALUES (%s, %s)
            """,
            (referrer_id, referred_user_id),
        )
        conn.commit()
        return referrer_id
    except psycopg2.IntegrityError as exc:
        conn.rollback()
        if getattr(exc, "pgcode", None) == "23505":
            return None
        logging.error(f"Error de integridad al otorgar referido: {exc}")
        return None
    except Exception as e:
        conn.rollback()
        logging.error(f"Error al otorgar recompensa de referido: {e}")
        return None
    finally:
        conn.close()


async def try_grant_referral_after_subscription(
    referred_user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    is_subscribed: bool,
) -> None:
    """Solo paga referido si está en el canal (evita cuentas fantasma que nunca se unen)."""
    if not is_subscribed:
        return
    result = _grant_referral_reward_tx(referred_user_id)
    if result == "rate_limited":
        return
    if isinstance(result, int):
        try:
            await context.bot.send_message(
                chat_id=result,
                text=(
                    f"🔥 ¡Un referido se unió al canal y validó tu invitación! "
                    f"Ganaste {format_doge_display(PUNTOS_POR_REFERIDO)} DOGE."
                ),
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

# --- 6. Handler de Botones ---

async def handle_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text_received = update.message.text
    user_id = update.effective_user.id
    
    is_member = await check_subscription(user_id, context)
    if not is_member:
        await start_command(update, context)
        return

    await try_grant_referral_after_subscription(user_id, context, True)

    if text_received in BOT_LINKS:
        response_text = f"Has seleccionado <b>{text_received}</b>. Aquí tienes los enlaces 👇"
        reply_markup = create_inline_keyboard(BOT_LINKS[text_received])

    elif text_received == "💰 Balance":
        puntos = get_user_points(user_id)
        response_text = (
            f"🎁 <b>Balance Actual</b>\n"
            f"Tienes: <b>{format_doge_display(puntos)}</b> DOGE.\n\n"
            f"Puedes retirar tus DOGE aqui.\n(Minimo de retiro = 0.1 DOGE)."
        )
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Solicitar retiro al Admin", callback_data="solicitar_canje")]])
        
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

# --- 7. Handler para Notificar Canje (Admin) ---

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    
    if query.data == "solicitar_canje":
        if await check_subscription(user.id, context):
            await try_grant_referral_after_subscription(user.id, context, True)
        puntos = get_user_points(user.id)
        if ADMIN_ID != 0:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🚨 <b>SOLICITUD DE RETIRO</b>\n\nUsuario: {user.first_name} (@{user.username})\nID: {user.id}\nSaldo: {format_doge_display(puntos)} DOGE.",
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
