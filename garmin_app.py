import os
import logging
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# ----------------------------------------------------------------------
# 1. SEGURIDAD Y LOGS (Oculta tokens en logs)
# ----------------------------------------------------------------------
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ----------------------------------------------------------------------
# 2. VARIABLES DE ENTORNO
# ----------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
INTERVALS_API_KEY = os.getenv("INTERVALS_API_KEY")
ATHLETE_ID = os.getenv("ATHLETE_ID")
CHAT_ID = os.getenv("CHAT_ID")

# ----------------------------------------------------------------------
# 3. SALUD DEL SERVICIO WEB (Flask)
# ----------------------------------------------------------------------
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot de Garmin/Intervals activo.", 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server.run(host="0.0.0.0", port=port)

# ----------------------------------------------------------------------
# 4. MENÚ DE BOTONES Y HANDLERS DEL BOT
# ----------------------------------------------------------------------
def main_menu_keyboard():
    """Genera el teclado con los botones principales"""
    keyboard = [
        [
            InlineKeyboardButton("📊 Último Entreno", callback_data="ultimo_entreno"),
            InlineKeyboardButton("📈 Resumen Semanal", callback_data="resumen_semanal")
        ],
        [
            InlineKeyboardButton("🏃 Analizar Ratios", callback_data="analizar_ratios"),
            InlineKeyboardButton("⚙️ Estado API", callback_data="estado_api")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start con menú desplegable"""
    texto = (
        "¡Hola! 👋 BIenvenido a **Javi-Analisis-Entreno**.\n\n"
        "Selecciona una opción del menú para consultar tus métricas de entrenamiento:"
    )
    await update.message.reply_text(
        texto,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los clics en los botones inline"""
    query = update.callback_query
    await query.answer()

    if query.data == "ultimo_entreno":
        # Aquí conectas tu función que consulta el último entrenamiento de Intervals
        await query.edit_message_text(
            "🔎 *Obteniendo datos del último entrenamiento...*\n\n"
            "(Aquí se procesan los datos recuperados con tu API Key)",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    elif query.data == "resumen_semanal":
        await query.edit_message_text(
            "📈 *Resumen Semanal:*\n\nPróximamente métricas acumuladas.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    elif query.data == "estado_api":
        estado = "✅ Configurada" if INTERVALS_API_KEY else "❌ No configurada"
        await query.edit_message_text(
            f"⚙️ **Estado del Bot:**\n• API Key Intervals: {estado}\n• ID Atleta: {ATHLETE_ID or 'No configurado'}",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

# ----------------------------------------------------------------------
# 5. INICIALIZACIÓN Y EJECUCIÓN
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("Error: BOT_TOKEN no disponible en las variables de entorno.")

    # A) Iniciar Flask en hilo secundario
    threading.Thread(target=run_web_server, daemon=True).start()

    # B) Configurar Bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Bot de Telegram iniciado correctamente.")
    app.run_polling()