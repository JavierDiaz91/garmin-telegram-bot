import os
import logging
import threading
import requests
from datetime import date
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ----------------------------------------------------------------------
# 1. LOGS Y SEGURIDAD
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

# ----------------------------------------------------------------------
# 3. SERVIDOR WEBSERVER FLASK (Render Free Healthcheck)
# ----------------------------------------------------------------------
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot de Garmin/Intervals activo.", 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server.run(host="0.0.0.0", port=port)

# ----------------------------------------------------------------------
# 4. FUNCIÓN PARA CONSULTAR ACTIVIDAD DE HOY EN INTERVALS / GARMIN
# ----------------------------------------------------------------------
def obtener_entrenamiento_hoy():
    if not INTERVALS_API_KEY or not ATHLETE_ID:
        return "⚠️ *Error:* Faltan configurar las variables `INTERVALS_API_KEY` o `ATHLETE_ID` en Render."

    today_str = date.today().isoformat()  # Formato YYYY-MM-DD
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events?oldest={today_str}&newest={today_str}"
    
    # La API de Intervals usa HTTP Basic Auth con el usuario 'API_KEY' y la contraseña la clave
    try:
        response = requests.get(url, auth=('API_KEY', INTERVALS_API_KEY), timeout=10)
        
        if response.status_code != 200:
            return f"❌ Error al conectar con Intervals.icu (Código {response.status_code})."
        
        eventos = response.json()
        # Filtramos solo las actividades realizadas de hoy
        actividades = [e for e in eventos if e.get("type") in ["Run", "VirtualRun", "Ride", "Walk"] or e.get("moving_time")]

        if not actividades:
            return f"📅 *Fecha:* {today_str}\n\n🏃 No hay actividades ni entrenamientos registrados para el día de hoy en Garmin/Intervals."

        act = actividades[0] # Tomamos la primera actividad del día

        # Extraer métricas desde el objeto devuelto por Garmin -> Intervals
        nombre = act.get("name", "Entrenamiento")
        distancia = round(act.get("distance", 0) / 1000, 2)  # metros a km
        
        moving_time = act.get("moving_time", 0)
        m, s = divmod(moving_time, 60)
        h, m = divmod(m, 60)
        tiempo_mov_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

        # Ritmo en min/km
        velocity = act.get("average_speed", 0) # m/s
        if velocity > 0:
            pace_sec = 1000 / velocity
            pace_m, pace_s = divmod(int(pace_sec), 60)
            ritmo_str = f"{pace_m}:{pace_s:02d}"
        else:
            ritmo_str = "N/A"

        fc_avg = act.get("average_heartrate", "N/A")
        fc_max = act.get("max_heartrate", "N/A")
        cadence = act.get("average_cadence", "N/A")
        if cadence != "N/A":
            cadence = int(cadence * 2) # Ajuste si la API devuelve SPM por pierna

        calorias = act.get("calories", "N/A")
        desnivel_pos = act.get("total_elevation_gain", 0)

        # Formatear el reporte de la sesión
        reporte = (
            f"🏃 *RESUMEN DE SESIÓN: {nombre.upper()}*\n"
            f"📅 Fecha: {today_str}\n\n"
            f"📏 *TIEMPOS Y RITMOS*\n"
            f"• Distancia: *{distancia} km*\n"
            f"• Tiempo en Movimiento: *{tiempo_mov_str} min*\n"
            f"• Ritmo Medio en Movimiento: *{ritmo_str} /km*\n"
            f"💡 _El ritmo en movimiento descarta pausas para reflejar tu velocidad real._\n\n"
            f"🫀 *MÉTRICAS CARDIACAS Y TÉCNICA*\n"
            f"• FC Media: *{fc_avg} ppm* | FC Máx: *{fc_max} ppm*\n"
            f"• Cadencia Media: *{cadence} ppm*\n"
            f"💡 _Mantener cadencias sobre 160 ppm ayuda a optimizar impacto y pisada._\n\n"
            f"⛰️ *DESCANSO Y ESFUERZO*\n"
            f"• Desnivel Positivo: *+{int(desnivel_pos)} m*\n"
            f"• Calorías Consumidas: *{calorias} kcal*\n"
        )
        return reporte

    except Exception as e:
        logging.error(f"Error al obtener datos: {e}")
        return "⚠️ Ocurrió un error al procesar la información del entrenamiento."

# ----------------------------------------------------------------------
# 5. MENÚ Y BOTONES
# ----------------------------------------------------------------------
def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("📊 Diagnóstico Completo", callback_data="diagnostico")
        ],
        [
            InlineKeyboardButton("🏃 Entrenamiento Hoy", callback_data="entrenamiento_hoy")
        ],
        [
            InlineKeyboardButton("📈 Carga (CTL/ATL)", callback_data="carga"),
            InlineKeyboardButton("🫀 Salud y Sueño", callback_data="salud_sueno")
        ],
        [
            InlineKeyboardButton("🏠 Menú Principal", callback_data="menu_principal")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "👋 **¡Hola Javier! Soy tu bot de rendimiento deportivo.**\n\n"
        "Selecciona una opción del menú para consultar tus métricas o entrenamiento en [Intervals.icu](https://intervals.icu):"
    )
    await update.message.reply_text(
        texto,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "entrenamiento_hoy":
        await query.edit_message_text("🔎 *Consultando Garmin / Intervals.icu...*", parse_mode="Markdown")
        
        # Obtenemos los datos dinámicos reales
        resultado = obtener_entrenamiento_hoy()
        
        await query.edit_message_text(
            resultado,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    
    elif query.data == "menu_principal":
        texto = (
            "👋 **¡Hola Javier! Soy tu bot de rendimiento deportivo.**\n\n"
            "Selecciona una opción del menú para consultar tus métricas o entrenamiento en [Intervals.icu](https://intervals.icu):"
        )
        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
            disable_web_page_preview=True
        )

# ----------------------------------------------------------------------
# 6. INICIALIZACIÓN
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("Error: Falta BOT_TOKEN en las variables de entorno.")

    threading.Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Bot en ejecución...")
    app.run_polling()