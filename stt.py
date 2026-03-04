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

    def transcribe(self, wav_path: str) -> str | None:
        """Transcribe audio file. Try primary, then fallback."""
        text = None

        if self.primary == "openai_api":
            text = self._openai_api(wav_path)
            if text is None and self.fallback == "whisper_cpp":
                log.warning("OpenAI API falló, usando whisper.cpp")
                text = self._whisper_cpp(wav_path)
        elif self.primary == "whisper_cpp":
            text = self._whisper_cpp(wav_path)

        if text:
            text = text.strip()
            log.info("Transcripcion: %s", text[:80])
        else:
            log.warning("No se pudo transcribir")

        return text

    def _openai_api(self, wav_path: str) -> str | None:
        """Transcribe using OpenAI Whisper API via httpx."""
        api_key = self.openai_cfg.get("api_key", "")
        if not api_key or api_key == "OPENAI_API_KEY":
            log.warning("OpenAI API key no configurada")
            return None

        try:
            with open(wav_path, "rb") as f:
                response = httpx.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={
                        "model": self.openai_cfg.get("model", "whisper-1"),
                        "language": self.openai_cfg.get("language", "es"),
                        "response_format": "text",
                    },
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
