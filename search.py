"""Internet search via Perplexity API (OpenAI-compatible)."""

import logging
import httpx

log = logging.getLogger("maya.search")


class Search:
    def __init__(self, config: dict):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "sonar")
        self.base_url = config.get("base_url", "https://api.perplexity.ai")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def query(self, question: str) -> str | None:
        """Search the internet and return a concise answer in Spanish."""
        if not self.api_key:
            log.warning("Search no configurado (sin API key)")
            return None

        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Responde en español mexicano, de forma breve y clara. "
                                "Maximo 3 oraciones. Si hay datos numericos o fechas, incluyelos. "
                                "No uses markdown ni emojis."
                            ),
                        },
                        {"role": "user", "content": question},
                    ],
                },
                timeout=30.0,
            )
            response.raise_for_status()
            answer = response.json()["choices"][0]["message"]["content"]
            log.info("Busqueda OK: %s -> %s", question[:50], answer[:80])
            return answer
        except Exception as e:
            log.error("Error en busqueda: %s", e)
            return None
