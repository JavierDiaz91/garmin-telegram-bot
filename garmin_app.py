import os
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ==========================================
# 1. SILENCIAR LOGS SENSIBLES (HTTPX)
# ==========================================
# Evita que httpx imprima las URLs completas con el Token de Telegram en Render
logging.getLogger("httpx").setLevel(logging.WARNING)

# Configuración básica del logging para ver mensajes útiles del bot
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ==========================================
# 2. SERVIDOR WEB PARA HEALTH CHECK (RENDER FREE)
# ==========================================
# Flask responderá a las peticiones de Render para evitar el error 'No open ports detected'
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot de Telegram activo y corriendo correctamente.", 200

def run_web_server():
    # Render asigna automáticamente un puerto mediante la variable de entorno PORT
    port = int(os.environ.get("PORT", 8080))
    server.run(host="0.0.0.0", port=port)

# ==========================================
# 3. HANDLERS Y LÓGICA DE TU BOT DE TELEGRAM
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Respuesta al comando /start"""
    await update.message.reply_text("¡Hola! Tu bot de Garmin está activo y funcionando en Render.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Respuesta al comando /help"""
    await update.message.reply_text("Usa los comandos configurados para interactuar con tus datos de Garmin.")

# Aquí puedes agregar tus funciones adicionales (entrenamientos, llamadas a la API de Intervals, etc.)

# ==========================================
# 4. PUNTO DE ENTRADA PRINCIPAL
# ==========================================
if __name__ == "__main__":
    # Obtener el Token de Telegram desde las Variables de Entorno de Render
    BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")

    if not BOT_TOKEN:
        raise ValueError("Error: No se encontró la variable de entorno BOT_TOKEN o TELEGRAM_BOT_TOKEN en Render.")

    # A) Iniciar el servidor web de Flask en un hilo secundario (Background Thread)
    flask_thread = threading.Thread(target=run_web_server, daemon=True)
    flask_thread.start()
    logging.info("Servidor Web iniciado para responder a los Health Checks de Render.")

    # B) Configurar e iniciar el Bot de Telegram (Polling)
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Registro de Handlers/Comandos
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))

    # Iniciar la escucha continua de mensajes
    logging.info("Iniciando Polling del Bot de Telegram...")
    application.run_polling()