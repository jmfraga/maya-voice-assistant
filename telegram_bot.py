"""Telegram Bot API - Send messages + contact self-registration via polling."""

import logging
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
    def __init__(self, config: dict, db=None):
        self.token = config.get("bot_token", "")
        self.contacts = config.get("contacts", {})
        self.base_url = BASE_URL.format(token=self.token)
        self.db = db
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
                    params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
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

    def _handle_message(self, msg: dict):
        """Handle an incoming Telegram message."""
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()
        first_name = msg["chat"].get("first_name", "")

        if text == "/start":
            self._start_registration(chat_id, first_name)
        elif chat_id in self._conversations:
            self._continue_registration(chat_id, text)

    def _start_registration(self, chat_id: int, first_name: str):
        """Begin the contact registration flow."""
        if not self.db:
            return

        # Check if already registered
        if self.db.is_chat_id_registered(chat_id):
            self.send_to_chat_id(
                chat_id,
                "Ya tienes una solicitud pendiente o ya estas registrado. "
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
