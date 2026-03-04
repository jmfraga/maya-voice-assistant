"""Wake word detection using Porcupine."""

import os
import logging
import struct
import numpy as np
import sounddevice as sd

log = logging.getLogger("maya.wakeword")


class WakeWordDetector:
    def __init__(self, config: dict, device: int | None = None):
        import pvporcupine
        keyword_path = config["keyword_path"]
        if not os.path.isabs(keyword_path):
            keyword_path = os.path.join(os.path.dirname(__file__), keyword_path)

        self.porcupine = pvporcupine.create(
            access_key=config["access_key"],
            keyword_paths=[keyword_path],
            sensitivities=[config.get("sensitivity", 0.6)],
        )
        self.sample_rate = self.porcupine.sample_rate
        self.frame_length = self.porcupine.frame_length
        self.device = device
        self._running = False
        log.info(
            "Porcupine init: rate=%d, frame=%d",
            self.sample_rate, self.frame_length,
        )

    def listen(self) -> bool:
        """Block until wake word is detected. Returns True on detection, False on stop."""
        self._running = True
        log.info("Escuchando wake word...")

        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self.frame_length,
                device=self.device,
            )
            stream.start()

            while self._running:
                data, overflowed = stream.read(self.frame_length)
                if overflowed:
                    continue

                pcm = struct.unpack_from(
                    "h" * self.frame_length,
                    data.tobytes(),
                )
                result = self.porcupine.process(pcm)
                if result >= 0:
                    log.info("Wake word detectado!")
                    stream.stop()
                    stream.close()
                    return True

            stream.stop()
            stream.close()
            return False

        except Exception as e:
            log.error("Error en wake word: %s", e)
            return False

    def stop(self):
        """Signal the listen loop to stop."""
        self._running = False

    def cleanup(self):
        """Release Porcupine resources."""
        if hasattr(self, "porcupine") and self.porcupine:
            self.porcupine.delete()
            self.porcupine = None
            log.info("Porcupine cleanup")
