import os

# Lee directamente las variables configuradas en Render (o usa el fallback si no existen)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8539578864:AAFtzSbUv9FMUTw8luPGsxTGQp-kXhioXUs")
INTERVALS_API_KEY = os.getenv("INTERVALS_API_KEY", "1l4xa2od589fm5giiqpmtdvf7")
ATHLETE_ID = os.getenv("ATHLETE_ID", "i654156")
CHAT_ID = os.getenv("CHAT_ID", "1065288817")




import datetime
import logging
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# Configuración de logs en consola
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 🔑 CONFIGURACIÓN Y CREDENCIALES
INTERVALS_API_KEY = "1l4xa2od589fm5giiqpmtdvf7"
ATHLETE_ID = "i654156"

BOT_TOKEN = "8539578864:AAFtzSbUv9FMUTw8luPGsxTGQp-kXhioXUs"
CHAT_ID = "1065288817"

# --- MÓDULO DE CONSULTA A INTERVALS.ICU ---

def obtener_datos_wellness(fecha_str):
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness/{fecha_str}"
    response = requests.get(url, auth=('API_KEY', INTERVALS_API_KEY))
    if response.status_code == 200:
        return response.json()
    return None

def obtener_entrenamiento_hoy():
    today = datetime.date.today().isoformat()
    # Consultamos los eventos/workouts del día
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/events?oldest={today}&newest={today}"
    
    response = requests.get(url, auth=('API_KEY', INTERVALS_API_KEY))
    if response.status_code == 200:
        eventos = response.json()
        
        # Filtramos excluyendo solo si es una nota vacía o feriado, dejando cualquier entreno o plan
        entrenamientos = []
        for e in eventos:
            # Si tiene TSS/load, o si es de tipo Workout/Run/Ride/etc., lo tomamos
            tipo = str(e.get("type", "")).lower()
            categoria = str(e.get("category", "")).upper()
            
            if categoria == "WORKOUT" or tipo in ["workout", "ride", "run", "swim", "weighttraining", "note"] or e.get("icu_training_load"):
                entrenamientos.append(e)
                
        return entrenamientos
    return []

def obtener_datos_procesados():
    today = datetime.date.today()
    date_str = today.strftime("%d/%m/%Y")
    iso_date = today.isoformat()
    
    data = obtener_datos_wellness(iso_date)
    
    if not data or data.get("restingHR") is None:
        yesterday_str = (today - datetime.timedelta(days=1)).isoformat()
        data_ayer = obtener_datos_wellness(yesterday_str)
        if data_ayer:
            for k in ["restingHR", "hrv", "hrvSDNN", "bodyBattery", "sleepScore"]:
                if data and data.get(k) is None and data_ayer.get(k) is not None:
                    data[k] = data_ayer.get(k)
            if not data:
                data = data_ayer
                date_str = (today - datetime.timedelta(days=1)).strftime("%d/%m/%Y")

    if not data:
        return None, date_str

    return data, date_str

# --- GENERADORES DE VISTAS (REPORTES) ---

def generar_reporte_completo():
    data, date_str = obtener_datos_procesados()
    if not data:
        return "❌ <i>No se pudieron obtener datos desde Intervals.icu</i>"

    rhr = data.get("restingHR")
    body_battery = data.get("bodyBattery") or data.get("bb")
    sleep_score = data.get("sleepScore") or data.get("sleepQuality")
    hrv = data.get("hrv") or data.get("hrvSDNN")
    
    ctl = data.get("ctl")
    atl = data.get("atl")
    
    ctl_fmt = f"{round(ctl, 1)}" if ctl is not None else "N/D"
    atl_fmt = f"{round(atl, 1)}" if atl is not None else "N/D"
    tsb_val = round(ctl - atl, 1) if (ctl is not None and atl is not None) else None
    tsb_fmt = f"{tsb_val}" if tsb_val is not None else "N/D"

    puntuacion = 100
    alertas = []
    
    if rhr and rhr > 55:
        alertas.append(f"• FC en Reposo elevada (<code>{rhr} bpm</code>)")
        puntuacion -= 25
        
    if body_battery and body_battery < 60:
        alertas.append(f"• Body Battery en nivel bajo (<code>{body_battery}%</code>)")
        puntuacion -= 25
        
    if sleep_score and sleep_score < 70:
        alertas.append(f"• Calidad de descanso insuficiente (<code>{sleep_score}/100</code>)")
        puntuacion -= 20

    if puntuacion >= 80:
        badge_estado = "🟢 <b>ESTADO ÓPTIMO</b>"
        recomendacion = "Sistema recuperado. Tienes luz verde para la sesión programada."
    elif puntuacion >= 50:
        badge_estado = "🟡 <b>FATIGA MODERADA</b>"
        recomendacion = "Carga acumulada considerable. Se sugiere rodaje suave en Z2 o ajustar intensidad."
    else:
        badge_estado = "🔴 <b>ALERTA DE FATIGA</b>"
        recomendacion = "Estrés fisiológico alto. Considera descanso activo, movilidad o sesión de recuperación."

    rhr_str = f"<b>{rhr}</b> bpm" if rhr is not None else "<i>N/D</i>"
    hrv_str = f"<b>{hrv}</b> ms" if hrv is not None else "<i>N/D</i>"
    bb_str = f"<b>{body_battery}%</b>" if body_battery is not None else "<i>N/D</i>"
    sleep_str = f"<b>{sleep_score}/100</b>" if sleep_score is not None else "<i>N/D</i>"

    return f"""<b>📊 INFORME DE RENDIMIENTO MATUTINO</b>
📅 <i>{date_str}</i>
────────────────────────

{badge_estado}
💡 {recomendacion}

<b>🫀 SALUD Y RECUPERACIÓN</b>
• FC en Reposo:  {rhr_str}
• Estado HRV:     {hrv_str}
• Body Battery:   {bb_str}
• Calidad Sueño:  {sleep_str}

<b>📈 CARGA DE ENTRENAMIENTO</b>
• Aptitud (CTL): <b>{ctl_fmt}</b>
• Fatiga (ATL):   <b>{atl_fmt}</b>
• Forma (TSB):    <b>{tsb_fmt}</b>

<b>🔍 DIAGNÓSTICO FISIOLÓGICO</b>
{chr(10).join(alertas) if alertas else "• Sin signos de estrés fisiológico detectados."}
────────────────────────
🤖 <i>Generado por GarminBot • Intervals.icu</i>"""

def generar_vista_carga():
    data, date_str = obtener_datos_procesados()
    if not data:
        return "❌ <i>No se pudieron obtener datos desde Intervals.icu</i>"

    ctl = data.get("ctl")
    atl = data.get("atl")
    ctl_fmt = round(ctl, 1) if ctl is not None else "N/D"
    atl_fmt = round(atl, 1) if atl is not None else "N/D"
    tsb_val = round(ctl - atl, 1) if (ctl is not None and atl is not None) else "N/D"

    return f"""<b>📈 RESUMEN DE CARGA Y FORMA FÍSICA</b>
📅 <i>{date_str}</i>
────────────────────────
• <b>Fitness (CTL):</b> {ctl_fmt}  <i>(Aptitud a largo plazo)</i>
• <b>Fatiga (ATL):</b> {atl_fmt}  <i>(Carga acumulada reciente)</i>
• <b>Forma (TSB):</b> {tsb_val}  <i>(Balance de frescura)</i>

💡 <i>Un TSB positivo indica frescura física; un TSB negativo refleja fatiga acumulada por entrenamientos.</i>
────────────────────────"""

def generar_vista_salud():
    data, date_str = obtener_datos_procesados()
    if not data:
        return "❌ <i>No se pudieron obtener datos desde Intervals.icu</i>"

    rhr = data.get("restingHR", "N/D")
    hrv = data.get("hrv") or data.get("hrvSDNN", "N/D")
    bb = data.get("bodyBattery") or data.get("bb", "N/D")
    sleep = data.get("sleepScore") or data.get("sleepQuality", "N/D")

    return f"""<b>🫀 MÉTRICAS DE SALUD Y RECUPERACIÓN</b>
📅 <i>{date_str}</i>
────────────────────────
• <b>FC en Reposo:</b> {rhr} bpm
• <b>Variabilidad (HRV):</b> {hrv} ms
• <b>Body Battery:</b> {bb}%
• <b>Calidad de Sueño:</b> {sleep}/100
────────────────────────"""

def generar_vista_entrenamiento():
    entrenamientos = obtener_entrenamiento_hoy()
    date_str = datetime.date.today().strftime("%d/%m/%Y")

    if not entrenamientos:
        return f"""<b>🚴‍♂️ ENTRENAMIENTO PROGRAMADO</b>
📅 <i>{date_str}</i>
────────────────────────
🛋️ <b>Día de Descanso / Sin sesión agendada</b>

<i>No hay entrenamientos planificados en Intervals.icu para el día de hoy. ¡Aprovecha para recuperar!</i>
────────────────────────"""

    entreno = entrenamientos[0]
    nombre = entreno.get("name", "Entrenamiento")
    tipo = entreno.get("type", "Actividad")
    
    moving_time = entreno.get("moving_time") or entreno.get("elapsed_time")
    duracion = f"{round(moving_time / 60)} min" if moving_time else "N/D"
    load = entreno.get("icu_training_load", "N/D")
    
    descripcion = entreno.get("description", "").strip()
    if len(descripcion) > 200:
        descripcion = descripcion[:197] + "..."

    return f"""<b>🚴‍♂️ ENTRENAMIENTO PROGRAMADO</b>
📅 <i>{date_str}</i>
────────────────────────
🎯 <b>{nombre}</b>
• <b>Disciplina:</b> {tipo}
• <b>Duración estimada:</b> {duracion}
• <b>Carga esperada (TSS):</b> {load}

{f"📝 <b>Detalles:</b>{chr(10)}<i>{descripcion}</i>" if descripcion else ""}
────────────────────────"""

# --- INTERFAZ CON BOTONES (KEYBOARD) ---

def obtener_teclado_principal():
    """Crea la botonera interactiva"""
    keyboard = [
        [
            InlineKeyboardButton("📊 Diagnóstico Completo", callback_data="btn_completo")
        ],
        [
            InlineKeyboardButton("🏃‍♂️ Entrenamiento Hoy", callback_data="btn_entreno")
        ],
        [
            InlineKeyboardButton("📈 Carga (CTL/ATL)", callback_data="btn_carga"),
            InlineKeyboardButton("🫀 Salud y Sueño", callback_data="btn_salud")
        ],
        [
            InlineKeyboardButton("🏠 Menú Principal", callback_data="btn_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- MANEJADORES DE TELEGRAM (HANDLERS) ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    texto = (
        "👋 <b>¡Hola Javier! Soy tu bot de rendimiento deportivo.</b>\n\n"
        "Selecciona una opción del menú para consultar tus métricas o entrenamiento en Intervals.icu:"
    )
    await update.message.reply_text(
        texto, 
        reply_markup=obtener_teclado_principal(), 
        parse_mode='HTML'
    )

async def hoy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /hoy"""
    mensaje_espera = await update.message.reply_text("🔎 <i>Consultando Intervals.icu...</i>", parse_mode='HTML')
    reporte = generar_reporte_completo()
    
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mensaje_espera.message_id)
    await update.message.reply_text(
        reporte, 
        reply_markup=obtener_teclado_principal(), 
        parse_mode='HTML'
    )

async def manejar_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los clics en los botones"""
    query = update.callback_query
    
    if query.data in ["btn_completo", "btn_hoy"]:
        nuevo_texto = generar_reporte_completo()
    elif query.data == "btn_entreno":
        nuevo_texto = generar_vista_entrenamiento()
    elif query.data == "btn_carga":
        nuevo_texto = generar_vista_carga()
    elif query.data == "btn_salud":
        nuevo_texto = generar_vista_salud()
    elif query.data == "btn_menu":
        nuevo_texto = (
            "👋 <b>¡Hola Javier! Soy tu bot de rendimiento deportivo.</b>\n\n"
            "Selecciona una opción del menú para consultar tus métricas o entrenamiento en Intervals.icu:"
        )
    else:
        nuevo_texto = generar_reporte_completo()

    try:
        await query.answer()
        await query.edit_message_text(
            text=nuevo_texto, 
            reply_markup=obtener_teclado_principal(), 
            parse_mode='HTML'
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            await query.answer(text="ℹ️ Las métricas ya están actualizadas.", show_alert=False)
        else:
            raise e

async def enviar_reporte_programado(context: ContextTypes.DEFAULT_TYPE):
    """Envío automático diario"""
    reporte = generar_reporte_completo()
    await context.bot.send_message(
        chat_id=CHAT_ID, 
        text=reporte, 
        reply_markup=obtener_teclado_principal(), 
        parse_mode='HTML'
    )

# --- EJECUCIÓN PRINCIPAL ---

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Registro de handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("hoy", hoy_cmd))
    app.add_handler(CommandHandler("ayuda", start_cmd))
    app.add_handler(CallbackQueryHandler(manejar_botones))

    # Tareas programadas internas (07:00 hs y 20:00 hs)
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(enviar_reporte_programado, time=datetime.time(hour=7, minute=0, second=0))
        job_queue.run_daily(enviar_reporte_programado, time=datetime.time(hour=20, minute=0, second=0))

    print("🤖 Bot interactivo activo y escuchando mensajes...")
    app.run_polling()