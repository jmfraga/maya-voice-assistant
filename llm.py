"""LLM module: Claude API / OpenAI API with context building and action parsing."""

import re
import logging
from datetime import datetime

log = logging.getLogger("maya.llm")

ACTION_PATTERN = re.compile(r"\[ACCION:([^\]]+)\]")


def parse_actions(text: str) -> tuple[str, list[dict]]:
    """Extract action tags from LLM response. Returns (clean_text, actions)."""
    actions = []
    for match in ACTION_PATTERN.finditer(text):
        parts = match.group(1).split(":")
        if len(parts) >= 2:
            action = {"type": parts[0].strip()}
            if action["type"] == "TELEGRAM" and len(parts) >= 3:
                action["recipient"] = parts[1].strip()
                action["message"] = ":".join(parts[2:]).strip()
            elif action["type"] == "MEDICAMENTO" and len(parts) >= 4:
                action["name"] = parts[1].strip()
                action["dosage"] = parts[2].strip()
                action["schedule"] = parts[3].strip()
            elif action["type"] == "RECORDATORIO" and len(parts) >= 3:
                action["text"] = parts[1].strip()
                action["time"] = parts[2].strip()
            elif action["type"] == "CONTACTO" and len(parts) >= 4:
                action["name"] = parts[1].strip()
                action["phone"] = parts[2].strip()
                action["relationship"] = parts[3].strip()
            elif action["type"] == "CONFIRMAR_MEDICAMENTO" and len(parts) >= 2:
                action["name"] = parts[1].strip()
            elif action["type"] == "MEMORIA" and len(parts) >= 3:
                action["category"] = parts[1].strip()
                action["content"] = ":".join(parts[2:]).strip()
            actions.append(action)

    clean = ACTION_PATTERN.sub("", text).strip()
    return clean, actions


class LLM:
    def __init__(self, config: dict, assistant_config: dict):
        self.provider = config.get("primary", "claude")
        self.config = config
        self.system_prompt = assistant_config.get("system_prompt", "")
        self.assistant_name = assistant_config.get("name", "Maya")
        self._client = None
        self._init_client()

    def _init_client(self):
        """Initialize the appropriate LLM client."""
        if self.provider == "claude":
            import anthropic
            cfg = self.config.get("claude", {})
            self._client = anthropic.Anthropic(api_key=cfg.get("api_key", ""))
            self.model = cfg.get("model", "claude-sonnet-4-20250514")
            self.max_tokens = cfg.get("max_tokens", 500)
        elif self.provider == "openai":
            cfg = self.config.get("openai", {})
            self._api_key = cfg.get("api_key", "")
            self.model = cfg.get("model", "gpt-4o-mini")
            self.max_tokens = cfg.get("max_tokens", 500)
        else:
            log.error("Proveedor LLM desconocido: %s", self.provider)

    def build_context(self, user_name: str, db=None, user_id: str | None = None) -> str:
        """Build context string with user info, medications, reminders, memories."""
        parts = [f"Usuario actual: {user_name}"]
        now = datetime.now()
        parts.append(f"Fecha y hora: {now.strftime('%A %d de %B de %Y, %H:%M')}")

        if db and user_id:
            # Medicamentos (detallado)
            meds = db.get_medications(user_id)
            if meds:
                parts.append(f"Medicamentos de {user_name}:")
                for m in meds:
                    parts.append(f"  - {m['name']}: {m['dosage']}, horario: {m['schedule']}")

            # Tomas de hoy
            today = now.strftime("%Y-%m-%d")
            med_log = db.get_medication_log(user_id, date=today)
            if med_log:
                taken = [f"{ml['med_name']} ({ml['taken_at'][-5:]})" for ml in med_log]
                parts.append(f"Medicamentos tomados hoy: {', '.join(taken)}")

            # Recordatorios pendientes
            reminders = db.get_pending_reminders(user_id)
            if reminders:
                rem_list = ", ".join(
                    f"{r['text']} a las {r['remind_at']}" for r in reminders
                )
                parts.append(f"Recordatorios pendientes: {rem_list}")

            # Memorias
            memories = db.get_memories(user_id, limit=30)
            if memories:
                parts.append("Cosas que recuerdo de ti:")
                for mem in memories:
                    parts.append(f"  - [{mem['category']}] {mem['content']}")

            # Historial expandido (20 msgs, 300 chars)
            history = db.get_recent_conversations(user_id, limit=20)
            if history:
                parts.append("Conversacion reciente:")
                for msg in history:
                    role = "Usuario" if msg["role"] == "user" else self.assistant_name
                    parts.append(f"  {role}: {msg['content'][:300]}")

            # Contactos
            contacts = db.get_contacts(user_id)
            if contacts:
                contact_list = ", ".join(
                    f"{c['name']} ({c['relationship']})" for c in contacts
                )
                parts.append(f"Contactos: {contact_list}")

        return "\n".join(parts)

    def chat(self, user_text: str, user_name: str = "Usuario",
             db=None, user_id: str | None = None) -> tuple[str, list[dict]]:
        """Send message to LLM, return (response_text, actions)."""
        context = self.build_context(user_name, db, user_id)
        system = f"{self.system_prompt}\n\n--- Contexto ---\n{context}"

        try:
            if self.provider == "claude":
                full_text = self._chat_claude(system, user_text)
            elif self.provider == "openai":
                full_text = self._chat_openai(system, user_text)
            else:
                return "Proveedor LLM no configurado.", []

            clean_text, actions = parse_actions(full_text)
            if actions:
                log.info("Acciones detectadas: %s", [a["type"] for a in actions])
            return clean_text, actions

        except Exception as e:
            log.error("Error LLM (%s): %s", self.provider, e)
            return "Disculpe, tuve un problema. ¿Puede repetir?", []

    def _chat_claude(self, system: str, user_text: str) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        return response.content[0].text

    def _chat_openai(self, system: str, user_text: str) -> str:
        import httpx
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
