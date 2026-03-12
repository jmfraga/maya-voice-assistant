"""Telegram Bot API - Bidirectional chat, voice messages, contact self-registration."""

import logging
import os
import subprocess
import tempfile
import threading
import time
import httpx

log = logging.getLogger("maya.telegram")

BASE_URL = "https://api.telegram.org/bot{token}"

# Conversation states for /start registration flow
CONV_IDLE = 0
CONV_AWAITING_NAME = 1
CONV_AWAITING_RELATIONSHIP = 2


class TelegramBot:
    def __init__(self, config: dict, db=None, llm=None, stt=None):
        self.token = config.get("bot_token", "")
        self.contacts = config.get("contacts", {})
        self.base_url = BASE_URL.format(token=self.token)
        self.db = db
        self.llm = llm
        self.stt = stt
        self._conversations = {}  # chat_id -> {state, name}
        self._polling = False
        self._poll_thread = None

    def _is_configured(self) -> bool:
        return bool(self.token) and self.token != "TELEGRAM_BOT_TOKEN"

    # --- Sending ---
    def send_message(self, recipient: str, text: str) -> bool:
        """Send message to a contact by name. Checks DB contacts too."""
        if not self._is_configured():
            log.warning("Telegram bot token no configurado")
            return False

        chat_id = self._resolve_recipient(recipient)
        if not chat_id:
            log.warning("Contacto Telegram no encontrado: %s", recipient)
            return False

        return self.send_to_chat_id(chat_id, text)

    def _resolve_recipient(self, recipient: str) -> int | None:
        """Resolve recipient name to chat_id from config and DB."""
        # Config contacts
        chat_id = self.contacts.get(recipient)
        if chat_id:
            return chat_id
        for name, cid in self.contacts.items():
            if name.lower() == recipient.lower():
                return cid

        # DB contacts (all users)
        if self.db:
            for user in self.db.get_users():
                for c in self.db.get_contacts(user["id"]):
                    if c["name"].lower() == recipient.lower() and c["telegram_chat_id"]:
                        return c["telegram_chat_id"]
        return None

    def notify_admins(self, text: str):
        """Send notification to all configured contacts (admin/family)."""
        if not self._is_configured():
            return
        for name, chat_id in self.contacts.items():
            try:
                self.send_to_chat_id(int(chat_id), text)
            except (ValueError, TypeError):
                pass

    def send_to_chat_id(self, chat_id: int, text: str) -> bool:
        """Send message directly to a chat_id."""
        if not self._is_configured():
            return False
        try:
            response = httpx.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10.0,
            )
            data = response.json()
            if data.get("ok"):
                log.info("Mensaje enviado a chat_id=%d", chat_id)
                return True
            log.error("Telegram error: %s", data)
            return False
        except Exception as e:
            log.error("Error enviando Telegram: %s", e)
            return False

    # --- Polling for incoming messages ---
    def start_polling(self):
        """Start background polling thread for incoming messages."""
        if not self._is_configured():
            log.warning("Telegram no configurado, polling desactivado")
            return
        if self._polling:
            return

        # Clear any existing webhook/polling session
        try:
            httpx.post(f"{self.base_url}/deleteWebhook",
                       json={"drop_pending_updates": True}, timeout=5.0)
        except Exception:
            pass

        # Set bot commands menu
        try:
            httpx.post(f"{self.base_url}/setMyCommands", json={
                "commands": [
                    {"command": "estado", "description": "Resumen del dia"},
                    {"command": "medicamentos", "description": "Medicamentos y tomas de hoy"},
                    {"command": "recordatorios", "description": "Recordatorios pendientes"},
                    {"command": "ayuda", "description": "Comandos disponibles"},
                ]
            }, timeout=5.0)
            log.info("Comandos de Telegram configurados")
        except Exception:
            pass

        self._polling = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        log.info("Telegram polling iniciado")

    def stop_polling(self):
        self._polling = False

    def _poll_loop(self):
        offset = 0
        while self._polling:
            try:
                response = httpx.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": offset, "timeout": 30,
                            "allowed_updates": ["message"]},
                    timeout=35.0,
                )
                data = response.json()
                if not data.get("ok"):
                    log.error("Polling error: %s", data)
                    time.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if msg:
                        self._handle_message(msg)

            except httpx.TimeoutException:
                continue
            except Exception as e:
                log.error("Error en polling: %s", e)
                time.sleep(5)

    # --- Message routing ---
    def _handle_message(self, msg: dict):
        """Route incoming Telegram message."""
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        first_name = msg["chat"].get("first_name", "")

        # Commands
        if text == "/start":
            self._start_registration(chat_id, first_name)
            return

        if text in ("/estado", "/status"):
            self._cmd_estado(chat_id)
            return

        if text in ("/medicamentos", "/meds"):
            self._cmd_medicamentos(chat_id)
            return

        if text in ("/recordatorios", "/reminders"):
            self._cmd_recordatorios(chat_id)
            return

        if text in ("/ayuda", "/help"):
            self._cmd_ayuda(chat_id)
            return

        # Registration flow in progress
        if chat_id in self._conversations:
            self._continue_registration(chat_id, text)
            return

        # Voice message
        if msg.get("voice"):
            self._handle_voice(chat_id, msg["voice"])
            return

        # Regular text chat — only for registered contacts
        if text:
            self._handle_chat(chat_id, text)

    # --- Contact lookup ---
    def _get_contact_info(self, chat_id: int) -> dict | None:
        """Find registered contact by chat_id. Checks DB contacts and config contacts."""
        if not self.db:
            return None

        # Check DB contacts first
        contacts = self.db.get_contacts_by_chat_id(chat_id)
        if contacts:
            first = contacts[0]
            user_ids = list({c["user_id"] for c in contacts})
            return {
                "name": first["name"],
                "relationship": first["relationship"],
                "user_ids": user_ids,
            }

        # Fallback: check config contacts (name -> chat_id mapping)
        for name, cid in self.contacts.items():
            if int(cid) == chat_id:
                # Config contact — link to all users
                all_users = [u["id"] for u in self.db.get_users()]
                return {
                    "name": name,
                    "relationship": "familiar",
                    "user_ids": all_users,
                }

        return None

    def _require_registered(self, chat_id: int) -> dict | None:
        """Check if chat_id is a registered contact. Send rejection if not."""
        info = self._get_contact_info(chat_id)
        if not info:
            self.send_to_chat_id(
                chat_id,
                "No estas registrado como contacto. "
                "Escribe /start para solicitar acceso."
            )
            return None
        return info

    # --- Chat with Maya ---
    def _handle_chat(self, chat_id: int, text: str):
        """Handle a regular text message from a registered contact."""
        contact = self._require_registered(chat_id)
        if not contact:
            return

        if not self.llm:
            self.send_to_chat_id(chat_id, "Maya no esta disponible en este momento.")
            return

        # Save user message
        self.db.save_telegram_conversation(chat_id, "user", text)

        # Get conversation history
        history = self.db.get_telegram_history(chat_id, limit=10)

        # LLM response
        response, actions = self.llm.chat_telegram(
            text,
            contact_name=contact["name"],
            relationship=contact["relationship"],
            db=self.db,
            user_ids=contact["user_ids"],
            chat_history=history,
        )

        # Process actions
        self._process_actions(actions, contact)

        # Save and send response
        self.db.save_telegram_conversation(chat_id, "assistant", response)
        self.send_to_chat_id(chat_id, response)

    # --- Voice messages ---
    def _handle_voice(self, chat_id: int, voice: dict):
        """Handle incoming voice message: download, transcribe, respond."""
        contact = self._require_registered(chat_id)
        if not contact:
            return

        if not self.llm or not self.stt:
            self.send_to_chat_id(chat_id, "Maya no puede procesar audio en este momento.")
            return

        file_id = voice.get("file_id")
        if not file_id:
            return

        self.send_to_chat_id(chat_id, "Escuchando tu mensaje de voz...")

        try:
            # Get file path from Telegram
            resp = httpx.get(
                f"{self.base_url}/getFile",
                params={"file_id": file_id},
                timeout=10.0,
            )
            file_info = resp.json()
            if not file_info.get("ok"):
                self.send_to_chat_id(chat_id, "No pude descargar el audio.")
                return

            file_path = file_info["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"

            # Download OGG file
            audio_resp = httpx.get(download_url, timeout=30.0)
            audio_resp.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
                ogg_file.write(audio_resp.content)
                ogg_path = ogg_file.name

            # Convert OGG to WAV using ffmpeg
            wav_path = ogg_path.replace(".ogg", ".wav")
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
                capture_output=True, timeout=30,
            )

            os.unlink(ogg_path)

            if result.returncode != 0:
                log.error("ffmpeg error: %s", result.stderr[:200])
                self.send_to_chat_id(chat_id, "No pude convertir el audio.")
                return

            # Transcribe
            text = self.stt.transcribe(wav_path)
            os.unlink(wav_path)

            if not text:
                self.send_to_chat_id(chat_id, "No pude entender el mensaje de voz.")
                return

            log.info("Telegram voz transcrito: %s", text[:80])

            # Process as regular chat
            self._handle_chat(chat_id, text)

        except Exception as e:
            log.error("Error procesando voz Telegram: %s", e)
            self.send_to_chat_id(chat_id, "Hubo un error al procesar tu mensaje de voz.")

    # --- Actions ---
    def _process_actions(self, actions: list[dict], contact: dict):
        """Process LLM actions from Telegram chat (mainly MENSAJE_PENDIENTE)."""
        if not actions or not self.db:
            return

        for action in actions:
            atype = action.get("type", "")
            if atype == "MENSAJE_PENDIENTE":
                message = action.get("message", "")
                if not message:
                    continue
                # Save pending message for each user this contact is linked to
                for uid in contact.get("user_ids", []):
                    self.db.add_pending_message(uid, contact["name"], message)
                log.info("Mensaje pendiente de %s: %s", contact["name"], message[:50])

    # --- Commands ---
    def _cmd_estado(self, chat_id: int):
        """Send a quick status summary for each linked user."""
        contact = self._require_registered(chat_id)
        if not contact:
            return

        lines = []
        for uid in contact["user_ids"]:
            user = self.db.get_user(uid)
            if not user:
                continue
            uname = user["real_name"]
            lines.append(f"<b>{uname}</b>")

            # Medications taken today
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            med_log = self.db.get_medication_log(uid, date=today)
            total_meds = len(self.db.get_medications(uid))
            taken = len(med_log) if med_log else 0
            lines.append(f"  Medicamentos: {taken}/{total_meds} tomados hoy")

            # Pending reminders
            reminders = self.db.get_pending_reminders(uid)
            if reminders:
                lines.append(f"  Recordatorios pendientes: {len(reminders)}")
            else:
                lines.append("  Sin recordatorios pendientes")

            # Last conversation
            history = self.db.get_recent_conversations(uid, limit=1)
            if history:
                last = history[-1]
                lines.append(f"  Ultima interaccion: {last['created_at']}")

            lines.append("")

        if not lines:
            self.send_to_chat_id(chat_id, "No hay informacion disponible.")
            return

        self.send_to_chat_id(chat_id, "\n".join(lines))

    def _cmd_medicamentos(self, chat_id: int):
        """Send medication list for each linked user."""
        contact = self._require_registered(chat_id)
        if not contact:
            return

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        lines = []

        for uid in contact["user_ids"]:
            user = self.db.get_user(uid)
            if not user:
                continue
            uname = user["real_name"]
            lines.append(f"<b>{uname}</b>")

            meds = self.db.get_medications(uid)
            med_log = self.db.get_medication_log(uid, date=today)
            taken_names = {ml["med_name"] for ml in (med_log or [])}

            if not meds:
                lines.append("  Sin medicamentos registrados")
            else:
                for m in meds:
                    check = "✅" if m["name"] in taken_names else "⬜"
                    lines.append(f"  {check} {m['name']} — {m['dosage']}, {m['schedule']}")
            lines.append("")

        self.send_to_chat_id(chat_id, "\n".join(lines) if lines else "Sin informacion.")

    def _cmd_recordatorios(self, chat_id: int):
        """Send pending reminders for each linked user."""
        contact = self._require_registered(chat_id)
        if not contact:
            return

        lines = []
        for uid in contact["user_ids"]:
            user = self.db.get_user(uid)
            if not user:
                continue
            uname = user["real_name"]
            lines.append(f"<b>{uname}</b>")

            reminders = self.db.get_pending_reminders(uid)
            if not reminders:
                lines.append("  Sin recordatorios pendientes")
            else:
                for r in reminders:
                    lines.append(f"  ⏰ {r['remind_at']} — {r['text']}")
            lines.append("")

        self.send_to_chat_id(chat_id, "\n".join(lines) if lines else "Sin recordatorios.")

    def _cmd_ayuda(self, chat_id: int):
        """Send help text with available commands."""
        help_text = (
            "<b>Comandos disponibles:</b>\n\n"
            "/estado — Resumen del dia\n"
            "/medicamentos — Medicamentos y tomas de hoy\n"
            "/recordatorios — Recordatorios pendientes\n"
            "/ayuda — Este mensaje\n\n"
            "<b>Tambien puedes:</b>\n"
            "- Escribir cualquier pregunta sobre tus seres queridos\n"
            "- Enviar un mensaje de voz\n"
            "- Pedir que Maya les entregue un mensaje"
        )
        self.send_to_chat_id(chat_id, help_text)

    # --- Registration flow ---
    def _start_registration(self, chat_id: int, first_name: str):
        """Begin the contact registration flow."""
        if not self.db:
            return

        # Check if already registered as contact
        info = self._get_contact_info(chat_id)
        if info:
            self.send_to_chat_id(
                chat_id,
                f"Hola {info['name']}! Ya estas registrado.\n\n"
                "Puedes escribirme o enviar un mensaje de voz.\n"
                "Escribe /ayuda para ver los comandos disponibles."
            )
            return

        # Check if pending
        if self.db.is_chat_id_registered(chat_id):
            self.send_to_chat_id(
                chat_id,
                "Ya tienes una solicitud pendiente. "
                "El administrador te avisara cuando seas aprobado."
            )
            return

        self._conversations[chat_id] = {"state": CONV_AWAITING_NAME, "name": ""}
        self.send_to_chat_id(
            chat_id,
            f"Hola{' ' + first_name if first_name else ''}! "
            "Soy Maya, asistente de los Abuelos Fraga.\n\n"
            "Para registrarte como contacto, necesito algunos datos.\n\n"
            "¿Cual es tu nombre completo?"
        )

    def _continue_registration(self, chat_id: int, text: str):
        """Continue the multi-step registration."""
        conv = self._conversations[chat_id]

        if conv["state"] == CONV_AWAITING_NAME:
            conv["name"] = text
            conv["state"] = CONV_AWAITING_RELATIONSHIP
            self.send_to_chat_id(
                chat_id,
                f"Gracias, {text}.\n\n"
                "¿Cual es tu relacion con los Abuelos Fraga?\n"
                "(Ejemplo: hijo, hija, nieto, nieta, vecino, doctor, etc.)"
            )

        elif conv["state"] == CONV_AWAITING_RELATIONSHIP:
            name = conv["name"]
            relationship = text
            del self._conversations[chat_id]

            # Save to pending_contacts
            self.db.add_pending_contact(chat_id, name, relationship)

            self.send_to_chat_id(
                chat_id,
                f"Listo, {name}! Tu solicitud fue enviada.\n\n"
                "El administrador revisara tu solicitud. "
                "Te avisare cuando seas aprobado."
            )
            log.info("Nueva solicitud de contacto: %s (%s), chat_id=%d",
                     name, relationship, chat_id)
