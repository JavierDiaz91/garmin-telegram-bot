import datetime
import zoneinfo
import os
import logging
import threading
import httpx
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ----------------------------------------------------------------------
# 1. LOGS Y CONFIGURACIÓN
# ----------------------------------------------------------------------
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
INTERVALS_API_KEY = os.getenv("INTERVALS_API_KEY")
ATHLETE_ID = os.getenv("ATHLETE_ID")

TZ_AR = zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")

# ----------------------------------------------------------------------
# 2. WEBSERVER FLASK (Render Healthcheck)
# ----------------------------------------------------------------------
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot de Rendimiento Deportivo activo.", 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server.run(host="0.0.0.0", port=port)

# ----------------------------------------------------------------------
# 3. CLIENTE ASÍNCRONO DE INTERVALS.ICU
# ----------------------------------------------------------------------
async def fetch_intervals_data(endpoint: str, params: dict = None):
    """Realiza peticiones asíncronas HTTP a la API de Intervals.icu"""
    if not INTERVALS_API_KEY or not ATHLETE_ID:
        return None, "⚠️ *Error:* Faltan configurar `INTERVALS_API_KEY` o `ATHLETE_ID` en las variables de entorno."

    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/{endpoint}"
    auth = ('API_KEY', INTERVALS_API_KEY)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, auth=auth, params=params)
            if response.status_code == 200:
                return response.json(), None
            return None, f"❌ Error API (Código {response.status_code})"
    except Exception as e:
        logging.error(f"Error consultando {endpoint}: {e}")
        return None, "⚠️ Error de conexión con Intervals.icu."

# ----------------------------------------------------------------------
# 4. LÓGICA DE DIAGNÓSTICO INTEGRADO (PRE & POST WORKOUT)
# ----------------------------------------------------------------------
async def obtener_estado_fisiologico_completo():
    today_str = datetime.datetime.now(TZ_AR).strftime("%Y-%m-%d")

    # 1. Obtener datos de Salud / Bienestar del día (ESTADO PRE-ENTRENO)
    wellness, err_w = await fetch_intervals_data(f"wellness/{today_str}")
    
    # 2. Obtener actividades / planes del día (ESTADO POST-ENTRENO O PLAN)
    events, err_e = await fetch_intervals_data("events", {"oldest": today_str, "newest": today_str})

    if err_w and err_e:
        return "⚠️ No se pudieron obtener los datos de la jornada."

    # --- PROCESAR FISIOLOGÍA PRE-ENTRENO ---
    hrv = wellness.get("hrv") if wellness and wellness.get("hrv") is not None else None
    rhr = wellness.get("restingHR") if wellness and wellness.get("restingHR") is not None else "N/D"
    sleep_sec = wellness.get("sleepSecs", 0) if wellness else 0
    sleep_hours = round(sleep_sec / 3600, 1) if sleep_sec else "N/D"
    
    ctl_raw = wellness.get("ctl") if wellness else None
    atl_raw = wellness.get("atl") if wellness else None

    # Formateo y redondeo de métricas numéricas
    ctl = round(ctl_raw, 1) if isinstance(ctl_raw, (int, float)) else "N/D"
    atl = round(atl_raw, 1) if isinstance(atl_raw, (int, float)) else "N/D"
    tsb = round(ctl - atl, 1) if isinstance(ctl, (int, float)) and isinstance(atl, (int, float)) else "N/D"

    hrv_str = f"{round(hrv, 1)} ms" if isinstance(hrv, (int, float)) else "N/D"

    msg_pre = (
        f"🧘 *ESTADO FISIOLÓGICO Y RECUPERACIÓN (PRE-ENTRENO)*\n"
        f"📅 Fecha: `{today_str}`\n\n"
        f"• *VFC / HRV:* {hrv_str}\n"
        f"• *FC Reposo:* {rhr} ppm\n"
        f"• *Sueño:* {sleep_hours} hs\n"
        f"• *Estado Forma (TSB):* {tsb} (CTL: {ctl} | ATL: {atl})\n"
    )

    # --- PROCESAR SESIÓN / POST-ENTRENO ---
    if not events:
        msg_post = "🏃 *ENTRENAMIENTO:* No hay planes ni actividades registradas hoy."
        return f"{msg_pre}\n---\n\n{msg_post}"

    actividades = [e for e in events if e.get("moving_time") or e.get("distance")]

    if actividades:
        act = actividades[0]
        nombre = act.get("name", "Entrenamiento")
        distancia = round(act.get("distance", 0) / 1000, 2)
        
        # Métricas post-sesión
        icu_training_load = act.get("icu_training_load")
        load_str = round(icu_training_load, 1) if isinstance(icu_training_load, (int, float)) else "N/D"
        
        rpe = act.get("perceived_exertion", "N/D")
        feeling = act.get("feeling", "N/D")
        decoupling = act.get("decoupling")
        
        fc_avg = act.get("average_heartrate", "N/D")
        fc_max = act.get("max_heartrate", "N/D")

        decoupling_str = f"{round(decoupling, 1)}%" if isinstance(decoupling, (int, float)) else "N/D"

        msg_post = (
            f"⚡ *RESPUESTA POST-ENTRENO: {nombre.upper()}*\n\n"
            f"• *Distancia:* {distancia} km\n"
            f"• *Carga de Estrés (TSS/Load):* {load_str}\n"
            f"• *FC Media / Máx:* {fc_avg} / {fc_max} ppm\n"
            f"• *Esfuerzo Percibido (RPE):* {rpe}/10\n"
            f"• *Sensación Subjetiva:* {feeling}/5\n"
            f"• *Desacople Aeróbico:* {decoupling_str}"
        )
    else:
        workout = events[0]
        nombre = workout.get("name", "Planificado")
        msg_post = (
            f"📋 *SESIÓN PLANIFICADA HOY*\n\n"
            f"📌 *Plan:* {nombre}\n"
            f"⚠️ _Esperando datos sincronizados del reloj post-sesión..._"
        )

    return f"{msg_pre}\n---\n\n{msg_post}"

# ----------------------------------------------------------------------
# 5. MENÚ Y MANEJO DE EVENTOS DE TELEGRAM
# ----------------------------------------------------------------------
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Diagnóstico Fisiológico Completo", callback_data="diagnostico_completo")],
        [InlineKeyboardButton("🏃 Entrenamiento Hoy", callback_data="entrenamiento_hoy")],
        [
            InlineKeyboardButton("📈 Carga (CTL/ATL)", callback_data="carga"),
            InlineKeyboardButton("🫀 Salud y Sueño", callback_data="salud_sueno")
        ],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu_principal")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "👋 **¡Hola Javier! Soy tu bot de rendimiento deportivo.**\n\n"
        "Selecciona una opción del menú para consultar tus métricas o entrenamientos:"
    )
    await update.message.reply_text(
        texto,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data in ["diagnostico_completo", "entrenamiento_hoy", "carga", "salud_sueno"]:
        await query.edit_message_text("🔎 *Consultando API de Intervals.icu...*", parse_mode="Markdown")
        res = await obtener_estado_fisiologico_completo()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "menu_principal":
        texto = (
            "👋 **¡Hola Javier! Soy tu bot de rendimiento deportivo.**\n\n"
            "Selecciona una opción del menú para consultar tus métricas o entrenamientos:"
        )
        await query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

# ----------------------------------------------------------------------
# 6. ARRANQUE DEL BOT
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("Error: Falta BOT_TOKEN en las variables de entorno.")

    # Servidor Flask en segundo plano para Keep-Alive en Render
    threading.Thread(target=run_web_server, daemon=True).start()

    # Bot de Telegram asíncrono
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Bot de rendimiento iniciado con éxito...")
    app.run_polling()