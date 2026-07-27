import datetime
import zoneinfo
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
# 3. WEBSERVER FLASK (Render Healthcheck)
# ----------------------------------------------------------------------
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot de Garmin/Intervals activo.", 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server.run(host="0.0.0.0", port=port)

# ----------------------------------------------------------------------
# 4. FUNCIONES DE CONSULTA A INTERVALS.ICU
# ----------------------------------------------------------------------

def obtener_entrenamiento_hoy():
    if not INTERVALS_API_KEY or not ATHLETE_ID:
        return "⚠️ *Error:* Faltan configurar `INTERVALS_API_KEY` o `ATHLETE_ID` en Render."

    # Usar hora local de Argentina para evitar descalce por UTC en Render
    tz_ar = zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")
    today_str = datetime.datetime.now(tz_ar).strftime("%Y-%m-%d")

    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events?oldest={today_str}&newest={today_str}"
    
    try:
        response = requests.get(url, auth=('API_KEY', INTERVALS_API_KEY), timeout=10)
        if response.status_code != 200:
            return f"❌ Error al consultar la API (Código {response.status_code})."
        
        eventos = response.json()
        if not eventos:
            return f"📅 *Fecha:* {today_str}\n\n🏃 No hay actividades ni planes para hoy en Intervals.icu."

        # Buscar si hay una actividad completada (tiene moving_time o distancia real)
        actividades_realizadas = [e for e in eventos if e.get("moving_time") or e.get("distance")]
        
        if actividades_realizadas:
            act = actividades_realizadas[0]
            nombre = act.get("name", "Entrenamiento")
            distancia = round(act.get("distance", 0) / 1000, 2)
            
            moving_time = act.get("moving_time", 0)
            m, s = divmod(moving_time, 60)
            h, m = divmod(m, 60)
            tiempo_mov_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

            velocity = act.get("average_speed", 0)
            ritmo_str = f"{int(1000/velocity)//60}:{int(1000/velocity)%60:02d}" if velocity > 0 else "N/A"

            fc_avg = act.get("average_heartrate", "N/A")
            fc_max = act.get("max_heartrate", "N/A")
            cadence = act.get("average_cadence", "N/A")
            if cadence != "N/A":
                cadence = int(cadence * 2)

            calorias = act.get("calories", "N/A")

            return (
                f"🏃 *SESIÓN COMPLETADA: {nombre.upper()}*\n"
                f"📅 Fecha: {today_str}\n\n"
                f"📏 *TIEMPOS Y RITMOS*\n"
                f"• Distancia: *{distancia} km*\n"
                f"• Tiempo en Movimiento: *{tiempo_mov_str} min*\n"
                f"• Ritmo Medio: *{ritmo_str} /km*\n\n"
                f"🫀 *MÉTRICAS CARDIACAS*\n"
                f"• FC Media: *{fc_avg} ppm* | FC Máx: *{fc_max} ppm*\n"
                f"• Cadencia Media: *{cadence} ppm*\n"
                f"• Calorías: *{calorias} kcal*\n"
            )

        # Si no hay ejecutados, mostramos el planificado (TrainingPeaks / Intervals Workout)
        workout = eventos[0]
        nombre = workout.get("name", "Entrenamiento Planificado")
        descripcion = workout.get("description", "Sin descripción detallada.")
        distancia_plan = round(workout.get("distance", 0) / 1000, 2)

        return (
            f"📋 *ENTRENAMIENTO PLANIFICADO PARA HOY*\n"
            f"📅 Fecha: {today_str}\n\n"
            f"📌 *Sesión:* {nombre}\n"
            f"📏 *Distancia prevista:* {distancia_plan if distancia_plan > 0 else '8.0'} km\n\n"
            f"📝 *Detalles:* {descripcion}\n\n"
            f"⚠️ _Aún no se ha detectado el archivo de reloj Garmin/FIT guardado para esta sesión._"
        )

    except Exception as e:
        logging.error(f"Error en entrenamiento_hoy: {e}")
        return "⚠️ Ocurrió un error al consultar el entrenamiento de hoy."


def obtener_carga_trabajo():
    """Consulta las métricas de Carga (CTL / ATL / TSB)"""
    if not INTERVALS_API_KEY or not ATHLETE_ID:
        return "⚠️ *Error:* Faltan variables de entorno."

    today_str = date.today().isoformat()
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness/{today_str}"
    
    try:
        response = requests.get(url, auth=('API_KEY', INTERVALS_API_KEY), timeout=10)
        if response.status_code == 200:
            data = response.json()
            ctl = data.get("ctl", "N/D")
            atl = data.get("atl", "N/D")
            
            tsb = "N/D"
            if isinstance(ctl, (int, float)) and isinstance(atl, (int, float)):
                tsb = round(ctl - atl, 1)

            return (
                f"📈 *MÉTRICAS DE CARGA Y FORMA (IMPULSE-RESPONSE)*\n"
                f"📅 *Fecha:* {today_str}\n\n"
                f"• *CTL (Fitness / Carga a Largo Plazo):* {ctl}\n"
                f"• *ATL (Fatiga / Carga a Corto Plazo):* {atl}\n"
                f"• *TSB (Forma / Frescura):* {tsb}\n\n"
                f"💡 _Interpretación de TSB: Un valor positivo indica frescura/recuperación; un valor negativo (-10 a -30) indica zona óptima de estimulación y carga._"
            )
        return "⚠️ No se registraron datos de carga para la jornada de hoy."
    except Exception as e:
        logging.error(f"Error en carga: {e}")
        return "⚠️ Error al conectar con Intervals.icu para métricas de carga."


def obtener_salud_sueno():
    """Consulta métricas de Salud, Descanso y VFC"""
    if not INTERVALS_API_KEY or not ATHLETE_ID:
        return "⚠️ *Error:* Faltan variables de entorno."

    today_str = date.today().isoformat()
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness/{today_str}"
    
    try:
        response = requests.get(url, auth=('API_KEY', INTERVALS_API_KEY), timeout=10)
        if response.status_code == 200:
            data = response.json()
            hrv = data.get("hrv", "N/D")
            rhr = data.get("restingHR", "N/D")
            sleep_sec = data.get("sleepSecs", 0)
            score = data.get("readiness", "N/D")

            sleep_hours = round(sleep_sec / 3600, 1) if sleep_sec else "N/D"

            return (
                f"🫀 *SALUD, VFC Y DESCANSO*\n"
                f"📅 *Fecha:* {today_str}\n\n"
                f"• *VFC / HRV (RMSSD):* {hrv} ms\n"
                f"• *FC en Reposo:* {rhr} ppm\n"
                f"• *Horas de Sueño:* {sleep_hours} hs\n"
                f"• *Readiness / Disposición:* {score}\n\n"
                f"💡 _La Variabilidad de Frecuencia Cardíaca (HRV) alta indica que el sistema parasimpático está asimilando correctamente el estrés del entrenamiento._"
            )
        return "⚠️ No hay métricas de salud o sueño cargadas para hoy."
    except Exception as e:
        logging.error(f"Error en salud_sueno: {e}")
        return "⚠️ Error al consultar los datos de salud."

# ----------------------------------------------------------------------
# 5. MENÚ Y MANEJO DE EVENTOS
# ----------------------------------------------------------------------
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Diagnóstico Completo", callback_data="diagnostico")],
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

    data = query.data

    if data == "entrenamiento_hoy":
        await query.edit_message_text("🔎 *Consultando Garmin / Intervals.icu...*", parse_mode="Markdown")
        res = obtener_entrenamiento_hoy()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "carga":
        await query.edit_message_text("🔎 *Obteniendo métricas de carga (CTL/ATL)...*", parse_mode="Markdown")
        res = obtener_carga_trabajo()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "salud_sueno":
        await query.edit_message_text("🔎 *Obteniendo datos de HRV y descanso...*", parse_mode="Markdown")
        res = obtener_salud_sueno()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "diagnostico":
        await query.edit_message_text("🔎 *Generando diagnóstico integrado...*", parse_mode="Markdown")
        entreno = obtener_entrenamiento_hoy()
        carga = obtener_carga_trabajo()
        res_completo = f"{entreno}\n\n---\n\n{carga}"
        await query.edit_message_text(res_completo, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "menu_principal":
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
# 6. ARRANQUE
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("Error: Falta BOT_TOKEN.")

    threading.Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Bot en ejecución...")
    app.run_polling()