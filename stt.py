"""Speech-to-text: OpenAI Whisper API (primary) + whisper.cpp (fallback)."""

import os
import subprocess
import logging
import httpx

log = logging.getLogger("maya.stt")


class STT:
    def __init__(self, config: dict):
        self.primary = config.get("primary", "openai_api")
        self.fallback = config.get("fallback", "whisper_cpp")
        self.openai_cfg = config.get("openai_api", {})
        self.whisper_cpp_cfg = config.get("whisper_cpp", {})
        self.synapse_cfg = config.get("synapse", {})
        self.initial_prompt = ""

    def set_user_names(self, names: list[str]):
        """Set initial_prompt with user names to improve transcription accuracy."""
        if names:
            self.initial_prompt = "Nombres: " + ", ".join(names) + "."
            log.info("STT initial_prompt: %s", self.initial_prompt)

    def transcribe(self, wav_path: str) -> str | None:
        """Transcribe audio file. Try primary, then fallback chain."""
        providers = {
            "synapse": self._synapse,
            "openai_api": self._openai_api,
            "whisper_cpp": self._whisper_cpp,
        }

        primary_fn = providers.get(self.primary)
        if primary_fn:
            text = primary_fn(wav_path)
            if text:
                text = text.strip()
                log.info("Transcripcion: %s", text[:80])
                return text
            log.warning("%s falló, intentando fallback %s", self.primary, self.fallback)

        fallback_fn = providers.get(self.fallback)
        if fallback_fn and self.fallback != self.primary:
            text = fallback_fn(wav_path)
            if text:
                text = text.strip()
                log.info("Transcripcion (fallback): %s", text[:80])
                return text

        log.warning("No se pudo transcribir")
        return None

    def _synapse(self, wav_path: str) -> str | None:
        """Transcribe using Synapse (OpenAI-compatible Whisper endpoint)."""
        base_url = self.synapse_cfg.get("base_url", "")
        api_key = self.synapse_cfg.get("api_key", "")
        if not base_url or not api_key:
            log.warning("Synapse STT no configurado")
            return None

        try:
            with open(wav_path, "rb") as f:
                data = {
                    "model": self.synapse_cfg.get("model", "whisper-1"),
                    "language": "es",
                    "response_format": "text",
                }
                if self.initial_prompt:
                    data["prompt"] = self.initial_prompt
                response = httpx.post(
                    f"{base_url}/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data=data,
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.text
        except Exception as e:
            log.error("Error Synapse STT: %s", e)
            return None

    def _openai_api(self, wav_path: str) -> str | None:
        """Transcribe using OpenAI Whisper API via httpx."""
        api_key = self.openai_cfg.get("api_key", "")
        if not api_key or api_key == "OPENAI_API_KEY":
            log.warning("OpenAI API key no configurada")
            return None

        try:
            with open(wav_path, "rb") as f:
                data = {
                    "model": self.openai_cfg.get("model", "whisper-1"),
                    "language": self.openai_cfg.get("language", "es"),
                    "response_format": "text",
                }
                if self.initial_prompt:
                    data["prompt"] = self.initial_prompt
                response = httpx.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data=data,
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.text
        except Exception as e:
            log.error("Error OpenAI Whisper API: %s", e)
            return None

    def _whisper_cpp(self, wav_path: str) -> str | None:
        """Transcribe using local whisper.cpp."""
        binary = os.path.expanduser(self.whisper_cpp_cfg.get("binary", ""))
        model = os.path.expanduser(self.whisper_cpp_cfg.get("model", ""))

        if not os.path.isfile(binary):
            log.error("whisper.cpp binary no encontrado: %s", binary)
            return None
        if not os.path.isfile(model):
            log.error("whisper.cpp model no encontrado: %s", model)
            return None

        try:
            result = subprocess.run(
                [
                    binary,
                    "-m", model,
                    "-l", "es",
                    "-nt",  # no timestamps
                    "-f", wav_path,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                log.error("whisper.cpp error: %s", result.stderr[:200])
                return None
        except Exception as e:
            log.error("Error whisper.cpp: %s", e)
            return None
