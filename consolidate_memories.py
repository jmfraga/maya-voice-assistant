#!/usr/bin/env python3
"""Nightly memory consolidation for Maya.

Run via cron at 3am:
  0 3 * * * cd /home/jmfraga/voice_assistant && /usr/bin/python3 consolidate_memories.py

For each user, sends all memories to LLM to:
- Remove duplicates
- Merge related memories
- Clean up outdated info
"""

import os
import sys
import yaml
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "data", "consolidation.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("maya.consolidate")


def load_config():
    config_path = os.path.join(BASE_DIR, "config.yaml")
    if not os.path.isfile(config_path):
        log.error("config.yaml no encontrado")
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_llm_client(config):
    """Create a minimal LLM client for consolidation."""
    import anthropic

    llm_cfg = config.get("llm", {})
    provider = llm_cfg.get("provider", "claude")

    if provider == "claude":
        cfg = llm_cfg.get("claude", {})
        client = anthropic.Anthropic(api_key=cfg.get("api_key", ""))
        model = cfg.get("model", "claude-sonnet-4-20250514")
        return client, model
    else:
        log.error("Solo Claude soportado para consolidacion")
        sys.exit(1)


def consolidate_user_memories(client, model, db, user_id, user_name):
    """Consolidate all memories for one user."""
    memories = db.get_memories(user_id, limit=200)
    if len(memories) < 5:
        log.info("  %s: solo %d memorias, no necesita consolidacion", user_name, len(memories))
        return 0

    # Group by category
    by_cat = {}
    for m in memories:
        by_cat.setdefault(m["category"], []).append(m)

    total_removed = 0

    for category, mems in by_cat.items():
        if len(mems) < 3:
            continue

        mem_list = "\n".join(f"[{m['id']}] {m['content']}" for m in mems)
        prompt = (
            f"Memorias de {user_name} en categoria '{category}':\n{mem_list}\n\n"
            "Consolida estas memorias:\n"
            "1. Identifica duplicados (misma info dicha diferente)\n"
            "2. Fusiona memorias relacionadas en una mas completa\n"
            "3. Si hay contradicciones, queda la mas reciente (IDs mayores son mas recientes)\n\n"
            "Responde con una lista de acciones, una por linea:\n"
            "MANTENER:id — no cambiar\n"
            "ELIMINAR:id — es duplicado o fue fusionado\n"
            "REEMPLAZAR:id:nuevo texto — actualizar con texto consolidado\n\n"
            "Responde SOLO con las acciones, sin explicaciones."
        )

        try:
            result = client.messages.create(
                model=model, max_tokens=500,
                system="Consolida memorias. Responde solo con acciones MANTENER/ELIMINAR/REEMPLAZAR.",
                messages=[{"role": "user", "content": prompt}],
            )
            response = result.content[0].text.strip()
        except Exception as e:
            log.error("  Error LLM consolidando %s/%s: %s", user_name, category, e)
            continue

        # Process actions
        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("ELIMINAR:"):
                try:
                    mem_id = int(line.split(":")[1].strip())
                    db.delete_memory(mem_id)
                    total_removed += 1
                    log.info("  Eliminada memoria %d", mem_id)
                except (ValueError, IndexError):
                    pass
            elif line.startswith("REEMPLAZAR:"):
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    try:
                        mem_id = int(parts[1].strip())
                        new_text = parts[2].strip()
                        db.delete_memory(mem_id)
                        db.save_memory(user_id, category, new_text)
                        log.info("  Reemplazada memoria %d -> %s", mem_id, new_text[:50])
                    except (ValueError, IndexError):
                        pass

    return total_removed


def main():
    log.info("=== Consolidacion nocturna de memorias ===")
    config = load_config()

    from db import Database
    db = Database(os.path.join(BASE_DIR, "data", "assistant.db"))
    client, model = get_llm_client(config)

    users = db.get_users()
    for user in users:
        log.info("Consolidando memorias de %s...", user["name"])
        removed = consolidate_user_memories(client, model, db, user["id"], user["name"])
        log.info("  %s: %d memorias eliminadas/fusionadas", user["name"], removed)

    log.info("=== Consolidacion completada ===")


if __name__ == "__main__":
    main()
