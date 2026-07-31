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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("MY_CHAT_ID")

TZ_AR = zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")

groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        logging.info("Cliente de Groq inicializado correctamente.")
    except Exception as e:
        logging.error(f"Error al inicializar cliente de Groq: {e}")

# Base de datos local para Zapatillas
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
            "title": {"display": True, "text": "PMC - Carga de Entrenamiento", "fontColor": "#ffffff"},
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
# 5. FUNCIONES DE MÉTRICAS
# ----------------------------------------------------------------------

import datetime

import datetime

async def obtener_dinamicas_biomecanica():
    now = datetime.datetime.now(TZ_AR)
    today_str = now.strftime("%Y-%m-%d")
    oldest_str = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    # Traemos las actividades de los últimos 30 días
    actividades, err = await fetch_intervals_data("activities", {
        "oldest": oldest_str,
        "newest": today_str
    })

    if err or not actividades or not isinstance(actividades, list):
        return "⚠️ No se encontraron actividades en los últimos 30 días."

    # ORDENAR POR FECHA: Nos aseguramos de tener de la más vieja a la más reciente
    actividades_ordenadas = sorted(
        actividades, 
        key=lambda x: x.get("start_date_local", "")
    )

    # Tomamos la ÚLTIMA (que es la más reciente de todas)
    act = actividades_ordenadas[-1]

    nombre = act.get("name", "Entrenamiento")
    fecha = act.get("start_date_local", "")[:10]
    
    # Cadencia corregida a SPM (pasos por minuto)
    cad_raw = act.get("average_cadence")
    if isinstance(cad_raw, (int, float)):
        cad = int(round(cad_raw * 2)) if cad_raw < 100 else int(round(cad_raw))
    else:
        cad = "N/D"
        
    stride_len = round(act.get("stride_length", 0), 2) if act.get("stride_length") else "N/D"
    fc_avg = act.get("average_heartrate", "N/D")
    fc_max = act.get("max_heartrate", "N/D")
    dist_km = round(act.get("distance", 0) / 1000, 2)
    moving_time_min = round(act.get("moving_time", 0) / 60, 1)
    
    speed_ms = act.get("average_speed", 0)
    pace_str = "N/D"
    if speed_ms > 0:
        pace_sec = 1000 / speed_ms
        m, s = divmod(int(pace_sec), 60)
        pace_str = f"{m}:{s:02d} min/km"

    return (
        f"🏃‍♂️ *ÚLTIMA CARRERA REGISTRADA*\n"
        f"📌 *{nombre.upper()}* (`{fecha}`)\n"
        f"📏 *Distancia:* `{dist_km} km` | ⏱️ *Tiempo:* `{moving_time_min} min` | ⏱️ *Ritmo:* `{pace_str}`\n"
        f"📱 *Dispositivo:* Garmin Forerunner 55\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🦶 *EFICIENCIA DE ZANCADA & FC:*\n"
        f"• 🔄 *Cadencia Media:* `{cad} ppm`\n"
        f"• 📏 *Longitud de Zancada:* `{stride_len} m`\n"
        f"• ❤️ *FC Media / Máx:* `{fc_avg} ppm` / `{fc_max} ppm`"
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
            fechas.append(item.get("id", "")[5:])
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

    url_chart = generar_url_grafico_pmc(fechas, ctl_list, atl_list, tsb_list) if fechas else None

    texto = (
        f"📈 *ESTADO DE CARGA Y FORMA (PMC)*\n"
        f"📅 Fecha: `{today_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🛡️ *Fitness (CTL):* `{ctl_actual}`\n"
        f"🔥 *Fatiga (ATL):* `{atl_actual}`\n"
        f"⚖️ *Forma (TSB):* `{tsb_actual}`\n\n"
        f"🎯 *DIAGNÓSTICO RÁPIDO:*\n"
        f"└ {estado_tsb}\n\n"
        f"📌 *¿CÓMO LEER EL GRÁFICO?*\n"
        f"• 🔷 *Línea Azul (CTL - Fitness):* Acumulación de carga a largo plazo (42 días). Cuanto más alta, mayor es tu base física.\n"
        f"• 🟧 *Línea Naranja (ATL - Fatiga):* Carga a corto plazo (últimos 7 días). Sube rápido cuando entrenás duro.\n"
        f"• 🟢 *Línea Verde (TSB - Forma):* Tu frescura actual (CTL - ATL). Si está muy negativa estás cansado; si está positiva, estás descansado y listo para competir."
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
        if isinstance(cad_raw, (int, float)):
            # Si Intervals manda cadencia de 1 sola pierna (<100), la duplicamos a SPM totales
            cad = int(round(cad_raw * 2)) if cad_raw < 100 else int(round(cad_raw))
        else:
            cad = "N/D"
        
        stride = round(act.get("stride_length", 0), 2) if act.get("stride_length") else "N/D"

        resumen.append(
            f"📅 *{fecha}* ➔ *{nombre}*\n"
            f"  ├ 📏 `{dist} km` | ⏱️ `{dur} min` | ⚡ `TSS {tss}`\n"
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
# 6. MORNING BRIEFING
# ----------------------------------------------------------------------
async def enviar_morning_briefing(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        logging.warning("No se ejecutó Morning Briefing: falta CHAT_ID en variables de entorno.")
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
        f"💡 *CONSEJO DEL DÍA:* Revisa tus zonas de FC y mantendé una hidratación adecuada."
    )

    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=texto_briefing, parse_mode="Markdown")
        logging.info("Morning Briefing enviado con éxito.")
    except Exception as e:
        logging.error(f"Error enviando Morning Briefing: {e}")

# ----------------------------------------------------------------------
# 7. MENÚ Y BOTONES INTERACTIVOS
# ----------------------------------------------------------------------
def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🏃 Entreno Hoy", callback_data="entrenamiento_hoy"),
            InlineKeyboardButton("📈 PMC & Carga", callback_data="carga")
        ],
        [
            InlineKeyboardButton("🦶 Biomecánica", callback_data="biomecanica"),
            InlineKeyboardButton("🩺 Sueño & HRV", callback_data="salud_sueno")
        ],
        [
            InlineKeyboardButton("👟 Zapatillas", callback_data="zapatillas"),
            InlineKeyboardButton("🌤️ Clima Entreno", callback_data="clima")
        ],
        [
            InlineKeyboardButton("⚡ Diagnóstico IA", callback_data="diagnostico_completo")
        ],
        [
            InlineKeyboardButton("🏠 Menú Principal", callback_data="menu_principal")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🚀 *¡HOLA JAVII! QUE NECESITAS SABER HOY?*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• Tocá cualquier botón para consultar métricas o ver tu gráfico PMC.\n"
        "• Mandame una *foto o captura* de tu entrenamiento para analizarla profundamente.\n"
        "• O escribime en texto libre tu consulta."
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

    elif data == "clima":
        await query.edit_message_text("🔍 *Consultando clima actual de Rafaela...*", parse_mode="Markdown")
        res = await obtener_clima_rafaela()
        texto_clima = f"🌤️ *CONDICIONES PARA ENTRENAR EN RAFAELA*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n{res}"
        await query.edit_message_text(texto_clima, parse_mode="Markdown", reply_markup=main_menu_keyboard())

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
# 8. MANEJADOR DE IMÁGENES CON IA (VISIÓN)
# ----------------------------------------------------------------------
async def handle_user_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not groq_client:
        await update.message.reply_text("⚠️ *La IA no está configurada.* Falta `GROQ_API_KEY`.", parse_mode="Markdown")
        return

    thinking_msg = await update.message.reply_text("👁️ *Javii, analizando la foto de tu entrenamiento...*", parse_mode="Markdown")

    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        base64_image = base64.b64encode(photo_bytes).decode('utf-8')

        contexto = await obtener_diagnostico_completo(dias_historia=3)

        system_prompt = (
            "Sos un fisiólogo deportivo y especialista en biomecánica con visión por computadora.\n"
            "REGLA ABSOLUTA: Solo procesa imágenes que correspondan a ENTRENAMIENTOS, RELOJES DEPORTIVOS, APPS DE RUNNING O TABLAS DE MÉTRICAS.\n"
            "Si la imagen NO es de entrenamiento, responde: '⚠️ Javii, solo puedo interpretar imágenes referidas a tus entrenamientos o relojes deportivos.'\n\n"
            "FORMATO:\n"
            "1. PROHIBIDO usar '##', '###' o '=='. Usa negritas (*texto*) y emojis.\n"
            "2. Extrae las métricas visibles y analízalas."
        )

        user_content = [
            {
                "type": "text",
                "text": f"DATOS RECIENTES:\n{contexto}\n\nAnaliza la foto de mi entrenamiento."
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
            model="llama-3.2-90b-vision-preview",
            temperature=0.2,
            max_tokens=1000
        )

        respuesta_ai = chat_completion.choices[0].message.content
        await thinking_msg.edit_text(respuesta_ai, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    except Exception as e:
        logging.error(f"Error procesando imagen: {e}")
        await thinking_msg.edit_text(f"❌ *Error al analizar la imagen:* `{e}`", parse_mode="Markdown", reply_markup=main_menu_keyboard())


async def obtener_clima_actual():
    # Coordenadas de Rafaela, Santa Fe (-31.25, -61.48)
    url = "https://api.open-meteo.com/v1/forecast?latitude=-31.25&longitude=-61.48&current_weather=true&hourly=relativehumidity_2m"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                current = data.get("current_weather", {})
                temp = current.get("temperature", "N/D")
                wind = current.get("windspeed", "N/D")
                
                return (
                    f"🌤️ *CLIMA ACTUAL PARA ENTRENAR*\n"
                    f"📍 *Rafaela, Santa Fe*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🌡️ *Temperatura:* `{temp} °C`\n"
                    f"💨 *Viento:* `{wind} km/h`\n\n"
                    f"💡 *Recomendación:* " + (
                        "Ideal para salir a sumar kms." if temp < 25 else "Hidratate bien por el calor."
                    )
                )
    except Exception as e:
        logging.error(f"Error obteniendo clima: {e}")
    
    return "⚠️ No se pudo obtener la información del clima en este momento."

# 9. MANEJADOR DE TEXTO CON IA (CONVERSACIONAL Y NATURAL)
# ----------------------------------------------------------------------
async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = update.message.text

    if not groq_client:
        await update.message.reply_text("⚠️ *La IA no está configurada.* Falta `GROQ_API_KEY`.", parse_mode="Markdown")
        return

    thinking_msg = await update.message.reply_text("⚡ *Procesando...*", parse_mode="Markdown")

    try:
        # Recuperamos solo el último entrenamiento/datos para no saturar al modelo de contexto innecesario
        contexto = await obtener_diagnostico_completo(dias_historia=3)

        system_instruction = (
            "Sos el asistente personal de entrenamiento de Javii.\n"
            "Tu objetivo es ser conciso, directo, conversacional y natural.\n\n"
            "REGLAS CRÍTICAS DE RESPUESTA:\n"
            "1. Responde DIRECTAMENTE a lo que Javii te pregunta. Si te hace una pregunta cerrada (ej: '¿Podés leer una foto?'), responde de forma corta, clara y amigable (ej: '¡Sí, Javii! Mandame la captura o foto del entrenamiento y te la analizo al toque.').\n"
            "2. NO generes análisis largos ni informes de la semana a menos que Javii te pida explícitamente analizar sus datos o su entrenamiento.\n"
            "3. PROHIBIDO usar caracteres como '##', '###', '==' o '--'. Usa negritas (*texto*) y emojis cuando sea conveniente.\n"
            "4. Dirigite siempre como Javii."
        )

        user_content = (
            f"DATOS RECIENTES DE CONTEXTO (Usar solo si es relevante a la pregunta):\n{contexto}\n\n"
            f"MENSAJE DE JAVII: \"{user_prompt}\""
        )

        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.4,
            max_tokens=600
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
# 10. INICIALIZACIÓN DE TAREAS Y ARRANQUE
# ----------------------------------------------------------------------
async def post_init(application):
    """Inicializa la tarea programada una vez que la app levantó correctamente."""
    if CHAT_ID:
        scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires")
        scheduler.add_job(
            enviar_morning_briefing,
            trigger="cron",
            hour=7,
            minute=30,
            args=[application]
        )
        scheduler.start()
        logging.info("Planificador Morning Briefing activo (07:30 AM AR).")

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("Error: Falta BOT_TOKEN.")

    threading.Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_user_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))

    logging.info("Bot de Rendimiento en ejecución...")
    app.run_polling()