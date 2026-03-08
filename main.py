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
                    telegram: TelegramBot) -> list[str]:
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
                db.save_memory(user_id, action["category"], action["content"])
                results.append(f"Memoria guardada: {action['content']}")

            elif atype == "CONSULTA_TRATAMIENTO":
                _handle_treatment_query(action, user_id, db, telegram, results)

        except Exception as e:
            log.error("Error ejecutando accion %s: %s", atype, e)
            results.append(f"Error al ejecutar {atype}")

    return results


def reminder_thread(db: Database, tts: TTS, display: Display):
    """Background thread that checks for due reminders every 30s."""
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
        if llm.provider == "claude":
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
                headers={"Authorization": f"Bearer {llm._api_key}",
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


def _smart_save_memory(llm, db, user_id: str, category: str, new_content: str):
    """Save memory with LLM-powered dedup and contradiction handling.

    On contradiction: asks LLM to merge both into the most accurate version.
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

    if result.startswith("CONTRADICE:"):
        try:
            old_id = int(result.split(":")[1].strip())
            old_mem = next((m for m in existing if m["id"] == old_id), None)
            if old_mem:
                # Ask LLM to resolve: keep newer as truth, merge if possible
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


def run_onboarding(user_id: str, user_name: str, config: dict, db,
                    stt, tts, llm, display, audio_cfg: dict):
    """Guided onboarding: Maya introduces herself and learns about the user."""
    log.info("=== Onboarding para %s ===", user_name)

    display.show_conversation()
    display.set_user(user_name)

    def _say(text):
        """Speak and show on display."""
        display.set_status("Hablando...", "#e94560")
        display.set_response(text)
        path = tts.speak(text)
        if path:
            play_audio(path)
            os.unlink(path)

    def _listen():
        """Record, transcribe, return text or None."""
        display.set_status("Escuchando...", "#e94560")
        display.set_transcript("...")
        audio = record_until_silence(
            sample_rate=audio_cfg.get("sample_rate", 16000),
            silence_threshold=audio_cfg.get("silence_threshold", 500),
            silence_duration=audio_cfg.get("silence_duration", 1.5),
            max_seconds=audio_cfg.get("max_record_seconds", 30),
            initial_wait=8.0,
        )
        if audio is None:
            return None
        display.set_status("Procesando...", "#f0a500")
        wav_path = save_wav(audio, audio_cfg.get("sample_rate", 16000))
        text = stt.transcribe(wav_path)
        os.unlink(wav_path)
        if text:
            display.set_transcript(text)
            log.info("Onboarding respuesta: %s", text)
        return text

    # --- Step 1: Introduction ---
    _say(f"Hola {user_name}! Soy Maya, tu asistente personal. "
         "Estoy aqui para ayudarte con tus medicamentos, recordatorios, "
         "y lo que necesites. Vamos a conocernos un poquito!")

    time.sleep(0.5)

    # --- Step 2: Preferred name ---
    question_name = "Como te gustaria que te llame?"
    _say("Para empezar, como te gustaria que te llame? "
         "Puedes decirme tu nombre, un apodo, o como prefieras.")

    nickname_raw = _listen()
    if nickname_raw:
        nickname = _extract_with_llm(llm, question_name, nickname_raw)
        _smart_save_memory(llm, db, user_id, "preferencia",
                           f"Prefiere que le llamen: {nickname}")
        _say(f"Perfecto! Te voy a decir {nickname}.")
    else:
        _say(f"Esta bien, te sigo diciendo {user_name}.")

    time.sleep(0.3)

    # --- Step 3: What to remember ---
    question_about = "Hay algo importante que quieras que recuerde sobre ti?"
    _say("Cuentame, hay algo importante que quieras que recuerde sobre ti? "
         "Por ejemplo, tus gustos, tu comida favorita, algo que te guste hacer...")

    about_raw = _listen()
    if about_raw:
        about = _extract_with_llm(llm, question_about, about_raw)
        _smart_save_memory(llm, db, user_id, "informacion", about)
        _say(f"Que interesante! Ya lo guarde.")
    else:
        _say("No te preocupes, poco a poco nos vamos conociendo.")

    time.sleep(0.3)

    # --- Step 4: How to help ---
    question_help = "En que te gustaria que te ayude mas?"
    _say("Y por ultimo, en que te gustaria que te ayude? "
         "Puedo recordarte tus medicinas, ponerte recordatorios, "
         "mandarte mensajes por Telegram, o simplemente platicar contigo.")

    help_raw = _listen()
    if help_raw:
        help_pref = _extract_with_llm(llm, question_help, help_raw)
        _smart_save_memory(llm, db, user_id, "preferencia",
                           f"Le gustaria ayuda con: {help_pref}")
        _say("Entendido! Lo voy a tener presente.")
    else:
        _say("No te preocupes, cuando necesites algo solo dime Oye Maya.")

    time.sleep(0.3)

    # --- Step 5: Summary ---
    memories = db.get_memories(user_id, limit=10)
    summary_parts = []
    for m in memories:
        summary_parts.append(f"- {m['content']}")
    summary = "\n".join(summary_parts) if summary_parts else "Aun no hay notas."

    display.set_transcript("Lo que aprendi de ti:")
    display.set_response(summary)

    _say(f"Listo! Ya nos conocemos un poquito. "
         "Acuerdate que puedes hablarme cuando quieras diciendo Oye Maya, "
         "o tocando tu nombre en la pantalla. Aqui estoy para lo que necesites!")

    time.sleep(2)

    display.active_user_id = None
    display.show_main()
    log.info("=== Onboarding completado para %s ===", user_name)


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

    # Speaker ID
    speaker = SpeakerID(
        config.get("speaker_id", {}),
        config.get("users", {}),
        BASE_DIR,
    )

    # Telegram (with DB for self-registration)
    telegram = TelegramBot(config.get("telegram", {}), db=db)
    telegram.start_polling()

    # Admin web interface (accessible via Tailscale)
    admin_cfg = config.get("admin", {})
    start_admin(db, admin_cfg.get("host", "0.0.0.0"), admin_cfg.get("port", 8085),
                telegram_bot=telegram)

    # Weather
    weather_cfg = config.get("weather", {})
    weather = Weather(
        api_key=weather_cfg.get("api_key", ""),
        city=weather_cfg.get("city", ""),
    )
    weather.start()

    # Talk trigger event (for tap-to-talk and wake word)
    talk_event = threading.Event()
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

    # Display
    display = Display(
        config=config, db=db, weather=weather,
        on_close=on_exit_pressed, on_talk=on_talk_pressed,
        on_user_talk=on_user_talk,
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

    # Start reminder thread
    rem_thread = threading.Thread(
        target=reminder_thread, args=(db, tts, display), daemon=True,
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
                                   display, audio_cfg)
                except Exception as e:
                    log.error("Error en onboarding: %s", e, exc_info=True)
                    display.active_user_id = None
                    display.show_main()
                continue

            display.show_conversation()
            display.enable_talk_btn(False)

            # b. Play acknowledgment chime
            ack_sound = os.path.join(sounds_dir, "wake_ack.wav")
            if os.path.isfile(ack_sound):
                play_audio(ack_sound)

            display.set_status("Escuchando...", "#e94560")

            # c. Record until silence
            audio = record_until_silence(
                sample_rate=audio_cfg.get("sample_rate", 16000),
                silence_threshold=audio_cfg.get("silence_threshold", 500),
                silence_duration=audio_cfg.get("silence_duration", 1.5),
                max_seconds=audio_cfg.get("max_record_seconds", 30),
            )

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

            # Save user message
            if user_id:
                db.save_conversation(user_id, "user", text)

            # g. Process with LLM
            display.set_status("Pensando...", "#f0a500")
            response_text, actions = llm.chat(text, user_name, db, user_id)

            # h. Execute actions
            if actions:
                action_results = execute_actions(actions, user_id or "unknown", db, telegram)
                for r in action_results:
                    log.info("Accion: %s", r)

            # i. TTS response
            display.set_status("Hablando...", "#e94560")
            display.set_response(response_text)

            tts_path = tts.speak(response_text)
            if tts_path:
                play_audio(tts_path)
                os.unlink(tts_path)

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

                followup_audio = record_until_silence(
                    sample_rate=audio_cfg.get("sample_rate", 16000),
                    silence_threshold=audio_cfg.get("silence_threshold", 500),
                    silence_duration=audio_cfg.get("silence_duration", 1.5),
                    max_seconds=audio_cfg.get("max_record_seconds", 30),
                    initial_wait=followup_wait,
                )

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

                display.set_status("Pensando...", "#f0a500")
                fw_response, fw_actions = llm.chat(fw_text, user_name, db, user_id)

                if fw_actions:
                    fw_results = execute_actions(fw_actions, user_id or "unknown", db, telegram)
                    for r in fw_results:
                        log.info("Accion follow-up ronda %d: %s", followup_round, r)

                display.set_status("Hablando...", "#e94560")
                display.set_response(fw_response)
                fw_tts = tts.speak(fw_response)
                if fw_tts:
                    play_audio(fw_tts)
                    os.unlink(fw_tts)

                if user_id:
                    db.save_conversation(user_id, "assistant", fw_response)
                update_reminders_display(db, display)

            # Return to main screen after conversation ends
            display.active_user_id = None
            display.show_main()

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
