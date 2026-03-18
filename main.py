#!/usr/bin/env python3
"""Maya - Asistente de Voz para Personas Mayores.

Entry point: wake word → record → STT → LLM → TTS → display loop.
"""

import os
import sys
import signal
import threading
import time
import re as _re
import logging
import logging.handlers
import tempfile
from datetime import datetime, timedelta
import yaml

# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# --- Single instance lock ---
LOCK_FILE = os.path.join(BASE_DIR, "data", ".maya.lock")

def _acquire_lock():
    """Ensure only one Maya instance runs. Kill old if needed."""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    if os.path.isfile(LOCK_FILE):
        try:
            old_pid = int(open(LOCK_FILE).read().strip())
            if old_pid != os.getpid():
                os.kill(old_pid, signal.SIGTERM)
                time.sleep(1)
                try:
                    os.kill(old_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

def _release_lock():
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass

_acquire_lock()

# --- Logging ---
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
log_handler = logging.handlers.RotatingFileHandler(
    os.path.join(BASE_DIR, "logs", "assistant.log"),
    maxBytes=2 * 1024 * 1024,
    backupCount=3,
)
log_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[log_handler, console_handler])
log = logging.getLogger("maya")

# --- Load config ---
with open(os.path.join(BASE_DIR, "config.yaml")) as f:
    config = yaml.safe_load(f)

# --- Imports (after config so we can fail early) ---
from audio import bt_connect, record_until_silence, save_wav, play_audio, generate_sounds
from wakeword import WakeWordDetector
from stt import STT
from tts import TTS
from llm import LLM, parse_actions
from db import Database
from speaker_id import SpeakerID
from telegram_bot import TelegramBot
from display import Display
from weather import Weather
from admin import start_admin
from search import Search
from radio import Radio

# --- Globals ---
running = True
wakeword_detector = None


def cleanup(*_):
    """SIGTERM/SIGINT handler."""
    global running
    log.info("Señal recibida, cerrando...")
    running = False
    if wakeword_detector:
        wakeword_detector.stop()


signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)


def resolve_relative_time(time_str: str) -> str:
    """Convert relative time formats to absolute HH:MM.

    Supports: +10m, +1h, +1h30m, '14:30' (passthrough),
    and Spanish: '10 minutos', 'media hora', 'una hora'.
    """
    time_str = time_str.strip()

    # Already absolute HH:MM
    if _re.match(r"^\d{1,2}:\d{2}$", time_str):
        return time_str

    now = datetime.now()
    delta = None

    # +Xm, +Xh, +XhYm
    m = _re.match(r"^\+?(\d+)h(\d+)m$", time_str, _re.IGNORECASE)
    if m:
        delta = timedelta(hours=int(m.group(1)), minutes=int(m.group(2)))
    if delta is None:
        m = _re.match(r"^\+(\d+)h$", time_str, _re.IGNORECASE)
        if m:
            delta = timedelta(hours=int(m.group(1)))
    if delta is None:
        m = _re.match(r"^\+(\d+)m$", time_str, _re.IGNORECASE)
        if m:
            delta = timedelta(minutes=int(m.group(1)))

    # Spanish fallback
    if delta is None:
        lower = time_str.lower()
        if "media hora" in lower:
            delta = timedelta(minutes=30)
        elif "una hora" in lower:
            delta = timedelta(hours=1)
        else:
            m = _re.search(r"(\d+)\s*minuto", lower)
            if m:
                delta = timedelta(minutes=int(m.group(1)))
            else:
                m = _re.search(r"(\d+)\s*hora", lower)
                if m:
                    delta = timedelta(hours=int(m.group(1)))

    if delta:
        target = now + delta
        return target.strftime("%H:%M")

    # Could not parse, return as-is
    return time_str


_MEAL_TIMES = {"desayuno": "08:00", "comida": "14:00", "cena": "20:00",
               "almuerzo": "14:00", "merienda": "17:00"}


def parse_medication_schedule(schedule: str) -> list[str]:
    """Parse a medication schedule string into a list of HH:MM times.

    Supports:
      - Explicit times: "08:00, 14:00, 20:00"
      - Intervals: "cada 8 horas" (anchored at 08:00)
      - Meal names: "desayuno, comida, cena"
      - Frequency: "dos veces al dia" / "tres veces al dia"
    """
    if not schedule:
        return []

    schedule = schedule.strip().lower()

    # Explicit times: "08:00, 14:00, 20:00" or "8:00 y 20:00"
    explicit = _re.findall(r"\b(\d{1,2}:\d{2})\b", schedule)
    if explicit:
        return sorted(set(t.zfill(5) for t in explicit))

    # Interval: "cada X horas"
    m = _re.search(r"cada\s+(\d+)\s*hora", schedule)
    if m:
        interval = int(m.group(1))
        if 1 <= interval <= 24:
            times = []
            h = 8  # anchor
            for _ in range(24 // interval):
                times.append(f"{h:02d}:00")
                h = (h + interval) % 24
            return sorted(set(times))

    # Meal names
    found_meals = [_MEAL_TIMES[k] for k in _MEAL_TIMES if k in schedule]
    if found_meals:
        return sorted(set(found_meals))

    # Frequency: "X veces al dia"
    freq_map = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "1": 1, "2": 2, "3": 3, "4": 4}
    m = _re.search(r"(\w+)\s+vec(?:es|e)\s+al\s+d[ií]a", schedule)
    if m:
        n = freq_map.get(m.group(1), 0)
        if n == 1:
            return ["08:00"]
        elif n == 2:
            return ["08:00", "20:00"]
        elif n == 3:
            return ["08:00", "14:00", "20:00"]
        elif n == 4:
            return ["08:00", "12:00", "16:00", "20:00"]

    return []


def _handle_treatment_query(action: dict, user_id: str, db: Database,
                            telegram: TelegramBot, results: list[str]):
    """Handle CONSULTA_TRATAMIENTO action: lookup dose, log measurement, send alerts."""
    measurement = action.get("measurement", "")
    try:
        value = float(action.get("value", "0"))
    except (ValueError, TypeError):
        results.append("No pude interpretar el valor de la medicion")
        return

    match = db.lookup_treatment_dose(user_id, measurement, value)
    if not match:
        results.append(f"No hay esquema configurado para {measurement}")
        return

    schema_id = match["schema_id"]
    dose = match["dose"]
    dose_unit = match["dose_unit"]
    schema_name = match["schema_name"]
    m_unit = match["measurement_unit"]

    # Check if out of alert range
    alert_low = match.get("alert_low")
    alert_high = match.get("alert_high")
    is_alert = False
    if (alert_low is not None and value < alert_low) or \
       (alert_high is not None and value > alert_high):
        is_alert = True

    # Log the measurement
    db.log_measurement(user_id, schema_id, value,
                       dose_given=dose, dose_unit=dose_unit,
                       alert_sent=1 if is_alert else 0)

    results.append(f"Segun tu esquema de {schema_name}, con {value}{m_unit} te tocan {dose} {dose_unit}")

    # Send Telegram alerts if out of range
    if is_alert and match.get("alert_contacts"):
        user = db.get_user(user_id)
        user_name = user["real_name"] if user else user_id

        consecutive = db.count_consecutive_alerts(user_id, schema_id)

        contact_ids = [c.strip() for c in match["alert_contacts"].split(",") if c.strip()]

        if consecutive >= 2:
            alert_msg = (
                f"🔴 {user_name} registro {match['measurement_name']} fuera de rango "
                f"por {consecutive}a vez consecutiva: {value} {m_unit}. "
                f"Se le indicaron {dose} {dose_unit} de {schema_name}. "
                f"Consideren comunicarse."
            )
        else:
            alert_msg = (
                f"⚠️ {user_name} registro {match['measurement_name']} de {value} {m_unit} "
                f"(fuera del rango normal). "
                f"Se le indicaron {dose} {dose_unit} segun su esquema de {schema_name}."
            )

        for contact_id_str in contact_ids:
            try:
                contact_id = int(contact_id_str)
                # Look up contact to find telegram_chat_id
                contacts = db.get_contacts(user_id)
                contact = next((c for c in contacts if c["id"] == contact_id), None)
                if contact and contact.get("telegram_chat_id"):
                    telegram.send_to_chat_id(contact["telegram_chat_id"], alert_msg)
                    log.info("Alerta enviada a %s (chat_id=%s)", contact["name"], contact["telegram_chat_id"])
            except (ValueError, TypeError):
                pass

        results.append(f"Alerta enviada a {len(contact_ids)} contacto(s)")


def execute_actions(actions: list[dict], user_id: str, db: Database,
                    telegram: TelegramBot, llm=None, search=None,
                    radio=None) -> list[str]:
    """Execute parsed actions from LLM response. Returns list of result messages."""
    results = []
    for action in actions:
        atype = action.get("type", "")
        try:
            if atype == "TELEGRAM":
                ok = telegram.send_message(action["recipient"], action["message"])
                if ok:
                    results.append(f"Mensaje enviado a {action['recipient']}")
                else:
                    results.append(f"No pude enviar el mensaje a {action['recipient']}")

            elif atype == "MEDICAMENTO":
                db.add_medication(
                    user_id, action["name"],
                    action.get("dosage", ""), action.get("schedule", ""),
                )
                results.append(f"Medicamento '{action['name']}' registrado")

            elif atype == "RECORDATORIO":
                resolved_time = resolve_relative_time(action["time"])
                db.add_reminder(user_id, action["text"], resolved_time)
                results.append(f"Recordatorio creado para las {resolved_time}")

            elif atype == "CONTACTO":
                db.add_contact(
                    user_id, action["name"],
                    phone=action.get("phone", ""),
                    relationship=action.get("relationship", ""),
                )
                results.append(f"Contacto '{action['name']}' guardado")

            elif atype == "CONFIRMAR_MEDICAMENTO":
                ok = db.confirm_medication_by_name(action["name"], user_id)
                if ok:
                    results.append(f"Toma de '{action['name']}' registrada")
                else:
                    results.append(f"No encontré el medicamento '{action['name']}'")

            elif atype == "MEMORIA":
                cat = action["category"]
                content = action["content"]
                if llm:
                    _smart_save_memory(
                        llm, db, user_id, cat, content,
                        telegram=telegram,
                    )
                else:
                    db.save_memory(user_id, cat, content)
                results.append(f"Memoria guardada: {content}")

            elif atype == "MENSAJE_PENDIENTE":
                message = action.get("message", "")
                if message and db:
                    sent = False
                    user_data = db.get_user(user_id)
                    user_name = user_data["real_name"] if user_data else user_id

                    # First: try sending to the user's own Telegram
                    if user_data and user_data.get("telegram_chat_id"):
                        telegram.send_to_chat_id(
                            user_data["telegram_chat_id"],
                            f"{message}"
                        )
                        results.append("Mensaje enviado a tu Telegram")
                        sent = True

                    # Also send to contacts with telegram_chat_id
                    contacts = db.get_contacts(user_id)
                    for c in contacts:
                        if c.get("telegram_chat_id"):
                            telegram.send_to_chat_id(
                                c["telegram_chat_id"],
                                f"Mensaje de {user_name}: {message}"
                            )
                            results.append(f"Mensaje enviado a {c['name']}")
                            sent = True

                    if not sent:
                        results.append("No hay Telegram configurado para enviar el mensaje")

            elif atype == "RADIO":
                station = action.get("station", "")
                if radio:
                    if station.lower() in ("apagar", "parar", "stop", "off"):
                        radio.stop()
                        results.append("Radio apagada")
                        results.append("__RADIO_OFF__")
                    else:
                        name = radio.play(station)
                        if name:
                            results.append(f"Poniendo {name}")
                            results.append(f"__RADIO_ON__:{name}")
                        else:
                            available = ", ".join(s["key"] for s in radio.list_stations())
                            results.append(f"No encontre esa estacion. Disponibles: {available}")

            elif atype == "BUSCAR":
                query = action.get("query", "")
                if search and query:
                    answer = search.query(query)
                    if answer:
                        results.append(f"__SEARCH__:{answer}")
                    else:
                        results.append("No pude encontrar informacion sobre eso")
                else:
                    results.append("Busqueda no disponible")

            elif atype == "CONSULTA_TRATAMIENTO":
                _handle_treatment_query(action, user_id, db, telegram, results)

        except Exception as e:
            log.error("Error ejecutando accion %s: %s", atype, e)
            results.append(f"Error al ejecutar {atype}")

    return results


_DAY_NAMES = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]


def _med_applies_today(med) -> bool:
    """Check if a medication applies today based on days_of_week field."""
    days = (med.get("days_of_week") or "").strip().lower()
    if not days:
        return True  # empty = every day
    if days == "sos":
        return False  # as-needed, never auto-remind
    today = _DAY_NAMES[datetime.now().weekday()]
    return today in [d.strip() for d in days.split(",")]


def _check_medication_reminders(db: Database, tts: TTS, display: Display):
    """Check all users' medication schedules and announce due medications."""
    now = datetime.now()
    now_minutes = now.hour * 60 + now.minute

    for user in db.get_users():
        user_id = user["id"]
        user_name = user["real_name"]
        meds = db.get_medications(user_id)
        for med in meds:
            if not _med_applies_today(med):
                continue
            times = parse_medication_schedule(med.get("schedule", ""))
            for t in times:
                try:
                    h, m = int(t.split(":")[0]), int(t.split(":")[1])
                except (ValueError, IndexError):
                    continue
                slot_minutes = h * 60 + m
                diff = now_minutes - slot_minutes
                # 0-15 minutes after scheduled time
                if 0 <= diff <= 15:
                    if not db.is_medication_taken_today(med["id"], user_id, t):
                        msg = f"{user_name}, ya es hora de su {med['name']}"
                        if med.get("dosage"):
                            msg += f", {med['dosage']}"
                        log.info("Recordatorio medicamento: %s", msg)
                        display.set_status("Medicamento", "#e94560")
                        display.set_response(msg)
                        audio_path = tts.speak(msg)
                        if audio_path:
                            play_audio(audio_path)
                            os.unlink(audio_path)
                        time.sleep(1)


def generate_weekly_report(db: Database, user_id: str) -> str:
    """Generate a weekly health report for a user (HTML for Telegram)."""
    user = db.get_user(user_id)
    name = user["real_name"] if user else user_id
    now = datetime.now()
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    lines = [f"<b>Reporte semanal de {name}</b>",
             f"Periodo: {week_ago} al {now.strftime('%Y-%m-%d')}\n"]

    # Medications taken
    med_log = db.get_medication_log(user_id, date=None)
    week_entries = [m for m in med_log if m["taken_at"] >= week_ago]
    meds = db.get_medications(user_id)
    if meds:
        lines.append("<b>Medicamentos:</b>")
        for med in meds:
            count = sum(1 for e in week_entries if e["medication_id"] == med["id"])
            lines.append(f"  - {med['name']}: {count} tomas registradas")
    else:
        lines.append("Sin medicamentos registrados.")

    # Measurements (treatment schemas)
    schemas = db.get_treatment_schemas(user_id)
    for schema in schemas:
        measurements = db.get_measurement_log(user_id, schema_id=schema["id"], limit=50)
        week_meas = [m for m in measurements if m["measured_at"] >= week_ago]
        if week_meas:
            values = [m["measurement_value"] for m in week_meas]
            alerts = sum(1 for m in week_meas if m.get("alert_sent"))
            lines.append(f"\n<b>{schema['name']}:</b>")
            lines.append(f"  {len(week_meas)} mediciones, "
                         f"rango {min(values):.0f}-{max(values):.0f} {schema['measurement_unit']}")
            if alerts:
                lines.append(f"  {alerts} alerta(s) fuera de rango")

    # Conversations count
    convos = db.get_recent_conversations(user_id, limit=200)
    week_convos = [c for c in convos if c.get("created_at", "") >= week_ago]
    user_msgs = sum(1 for c in week_convos if c["role"] == "user")
    lines.append(f"\n<b>Interacciones:</b> {user_msgs} conversaciones esta semana")

    # Reminders
    reminders = db.get_all_active_reminders()
    user_rems = [r for r in reminders if r["user_id"] == user_id]
    if user_rems:
        lines.append(f"<b>Recordatorios activos:</b> {len(user_rems)}")

    return "\n".join(lines)


def _send_weekly_reports(db: Database, telegram: "TelegramBot"):
    """Send weekly health reports to all contacts. Called Sunday at 10am."""
    now = datetime.now()
    if now.weekday() != 6 or now.hour != 10 or now.minute > 1:
        return

    for user in db.get_users():
        user_id = user["id"]
        report = generate_weekly_report(db, user_id)
        # Send to emergency contacts only
        contacts = db.get_emergency_contacts(user_id)
        for c in contacts:
            telegram.send_to_chat_id(c["telegram_chat_id"], report)
            log.info("Reporte semanal de %s enviado a %s", user_id, c["name"])
    log.info("Reportes semanales enviados")


def _check_user_activity(db: Database, tts: TTS, display: Display):
    """Check if any user hasn't interacted in a while. Proactive wellness check."""
    now = datetime.now()
    # Only during daytime (9am - 8pm)
    if now.hour < 9 or now.hour >= 20:
        return

    for user in db.get_users():
        user_id = user["id"]
        user_name = user["real_name"]
        convos = db.get_recent_conversations(user_id, limit=1)
        if not convos:
            continue
        last = convos[0]
        last_time = last.get("created_at", "")
        if not last_time:
            continue
        try:
            last_dt = datetime.fromisoformat(last_time)
        except (ValueError, TypeError):
            continue
        hours_since = (now - last_dt).total_seconds() / 3600
        # If more than 8 hours since last interaction during daytime
        if hours_since >= 8:
            # Only nudge once per day — check if we already nudged today
            today = now.strftime("%Y-%m-%d")
            recent = db.get_recent_conversations(user_id, limit=5)
            already_nudged = any(
                "__WELLNESS__" in c.get("content", "") and
                c.get("created_at", "").startswith(today)
                for c in recent if c["role"] == "assistant"
            )
            if not already_nudged:
                msg = f"{user_name}, lleva un rato sin platicar conmigo. Todo bien? Aqui estoy si necesita algo."
                log.info("Wellness check: %s (%.1f horas sin interaccion)", user_name, hours_since)
                display.set_status("Saludando", "#4ecca3")
                display.set_response(msg)
                audio_path = tts.speak(msg)
                if audio_path:
                    play_audio(audio_path)
                    os.unlink(audio_path)
                db.save_conversation(user_id, "assistant", f"__WELLNESS__ {msg}")
                time.sleep(2)  # pause between users


def reminder_thread(db: Database, tts: TTS, display: Display, telegram=None):
    """Background thread that checks for due reminders, medications, activity, and reports every 30s."""
    while running:
        try:
            due = db.get_due_reminders()
            for rem in due:
                log.info("Recordatorio: %s para %s", rem["text"], rem["real_name"])
                display.set_status("Recordatorio", "#e94560")
                msg = f"{rem['real_name']}, recordatorio: {rem['text']}"
                display.set_response(msg)
                audio_path = tts.speak(msg)
                if audio_path:
                    play_audio(audio_path)
                    os.unlink(audio_path)
                db.mark_reminder_triggered(rem["id"])
        except Exception as e:
            log.error("Error en reminder thread: %s", e)

        try:
            _check_medication_reminders(db, tts, display)
        except Exception as e:
            log.error("Error en medication reminders: %s", e)

        try:
            _check_user_activity(db, tts, display)
        except Exception as e:
            log.error("Error en activity check: %s", e)

        if telegram:
            try:
                _send_weekly_reports(db, telegram)
            except Exception as e:
                log.error("Error en weekly reports: %s", e)

        # Sleep in small increments so we can exit quickly
        for _ in range(30):
            if not running:
                return
            time.sleep(1)


def update_reminders_display(db: Database, display: Display):
    """Update the reminders section on display."""
    try:
        reminders = db.get_all_active_reminders()
        if reminders:
            lines = []
            for r in reminders[:5]:
                lines.append(f"  {r['remind_at']} - {r['text']} ({r['real_name']})")
            display.set_reminders("\n".join(lines))
        else:
            display.set_reminders("Sin recordatorios pendientes")
    except Exception:
        pass


def _llm_quick(llm, system: str, prompt: str, max_tokens: int = 150) -> str:
    """Quick LLM call for extraction/consolidation tasks."""
    try:
        if llm.provider == "synapse":
            import httpx
            resp = httpx.post(
                f"{llm._synapse_base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {llm._synapse_api_key}",
                         "Content-Type": "application/json"},
                json={"model": llm.model, "max_tokens": max_tokens,
                      "messages": [
                          {"role": "system", "content": system},
                          {"role": "user", "content": prompt}]},
                timeout=15.0,
            )
            return resp.json()["choices"][0]["message"]["content"].strip()
        elif llm.provider == "claude":
            result = llm._client.messages.create(
                model=llm.model, max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return result.content[0].text.strip()
        elif llm.provider == "openai":
            import httpx
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {llm._openai_api_key}",
                         "Content-Type": "application/json"},
                json={"model": llm.model, "max_tokens": max_tokens,
                      "messages": [
                          {"role": "system", "content": system},
                          {"role": "user", "content": prompt}]},
                timeout=15.0,
            )
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("Error en LLM quick: %s", e)
    return ""


def _extract_with_llm(llm, question: str, raw_answer: str) -> str:
    """Use LLM to extract clean info from a raw spoken answer."""
    prompt = (
        f"El usuario respondio a la pregunta: '{question}'\n"
        f"Su respuesta fue: '{raw_answer}'\n\n"
        "Extrae SOLO la informacion relevante de la respuesta, limpia y concisa. "
        "Por ejemplo si la pregunta fue 'como te gustaria que te llamara' y "
        "respondio 'dime Juanito', extrae solo 'Juanito'. "
        "Si la respuesta es vaga como 'todo eso' o 'me parece bien', responde: TODO. "
        "Responde UNICAMENTE con el dato extraido, sin explicaciones ni frases adicionales."
    )
    result = _llm_quick(llm,
                        "Eres un extractor de datos. Responde con maximo 10 palabras.",
                        prompt, max_tokens=50)
    return result if result and result != "TODO" else raw_answer


HEALTH_CATEGORIES = {"salud", "medicamento", "enfermedad", "alergia", "condicion",
                     "tratamiento", "diagnostico"}

# --- Mood detection ---
_mood_tracker: dict[str, list[str]] = {}  # user_id -> last N moods


def analyze_mood(audio: "np.ndarray", text: str, sample_rate: int = 16000) -> str:
    """Analyze mood from audio features and text. Returns mood label."""
    import numpy as np

    mood = "normal"

    # Audio features
    audio_flat = audio.flatten().astype(np.float32)
    rms = np.sqrt(np.mean(audio_flat ** 2))
    duration = len(audio_flat) / sample_rate

    # Very quiet + short = possibly low energy / sad
    if rms < 200 and duration < 2.0:
        mood = "bajo"

    # Text analysis (simple keyword matching)
    lower = text.lower() if text else ""
    sad_words = {"triste", "mal", "solo", "sola", "cansado", "cansada", "dolor",
                 "no puedo", "no quiero", "aburrido", "aburrida", "llore", "llorar",
                 "extraño", "miedo", "preocupado", "preocupada", "deprimido", "deprimida"}
    anxious_words = {"nervioso", "nerviosa", "ansiedad", "angustia", "asustado",
                     "asustada", "no duermo", "insomnio", "temblor", "mareo"}
    happy_words = {"bien", "contento", "contenta", "feliz", "alegre", "bonito",
                   "excelente", "perfecto", "genial", "maravilloso"}

    if any(w in lower for w in sad_words):
        mood = "triste"
    elif any(w in lower for w in anxious_words):
        mood = "ansioso"
    elif any(w in lower for w in happy_words) and mood == "normal":
        mood = "contento"

    return mood


def _track_mood(user_id: str, mood: str, db: Database, telegram: "TelegramBot"):
    """Track mood over time. Alert family if concerning pattern detected."""
    if user_id not in _mood_tracker:
        _mood_tracker[user_id] = []

    _mood_tracker[user_id].append(mood)
    # Keep last 5
    _mood_tracker[user_id] = _mood_tracker[user_id][-5:]

    recent = _mood_tracker[user_id]
    concerning = {"triste", "bajo", "ansioso"}

    # Alert if 3+ of last 5 are concerning
    concerning_count = sum(1 for m in recent if m in concerning)
    if concerning_count >= 3:
        user = db.get_user(user_id)
        name = user["real_name"] if user else user_id
        mood_str = ", ".join(recent[-3:])
        alert = (f"Aviso sobre {name}: sus ultimas interacciones muestran "
                 f"un patron de animo bajo ({mood_str}). "
                 f"Podria ser bueno comunicarse con {'ella' if name.endswith('a') else 'el'}.")
        contacts = db.get_emergency_contacts(user_id)
        for c in contacts:
            telegram.send_to_chat_id(c["telegram_chat_id"], alert)
        log.info("Alerta de animo enviada para %s: %s", name, mood_str)
        # Reset tracker to avoid repeated alerts
        _mood_tracker[user_id] = []


def _smart_save_memory(llm, db, user_id: str, category: str, new_content: str,
                       telegram=None):
    """Save memory with LLM-powered dedup and contradiction handling.

    On contradiction in health categories: notifies admin via Telegram.
    On other contradictions: merges keeping newer as truth.
    """
    existing = db.get_memories(user_id, category=category, limit=50)

    if not existing:
        db.save_memory(user_id, category, new_content)
        return

    existing_list = "\n".join(
        f"  [{m['id']}] {m['content']}" for m in existing
    )
    prompt = (
        f"Memorias existentes en categoria '{category}':\n{existing_list}\n\n"
        f"Nueva memoria a guardar: '{new_content}'\n\n"
        "Analiza si la nueva memoria:\n"
        "1. DUPLICA una existente (dice lo mismo) -> responde: DUPLICA:id\n"
        "2. CONTRADICE una existente (info opuesta) -> responde: CONTRADICE:id\n"
        "3. ACTUALIZA una existente (info mas reciente/completa) -> responde: ACTUALIZA:id\n"
        "4. Es informacion NUEVA -> responde: NUEVA\n\n"
        "Responde SOLO con una de las opciones."
    )

    result = _llm_quick(llm,
                        "Analiza memorias. Responde solo DUPLICA:id, CONTRADICE:id, ACTUALIZA:id o NUEVA.",
                        prompt, max_tokens=30)

    log.info("Memoria check: '%s' -> %s", new_content, result)

    if result.startswith("DUPLICA:"):
        log.info("Memoria duplicada, no se guarda: %s", new_content)
        return

    if result.startswith("ACTUALIZA:"):
        try:
            old_id = int(result.split(":")[1].strip())
            db.delete_memory(old_id)
            log.info("Memoria %d actualizada por: %s", old_id, new_content)
        except (ValueError, IndexError):
            pass
        db.save_memory(user_id, category, new_content)
        return

    is_health = category.lower() in HEALTH_CATEGORIES

    if result.startswith("CONTRADICE:"):
        try:
            old_id = int(result.split(":")[1].strip())
            old_mem = next((m for m in existing if m["id"] == old_id), None)
            if old_mem:
                # Health-critical: notify admin, save new but keep old marked
                if is_health and telegram:
                    user = db.get_user(user_id)
                    u_name = user["name"] if user else user_id
                    alert_msg = (
                        f"\u26A0\uFE0F Contradiccion de salud ({u_name}):\n"
                        f"Anterior: {old_mem['content']}\n"
                        f"Nuevo: {new_content}\n\n"
                        "Se guardo la nueva version. Revisa en admin > Memorias."
                    )
                    telegram.notify_admins(alert_msg)
                    log.warning("Contradiccion de salud para %s: '%s' vs '%s'",
                                user_id, old_mem['content'], new_content)

                # Merge via LLM (newer has priority)
                merge_prompt = (
                    f"Memoria anterior: '{old_mem['content']}'\n"
                    f"Memoria nueva (mas reciente): '{new_content}'\n\n"
                    "Estas dos memorias se contradicen. La mas reciente es la "
                    "que el usuario acaba de decir, asi que tiene prioridad. "
                    "Genera UNA sola memoria que refleje la informacion correcta "
                    "y mas actual. Responde solo con la memoria final, sin explicaciones."
                )
                merged = _llm_quick(llm,
                                    "Fusiona memorias. La mas reciente tiene prioridad.",
                                    merge_prompt, max_tokens=100)
                if merged:
                    db.delete_memory(old_id)
                    db.save_memory(user_id, category, merged)
                    log.info("Memoria %d contradicha, fusionada: %s", old_id, merged)
                    return
        except (ValueError, IndexError):
            pass

    db.save_memory(user_id, category, new_content)


def _extract_medication(llm, raw_text: str) -> dict | None:
    """Use LLM to extract medication info from spoken text.

    Returns {"name": ..., "dosage": ..., "schedule": ...} or None.
    """
    prompt = (
        f"El usuario dijo: '{raw_text}'\n\n"
        "Extrae la informacion del medicamento mencionado. "
        "Responde UNICAMENTE en este formato JSON (sin markdown, sin explicacion):\n"
        '{"name": "nombre del medicamento", "dosage": "dosis si la menciono o vacio", '
        '"schedule": "horario si lo menciono o vacio"}\n\n'
        "Si no menciona ningun medicamento o dice que no toma, responde: NINGUNO"
    )
    result = _llm_quick(
        llm,
        "Eres un extractor de datos medicos. Responde solo JSON o NINGUNO.",
        prompt, max_tokens=100,
    )
    if not result or "NINGUNO" in result.upper():
        return None
    try:
        import json
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        log.warning("No pude parsear medicamento: %s", result)
        return {"name": raw_text.strip(), "dosage": "", "schedule": ""}


def run_onboarding(user_id: str, user_name: str, config: dict, db,
                    stt, tts, llm, display, audio_cfg: dict,
                    speaker: "SpeakerID | None" = None,
                    weather=None):
    """Guided onboarding: 8-step introduction for first-time users."""
    log.info("=== Onboarding para %s ===", user_name)

    display.show_conversation()
    display.set_user(user_name)

    sample_rate = audio_cfg.get("sample_rate", 16000)
    voice_samples: list = []  # raw audio arrays for enrollment

    def _say(text):
        """Speak and show on display."""
        display.set_status("Hablando...", "#e94560")
        display.set_response(text)
        path = tts.speak(text)
        if path:
            play_audio(path)
            os.unlink(path)

    def _listen(initial_wait=8.0, collect_audio=False):
        """Record, transcribe, return text (or (text, audio) if collect_audio)."""
        display.set_status("Escuchando...", "#e94560")
        display.set_transcript("...")
        audio = record_until_silence(
            sample_rate=sample_rate,
            silence_threshold=audio_cfg.get("silence_threshold", 500),
            silence_duration=audio_cfg.get("silence_duration", 1.5),
            max_seconds=audio_cfg.get("max_record_seconds", 30),
            initial_wait=initial_wait,
        )
        if audio is None:
            return (None, None) if collect_audio else None
        display.set_status("Procesando...", "#f0a500")
        wav_path = save_wav(audio, sample_rate)
        text = stt.transcribe(wav_path)
        os.unlink(wav_path)
        if text:
            display.set_transcript(text)
            log.info("Onboarding respuesta: %s", text)
        if collect_audio:
            return text, audio
        return text

    # -- Step 1: Welcome --
    _say(f"Hola {user_name}! Soy Maya, tu asistente personal. "
         "Estoy aqui para ayudarte con tus medicamentos, recordatorios, "
         "y lo que necesites. Vamos a conocernos!")

    time.sleep(0.5)

    # -- Step 2: Preferred name --
    question_name = "Como te gustaria que te llame?"
    _say("Para empezar, como te gustaria que te llame? "
         "Puedes decirme tu nombre, un apodo, o como prefieras.")

    nickname_raw = _listen()
    nickname = user_name
    if nickname_raw:
        nickname = _extract_with_llm(llm, question_name, nickname_raw)
        _smart_save_memory(llm, db, user_id, "preferencia",
                           f"Prefiere que le llamen: {nickname}")
        _say(f"Perfecto! Te voy a decir {nickname}.")
    else:
        _say(f"Esta bien, te sigo diciendo {user_name}.")

    time.sleep(0.3)

    # -- Step 3: Wake word practice --
    _say("Para hablarme, solo di 'Oye Maya'. Vamos a practicar! Dime: Oye Maya.")

    ww_text, ww_audio = _listen(initial_wait=10.0, collect_audio=True)
    if ww_audio is not None:
        voice_samples.append(ww_audio)
    if ww_text:
        _say("Muy bien! Asi de facil. Cada que necesites algo, solo di Oye Maya.")
    else:
        _say("No te preocupes, con la practica se hace mas facil. "
             "Solo di Oye Maya cuando quieras hablarme.")

    time.sleep(0.3)

    # -- Step 4: Voice enrollment (3 phrases) --
    _say("Ahora voy a aprender a reconocer tu voz para saber quien me habla. "
         "Te voy a pedir que repitas tres frases cortitas.")
    time.sleep(0.3)

    enrollment_phrases = [
        "Ahora dime: Hoy es un buen dia.",
        "Ahora dime: Me gusta platicar con Maya.",
        "Y por ultimo: Buenos dias Maya.",
    ]

    for prompt_text in enrollment_phrases:
        _say(prompt_text)
        phrase_text, phrase_audio = _listen(initial_wait=10.0, collect_audio=True)
        if phrase_audio is not None:
            voice_samples.append(phrase_audio)
        if phrase_text:
            _say("Perfecto!")
        else:
            _say("No importa, seguimos.")
        time.sleep(0.2)

    # Attempt enrollment with collected samples
    if speaker and voice_samples:
        try:
            ok = speaker.enroll(user_id, voice_samples, sample_rate=sample_rate)
            if ok:
                _say("Listo! Ya puedo reconocer tu voz.")
                log.info("Enrollment exitoso para %s con %d muestras",
                         user_id, len(voice_samples))
            else:
                log.warning("Enrollment fallo para %s", user_id)
        except Exception as e:
            log.warning("Enrollment no disponible: %s", e)
    elif not voice_samples:
        log.info("Sin muestras de voz para enrollment de %s", user_id)

    time.sleep(0.3)

    # -- Step 5: About you --
    _say("Cuentame algo sobre ti. Que te gusta hacer? Tu comida favorita? "
         "Lo que quieras compartir.")

    about_raw = _listen()
    if about_raw:
        about = _extract_with_llm(llm, "Cuentame algo sobre ti", about_raw)
        _smart_save_memory(llm, db, user_id, "informacion", about)
        _say("Que interesante! Ya lo guarde.")
    else:
        _say("No te preocupes, poco a poco nos vamos conociendo.")

    time.sleep(0.3)

    # -- Step 6: Medications --
    _say("Tomas algun medicamento? Dime cual y te lo anoto.")

    med_count = 0
    for _ in range(10):  # max 10 medications
        med_raw = _listen(initial_wait=10.0)
        if not med_raw:
            break

        lower = med_raw.lower().strip()
        if lower in ("no", "no gracias", "ninguno", "ya no", "eso es todo",
                      "ya", "nada mas", "nada", "no mas"):
            break

        med_info = _extract_medication(llm, med_raw)
        if med_info and med_info.get("name"):
            db.add_medication(
                user_id,
                med_info["name"],
                med_info.get("dosage", ""),
                med_info.get("schedule", ""),
            )
            med_count += 1
            _say(f"Anote {med_info['name']}. Algun otro medicamento?")
        else:
            break

    if med_count > 0:
        _say(f"Listo, tengo {med_count} medicamento{'s' if med_count > 1 else ''} "
             f"registrado{'s' if med_count > 1 else ''}.")
    else:
        _say("Perfecto, si despues necesitas agregar alguno me dices.")

    time.sleep(0.3)

    # -- Step 7: Quick demo --
    _say("Ya casi terminamos! Te voy a ensenar lo que puedo hacer. "
         "Puedes preguntarme que dia es, como esta el clima, "
         "o pedirme que te recuerde algo. Prueba preguntarme algo!")

    demo_text = _listen(initial_wait=12.0)
    if demo_text:
        display.set_status("Pensando...", "#f0a500")
        response_text, actions = llm.chat(demo_text, nickname, db, user_id, weather=weather)
        if actions:
            execute_actions(actions, user_id, db, None, llm=llm)
        display.set_status("Hablando...", "#e94560")
        display.set_response(response_text)
        path = tts.speak(response_text)
        if path:
            play_audio(path)
            os.unlink(path)
        db.save_conversation(user_id, "user", demo_text)
        db.save_conversation(user_id, "assistant", response_text)
    else:
        _say("No te preocupes, cuando quieras puedes preguntarme lo que sea.")

    time.sleep(0.5)

    # -- Step 8: Wrap-up --
    _say("Listo! Ya nos conocemos. Acuerdate: solo di Oye Maya y aqui estoy. "
         "Tambien puedes tocar tu nombre en la pantalla.")

    # Mark onboarding complete
    db.set_onboarded(user_id)

    time.sleep(2)

    display.active_user_id = None
    display.show_main()
    log.info("=== Onboarding completado para %s ===", user_name)


# --- Cancel/exit detection for barge-in ---
_CANCEL_PHRASES = {
    "para", "maya para", "cancela", "maya cancela",
    "olvidalo", "olvídalo", "dejalo", "déjalo", "ya dejalo",
    "ya para", "ya basta", "basta",
    "eso es todo", "es todo", "no gracias",
    "hasta luego", "adios", "adiós",
}


def _is_cancel(text: str) -> bool:
    """Check if transcribed text is a cancel/stop command."""
    t = text.lower().strip().rstrip(".,!?¿¡")
    return t in _CANCEL_PHRASES


def main():
    global wakeword_detector, running

    log.info("=== Maya iniciando ===")

    # --- Initialize modules ---
    audio_cfg = config.get("audio", {})
    bt_mac = audio_cfg.get("bt_device_mac", "")

    # BT connect
    if bt_mac:
        bt_connect(bt_mac)

    # Generate sounds if missing
    sounds_dir = os.path.join(BASE_DIR, "sounds")
    if not os.path.isfile(os.path.join(sounds_dir, "wake_ack.wav")):
        generate_sounds(sounds_dir)

    # Database
    db = Database(os.path.join(BASE_DIR, "data", "assistant.db"))
    for uid, ucfg in config.get("users", {}).items():
        db.ensure_user(uid, ucfg.get("real_name", uid))

    # STT, TTS, LLM
    stt = STT(config.get("stt", {}))
    tts = TTS(config.get("tts", {}))
    llm = LLM(config.get("llm", {}), config.get("assistant", {}))

    # Set STT initial_prompt with user names for better transcription
    user_names = [ucfg.get("real_name", uid) for uid, ucfg in config.get("users", {}).items()]
    stt.set_user_names(user_names)

    # Speaker ID
    speaker = SpeakerID(
        config.get("speaker_id", {}),
        config.get("users", {}),
        BASE_DIR,
    )

    # Search (Perplexity)
    search = Search(config.get("search", {}))
    if search.enabled:
        log.info("Busqueda habilitada (Perplexity)")

    # Radio
    radio = Radio(db=db)

    # Weather
    weather_cfg = config.get("weather", {})
    weather = Weather(
        api_key=weather_cfg.get("api_key", ""),
        city=weather_cfg.get("city", ""),
    )
    weather.start()

    # Telegram (with DB, LLM, STT, TTS, weather for bidirectional chat)
    telegram = TelegramBot(config.get("telegram", {}), db=db, llm=llm, stt=stt,
                           tts=tts, weather=weather)
    telegram.start_polling()

    # Admin web interface (accessible via Tailscale)
    admin_cfg = config.get("admin", {})
    start_admin(db, admin_cfg.get("host", "0.0.0.0"), admin_cfg.get("port", 8085),
                telegram_bot=telegram)

    # Talk trigger event (for tap-to-talk and wake word)
    talk_event = threading.Event()
    tts_interrupt = threading.Event()  # Interrupt TTS on barge-in
    user_talk_info = {"user_id": None, "mode": None}  # set by user menu

    def on_talk_pressed():
        """Called from display button or wake word detection."""
        talk_event.set()

    def on_exit_pressed():
        """Called from display exit button."""
        global running
        running = False
        talk_event.set()  # Unblock main loop if waiting

    def on_user_talk(user_id, mode="talk"):
        """Called when user taps 'Hablar con Maya' from their menu."""
        user_talk_info["user_id"] = user_id
        user_talk_info["mode"] = mode
        talk_event.set()

    def on_radio_stop():
        """Called from display radio stop button."""
        radio.stop()
        display.set_radio(None)
        log.info("Radio apagada desde pantalla")

    # Display
    display = Display(
        config=config, db=db, weather=weather,
        on_close=on_exit_pressed, on_talk=on_talk_pressed,
        on_user_talk=on_user_talk, on_radio_stop=on_radio_stop,
        radio=radio,
    )
    display.start()
    time.sleep(0.5)  # Let display init

    # Wake word detector
    use_wakeword = False
    try:
        wakeword_detector = WakeWordDetector(config.get("wake_word", {}))
        use_wakeword = True
    except Exception as e:
        log.error("Error inicializando wake word: %s", e)
        log.info("Modo tap-to-talk (sin wake word)")

    # Start reminder thread (includes med reminders, activity check, weekly reports)
    rem_thread = threading.Thread(
        target=reminder_thread, args=(db, tts, display, telegram), daemon=True,
    )
    rem_thread.start()

    # --- Startup announcement ---
    display.set_status("Lista", "#4ecca3")
    update_reminders_display(db, display)

    ready_sound = os.path.join(sounds_dir, "ready.wav")
    if os.path.isfile(ready_sound):
        play_audio(ready_sound)

    startup_path = tts.speak("Hola, soy Maya. Estoy lista para ayudarte.")
    if startup_path:
        play_audio(startup_path)
        os.unlink(startup_path)

    log.info("=== Maya lista ===")

    # Wake word listener thread (runs in background, sets talk_event)
    def wakeword_loop():
        while running and wakeword_detector:
            if wakeword_detector.listen():
                tts_interrupt.set()  # Interrupt any playing TTS (barge-in)
                talk_event.set()

    if use_wakeword:
        threading.Thread(target=wakeword_loop, daemon=True).start()

    # --- Main loop ---
    while running:
        try:
            # a. Wait for activation (wake word OR tap-to-talk button)
            if use_wakeword:
                display.set_status("Diga 'Oye Maya' o toque el boton", "#4ecca3")
            else:
                display.set_status("Toque el boton para hablar", "#4ecca3")
            display.set_transcript("...")
            display.set_response("...")
            display.enable_talk_btn(True)

            talk_event.clear()
            talk_event.wait()  # Block until wake word or button tap

            if not running:
                break

            # Check if user was selected from menu
            selected_user_id = user_talk_info.get("user_id")
            selected_mode = user_talk_info.get("mode")
            user_talk_info["user_id"] = None
            user_talk_info["mode"] = None

            # Onboarding mode: guided introduction
            if selected_mode == "onboarding" and selected_user_id:
                uid = selected_user_id
                db_user = db.get_user(uid)
                uname = db_user["real_name"] if db_user else uid
                try:
                    run_onboarding(uid, uname, config, db, stt, tts, llm,
                                   display, audio_cfg, speaker=speaker,
                                   weather=weather)
                except Exception as e:
                    log.error("Error en onboarding: %s", e, exc_info=True)
                    display.active_user_id = None
                    display.show_main()
                continue

            display.show_conversation()
            display.enable_talk_btn(False)

            # b. Pause radio if playing (so mic doesn't pick it up)
            radio_was_playing = radio.current_station
            if radio_was_playing:
                radio.stop()
                display.set_radio(None)

            # b2. Play acknowledgment chime
            ack_sound = os.path.join(sounds_dir, "wake_ack.wav")
            if os.path.isfile(ack_sound):
                play_audio(ack_sound)

            display.set_status("Escuchando...", "#e94560")
            display.set_listening(True)

            # c. Record until silence
            audio = record_until_silence(
                sample_rate=audio_cfg.get("sample_rate", 16000),
                silence_threshold=audio_cfg.get("silence_threshold", 500),
                silence_duration=audio_cfg.get("silence_duration", 1.5),
                max_seconds=audio_cfg.get("max_record_seconds", 30),
            )
            display.set_listening(False)

            if audio is None:
                display.set_status("No escuche nada", "#8899aa")
                display.active_user_id = None
                display.show_main()
                time.sleep(1)
                continue

            # d. Speaker identification (skip if user selected from menu)
            user_id = selected_user_id
            user_name = "Usuario"
            if user_id:
                db_user = db.get_user(user_id)
                user_name = db_user["real_name"] if db_user else user_id
                display.set_user(user_name)
                log.info("Usuario seleccionado: %s", user_name)
            elif speaker.enabled:
                user_id = speaker.identify(audio, audio_cfg.get("sample_rate", 16000))
                if user_id:
                    user_name = config["users"][user_id].get("real_name", user_id)
                    display.set_user(user_name)
                    log.info("Hablante: %s", user_name)

            # Default user when speaker_id is disabled and no selection
            if not user_id:
                users = config.get("users", {})
                user_id = list(users.keys())[0] if users else "default"
                user_name = users.get(user_id, {}).get("real_name", "Usuario")
                db.ensure_user(user_id, user_name)

            # d2. Deliver pending messages (from Telegram contacts)
            if user_id:
                pending = db.get_pending_messages(user_id)
                if pending:
                    display.set_status("Tienes mensajes", "#4ecca3")
                    for pm in pending:
                        msg_text = f"Tienes un mensaje de {pm['from_name']}: {pm['message']}"
                        display.set_response(msg_text)
                        msg_audio = tts.speak(msg_text)
                        if msg_audio:
                            play_audio(msg_audio)
                            os.unlink(msg_audio)
                        db.mark_message_delivered(pm["id"])
                        time.sleep(0.5)

            # e. Transcribe
            display.set_status("Procesando...", "#f0a500")
            wav_path = save_wav(audio, audio_cfg.get("sample_rate", 16000))

            text = stt.transcribe(wav_path)
            os.unlink(wav_path)

            if not text:
                display.set_status("No entendi", "#8899aa")
                err_path = tts.speak("Disculpa, no te entendi. Puedes repetir?")
                if err_path:
                    play_audio(err_path)
                    os.unlink(err_path)
                display.active_user_id = None
                display.show_main()
                continue

            # f. Show transcript
            display.set_transcript(text)
            log.info("Transcripcion: %s", text)

            # f1. Cancel detection (user accidentally triggered or wants to dismiss)
            if _is_cancel(text):
                log.info("Usuario cancelo: '%s'", text)
                display.active_user_id = None
                display.show_main()
                if radio_was_playing and not radio.playing:
                    name = radio.play(radio_was_playing)
                    if name:
                        display.set_radio(name)
                continue

            # f2. Mood analysis
            try:
                mood = analyze_mood(audio, text, audio_cfg.get("sample_rate", 16000))
                if mood != "normal":
                    log.info("Animo detectado: %s (%s)", mood, user_name)
                if user_id:
                    _track_mood(user_id, mood, db, telegram)
            except Exception as e:
                log.warning("Error en mood analysis: %s", e)

            # Save user message
            if user_id:
                db.save_conversation(user_id, "user", text)

            # g. Process with LLM
            display.set_status("Pensando...", "#f0a500")
            response_text, actions = llm.chat(text, user_name, db, user_id, weather=weather)

            # h. Execute actions
            search_answer = None
            if actions:
                action_results = execute_actions(actions, user_id or "unknown", db, telegram, llm=llm, search=search, radio=radio)
                for r in action_results:
                    if r.startswith("__SEARCH__:"):
                        search_answer = r[len("__SEARCH__:"):]
                    elif r.startswith("__RADIO_ON__:"):
                        display.set_radio(r[len("__RADIO_ON__:"):])
                        radio_was_playing = radio.current_station
                    elif r == "__RADIO_OFF__":
                        display.set_radio(None)
                        radio_was_playing = None
                    else:
                        log.info("Accion: %s", r)

            # i. TTS response (interruptible — barge-in with wake word)
            display.set_status("Hablando...", "#e94560")
            display.set_response(response_text)

            tts_interrupted = False
            tts_path = tts.speak(response_text)
            if tts_path:
                tts_interrupt.clear()
                tts_interrupted = play_audio(tts_path, interrupt=tts_interrupt)
                os.unlink(tts_path)

            # i2. Speak search results if any (skip if interrupted)
            if search_answer and not tts_interrupted:
                display.set_status("Resultado de busqueda", "#3498DB")
                display.set_response(search_answer)
                search_path = tts.speak(search_answer)
                if search_path:
                    tts_interrupt.clear()
                    tts_interrupted = play_audio(search_path, interrupt=tts_interrupt)
                    os.unlink(search_path)
                if user_id:
                    db.save_conversation(user_id, "assistant", f"[Busqueda] {search_answer}")

            if tts_interrupted:
                talk_event.clear()  # Consume wake word event
                log.info("TTS interrumpido por barge-in")

            # Save assistant response
            if user_id:
                db.save_conversation(user_id, "assistant", response_text)

            # k. Update reminders display
            update_reminders_display(db, display)

            # l. Follow-up loop: multi-turn conversation
            max_rounds = audio_cfg.get("max_followup_rounds", 5)
            followup_wait = audio_cfg.get("followup_wait", 5.0)

            for followup_round in range(1, max_rounds + 1):
                log.info("Esperando follow-up (ronda %d/%d)...", followup_round, max_rounds)
                display.set_status("Escuchando...", "#e94560")
                display.enable_talk_btn(False)
                display.set_listening(True)

                followup_audio = record_until_silence(
                    sample_rate=audio_cfg.get("sample_rate", 16000),
                    silence_threshold=audio_cfg.get("silence_threshold", 500),
                    silence_duration=audio_cfg.get("silence_duration", 1.5),
                    max_seconds=audio_cfg.get("max_record_seconds", 30),
                    initial_wait=followup_wait,
                )
                display.set_listening(False)

                if followup_audio is None:
                    log.info("Sin follow-up en ronda %d", followup_round)
                    break

                display.set_status("Procesando...", "#f0a500")
                fw_path = save_wav(followup_audio, audio_cfg.get("sample_rate", 16000))
                fw_text = stt.transcribe(fw_path)
                os.unlink(fw_path)

                if not fw_text:
                    log.info("Follow-up sin texto en ronda %d", followup_round)
                    break

                display.set_transcript(fw_text)
                log.info("Follow-up ronda %d: %s", followup_round, fw_text)
                if user_id:
                    db.save_conversation(user_id, "user", fw_text)

                # Cancel detection in follow-up
                if _is_cancel(fw_text):
                    log.info("Cancel en follow-up ronda %d: '%s'", followup_round, fw_text)
                    bye = tts.speak("Esta bien, aqui estoy si me necesita.")
                    if bye:
                        play_audio(bye)
                        os.unlink(bye)
                    break

                display.set_status("Pensando...", "#f0a500")
                fw_response, fw_actions = llm.chat(fw_text, user_name, db, user_id, weather=weather)

                fw_search = None
                if fw_actions:
                    fw_results = execute_actions(fw_actions, user_id or "unknown", db, telegram, llm=llm, search=search, radio=radio)
                    for r in fw_results:
                        if r.startswith("__SEARCH__:"):
                            fw_search = r[len("__SEARCH__:"):]
                        elif r.startswith("__RADIO_ON__:"):
                            display.set_radio(r[len("__RADIO_ON__:"):])
                            radio_was_playing = radio.current_station
                        elif r == "__RADIO_OFF__":
                            display.set_radio(None)
                            radio_was_playing = None
                        else:
                            log.info("Accion follow-up ronda %d: %s", followup_round, r)

                display.set_status("Hablando...", "#e94560")
                display.set_response(fw_response)
                fw_tts = tts.speak(fw_response)
                fw_interrupted = False
                if fw_tts:
                    tts_interrupt.clear()
                    fw_interrupted = play_audio(fw_tts, interrupt=tts_interrupt)
                    os.unlink(fw_tts)

                if fw_search and not fw_interrupted:
                    display.set_response(fw_search)
                    fw_search_path = tts.speak(fw_search)
                    if fw_search_path:
                        tts_interrupt.clear()
                        fw_interrupted = play_audio(fw_search_path, interrupt=tts_interrupt)
                        os.unlink(fw_search_path)
                    if user_id:
                        db.save_conversation(user_id, "assistant", f"[Busqueda] {fw_search}")

                if fw_interrupted:
                    talk_event.clear()
                    log.info("Follow-up TTS interrumpido por barge-in")

                if user_id:
                    db.save_conversation(user_id, "assistant", fw_response)
                update_reminders_display(db, display)

            # Return to main screen after conversation ends
            display.active_user_id = None
            display.show_main()

            # Resume radio if it was playing before the conversation
            if radio_was_playing and not radio.playing:
                from radio import STATIONS
                name = radio.play(radio_was_playing)
                if name:
                    display.set_radio(name)

        except Exception as e:
            log.error("Error en loop principal: %s", e, exc_info=True)
            display.set_status("Error", "#e94560")
            display.active_user_id = None
            display.show_main()
            err_sound = os.path.join(sounds_dir, "error.wav")
            if os.path.isfile(err_sound):
                play_audio(err_sound)
            time.sleep(2)

    # --- Cleanup ---
    log.info("Cerrando Maya...")
    weather.stop()
    telegram.stop_polling()
    display.stop()
    if wakeword_detector:
        wakeword_detector.cleanup()
    _release_lock()
    log.info("=== Maya cerrada ===")


if __name__ == "__main__":
    main()
