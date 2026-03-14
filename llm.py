"""LLM module: Claude API / OpenAI API with context building and action parsing."""

import re
import logging
from datetime import datetime

log = logging.getLogger("maya.llm")

_DAYS_ES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
_MONTHS_ES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
              "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def _fecha_es(dt: datetime) -> str:
    """Format datetime as 'Sabado 14 de Marzo de 2026, 15:30' in Spanish."""
    day_name = _DAYS_ES[dt.weekday()]
    month_name = _MONTHS_ES[dt.month]
    return f"{day_name} {dt.day} de {month_name} de {dt.year}, {dt.strftime('%H:%M')}"

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
            elif action["type"] == "CONSULTA_TRATAMIENTO" and len(parts) >= 3:
                action["measurement"] = parts[1].strip()
                action["value"] = parts[2].strip()
            elif action["type"] == "MENSAJE_PENDIENTE" and len(parts) >= 2:
                action["message"] = ":".join(parts[1:]).strip()
            actions.append(action)

    clean = ACTION_PATTERN.sub("", text).strip()
    return clean, actions


class LLM:
    def __init__(self, config: dict, assistant_config: dict):
        self.provider = config.get("primary", "claude")
        self.fallback_provider = config.get("fallback", "")
        self.config = config
        self.system_prompt = assistant_config.get("system_prompt", "")
        self.assistant_name = assistant_config.get("name", "Maya")
        self._client = None
        self._synapse_cfg = config.get("synapse", {})
        self._init_client()

    def _init_client(self):
        """Initialize the appropriate LLM client."""
        if self.provider == "claude":
            self._init_claude()
        elif self.provider == "openai":
            self._init_openai()
        elif self.provider == "synapse":
            self._init_synapse()
            # Also init fallback
            if self.fallback_provider == "claude":
                self._init_claude()
            elif self.fallback_provider == "openai":
                self._init_openai()
        else:
            log.error("Proveedor LLM desconocido: %s", self.provider)

    def _init_claude(self):
        import anthropic
        cfg = self.config.get("claude", {})
        self._client = anthropic.Anthropic(api_key=cfg.get("api_key", ""))
        self._claude_model = cfg.get("model", "claude-sonnet-4-20250514")
        self._claude_max_tokens = cfg.get("max_tokens", 500)
        if self.provider == "claude":
            self.model = self._claude_model
            self.max_tokens = self._claude_max_tokens

    def _init_openai(self):
        cfg = self.config.get("openai", {})
        self._openai_api_key = cfg.get("api_key", "")
        self._openai_model = cfg.get("model", "gpt-4o-mini")
        self._openai_max_tokens = cfg.get("max_tokens", 500)
        if self.provider == "openai":
            self.model = self._openai_model
            self.max_tokens = self._openai_max_tokens

    def _init_synapse(self):
        cfg = self._synapse_cfg
        self._synapse_base_url = cfg.get("base_url", "")
        self._synapse_api_key = cfg.get("api_key", "")
        self.model = cfg.get("model", "maya-auto")
        self.max_tokens = cfg.get("max_tokens", 500)

    def build_context(self, user_name: str, db=None, user_id: str | None = None,
                       weather=None) -> str:
        """Build context string with user info, medications, reminders, memories, weather."""
        parts = [f"Usuario actual: {user_name}"]
        now = datetime.now()
        parts.append(f"Fecha y hora: {_fecha_es(now)}")

        # Weather
        if weather:
            w = weather.data if hasattr(weather, 'data') else weather
            if w:
                parts.append(f"Clima en {w.get('city', '')}: {w.get('temp')}°C, "
                             f"{w.get('description', '')}, "
                             f"sensacion termica {w.get('feels_like')}°C, "
                             f"humedad {w.get('humidity')}%")

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

            # Esquemas de tratamiento
            schemas = db.get_treatment_schemas(user_id)
            for schema in schemas:
                ranges = db.get_treatment_ranges(schema["id"])
                if ranges:
                    unit = schema["measurement_unit"]
                    parts.append(f"Esquema de tratamiento: {schema['name']} (segun {schema['measurement_name']})")
                    range_parts = []
                    for r in ranges:
                        range_parts.append(f"{r['range_min']}-{r['range_max']}{unit} -> {r['dose']} {r['dose_unit']}")
                    parts.append(f"  Rangos: {', '.join(range_parts)}")
                    if schema["alert_low"] is not None or schema["alert_high"] is not None:
                        parts.append(f"  Alerta si <{schema['alert_low']} o >{schema['alert_high']}")

            # Contactos
            contacts = db.get_contacts(user_id)
            if contacts:
                contact_list = ", ".join(
                    f"{c['name']} ({c['relationship']})" for c in contacts
                )
                parts.append(f"Contactos: {contact_list}")

        return "\n".join(parts)

    def chat(self, user_text: str, user_name: str = "Usuario",
             db=None, user_id: str | None = None,
             weather=None) -> tuple[str, list[dict]]:
        """Send message to LLM, return (response_text, actions)."""
        context = self.build_context(user_name, db, user_id, weather=weather)
        system = f"{self.system_prompt}\n\n--- Contexto ---\n{context}"

        providers = {
            "synapse": self._chat_synapse,
            "claude": self._chat_claude,
            "openai": self._chat_openai,
        }

        # Try primary
        primary_fn = providers.get(self.provider)
        if primary_fn:
            try:
                full_text = primary_fn(system, user_text)
                clean_text, actions = parse_actions(full_text)
                if actions:
                    log.info("Acciones detectadas: %s", [a["type"] for a in actions])
                return clean_text, actions
            except Exception as e:
                log.error("Error LLM %s: %s", self.provider, e)
                # Try fallback
                if self.fallback_provider:
                    fallback_fn = providers.get(self.fallback_provider)
                    if fallback_fn:
                        try:
                            log.info("Usando fallback LLM: %s", self.fallback_provider)
                            full_text = fallback_fn(system, user_text)
                            clean_text, actions = parse_actions(full_text)
                            if actions:
                                log.info("Acciones detectadas (fallback): %s", [a["type"] for a in actions])
                            return clean_text, actions
                        except Exception as e2:
                            log.error("Error LLM fallback %s: %s", self.fallback_provider, e2)

        return "Disculpe, tuve un problema. ¿Puede repetir?", []

    def _chat_synapse(self, system: str, user_text: str) -> str:
        """Chat via Synapse (OpenAI-compatible with Smart Route)."""
        import httpx
        response = httpx.post(
            f"{self._synapse_base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._synapse_api_key}",
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

    def chat_telegram(self, text: str, contact_name: str, relationship: str,
                      db=None, user_ids: list[str] = None,
                      chat_history: list[dict] = None) -> tuple[str, list[dict]]:
        """Chat with a family member via Telegram about their loved one(s)."""
        context_parts = [
            f"Hablando con: {contact_name} ({relationship})",
            f"Fecha y hora: {_fecha_es(datetime.now())}",
        ]

        user_names = []
        if db and user_ids:
            for uid in user_ids:
                user = db.get_user(uid)
                if not user:
                    continue
                uname = user["real_name"]
                user_names.append(uname)
                context_parts.append(f"\n--- Informacion de {uname} ---")

                meds = db.get_medications(uid)
                if meds:
                    context_parts.append(f"Medicamentos de {uname}:")
                    for m in meds:
                        context_parts.append(f"  - {m['name']}: {m['dosage']}, horario: {m['schedule']}")

                today = datetime.now().strftime("%Y-%m-%d")
                med_log = db.get_medication_log(uid, date=today)
                if med_log:
                    taken = [f"{ml['med_name']} ({ml['taken_at'][-5:]})" for ml in med_log]
                    context_parts.append(f"Medicamentos tomados hoy: {', '.join(taken)}")

                reminders = db.get_pending_reminders(uid)
                if reminders:
                    rem_list = ", ".join(f"{r['text']} a las {r['remind_at']}" for r in reminders)
                    context_parts.append(f"Recordatorios: {rem_list}")

                memories = db.get_memories(uid, limit=20)
                if memories:
                    context_parts.append(f"Memorias de {uname}:")
                    for mem in memories:
                        context_parts.append(f"  - [{mem['category']}] {mem['content']}")

                contacts = db.get_contacts(uid)
                if contacts:
                    contact_list = ", ".join(f"{c['name']} ({c['relationship']})" for c in contacts)
                    context_parts.append(f"Contactos: {contact_list}")

        if chat_history:
            context_parts.append("\nConversacion reciente en Telegram:")
            for msg in chat_history:
                role = contact_name if msg["role"] == "user" else self.assistant_name
                context_parts.append(f"  {role}: {msg['content'][:300]}")

        context = "\n".join(context_parts)
        users_str = " y ".join(user_names) if user_names else "los usuarios"

        system = (
            f"Eres {self.assistant_name}, asistente virtual de la familia. "
            f"Estas hablando por Telegram con {contact_name}, quien es {relationship} de {users_str}.\n\n"
            f"Tu rol es:\n"
            f"- Informar sobre el estado, medicamentos, recordatorios y bienestar de {users_str}\n"
            f"- Recibir mensajes para entregar a {users_str} cuando hablen contigo por voz\n"
            f"- Ser calida, concisa y tranquilizadora\n\n"
            f"Si {contact_name} quiere enviar un mensaje a alguno de los usuarios, usa:\n"
            f"[ACCION:MENSAJE_PENDIENTE:el mensaje a entregar]\n\n"
            f"Responde siempre en espanol mexicano, de forma breve y util.\n\n"
            f"--- Contexto ---\n{context}"
        )

        try:
            if self.provider == "claude":
                full_text = self._chat_claude(system, text)
            elif self.provider == "openai":
                full_text = self._chat_openai(system, text)
            else:
                return "Proveedor LLM no configurado.", []

            clean_text, actions = parse_actions(full_text)
            if actions:
                log.info("Telegram acciones: %s", [a["type"] for a in actions])
            return clean_text, actions

        except Exception as e:
            log.error("Error LLM telegram: %s", e)
            return "Disculpa, tuve un problema. Intenta de nuevo.", []
