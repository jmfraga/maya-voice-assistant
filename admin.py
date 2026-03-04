#!/usr/bin/env python3
"""Admin web interface for Maya - accessible via Tailscale.

Run standalone: python3 admin.py
Or import and call start_admin() from main.py
"""

import os
import logging
import yaml
from flask import Flask, render_template, request, redirect, url_for, flash

log = logging.getLogger("maya.admin")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def create_app(db=None, telegram_bot=None) -> Flask:
    """Create Flask app. Pass existing db instance or it creates one."""
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"),
                static_folder=os.path.join(BASE_DIR, "static"))
    app.secret_key = os.environ.get("MAYA_ADMIN_SECRET", "maya-admin-dev-key")

    if db is None:
        from db import Database
        db = Database(os.path.join(BASE_DIR, "data", "assistant.db"))

    # --- Dashboard ---
    @app.route("/")
    def index():
        users = db.get_users()
        pending = db.get_pending_contacts()
        stats = {
            "users": len(users),
            "reminders": len(db.get_all_active_reminders()),
            "pending_contacts": len(pending),
        }
        return render_template("admin_index.html", users=users, stats=stats)

    # --- Medications ---
    @app.route("/medications/<user_id>")
    def medications(user_id):
        users = db.get_users()
        user = next((u for u in users if u["id"] == user_id), None)
        if not user:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("index"))
        meds = db.get_medications(user_id, active_only=False)
        return render_template("admin_medications.html", user=user, medications=meds, users=users)

    @app.route("/medications/<user_id>/add", methods=["POST"])
    def add_medication(user_id):
        db.add_medication(
            user_id,
            request.form["name"],
            request.form.get("dosage", ""),
            request.form.get("schedule", ""),
            request.form.get("notes", ""),
        )
        flash("Medicamento agregado", "success")
        return redirect(url_for("medications", user_id=user_id))

    @app.route("/medications/edit/<int:med_id>", methods=["POST"])
    def edit_medication(med_id):
        user_id = request.form["user_id"]
        db.update_medication(
            med_id,
            name=request.form["name"],
            dosage=request.form.get("dosage", ""),
            schedule=request.form.get("schedule", ""),
            notes=request.form.get("notes", ""),
            active=1 if request.form.get("active") else 0,
        )
        flash("Medicamento actualizado", "success")
        return redirect(url_for("medications", user_id=user_id))

    @app.route("/medications/delete/<int:med_id>", methods=["POST"])
    def delete_medication(med_id):
        user_id = request.form["user_id"]
        db.delete_medication(med_id)
        flash("Medicamento eliminado", "success")
        return redirect(url_for("medications", user_id=user_id))

    # --- Contacts ---
    @app.route("/contacts/<user_id>")
    def contacts(user_id):
        users = db.get_users()
        user = next((u for u in users if u["id"] == user_id), None)
        if not user:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("index"))
        contact_list = db.get_contacts(user_id)
        return render_template("admin_contacts.html", user=user, contacts=contact_list, users=users)

    @app.route("/contacts/<user_id>/add", methods=["POST"])
    def add_contact(user_id):
        chat_id = request.form.get("telegram_chat_id", "0")
        db.add_contact(
            user_id,
            request.form["name"],
            telegram_chat_id=int(chat_id) if chat_id else 0,
            relationship=request.form.get("relationship", ""),
            phone=request.form.get("phone", ""),
        )
        flash("Contacto agregado", "success")
        if request.form.get("redirect") == "pending":
            return redirect(url_for("pending_contacts"))
        return redirect(url_for("contacts", user_id=user_id))

    @app.route("/contacts/edit/<int:contact_id>", methods=["POST"])
    def edit_contact(contact_id):
        user_id = request.form["user_id"]
        chat_id = request.form.get("telegram_chat_id", "0")
        db.update_contact(
            contact_id,
            name=request.form["name"],
            telegram_chat_id=int(chat_id) if chat_id else 0,
            relationship=request.form.get("relationship", ""),
            phone=request.form.get("phone", ""),
        )
        flash("Contacto actualizado", "success")
        if request.form.get("redirect") == "pending":
            return redirect(url_for("pending_contacts"))
        return redirect(url_for("contacts", user_id=user_id))

    @app.route("/contacts/delete/<int:contact_id>", methods=["POST"])
    def delete_contact(contact_id):
        user_id = request.form["user_id"]
        db.delete_contact(contact_id)
        flash("Contacto eliminado", "success")
        if request.form.get("redirect") == "pending":
            return redirect(url_for("pending_contacts"))
        return redirect(url_for("contacts", user_id=user_id))

    # --- Reminders ---
    @app.route("/reminders/<user_id>")
    def reminders(user_id):
        users = db.get_users()
        user = next((u for u in users if u["id"] == user_id), None)
        if not user:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("index"))
        rems = db.get_all_active_reminders()
        user_rems = [r for r in rems if r["user_id"] == user_id]
        return render_template("admin_reminders.html", user=user, reminders=user_rems, users=users)

    @app.route("/reminders/<user_id>/add", methods=["POST"])
    def add_reminder(user_id):
        db.add_reminder(
            user_id,
            request.form["text"],
            request.form["remind_at"],
            request.form.get("recurring", ""),
        )
        flash("Recordatorio creado", "success")
        return redirect(url_for("reminders", user_id=user_id))

    @app.route("/reminders/edit/<int:rem_id>", methods=["POST"])
    def edit_reminder(rem_id):
        user_id = request.form["user_id"]
        db.update_reminder(
            rem_id,
            text=request.form["text"],
            remind_at=request.form["remind_at"],
            recurring=request.form.get("recurring", ""),
            active=1 if request.form.get("active") else 0,
        )
        flash("Recordatorio actualizado", "success")
        return redirect(url_for("reminders", user_id=user_id))

    @app.route("/reminders/delete/<int:rem_id>", methods=["POST"])
    def delete_reminder(rem_id):
        user_id = request.form["user_id"]
        db.delete_reminder(rem_id)
        flash("Recordatorio eliminado", "success")
        return redirect(url_for("reminders", user_id=user_id))

    # --- Medication Log ---
    @app.route("/log/<user_id>")
    def medication_log(user_id):
        users = db.get_users()
        user = next((u for u in users if u["id"] == user_id), None)
        if not user:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("index"))
        logs = db.get_medication_log(user_id)
        return render_template("admin_log.html", user=user, logs=logs, users=users)

    # --- Users ---
    @app.route("/users/add", methods=["POST"])
    def add_user():
        user_id = request.form["id"].strip().lower()
        real_name = request.form["real_name"].strip()
        if user_id and real_name:
            db.ensure_user(user_id, real_name)
            flash(f"Usuario '{real_name}' creado", "success")
        return redirect(url_for("index"))

    @app.route("/users/edit/<user_id>", methods=["POST"])
    def edit_user(user_id):
        real_name = request.form.get("real_name", "").strip()
        if real_name:
            db.update_user(user_id, real_name)
            flash(f"Usuario actualizado a '{real_name}'", "success")
        return redirect(url_for("index"))

    @app.route("/users/delete/<user_id>", methods=["POST"])
    def delete_user(user_id):
        db.delete_user(user_id)
        flash(f"Usuario '{user_id}' eliminado", "success")
        return redirect(url_for("index"))

    # --- Pending Contacts (Telegram self-registration) ---
    @app.route("/pending")
    def pending_contacts():
        users = db.get_users()
        pending = db.get_all_pending_contacts()
        all_contacts = {u["id"]: db.get_contacts(u["id"]) for u in users}
        return render_template("admin_pending.html", pending=pending, users=users,
                               all_contacts=all_contacts)

    @app.route("/pending/approve/<int:pending_id>", methods=["POST"])
    def approve_contact(pending_id):
        user_ids = request.form.getlist("user_ids")
        contact = db.approve_pending_contact(pending_id)
        if contact:
            # If none selected, assign to all users
            if not user_ids:
                user_ids = [u["id"] for u in db.get_users()]
            for uid in user_ids:
                db.add_contact(
                    uid,
                    contact["name"],
                    telegram_chat_id=contact["chat_id"],
                    relationship=contact["relationship"],
                )
            # Notify the person on Telegram
            if telegram_bot:
                telegram_bot.send_to_chat_id(
                    contact["chat_id"],
                    "Tu solicitud fue aprobada! Ya estas registrado como contacto "
                    "de los Abuelos Fraga. Puedes recibir mensajes de Maya."
                )
            flash(f"Contacto '{contact['name']}' aprobado para {len(user_ids)} usuario(s)", "success")
        else:
            flash("Solicitud no encontrada", "error")
        return redirect(url_for("pending_contacts"))

    @app.route("/pending/reject/<int:pending_id>", methods=["POST"])
    def reject_contact(pending_id):
        contact = db.get_all_pending_contacts()
        target = next((c for c in contact if c["id"] == pending_id), None)
        db.reject_pending_contact(pending_id)
        if target and telegram_bot:
            telegram_bot.send_to_chat_id(
                target["chat_id"],
                "Tu solicitud de contacto no fue aprobada. "
                "Si crees que es un error, contacta al administrador."
            )
        flash("Solicitud rechazada", "success")
        return redirect(url_for("pending_contacts"))

    # --- Settings ---
    def _load_config():
        config_path = os.path.join(BASE_DIR, "config.yaml")
        with open(config_path) as f:
            return yaml.safe_load(f)

    def _save_config(cfg):
        config_path = os.path.join(BASE_DIR, "config.yaml")
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def _mask(value: str) -> str:
        """Mask API key for display: show first 4 and last 4 chars."""
        if not value or value.startswith(("OPENAI_", "ANTHROPIC_", "ELEVENLABS_", "PICOVOICE_", "TELEGRAM_", "CHANGE_ME", "VOICE_ID")):
            return ""
        if len(value) <= 10:
            return "*" * len(value)
        return value[:4] + "*" * (len(value) - 8) + value[-4:]

    @app.route("/settings")
    def settings():
        cfg = _load_config()
        users = db.get_users()
        keys = {
            "picovoice_key": _mask(cfg.get("wake_word", {}).get("access_key", "")),
            "openai_stt_key": _mask(cfg.get("stt", {}).get("openai_api", {}).get("api_key", "")),
            "elevenlabs_key": _mask(cfg.get("tts", {}).get("elevenlabs", {}).get("api_key", "")),
            "elevenlabs_voice_id": _mask(cfg.get("tts", {}).get("elevenlabs", {}).get("voice_id", "")),
            "anthropic_key": _mask(cfg.get("llm", {}).get("claude", {}).get("api_key", "")),
            "openai_llm_key": _mask(cfg.get("llm", {}).get("openai", {}).get("api_key", "")),
            "telegram_token": _mask(cfg.get("telegram", {}).get("bot_token", "")),
        }
        ppn_file = cfg.get("wake_word", {}).get("keyword_path", "")
        ppn_path = os.path.join(BASE_DIR, ppn_file) if ppn_file else ""
        ppn_exists = os.path.isfile(ppn_path)
        return render_template("admin_settings.html", config=cfg, keys=keys, users=users,
                               ppn_exists=ppn_exists)

    @app.route("/settings/upload-ppn", methods=["POST"])
    def upload_ppn():
        f = request.files.get("ppn_file")
        if not f or not f.filename.endswith(".ppn"):
            flash("Selecciona un archivo .ppn valido", "error")
            return redirect(url_for("settings"))

        filename = f.filename.replace(" ", "_")
        dest = os.path.join(BASE_DIR, filename)
        f.save(dest)

        # Update config with the new filename
        cfg = _load_config()
        cfg.setdefault("wake_word", {})["keyword_path"] = filename
        _save_config(cfg)

        flash(f"Wake word '{filename}' instalado. Reinicia Maya para activarlo.", "success")
        return redirect(url_for("settings"))

    @app.route("/settings/save", methods=["POST"])
    def save_settings():
        cfg = _load_config()

        # --- API Keys (only update if non-empty, meaning user typed a new value) ---
        def _update_key(form_field, cfg_path):
            """Update a nested config value if form field is non-empty."""
            val = request.form.get(form_field, "").strip()
            if val:
                obj = cfg
                for key in cfg_path[:-1]:
                    obj = obj.setdefault(key, {})
                obj[cfg_path[-1]] = val

        _update_key("picovoice_key", ["wake_word", "access_key"])
        _update_key("openai_stt_key", ["stt", "openai_api", "api_key"])
        _update_key("elevenlabs_key", ["tts", "elevenlabs", "api_key"])
        _update_key("elevenlabs_voice_id", ["tts", "elevenlabs", "voice_id"])
        _update_key("anthropic_key", ["llm", "claude", "api_key"])
        _update_key("openai_llm_key", ["llm", "openai", "api_key"])
        _update_key("telegram_token", ["telegram", "bot_token"])

        # --- LLM selection ---
        llm_provider = request.form.get("llm_provider", "claude")
        cfg.setdefault("llm", {})["primary"] = llm_provider

        # Claude model
        claude_model = request.form.get("claude_model", "").strip()
        if claude_model:
            cfg.setdefault("llm", {}).setdefault("claude", {})["model"] = claude_model

        # OpenAI model
        openai_model = request.form.get("openai_model", "").strip()
        if openai_model:
            cfg.setdefault("llm", {}).setdefault("openai", {})["model"] = openai_model

        # Max tokens
        max_tokens = request.form.get("llm_max_tokens", "").strip()
        if max_tokens and max_tokens.isdigit():
            mt = int(max_tokens)
            cfg.setdefault("llm", {}).setdefault("claude", {})["max_tokens"] = mt
            cfg.setdefault("llm", {}).setdefault("openai", {})["max_tokens"] = mt

        # --- STT selection ---
        stt_primary = request.form.get("stt_primary", "openai_api")
        cfg.setdefault("stt", {})["primary"] = stt_primary

        # --- TTS selection ---
        tts_primary = request.form.get("tts_primary", "elevenlabs")
        cfg.setdefault("tts", {})["primary"] = tts_primary

        # ElevenLabs model
        eleven_model = request.form.get("elevenlabs_model", "").strip()
        if eleven_model:
            cfg.setdefault("tts", {}).setdefault("elevenlabs", {})["model_id"] = eleven_model

        # ElevenLabs voice selection
        eleven_voice = request.form.get("elevenlabs_voice", "").strip()
        if eleven_voice:
            cfg.setdefault("tts", {}).setdefault("elevenlabs", {})["voice_id"] = eleven_voice

        # --- Wake word sensitivity ---
        sensitivity = request.form.get("wake_sensitivity", "").strip()
        if sensitivity:
            try:
                cfg.setdefault("wake_word", {})["sensitivity"] = float(sensitivity)
            except ValueError:
                pass

        # --- Audio settings ---
        silence_thresh = request.form.get("silence_threshold", "").strip()
        if silence_thresh and silence_thresh.isdigit():
            cfg.setdefault("audio", {})["silence_threshold"] = int(silence_thresh)

        silence_dur = request.form.get("silence_duration", "").strip()
        if silence_dur:
            try:
                cfg.setdefault("audio", {})["silence_duration"] = float(silence_dur)
            except ValueError:
                pass

        # --- Speaker ID ---
        speaker_enabled = request.form.get("speaker_id_enabled")
        cfg.setdefault("speaker_id", {})["enabled"] = speaker_enabled == "on"

        sim_threshold = request.form.get("similarity_threshold", "").strip()
        if sim_threshold:
            try:
                cfg.setdefault("speaker_id", {})["similarity_threshold"] = float(sim_threshold)
            except ValueError:
                pass

        # --- Telegram contacts ---
        contact_names = request.form.getlist("telegram_contact_name")
        contact_ids = request.form.getlist("telegram_contact_id")
        if contact_names:
            contacts = {}
            for name, cid in zip(contact_names, contact_ids):
                name = name.strip()
                cid = cid.strip()
                if name and cid:
                    try:
                        contacts[name] = int(cid)
                    except ValueError:
                        pass
            if contacts:
                cfg.setdefault("telegram", {})["contacts"] = contacts

        _save_config(cfg)
        flash("Configuracion guardada. Reinicia Maya para aplicar cambios.", "success")
        return redirect(url_for("settings"))

    return app


def start_admin(db=None, host="0.0.0.0", port=8085, telegram_bot=None):
    """Start admin server in a background thread."""
    import threading
    app = create_app(db, telegram_bot=telegram_bot)
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    log.info("Admin web en http://%s:%d", host, port)
    return thread


if __name__ == "__main__":
    from db import Database
    from telegram_bot import TelegramBot

    with open(os.path.join(BASE_DIR, "config.yaml")) as f:
        config = yaml.safe_load(f)

    db = Database(os.path.join(BASE_DIR, "data", "assistant.db"))
    for uid, ucfg in config.get("users", {}).items():
        db.ensure_user(uid, ucfg.get("real_name", uid))

    tg = TelegramBot(config.get("telegram", {}), db=db)

    admin_cfg = config.get("admin", {})
    app = create_app(db, telegram_bot=tg)

    tg.start_polling()

    app.run(
        host=admin_cfg.get("host", "0.0.0.0"),
        port=admin_cfg.get("port", 8085),
        debug=False,
    )
