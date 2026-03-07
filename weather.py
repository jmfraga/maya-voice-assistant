"""Weather module using OpenWeatherMap API."""

import logging
import threading
import time

import httpx

log = logging.getLogger("maya.weather")


class Weather:
    """Fetches current weather from OpenWeatherMap every 30 minutes."""

    def __init__(self, api_key: str, city: str = "", units: str = "metric",
                 lang: str = "es"):
        self.api_key = api_key
        self.city = city
        self.units = units
        self.lang = lang
        self._data = None
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        if not self.api_key or not self.city:
            log.warning("Weather: falta api_key o city, deshabilitado")
            return
        self._running = True
        threading.Thread(target=self._update_loop, daemon=True).start()

    def stop(self):
        self._running = False

    def set_city(self, city: str):
        self.city = city
        if self._running:
            threading.Thread(target=self._fetch, daemon=True).start()

    def _update_loop(self):
        while self._running:
            self._fetch()
            for _ in range(1800):  # 30 min
                if not self._running:
                    return
                time.sleep(1)

    def _fetch(self):
        if not self.city or not self.api_key:
            return
        try:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": self.city,
                "appid": self.api_key,
                "units": self.units,
                "lang": self.lang,
            }
            resp = httpx.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            with self._lock:
                self._data = {
                    "temp": round(data["main"]["temp"]),
                    "feels_like": round(data["main"]["feels_like"]),
                    "description": data["weather"][0]["description"].capitalize(),
                    "icon": data["weather"][0]["icon"],
                    "humidity": data["main"]["humidity"],
                    "city": data["name"],
                }
            log.info("Clima: %s C, %s", self._data["temp"], self._data["description"])
        except Exception as e:
            log.error("Error clima: %s", e)

    @property
    def data(self):
        with self._lock:
            return self._data.copy() if self._data else None
