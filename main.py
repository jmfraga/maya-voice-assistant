#!/usr/bin/env python3
"""Maya - Asistente de Voz para Personas Mayores.

Entry point: wake word → record → STT → LLM → TTS → display loop.
"""

import os
import sys
import signal
import threading
import time
import logging
import logging.handlers
import tempfile
import yaml

# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

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
                db.add_reminder(user_id, action["text"], action["time"])
                results.append(f"Recordatorio creado para las {action['time']}")

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

    # Talk trigger event (for tap-to-talk and wake word)
    talk_event = threading.Event()

    def on_talk_pressed():
        """Called from display button or wake word detection."""
        talk_event.set()

    def on_exit_pressed():
        """Called from display exit button."""
        global running
        running = False
        talk_event.set()  # Unblock main loop if waiting

    # Display
    display = Display(on_close=on_exit_pressed, on_talk=on_talk_pressed)
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

    startup_path = tts.speak("Hola, soy Maya. Estoy lista para ayudarle.")
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
                time.sleep(1)
                continue

            # d. Speaker identification
            user_id = None
            user_name = "Usuario"
            if speaker.enabled:
                user_id = speaker.identify(audio, audio_cfg.get("sample_rate", 16000))
                if user_id:
                    user_name = config["users"][user_id].get("real_name", user_id)
                    display.set_user(user_name)
                    log.info("Hablante: %s", user_name)

            # e. Transcribe
            display.set_status("Procesando...", "#f0a500")
            wav_path = save_wav(audio, audio_cfg.get("sample_rate", 16000))

            text = stt.transcribe(wav_path)
            os.unlink(wav_path)

            if not text:
                display.set_status("No entendi", "#8899aa")
                err_path = tts.speak("Disculpe, no pude entender. ¿Puede repetir?")
                if err_path:
                    play_audio(err_path)
                    os.unlink(err_path)
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

        except Exception as e:
            log.error("Error en loop principal: %s", e, exc_info=True)
            display.set_status("Error", "#e94560")
            err_sound = os.path.join(sounds_dir, "error.wav")
            if os.path.isfile(err_sound):
                play_audio(err_sound)
            time.sleep(2)

    # --- Cleanup ---
    log.info("Cerrando Maya...")
    telegram.stop_polling()
    display.stop()
    if wakeword_detector:
        wakeword_detector.cleanup()
    log.info("=== Maya cerrada ===")


if __name__ == "__main__":
    main()
