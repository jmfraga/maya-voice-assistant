"""Wake word detection using Porcupine."""

import os
import subprocess
import logging
import struct
import numpy as np

log = logging.getLogger("maya.wakeword")


class WakeWordDetector:
    def __init__(self, config: dict):
        import pvporcupine
        keyword_path = config["keyword_path"]
        if not os.path.isabs(keyword_path):
            keyword_path = os.path.join(os.path.dirname(__file__), keyword_path)

        # Use Spanish model if available
        model_path = config.get("model_path")
        if model_path and not os.path.isabs(model_path):
            model_path = os.path.join(os.path.dirname(__file__), model_path)
        if not model_path or not os.path.isfile(model_path):
            # Auto-detect: look for porcupine_params_es.pv next to this script
            auto_es = os.path.join(os.path.dirname(__file__), "porcupine_params_es.pv")
            if os.path.isfile(auto_es):
                model_path = auto_es

        self.porcupine = pvporcupine.create(
            access_key=config["access_key"],
            keyword_paths=[keyword_path],
            model_path=model_path,
            sensitivities=[config.get("sensitivity", 0.6)],
        )
        self.sample_rate = self.porcupine.sample_rate
        self.frame_length = self.porcupine.frame_length
        self._running = False
        self._proc = None
        log.info(
            "Porcupine init: rate=%d, frame=%d, model=%s",
            self.sample_rate, self.frame_length, model_path or "default",
        )

    def listen(self) -> bool:
        """Block until wake word is detected. Returns True on detection, False on stop."""
        self._running = True
        log.info("Escuchando wake word...")
        frame_bytes = self.frame_length * 2  # 16-bit = 2 bytes/sample

        try:
            self._proc = subprocess.Popen(
                ["pw-record", "--format=s16", "--rate=%d" % self.sample_rate,
                 "--channels=1", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            while self._running:
                raw = self._proc.stdout.read(frame_bytes)
                if not raw or len(raw) < frame_bytes:
                    break

                pcm = struct.unpack_from("h" * self.frame_length, raw)
                result = self.porcupine.process(pcm)
                if result >= 0:
                    log.info("Wake word detectado!")
                    self._kill_proc()
                    return True

            self._kill_proc()
            return False

        except Exception as e:
            log.error("Error en wake word: %s", e)
            self._kill_proc()
            return False

    def _kill_proc(self):
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def stop(self):
        """Signal the listen loop to stop."""
        self._running = False

    def cleanup(self):
        """Release Porcupine resources."""
        if hasattr(self, "porcupine") and self.porcupine:
            self.porcupine.delete()
            self.porcupine = None
            log.info("Porcupine cleanup")
