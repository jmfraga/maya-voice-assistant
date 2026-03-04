"""Text-to-speech: ElevenLabs API (primary) + Piper TTS (fallback)."""

import os
import subprocess
import tempfile
import logging
import httpx

log = logging.getLogger("maya.tts")


class TTS:
    def __init__(self, config: dict):
        self.primary = config.get("primary", "elevenlabs")
        self.fallback = config.get("fallback", "piper")
        self.eleven_cfg = config.get("elevenlabs", {})
        self.piper_cfg = config.get("piper", {})

    def speak(self, text: str) -> str | None:
        """Generate speech from text. Returns path to WAV file or None."""
        path = None

        if self.primary == "elevenlabs":
            path = self._elevenlabs(text)
            if path is None and self.fallback == "piper":
                log.warning("ElevenLabs falló, usando Piper")
                path = self._piper(text)
        elif self.primary == "piper":
            path = self._piper(text)

        return path

    def _elevenlabs(self, text: str) -> str | None:
        """Generate speech using ElevenLabs API."""
        api_key = self.eleven_cfg.get("api_key", "")
        voice_id = self.eleven_cfg.get("voice_id", "")
        if not api_key or api_key == "ELEVENLABS_API_KEY":
            log.warning("ElevenLabs API key no configurada")
            return None
        if not voice_id or voice_id == "VOICE_ID":
            log.warning("ElevenLabs voice_id no configurado")
            return None

        model_id = self.eleven_cfg.get("model_id", "eleven_multilingual_v2")

        try:
            response = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": model_id,
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()

            path = tempfile.mktemp(suffix=".mp3", prefix="maya_tts_")
            with open(path, "wb") as f:
                f.write(response.content)

            log.info("ElevenLabs TTS ok (%d bytes)", len(response.content))
            return path

        except Exception as e:
            log.error("Error ElevenLabs: %s", e)
            return None

    def _piper(self, text: str) -> str | None:
        """Generate speech using local Piper TTS."""
        binary = os.path.expanduser(self.piper_cfg.get("binary", "~/.local/bin/piper"))
        model = os.path.expanduser(self.piper_cfg.get("model", ""))

        if not os.path.isfile(binary):
            log.error("Piper binary no encontrado: %s", binary)
            return None
        if not os.path.isfile(model):
            log.error("Piper model no encontrado: %s", model)
            return None

        path = tempfile.mktemp(suffix=".wav", prefix="maya_tts_")
        try:
            result = subprocess.run(
                [binary, "--model", model, "--output_file", path],
                input=text,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and os.path.isfile(path):
                log.info("Piper TTS ok")
                return path
            else:
                log.error("Piper error: %s", result.stderr[:200])
                return None
        except Exception as e:
            log.error("Error Piper: %s", e)
            return None
