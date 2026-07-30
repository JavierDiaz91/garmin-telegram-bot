import datetime
import zoneinfo
import os
import logging
import threading
import urllib.parse
import json
import base64
import httpx
from flask import Flask
from groq import Groq
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MY_CHAT_ID = os.getenv("CHAT_ID")

TZ_AR = zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")

groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        logging.info("Cliente de Groq inicializado correctamente.")
    except Exception as e:
        logging.error(f"Error al inicializar cliente de Groq: {e}")

# Base de datos local simulada para Zapatillas
SHOES_DB_FILE = "zapatillas.json"

def cargar_zapatillas():
    if os.path.exists(SHOES_DB_FILE):
        try:
            with open(SHOES_DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "Pistas / Carbono": {"km": 120.0, "max_km": 400.0, "modelo": "Nike Vaporfly / Alphafly"},
        "Entreno Diario": {"km": 380.0, "max_km": 700.0, "modelo": "Kiprun / Pegasus"}
    }

def guardar_zapatillas(data):
    try:
        with open(SHOES_DB_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Error guardando zapatillas: {e}")

# ----------------------------------------------------------------------
# 2. WEBSERVER FLASK
# ----------------------------------------------------------------------
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot de Rendimiento Deportivo activo.", 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        logging.info(f"Iniciando servidor Flask en puerto {port}...")
        server.run(host="0.0.0.0", port=port, use_reloader=False)
    except Exception as e:
        logging.error(f"Error al iniciar servidor Flask: {e}")

# ----------------------------------------------------------------------
# 3. CLIENTE ASÍNCRONO DE INTERVALS.ICU Y CLIMA
# ----------------------------------------------------------------------
async def fetch_intervals_data(endpoint: str, params: dict = None):
    if not INTERVALS_API_KEY or not ATHLETE_ID:
        return None, "⚠️ *Error:* Faltan configurar `INTERVALS_API_KEY` o `ATHLETE_ID`."

    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/{endpoint}"
    auth = ('API_KEY', INTERVALS_API_KEY)

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(url, auth=auth, params=params)
            if response.status_code == 200:
                return response.json(), None
            return None, f"❌ Error API (Código {response.status_code})"
    except Exception as e:
        logging.error(f"Error consultando {endpoint}: {e}")
        return None, "⚠️ Error de conexión con Intervals.icu."

async def obtener_clima_rafaela():
    """Consulta clima público para Rafaela, Santa Fe."""
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=-31.2503&longitude=-61.4867&current_weather=true&timezone=America%2FArgentina%2FBuenos_Aires"
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(url)
            if res.status_code == 200:
                data = res.json().get("current_weather", {})
                temp = data.get("temperature", "N/D")
                wind = data.get("windspeed", "N/D")
                return f"🌡️ `{temp}°C` | 💨 Viento: `{wind} km/h`"
    except Exception as e:
        logging.error(f"Error consultando clima: {e}")
    return "🌡️ Clima no disponible"

# ----------------------------------------------------------------------
# 4. GENERADOR DE GRÁFICOS (QUICKCHART.IO)
# ----------------------------------------------------------------------
def generar_url_grafico_pmc(fechas, ctl, atl, tsb):
    """Genera una imagen PNG con la gráfica PMC (Fitness vs Fatiga vs Forma)."""
    chart_config = {
        "type": "line",
        "data": {
            "labels": fechas,
            "datasets": [
                {
                    "label": "Fitness (CTL)",
                    "borderColor": "#2196F3",
                    "backgroundColor": "#2196F3",
                    "data": ctl,
                    "fill": False,
                    "borderWidth": 2,
                    "pointRadius": 0
                },
                {
                    "label": "Fatiga (ATL)",
                    "borderColor": "#FF9800",
                    "backgroundColor": "#FF9800",
                    "data": atl,
                    "fill": False,
                    "borderWidth": 2,
                    "pointRadius": 0
                },
                {
                    "label": "Forma (TSB)",
                    "borderColor": "#4CAF50",
                    "backgroundColor": "rgba(76, 175, 80, 0.2)",
                    "data": tsb,
                    "fill": True,
                    "borderWidth": 1.5,
                    "pointRadius": 0
                }
            ]
        },
        "options": {
            "title": {"display": True, "text": "Performance Management Chart (Últimos 14 días)", "fontColor": "#ffffff"},
            "legend": {"labels": {"fontColor": "#ffffff"}},
            "scales": {
                "xAxes": [{"ticks": {"fontColor": "#cccccc"}}],
                "yAxes": [{"ticks": {"fontColor": "#cccccc"}}]
            }
        }
    }
    
    encoded_config = urllib.parse.quote(json.dumps(chart_config))
    return f"https://quickchart.io/chart?c={encoded_config}&bkg=%231e1e1e&w=600&h=320"

# ----------------------------------------------------------------------
# 5. FUNCIONES DE MÉRICA Y REPORTE
# ----------------------------------------------------------------------

async def obtener_dinamicas_biomecanica():
    actividades, err = await fetch_intervals_data("activities", {"limit": 3})

    if err or not actividades:
        return "⚠️ No se encontraron actividades recientes con métricas biomecánicas."

    act = actividades[0]
    nombre = act.get("name", "Entrenamiento")
    fecha = act.get("start_date_local", "")[:10]
    
    cadencia_raw = act.get("average_cadence")
    cadencia = int(round(cadencia_raw)) if isinstance(cadencia_raw, (int, float)) else "N/D"
    
    stride_len = round(act.get("stride_length", 0), 2) if act.get("stride_length") else "N/D"
    gct = round(act.get("ground_contact_time", 0), 1) if act.get("ground_contact_time") else "N/D"
    gct_bal = act.get("ground_contact_balance", "N/D")
    osc_vert = round(act.get("vertical_oscillation", 0), 1) if act.get("vertical_oscillation") else "N/D"
    rel_vert = round(act.get("vertical_ratio", 0), 1) if act.get("vertical_ratio") else "N/D"

    vo2max = act.get("icu_vo2max") or act.get("vo2max", "N/D")
    te_aero = act.get("aerobic_training_effect", "N/D")
    te_anaero = act.get("anaerobic_training_effect", "N/D")
    threshold_hr = act.get("threshold_heartrate", "N/D")

    return (
        f"🏃‍♂️ *DINÁMICAS DE CARRERA Y BIOMECÁNICA*\n"
        f"📌 *{nombre.upper()}* (`{fecha}`)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🦶 *EFICIENCIA Y PISADA:*\n"
        f"• 🔄 *Cadencia Media:* `{cadencia} ppm`\n"
        f"• 📏 *Longitud de Zancada:* `{stride_len} m`\n"
        f"• ⏱️ *Tiempo Contacto Suelo (GCT):* `{gct} ms`\n"
        f"• ⚖️ *Equilibrio GCT L/R:* `{gct_bal}`\n\n"
        f"🦘 *OSCILACIÓN Y RATIO VERTICAL:*\n"
        f"• ⬆️ *Oscilación Vertical:* `{osc_vert} cm`\n"
        f"• 📐 *Relación Vertical:* `{rel_vert}%`\n\n"
        f"⚡ *IMPACTO Y EFECTO DE ENTRENAMIENTO:*\n"
        f"• 🫁 *Training Effect Aeróbico:* `{te_aero} / 5.0`\n"
        f"• 💥 *Training Effect Anaeróbico:* `{te_anaero} / 5.0`\n"
        f"• 🎯 *VO2 Máx Estimado:* `{vo2max} ml/kg/min`\n"
        f"• 🩸 *Umbral Lactato FC:* `{threshold_hr} ppm`"
    )

async def obtener_salud_sueno():
    now = datetime.datetime.now(TZ_AR)
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    wellness_list, err = await fetch_intervals_data("wellness", {"oldest": yesterday_str, "newest": today_str})

    if err or not wellness_list:
        wellness_today, _ = await fetch_intervals_data(f"wellness/{today_str}")
        wellness_list = [wellness_today] if wellness_today else []

    if not wellness_list:
        return "⚠️ No hay registros de salud para analizar."

    datos = {item.get("id"): item for item in wellness_list if isinstance(item, dict)}
    hoy = datos.get(today_str, {})
    ayer = datos.get(yesterday_str, {})

    hrv_h = f"{round(hoy.get('hrv'), 1)} ms" if isinstance(hoy.get("hrv"), (int, float)) else "N/D"
    sue_h = round(hoy.get("sleepSecs", 0) / 3600, 1) if hoy.get("sleepSecs") else "N/D"
    rhr_h = hoy.get("restingHR", "N/D")

    hrv_a = f"{round(ayer.get('hrv'), 1)} ms" if isinstance(ayer.get("hrv"), (int, float)) else "N/D"
    sue_a = round(ayer.get("sleepSecs", 0) / 3600, 1) if ayer.get("sleepSecs") else "N/D"
    rhr_a = ayer.get("restingHR", "N/D")

    return (
        f"🫀 *MONITOREO DE RECOVERY Y SALUD*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"☀️ *HOY (`{today_str}`):*\n"
        f"• 😴 *Sueño Nocturno:* `{sue_h} hs`\n"
        f"• 📈 *VFC / HRV (RMSSD):* `{hrv_h}`\n"
        f"• ❤️ *FC en Reposo:* `{rhr_h} ppm`\n\n"
        f"🌙 *AYER (`{yesterday_str}`):*\n"
        f"• 😴 *Sueño Nocturno:* `{sue_a} hs`\n"
        f"• 📈 *VFC / HRV:* `{hrv_a}`\n"
        f"• ❤️ *FC en Reposo:* `{rhr_a} ppm`"
    )

async def obtener_carga_trabajo_con_grafico():
    now = datetime.datetime.now(TZ_AR)
    today_str = now.strftime("%Y-%m-%d")
    oldest_str = (now - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

    wellness_list, err = await fetch_intervals_data("wellness", {"oldest": oldest_str, "newest": today_str})

    if err or not wellness_list:
        return "⚠️ Sin datos de carga disponibles.", None

    fechas, ctl_list, atl_list, tsb_list = [], [], [], []

    for item in wellness_list:
        if isinstance(item, dict):
            fechas.append(item.get("id", "")[5:]) # MM-DD
            c = round(item.get("ctl", 0), 1) if item.get("ctl") else 0
            a = round(item.get("atl", 0), 1) if item.get("atl") else 0
            ctl_list.append(c)
            atl_list.append(a)
            tsb_list.append(round(c - a, 1))

    ctl_actual = ctl_list[-1] if ctl_list else "N/D"
    atl_actual = atl_list[-1] if atl_list else "N/D"
    tsb_actual = tsb_list[-1] if tsb_list else "N/D"

    if isinstance(tsb_actual, (int, float)):
        if tsb_actual > 10:
            estado_tsb = "🟢 FRESCO / RECUPERADO"
        elif -10 <= tsb_actual <= 10:
            estado_tsb = "🔵 ZONA NEUTRA / MANTENIMIENTO"
        elif -30 <= tsb_actual < -10:
            estado_tsb = "🟠 ZONA DE SOBRECARGA ÓPTIMA"
        else:
            estado_tsb = "🔴 RIESGO DE FATIGA EXTREMA"
    else:
        estado_tsb = "⚪ SIN DATOS"

    url_chart = generar_url_grafico_pmc(fechas, ctl_list, atl_list, tsb_list)

    texto = (
        f"📈 *ESTADO DE CARGA Y FORMA (CTL / ATL / TSB)*\n"
        f"📅 Fecha: `{today_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🛡️ *Fitness (CTL):* `{ctl_actual}` _(Últimos 42 días)_\n"
        f"🔥 *Fatiga (ATL):* `{atl_actual}` _(Últimos 7 días)_\n"
        f"⚖️ *Forma / TSB:* `{tsb_actual}`\n\n"
        f"🎯 *DIAGNÓSTICO RÁPIDO:*\n"
        f"└ {estado_tsb}"
    )

    return texto, url_chart

async def obtener_estado_zapatillas():
    zapas = cargar_zapatillas()
    lineas = ["👟 *ESTADO Y KILOMETRAJE DE ZAPATILLAS*", "━━━━━━━━━━━━━━━━━━━━━━━\n"]

    for nombre, info in zapas.items():
        km = info.get("km", 0)
        max_km = info.get("max_km", 600)
        pct = min(100, int((km / max_km) * 100))
        bar_filled = "█" * (pct // 10)
        bar_empty = "░" * (10 - (pct // 10))
        alerta = " ⚠️ *RECAMBIO SUGERIDO*" if pct >= 85 else ""

        lineas.append(
            f"📌 *{nombre}* ({info.get('modelo', '')})\n"
            f"  ├ 📏 `{km:.1f} km` / `{max_km:.0f} km` ({pct}%)\n"
            f"  └ `[{bar_filled}{bar_empty}]`{alerta}\n"
        )

    return "\n".join(lineas)

async def obtener_historial_actividades(dias: int = 7):
    now = datetime.datetime.now(TZ_AR)
    today_str = now.strftime("%Y-%m-%d")
    oldest_str = (now - datetime.timedelta(days=dias)).strftime("%Y-%m-%d")

    actividades, err = await fetch_intervals_data("activities", {"oldest": oldest_str, "newest": today_str})

    if err or not actividades:
        return f"⚠️ Sin registros de actividades en los últimos {dias} días."

    resumen = []
    for act in actividades:
        fecha = act.get("start_date_local", "")[:10]
        nombre = act.get("name", "Entrenamiento")
        dist = round(act.get("distance", 0) / 1000, 2)
        dur = round(act.get("moving_time", 0) / 60, 1)
        tss = round(act.get("icu_training_load", 0), 1) if act.get("icu_training_load") else "N/D"
        fc_avg = act.get("average_heartrate", "N/D")
        
        cad_raw = act.get("average_cadence")
        cad = int(round(cad_raw)) if isinstance(cad_raw, (int, float)) else "N/D"
        
        stride = round(act.get("stride_length", 0), 2) if act.get("stride_length") else "N/D"

        resumen.append(
            f"📅 *{fecha}* ➔ *{nombre}*\n"
            f"  └ 📏 `{dist} km` | ⏱️ `{dur} min` | ⚡ `TSS {tss}`\n"
            f"  └ ❤️ `FC {fc_avg} ppm` | 🔄 `Cad {cad} ppm` | 🦶 `Zancada {stride} m`"
        )

    return "\n\n".join(resumen)

async def obtener_diagnostico_completo(dias_historia: int = 7):
    now = datetime.datetime.now(TZ_AR)
    today_str = now.strftime("%Y-%m-%d")
    oldest_str = (now - datetime.timedelta(days=dias_historia)).strftime("%Y-%m-%d")

    wellness_list, _ = await fetch_intervals_data("wellness", {"oldest": oldest_str, "newest": today_str})
    
    resumen_wellness = []
    if isinstance(wellness_list, list):
        for item in wellness_list:
            f = item.get("id")
            sueño = round(item.get("sleepSecs", 0) / 3600, 1) if item.get("sleepSecs") else "N/D"
            hrv = round(item.get("hrv"), 1) if item.get("hrv") else "N/D"
            ctl = round(item.get("ctl"), 1) if item.get("ctl") else "N/D"
            atl = round(item.get("atl"), 1) if item.get("atl") else "N/D"
            resumen_wellness.append(f"[{f}] Sueño: {sueño}hs | HRV: {hrv}ms | CTL: {ctl} | ATL: {atl}")

    texto_wellness = "\n".join(resumen_wellness) if resumen_wellness else "Sin registros."
    texto_actividades = await obtener_historial_actividades(dias=dias_historia)
    biomecanica = await obtener_dinamicas_biomecanica()

    return (
        f"📌 FECHA SERVIDOR: {today_str}\n\n"
        f"📊 SALUD Y CARGA HISTÓRICA ({dias_historia} DÍAS):\n{texto_wellness}\n\n"
        f"🏃 ACTIVIDADES COMPLETADAS ({dias_historia} DÍAS):\n{texto_actividades}\n\n"
        f"🧬 ÚLTIMAS DINÁMICAS BIOMECÁNICAS REGISTRADAS:\n{biomecanica}"
    )

# ----------------------------------------------------------------------
# 6. MORNING BRIEFING AUTOMÁTICO
# ----------------------------------------------------------------------
async def enviar_morning_briefing(app):
    """Tarea programada que envía un informe diario proactivo a las 07:30 AM."""
    if not MY_CHAT_ID:
        logging.warning("No se ejecutó Morning Briefing: falta MY_CHAT_ID en variables de entorno.")
        return

    salud = await obtener_salud_sueno()
    clima = await obtener_clima_rafaela()
    carga, _ = await obtener_carga_trabajo_con_grafico()

    texto_briefing = (
        f"🌅 *MORNING BRIEFING - ALTO RENDIMIENTO*\n"
        f"📍 Rafaela, Santa Fe | {clima}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{salud}\n\n"
        f"{carga}\n\n"
        f"💡 *CONSEJO DEL DÍA:* Ajustá la hidratación según la temperatura local y recordá revisar la cadencia objetivo en las pasadas de hoy."
    )

    try:
        await app.bot.send_message(chat_id=MY_CHAT_ID, text=texto_briefing, parse_mode="Markdown")
        logging.info("Morning Briefing enviado con éxito.")
    except Exception as e:
        logging.error(f"Error enviando Morning Briefing: {e}")

# ----------------------------------------------------------------------
# 7. MENÚ Y BOTONES INTERACTIVOS
# ----------------------------------------------------------------------
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("⚡ DIAGNÓSTICO INTEGRAL Y IA", callback_data="diagnostico_completo")],
        [
            InlineKeyboardButton("🏃 Entreno Hoy", callback_data="entrenamiento_hoy"),
            InlineKeyboardButton("🦶 Biomecánica & Cadencia", callback_data="biomecanica")
        ],
        [
            InlineKeyboardButton("📈 Carga & Gráfico PMC", callback_data="carga"),
            InlineKeyboardButton("🫀 Sueño & HRV", callback_data="salud_sueno")
        ],
        [InlineKeyboardButton("👟 Kilometraje Zapatillas", callback_data="zapatillas")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu_principal")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🚀 *¡HOLA JAVII! CENTRO DE ALTO RENDIMIENTO DEPORTIVO*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• Tocá cualquier botón para consultar métricas o ver tu gráfico PMC.\n"
        "• Mandame una *foto o captura* de tu reloj/entrenamiento y la analizaré con Visión por IA.\n"
        "• O escribime en texto libre para analizar cualquier aspecto de tu rendimiento."
    )
    await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "biomecanica":
        await query.edit_message_text("🔍 *Escaneando cadencia, zancada y oscilación...*", parse_mode="Markdown")
        res = await obtener_dinamicas_biomecanica()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "salud_sueno":
        await query.edit_message_text("🔍 *Recuperando métricas de sueño y VFC...*", parse_mode="Markdown")
        res = await obtener_salud_sueno()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "carga":
        await query.edit_message_text("🔍 *Generando modelo PMC y gráfico...*", parse_mode="Markdown")
        res_texto, url_chart = await obtener_carga_trabajo_con_grafico()
        
        if url_chart:
            await query.message.reply_photo(
                photo=url_chart,
                caption=res_texto,
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
        else:
            await query.edit_message_text(res_texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "zapatillas":
        await query.edit_message_text("🔍 *Verificando desgaste de calzado...*", parse_mode="Markdown")
        res = await obtener_estado_zapatillas()
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "entrenamiento_hoy":
        await query.edit_message_text("🔍 *Buscando entrenamientos de hoy...*", parse_mode="Markdown")
        res = await obtener_historial_actividades(dias=1)
        await query.edit_message_text(f"🏃 *ENTRENAMIENTO DE HOY:*\n\n{res}", parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "diagnostico_completo":
        await query.edit_message_text("🔍 *Procesando diagnóstico integrado...*", parse_mode="Markdown")
        res = await obtener_diagnostico_completo(dias_historia=7)
        await query.edit_message_text(res, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif data == "menu_principal":
        texto = "🏠 *MENÚ PRINCIPAL DE RENDIMIENTO*\n\nElegí una opción o escribime directamente tu consulta:"
        await query.edit_message_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())

# ----------------------------------------------------------------------
# 8. MANEJADOR DE IMÁGENES CON VISIÓN POR IA (LLAMA 3.2 VISION)
# ----------------------------------------------------------------------
async def handle_user_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not groq_client:
        await update.message.reply_text("⚠️ *La IA no está configurada.* Falta `GROQ_API_KEY`.", parse_mode="Markdown")
        return

    thinking_msg = await update.message.reply_text("👁️ *Javii, analizando la foto de tu entrenamiento...*", parse_mode="Markdown")

    try:
        # Descargar la foto enviada en la mayor resolución
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        base64_image = base64.b64encode(photo_bytes).decode('utf-8')

        contexto = await obtener_diagnostico_completo(dias_historia=3)

        system_prompt = (
            "Sos un fisiólogo deportivo y especialista en biomecánica con visión por computadora.\n"
            "REGLA DE SEGURIDAD ABSOLUTA: Solo debes procesar imágenes que correspondan a ENTRENAMIENTOS, RELOJES DEPORTIVOS, APPS DE RUNNING/CICLISMO, PANTALLAS DE CINTAS O TABLAS DE MÉTRICAS.\n"
            "Si la imagen NO es de un entrenamiento (ej: una selfi, paisaje sin datos, comida, mascota), responde educadamente: '⚠️ Javii, solo puedo interpretar imágenes referidas a tus entrenamientos o pantallas de reloj deportivo.'\n\n"
            "FORMATO DE RESPUESTA TELEGRAM:\n"
            "1. PROHIBIDO usar caracteres como '##', '###', '=='. Usa solo negritas (*texto*) y emojis.\n"
            "2. Extrae las métricas de la foto (ritmo, cadencia, distancia, FC, etc.) y compáralas brevemente con su contexto reciente de Intervals.icu.\n"
            "3. Redondeá la cadencia a números enteros sin decimales."
        )

        user_content = [
            {
                "type": "text",
                "text": f"DATOS RECIENTES DE INTERVALS.ICU:\n{contexto}\n\nPor favor analiza esta foto de mi entrenamiento."
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}"
                }
            }
        ]

        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            model="llama-3.2-11b-vision-preview",
            temperature=0.2,
            max_tokens=1000
        )

        respuesta_ai = chat_completion.choices[0].message.content
        await thinking_msg.edit_text(respuesta_ai, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    except Exception as e:
        logging.error(f"Error procesando imagen con Groq Vision: {e}")
        await thinking_msg.edit_text(f"❌ *Error al analizar la imagen:* `{e}`", parse_mode="Markdown", reply_markup=main_menu_keyboard())

# ----------------------------------------------------------------------
# 9. MANEJADOR DE TEXTO CON IA (GROQ LLAMA 3.3 70B)
# ----------------------------------------------------------------------
async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = update.message.text

    if not groq_client:
        await update.message.reply_text("⚠️ *La IA no está configurada.* Falta `GROQ_API_KEY`.", parse_mode="Markdown")
        return

    thinking_msg = await update.message.reply_text("⚡ *Javii, procesando tu biomecánica y métricas...*", parse_mode="Markdown")

    try:
        contexto = await obtener_diagnostico_completo(dias_historia=7)

        system_instruction = (
            "Sos un fisiólogo del deporte, biomecánico experto en atletismo y coach de alto rendimiento.\n"
            "Tu rol es analizar los datos biomecánicos y de carga de Javii.\n\n"
            "REGLAS CRÍTICAS DE FORMATO PARA TELEGRAM:\n"
            "1. PROHIBIDO usar caracteres como '##', '###', '==' o '--'.\n"
            "2. Usa ÚNICAMENTE negritas (*texto*), viñetas con emojis o guiones.\n"
            "3. Redondeá siempre la cadencia a números enteros (ej. '79 ppm').\n"
            "4. Dirigite al atleta como Javii.\n\n"
            "ESTRUCTURA DE TU RESPUESTA:\n"
            "• Saludo breve a Javii.\n"
            "• 📊 *Diagnóstico de Métricas Clave*: Datos numéricos precisos del entreno.\n"
            "• 🔬 *Explicación Fisiológica / Biomecánica*: Análisis claro de eficiencia o fatiga.\n"
            "• 💡 *Pauta para el Próximo Entreno*: Consejo o sugerencia práctica accionable."
        )

        user_content = (
            f"DATOS DE INTERVALS.ICU:\n{contexto}\n\n"
            f"PREGUNTA DE JAVII: \"{user_prompt}\""
        )

        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            max_tokens=1200
        )

        respuesta_ai = chat_completion.choices[0].message.content

        try:
            await thinking_msg.edit_text(respuesta_ai, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        except Exception:
            await thinking_msg.edit_text(respuesta_ai, reply_markup=main_menu_keyboard())

    except Exception as e:
        logging.error(f"Error generando respuesta con Groq: {e}")
        await thinking_msg.edit_text(f"❌ *Error al procesar:* `{e}`", parse_mode="Markdown", reply_markup=main_menu_keyboard())

# ----------------------------------------------------------------------
# 10. ARRANQUE DEL BOT Y PLANIFICADOR
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("Error: Falta BOT_TOKEN.")

    threading.Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Planificador APScheduler para el Morning Briefing (07:30 AM Argentina)
    scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires")
    scheduler.add_job(
        enviar_morning_briefing,
        trigger="cron",
        hour=7,
        minute=30,
        args=[app]
    )
    scheduler.start()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_user_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))

    logging.info("Bot de Rendimiento en ejecución...")
    app.run_polling()