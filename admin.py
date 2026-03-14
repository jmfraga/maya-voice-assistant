#!/usr/bin/env python3
"""Admin web interface for Maya - accessible via Tailscale.

Run standalone: python3 admin.py
Or import and call start_admin() from main.py
"""

import os
import logging
import functools
import yaml
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

log = logging.getLogger("maya.admin")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def create_app(db=None, telegram_bot=None) -> Flask:
    """Create Flask app. Pass existing db instance or it creates one."""
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"),
                static_folder=os.path.join(BASE_DIR, "static"))
    app.secret_key = os.environ.get("MAYA_ADMIN_SECRET", "maya-admin-dev-key")
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max upload

    @app.after_request
    def add_no_cache(response):
        if response.content_type and "text/html" in response.content_type:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    if db is None:
        from db import Database
        db = Database(os.path.join(BASE_DIR, "data", "assistant.db"))

    # --- Auth helpers ---
    def login_required(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("admin_user"):
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    def admin_only(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("admin_user"):
                return redirect(url_for("login"))
            if session.get("admin_role") != "admin":
                flash("Acceso restringido a administradores", "error")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return decorated

    @app.before_request
    def require_login():
        public = ("login", "static", "user_photo")
        if request.endpoint and request.endpoint in public:
            return
        if not session.get("admin_user"):
            return redirect(url_for("login"))

    @app.context_processor
    def inject_auth():
        return {
            "current_user": session.get("admin_user"),
            "current_role": session.get("admin_role"),
        }

    # --- Login/Logout ---
    @app.route("/login", methods=["GET", "POST"])
    def login():
        # If no admin users exist, show setup form
        admin_users = db.get_admin_users()
        if not admin_users:
            if request.method == "POST":
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "").strip()
                if username and password:
                    db.add_admin_user(username, generate_password_hash(password), "admin")
                    session["admin_user"] = username
                    session["admin_role"] = "admin"
                    flash(f"Cuenta de administrador '{username}' creada", "success")
                    return redirect(url_for("index"))
            return render_template("admin_login.html", setup=True)

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            user = db.get_admin_user(username)
            if user and check_password_hash(user["password_hash"], password):
                session["admin_user"] = username
                session["admin_role"] = user["role"]
                return redirect(url_for("index"))
            flash("Usuario o contraseña incorrectos", "error")
        return render_template("admin_login.html", setup=False)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

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

    # --- Memories ---
    @app.route("/memories/<user_id>")
    def memories(user_id):
        users = db.get_users()
        user = next((u for u in users if u["id"] == user_id), None)
        if not user:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("index"))
        mems = db.get_memories(user_id, limit=100)
        return render_template("admin_memories.html", user=user, memories=mems, users=users)

    @app.route("/memories/delete/<int:mem_id>", methods=["POST"])
    def delete_memory(mem_id):
        user_id = request.form["user_id"]
        db.delete_memory(mem_id)
        flash("Memoria eliminada", "success")
        return redirect(url_for("memories", user_id=user_id))

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
        # Telegram chat_id for direct user-Maya chat
        tg_chat_id = request.form.get("user_telegram_chat_id", "").strip()
        if tg_chat_id:
            try:
                db.set_user_telegram(user_id, int(tg_chat_id))
                flash("Telegram vinculado al usuario", "success")
            except ValueError:
                flash("Chat ID debe ser un numero", "error")
        elif "user_telegram_chat_id" in request.form:
            # Field present but empty — clear the association
            db.set_user_telegram(user_id, None)
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

    # --- Treatment Schemas ---
    @app.route("/treatments/<user_id>")
    def treatments(user_id):
        users = db.get_users()
        user = next((u for u in users if u["id"] == user_id), None)
        if not user:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("index"))
        schemas = db.get_treatment_schemas(user_id, active_only=False)
        # Load ranges for each schema
        for s in schemas:
            s["ranges"] = db.get_treatment_ranges(s["id"])
        contacts = db.get_contacts(user_id)
        return render_template("admin_treatments.html", user=user, schemas=schemas,
                               contacts=contacts, users=users)

    @app.route("/treatments/<user_id>/add", methods=["POST"])
    def add_treatment(user_id):
        alert_low = request.form.get("alert_low", "").strip()
        alert_high = request.form.get("alert_high", "").strip()
        alert_contacts = ",".join(request.form.getlist("alert_contacts"))
        db.add_treatment_schema(
            user_id,
            request.form["name"],
            request.form["measurement_name"],
            request.form.get("measurement_unit", ""),
            alert_low=float(alert_low) if alert_low else None,
            alert_high=float(alert_high) if alert_high else None,
            alert_contacts=alert_contacts,
            notes=request.form.get("notes", ""),
        )
        flash("Esquema de tratamiento creado", "success")
        return redirect(url_for("treatments", user_id=user_id))

    @app.route("/treatments/edit/<int:schema_id>", methods=["POST"])
    def edit_treatment(schema_id):
        user_id = request.form["user_id"]
        alert_low = request.form.get("alert_low", "").strip()
        alert_high = request.form.get("alert_high", "").strip()
        alert_contacts = ",".join(request.form.getlist("alert_contacts"))
        db.update_treatment_schema(
            schema_id,
            name=request.form["name"],
            measurement_name=request.form["measurement_name"],
            measurement_unit=request.form.get("measurement_unit", ""),
            alert_low=float(alert_low) if alert_low else None,
            alert_high=float(alert_high) if alert_high else None,
            alert_contacts=alert_contacts,
            notes=request.form.get("notes", ""),
            active=1 if request.form.get("active") else 0,
        )
        flash("Esquema actualizado", "success")
        return redirect(url_for("treatments", user_id=user_id))

    @app.route("/treatments/toggle/<int:schema_id>", methods=["POST"])
    def toggle_treatment(schema_id):
        user_id = request.form["user_id"]
        schema = db.get_treatment_schema(schema_id)
        if schema:
            new_active = 0 if schema["active"] else 1
            db.update_treatment_schema(schema_id, active=new_active)
            estado = "activado" if new_active else "desactivado"
            flash(f"Esquema {estado}", "success")
        return redirect(url_for("treatments", user_id=user_id))

    @app.route("/treatments/delete/<int:schema_id>", methods=["POST"])
    def delete_treatment(schema_id):
        user_id = request.form["user_id"]
        db.delete_treatment_schema(schema_id)
        flash("Esquema eliminado", "success")
        return redirect(url_for("treatments", user_id=user_id))

    @app.route("/treatments/<int:schema_id>/add-range", methods=["POST"])
    def add_treatment_range(schema_id):
        user_id = request.form["user_id"]
        db.add_treatment_range(
            schema_id,
            float(request.form["range_min"]),
            float(request.form["range_max"]),
            float(request.form["dose"]),
            request.form.get("dose_unit", ""),
            request.form.get("time_of_day", "any"),
            request.form.get("notes", ""),
        )
        flash("Rango agregado", "success")
        return redirect(url_for("treatments", user_id=user_id))

    @app.route("/treatments/range/edit/<int:range_id>", methods=["POST"])
    def edit_treatment_range(range_id):
        user_id = request.form["user_id"]
        db.update_treatment_range(
            range_id,
            range_min=float(request.form["range_min"]),
            range_max=float(request.form["range_max"]),
            dose=float(request.form["dose"]),
            dose_unit=request.form.get("dose_unit", ""),
            time_of_day=request.form.get("time_of_day", "any"),
            notes=request.form.get("notes", ""),
        )
        flash("Rango actualizado", "success")
        return redirect(url_for("treatments", user_id=user_id))

    @app.route("/treatments/range/delete/<int:range_id>", methods=["POST"])
    def delete_treatment_range(range_id):
        user_id = request.form["user_id"]
        db.delete_treatment_range(range_id)
        flash("Rango eliminado", "success")
        return redirect(url_for("treatments", user_id=user_id))

    @app.route("/measurements/<user_id>")
    def measurements(user_id):
        users = db.get_users()
        user = next((u for u in users if u["id"] == user_id), None)
        if not user:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("index"))
        schema_id = request.args.get("schema_id", type=int)
        logs = db.get_measurement_log(user_id, schema_id=schema_id)
        schemas = db.get_treatment_schemas(user_id)
        return render_template("admin_measurements.html", user=user, logs=logs,
                               schemas=schemas, selected_schema=schema_id, users=users)

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
    @admin_only
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
            "weather_key": _mask(cfg.get("weather", {}).get("api_key", "")),
            "synapse_key": _mask(cfg.get("synapse", {}).get("api_key", "")),
        }
        ppn_file = cfg.get("wake_word", {}).get("keyword_path", "")
        ppn_path = os.path.join(BASE_DIR, ppn_file) if ppn_file else ""
        ppn_exists = os.path.isfile(ppn_path)
        return render_template("admin_settings.html", config=cfg, keys=keys, users=users,
                               ppn_exists=ppn_exists)

    @app.route("/photos/<user_id>")
    def user_photo(user_id):
        """Serve user photo."""
        from flask import send_from_directory, make_response
        photos_dir = os.path.join(BASE_DIR, "data", "photos")
        for ext in (".jpg", ".jpeg", ".png"):
            if os.path.isfile(os.path.join(photos_dir, f"{user_id}{ext}")):
                resp = make_response(send_from_directory(photos_dir, f"{user_id}{ext}"))
                resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                return resp
        # Return a 1x1 transparent pixel if no photo
        return "", 204

    @app.route("/users/<user_id>/upload-photo", methods=["POST"])
    def upload_photo(user_id):
        log.info("Upload foto para %s, files: %s", user_id, list(request.files.keys()))
        f = request.files.get("photo")
        if not f or not f.filename:
            log.warning("No se recibio archivo para %s", user_id)
            flash("Selecciona una foto primero", "error")
            return redirect(url_for("index"))

        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png"):
            flash("Solo se permiten archivos .jpg o .png", "error")
            return redirect(url_for("index"))

        photos_dir = os.path.join(BASE_DIR, "data", "photos")
        os.makedirs(photos_dir, exist_ok=True)

        # Remove old photos for this user
        for old_ext in (".jpg", ".jpeg", ".png"):
            old = os.path.join(photos_dir, f"{user_id}{old_ext}")
            if os.path.isfile(old):
                os.remove(old)

        dest = os.path.join(photos_dir, f"{user_id}{ext}")
        f.save(dest)
        flash(f"Foto de {user_id} actualizada. Reinicia Maya para verla.", "success")
        return redirect(url_for("index"))

    @app.route("/settings/upload-ppn", methods=["POST"])
    @admin_only
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
    @admin_only
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

        # --- Synapse (shared config for STT/TTS/LLM) ---
        synapse_base_url = request.form.get("synapse_base_url", "").strip()
        if synapse_base_url:
            cfg.setdefault("synapse", {})["base_url"] = synapse_base_url
        _update_key("synapse_api_key", ["synapse", "api_key"])

        # --- LLM selection ---
        llm_provider = request.form.get("llm_provider", "claude")
        cfg.setdefault("llm", {})["primary"] = llm_provider

        llm_fallback = request.form.get("llm_fallback", "").strip()
        cfg.setdefault("llm", {})["fallback"] = llm_fallback

        # Synapse LLM model
        synapse_llm_model = request.form.get("synapse_llm_model", "").strip()
        if synapse_llm_model:
            cfg.setdefault("llm", {}).setdefault("synapse", {})["model"] = synapse_llm_model
            # Copy shared synapse config into llm.synapse
            syn_global = cfg.get("synapse", {})
            llm_syn = cfg["llm"]["synapse"]
            if "base_url" not in llm_syn and syn_global.get("base_url"):
                llm_syn["base_url"] = syn_global["base_url"]
            if "api_key" not in llm_syn and syn_global.get("api_key"):
                llm_syn["api_key"] = syn_global["api_key"]

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
            cfg.setdefault("llm", {}).setdefault("synapse", {})["max_tokens"] = mt

        # --- STT selection ---
        stt_primary = request.form.get("stt_primary", "openai_api")
        cfg.setdefault("stt", {})["primary"] = stt_primary

        stt_fallback = request.form.get("stt_fallback", "").strip()
        cfg.setdefault("stt", {})["fallback"] = stt_fallback

        # Copy shared synapse config into stt.synapse
        syn_global = cfg.get("synapse", {})
        if syn_global.get("base_url"):
            stt_syn = cfg.setdefault("stt", {}).setdefault("synapse", {})
            stt_syn["base_url"] = syn_global["base_url"]
            if syn_global.get("api_key"):
                stt_syn["api_key"] = syn_global["api_key"]

        # --- TTS selection ---
        tts_primary = request.form.get("tts_primary", "elevenlabs")
        cfg.setdefault("tts", {})["primary"] = tts_primary

        tts_fallback = request.form.get("tts_fallback", "").strip()
        cfg.setdefault("tts", {})["fallback"] = tts_fallback

        # Synapse TTS voice
        synapse_tts_voice = request.form.get("synapse_tts_voice", "").strip()
        if synapse_tts_voice:
            tts_syn = cfg.setdefault("tts", {}).setdefault("synapse", {})
            tts_syn["voice"] = synapse_tts_voice
            if syn_global.get("base_url"):
                tts_syn["base_url"] = syn_global["base_url"]
            if syn_global.get("api_key"):
                tts_syn["api_key"] = syn_global["api_key"]

        # OpenAI voice
        openai_voice = request.form.get("openai_voice", "").strip()
        if openai_voice:
            cfg.setdefault("tts", {}).setdefault("openai", {})["voice"] = openai_voice

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

        # --- Weather ---
        weather_key = request.form.get("weather_api_key", "").strip()
        if weather_key:
            cfg.setdefault("weather", {})["api_key"] = weather_key
        weather_city = request.form.get("weather_city", "").strip()
        if weather_city:
            cfg.setdefault("weather", {})["city"] = weather_city

        # --- Production mode ---
        cfg["production_mode"] = request.form.get("production_mode") == "on"

        _save_config(cfg)
        flash("Configuracion guardada. Reinicia Maya para aplicar cambios.", "success")
        return redirect(url_for("settings"))

    # --- Admin Users Management ---
    @app.route("/admin-users")
    @admin_only
    def admin_users_page():
        admin_list = db.get_admin_users()
        users = db.get_users()
        return render_template("admin_users.html", admin_list=admin_list, users=users)

    @app.route("/admin-users/add", methods=["POST"])
    @admin_only
    def add_admin_user():
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "familiar")
        if not username or not password:
            flash("Usuario y contraseña requeridos", "error")
        elif db.get_admin_user(username):
            flash(f"El usuario '{username}' ya existe", "error")
        else:
            db.add_admin_user(username, generate_password_hash(password), role)
            flash(f"Usuario '{username}' ({role}) creado", "success")
        return redirect(url_for("admin_users_page"))

    @app.route("/admin-users/edit/<int:uid>", methods=["POST"])
    @admin_only
    def edit_admin_user(uid):
        role = request.form.get("role", "familiar")
        password = request.form.get("password", "").strip()
        updates = {"role": role}
        if password:
            updates["password_hash"] = generate_password_hash(password)
        db.update_admin_user(uid, **updates)
        flash("Usuario actualizado", "success")
        return redirect(url_for("admin_users_page"))

    @app.route("/admin-users/delete/<int:uid>", methods=["POST"])
    @admin_only
    def delete_admin_user(uid):
        db.delete_admin_user(uid)
        flash("Usuario eliminado", "success")
        return redirect(url_for("admin_users_page"))

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
