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
# 4. FUNCIONES MODULARES CON DESCRIPCIONES DE SIGLAS
# ----------------------------------------------------------------------

async def obtener_salud_sueno():
    today_str = datetime.datetime.now(TZ_AR).strftime("%Y-%m-%d")
    wellness, err = await fetch_intervals_data(f"wellness/{today_str}")

    if err or not wellness:
        return "⚠️ No hay métricas de salud o descanso registradas para hoy."

    hrv = wellness.get("hrv")
    rhr = wellness.get("restingHR", "N/D")
    sleep_sec = wellness.get("sleepSecs", 0)
    sleep_hours = round(sleep_sec / 3600, 1) if sleep_sec else "N/D"
    readiness = wellness.get("readiness", "N/D")

    hrv_str = f"{round(hrv, 1)} ms" if isinstance(hrv, (int, float)) else "N/D"

    return (
        f"🫀 *SALUD, VFC Y DESCANSO*\n"
        f"📅 Fecha: `{today_str}`\n\n"
        f"• *VFC / HRV:* {hrv_str}\n"
        f"  └ _Variabilidad de Frecuencia Cardíaca (RMSSD). Mayor valor = mejor recuperación del sistema nervioso parasimpático._\n\n"
        f"• *FC Reposo:* {rhr} ppm\n"
        f"  └ _Frecuencia cardíaca en reposo. Un incremento anormal puede indicar fatiga o infección._\n\n"
        f"• *Sueño:* {sleep_hours} hs\n"
        f"  └ _Tiempo total de descanso nocturno._\n\n"
        f"• *Readiness / Disposición:* {readiness}\n"
        f"  └ _Puntuación de preparación para asimilar carga de entrenamiento._"
    )

async def obtener_carga_trabajo():
    today_str = datetime.datetime.now(TZ_AR).strftime("%Y-%m-%d")
    wellness, err = await fetch_intervals_data(f"wellness/{today_str}")

    if err or not wellness:
        return "⚠️ No hay datos de carga registrados para hoy."

    ctl_raw = wellness.get("ctl")
    atl_raw = wellness.get("atl")

    ctl = round(ctl_raw, 1) if isinstance(ctl_raw, (int, float)) else "N/D"
    atl = round(atl_raw, 1) if isinstance(atl_raw, (int, float)) else "N/D"
    tsb = round(ctl - atl, 1) if isinstance(ctl, (int, float)) and isinstance(atl, (int, float)) else "N/D"

    return (
        f"📈 *MÉTRICAS DE CARGA Y FORMA (MODELO IMPULSO-RESPUESTA)*\n"
        f"📅 Fecha: `{today_str}`\n\n"
        f"• *CTL (Chronic Training Load / Fitness):* {ctl}\n"
        f"  └ _Carga histórica acumulada (últimos 42 días). Refleja tu nivel de condición física base._\n\n"
        f"• *ATL (Acute Training Load / Fatiga):* {atl}\n"
        f"  └ _Carga reciente (últimos 7 días). Refleja el cansancio acumulado a corto plazo._\n\n"
        f"• *TSB (Training Stress Balance / Forma):* {tsb}\n"
        f"  └ _Resultado de CTL - ATL. Indica tu nivel de frescura o preparación actual._\n\n"
        f"💡 *Guía rápida de TSB:*\n"
        f"• `> +10`: Frescura / Transición o Pacing de competición.\n"
        f"• `-10 a -30`: Zona óptima de estimulación y sobrecarga progresiva.\n"
        f"• `< -30`: Riesgo elevado de sobreentrenamiento / lesión."
    )

async def obtener_entrenamiento_hoy():
    today_str = datetime.datetime.now(TZ_AR).strftime("%Y-%m-%d")
    events, err = await fetch_intervals_data("events", {"oldest": today_str, "newest": today_str})

    if err or not events:
        return f"📅 Fecha: `{today_str}`\n\n🏃 No hay planes ni actividades registradas hoy."

    actividades = [e for e in events if e.get("moving_time") or e.get("distance")]

    if actividades:
        act = actividades[0]
        nombre = act.get("name", "Entrenamiento")
        distancia = round(act.get("distance", 0) / 1000, 2)
        
        icu_training_load = act.get("icu_training_load")
        load_str = round(icu_training_load, 1) if isinstance(icu_training_load, (int, float)) else "N/D"
        
        rpe = act.get("perceived_exertion", "N/D")
        feeling = act.get("feeling", "N/D")
        decoupling = act.get("decoupling")
        
        fc_avg = act.get("average_heartrate", "N/D")
        fc_max = act.get("max_heartrate", "N/D")

        decoupling_str = f"{round(decoupling, 1)}%" if isinstance(decoupling, (int, float)) else "N/D"

        return (
            f"🏃 *SESIÓN COMPLETADA: {nombre.upper()}*\n"
            f"📅 Fecha: `{today_str}`\n\n"
            f"📏 *MÉTRICAS Y CARGA*\n"
            f"• *Distancia:* {distancia} km\n"
            f"• *Carga / TSS (Training Stress Score):* {load_str}\n"
            f"  └ _Estrés fisiológico provocado por la combinación de volumen e intensidad._\n"
            f"• *FC Media / Máx:* {fc_avg} / {fc_max} ppm\n\n"
            f"🧠 *SUBJETIVO Y DESACOPLE*\n"
            f"• *RPE (Rate of Perceived Exertion):* {rpe}/10\n"
            f"  └ _Escala subjetiva de esfuerzo percibido (1 = muy suave, 10 = esfuerzo máximo)._\n"
            f"• *Sensación / Feeling:* {feeling}/5\n"
            f"  └ _Percepción subjetiva de cómo se sintió el cuerpo durante la sesión._\n"
            f"• *Desacople Aeróbico:* {decoupling_str}\n"
            f"  └ _Deriva cardíaca respecto al ritmo/potencia. Un valor < 5% indica buena eficiencia aeróbica._"
        )

    workout = events[0]
    nombre = workout.get("name", "Planificado")
    descripcion = workout.get("description", "Sin descripción detallada.")
    distancia_plan = round(workout.get("distance", 0) / 1000, 2)

    return (
        f"📋 *ENTRENAMIENTO PLANIFICADO HOY*\n"
        f"📅 Fecha: `{today_str}`\n\n"
        f"📌 *Sesión:* {nombre}\n"
        f"📏 *Distancia prevista:* {distancia_plan if distancia_plan > 0 else 'N/D'} km\n\n"
        f"📝 *Detalles:* {descripcion}\n\n"
        f"⚠️ _Aún no se ha detectado el archivo de entrenamiento completado._"
    )

async def obtener_diagnostico_completo():
    salud = await obtener_salud_sueno()
    carga = await obtener_carga_trabajo()
    entreno = await obtener_entrenamiento_hoy()
    
    return f"{salud}\n\n---\n\n{carga}\n\n---\n\n{entreno}"

# ----------------------------------------------------------------------
# 5. MENÚ Y HANDLERS DE TELEGRAM
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
        "Selecciona una opción del menú para consultar tus métricas:"
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

    if data == "salud_sueno":
        await query.edit_message_text("🔎 *Obteniendo datos de HRV y descanso...*", parse_mode="Markdown")
        res = await obtener_salud_sueno()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "carga":
        await query.edit_message_text("🔎 *Obteniendo métricas de carga (CTL/ATL)...*", parse_mode="Markdown")
        res = await obtener_carga_trabajo()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "entrenamiento_hoy":
        await query.edit_message_text("🔎 *Consultando entrenamientos de hoy...*", parse_mode="Markdown")
        res = await obtener_entrenamiento_hoy()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "diagnostico_completo":
        await query.edit_message_text("🔎 *Generando diagnóstico integrado...*", parse_mode="Markdown")
        res = await obtener_diagnostico_completo()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "menu_principal":
        texto = (
            "👋 **¡Hola Javier! Soy tu bot de rendimiento deportivo.**\n\n"
            "Selecciona una opción del menú para consultar tus métricas:"
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

    threading.Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Bot en ejecución...")
    app.run_polling()