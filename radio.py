"""Internet radio player via ffplay (PipeWire audio)."""

import subprocess
import logging
import threading

log = logging.getLogger("maya.radio")

# Mexican and general radio stations (online streams)
STATIONS = {
    "romantica": {
        "name": "Radio Romantica",
        "url": "https://stream.zeno.fm/yn65fsaurfhvv",
        "desc": "Baladas romanticas en español",
    },
    "clasica": {
        "name": "Radio Clasica UNAM",
        "url": "https://stream.zeno.fm/4d60am6ar1zuv",
        "desc": "Musica clasica",
    },
    "noticias": {
        "name": "Radio Formula",
        "url": "https://stream.zeno.fm/s850mfsp3fhvv",
        "desc": "Noticias Mexico",
    },
    "ranchera": {
        "name": "Radio Ranchera",
        "url": "https://stream.zeno.fm/e0n1fdaurfhvv",
        "desc": "Musica ranchera y mexicana",
    },
    "instrumental": {
        "name": "Radio Instrumental",
        "url": "https://stream.zeno.fm/0r0xa792kwzuv",
        "desc": "Piano y guitarra instrumental",
    },
}


class Radio:
    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._station: str | None = None
        self._lock = threading.Lock()

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def current_station(self) -> str | None:
        return self._station if self.playing else None

    def play(self, station_key: str) -> str | None:
        """Start playing a radio station. Returns station name or None on error."""
        key = station_key.strip().lower()
        station = STATIONS.get(key)
        if not station:
            # Try fuzzy match
            for k, v in STATIONS.items():
                if key in k or key in v["name"].lower() or key in v["desc"].lower():
                    station = v
                    key = k
                    break
        if not station:
            log.warning("Estacion no encontrada: %s", station_key)
            return None

        self.stop()

        try:
            # ffplay: no video, low log level, autoexit disabled for streaming
            self._process = subprocess.Popen(
                ["ffplay", "-nodisp", "-loglevel", "error", station["url"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._station = key
            log.info("Radio: %s (%s)", station["name"], station["url"])
            return station["name"]
        except FileNotFoundError:
            log.error("ffplay no encontrado — instalar con: sudo apt install ffmpeg")
            return None
        except Exception as e:
            log.error("Error iniciando radio: %s", e)
            return None

    def stop(self) -> bool:
        """Stop the currently playing station."""
        with self._lock:
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                except Exception:
                    pass
                self._process = None
                self._station = None
                log.info("Radio apagada")
                return True
        return False

    def list_stations(self) -> list[dict]:
        """Return list of available stations."""
        return [{"key": k, "name": v["name"], "desc": v["desc"]}
                for k, v in STATIONS.items()]
