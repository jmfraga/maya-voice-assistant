"""SQLite database for users, medications, contacts, reminders, conversations."""

import sqlite3
import os
import logging
from datetime import datetime

log = logging.getLogger("maya.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    real_name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    dosage TEXT,
    schedule TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    telegram_chat_id INTEGER,
    relationship TEXT,
    phone TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    text TEXT NOT NULL,
    remind_at TEXT NOT NULL,
    recurring TEXT,
    active INTEGER DEFAULT 1,
    last_triggered TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS medication_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    medication_id INTEGER REFERENCES medications(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    taken_at TEXT DEFAULT (datetime('now', 'localtime')),
    confirmed INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pending_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    relationship TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS treatment_schemas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    measurement_name TEXT NOT NULL,
    measurement_unit TEXT DEFAULT '',
    alert_low REAL,
    alert_high REAL,
    alert_contacts TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    notes TEXT,
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS treatment_ranges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_id INTEGER NOT NULL REFERENCES treatment_schemas(id),
    range_min REAL NOT NULL,
    range_max REAL NOT NULL,
    dose REAL NOT NULL,
    dose_unit TEXT DEFAULT '',
    time_of_day TEXT DEFAULT 'any',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS measurement_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    schema_id INTEGER REFERENCES treatment_schemas(id),
    measurement_value REAL NOT NULL,
    dose_given REAL,
    dose_unit TEXT,
    alert_sent INTEGER DEFAULT 0,
    notes TEXT,
    measured_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'familiar',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS telegram_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS pending_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    from_name TEXT NOT NULL,
    message TEXT NOT NULL,
    delivered INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS radio_stations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT DEFAULT '',
    active INTEGER DEFAULT 1
);
"""


class Database:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # Migrations
            cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "onboarded_at" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN onboarded_at TEXT")
                log.info("Columna onboarded_at agregada a users")
            if "telegram_chat_id" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN telegram_chat_id INTEGER")
                log.info("Columna telegram_chat_id agregada a users")
            # Contacts migration: emergency flag
            contact_cols = [row[1] for row in conn.execute("PRAGMA table_info(contacts)").fetchall()]
            if "emergency" not in contact_cols:
                conn.execute("ALTER TABLE contacts ADD COLUMN emergency INTEGER DEFAULT 0")
                log.info("Columna emergency agregada a contacts")
        log.info("DB inicializada: %s", self.db_path)

    # --- Users ---
    def ensure_user(self, user_id: str, real_name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, real_name) VALUES (?, ?)",
                (user_id, real_name),
            )

    def get_user(self, user_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_user(self, user_id: str, real_name: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET real_name = ? WHERE id = ?",
                (real_name, user_id),
            )

    def set_onboarded(self, user_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET onboarded_at = datetime('now', 'localtime') WHERE id = ?",
                (user_id,),
            )
            log.info("Onboarding completado para %s", user_id)

    def is_onboarded(self, user_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT onboarded_at FROM users WHERE id = ?", (user_id,),
            ).fetchone()
            return bool(row and row["onboarded_at"])

    def set_user_telegram(self, user_id: str, chat_id: int | None):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET telegram_chat_id = ? WHERE id = ?",
                (chat_id, user_id),
            )

    def get_user_by_chat_id(self, chat_id: int) -> dict | None:
        """Find a Maya user by their Telegram chat_id."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_chat_id = ?", (chat_id,),
            ).fetchone()
            return dict(row) if row else None

    def delete_user(self, user_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM measurement_log WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM treatment_ranges WHERE schema_id IN "
                         "(SELECT id FROM treatment_schemas WHERE user_id = ?)", (user_id,))
            conn.execute("DELETE FROM treatment_schemas WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM medication_log WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM contacts WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM medications WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            log.info("Usuario eliminado: %s", user_id)

    def get_users(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY real_name").fetchall()
            return [dict(r) for r in rows]

    # --- Medications ---
    def add_medication(self, user_id: str, name: str, dosage: str = "",
                       schedule: str = "", notes: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO medications (user_id, name, dosage, schedule, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, name, dosage, schedule, notes),
            )
            log.info("Medicamento agregado: %s para %s", name, user_id)
            return cur.lastrowid

    def get_medications(self, user_id: str, active_only: bool = True) -> list[dict]:
        with self._conn() as conn:
            q = "SELECT * FROM medications WHERE user_id = ?"
            params = [user_id]
            if active_only:
                q += " AND active = 1"
            rows = conn.execute(q + " ORDER BY name", params).fetchall()
            return [dict(r) for r in rows]

    def update_medication(self, med_id: int, **kwargs):
        allowed = {"name", "dosage", "schedule", "notes", "active"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [med_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE medications SET {sets} WHERE id = ?", vals)

    def delete_medication(self, med_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM medications WHERE id = ?", (med_id,))

    def confirm_medication(self, medication_id: int, user_id: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO medication_log (medication_id, user_id) VALUES (?, ?)",
                (medication_id, user_id),
            )
            log.info("Medicamento %d confirmado por %s", medication_id, user_id)

    def confirm_medication_by_name(self, name: str, user_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM medications WHERE user_id = ? AND name LIKE ? AND active = 1",
                (user_id, f"%{name}%"),
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO medication_log (medication_id, user_id) VALUES (?, ?)",
                    (row["id"], user_id),
                )
                return True
            return False

    def is_medication_taken_today(self, med_id: int, user_id: str,
                                   time_slot: str | None = None) -> bool:
        """Check if a medication was taken today, optionally within ±2 hours of time_slot (HH:MM)."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as conn:
            if time_slot:
                # Parse time_slot to get ±2h window
                try:
                    slot_h, slot_m = int(time_slot.split(":")[0]), int(time_slot.split(":")[1])
                    low_h = (slot_h - 2) % 24
                    high_h = (slot_h + 2) % 24
                    low_time = f"{today} {low_h:02d}:{slot_m:02d}:00"
                    high_time = f"{today} {high_h:02d}:{slot_m:02d}:00"
                    if low_h <= high_h:
                        row = conn.execute(
                            "SELECT 1 FROM medication_log "
                            "WHERE medication_id = ? AND user_id = ? "
                            "AND taken_at >= ? AND taken_at <= ?",
                            (med_id, user_id, low_time, high_time),
                        ).fetchone()
                    else:
                        # Wraps around midnight
                        row = conn.execute(
                            "SELECT 1 FROM medication_log "
                            "WHERE medication_id = ? AND user_id = ? "
                            "AND DATE(taken_at) = ? "
                            "AND (taken_at >= ? OR taken_at <= ?)",
                            (med_id, user_id, today, low_time, high_time),
                        ).fetchone()
                except (ValueError, IndexError):
                    row = conn.execute(
                        "SELECT 1 FROM medication_log "
                        "WHERE medication_id = ? AND user_id = ? AND DATE(taken_at) = ?",
                        (med_id, user_id, today),
                    ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM medication_log "
                    "WHERE medication_id = ? AND user_id = ? AND DATE(taken_at) = ?",
                    (med_id, user_id, today),
                ).fetchone()
            return row is not None

    def get_medication_log(self, user_id: str, date: str | None = None) -> list[dict]:
        with self._conn() as conn:
            q = """SELECT ml.*, m.name as med_name, m.dosage
                   FROM medication_log ml JOIN medications m ON ml.medication_id = m.id
                   WHERE ml.user_id = ?"""
            params = [user_id]
            if date:
                q += " AND DATE(ml.taken_at) = ?"
                params.append(date)
            q += " ORDER BY ml.taken_at DESC"
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    # --- Contacts ---
    def add_contact(self, user_id: str, name: str, telegram_chat_id: int = 0,
                    relationship: str = "", phone: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO contacts (user_id, name, telegram_chat_id, relationship, phone) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, name, telegram_chat_id, relationship, phone),
            )
            return cur.lastrowid

    def get_contacts(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE user_id = ? ORDER BY name",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_emergency_contacts(self, user_id: str) -> list[dict]:
        """Get only emergency contacts with telegram_chat_id."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE user_id = ? AND emergency = 1 "
                "AND telegram_chat_id IS NOT NULL AND telegram_chat_id != 0 "
                "ORDER BY name",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_contact(self, contact_id: int, **kwargs):
        allowed = {"name", "telegram_chat_id", "relationship", "phone", "emergency"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [contact_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE contacts SET {sets} WHERE id = ?", vals)

    def delete_contact(self, contact_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))

    # --- Reminders ---
    def add_reminder(self, user_id: str, text: str, remind_at: str,
                     recurring: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO reminders (user_id, text, remind_at, recurring) "
                "VALUES (?, ?, ?, ?)",
                (user_id, text, remind_at, recurring),
            )
            log.info("Recordatorio creado: '%s' para %s a las %s", text, user_id, remind_at)
            return cur.lastrowid

    def get_pending_reminders(self, user_id: str) -> list[dict]:
        now = datetime.now().strftime("%H:%M")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE user_id = ? AND active = 1 "
                "AND remind_at >= ? ORDER BY remind_at",
                (user_id, now),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_active_reminders(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT r.*, u.real_name FROM reminders r "
                "JOIN users u ON r.user_id = u.id "
                "WHERE r.active = 1 ORDER BY r.remind_at"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_due_reminders(self) -> list[dict]:
        now = datetime.now().strftime("%H:%M")
        today = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT r.*, u.real_name FROM reminders r "
                "JOIN users u ON r.user_id = u.id "
                "WHERE r.active = 1 AND r.remind_at <= ? "
                "AND (r.last_triggered IS NULL OR DATE(r.last_triggered) < ?)",
                (now, today),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_reminder_triggered(self, reminder_id: int):
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE reminders SET last_triggered = ? WHERE id = ?",
                (now, reminder_id),
            )
            # Deactivate non-recurring
            conn.execute(
                "UPDATE reminders SET active = 0 WHERE id = ? AND (recurring IS NULL OR recurring = '')",
                (reminder_id,),
            )

    def update_reminder(self, rem_id: int, **kwargs):
        allowed = {"text", "remind_at", "recurring", "active"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [rem_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE reminders SET {sets} WHERE id = ?", vals)

    def delete_reminder(self, rem_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM reminders WHERE id = ?", (rem_id,))

    # --- Conversations ---
    def save_conversation(self, user_id: str, role: str, content: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )

    def get_recent_conversations(self, user_id: str, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    # --- Memories ---
    def save_memory(self, user_id: str, category: str, content: str):
        """Save a memory, deduplicating by substring match in same category."""
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id, content FROM memories WHERE user_id = ? AND category = ?",
                (user_id, category),
            ).fetchall()
            # Check for similar existing memory (substring match)
            for row in existing:
                if content.lower() in row["content"].lower() or row["content"].lower() in content.lower():
                    conn.execute(
                        "UPDATE memories SET content = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                        (content, row["id"]),
                    )
                    log.info("Memoria actualizada [%s]: %s", category, content)
                    return
            conn.execute(
                "INSERT INTO memories (user_id, category, content) VALUES (?, ?, ?)",
                (user_id, category, content),
            )
            log.info("Memoria guardada [%s]: %s", category, content)

    def get_memories(self, user_id: str, category: str | None = None, limit: int = 30) -> list[dict]:
        with self._conn() as conn:
            q = "SELECT * FROM memories WHERE user_id = ?"
            params: list = [user_id]
            if category:
                q += " AND category = ?"
                params.append(category)
            q += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    def delete_memory(self, memory_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    # --- Pending Contacts (Telegram self-registration) ---
    def add_pending_contact(self, chat_id: int, name: str, relationship: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR REPLACE INTO pending_contacts (chat_id, name, relationship, status) "
                "VALUES (?, ?, ?, 'pending')",
                (chat_id, name, relationship),
            )
            log.info("Solicitud de contacto: %s (%s), chat_id=%d", name, relationship, chat_id)
            return cur.lastrowid

    def get_pending_contacts(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_contacts WHERE status = 'pending' ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_pending_contacts(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_contacts ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def approve_pending_contact(self, pending_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_contacts WHERE id = ?", (pending_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE pending_contacts SET status = 'approved' WHERE id = ?",
                (pending_id,),
            )
            return dict(row)

    def reject_pending_contact(self, pending_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_contacts SET status = 'rejected' WHERE id = ?",
                (pending_id,),
            )

    def is_chat_id_registered(self, chat_id: int) -> bool:
        """Check if chat_id exists in contacts or has a pending request."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM contacts WHERE telegram_chat_id = ? "
                "UNION SELECT 1 FROM pending_contacts WHERE chat_id = ? AND status = 'pending'",
                (chat_id, chat_id),
            ).fetchone()
            return row is not None

    # --- Treatment Schemas ---
    def add_treatment_schema(self, user_id: str, name: str, measurement_name: str,
                             measurement_unit: str = "", alert_low: float | None = None,
                             alert_high: float | None = None, alert_contacts: str = "",
                             notes: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO treatment_schemas "
                "(user_id, name, measurement_name, measurement_unit, alert_low, alert_high, alert_contacts, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, name, measurement_name, measurement_unit, alert_low, alert_high, alert_contacts, notes),
            )
            log.info("Esquema de tratamiento creado: %s para %s", name, user_id)
            return cur.lastrowid

    def get_treatment_schemas(self, user_id: str, active_only: bool = True) -> list[dict]:
        with self._conn() as conn:
            q = "SELECT * FROM treatment_schemas WHERE user_id = ?"
            params: list = [user_id]
            if active_only:
                q += " AND active = 1"
            rows = conn.execute(q + " ORDER BY name", params).fetchall()
            return [dict(r) for r in rows]

    def get_treatment_schema(self, schema_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM treatment_schemas WHERE id = ?", (schema_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_treatment_schema(self, schema_id: int, **kwargs):
        allowed = {"name", "measurement_name", "measurement_unit", "alert_low",
                    "alert_high", "alert_contacts", "notes", "active"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [schema_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE treatment_schemas SET {sets} WHERE id = ?", vals)

    def delete_treatment_schema(self, schema_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM treatment_ranges WHERE schema_id = ?", (schema_id,))
            conn.execute("DELETE FROM measurement_log WHERE schema_id = ?", (schema_id,))
            conn.execute("DELETE FROM treatment_schemas WHERE id = ?", (schema_id,))
            log.info("Esquema de tratamiento %d eliminado", schema_id)

    # --- Treatment Ranges ---
    def add_treatment_range(self, schema_id: int, range_min: float, range_max: float,
                            dose: float, dose_unit: str = "", time_of_day: str = "any",
                            notes: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO treatment_ranges (schema_id, range_min, range_max, dose, dose_unit, time_of_day, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (schema_id, range_min, range_max, dose, dose_unit, time_of_day, notes),
            )
            return cur.lastrowid

    def get_treatment_ranges(self, schema_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM treatment_ranges WHERE schema_id = ? ORDER BY range_min",
                (schema_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_treatment_range(self, range_id: int, **kwargs):
        allowed = {"range_min", "range_max", "dose", "dose_unit", "time_of_day", "notes"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [range_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE treatment_ranges SET {sets} WHERE id = ?", vals)

    def delete_treatment_range(self, range_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM treatment_ranges WHERE id = ?", (range_id,))

    def lookup_treatment_dose(self, user_id: str, measurement_name: str,
                              value: float) -> dict | None:
        """Find the matching dose for a measurement value. Returns schema+range info or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT ts.id as schema_id, ts.name as schema_name, "
                "ts.measurement_name, ts.measurement_unit, "
                "ts.alert_low, ts.alert_high, ts.alert_contacts, "
                "tr.dose, tr.dose_unit, tr.range_min, tr.range_max, tr.notes as range_notes "
                "FROM treatment_schemas ts "
                "JOIN treatment_ranges tr ON tr.schema_id = ts.id "
                "WHERE ts.user_id = ? AND ts.active = 1 "
                "AND LOWER(ts.measurement_name) LIKE ? "
                "AND ? >= tr.range_min AND ? <= tr.range_max "
                "LIMIT 1",
                (user_id, f"%{measurement_name.lower()}%", value, value),
            ).fetchone()
            return dict(row) if row else None

    # --- Measurement Log ---
    def log_measurement(self, user_id: str, schema_id: int, value: float,
                        dose_given: float | None = None, dose_unit: str = "",
                        alert_sent: int = 0, notes: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO measurement_log "
                "(user_id, schema_id, measurement_value, dose_given, dose_unit, alert_sent, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, schema_id, value, dose_given, dose_unit, alert_sent, notes),
            )
            log.info("Medicion registrada: %s=%s para %s", schema_id, value, user_id)
            return cur.lastrowid

    def get_measurement_log(self, user_id: str, schema_id: int | None = None,
                            limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            q = ("SELECT ml.*, ts.name as schema_name, ts.measurement_name, ts.measurement_unit "
                 "FROM measurement_log ml "
                 "JOIN treatment_schemas ts ON ml.schema_id = ts.id "
                 "WHERE ml.user_id = ?")
            params: list = [user_id]
            if schema_id:
                q += " AND ml.schema_id = ?"
                params.append(schema_id)
            q += " ORDER BY ml.measured_at DESC, ml.id DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    def count_consecutive_alerts(self, user_id: str, schema_id: int) -> int:
        """Count how many of the most recent measurements were out of range (alert_sent=1)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT alert_sent FROM measurement_log "
                "WHERE user_id = ? AND schema_id = ? "
                "ORDER BY measured_at DESC, id DESC LIMIT 5",
                (user_id, schema_id),
            ).fetchall()
            count = 0
            for row in rows:
                if row["alert_sent"]:
                    count += 1
                else:
                    break
            return count

    # --- Admin Users ---
    def get_admin_user(self, username: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM admin_users WHERE username = ?", (username,),
            ).fetchone()
            return dict(row) if row else None

    def get_admin_users(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, username, role, created_at FROM admin_users ORDER BY id",
            ).fetchall()]

    def add_admin_user(self, username: str, password_hash: str, role: str = "familiar"):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO admin_users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, password_hash, role),
            )
            log.info("Admin user creado: %s (%s)", username, role)

    def update_admin_user(self, user_id: int, **kwargs):
        with self._conn() as conn:
            for key, val in kwargs.items():
                if key in ("username", "password_hash", "role"):
                    conn.execute(
                        f"UPDATE admin_users SET {key} = ? WHERE id = ?",
                        (val, user_id),
                    )

    def delete_admin_user(self, user_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))

    # --- Telegram Conversations ---
    def get_contacts_by_chat_id(self, chat_id: int) -> list[dict]:
        """Get all contacts + user info for a given Telegram chat_id."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT c.*, u.real_name as user_real_name "
                "FROM contacts c JOIN users u ON c.user_id = u.id "
                "WHERE c.telegram_chat_id = ?",
                (chat_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_telegram_conversation(self, chat_id: int, role: str, content: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO telegram_conversations (chat_id, role, content) "
                "VALUES (?, ?, ?)",
                (chat_id, role, content),
            )

    def get_telegram_history(self, chat_id: int, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM telegram_conversations WHERE chat_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    # --- Pending Messages (Telegram relay) ---
    def add_pending_message(self, user_id: str, from_name: str, message: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO pending_messages (user_id, from_name, message) "
                "VALUES (?, ?, ?)",
                (user_id, from_name, message),
            )
            log.info("Mensaje pendiente para %s de %s: %s", user_id, from_name, message[:50])
            return cur.lastrowid

    def get_pending_messages(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_messages WHERE user_id = ? AND delivered = 0 "
                "ORDER BY created_at",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_message_delivered(self, msg_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_messages SET delivered = 1 WHERE id = ?",
                (msg_id,),
            )

    # --- Radio Stations ---
    def get_radio_stations(self, active_only: bool = True) -> list[dict]:
        with self._conn() as conn:
            q = "SELECT * FROM radio_stations"
            if active_only:
                q += " WHERE active = 1"
            q += " ORDER BY id LIMIT 5"
            return [dict(r) for r in conn.execute(q).fetchall()]

    def add_radio_station(self, key: str, name: str, url: str, description: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO radio_stations (key, name, url, description) VALUES (?, ?, ?, ?)",
                (key, name, url, description),
            )
            log.info("Estacion de radio agregada: %s", name)
            return cur.lastrowid

    def update_radio_station(self, station_id: int, **kwargs):
        allowed = {"key", "name", "url", "description", "active"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [station_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE radio_stations SET {sets} WHERE id = ?", vals)

    def delete_radio_station(self, station_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM radio_stations WHERE id = ?", (station_id,))

    def seed_radio_stations(self):
        """Seed default radio stations if table is empty."""
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM radio_stations").fetchone()[0]
            if count == 0:
                defaults = [
                    ("romantica", "Radio Romantica", "https://stream.zeno.fm/yn65fsaurfhvv", "Baladas romanticas"),
                    ("clasica", "Radio Clasica", "https://stream.zeno.fm/4d60am6ar1zuv", "Musica clasica"),
                    ("noticias", "Radio Formula", "https://stream.zeno.fm/s850mfsp3fhvv", "Noticias Mexico"),
                    ("ranchera", "Radio Ranchera", "https://stream.zeno.fm/e0n1fdaurfhvv", "Musica mexicana"),
                    ("instrumental", "Radio Instrumental", "https://stream.zeno.fm/0r0xa792kwzuv", "Piano y guitarra"),
                ]
                for key, name, url, desc in defaults:
                    conn.execute(
                        "INSERT INTO radio_stations (key, name, url, description) VALUES (?, ?, ?, ?)",
                        (key, name, url, desc),
                    )
                log.info("Estaciones de radio default cargadas (%d)", len(defaults))
